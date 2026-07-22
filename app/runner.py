"""Bounded asynchronous job service for immutable lesson-code snapshots.

The runner is intentionally a peer of ``terminal.py`` rather than a PTY or a
route service.  It owns health, admission, process state, output collection,
termination, retention, and shutdown; later HTTP integration is a consumer of
this module, not an alternate owner of those invariants.
"""
from __future__ import annotations

import asyncio
import codecs
import inspect
import os
import signal
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Awaitable, Callable, Mapping
from uuid import uuid4

from app import sandbox
from app.services.runner_registry import RUNNER_REGISTRY, RunnerSpec


OUTPUT_LIMIT_BYTES = 1024 * 1024
OUTPUT_READ_BYTES = 8 * 1024
TERMINAL_RETENTION_SECONDS = 15 * 60
MAX_TERMINAL_JOBS = 8
GLOBAL_ACTIVE_LIMIT = 2
PER_LESSON_ACTIVE_LIMIT = 1

STARTING = "STARTING"
RUNNING = "RUNNING"
TERMINATING = "TERMINATING"
FINISHED = "FINISHED"
JOB_STATES = (STARTING, RUNNING, TERMINATING, FINISHED)

TERMINAL_CAUSES = frozenset({
    "exit",
    "signal",
    "timeout",
    "cancelled",
    "output-limit",
    "spawn-failed",
    "shutdown",
})

RUNNER_ENV: Mapping[str, str] = MappingProxyType({
    "PATH": "/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin",
    "HOME": sandbox.USER_HOME,
    # bubblewrap synthesizes PWD after --chdir; state the same safe value in
    # the allowlist so the child environment remains explicit and testable.
    "PWD": sandbox.RUNNER_WORKDIR,
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GOCACHE": f"{sandbox.USER_HOME}/.cache/go-build",
    "GOMODCACHE": sandbox.GO_MODULE_CACHE_ROOT,
    "GOFLAGS": "-mod=readonly",
})


class RunnerError(RuntimeError):
    code = "runner-error"


class RunnerUnavailableError(RunnerError):
    code = "runner-unavailable"


class RunnerShuttingDownError(RunnerError):
    code = "runner-unavailable"


class UnknownRunnerError(RunnerError):
    code = "unknown-runner"


class IncompatibleRunnerError(RunnerError):
    code = "incompatible-runner"


class SnapshotTooLargeError(RunnerError):
    code = "snapshot-too-large"


class IdempotencyConflictError(RunnerError):
    code = "idempotency-conflict"


class RateLimitedError(RunnerError):
    code = "rate-limited"


class LessonCapacityError(RunnerError):
    code = "lesson-run-active"


class GlobalCapacityError(RunnerError):
    code = "runner-capacity"


class JobMissingError(RunnerError):
    code = "job-missing"


class ReaderCapacityError(RunnerError):
    code = "busy"


@dataclass(frozen=True)
class RunnerHealth:
    available: bool
    detail: str = ""


def _probe_result(
    argv: list[str],
    *,
    pass_fds: tuple[int, ...] = (),
    env: Mapping[str, str] = RUNNER_ENV,
) -> str:
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            pass_fds=pass_fds,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if result.returncode == 0:
        return ""
    detail = " ".join((result.stderr or result.stdout or "").split())
    return detail[:500] or f"exit {result.returncode}"


def _probe_ro_bind_data() -> str:
    fd = sandbox._snapshot_memfd(b"probe\n")
    target = "/tmp/ephemeris-runner-ro-bind-data-probe"
    try:
        return _probe_result([
            sandbox.BWRAP,
            "--unshare-user",
            "--die-with-parent",
            "--ro-bind", "/", "/",
            "--tmpfs", "/tmp",
            "--perms", "0444",
            "--ro-bind-data", str(fd), target,
            "--",
            "/usr/bin/python3", "-c",
            "import os,stat,sys; sys.exit(stat.S_IMODE(os.stat(sys.argv[1]).st_mode) != 0o444)",
            target,
        ], pass_fds=(fd,))
    finally:
        os.close(fd)


