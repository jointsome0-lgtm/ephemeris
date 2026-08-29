"""Phase-F HTTP coordinator for immutable lesson runner jobs."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import stat as stat_module
import tempfile
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .. import runner
from ..db import DATA_DIR, append_event, get_conn
from . import artifacts, bundle_schema, lessons


RATE_WINDOW_SECONDS = 60.0
RATE_MAX_PER_WINDOW = 10
MAX_BODY_BYTES = 16 * 1024
MAX_KEY_LEN = 128
PROJECTION_NAME = "runs.jsonl"
PROJECTION_STATE_DIR = DATA_DIR / "run-projections"
OUTPUT_TAIL_BYTES = 8 * 1024
# Durable retention bound for one lesson's run history (spec §6.6). The tail
# above caps a RECORD, not the file: JSON escaping expands control bytes, so a
# legal 8 KiB output tail can serialize to roughly 50 KiB, and ten starts a
# minute append forever. Past this many bytes the projection is compacted —
# oldest whole records dropped, newest kept — which is what its own readers
# already do (`lessons.py`: newest complete lines within 2 MiB). A record is
# never split and the newest record always survives, even alone.
PROJECTION_MAX_BYTES = 20 * 1024 * 1024
PROJECTION_STATE_VERSION = 1
PROJECTION_STATE_MAX_BYTES = 4 * 1024
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


def prepare_request(
    lesson: dict,
    block_id: str,
    file_rev: str,
    idempotency_key: str,
) -> runner.RunnerRequest:
    snapshot = artifacts.get_run_snapshot(lesson, block_id, file_rev)
    return runner.RunnerRequest(
        lesson_key=lesson["uid"],
        block_id=snapshot.block_id,
        file_rev=snapshot.file_rev,
        idempotency_key=idempotency_key,
        runner_id=snapshot.runner_id,
        filename=snapshot.filename,
        snapshot=snapshot.data,
        bundle_dir=str(lessons._lesson_dir(lesson["slug"]).absolute()),
        bundle_root=str(lessons.LESSONS_DIR.absolute()),
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


def _finished_at(job: runner.RunnerJob) -> datetime:
    """Wall clock at the runner's terminal event, not at hook execution.

    The finish hook runs on a worker thread and can lag the exit it reports —
    behind a busy pool, or behind a sibling lesson's projection. `now()` would
    then date the run to when the app got around to recording it. The runner
    stamped `finished_monotonic` at the terminal event, so subtract however
    long ago that was.
    """
    now = datetime.now(timezone.utc)
    if job.finished_monotonic is None:
        return now
    return now - timedelta(
        seconds=max(0.0, _monotonic() - job.finished_monotonic)
    )


def _projection_record(job: runner.RunnerJob, terminal: dict) -> dict:
    finished_at = _finished_at(job)
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


def _safe_uid(uid: object) -> str:
    if not isinstance(uid, str) or bundle_schema.UUID_RE.match(uid) is None:
        raise OSError("run has no safe lesson identity")
    return uid


@contextmanager
def _projection_file_lock(uid: str):
    """Serialize one bundle's projection outside the agent-writable bundle."""
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


def _file_seal(st: os.stat_result) -> dict:
    return {
        "dev": st.st_dev, "ino": st.st_ino, "size": st.st_size,
        "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns,
    }


def _seal_matches(st: os.stat_result, seal: object) -> bool:
    """Is this the exact file the app last published, byte for byte?"""
    if not isinstance(seal, dict):
        return False
    return (
        stat_module.S_ISREG(st.st_mode)
        and st.st_nlink == 1
        and all(
            isinstance(seal.get(name), int) and seal[name] == value
            for name, value in _file_seal(st).items()
        )
    )


def _read_state(uid: str) -> dict | None:
    try:
        data = (PROJECTION_STATE_DIR / f"{uid}.json").read_bytes()
    except OSError:
        return None
    if len(data) > PROJECTION_STATE_MAX_BYTES:
        return None
    try:
        state = json.loads(data)
    except ValueError:
        return None
    if (
        not isinstance(state, dict)
        or state.get("v") != PROJECTION_STATE_VERSION
        or state.get("lesson_uid") != uid
        or not isinstance(state.get("file"), dict)
    ):
        return None
    return state


