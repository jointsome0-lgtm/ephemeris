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
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import cache
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Awaitable, Callable, Mapping
from uuid import uuid4

from app.services.runner_registry import RUNNER_REGISTRY, RunnerSpec


OUTPUT_LIMIT_BYTES = 1024 * 1024
OUTPUT_READ_BYTES = 8 * 1024
TERMINAL_RETENTION_SECONDS = 15 * 60
MAX_TERMINAL_JOBS = 8
GLOBAL_ACTIVE_LIMIT = 2
PER_LESSON_ACTIVE_LIMIT = 1

# Per-process backstops under the wall-clock timeout: a snapshot that spins,
# allocates, or writes in a loop is stopped by the kernel before the timer
# fires. RLIMIT_CPU is set per job to the runner's own wall limit.
RUNNER_ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024
RUNNER_FILE_BYTES = 32 * 1024 * 1024
RUNNER_NOFILE = 256

STARTING = "STARTING"
RUNNING = "RUNNING"
TERMINATING = "TERMINATING"
FINISHED = "FINISHED"

TERMINAL_CAUSES = frozenset({
    "exit",
    "signal",
    "timeout",
    "cancelled",
    "output-limit",
    "spawn-failed",
    "shutdown",
})

_HOME = os.path.expanduser("~")
RUNNER_ENV: Mapping[str, str] = MappingProxyType({
    "PATH": "/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin",
    "HOME": _HOME,
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "GOCACHE": f"{_HOME}/.cache/go-build",
    "GOMODCACHE": f"{_HOME}/go/pkg/mod",
    "GOFLAGS": "-mod=readonly",
})


class RunnerError(RuntimeError):
    code = "runner-error"


class RunnerUnavailableError(RunnerError):
    code = "runner-unavailable"


class RunnerShuttingDownError(RunnerError):
    code = "runner-unavailable"


class IdempotencyConflictError(RunnerError):
    code = "idempotency-conflict"


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
    env: Mapping[str, str] = RUNNER_ENV,
) -> str:
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
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


@cache
def _cached_runner_health_unlocked() -> RunnerHealth:
    """Probe every registry executable once per process lifetime."""
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
    return RunnerHealth(True)


_runner_health_lock = threading.Lock()


def _cached_runner_health() -> RunnerHealth:
    """Return one process-lifetime probe result with a single-flight cache miss."""
    with _runner_health_lock:
        return _cached_runner_health_unlocked()


def _clear_cached_runner_health() -> None:
    with _runner_health_lock:
        _cached_runner_health_unlocked.cache_clear()


_cached_runner_health.cache_clear = _clear_cached_runner_health  # type: ignore[attr-defined]


def runner_health() -> RunnerHealth:
    return _cached_runner_health()


def require_runner_health() -> None:
    result = runner_health()
    if not result.available:
        raise RunnerUnavailableError(result.detail or "runner health probe failed")


@dataclass(frozen=True)
class RunnerRequest:
    lesson_uid: str
    block_id: str
    file_rev: str
    idempotency_key: str
    runner_id: str
    filename: str
    snapshot: bytes
    bundle_dir: str
    bundle_root: str
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
    workdir: str = ""
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


@dataclass
class ReaderLease:
    job: RunnerJob
    active: bool = True


SpawnHook = Callable[[RunnerJob], Awaitable[asyncio.subprocess.Process]]
FinishHook = Callable[[RunnerJob], object]


