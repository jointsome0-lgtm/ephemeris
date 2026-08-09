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

What may be deleted is decided HERE, by `SHELF_CONTENTS` below — four paths
and four digests, the shelf's complete and final inventory. The bundle's own
`assets/libs/SHASUMS256` is only consulted for the one thing it can still be
trusted to say: that the app once owned this directory. It cannot be trusted
for what to delete, because it lives inside a directory a lesson session can
write — a session could append a line naming a file it authored, with that
file's real digest, and a tool that read its inventory from there would
hash-match and delete the author's work.

So, in order:

- The stamp must be present and begin with the seeder's marker. Without it the
  directory predates the shelf or belongs to the agent; it is left untouched.
- A file is removed only when it is one of the four AND its sha256 still
  matches. An edited copy no longer matches and stays.
- Anything else under the shelf — a file the pinned inventory does not name, a
  symlink, a directory that will not empty — hands the whole directory back:
  the stamp stays too, so a rerun still sees what is owed.
- Directories are removed only when they are already empty, and never
  `assets/` itself.
- Nothing is reached through a symlink at any path component, nothing
  non-regular is read, and the inode that was hashed is the inode unlinked.

## Run it with the service stopped

Bundles are writable from inside a live lesson session, and no POSIX call
deletes a verified inode by identity — `unlink` takes a name. This tool
re-checks that the name still holds the inode it hashed, immediately before
removing it, which leaves a window measured in microseconds rather than in
the seconds a hash of a 3 MB file takes. Closing it entirely is the operator's
half: stop the service first. There is no hurry — the bytes have been sitting
there since #146.

Usage:
    systemctl --user stop ephemeris
    ACTIVITY_DATA_DIR=... python -m scripts.drop_lesson_libs --dry-run
    ACTIVITY_DATA_DIR=... python -m scripts.drop_lesson_libs
    ACTIVITY_DATA_DIR=... python -m scripts.drop_lesson_libs --slug demo-lesson-1

Idempotent: a bundle with no seeded shelf is a no-op, so a rerun reports
nothing to do. Exit code 0 = nothing failed; 1 otherwise.
"""
from __future__ import annotations

import argparse
import hashlib
import os
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

# The shelf's complete and final inventory, bundle-relative — the paths as the
# seeder flattened them (`d3/7.9.0/d3.min.js` → `d3/d3.min.js`).
#
# Pinned here, in a file only the repository can change, rather than parsed out
# of each bundle's own stamp: see the module docstring. This is the whole of
# what this tool will ever delete.
#
# One set for the shelf's entire life. `vendor/lesson-libs/SHASUMS256` was
# added by #146 (1a2c26b) and removed by #161, with no commit in between — so
# no bundle anywhere can hold a seeded copy at some other version.
SHELF_CONTENTS = {
    "d3/d3.min.js":
        "f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539",
    "katex/katex.min.css":
        "3124b9fb2162a8591273f05c41f84c9a8a96629d769e6cd6a8f3ba26983b931f",
    "katex/katex.min.js":
        "45fbe318fea878fdc0a111913dc1f87894b2c439360d0228c086ef313f213efc",
    "mermaid/mermaid.min.js":
        "74d7c46dabca328c2294733910a8aa1ed0c37451776e8d5295da38a2b758fb9b",
}

# The stamp is four comment lines and four digests; anything larger is not the
# file this tool knows how to read.
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


def _read_at(dir_fd: int, name: str, limit: int) -> tuple[bytes, tuple[int, int]] | None:
    """The bytes of a regular file opened relative to `dir_fd`, with the
    identity of the inode they came from. None when nothing is there; raises
    :class:`Unverifiable` for anything else.

    `O_NONBLOCK` is not decoration: without it a FIFO planted at one of these
    names parks the `open` on a writer that never comes, and the `fstat` that
    would reject it as non-regular never runs. This tool is meant to be run by
    hand on a live instance and must not be something a lesson can hang.

    The returned `(st_dev, st_ino)` is what :func:`_unlink_verified` re-checks
    the name against, so the file that gets deleted is the file that was read.
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
            return handle.read(limit), (info.st_dev, info.st_ino)
    except OSError as exc:
        raise Unverifiable(exc.strerror or str(exc)) from exc
    finally:
        os.close(fd)


def _unlink_verified(dir_fd: int, name: str, identity: tuple[int, int]) -> None:
    """Remove `name`, but only while it still holds the inode that was hashed.

    `unlink` takes a name and POSIX offers no way to remove a verified inode by
    identity, so a lesson session writing this bundle could in principle
    replace the file between the hash and the removal — and the replacement,
    whose bytes nobody checked, is what would go. Re-stating the identity here
    shrinks that window from "however long hashing 3 MB takes" to the gap
    between these two calls. The operator closes the rest by stopping the
    service; the module docstring says so.
    """
    try:
        now = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return  # somebody else removed it, which is the wanted state
    if (now.st_dev, now.st_ino) != identity:
        raise Unverifiable(
            "it was replaced while this run was reading it; nothing was removed"
        )
    os.unlink(name, dir_fd=dir_fd)


def _is_app_stamped(shelf: Path) -> bool:
    """Whether the shelf still carries the seeder's ownership stamp.

    This is the ONLY question the bundle's own `SHASUMS256` is asked. Its
    digest lines are not read: the file sits in a directory a lesson session
    can write, so a session could append a line naming a file it authored and
    a tool that believed it would delete the author's work. What may be
    deleted is `SHELF_CONTENTS`, which lives in the repository.
    """
    try:
        found = _descend(shelf, STAMP_NAME)
        if found is None:
            return False
        fd, name = found
        try:
            read = _read_at(fd, name, _STAMP_MAX_BYTES)
        finally:
            os.close(fd)
    except Unverifiable:
        return False
    if read is None:
        return False
    raw, _identity = read
    try:
        return raw.decode("utf-8").startswith(STAMP_MARKER)
    except UnicodeDecodeError:
        return False


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
    if not _is_app_stamped(shelf):
        return 0, 0, [f"{SHELF_DIR}/{STAMP_NAME} is absent or not the app's stamp"]

    removed = freed = 0
    kept: list[str] = []
    for relative, digest in sorted(SHELF_CONTENTS.items()):
        try:
            found = _descend(shelf, relative)
            if found is None:
                continue  # already gone — nothing owed
            fd, name = found
            try:
                read = _read_at(fd, name, _LIBRARY_MAX_BYTES)
                if read is None:
                    continue
                data, identity = read
                if hashlib.sha256(data).hexdigest() != digest:
                    kept.append(
                        f"{relative} is not the copy the shelf published; left in place"
                    )
                    continue
                if apply:
                    _unlink_verified(fd, name, identity)
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
        not in SHELF_CONTENTS and relative != STAMP_NAME
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