def _probe_go_module_cache() -> str:
    try:
        fd = sandbox.open_runner_module_cache_fd()
    except OSError as exc:
        return f"required no-follow Go module cache is unavailable: {exc}"
    try:
        return _probe_result([
            sandbox.BWRAP,
            "--unshare-user",
            "--die-with-parent",
            "--ro-bind", "/", "/",
            "--tmpfs", sandbox.USER_HOME,
            "--dir", f"{sandbox.USER_HOME}/go",
            "--dir", f"{sandbox.USER_HOME}/go/pkg",
            "--ro-bind-fd", str(fd), sandbox.GO_MODULE_CACHE_ROOT,
            "--",
            "/usr/bin/test", "-d", sandbox.GO_MODULE_CACHE_ROOT,
        ], pass_fds=(fd,))
    finally:
        os.close(fd)


@cache
def _cached_runner_health() -> RunnerHealth:
    """Probe the complete F3 execution contract once per process lifetime."""
    try:
        sandbox.require_sandbox_runtime()
    except sandbox.SandboxError as exc:
        return RunnerHealth(False, str(exc))

    detail = _probe_ro_bind_data()
    if detail:
        return RunnerHealth(False, f"--ro-bind-data probe failed: {detail}")

    try:
        sandbox.require_runner_scope_runtime()
    except sandbox.SandboxError as exc:
        return RunnerHealth(False, str(exc))

    checked: set[str] = set()
    for runner_id, spec in RUNNER_REGISTRY.items():
        executable = spec.argv[0]
        if executable in checked:
            continue
        checked.add(executable)
        version_argv = (
            [executable, "version"]
            if PurePosixPath(executable).name == "go"
            else [executable, "--version"]
        )
        detail = _probe_result(version_argv)
        if detail:
            return RunnerHealth(False, f"{runner_id} executable probe failed: {detail}")
    detail = _probe_go_module_cache()
    if detail:
        return RunnerHealth(False, detail)
    return RunnerHealth(True)


def runner_health() -> RunnerHealth:
    return _cached_runner_health()


def require_runner_health() -> None:
    result = runner_health()
    if not result.available:
        raise RunnerUnavailableError(result.detail or "runner health probe failed")


@dataclass(frozen=True)
class RunnerRequest:
    lesson_key: str
    block_id: str
    file_rev: str
    idempotency_key: str
    runner_id: str
    filename: str
    snapshot: bytes
    bundle_dir: str
    bundle_root: str
    private_root: str
    private_masks: tuple[str, ...] = ()
    lesson_uid: str = ""
    lesson_id: int = 0
    slug: str = ""


@dataclass
class RunnerJob:
    job_id: str
    request: RunnerRequest
    spec: RunnerSpec
    state: str = STARTING
    cause: str | None = None
    exit_code: int | None = None
    signal: int | None = None
    truncated: bool = False
    output_bytes: int = 0
    events: list[dict] = field(default_factory=list)
    created_monotonic: float = field(default_factory=time.monotonic)
    finished_monotonic: float | None = None
    process_reaped: bool = False
    stdout_eof: bool = False
    stderr_eof: bool = False
    reservation_released: bool = False
    event_recorded: bool = False
    reader_count: int = 0
    scope_unit: str = ""
    process: asyncio.subprocess.Process | None = field(default=None, repr=False)
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    finished: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    event_attempted: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _waiters: set[asyncio.Future[None]] = field(default_factory=set, repr=False)
    _next_seq: int = field(default=1, repr=False)

    @property
    def active(self) -> bool:
        return self.state in (STARTING, RUNNING)


@dataclass(frozen=True)
class Admission:
    job: RunnerJob
    replayed: bool


@dataclass(frozen=True)
class AdmissionPermit:
    rate_charge: object | None


SpawnHook = Callable[[RunnerJob], Awaitable[asyncio.subprocess.Process]]
RateHook = Callable[[str], object]
RateRefundHook = Callable[[str, object], None]
FinishHook = Callable[[RunnerJob], object]
_NO_RATE_PERMIT = object()


