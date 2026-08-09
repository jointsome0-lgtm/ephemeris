"""Retire the seeded lesson-libs shelf from bundles that still carry it (#161).

Between #146 and #161 the app copied a vendored shelf (d3, KaTeX, mermaid) into
`<bundle>/assets/libs/` on every lesson-terminal open, because a lesson had no
way to install a library for itself. It has one now — the build step installs
and bundles whatever the agent names — so the shelf, its seeder and the
repository copy are gone. What is not gone is the bytes already written into
bundles: roughly 4.4 MB per seeded bundle of libraries nothing references, at a
path the app no longer manages and no longer restores.

Doing nothing was the alternative and it is worse than it sounds. The copies
would sit there as ordinary bundle content — served by the files route, found
by the next study agent reading its own `assets/`, frozen at the version they
were pinned at, with no refresh path left in the repository. A lesson would
quietly go on referencing a library the app stopped standing behind.

## What makes this safe to delete

`assets/libs/SHASUMS256` is the seeder's ownership stamp, and it is also the
inventory of exactly what the seeder wrote. So this tool never guesses:

- The stamp must be present and begin with the seeder's marker. Without it the
  directory predates the shelf or belongs to the agent; it is left untouched.
- Each file is removed only when its sha256 still matches the digest the stamp
  recorded. A file the agent edited no longer matches and stays, along with
  everything else the stamp does not name.
- Directories are removed only when they are already empty afterwards, and
  never `assets/` itself.
- Nothing is reached through a symlink, and a symlink is never what gets
  removed in place of the file it names.

A bundle where anything was kept keeps its `assets/libs/` and its stamp, so a
rerun re-examines it rather than assuming the last run finished the job.

Usage:
    ACTIVITY_DATA_DIR=... python -m scripts.drop_lesson_libs --dry-run
    ACTIVITY_DATA_DIR=... python -m scripts.drop_lesson_libs
    ACTIVITY_DATA_DIR=... python -m scripts.drop_lesson_libs --slug thank-go-1-2

Idempotent: a bundle with no seeded shelf is a no-op, so a rerun reports
nothing to do. Exit code 0 = nothing failed; 1 otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat as stat_module
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.lessons import LESSONS_DIR  # noqa: E402

# What the seeder wrote as the first bytes of every stamp it produced. The
# constant lived in `app/services/lessons.py` until the seeder was deleted;
# this tool is the last reader of it, which is the right place for it to end.
STAMP_MARKER = "# ephemeris lesson-libs"
SHELF_DIR = "assets/libs"
STAMP_NAME = "SHASUMS256"
# sha256sum's own output format: digest, two spaces, path relative to the shelf.
_LINE = re.compile(r"^([0-9a-f]{64}) {2}(\S.*)$")
# The stamp is four comment lines and a handful of digests; anything larger is
# not the file this tool knows how to read.
_STAMP_MAX_BYTES = 64 * 1024


def _read_no_follow(path: Path, limit: int) -> bytes | None:
    """Read a regular file on a descriptor that followed no symlink.

    `None` for anything that is not an ordinary file, is missing, or is bigger
    than `limit` — every one of which means "not something this tool acts on".
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat_module.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        with os.fdopen(os.dup(fd), "rb") as handle:
            return handle.read(limit)
    finally:
        os.close(fd)


def _inventory(shelf: Path) -> dict[str, str] | None:
    """The stamp's `{relative path: sha256}`, or None when it is not ours."""
    raw = _read_no_follow(shelf / STAMP_NAME, _STAMP_MAX_BYTES)
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text.startswith(STAMP_MARKER):
        return None
    entries: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        matched = _LINE.match(line)
        if not matched:
            return None  # a stamp this tool cannot read in full is not acted on
        digest, relative = matched.group(1), matched.group(2)
        segments = relative.split("/")
        if any(seg in ("", ".", "..") for seg in segments) or "\\" in relative:
            return None
        entries[relative] = digest
    return entries


def _digest(path: Path) -> str | None:
    data = _read_no_follow(path, 64 * 1024 * 1024)
    return None if data is None else hashlib.sha256(data).hexdigest()


