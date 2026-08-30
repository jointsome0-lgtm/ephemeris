"""Shared projection machinery for the lesson record services.

One per-lesson lock registry, one idempotency-key grammar, one durable
publisher (stage, fsync, replace, dirsync) in a descriptor-relative and a
path-taking form, one private cross-process lock
under a projections directory, one file seal, the publication identity gate,
and one record skeleton parameterised by the per-kind pieces.  No rate state
lives here.
"""
from __future__ import annotations

import errno
import os
import sqlite3
import stat as stat_module
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from ..db import immediate

MAX_KEY_LEN = 128

DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)

_lesson_locks_lock = threading.Lock()
_lesson_locks: dict[str, threading.RLock] = {}


def lesson_lock(slug: str) -> threading.RLock:
    with _lesson_locks_lock:
        lock = _lesson_locks.get(slug)
        if lock is None:
            lock = _lesson_locks[slug] = threading.RLock()
        return lock


def has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def valid_idempotency_key(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_KEY_LEN:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not has_control_chars(value)


def require_eligible(
    read, error: type[Exception], unavailable: str, affordance: str
) -> None:
    if read.rejected:
        raise error(
            "manifest-rejected", 409,
            f"the lesson manifest is rejected; {affordance} access is refused",
        )
    if "identity-mismatch" in read.codes():
        raise error(
            "identity-mismatch", 409,
            "manifest lesson_uid differs from the DB uid; resolve the mismatch first",
        )
    if not read.bridge_eligible:
        raise error(
            unavailable, 409,
            f"this lesson's manifest/profile grants no {affordance} affordance",
        )


def identity_contradicts(lesson: dict) -> bool:
    """The publication identity gate (S-H7): never publish lesson A's
    conclusions into a bundle whose manifest says it is lesson B.

    A readable manifest carrying a `lesson_uid` that differs from the DB uid
    blocks publication; the row still commits and the projection stays pending
    until the contradiction is resolved. A missing, v1/legacy, or rejected
    manifest does NOT block — the slug directory is the DB's own mapping, and
    demanding a valid v2 manifest would silence exactly the legacy lessons the
    assessment channel exists for (D-S1-4). The read is the PURE one (D-F1-2):
    projecting never creates a directory, a skeleton manifest, or a file.

    A rejected read carries no trusted identity, so it never gates. The
    reader can assign `lesson_uid` and only afterwards accumulate a rejecting
    finding — an empty `pages` list, a duplicate id — and honouring that
    half-parsed value would block the projection permanently on exactly the
    broken manifests the rule above says must publish. This is the
    `effective_profile` idiom (§9.2): consumers read nothing but findings out
    of a rejected manifest."""
    from . import lessons

    read = lessons.read_bundle_readonly(lesson)
    if read.rejected:
        return False
    uid = read.lesson_uid
    return isinstance(uid, str) and bool(uid) and uid != lesson.get("uid")


def safe_uid(lesson: dict) -> str:
    from . import bundle_schema

    uid = lesson.get("uid")
    if not isinstance(uid, str) or bundle_schema.UUID_RE.match(uid) is None:
        raise OSError("lesson has no safe projection identity")
    return uid


def projection_exists(lesson: dict, name: str) -> bool:
    from . import lessons

    try:
        os.lstat(lessons._lesson_dir(lesson["slug"]) / name)
    except (OSError, lessons.LessonError):
        return False
    return True


def ensure_state_dir(state_dir: Path) -> None:
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not stat_module.S_ISDIR(os.lstat(state_dir).st_mode):
        raise OSError("projection state root is not a directory")


@contextmanager
def file_lock(state_dir: Path, lesson: dict, *, blocking: bool = False):
    try:
        import fcntl
    except ImportError as exc:
        raise OSError("advisory file locking (fcntl.flock) is unavailable") from exc

    uid = safe_uid(lesson)
    ensure_state_dir(state_dir)
    fd = os.open(
        state_dir / f"{uid}.lock",
        os.O_RDWR | os.O_CREAT | os.O_NONBLOCK
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        st = os.fstat(fd)
        if not stat_module.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise OSError("unsafe projection lock file")
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def file_seal(st: os.stat_result) -> dict:
    return {
        "dev": st.st_dev, "ino": st.st_ino, "size": st.st_size,
        "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns,
    }


def seal_matches(st: os.stat_result, seal: object) -> bool:
    if not isinstance(seal, dict):
        return False
    return (
        stat_module.S_ISREG(st.st_mode)
        and st.st_nlink == 1
        and all(
            isinstance(seal.get(name), int) and seal[name] == value
            for name, value in file_seal(st).items()
        )
    )


def write_all(fd: int, data: bytes) -> os.stat_result:
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError(errno.EIO, "short write on projection file")
        view = view[count:]
    return os.fstat(fd)


def stage(
    dir_fd: int, data: bytes | Callable[[int], None], *, prefix: str
) -> tuple[str, int]:
    for _ in range(20):
        name = f"{prefix}{uuid4().hex}.tmp"
        try:
            fd = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=dir_fd,
            )
        except FileExistsError:
            continue
        try:
            if callable(data):
                data(fd)
            else:
                write_all(fd, data)
            os.fsync(fd)
            st = os.fstat(fd)
            if not stat_module.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise OSError("staged projection is not a single-link regular file")
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(name, dir_fd=dir_fd)
            except OSError:
                pass
            raise
        return name, fd
    raise OSError(errno.EEXIST, "could not allocate a projection temp file")


def move_aside(
    dir_fd: int, name: str, *, retire_regular: bool = False
) -> str | None:
    try:
        st = os.lstat(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    if (
        not retire_regular
        and stat_module.S_ISREG(st.st_mode)
        and st.st_nlink == 1
    ):
        return None
    if stat_module.S_ISDIR(st.st_mode):
        try:
            os.rmdir(name, dir_fd=dir_fd)
            return None
        except OSError:
            pass
    aside = f"{name}.collision-{uuid4().hex[:8]}"
    os.rename(name, aside, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    return aside


def publish(
    dir_fd: int,
    name: str,
    data: bytes | Callable[[int], None],
    *,
    prefix: str,
    aside: bool = True,
    expect: dict | None = None,
    verify: Callable[[int, os.stat_result], None] | None = None,
) -> os.stat_result | None:
    temp_name, fd = stage(dir_fd, data, prefix=prefix)
    try:
        staged = os.fstat(fd)
        if not stat_module.S_ISREG(staged.st_mode) or staged.st_nlink != 1:
            raise OSError("staged projection is not a single-link regular file")
        if expect is not None:
            try:
                holder = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            except OSError:
                return None
            if not seal_matches(holder, expect):
                return None
        if aside:
            move_aside(dir_fd, name)
        os.replace(temp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        temp_name = None
        os.fsync(dir_fd)
        published = os.fstat(fd)
        holder = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if (
            (published.st_size, published.st_mtime_ns)
            != (staged.st_size, staged.st_mtime_ns)
            or (holder.st_dev, holder.st_ino)
            != (published.st_dev, published.st_ino)
        ):
            raise OSError("projection changed during publication")
        if verify is not None:
            verify(fd, published)
        return published
    finally:
        os.close(fd)
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=dir_fd)
            except OSError:
                pass


def replace_file(path: Path, data: bytes, *, prefix: str) -> os.stat_result | None:
    """Atomically replace the file at `path`: stage, fsync, rename over
    whatever single entry holds the name (a planted link or special file is
    replaced, never followed or opened), dirsync. The parent is opened as a
    directory through no link."""
    dir_fd = os.open(path.parent, DIRECTORY_FLAGS)
    try:
        return publish(dir_fd, path.name, data, prefix=prefix, aside=False)
    finally:
        os.close(dir_fd)


def record(
    conn: sqlite3.Connection,
    lesson: dict,
    error: type[Exception],
    *,
    replay: Callable[[], dict | None],
    validate: Callable[[], object],
    write: Callable[[object], object],
    project: Callable[[object], dict],
) -> dict:
    with lesson_lock(lesson["slug"]):
        found = replay()
    if found is not None:
        return found
    try:
        prepared = validate()
    except error:
        with lesson_lock(lesson["slug"]):
            found = replay()
        if found is not None:
            return found
        raise
    with lesson_lock(lesson["slug"]):
        found = replay()
        if found is not None:
            return found
        try:
            with immediate(conn):
                committed = write(prepared)
        except sqlite3.IntegrityError:
            found = replay()
            if found is None:
                raise
            return found
        return project(committed)
