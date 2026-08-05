"""Phase-F HTTP coordinator for immutable lesson runner jobs."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import stat as stat_module
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .. import runner
from ..db import DATA_DIR, DB_PATH, append_event, get_conn
from . import artifacts, bundle_schema, lessons


RATE_WINDOW_SECONDS = 60.0
RATE_MAX_PER_WINDOW = 10
MAX_BODY_BYTES = 16 * 1024
MAX_KEY_LEN = 128
PROJECTION_NAME = "runs.jsonl"
PROJECTION_STATE_DIR = DATA_DIR / "run-projections"
OUTPUT_TAIL_BYTES = 8 * 1024
RECORD_KIND = "run"
RECORD_VERSION = 1
# The `lesson_run` ledger event stays body-free — the documented contract
# (docs/lesson-artifacts-api.md, docs/learn-bundle-spec.md §8). The bounded
# output tail is a bundle-projection field only: the ledger and its JSONL
# export carry run identity and result, never what the learner's code printed.
EVENT_OMITTED_FIELDS = ("output_tail", "output_tail_truncated")

_monotonic = time.monotonic
_rate_lock = threading.Lock()
_rate: dict[str, deque[float]] = {}


class RunRequestError(Exception):
    def __init__(self, code: str, status: int, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.status = status
        self.detail = detail


def _reset_rate_limit() -> None:
    with _rate_lock:
        _rate.clear()


def _check_rate(lesson_key: str) -> float:
    now = _monotonic()
    with _rate_lock:
        window = _rate.setdefault(lesson_key, deque())
        while window and now - window[0] > RATE_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= RATE_MAX_PER_WINDOW:
            retry = max(1, int(RATE_WINDOW_SECONDS - (now - window[0])) + 1)
            exc = runner.RateLimitedError(lesson_key)
            exc.retry_after = retry
            raise exc
        window.append(now)
        return now


def _refund_rate(lesson_key: str, stamp: object) -> None:
    if not isinstance(stamp, float):
        return
    with _rate_lock:
        window = _rate.get(lesson_key)
        if window is None:
            return
        try:
            window.remove(stamp)
        except ValueError:
            pass


def _valid_key(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_KEY_LEN:
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return not any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def clean_start_payload(payload: dict) -> tuple[str, str]:
    file_rev = payload.get("file_rev")
    if not isinstance(file_rev, str) or not artifacts.FILE_REV_RE.match(file_rev):
        raise RunRequestError(
            "invalid-file-rev", 400,
            "file_rev must be sha256:<64 lowercase hex>",
        )
    key = payload.get("idempotency_key")
    if not _valid_key(key):
        raise RunRequestError(
            "invalid-idempotency-key", 400,
            f"idempotency_key must be 1-{MAX_KEY_LEN} chars with no control characters",
        )
    return file_rev, key


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _runner_private_masks(data_root: Path, db_path: Path) -> tuple[str, ...]:
    """Mask an external DB override in addition to the runner's root mask."""
    root_absolute = data_root.absolute()
    root_resolved = root_absolute.resolve(strict=False)
    db_absolute = db_path.absolute()
    candidates = (
        db_absolute.parent,
        db_absolute.resolve(strict=False).parent,
    )
    masks: list[str] = []
    for candidate in candidates:
        if not (
            _inside(candidate, root_absolute)
            or _inside(candidate, root_resolved)
        ):
            masks.append(str(candidate))
    return tuple(dict.fromkeys(masks))


