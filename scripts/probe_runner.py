#!/usr/bin/env python3
"""On-host F3 isolation and real-execution fixture matrix.

All state is invented and lives in one TemporaryDirectory under /tmp.  The
script never resolves or opens the configured Ephemeris data directory.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.runner import (  # noqa: E402
    FINISHED,
    RUNNER_ENV,
    RunnerRequest,
    RunnerService,
    require_runner_health,
)
from app.services.runner_registry import (  # noqa: E402
    RUNNER_REGISTRY,
    SNAPSHOT_PATH,
    RunnerSpec,
)


FIXTURES = ROOT / "fixtures" / "runner"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def output(job, stream: str | None = None) -> str:
    return "".join(
        event["text"] for event in job.events
        if event["event"] == "output"
        and (stream is None or event["stream"] == stream)
    )


async def matrix() -> dict[str, object]:
    require_runner_health()
    with tempfile.TemporaryDirectory(prefix="ephemeris-f3-probe-", dir="/tmp") as raw:
        private = Path(raw)
        bundle_root = private / "lessons"
        bundle = bundle_root / "invented-current-bundle"
        other_bundle = bundle_root / "invented-other-bundle"
        bundle.mkdir(parents=True)
        other_bundle.mkdir()
        (bundle / "invented-readable.txt").write_text("demo\n", encoding="utf-8")
        (other_bundle / "invented-private.txt").write_text("hidden\n", encoding="utf-8")
        private_sentinel = private / "invented-private.sqlite"
        private_sentinel.write_text("not a database\n", encoding="utf-8")

        counter = 0

        def request(runner_id: str, name: str, snapshot: bytes) -> RunnerRequest:
            nonlocal counter
            counter += 1
            return RunnerRequest(
                lesson_key=f"invented-lesson-{counter}",
                block_id="blk_demo",
                file_rev=f"sha256:invented-{counter}",
                idempotency_key=f"invented-key-{counter}",
                runner_id=runner_id,
                filename=f"attempts/blk_demo/{name}",
                snapshot=snapshot,
                bundle_dir=str(bundle),
                bundle_root=str(bundle_root),
                private_root=str(private),
            )

        async def run(
            service: RunnerService,
            runner_id: str,
            name: str,
            snapshot: bytes,
            timeout: float = 90,
        ):
            admitted = await service.admit(request(runner_id, name, snapshot))
            job = await asyncio.wait_for(service.wait(admitted.job.job_id), timeout)
            if job is None:
                raise RuntimeError("admitted runner job vanished")
            return job

        service = RunnerService()
        result: dict[str, object] = {}

        success = await run(
            service, "python-script-v1", "success.py", fixture("python_success.py")
        )
        result["success"] = {
            "state": success.state,
            "cause": success.cause,
            "exit_code": success.exit_code,
            "stdout": output(success, "stdout"),
            "stderr": output(success, "stderr"),
        }

        syntax = await run(
            service,
            "python-script-v1",
            "syntax_error.py",
            fixture("python_syntax_error.py"),
        )
        result["syntax_error"] = {
            "cause": syntax.cause,
            "exit_code": syntax.exit_code,
            "stderr_has_syntax_error": "SyntaxError" in output(syntax, "stderr"),
        }

        timeout_registry = {
            "python-timeout-fixture": RunnerSpec(
                ("/usr/bin/python3", SNAPSHOT_PATH), (".py",), wall_seconds=1
            )
        }
        timeout_service = RunnerService(registry=timeout_registry)
        timed = await run(
            timeout_service,
            "python-timeout-fixture",
            "timeout.py",
            fixture("python_timeout.py"),
            timeout=10,
        )
        result["timeout"] = {
            "cause": timed.cause,
            "state": timed.state,
            "signal": timed.signal,
        }
        await timeout_service.shutdown()

        overflow = await run(
            service,
            "python-script-v1",
            "output_overflow.py",
            fixture("python_output_overflow.py"),
            timeout=15,
        )
        result["output_overflow"] = {
            "cause": overflow.cause,
            "state": overflow.state,
            "output_bytes": overflow.output_bytes,
            "truncated": overflow.truncated,
        }

        fsize = await run(
            service, "python-script-v1", "fsize.py", fixture("python_fsize.py")
        )
        result["file_limit"] = {
            "cause": fsize.cause,
            "exit_code": fsize.exit_code,
            "signal": fsize.signal,
            "failed": (fsize.exit_code or 0) != 0 or fsize.signal is not None,
        }

        descendant_admission = await service.admit(request(
            "python-script-v1", "descendant.py", fixture("python_descendant.py")
        ))
        descendant = descendant_admission.job
        deadline = time.monotonic() + 10
        while "descendant-ready" not in output(descendant):
            if time.monotonic() >= deadline:
                raise RuntimeError("descendant fixture never became ready")
            await asyncio.sleep(0.02)
        cancel_won = await service.cancel(descendant.job_id)
        descendant = await asyncio.wait_for(service.wait(descendant.job_id), 10)
        result["descendant_cleanup"] = {
            "cancel_won": cancel_won,
            "cause": descendant.cause if descendant else None,
            "state": descendant.state if descendant else None,
            "process_reaped": descendant.process_reaped if descendant else False,
            "both_eof": bool(
                descendant and descendant.stdout_eof and descendant.stderr_eof
            ),
        }

        go_source = fixture("go_hello.go")
        cold_started = time.monotonic()
        cold_go = await run(service, "go-run-v1", "main.go", go_source, timeout=90)
        cold_wall_ms = int((time.monotonic() - cold_started) * 1000)
        go_text = output(cold_go, "stdout")
        result["cold_go"] = {
            "cause": cold_go.cause,
            "exit_code": cold_go.exit_code,
            "wall_ms": cold_wall_ms,
            "job_ms": next(
                event["duration_ms"] for event in cold_go.events
                if event["event"] == "exit"
            ),
            "warm_child_reported": "go-warm-child-ok" in go_text,
            "output": go_text,
        }

        repeat_a = await run(service, "go-run-v1", "main.go", go_source, timeout=90)
        repeat_b = await run(service, "go-run-v1", "main.go", go_source, timeout=90)
        changed_source = go_source.replace(b"go-warm-child-ok", b"go-warm-child-changed")
        changed = await run(service, "go-run-v1", "main.go", changed_source, timeout=90)
        result["go_repeated_and_changed"] = {
            "repeat_ok": all(
                job.cause == "exit" and job.exit_code == 0
                for job in (repeat_a, repeat_b)
            ),
            "changed_source_observed": "go-warm-child-changed" in output(changed),
        }

        go_error = await run(
            service,
            "go-run-v1",
            "compile_error.go",
            fixture("go_compile_error.go"),
            timeout=90,
        )
        result["go_compile_error"] = {
            "cause": go_error.cause,
            "exit_code": go_error.exit_code,
            "stderr_has_undefined": "undefined" in output(go_error, "stderr"),
        }

        isolation_template = fixture("isolation_probe.py").decode("utf-8")
        isolation_source = (
            isolation_template
            .replace("__EPHEMERIS_REPO__", str(ROOT))
            .replace("__PRIVATE_SENTINEL__", str(private_sentinel))
            .replace("__OTHER_BUNDLE__", str(other_bundle))
            .replace("__CURRENT_BUNDLE__", str(bundle))
            .replace("__HOST_NETNS__", str(os.stat("/proc/self/ns/net").st_ino))
            .encode("utf-8")
        )
        isolation = await run(
            service, "python-script-v1", "isolation_probe.py", isolation_source
        )
        result["isolation"] = json.loads(output(isolation, "stdout"))

        shutdown_service = RunnerService()
        shutdown_admission = await shutdown_service.admit(request(
            "python-script-v1", "shutdown.py", fixture("python_shutdown.py")
        ))
        shutdown_job = shutdown_admission.job
        deadline = time.monotonic() + 10
        while "shutdown-ready" not in output(shutdown_job):
            if time.monotonic() >= deadline:
                raise RuntimeError("shutdown fixture never became ready")
            await asyncio.sleep(0.02)
        await asyncio.wait_for(shutdown_service.shutdown(), 10)
        result["shutdown"] = {
            "cause": shutdown_job.cause,
            "state": shutdown_job.state,
            "released": shutdown_job.reservation_released,
            "active_total": shutdown_service.active_total,
        }

        await service.shutdown()
        result["limits"] = {
            "python_wall_seconds": RUNNER_REGISTRY["python-script-v1"].wall_seconds,
            "go_wall_seconds": RUNNER_REGISTRY["go-run-v1"].wall_seconds,
        }
        return result


def main() -> int:
    print(json.dumps(asyncio.run(matrix()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