class RunnerService:
    """Single-process owner of F3 admission and runner job state."""

    def __init__(
        self,
        *,
        rate_hook: RateHook | None = None,
        rate_refund_hook: RateRefundHook | None = None,
        spawn_hook: SpawnHook | None = None,
        health_hook: Callable[[], None] = require_runner_health,
        finish_hook: FinishHook | None = None,
        registry: Mapping[str, RunnerSpec] = RUNNER_REGISTRY,
        retention_seconds: float = TERMINAL_RETENTION_SECONDS,
        max_terminal_jobs: int = MAX_TERMINAL_JOBS,
        global_limit: int = GLOBAL_ACTIVE_LIMIT,
        per_lesson_limit: int = PER_LESSON_ACTIVE_LIMIT,
    ) -> None:
        self._lock = asyncio.Lock()
        self._output_lock = asyncio.Lock()
        self._jobs: dict[str, RunnerJob] = {}
        self._idempotency: dict[
            tuple[str, str], tuple[str, str, str, float | None]
        ] = {}
        self._finish_tasks: set[asyncio.Task[None]] = set()
        self._prepare_locks: dict[str, asyncio.Lock] = {}
        self._active_by_lesson: dict[str, int] = {}
        self._active_total = 0
        self._accepting = True
        self._rate_hook = rate_hook
        self._rate_refund_hook = rate_refund_hook
        self._spawn_hook = spawn_hook or self._spawn
        self._health_hook = health_hook
        self._finish_hook = finish_hook
        self._registry = registry
        self._retention_seconds = retention_seconds
        self._max_terminal_jobs = max_terminal_jobs
        self._global_limit = global_limit
        self._per_lesson_limit = per_lesson_limit

    @property
    def active_total(self) -> int:
        return self._active_total

    @asynccontextmanager
    async def prepare_start(self, lesson_key: str):
        """Serialize one lesson's preflight/validation/admit pipeline."""
        async with self._lock:
            lock = self._prepare_locks.get(lesson_key)
            if lock is None:
                lock = self._prepare_locks[lesson_key] = asyncio.Lock()
        async with lock:
            yield

    def _replay_locked(
        self,
        lesson_key: str,
        idempotency_key: str,
        block_id: str,
        file_rev: str,
    ) -> Admission | None:
        replay = self._idempotency.get((lesson_key, idempotency_key))
        if replay is None:
            return None
        saved_block, saved_rev, job_id, _expires = replay
        if (saved_block, saved_rev) != (block_id, file_rev):
            raise IdempotencyConflictError(idempotency_key)
        job = self._jobs.get(job_id)
        if job is None:
            raise JobMissingError(job_id)
        return Admission(job, True)

    async def preflight(
        self,
        lesson_key: str,
        idempotency_key: str,
        block_id: str,
        file_rev: str,
    ) -> Admission | AdmissionPermit:
        """Resolve cheap state and reserve rate before filesystem validation."""
        async with self._lock:
            self._prune_locked()
            replay = self._replay_locked(
                lesson_key, idempotency_key, block_id, file_rev
            )
            if replay is not None:
                return replay
            if not self._accepting:
                raise RunnerShuttingDownError("runner service is shutting down")
            if self._active_by_lesson.get(lesson_key, 0) >= self._per_lesson_limit:
                raise LessonCapacityError(lesson_key)
            if self._active_total >= self._global_limit:
                raise GlobalCapacityError("global runner capacity reached")
            rate_charge: object | None = None
            if self._rate_hook is not None:
                rate_charge = self._rate_hook(lesson_key)
                if rate_charge is False:
                    raise RateLimitedError(lesson_key)
            return AdmissionPermit(rate_charge)

    async def admit(
        self,
        request: RunnerRequest,
        *,
        rate_permit: object = _NO_RATE_PERMIT,
    ) -> Admission:
        """Validate health off-loop, then reserve under the admission lock."""
        replay_key = (request.lesson_key, request.idempotency_key)
        permit_supplied = rate_permit is not _NO_RATE_PERMIT
        supplied_charge = rate_permit if permit_supplied else None

        # Keep replay/error ordering ahead of health, and reject cheap request
        # defects without starting subprocess probes.  Health itself must run
        # outside both the event loop and this lock so status/SSE/cancel remain
        # responsive during a cold or unhealthy probe.
        async with self._lock:
            self._prune_locked()
            try:
                replay = self._replay_locked(
                    request.lesson_key, request.idempotency_key,
                    request.block_id, request.file_rev,
                )
            except (IdempotencyConflictError, JobMissingError):
                if permit_supplied:
                    self._refund_rate_locked(
                        request.lesson_key, supplied_charge
                    )
                raise
            if replay is not None:
                if permit_supplied:
                    self._refund_rate_locked(
                        request.lesson_key, supplied_charge
                    )
                return replay
            if not self._accepting:
                if permit_supplied:
                    self._refund_rate_locked(
                        request.lesson_key, supplied_charge
                    )
                raise RunnerShuttingDownError("runner service is shutting down")
            try:
                if not request.private_root:
                    raise RunnerUnavailableError(
                        "runner private instance root is required"
                    )
                if len(request.snapshot) > sandbox.RUNNER_FILE_BYTES:
                    raise SnapshotTooLargeError(
                        f"runner snapshot exceeds {sandbox.RUNNER_FILE_BYTES} bytes"
                    )
                spec = self._registry.get(request.runner_id)
                if spec is None:
                    raise UnknownRunnerError(request.runner_id)
                basename = PurePosixPath(request.filename).name
                if basename in ("", ".", "..") or not spec.accepts(basename):
                    raise IncompatibleRunnerError(request.filename)
            except RunnerError:
                if permit_supplied:
                    self._refund_rate_locked(
                        request.lesson_key, supplied_charge
                    )
                raise

        try:
            await asyncio.to_thread(self._health_hook)
        except BaseException:
            if permit_supplied:
                async with self._lock:
                    self._refund_rate_locked(
                        request.lesson_key, supplied_charge
                    )
            raise

        # State may have changed while health ran.  Repeat all mutable checks;
        # a concurrent identical winner replays and any supplied rate permit is
        # refunded before returning or refusing.
        async with self._lock:
            self._prune_locked()
            try:
                replay = self._replay_locked(
                    request.lesson_key, request.idempotency_key,
                    request.block_id, request.file_rev,
                )
            except (IdempotencyConflictError, JobMissingError):
                if permit_supplied:
                    self._refund_rate_locked(
                        request.lesson_key, supplied_charge
                    )
                raise
            if replay is not None:
                if permit_supplied:
                    self._refund_rate_locked(
                        request.lesson_key, supplied_charge
                    )
                return replay
            if not self._accepting:
                if permit_supplied:
                    self._refund_rate_locked(
                        request.lesson_key, supplied_charge
                    )
                raise RunnerShuttingDownError("runner service is shutting down")
            rate_charge: object | None = supplied_charge
            if not permit_supplied and self._rate_hook is not None:
                rate_charge = self._rate_hook(request.lesson_key)
                if rate_charge is False:
                    raise RateLimitedError(request.lesson_key)
            lesson_active = self._active_by_lesson.get(request.lesson_key, 0)
            if lesson_active >= self._per_lesson_limit:
                self._refund_rate_locked(request.lesson_key, rate_charge)
                raise LessonCapacityError(request.lesson_key)
            if self._active_total >= self._global_limit:
                self._refund_rate_locked(request.lesson_key, rate_charge)
                raise GlobalCapacityError("global runner capacity reached")

            job_id = str(uuid4())
            job = RunnerJob(
                job_id, request, spec,
                scope_unit=f"ephemeris-runner-{job_id}",
            )
            self._jobs[job.job_id] = job
            self._idempotency[replay_key] = (
                request.block_id, request.file_rev, job.job_id, None
            )
            self._active_by_lesson[request.lesson_key] = lesson_active + 1
            self._active_total += 1
            job.task = asyncio.create_task(
                self._drive_job(job), name=f"lesson-runner-{job.job_id}"
            )
            return Admission(job, False)

    async def charge_validation_refusal(self, lesson_key: str) -> None:
        """Charge one expensive start validation that did not reach admit()."""
        async with self._lock:
            if self._rate_hook is None:
                return
            charge = self._rate_hook(lesson_key)
            if charge is False:
                raise RateLimitedError(lesson_key)

    def _refund_rate_locked(self, lesson_key: str, charge: object | None) -> None:
        if charge is not None and self._rate_refund_hook is not None:
            self._rate_refund_hook(lesson_key, charge)

    async def get(self, job_id: str) -> RunnerJob | None:
        async with self._lock:
            self._prune_locked()
            return self._jobs.get(job_id)

    async def attach_reader(self, job_id: str) -> RunnerJob:
        async with self._lock:
            self._prune_locked()
            job = self._jobs.get(job_id)
            if job is None:
                raise JobMissingError(job_id)
            if job.reader_count >= 2:
                raise ReaderCapacityError(job_id)
            job.reader_count += 1
            return job

    async def detach_reader(self, job: RunnerJob) -> None:
        async with self._lock:
            if job.reader_count > 0:
                job.reader_count -= 1
            self._prune_locked()

    async def events_after(
        self, job_id: str, after: int
    ) -> tuple[RunnerJob, tuple[dict, ...], str]:
        async with self._lock:
            self._prune_locked()
            job = self._jobs.get(job_id)
            if job is None:
                raise JobMissingError(job_id)
            events = tuple(
                event.copy() for event in job.events
                if int(event["seq"]) > after
            )
            return job, events, job.state

    async def wait_for_update(self, job_id: str, after: int) -> None:
        """Wait without a shared-clear race between concurrent SSE readers."""
        async with self._lock:
            self._prune_locked()
            job = self._jobs.get(job_id)
            if job is None:
                raise JobMissingError(job_id)
            if job.state == FINISHED or any(
                int(event["seq"]) > after for event in job.events
            ):
                return
            waiter = asyncio.get_running_loop().create_future()
            job._waiters.add(waiter)
        try:
            await waiter
        finally:
            async with self._lock:
                job._waiters.discard(waiter)

    async def cancel(self, job_id: str) -> bool:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state == FINISHED:
                return False
            process = job.process
            if (
                job.process_reaped
                or process is not None and process.returncode is not None
            ):
                return False
            won = self._begin_termination_locked(job, "cancelled")
        if won and process is not None:
            await asyncio.to_thread(self._kill_tree, job)
        return won

    async def wait(self, job_id: str) -> RunnerJob | None:
        job = await self.get(job_id)
        if job is None:
            return None
        await job.finished.wait()
        return job

    async def shutdown(self) -> None:
        """Stop admission and converge every active job on the shared kill path."""
        async with self._lock:
            self._accepting = False
            jobs = [job for job in self._jobs.values() if job.state != FINISHED]
            for job in jobs:
                self._begin_termination_locked(job, "shutdown")
            tasks = [job.task for job in jobs if job.task is not None]
        kill_jobs = [job for job in jobs if job.process is not None]
        if kill_jobs:
            await asyncio.gather(*(
                asyncio.to_thread(self._kill_tree, job) for job in kill_jobs
            ))
        for job in kill_jobs:
            process = job.process
            if process is None:
                continue
            for reader in (process.stdout, process.stderr):
                transport = getattr(reader, "_transport", None)
                if transport is not None:
                    transport.close()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        finish_tasks = tuple(self._finish_tasks)
        if finish_tasks:
            await asyncio.gather(*finish_tasks, return_exceptions=True)

    async def _spawn(
        self, job: RunnerJob
    ) -> asyncio.subprocess.Process:
        request = job.request
        spec = job.spec
        basename = PurePosixPath(request.filename).name
        snapshot_path = f"{sandbox.RUNNER_WORKDIR}/{basename}"
        command = spec.command(snapshot_path)
        return await sandbox.spawn_sandboxed(
            "lesson-runner",
            request.bundle_dir,
            command,
            bundle_root=request.bundle_root,
            private_root=request.private_root,
            private_masks=request.private_masks,
            stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=RUNNER_ENV,
            snapshot=request.snapshot,
            snapshot_name=basename,
            runner_wall_seconds=spec.wall_seconds,
            runner_scope_unit=job.scope_unit,
        )

    async def _drive_job(self, job: RunnerJob) -> None:
        async with self._lock:
            if job.state != STARTING:
                job.process_reaped = True
                job.stdout_eof = True
                job.stderr_eof = True
                self._finish_locked(job)
                return
        try:
            process = await self._spawn_hook(job)
        except Exception:
            async with self._lock:
                self._begin_termination_locked(job, "spawn-failed")
                job.process_reaped = True
                job.stdout_eof = True
                job.stderr_eof = True
                self._finish_locked(job)
            return

        async with self._lock:
            job.process = process
            if job.state == STARTING:
                job.state = RUNNING
                kill_now = False
            else:
                kill_now = True
        if kill_now:
            await asyncio.to_thread(self._kill_tree, job)

        readers = [
            asyncio.create_task(self._read_stream(job, "stdout", process.stdout)),
            asyncio.create_task(self._read_stream(job, "stderr", process.stderr)),
        ]
        wait_task = asyncio.create_task(process.wait())
        try:
            try:
                returncode = await asyncio.wait_for(
                    asyncio.shield(wait_task), timeout=job.spec.wall_seconds
                )
            except asyncio.TimeoutError:
                async with self._lock:
                    won = self._begin_termination_locked(job, "timeout")
                if won:
                    await asyncio.to_thread(self._kill_tree, job)
                returncode = await wait_task
        except asyncio.CancelledError:
            async with self._lock:
                self._begin_termination_locked(job, "shutdown")
            await asyncio.to_thread(self._kill_tree, job)
            returncode = await asyncio.shield(wait_task)
        finally:
            async with self._lock:
                job.process_reaped = True

        async with self._lock:
            if returncode < 0:
                job.signal = -returncode
                self._begin_termination_locked(job, "signal")
            else:
                job.exit_code = returncode
                self._begin_termination_locked(job, "exit")
            self._finish_locked(job)
        await asyncio.gather(*readers, return_exceptions=True)
        async with self._lock:
            self._finish_locked(job)

    async def _read_stream(
        self,
        job: RunnerJob,
        stream_name: str,
        reader: asyncio.StreamReader | None,
    ) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        eof_attr = "stdout_eof" if stream_name == "stdout" else "stderr_eof"
        if reader is None:
            async with self._lock:
                setattr(job, eof_attr, True)
                self._finish_locked(job)
            return
        try:
            while True:
                chunk = await reader.read(OUTPUT_READ_BYTES)
                if not chunk:
                    break
                overflow = False
                async with self._output_lock:
                    remaining = OUTPUT_LIMIT_BYTES - job.output_bytes
                    accepted = chunk[:max(0, remaining)]
                    if accepted:
                        job.output_bytes += len(accepted)
                        text = decoder.decode(accepted, final=False)
                        if text:
                            self._append_output_event(job, stream_name, text)
                    if len(chunk) > len(accepted):
                        job.truncated = True
                        overflow = True
                if overflow:
                    async with self._lock:
                        won = self._begin_termination_locked(job, "output-limit")
                        process = job.process
                    if won and process is not None:
                        await asyncio.to_thread(self._kill_tree, job)
                    # Continue draining, but the combined cap admits no bytes.
            tail = decoder.decode(b"", final=True)
            if tail:
                async with self._output_lock:
                    self._append_output_event(job, stream_name, tail)
        except (OSError, ValueError):
            async with self._lock:
                won = self._begin_termination_locked(job, "spawn-failed")
                process = job.process
            if won and process is not None:
                await asyncio.to_thread(self._kill_tree, job)
        finally:
            async with self._lock:
                setattr(job, eof_attr, True)
                self._finish_locked(job)

    def _append_output_event(self, job: RunnerJob, stream_name: str, text: str) -> None:
        job.events.append({
            "seq": job._next_seq,
            "event": "output",
            "stream": stream_name,
            "text": text,
        })
        job._next_seq += 1
        self._notify_waiters(job)

    def _begin_termination_locked(self, job: RunnerJob, cause: str) -> bool:
        if cause not in TERMINAL_CAUSES:
            raise ValueError(f"unknown terminal cause: {cause}")
        if job.cause is not None:
            return False
        job.cause = cause
        job.state = TERMINATING
        self._release_locked(job)
        return True

    def _release_locked(self, job: RunnerJob) -> None:
        if job.reservation_released:
            return
        job.reservation_released = True
        self._active_total -= 1
        lesson = job.request.lesson_key
        remaining = self._active_by_lesson.get(lesson, 0) - 1
        if remaining > 0:
            self._active_by_lesson[lesson] = remaining
        else:
            self._active_by_lesson.pop(lesson, None)

    def _finish_locked(self, job: RunnerJob) -> None:
        if (
            job.state != TERMINATING
            or not job.process_reaped
            or not job.stdout_eof
            or not job.stderr_eof
        ):
            return
        job.state = FINISHED
        job.finished_monotonic = time.monotonic()
        event = {
            "seq": job._next_seq,
            "event": "exit",
            "cause": job.cause,
            "truncated": job.truncated,
            "duration_ms": max(
                0, int((job.finished_monotonic - job.created_monotonic) * 1000)
            ),
        }
        if job.exit_code is not None:
            event["exit_code"] = job.exit_code
        if job.signal is not None:
            event["signal"] = job.signal
        job.events.append(event)
        job._next_seq += 1
        self._notify_waiters(job)
        job.finished.set()
        replay_key = (job.request.lesson_key, job.request.idempotency_key)
        replay = self._idempotency.get(replay_key)
        if replay is not None and replay[2] == job.job_id:
            self._idempotency[replay_key] = (
                replay[0], replay[1], replay[2],
                job.finished_monotonic + self._retention_seconds,
            )
        self._prune_locked()
        if self._finish_hook is not None:
            task = asyncio.create_task(self._notify_finish(job))
            self._finish_tasks.add(task)
            task.add_done_callback(self._finish_tasks.discard)
        else:
            job.event_attempted.set()

    async def _notify_finish(self, job: RunnerJob) -> None:
        try:
            result = self._finish_hook(job) if self._finish_hook is not None else None
            if inspect.isawaitable(result):
                result = await result
            job.event_recorded = bool(result)
        except Exception:
            job.event_recorded = False
        finally:
            job.event_attempted.set()
            self._notify_waiters(job)

    @staticmethod
    def _notify_waiters(job: RunnerJob) -> None:
        waiters = tuple(job._waiters)
        job._waiters.clear()
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(None)

    def _prune_locked(self) -> None:
        now = time.monotonic()
        terminal = [
            job for job in self._jobs.values()
            if job.state == FINISHED and job.finished_monotonic is not None
        ]
        terminal.sort(key=lambda job: job.finished_monotonic or 0)
        protected = [job for job in terminal if job.reader_count > 0]
        eligible = [job for job in terminal if job.reader_count == 0]
        expired = {
            job.job_id for job in eligible
            if now - (job.finished_monotonic or now) >= self._retention_seconds
        }
        retained_slots = max(0, self._max_terminal_jobs - len(protected))
        unexpired = [job for job in eligible if job.job_id not in expired]
        excess = max(0, len(unexpired) - retained_slots)
        expired.update(job.job_id for job in unexpired[:excess])
        for job_id in expired:
            self._jobs.pop(job_id, None)
        for key, replay in tuple(self._idempotency.items()):
            expires = replay[3]
            if (
                expires is not None
                and now >= expires
                and replay[2] not in self._jobs
            ):
                self._idempotency.pop(key, None)

    @staticmethod
    def _kill_tree(job: RunnerJob) -> None:
        scope_env = {"PATH": RUNNER_ENV["PATH"]}
        for name in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
            value = os.environ.get(name)
            if value:
                scope_env[name] = value
        try:
            subprocess.run(
                [
                    sandbox.SYSTEMCTL,
                    "--user",
                    "kill",
                    "--kill-whom=all",
                    "--signal=SIGKILL",
                    f"{job.scope_unit}.scope",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=scope_env,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        process = job.process
        if process is None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
