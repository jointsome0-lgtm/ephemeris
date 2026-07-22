"""Pure reads and conflict-aware writes for lesson editor artifacts.

Paths come only from the record-time manifest.  Filesystem operations anchor
to directory descriptors so a learner-controlled parent cannot redirect a
save between validation and publication.
"""
from __future__ import annotations

import errno
import hashlib
import os
import re
import sqlite3
import stat as stat_module
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import uuid4

from ..db import append_event
from . import bundle_schema, lessons
from .runner_registry import RUNNER_REGISTRY


MAX_FILE_BYTES = 64 * 1024
MAX_BODY_BYTES = 512 * 1024
MAX_DEPTH_BELOW_ROOT = 4
RATE_WINDOW_SECONDS = 60.0
RATE_MAX_PER_WINDOW = 30
FILE_REV_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")

_monotonic = time.monotonic
_rate_lock = threading.Lock()
_rate: dict[int, deque[float]] = {}
_bundle_locks_lock = threading.Lock()
_bundle_locks: dict[str, threading.RLock] = {}

_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


class ArtifactError(Exception):
    def __init__(
        self, code: str, status: int, detail: str = "", **fields: object
    ) -> None:
        super().__init__(detail or code)
        self.code = code
        self.status = status
        self.detail = detail
        self.fields = fields


class _MissingPath(Exception):
    pass


@dataclass
class _OpenArtifact:
    fd: int
    data: bytes
    identity: tuple[int, ...]


@dataclass(frozen=True)
class RunSnapshot:
    block_id: str
    runner_id: str
    filename: str
    file_rev: str
    data: bytes


def bundle_lock(slug: str) -> threading.RLock:
    """The phase-F per-bundle lock shared by save and later run admission."""
    with _bundle_locks_lock:
        lock = _bundle_locks.get(slug)
        if lock is None:
            lock = _bundle_locks[slug] = threading.RLock()
        return lock


def _reset_rate_limit() -> None:
    with _rate_lock:
        _rate.clear()


def _check_rate(lesson_id: int) -> float:
    now = _monotonic()
    with _rate_lock:
        window = _rate.setdefault(lesson_id, deque())
        while window and now - window[0] > RATE_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= RATE_MAX_PER_WINDOW:
            retry = max(1, int(RATE_WINDOW_SECONDS - (now - window[0])) + 1)
            raise ArtifactError(
                "rate-limited", 429, f"retry after ~{retry}s", retry_after=retry
            )
        window.append(now)
        return now


def _refund_rate(lesson_id: int, stamp: float | None) -> None:
    if stamp is None:
        return
    with _rate_lock:
        window = _rate.get(lesson_id)
        if window is not None:
            try:
                window.remove(stamp)
            except ValueError:
                pass


def _require_eligible(read: bundle_schema.ManifestRead) -> None:
    if read.rejected:
        raise ArtifactError(
            "manifest-rejected", 409,
            "the lesson manifest is rejected; artifact access is refused",
        )
    if "identity-mismatch" in read.codes():
        raise ArtifactError(
            "identity-mismatch", 409,
            "manifest lesson_uid differs from the DB uid",
        )
    if not read.bridge_eligible:
        raise ArtifactError(
            "blocks-unavailable", 409,
            "this lesson's manifest/profile grants no editor affordance",
        )


def _resolve_block(
    read: bundle_schema.ManifestRead, block_id: str, *, for_save: bool
) -> tuple[dict, tuple[str, ...]]:
    if not isinstance(block_id, str) or not bundle_schema.BLOCK_ID_RE.match(block_id):
        raise ArtifactError(
            "invalid-block-id", 400, "block_id must match blk_[a-z0-9]{4,32}"
        )
    block = next((item for item in read.blocks if item["id"] == block_id), None)
    if block is None:
        raise ArtifactError(
            "unknown-block", 422,
            "block_id is not declared in the record-time manifest",
        )
    file_parts = PurePosixPath(block["file"]).parts
    roots = [
        root for root in read.artifact_roots
        if block["file"].startswith(root + "/")
    ]
    if not roots:
        raise ArtifactError(
            "undiscoverable-path", 422, "block file is outside its artifact root"
        )
    root_parts = PurePosixPath(max(roots, key=len)).parts
    below_root = file_parts[len(root_parts):]
    if not below_root:
        raise ArtifactError(
            "undiscoverable-path", 422, "block file does not name an artifact"
        )
    if for_save and len(below_root) > MAX_DEPTH_BELOW_ROOT:
        raise ArtifactError(
            "undiscoverable-path", 422,
            f"block file is deeper than {MAX_DEPTH_BELOW_ROOT} levels below its artifact root",
        )
    return block, file_parts


