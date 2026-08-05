"""Finished-run projection into lesson bundles."""
from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

import pytest

from app import runner
from app.db import get_conn
from app.services import bundle_schema, runs
from app.services.runner_registry import RUNNER_REGISTRY


@pytest.fixture(scope="module", autouse=True)
def _started_app(client):
    yield


def _job(bundle_dir, output: str, *, exit_code: int = 2) -> runner.RunnerJob:
    request = runner.RunnerRequest(
        lesson_key="invented-runs-projection",
        block_id="blk_runs01",
        file_rev="sha256:" + "1" * 64,
        idempotency_key=str(uuid4()),
        runner_id="python-script-v1",
        filename="main.py",
        snapshot=b"print('invented')\n",
        bundle_dir=str(bundle_dir),
        bundle_root=str(bundle_dir.parent),
        private_root=str(bundle_dir.parent.parent),
        lesson_uid=str(uuid4()),
        lesson_id=170,
        slug=bundle_dir.name,
    )
    return runner.RunnerJob(
        job_id=str(uuid4()),
        request=request,
        spec=RUNNER_REGISTRY[request.runner_id],
        state=runner.FINISHED,
        events=[
            {"seq": 1, "event": "output", "stream": "stdout", "text": output},
            {
                "seq": 2,
                "event": "exit",
                "cause": "exit",
                "exit_code": exit_code,
                "truncated": False,
                "duration_ms": 125,
            },
        ],
    )


def _event_payloads() -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT payload_json FROM events WHERE type = 'lesson_run' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(row["payload_json"]) for row in rows]