def prepare_request(
    lesson: dict,
    block_id: str,
    file_rev: str,
    idempotency_key: str,
) -> runner.RunnerRequest:
    snapshot = artifacts.get_run_snapshot(lesson, block_id, file_rev)
    data_root = DATA_DIR.absolute()
    bundle_root = lessons.LESSONS_DIR.absolute()
    bundle_dir = lessons._lesson_dir(lesson["slug"]).absolute()
    return runner.RunnerRequest(
        lesson_key=lesson["uid"],
        block_id=snapshot.block_id,
        file_rev=snapshot.file_rev,
        idempotency_key=idempotency_key,
        runner_id=snapshot.runner_id,
        filename=snapshot.filename,
        snapshot=snapshot.data,
        bundle_dir=str(bundle_dir),
        bundle_root=str(bundle_root),
        private_root=str(data_root),
        private_masks=_runner_private_masks(data_root, DB_PATH),
        lesson_uid=lesson["uid"],
        lesson_id=lesson["id"],
        slug=lesson["slug"],
    )


def _terminal_event(job: runner.RunnerJob) -> dict:
    return next(event for event in reversed(job.events) if event["event"] == "exit")


def _output_tail(job: runner.RunnerJob) -> tuple[str, bool]:
    """Return the ordered combined stdout/stderr suffix at a UTF-8 byte cap."""
    data = "".join(
        event["text"] for event in job.events if event["event"] == "output"
    ).encode("utf-8")
    if len(data) <= OUTPUT_TAIL_BYTES:
        return data.decode("utf-8"), False
    start = len(data) - OUTPUT_TAIL_BYTES
    while start < len(data) and data[start] & 0xC0 == 0x80:
        start += 1
    return data[start:].decode("utf-8"), True


def _projection_record(job: runner.RunnerJob, terminal: dict) -> dict:
    finished_at = datetime.now(timezone.utc)
    duration_ms = max(0, int(terminal["duration_ms"]))
    started_at = finished_at - timedelta(milliseconds=duration_ms)
    output_tail, output_tail_truncated = _output_tail(job)
    return {
        "kind": RECORD_KIND,
        "v": RECORD_VERSION,
        "run_id": job.job_id,
        "lesson_uid": job.request.lesson_uid,
        "block_id": job.request.block_id,
        "runner_id": job.request.runner_id,
        "file_rev": job.request.file_rev,
        "cause": terminal["cause"],
        "exit_code": terminal.get("exit_code"),
        "signal": terminal.get("signal"),
        "duration_ms": duration_ms,
        "truncated": bool(terminal["truncated"]),
        "started_at": started_at.isoformat(timespec="microseconds"),
        "finished_at": finished_at.isoformat(timespec="microseconds"),
        "output_tail": output_tail,
        "output_tail_truncated": output_tail_truncated,
    }


def _ensure_projection_state_dir() -> None:
    PROJECTION_STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not stat_module.S_ISDIR(os.lstat(PROJECTION_STATE_DIR).st_mode):
        raise OSError("run projection state root is not a directory")