def _stat_identity(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev, st.st_ino, st.st_mode, st.st_nlink, st.st_size,
        st.st_mtime_ns, st.st_ctime_ns,
    )


def _open_lesson_root(lesson: dict) -> int:
    try:
        return os.open(lessons._lesson_dir(lesson["slug"]), _DIRECTORY_FLAGS)
    except (KeyError, OSError, lessons.LessonError) as exc:
        raise ArtifactError(
            "unsafe-file", 409, "lesson bundle root cannot be opened safely"
        ) from exc


def _open_parent(root_fd: int, parts: tuple[str, ...], *, create: bool) -> int:
    current = os.dup(root_fd)
    try:
        for part in parts:
            try:
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
            except FileNotFoundError:
                if not create:
                    raise _MissingPath from None
                created = False
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current)
                    created = True
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ArtifactError(
                        "unsafe-file", 409,
                        "artifact parent cannot be created safely",
                    ) from exc
                if created:
                    try:
                        os.fsync(current)
                    except OSError as exc:
                        raise ArtifactError(
                            "unsafe-file", 409,
                            "artifact parent creation could not be made durable",
                        ) from exc
                try:
                    child = os.open(part, _DIRECTORY_FLAGS, dir_fd=current)
                except OSError as exc:
                    raise ArtifactError(
                        "unsafe-file", 409,
                        "artifact parent is a symlink or non-directory",
                    ) from exc
            except OSError as exc:
                raise ArtifactError(
                    "unsafe-file", 409,
                    "artifact parent is a symlink or non-directory",
                ) from exc
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _open_artifact(parent_fd: int, name: str) -> _OpenArtifact | None:
    try:
        fd = os.open(name, _READ_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ArtifactError(
            "unsafe-file", 409, "artifact is a symlink or cannot be opened safely"
        ) from exc
    try:
        first = os.fstat(fd)
        if not stat_module.S_ISREG(first.st_mode) or first.st_nlink != 1:
            raise ArtifactError(
                "unsafe-file", 409,
                "artifact must be a single-link regular file",
            )
        if first.st_size > MAX_FILE_BYTES:
            raise ArtifactError(
                "file-too-large", 413,
                f"artifact exceeds {MAX_FILE_BYTES} bytes",
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(16 * 1024, MAX_FILE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ArtifactError(
                    "file-too-large", 413,
                    f"artifact exceeds {MAX_FILE_BYTES} bytes",
                )
        final = os.fstat(fd)
        if (
            not stat_module.S_ISREG(final.st_mode)
            or final.st_nlink != 1
            or final.st_size > MAX_FILE_BYTES
            or _stat_identity(final) != _stat_identity(first)
        ):
            raise ArtifactError(
                "unsafe-file", 409,
                "artifact identity changed while it was read",
            )
        return _OpenArtifact(fd, b"".join(chunks), _stat_identity(final))
    except BaseException:
        os.close(fd)
        raise


def _name_matches(parent_fd: int, name: str, opened: _OpenArtifact) -> bool:
    try:
        held = os.fstat(opened.fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return False
    return (
        _stat_identity(held) == opened.identity
        and _stat_identity(named) == opened.identity
    )


def _name_absent(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _revision(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _read_location(
    lesson: dict, file_parts: tuple[str, ...], *, create: bool
) -> tuple[int, str, _OpenArtifact | None] | None:
    root_fd = _open_lesson_root(lesson)
    try:
        try:
            parent_fd = _open_parent(root_fd, file_parts[:-1], create=create)
        except _MissingPath:
            return None
    finally:
        os.close(root_fd)
    try:
        opened = _open_artifact(parent_fd, file_parts[-1])
    except BaseException:
        os.close(parent_fd)
        raise
    return parent_fd, file_parts[-1], opened


def get_artifact(lesson: dict, block_id: str) -> dict:
    """Return one descriptor-bound UTF-8 artifact snapshot without mutation."""
    with bundle_lock(lesson["slug"]):
        read = lessons.read_bundle_readonly(lesson)
        _require_eligible(read)
        _block, file_parts = _resolve_block(read, block_id, for_save=False)
        location = _read_location(lesson, file_parts, create=False)
        if location is None:
            return {"exists": False, "content": "", "size": 0}
        parent_fd, _name, opened = location
        try:
            if opened is None:
                return {"exists": False, "content": "", "size": 0}
            try:
                content = opened.data.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ArtifactError(
                    "invalid-encoding", 422, "artifact is not strict UTF-8"
                ) from exc
            return {
                "exists": True,
                "content": content,
                "file_rev": _revision(opened.data),
                "size": len(opened.data),
            }
        finally:
            if opened is not None:
                os.close(opened.fd)
            os.close(parent_fd)


def get_run_snapshot(
    lesson: dict, block_id: str, file_rev: str
) -> RunSnapshot:
    """Validate one runnable block and return its exact execution bytes once."""
    if not isinstance(file_rev, str) or not FILE_REV_RE.match(file_rev):
        raise ArtifactError(
            "invalid-file-rev", 400,
            "file_rev must be sha256:<64 lowercase hex>",
        )
    with bundle_lock(lesson["slug"]):
        read = lessons.read_bundle_readonly(lesson)
        _require_eligible(read)
        block, file_parts = _resolve_block(read, block_id, for_save=False)
        runner_id = block.get("runner_id")
        spec = RUNNER_REGISTRY.get(runner_id) if isinstance(runner_id, str) else None
        if spec is None:
            raise ArtifactError(
                "unknown-runner", 422,
                "the block has no registered runner",
            )
        filename = file_parts[-1]
        if not spec.accepts(filename):
            raise ArtifactError(
                "incompatible-runner", 422,
                "the block file is incompatible with its runner",
            )
        location = _read_location(lesson, file_parts, create=False)
        if location is None:
            raise ArtifactError("file-missing", 409, "artifact file is missing")
        parent_fd, name, opened = location
        try:
            if opened is None:
                raise ArtifactError("file-missing", 409, "artifact file is missing")
            try:
                opened.data.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ArtifactError(
                    "invalid-encoding", 422, "artifact is not strict UTF-8"
                ) from exc
            current_rev = _revision(opened.data)
            if not _name_matches(parent_fd, name, opened):
                raise ArtifactError(
                    "file-conflict", 409,
                    "artifact changed while the run was being prepared",
                    file_rev=current_rev,
                )
            if file_rev != current_rev:
                raise ArtifactError(
                    "file-conflict", 409,
                    "file_rev does not match the current artifact",
                    file_rev=current_rev,
                )
            return RunSnapshot(
                block_id=block["id"], runner_id=runner_id,
                filename=filename, file_rev=current_rev, data=opened.data,
            )
        finally:
            if opened is not None:
                os.close(opened.fd)
            os.close(parent_fd)


def _clean_save(payload: dict) -> tuple[bytes, str]:
    content = payload.get("content")
    if not isinstance(content, str):
        raise ArtifactError("invalid-content", 400, "content must be a string")
    try:
        data = content.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ArtifactError("invalid-content", 400, "content is not valid UTF-8") from exc
    if len(data) > MAX_FILE_BYTES:
        raise ArtifactError(
            "file-too-large", 413, f"content exceeds {MAX_FILE_BYTES} UTF-8 bytes"
        )
    base_rev = payload.get("base_rev")
    if not isinstance(base_rev, str) or (
        base_rev != "absent" and not FILE_REV_RE.match(base_rev)
    ):
        raise ArtifactError(
            "invalid-base-rev", 400,
            "base_rev must be absent or sha256:<64 lowercase hex>",
        )
    return data, base_rev


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    written = 0
    while written < len(view):
        count = os.write(fd, view[written:])
        if count <= 0:
            raise OSError(errno.EIO, "short artifact write")
        written += count


def _stage_temp(parent_fd: int, data: bytes) -> str:
    for _ in range(20):
        name = f".artifact-{uuid4().hex}.tmp"
        try:
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent_fd,
            )
        except FileExistsError:
            continue
        try:
            _write_all(fd, data)
            os.fsync(fd)
            os.close(fd)
            fd = -1
        except BaseException:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(name, dir_fd=parent_fd)
            except OSError:
                pass
            raise
        return name
    raise OSError(errno.EEXIST, "could not allocate artifact temp file")


def save_artifact(
    conn: sqlite3.Connection, lesson: dict, block_id: str, payload: dict
) -> dict:
    """Compare and atomically publish one artifact under the phase-F lock."""
    stamp = _check_rate(lesson["id"])
    data, base_rev = _clean_save(payload)
    with bundle_lock(lesson["slug"]):
        read = lessons.read_bundle_readonly(lesson)
        _require_eligible(read)
        block, file_parts = _resolve_block(read, block_id, for_save=True)
        location = _read_location(lesson, file_parts, create=True)
        assert location is not None
        parent_fd, name, opened = location
        temp_name: str | None = None
        try:
            current_rev = _revision(opened.data) if opened is not None else None
            if opened is not None and opened.data == data:
                if not _name_matches(parent_fd, name, opened):
                    raise ArtifactError(
                        "file-conflict", 409,
                        "artifact changed while the save was in progress",
                        file_rev=current_rev,
                    )
                _refund_rate(lesson["id"], stamp)
                return {
                    "result": "unchanged",
                    "file_rev": current_rev,
                    "size": len(data),
                    "event_recorded": False,
                }

            expected = current_rev if current_rev is not None else "absent"
            if base_rev != expected:
                raise ArtifactError(
                    "file-conflict", 409,
                    "base_rev does not match the current artifact",
                    file_rev=current_rev,
                )

            temp_name = _stage_temp(parent_fd, data)
            identity_ok = (
                _name_matches(parent_fd, name, opened)
                if opened is not None
                else _name_absent(parent_fd, name)
            )
            if not identity_ok:
                raise ArtifactError(
                    "file-conflict", 409,
                    "artifact changed immediately before publication",
                    file_rev=current_rev,
                )
            os.replace(
                temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd
            )
            temp_name = None
            os.fsync(parent_fd)
        except ArtifactError:
            raise
        except OSError as exc:
            raise ArtifactError(
                "unsafe-file", 409, "artifact could not be published safely"
            ) from exc
        finally:
            if temp_name is not None:
                try:
                    os.unlink(temp_name, dir_fd=parent_fd)
                except OSError:
                    pass
            if opened is not None:
                os.close(opened.fd)
            os.close(parent_fd)

        file_rev = _revision(data)
        event_recorded = False
        try:
            with conn:
                append_event(conn, "lesson_artifact_saved", {
                    "lesson_uid": lesson["uid"],
                    "lesson_id": lesson["id"],
                    "slug": lesson["slug"],
                    "block_id": block["id"],
                    "file": block["file"],
                    "file_rev": file_rev,
                    "size": len(data),
                    "created": current_rev is None,
                })
            event_recorded = True
        except sqlite3.Error:
            # The file is the durable learner record.  Telemetry is explicitly
            # best-effort and the response makes loss observable.
            event_recorded = False
        return {
            "result": "saved",
            "file_rev": file_rev,
            "size": len(data),
            "event_recorded": event_recorded,
        }