def test_finished_run_projects_one_well_formed_line(tmp_path):
    bundle = tmp_path / "invented-bundle"
    bundle.mkdir()
    job = _job(bundle, "invented stdout\ninvented stderr\n", exit_code=7)

    assert runs._record_finish_sync(job) is True

    lines = (bundle / runs.PROJECTION_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record == {
        "kind": "run",
        "v": 1,
        "run_id": job.job_id,
        "lesson_uid": job.request.lesson_uid,
        "block_id": "blk_runs01",
        "runner_id": "python-script-v1",
        "file_rev": "sha256:" + "1" * 64,
        "cause": "exit",
        "exit_code": 7,
        "signal": None,
        "duration_ms": 125,
        "truncated": False,
        "started_at": record["started_at"],
        "finished_at": record["finished_at"],
        "output_tail": "invented stdout\ninvented stderr\n",
        "output_tail_truncated": False,
    }
    started = datetime.fromisoformat(record["started_at"])
    finished = datetime.fromisoformat(record["finished_at"])
    assert started.tzinfo is not None
    assert finished.tzinfo is not None
    assert int((finished - started).total_seconds() * 1000) == 125


def test_the_ledger_event_carries_no_output_body(tmp_path):
    """The `lesson_run` event stays body-free (docs/lesson-artifacts-api.md):
    only the bundle projection carries what the learner's code printed."""
    bundle = tmp_path / "invented-body-free"
    bundle.mkdir()
    job = _job(bundle, "invented secret output\n")

    assert runs._record_finish_sync(job) is True

    payload = next(
        item for item in _event_payloads() if item.get("run_id") == job.job_id
    )
    assert "output_tail" not in payload
    assert "output_tail_truncated" not in payload
    assert "invented secret output" not in json.dumps(payload)
    assert payload["lesson_uid"] == job.request.lesson_uid
    assert payload["exit_code"] == 2 and payload["cause"] == "exit"
    projected = json.loads((bundle / runs.PROJECTION_NAME).read_text("utf-8"))
    assert projected["output_tail"] == "invented secret output\n"


def test_further_runs_append_to_an_existing_projection(tmp_path):
    bundle = tmp_path / "invented-append"
    bundle.mkdir()
    jobs = [
        _job(bundle, f"invented run {index}\n", exit_code=index)
        for index in range(3)
    ]

    for job in jobs:
        assert runs._record_finish_sync(job) is True

    lines = (bundle / runs.PROJECTION_NAME).read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert [record["run_id"] for record in records] == [job.job_id for job in jobs]
    assert [record["output_tail"] for record in records] == [
        "invented run 0\n", "invented run 1\n", "invented run 2\n",
    ]
    assert [record["exit_code"] for record in records] == [0, 1, 2]


def test_a_projection_without_a_final_newline_is_refused_not_extended(tmp_path):
    """The bundle is agent-writable; a half-written line is never adopted, and
    refusing it must not cost the durable event."""
    bundle = tmp_path / "invented-incomplete"
    bundle.mkdir()
    poisoned = '{"kind":"run","v":1}\n{"kind":"run"'
    (bundle / runs.PROJECTION_NAME).write_text(poisoned, encoding="utf-8")
    job = _job(bundle, "invented output after poisoning\n")

    assert runs._record_finish_sync(job) is True

    assert (bundle / runs.PROJECTION_NAME).read_text(encoding="utf-8") == poisoned
    assert any(payload.get("run_id") == job.job_id for payload in _event_payloads())
    assert not list(bundle.glob(".runs-*.tmp"))


def test_output_tail_is_truncated_at_the_utf8_cap(tmp_path):
    bundle = tmp_path / "invented-large-output"
    bundle.mkdir()
    output = "prefix-" + "x" * (runs.OUTPUT_TAIL_BYTES + 37)
    job = _job(bundle, output, exit_code=0)

    assert runs._record_finish_sync(job) is True

    record = json.loads((bundle / runs.PROJECTION_NAME).read_text(encoding="utf-8"))
    assert record["output_tail"] == output[-runs.OUTPUT_TAIL_BYTES:]
    assert len(record["output_tail"].encode("utf-8")) == runs.OUTPUT_TAIL_BYTES
    assert record["output_tail_truncated"] is True


def test_output_tail_cuts_on_a_character_boundary(tmp_path):
    bundle = tmp_path / "invented-multibyte-output"
    bundle.mkdir()
    # "ы" is two UTF-8 bytes, so the cap lands mid-character without the walk.
    output = "ы" * (runs.OUTPUT_TAIL_BYTES // 2) + "x"
    job = _job(bundle, output, exit_code=0)

    assert runs._record_finish_sync(job) is True

    record = json.loads((bundle / runs.PROJECTION_NAME).read_text(encoding="utf-8"))
    tail = record["output_tail"]
    assert record["output_tail_truncated"] is True
    assert tail == "ы" * (runs.OUTPUT_TAIL_BYTES // 2 - 1) + "x"
    assert len(tail.encode("utf-8")) == runs.OUTPUT_TAIL_BYTES - 1
    assert output.endswith(tail)


def test_projection_failure_keeps_the_committed_event(tmp_path, monkeypatch):
    bundle = tmp_path / "invented-projection-failure"
    bundle.mkdir()
    job = _job(bundle, "invented durable output\n")
    observed = {}

    def fail_after_commit(projected_job, _record):
        observed["committed"] = any(
            payload.get("run_id") == projected_job.job_id
            for payload in _event_payloads()
        )
        raise OSError("invented projection failure")

    monkeypatch.setattr(runs, "_project_finish", fail_after_commit)
    assert runs._record_finish_sync(job) is True
    assert observed == {"committed": True}
    assert any(payload.get("run_id") == job.job_id for payload in _event_payloads())
    assert not (bundle / runs.PROJECTION_NAME).exists()


def test_runs_projection_name_is_reserved():
    read = bundle_schema.read_manifest_text(json.dumps({
        "schema_version": 2,
        "lesson_uid": str(uuid4()),
        "entry": "index.html",
        "pages": [
            {"id": "pg_runsroot", "path": "index.html"},
            {"id": "pg_runsfile", "path": "runs.jsonl"},
        ],
        "artifact_roots": ["attempts", "runs.jsonl"],
    }))
    assert "runs.jsonl" in bundle_schema.RESERVED_NAMES
    assert not bundle_schema.valid_v2_path("runs.jsonl")
    assert not bundle_schema.valid_v2_path("runs.jsonl/notes.html")
    assert "invalid-path" in read.codes()
    assert "runs.jsonl" not in read.page_paths()
    assert "runs.jsonl" not in read.artifact_roots
