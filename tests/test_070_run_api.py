"""Revision-bound run API verification."""
from __future__ import annotations

import hashlib
import json

from conftest import events_of



def test_run_api(client, suite_state):
    from app.main import app

    c = client
    _F3Process = suite_state["_F3Process"]
    _asyncio = suite_state["_asyncio"]
    _f1 = suite_state["_f1"]
    _f1_dir = suite_state["_f1_dir"]
    _f1_file = suite_state["_f1_file"]
    _f1_id = suite_state["_f1_id"]
    _f1_url = suite_state["_f1_url"]
    _runner = suite_state["_runner"]
    _mock = suite_state["_mock"]
    _time = suite_state["_time"]
    # --- F4: revision-bound run API, replay, SSE, cancel, event -------------
    from app.services import runs as _runs

    _f4_run_url = f"/learn/lessons/{_f1_id}/blocks/blk_editor01/runs"
    _f4_source = b"print('Run API Demo')\n"
    _f1_file.write_bytes(_f4_source)
    _f4_file_rev = c.get(_f1_url).json()["file_rev"]
    _f4_payload = {
        "file_rev": _f4_file_rev,
        "idempotency_key": "invented-run-action-1",
    }
    _f4_observed_jobs = []

    async def _f4_spawn(job):
        process = _F3Process()
        _f4_observed_jobs.append(job)

        async def complete():
            await _asyncio.sleep(0)
            process.stdout.feed_data(b"invented stdout\n")
            await _asyncio.sleep(0)
            process.stderr.feed_data(b"invented stderr\n")
            process.finish(0)

        _asyncio.create_task(complete())
        return process

    _f4_service = _runner.RunnerService(
        spawn_hook=_f4_spawn,
        health_hook=lambda: None,
        finish_hook=_runs._record_finish,
    )
    _f4_original_service = app.state.runner_service
    app.state.runner_service = _f4_service
    _f4_events_before = len(events_of("lesson_run"))
    try:
        _f4_guarded = c.post(
            _f4_run_url, json=_f4_payload, headers={"Origin": "null"}
        )
        _f4_started = c.post(_f4_run_url, json=_f4_payload)
        _f4_started_json = _f4_started.json()
        _f4_job_id = _f4_started_json.get("job_id", "missing")
        _f4_status = None
        for _ in range(100):
            _f4_status = c.get(f"/learn/runs/{_f4_job_id}")
            if _f4_status.json().get("state") == _runner.FINISHED:
                break
            _time.sleep(0.01)
        _f4_status_json = _f4_status.json()
        assert (
            _f4_guarded.status_code == 403
            and _f4_started.status_code == 200
            and _f4_started_json.get("replayed") is False
            and len(_f4_observed_jobs) == 1
            and _f4_observed_jobs[0].request.snapshot == _f4_source
            and _f4_observed_jobs[0].request.file_rev == _f4_file_rev
        ), "F4 start stays behind B2 and executes the one verified snapshot"
        assert (
            _f4_status.status_code == 200
            and _f4_status_json.get("state") == _runner.FINISHED
            and _f4_status_json.get("cause") == "exit"
            and _f4_status_json.get("exit_code") == 0
            and _f4_status_json.get("event_recorded") is True
        ), "F4 terminal status waits for honest best-effort event state"

        _f4_stream = c.get(f"/learn/runs/{_f4_job_id}/stream")
        _f4_ids = [
            int(line.split(":", 1)[1].strip())
            for line in _f4_stream.text.splitlines() if line.startswith("id:")
        ]
        _f4_after = _f4_ids[0]
        _f4_resumed = c.get(
            f"/learn/runs/{_f4_job_id}/stream?after={_f4_after}"
        )
        _f4_resumed_ids = [
            int(line.split(":", 1)[1].strip())
            for line in _f4_resumed.text.splitlines() if line.startswith("id:")
        ]
        assert (
            _f4_stream.status_code == 200
            and _f4_ids == sorted(set(_f4_ids))
            and _f4_resumed_ids == _f4_ids[1:]
            and all(seq > _f4_after for seq in _f4_resumed_ids)
            and _f4_stream.text.count("event: exit") == 1
            and _f4_resumed.text.count("event: exit") == 1
        ), "F4 SSE resumes strictly after cursor with one terminal exit"
        _f4_cross_stream = c.get(
            f"/learn/runs/{_f4_job_id}/stream",
            headers={"Origin": "http://evil.example"},
        )
        assert (
            _f4_cross_stream.status_code == 403
            and _f4_cross_stream.json().get("error") == "forbidden"
            and _f4_service._jobs[_f4_job_id].reader_count == 0
        ), "F4 cross-origin SSE is refused before reserving a reader slot"

        _f4_replay = c.post(_f4_run_url, json=_f4_payload)
        _f1_file.write_bytes(b"print('Run API Demo, edited')\n")
        _f4_edited_rev = c.get(_f1_url).json()["file_rev"]
        _f4_conflict = c.post(_f4_run_url, json={
            "file_rev": _f4_edited_rev,
            "idempotency_key": _f4_payload["idempotency_key"],
        })
        _f1_file.write_bytes(_f4_source)
        _f4_revision_conflict = c.post(_f4_run_url, json={
            "file_rev": "sha256:" + "1" * 64,
            "idempotency_key": "invented-stale-run",
        })
        _f4_missing_hold = _f1_file.with_name("main.py.invented-hold")
        _f1_file.rename(_f4_missing_hold)
        try:
            _f4_missing = c.post(_f4_run_url, json={
                "file_rev": _f4_file_rev,
                "idempotency_key": "invented-missing-run",
            })
        finally:
            _f4_missing_hold.rename(_f1_file)
        assert (
            _f4_replay.status_code == 200
            and _f4_replay.json().get("job_id") == _f4_job_id
            and _f4_replay.json().get("replayed") is True
            and _f4_conflict.status_code == 409
            and _f4_conflict.json().get("error") == "idempotency-conflict"
            and len(_f4_observed_jobs) == 1
        ), "F4 identical replay returns one job; changed identity conflicts"
        assert (
            _f4_revision_conflict.status_code == 409
            and _f4_revision_conflict.json().get("error") == "file-conflict"
            and _f4_revision_conflict.json().get("file_rev") == _f4_file_rev
            and _f4_missing.status_code == 409
            and _f4_missing.json().get("error") == "file-missing"
            and len(_f4_observed_jobs) == 1
        ), "F4 start requires the current saved bytes exactly once"

        _f4_run_events = events_of("lesson_run")
        _f4_event = json.loads(_f4_run_events[-1]["payload_json"])
        assert (
            len(_f4_run_events) == _f4_events_before + 1
            and _f4_event["lesson_uid"] == _f1["uid"]
            and _f4_event["block_id"] == "blk_editor01"
            and _f4_event["file_rev"] == _f4_file_rev
            and _f4_event["cause"] == "exit"
            and "output" not in _f4_event
            and "stdout" not in _f4_event
            and "stderr" not in _f4_event
            and "output_tail" not in _f4_event
        ), "F4 terminal job records one body-free lesson_run event"

        def _f4_bundle_state():
            return [
                (
                    str(path.relative_to(_f1_dir)), path.is_dir(),
                    path.stat().st_size if path.is_file() else None,
                    hashlib.sha256(path.read_bytes()).hexdigest()
                    if path.is_file() else None,
                )
                for path in sorted(_f1_dir.rglob("*"))
            ]

        _f4_pure_before = _f4_bundle_state()
        _f4_event_count_before_gets = len(events_of("lesson_run"))
        _f4_pure_status = c.get(f"/learn/runs/{_f4_job_id}")
        _f4_pure_stream = c.get(
            f"/learn/runs/{_f4_job_id}/stream",
            headers={"Last-Event-ID": str(_f4_ids[-1])},
        )
        assert (
            _f4_pure_status.status_code == 200
            and _f4_pure_stream.status_code == 200
            and _f4_bundle_state() == _f4_pure_before
            and len(events_of("lesson_run")) == _f4_event_count_before_gets
        ), "F4 status/stream GETs mutate no bundle or ledger state"

        def _f4_unhealthy():
            raise _runner.RunnerUnavailableError("invented unavailable runtime")

        app.state.runner_service = _runner.RunnerService(
            spawn_hook=_f4_spawn, health_hook=_f4_unhealthy,
        )
        _f4_unhealthy_responses = [
            c.post(_f4_run_url, json={
                "file_rev": _f4_file_rev,
                "idempotency_key": f"invented-unhealthy-run-{index}",
            })
            for index in range(2)
        ]
        assert (
            all(
                response.status_code == 409
                and response.json().get("error") == "runner-unavailable"
                for response in _f4_unhealthy_responses
            )
            and len(_f4_observed_jobs) == 1
        ), "F4 unhealthy runner refuses visibly"

        _f4_cancel_processes = []

        async def _f4_slow_spawn(_job):
            process = _F3Process()
            _f4_cancel_processes.append(process)
            return process

        _f4_cancel_service = _runner.RunnerService(
            spawn_hook=_f4_slow_spawn, health_hook=lambda: None,
            finish_hook=_runs._record_finish,
        )
        app.state.runner_service = _f4_cancel_service
        _f4_cancel_start = c.post(_f4_run_url, json={
            "file_rev": _f4_file_rev,
            "idempotency_key": "invented-cancel-run",
        })
        _f4_cancel_id = _f4_cancel_start.json().get("job_id", "missing")
        for _ in range(100):
            if c.get(f"/learn/runs/{_f4_cancel_id}").json().get("state") \
                    == _runner.RUNNING:
                break
            _time.sleep(0.01)
        _f4_cancel_guard = c.post(
            f"/learn/runs/{_f4_cancel_id}/cancel",
            headers={"Origin": "null"},
        )

        def _f4_finish_on_kill(job):
            if job.process is not None:
                job.process._result.get_loop().call_soon_threadsafe(
                    job.process.finish, -9
                )

        with _mock.patch.object(
                _runner.RunnerService, "_kill_tree",
                side_effect=_f4_finish_on_kill):
            _f4_cancel = c.post(f"/learn/runs/{_f4_cancel_id}/cancel")
            for _ in range(100):
                _f4_cancel_status = c.get(f"/learn/runs/{_f4_cancel_id}")
                if _f4_cancel_status.json().get("state") == _runner.FINISHED:
                    break
                _time.sleep(0.01)
            _f4_cancel_again = c.post(f"/learn/runs/{_f4_cancel_id}/cancel")
        assert (
            _f4_cancel_start.status_code == 200
            and _f4_cancel_guard.status_code == 403
            and _f4_cancel.status_code == 200
            and _f4_cancel_status.json().get("cause") == "cancelled"
            and _f4_cancel_again.status_code == 200
            and _f4_cancel_again.json().get("cause") == "cancelled"
            and sum(
                event["event"] == "exit"
                for event in _f4_cancel_service._jobs[_f4_cancel_id].events
            ) == 1
        ), "F4 cancel is guarded, idempotent, and still emits cancelled exit"

        with _mock.patch.object(
                _runs.artifacts, "get_run_snapshot",
                wraps=_runs.artifacts.get_run_snapshot) as snapshot_read:
            _f4_bad_grammar = c.post(_f4_run_url, json={
                "file_rev": "bad",
                "idempotency_key": "invented-bad-grammar",
            })
            _f4_bad_grammar_snapshot_reads = snapshot_read.call_count
        assert (
            _f4_bad_grammar.status_code == 400
            and _f4_bad_grammar.json().get("error") == "invalid-file-rev"
            and _f4_bad_grammar_snapshot_reads == 0
        ), "F4 grammar refusals never reach the filesystem"

        _f4_learn = c.get(f"/learn?lesson={_f1_id}").text
        assert (
            f'data-runs-url="/learn/lessons/{_f1_id}/blocks"' in _f4_learn
        ), "F4 Learn template advertises the runs route prefix"
    finally:
        app.state.runner_service = _f4_original_service

    suite_state.update({
        name: value for name, value in locals().items()
        if name not in {"client", "suite_state"}
    })