def _prune_empty(root: Path, shelf: Path) -> None:
    """Remove `root` and every empty directory up to and including `shelf`."""
    current = root
    while True:
        try:
            current.rmdir()
        except OSError:
            return  # not empty, or gone — either way, stop climbing
        if current == shelf:
            return
        current = current.parent


def retire(bundle: Path, *, apply: bool) -> tuple[int, int, list[str]]:
    """Retire one bundle's seeded shelf.

    Returns `(files removed, bytes freed, reasons anything was kept)`.
    """
    shelf = bundle / SHELF_DIR
    if not shelf.is_dir() or shelf.is_symlink():
        return 0, 0, []
    inventory = _inventory(shelf)
    if inventory is None:
        return 0, 0, [f"{SHELF_DIR}/{STAMP_NAME} is absent or not the app's stamp"]

    removed = freed = 0
    kept: list[str] = []
    for relative, digest in sorted(inventory.items()):
        target = shelf / relative
        found = _digest(target)
        if found is None:
            continue  # already gone, or never an ordinary file — nothing owed
        if found != digest:
            kept.append(f"{relative} no longer matches the stamp; left in place")
            continue
        size = target.stat().st_size
        if apply:
            try:
                target.unlink()
            except OSError as exc:
                kept.append(f"{relative}: {exc.strerror or exc}")
                continue
            _prune_empty(target.parent, shelf)
        removed += 1
        freed += size

    if kept:
        # The stamp stays: it is what a rerun reads to know what is still owed,
        # and while any seeded file remains the directory is still the app's.
        return removed, freed, kept

    # Anything under the shelf the stamp never named. Computed against the
    # inventory rather than against what is left on disk, so a dry run answers
    # the same question an applying run does — walking the untouched tree would
    # otherwise report every file this run intends to remove as a stray.
    strays = sorted(
        relative
        for parent, _dirs, files in os.walk(shelf)
        for name in files
        if (relative := (Path(parent) / name).relative_to(shelf).as_posix())
        not in inventory and relative != STAMP_NAME
    )
    if strays:
        kept.append(
            "the shelf holds files the stamp never named, so it is the agent's "
            "now: " + ", ".join(strays[:10])
        )
        return removed, freed, kept

    if apply:
        try:
            (shelf / STAMP_NAME).unlink(missing_ok=True)
            _prune_empty(shelf, shelf)
        except OSError as exc:
            kept.append(f"{STAMP_NAME}: {exc.strerror or exc}")
            return removed, freed, kept
    removed += 1
    return removed, freed, kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be removed and change nothing")
    parser.add_argument("--slug", action="append", default=None,
                        help="limit to this bundle slug; repeatable")
    args = parser.parse_args(argv)

    root = Path(LESSONS_DIR)
    if not root.is_dir():
        print(f"no lessons root at {root}", file=sys.stderr)
        return 1
    bundles = sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())
    if args.slug:
        wanted = set(args.slug)
        bundles = [p for p in bundles if p.name in wanted]
        missing = wanted - {p.name for p in bundles}
        if missing:
            print(f"no such bundle: {', '.join(sorted(missing))}", file=sys.stderr)
            return 1

    apply = not args.dry_run
    total_files = total_bytes = touched = 0
    problems = 0
    for bundle in bundles:
        removed, freed, kept = retire(bundle, apply=apply)
        if removed:
            touched += 1
            total_files += removed
            total_bytes += freed
            verb = "removed" if apply else "would remove"
            print(f"{bundle.name}: {verb} {removed} file(s), {freed / 1e6:.1f} MB")
        for reason in kept:
            problems += 1
            print(f"{bundle.name}: kept — {reason}")

    print(
        f"\n{len(bundles)} bundle(s) examined, {touched} carried a seeded shelf, "
        f"{total_files} file(s) {'removed' if apply else 'to remove'}, "
        f"{total_bytes / 1e6:.1f} MB {'freed' if apply else 'to free'}"
        + ("" if apply else "  (dry run — nothing changed)")
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
