"""Phase-F HTTP coordinator for immutable lesson runner jobs."""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path

from .. import runner
from ..db import DATA_DIR, DB_PATH, append_event, get_conn
from . import artifacts, lessons


RATE_WINDOW_SECONDS = 60.0
RATE_MAX_PER_WINDOW = 10
MAX_BODY_BYTES = 16 * 1024
MAX_KEY_LEN = 128

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
    payload: dict[str, object] = {
        "lesson_uid": job.request.lesson_uid,
        "lesson_id": job.request.lesson_id,
        "slug": job.request.slug,
        "block_id": job.request.block_id,
        "runner_id": job.request.runner_id,
        "file_rev": job.request.file_rev,
        "cause": terminal["cause"],
        "truncated": bool(terminal["truncated"]),
        "duration_ms": terminal["duration_ms"],
    }
    if "exit_code" in terminal:
        payload["exit_code"] = terminal["exit_code"]
    conn = get_conn()
    try:
        with conn:
            append_event(conn, "lesson_run", payload)
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


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