def _write_state(uid: str, st: os.stat_result) -> None:
    _ensure_projection_state_dir()
    data = (json.dumps({
        "v": PROJECTION_STATE_VERSION,
        "lesson_uid": uid,
        "file": _file_seal(st),
    }, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    fd, tmp_name = tempfile.mkstemp(dir=PROJECTION_STATE_DIR, prefix=".run-state-")
    try:
        try:
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_name, PROJECTION_STATE_DIR / f"{uid}.json")
        # The line itself is already fsynced, so a seal lost to a crash here
        # would outlive the data it describes: the next run would read the
        # app's own history as foreign and move it aside. Durable seal or no
        # published line — never the other order.
        parent_fd = os.open(
            PROJECTION_STATE_DIR,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


APPENDED = "appended"
FOREIGN = "foreign"  # not ours, or not there — the caller must not rewrite it
OVERSIZE = "oversize"  # ours, but appending would cross PROJECTION_MAX_BYTES


def _append_to_sealed(dir_fd: int, uid: str, line: bytes, state: dict) -> str:
    """Append in place when the file on the name is still exactly ours.

    `FOREIGN` means "not ours, or not there" — never "append failed": the
    caller must not fall back to rewriting a file this refused to recognise. A
    failure after the first byte is written raises instead, leaving the seal
    stale so the next run repairs the torn line by starting a fresh file.
    `OVERSIZE` is the opposite answer — the file IS ours and is recognised, it
    just has no room left, so the caller compacts it rather than retiring it.
    The size is decided before any byte is written: the bound is on the
    published file, never on a file that momentarily exceeded it.
    """
    try:
        fd = os.open(
            PROJECTION_NAME,
            os.O_RDWR | os.O_APPEND | os.O_NONBLOCK
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=dir_fd,
        )
    except OSError:
        return FOREIGN
    try:
        before = os.fstat(fd)
        if not _seal_matches(before, state["file"]):
            return FOREIGN
        if before.st_size + len(line) > PROJECTION_MAX_BYTES:
            return OVERSIZE
        _write_all(fd, line)
        os.fsync(fd)
        after = os.fstat(fd)
        if (
            not stat_module.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
            or after.st_size != before.st_size + len(line)
            or os.pread(fd, len(line), before.st_size) != line
        ):
            raise OSError("run projection changed during append")
        _write_state(uid, after)
        return APPENDED
    finally:
        os.close(fd)


def _move_aside(dir_fd: int) -> bool:
    """Retire whatever holds the projection name. True when something moved."""
    try:
        os.stat(PROJECTION_NAME, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    os.rename(
        PROJECTION_NAME, f"{PROJECTION_NAME}.collision-{uuid4().hex[:12]}",
        src_dir_fd=dir_fd, dst_dir_fd=dir_fd,
    )
    os.fsync(dir_fd)
    return True


def _publish_whole(
    dir_fd: int, uid: str, payload: bytes, expect: dict | None = None,
) -> bool:
    """Stage `payload` beside the projection, then take the name atomically.

    Every durable guarantee of the write path lives here: the staged bytes are
    fsynced and checked to be a regular single-link file before the rename, the
    directory entry is fsynced after it, and the seal is written for the inode
    whose bytes were actually synced. A crash anywhere leaves either the whole
    old file or the whole new one on the name — never a half-written log.

    `expect` (a seal) makes the swap CONDITIONAL, and a compaction passes one:
    staging up to the ceiling is a long window on an agent-writable directory,
    and `os.replace` would silently destroy whatever appeared on the name
    meanwhile. False then means "someone else holds the name" — nothing was
    replaced, and the caller preserves what it found the way §6.5 does. The
    check cannot be fused with the rename (POSIX has no rename-if-inode), so a
    hair-thin window remains; it is the same one `_move_aside` already leaves
    between retiring a file and creating its successor.
    """
    tmp_name = f".runs-{uuid4().hex}.tmp"
    tmp_fd = os.open(
        tmp_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=dir_fd,
    )
    try:
        try:
            _write_all(tmp_fd, payload)
            os.fsync(tmp_fd)
            staged = os.fstat(tmp_fd)
            if not stat_module.S_ISREG(staged.st_mode) or staged.st_nlink != 1:
                raise OSError("unsafe staged run projection")
        finally:
            os.close(tmp_fd)
        if expect is not None:
            try:
                holder = os.stat(PROJECTION_NAME, dir_fd=dir_fd, follow_symlinks=False)
            except OSError:
                return False
            if not _seal_matches(holder, expect):
                return False
        os.replace(tmp_name, PROJECTION_NAME, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        tmp_name = ""
        os.fsync(dir_fd)
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except OSError:
                pass
    published = os.open(
        PROJECTION_NAME,
        os.O_RDONLY | os.O_NONBLOCK
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        dir_fd=dir_fd,
    )
    try:
        published_st = os.fstat(published)
    finally:
        os.close(published)
    # Seal the inode whose bytes were just fsynced, not merely whatever holds
    # the name now: the bundle is writable, so the name can be swapped in the
    # window between the rename and this stat. Blessing that file would make
    # the seal certify content the app never wrote — the one thing it exists
    # to prevent. Refusing leaves no seal, so the next run starts over.
    if (published_st.st_dev, published_st.st_ino) != (staged.st_dev, staged.st_ino):
        raise OSError("run projection was replaced under the publisher")
    _write_state(uid, published_st)
    return True


def _restart_projection(dir_fd: int, uid: str, line: bytes) -> None:
    """Publish a fresh single-line file, preserving whatever held the name.

    Anything the seal does not recognise was not written by the app: a forged
    or hand-edited log, a stale file restored beside a fresh state dir, or our
    own torn append. Adopting it would let those bytes reach the next tutor as
    app-recorded history, so it is moved aside the way §6.5 moves a foreign
    node — never adopted, never written through, never deleted.
    """
    _move_aside(dir_fd)
    _publish_whole(dir_fd, uid, line)


def _read_sealed_tail(dir_fd: int, state: dict, budget: int) -> bytes | None:
    """The last `budget`+1 bytes of the published file, or None unless ours.

    Bounded on purpose: the first run after this ceiling ships meets a file
    written under the old unbounded contract, which can be far larger than the
    ceiling itself, and reading it whole would put its entire size in memory on
    the finish hook. One byte past the budget is exactly enough to decide the
    retained tail — any record boundary that matters lies inside it, and that
    extra byte is what says whether the cut is already on one.

    The seal is re-checked here rather than trusted from the append attempt:
    the bundle is agent-writable, so the file can be swapped between the two.
    Content that does not end on a record boundary is refused as well — a torn
    tail is exactly what must never be adopted into a rewritten history.
    """
    try:
        fd = os.open(
            PROJECTION_NAME,
            os.O_RDONLY | os.O_NONBLOCK
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=dir_fd,
        )
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not _seal_matches(st, state["file"]):
            return None
        want = min(st.st_size, max(0, budget) + 1)
        parts: list[bytes] = []
        offset = st.st_size - want
        remaining = want
        while remaining > 0:
            chunk = os.pread(fd, min(remaining, 1 << 20), offset)
            if not chunk:
                break
            parts.append(chunk)
            offset += len(chunk)
            remaining -= len(chunk)
        data = b"".join(parts)
        if len(data) != want or (data and not data.endswith(b"\n")):
            return None
        return data
    finally:
        os.close(fd)


def _retained_tail(data: bytes, budget: int) -> bytes:
    """The newest WHOLE records of `data` that fit in `budget` bytes.

    Cutting mid-record would hand the next reader a line it must skip and, in
    the worst case, a syntactically valid fragment of one — so the cut always
    lands just after a newline, dropping the record it fell inside.
    """
    if budget <= 0 or not data:
        return b""
    if len(data) <= budget:
        return data
    start = len(data) - budget
    if data[start - 1:start] != b"\n":
        boundary = data.find(b"\n", start)
        if boundary < 0:
            return b""
        start = boundary + 1
    return data[start:]


def _compact_budget() -> int:
    """How much of the file compaction keeps: three quarters of the ceiling.

    Not the ceiling itself. Refilling the file exactly would put the very next
    run over it again, so every later run would rewrite the whole projection —
    on the finish hook terminal status and cancel wait on. The quarter of
    headroom is what makes that copy amortised instead of per-run.
    """
    return PROJECTION_MAX_BYTES * 3 // 4


def _compact_projection(dir_fd: int, uid: str, line: bytes, state: dict) -> bool:
    """Republish the newest records that fit, plus this run's line.

    False means the file stopped being ours — between the append attempt and
    the read, or between the read and the swap. The caller then treats it as
    foreign, because a rewrite that adopted or destroyed it would launder or
    lose bytes the app cannot verify. The newest record always survives: if it
    alone exceeds the bound it is published alone rather than dropped, since a
    run that cannot be recorded at all is worse than a bound overshot by one
    line.
    """
    budget = max(0, _compact_budget() - len(line))
    data = _read_sealed_tail(dir_fd, state, budget)
    if data is None:
        return False
    return _publish_whole(
        dir_fd, uid, _retained_tail(data, budget) + line, expect=state["file"],
    )


def _publish_projection_line(job: runner.RunnerJob, line: bytes) -> None:
    """Append one line, trusting only what the app itself last published.

    Constant work per run: one append and one small seal write, never a copy
    of the accumulated history. The finish hook gates `event_attempted`, which
    terminal status and cancel wait on, so the cost of a run's projection must
    not grow with how much the learner has already run. Compaction at the
    retention bound is the one exception and stays amortised: it copies at most
    PROJECTION_MAX_BYTES once per bound's worth of appended records.
    """
    bundle_root = Path(job.request.bundle_root).absolute()
    bundle_dir = Path(job.request.bundle_dir).absolute()
    if bundle_dir == bundle_root or not _inside(bundle_dir, bundle_root):
        raise OSError("run bundle is outside its configured root")

    uid = _safe_uid(job.request.lesson_uid)
    dir_fd = os.open(
        bundle_dir,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        state = _read_state(uid)
        if state is not None:
            outcome = _append_to_sealed(dir_fd, uid, line, state)
            if outcome == APPENDED:
                return
            if outcome == OVERSIZE and _compact_projection(dir_fd, uid, line, state):
                return
        _restart_projection(dir_fd, uid, line)
    finally:
        os.close(dir_fd)


def retire_foreign_projection(lesson: dict) -> bool:
    """Reader-side half of the seal: check it when the next reader appears.

    §6.6 promises a study session that every line it reads was written by the
    app. The write path alone cannot keep that promise — it only looks at the
    file when a run finishes, so a `runs.jsonl` planted between two sessions
    would be read as app-owned history for as long as the learner does not
    run anything. Checking the seal at terminal open, where assessments
    reconcile, closes that window: a file the app did not publish is moved
    aside before the brief tells anyone to read it.

    Nothing here can refuse a workspace — a projection the app cannot verify
    must not keep a lesson from opening — so every failure means "left alone".
    Returns True when the name is now free of unverifiable content.
    """
    try:
        uid = _safe_uid(lesson.get("uid"))
        bundle_dir = lessons._lesson_dir(lesson["slug"])
        with _projection_file_lock(uid):
            dir_fd = os.open(
                bundle_dir,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                try:
                    st = os.stat(
                        PROJECTION_NAME, dir_fd=dir_fd, follow_symlinks=False
                    )
                except FileNotFoundError:
                    return True
                state = _read_state(uid)
                if state is not None and _seal_matches(st, state["file"]):
                    return True
                return _move_aside(dir_fd)
            finally:
                os.close(dir_fd)
    except (OSError, KeyError, lessons.LessonError):
        return False


def _identity_contradicts(job: runner.RunnerJob) -> bool:
    """Never write lesson A's run into a bundle whose manifest says lesson B.

    The same publication identity gate `assessments.py` applies (S-H7), for
    the same reason and with the same tolerances: admission proved the
    manifest was eligible when the run STARTED, but the agent can rewrite
    `lesson.json` while the run is in flight. A readable manifest whose
    `lesson_uid` contradicts the DB blocks; a missing, legacy, or rejected
    manifest does not — the slug directory is the DB's own mapping, and a
    rejected read carries no trusted identity to gate on.
    """
    lesson = {
        "uid": job.request.lesson_uid,
        "id": job.request.lesson_id,
        "slug": job.request.slug,
    }
    read = lessons.read_bundle_readonly(lesson)
    if read.rejected:
        return False
    uid = read.lesson_uid
    return isinstance(uid, str) and bool(uid) and uid != lesson["uid"]


def _project_finish(job: runner.RunnerJob, record: dict) -> None:
    if _identity_contradicts(job):
        raise OSError("bundle manifest identity contradicts the run's lesson")
    line = (
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    with _projection_file_lock(_safe_uid(job.request.lesson_uid)):
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