@contextmanager
def _projection_file_lock(job: runner.RunnerJob):
    """Serialize one bundle's projection outside the agent-writable bundle."""
    uid = job.request.lesson_uid
    if not isinstance(uid, str) or bundle_schema.UUID_RE.match(uid) is None:
        raise OSError("run has no safe lesson identity")
    try:
        import fcntl
    except ImportError as exc:
        raise OSError("advisory file locking (fcntl.flock) is unavailable") from exc

    _ensure_projection_state_dir()
    fd = os.open(
        PROJECTION_STATE_DIR / f"{uid}.lock",
        os.O_RDWR | os.O_CREAT | os.O_NONBLOCK
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        st = os.fstat(fd)
        if not stat_module.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise OSError("unsafe run projection lock file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError("short write on run projection")
        view = view[count:]


def _publish_projection_line(job: runner.RunnerJob, line: bytes) -> None:
    """Atomically append one line without trusting an existing bundle node."""
    bundle_root = Path(job.request.bundle_root).absolute()
    bundle_dir = Path(job.request.bundle_dir).absolute()
    if bundle_dir == bundle_root or not _inside(bundle_dir, bundle_root):
        raise OSError("run bundle is outside its configured root")

    dir_fd = os.open(
        bundle_dir,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    tmp_name = f".runs-{uuid4().hex}.tmp"
    tmp_fd = -1
    source_fd = -1
    try:
        tmp_fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=dir_fd,
        )
        try:
            source_fd = os.open(
                PROJECTION_NAME,
                os.O_RDONLY | os.O_NONBLOCK
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                dir_fd=dir_fd,
            )
        except FileNotFoundError:
            source_fd = -1
        if source_fd >= 0:
            source_st = os.fstat(source_fd)
            if not stat_module.S_ISREG(source_st.st_mode) or source_st.st_nlink != 1:
                raise OSError("unsafe existing run projection")
            copied = 0
            last = b""
            while True:
                chunk = os.read(source_fd, 1 << 20)
                if not chunk:
                    break
                _write_all(tmp_fd, chunk)
                copied += len(chunk)
                last = chunk[-1:]
            if copied != source_st.st_size:
                raise OSError("run projection changed while copying")
            if copied and last != b"\n":
                raise OSError("existing run projection has an incomplete line")
        _write_all(tmp_fd, line)
        os.fsync(tmp_fd)
        staged = os.fstat(tmp_fd)
        if not stat_module.S_ISREG(staged.st_mode) or staged.st_nlink != 1:
            raise OSError("unsafe staged run projection")
        os.replace(
            tmp_name, PROJECTION_NAME, src_dir_fd=dir_fd, dst_dir_fd=dir_fd
        )
        tmp_name = ""
        os.fsync(dir_fd)
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if tmp_fd >= 0:
            os.close(tmp_fd)
        if tmp_name:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except OSError:
                pass
        os.close(dir_fd)


def _project_finish(job: runner.RunnerJob, record: dict) -> None:
    line = (
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    with _projection_file_lock(job):
        _publish_projection_line(job, line)


def status_view(job: runner.RunnerJob) -> dict:
    result: dict[str, object] = {
        "job_id": job.job_id,
        "state": job.state,
        "block_id": job.request.block_id,
        "runner_id": job.request.runner_id,
        "file_rev": job.request.file_rev,
        "event_recorded": bool(job.event_recorded),
    }
    if job.state == runner.FINISHED:
        terminal = _terminal_event(job)
        for name in (
            "cause", "exit_code", "signal", "truncated", "duration_ms"
        ):
            if name in terminal:
                result[name] = terminal[name]
    return result


def _record_finish_sync(job: runner.RunnerJob) -> bool:
    terminal = _terminal_event(job)
    record = _projection_record(job, terminal)
    payload: dict[str, object] = {
        "lesson_id": job.request.lesson_id,
        "slug": job.request.slug,
        **{
            name: value for name, value in record.items()
            if name not in EVENT_OMITTED_FIELDS
        },
    }
    conn = get_conn()
    try:
        with conn:
            append_event(conn, "lesson_run", payload)
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    try:
        _project_finish(job, record)
    except Exception:
        # The ledger event is authoritative and already committed. Projection
        # is best-effort, like assessments.jsonl, and must not undo that fact.
        pass
    return True


async def _record_finish(job: runner.RunnerJob) -> bool:
    return await asyncio.to_thread(_record_finish_sync, job)


def create_service() -> runner.RunnerService:
    _reset_rate_limit()
    return runner.RunnerService(
        rate_hook=_check_rate,
        rate_refund_hook=_refund_rate,
        finish_hook=_record_finish,
    )


async def start(
    service: runner.RunnerService,
    lesson: dict,
    block_id: str,
    payload: dict,
) -> runner.Admission:
    lesson_key = lesson["uid"]
    try:
        file_rev, key = clean_start_payload(payload)
    except RunRequestError:
        await service.charge_validation_refusal(lesson_key)
        raise

    async with service.prepare_start(lesson_key):
        preflight = await service.preflight(lesson_key, key, block_id, file_rev)
        if isinstance(preflight, runner.Admission):
            return preflight
        request = await asyncio.to_thread(
            prepare_request, lesson, block_id, file_rev, key
        )
        return await service.admit(request, rate_permit=preflight.rate_charge)