class RunnerService:
    """Single-process owner of F3 admission and runner job state."""

    def __init__(
        self,
        *,
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
        self._spawn_hook = spawn_hook or self._spawn
        self._health_hook = health_hook
        self._finish_hook = finish_hook
        self._registry = registry
        self._retention_seconds = retention_seconds
        self._max_terminal_jobs = max_terminal_jobs
        self._global_limit = global_limit
        self._per_lesson_limit = per_lesson_limit

    @asynccontextmanager
    async def prepare_start(self, lesson_uid: str):
        """Serialize one lesson's preflight/validation/admit pipeline."""
        async with self._lock:
            lock = self._prepare_locks.get(lesson_uid)
            if lock is None:
                lock = self._prepare_locks[lesson_uid] = asyncio.Lock()
        async with lock:
            yield

    def _replay_locked(
        self,
        lesson_uid: str,
        idempotency_key: str,
        block_id: str,
        file_rev: str,
    ) -> Admission | None:
        replay = self._idempotency.get((lesson_uid, idempotency_key))
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
        lesson_uid: str,
        idempotency_key: str,
        block_id: str,
        file_rev: str,
    ) -> Admission | None:
        """Resolve replay and cheap refusals before filesystem validation."""
        async with self._lock:
            self._prune_locked()
            replay = self._replay_locked(
                lesson_uid, idempotency_key, block_id, file_rev
            )
            if replay is not None:
                return replay
            if not self._accepting:
                raise RunnerShuttingDownError("runner service is shutting down")
            if self._active_by_lesson.get(lesson_uid, 0) >= self._per_lesson_limit:
                raise LessonCapacityError(lesson_uid)
            if self._active_total >= self._global_limit:
                raise GlobalCapacityError("global runner capacity reached")
            return None

    async def admit(self, request: RunnerRequest) -> Admission:
        """Validate health off-loop, then reserve under the admission lock."""
        replay_key = (request.lesson_uid, request.idempotency_key)

        # Keep replay/error ordering ahead of health, and reject cheap request
        # defects without starting subprocess probes.  Health itself must run
        # outside both the event loop and this lock so status/SSE/cancel remain
        # responsive during a cold or unhealthy probe.
        async with self._lock:
            self._prune_locked()
            replay = self._replay_locked(
                request.lesson_uid, request.idempotency_key,
                request.block_id, request.file_rev,
            )
            if replay is not None:
                return replay
            if not self._accepting:
                raise RunnerShuttingDownError("runner service is shutting down")
            spec = self._registry[request.runner_id]

        await asyncio.to_thread(self._health_hook)

        # State may have changed while health ran.  Repeat all mutable checks;
        # a concurrent identical winner replays before this one reserves.
        async with self._lock:
            self._prune_locked()
            replay = self._replay_locked(
                request.lesson_uid, request.idempotency_key,
                request.block_id, request.file_rev,
            )
            if replay is not None:
                return replay
            if not self._accepting:
                raise RunnerShuttingDownError("runner service is shutting down")
            lesson_active = self._active_by_lesson.get(request.lesson_uid, 0)
            if lesson_active >= self._per_lesson_limit:
                raise LessonCapacityError(request.lesson_uid)
            if self._active_total >= self._global_limit:
                raise GlobalCapacityError("global runner capacity reached")

            job_id = str(uuid4())
            job = RunnerJob(job_id, request, spec)
            self._jobs[job.job_id] = job
            self._idempotency[replay_key] = (
                request.block_id, request.file_rev, job.job_id, None
            )
            self._active_by_lesson[request.lesson_uid] = lesson_active + 1
            self._active_total += 1
            job.task = asyncio.create_task(
                self._drive_job(job), name=f"lesson-runner-{job.job_id}"
            )
            return Admission(job, False)

    async def get(self, job_id: str) -> RunnerJob | None:
        async with self._lock:
            self._prune_locked()
            return self._jobs.get(job_id)

    async def attach_reader(self, job_id: str) -> ReaderLease:
        async with self._lock:
            self._prune_locked()
            job = self._jobs.get(job_id)
            if job is None:
                raise JobMissingError(job_id)
            if job.reader_count >= 2:
                raise ReaderCapacityError(job_id)
            if job.reader_count == 0 and sum(
                retained.reader_count > 0 for retained in self._jobs.values()
            ) >= self._max_terminal_jobs:
                raise ReaderCapacityError(job_id)
            job.reader_count += 1
            return ReaderLease(job)

    async def detach_reader(self, lease: ReaderLease) -> None:
        async with self._lock:
            if not lease.active:
                return
            lease.active = False
            job = lease.job
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
        """Run the snapshot from a fresh directory of its own.

        The directory is the job's cwd and holds nothing but the read-only
        snapshot; `_drive_job` removes it once the process is reaped.
        """
        request = job.request
        spec = job.spec
        basename = PurePosixPath(request.filename).name
        job.workdir = await asyncio.to_thread(
            _write_snapshot_dir, basename, request.snapshot
        )
        try:
            return await _create_leader(
                *spec.command(f"{job.workdir}/{basename}"),
                stdin=subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=job.workdir,
                env={
                    **RUNNER_ENV,
                    "TMPDIR": _scratch_dir(job.workdir),
                    "GOTMPDIR": _scratch_dir(job.workdir),
                },
                start_new_session=True,
                preexec_fn=_runner_rlimits(spec.wall_seconds),
            )
        except BaseException:
            await asyncio.to_thread(_remove_workdir, job.workdir)
            raise

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
            # Whatever the leader left behind in its process group goes with
            # it: a background child would otherwise outlive the job's wall
            # clock, and one holding the output pipes would keep the readers
            # below from ever reaching EOF.
            await asyncio.to_thread(self._kill_tree, job)
            if job.workdir:
                await asyncio.to_thread(_remove_workdir, job.workdir)

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
        lesson = job.request.lesson_uid
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
        replay_key = (job.request.lesson_uid, job.request.idempotency_key)
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
        """Kill the job's whole process group: it was started as a session leader."""
        process = job.process
        if process is None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class _LeaderProtocol(asyncio.subprocess.SubprocessStreamProtocol):
    def __init__(self, limit: int, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(limit=limit, loop=loop)
        self.exited: asyncio.Future[None] = loop.create_future()

    def process_exited(self) -> None:
        super().process_exited()
        if not self.exited.done():
            self.exited.set_result(None)


class _Leader(asyncio.subprocess.Process):
    """A job's session leader, whose ``wait()`` returns the moment it exits.

    ``asyncio.subprocess.Process.wait()`` on Python 3.12+ also waits for the
    output pipes to close, and a background child that inherited them keeps it
    from ever returning; the wall clock would then fire on a leader that exited
    normally long before.
    """

    def __init__(self, transport, protocol: _LeaderProtocol, loop) -> None:
        super().__init__(transport, protocol, loop)
        self._exited = protocol.exited

    async def wait(self) -> int:
        await asyncio.shield(self._exited)
        return self.returncode


async def _create_leader(*argv: str, **kwargs) -> _Leader:
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.subprocess_exec(
        lambda: _LeaderProtocol(limit=2**16, loop=loop), *argv, **kwargs
    )
    return _Leader(transport, protocol, loop)


def _scratch_dir(workdir: str) -> str:
    """Where the job's toolchain writes temporaries: beside cwd, never in it."""
    return f"{workdir}.tmp"


def _remove_workdir(workdir: str) -> None:
    shutil.rmtree(workdir, ignore_errors=True)
    shutil.rmtree(_scratch_dir(workdir), ignore_errors=True)


def _write_snapshot_dir(basename: str, snapshot: bytes) -> str:
    workdir = tempfile.mkdtemp(prefix="ephemeris-runner-")
    try:
        os.mkdir(_scratch_dir(workdir), 0o700)
        fd = os.open(
            os.path.join(workdir, basename),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o444,
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(snapshot)
    except BaseException:
        _remove_workdir(workdir)
        raise
    return workdir


def _runner_rlimits(wall_seconds: int) -> Callable[[], None]:
    # Imported in the parent: the hook runs between fork and exec, where a
    # first import could deadlock on the import lock.
    import resource

    def apply() -> None:
        for limit, cap in (
            (resource.RLIMIT_CPU, wall_seconds),
            (resource.RLIMIT_AS, RUNNER_ADDRESS_SPACE_BYTES),
            (resource.RLIMIT_NOFILE, RUNNER_NOFILE),
            (resource.RLIMIT_FSIZE, RUNNER_FILE_BYTES),
        ):
            _soft, hard = resource.getrlimit(limit)
            bounded = cap if hard == resource.RLIM_INFINITY else min(cap, hard)
            resource.setrlimit(limit, (bounded, bounded))

    return apply
