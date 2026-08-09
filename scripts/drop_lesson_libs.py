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
from pathlib import Path, PurePosixPath

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
# The biggest thing the shelf ever held was mermaid at 3.4 MB. Generous enough
# never to refuse an honest seeded copy, bounded so a file swapped for
# something enormous is refused rather than read into memory.
_LIBRARY_MAX_BYTES = 64 * 1024 * 1024


class Unverifiable(Exception):
    """Something IS at this path and this tool could not read it as its own.

    Distinct from absence, and the difference decides whether the stamp may be
    removed. An absent file is owed nothing; an unverifiable one is unfinished
    business, and dropping the stamp for it would leave seeded bytes behind
    with no marker for a rerun to find them by.
    """


def _descend(shelf: Path, relative: str) -> tuple[int, str] | None:
    """A descriptor for `relative`'s parent directory, reached without
    following one symlink, plus the final name to use against it.

    `O_NOFOLLOW` guards only the LAST component of a path, so opening
    `<shelf>/d3/d3.min.js` in a single call still walks through a `d3` that a
    lesson session may have replaced with a link at somebody else's directory
    — and this tool's next move is `unlink`. Each component is therefore
    opened on its own and refused if it is a link or not a directory, so the
    descriptor the caller hashes and unlinks through provably never left the
    shelf.

    None when a component is simply missing. Raises :class:`Unverifiable` when
    one is present but is not a directory this tool will walk.
    """
    parts = PurePosixPath(relative).parts
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(shelf, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Unverifiable(exc.strerror or str(exc)) from exc
    try:
        for component in parts[:-1]:
            child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
    except FileNotFoundError:
        os.close(fd)
        return None
    except OSError as exc:
        os.close(fd)
        # ELOOP for a symlinked component, ENOTDIR for a file in the way.
        raise Unverifiable(
            f"{exc.strerror or exc} on the path to it; nothing was read or removed"
        ) from exc
    return fd, parts[-1]


def _read_at(dir_fd: int, name: str, limit: int) -> bytes | None:
    """The bytes of a regular file opened relative to `dir_fd`, or None when
    nothing is there. Raises :class:`Unverifiable` for anything else.

    `O_NONBLOCK` is not decoration: without it a FIFO planted at one of these
    names parks the `open` on a writer that never comes, and the `fstat` that
    would reject it as non-regular never runs. This tool is meant to be run by
    hand on a live instance and must not be something a lesson can hang.
    """
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=dir_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        # ELOOP for a symlink at the name itself, ENXIO for a FIFO with no
        # writer, EACCES for a mode this process cannot read.
        raise Unverifiable(exc.strerror or str(exc)) from exc
    try:
        info = os.fstat(fd)
        if not stat_module.S_ISREG(info.st_mode):
            raise Unverifiable("not an ordinary file")
        if info.st_size > limit:
            raise Unverifiable(f"{info.st_size} bytes, past what this tool reads")
        with os.fdopen(os.dup(fd), "rb") as handle:
            return handle.read(limit)
    except OSError as exc:
        raise Unverifiable(exc.strerror or str(exc)) from exc
    finally:
        os.close(fd)


def _inventory(shelf: Path) -> dict[str, str] | None:
    """The stamp's `{relative path: sha256}`, or None when it is not ours."""
    try:
        found = _descend(shelf, STAMP_NAME)
        if found is None:
            return None
        fd, name = found
        try:
            raw = _read_at(fd, name, _STAMP_MAX_BYTES)
        finally:
            os.close(fd)
    except Unverifiable:
        return None
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


def _prune_empty(shelf: Path) -> None:
    """Remove every directory left empty under `shelf`, and `shelf` itself.

    Bottom-up, and `rmdir` removes only an EMPTY directory and never follows a
    link at the name it is given — so the worst a race here could achieve is
    the loss of an empty directory, which is also the only thing it removes on
    purpose.
    """
    for parent, dirs, _files in os.walk(shelf, topdown=False, followlinks=False):
        for name in dirs:
            try:
                os.rmdir(os.path.join(parent, name))
            except OSError:
                pass
    try:
        shelf.rmdir()
    except OSError:
        pass


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
        try:
            found = _descend(shelf, relative)
            if found is None:
                continue  # already gone — nothing owed
            fd, name = found
            try:
                data = _read_at(fd, name, _LIBRARY_MAX_BYTES)
                if data is None:
                    continue
                if hashlib.sha256(data).hexdigest() != digest:
                    kept.append(f"{relative} no longer matches the stamp; left in place")
                    continue
                if apply:
                    os.unlink(name, dir_fd=fd)
                removed += 1
                freed += len(data)
            finally:
                os.close(fd)
        except Unverifiable as exc:
            # Present, but this tool cannot prove it is the app's copy. Keeping
            # it also keeps the stamp, so a rerun still sees what is owed.
            kept.append(f"{relative}: {exc}; left in place")
        except OSError as exc:
            kept.append(f"{relative}: {exc.strerror or exc}")

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
        for parent, dirs, files in os.walk(shelf, followlinks=False)
        # A symlinked directory is listed in `dirs` and never descended into,
        # so it would go unseen among `files`. It is as much somebody else's
        # node as a stray file is.
        for name in files + [d for d in dirs if os.path.islink(os.path.join(parent, d))]
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
            os.unlink(shelf / STAMP_NAME)
        except FileNotFoundError:
            pass
        except OSError as exc:
            kept.append(f"{STAMP_NAME}: {exc.strerror or exc}")
            return removed, freed, kept
        # Last, and only once nothing is kept: the directories are empty by now
        # and the shelf has stopped being the app's.
        _prune_empty(shelf)
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
