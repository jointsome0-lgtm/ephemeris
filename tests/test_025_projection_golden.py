"""Golden bytes and writer behaviours of the shared projection module.

The rows below are invented. Each expected text was rendered by its service
before that service moved onto the shared module (#225 for attempts, #226 for
assessments, #227 for runs); a rebuild from the same rows, or the same
sequence of finishes, must still produce exactly these bytes.
"""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

LESSON_UID = "0f3a6c1e-5b2d-4e7a-9c8b-1d2e3f4a5b6c"
LESSON_SLUG = "vera-golden-projection"

ROWS = [
    ("11111111-1111-4111-8111-111111111111", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
     "vera-key-1", "pg_vera0001", "q_vera0001", "sha256:" + "1" * 64,
     "Vera Example answers: café naïve — ✓ \U0001F4D8", 0, "attempt",
     "2031-03-04T10:00:00.000001+00:00"),
    ("22222222-2222-4222-8222-222222222222", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2",
     "vera-key-2", "pg_vera0001", "q_vera0002", "sha256:" + "2" * 64,
     "line one\nline two\ttabbed \"quoted\" back\\slash /slash", 1, "attempt",
     "2031-03-04T10:00:00.000002+00:00"),
    ("44444444-4444-4444-8444-444444444444", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4",
     "vera-key-4", "pg_vera0002", "q_vera0003", "sha256:" + "4" * 64,
     "Why does the tie order fall back to attempt_id?", 0, "question",
     "2031-03-04T10:00:00.000003+00:00"),
    ("33333333-3333-4333-8333-333333333333", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3",
     "vera-key-3", "pg_vera0002", "q_vera0003", "sha256:" + "3" * 64,
     "", 0, "attempt",
     "2031-03-04T10:00:00.000003+00:00"),
    ("55555555-5555-4555-8555-555555555555", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5",
     "vera-key-5", "pg_vera0001", "q_vera0001", "sha256:" + "5" * 64,
     "backdated row inserted last", 1, "attempt",
     "2031-03-04T09:59:59.999999+00:00"),
]

GOLDEN = (
    '{"kind": "attempt", "v": 1, "attempt_id": "55555555-5555-4555-8555-555555555555", '
    '"event_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5", '
    '"lesson_uid": "0f3a6c1e-5b2d-4e7a-9c8b-1d2e3f4a5b6c", "page_id": "pg_vera0001", '
    '"question_id": "q_vera0001", '
    '"page_rev": "sha256:5555555555555555555555555555555555555555555555555555555555555555", '
    '"answer": "backdated row inserted last", '
    '"created_at": "2031-03-04T09:59:59.999999+00:00", "stale": true}\n'
    '{"kind": "attempt", "v": 1, "attempt_id": "11111111-1111-4111-8111-111111111111", '
    '"event_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1", '
    '"lesson_uid": "0f3a6c1e-5b2d-4e7a-9c8b-1d2e3f4a5b6c", "page_id": "pg_vera0001", '
    '"question_id": "q_vera0001", '
    '"page_rev": "sha256:1111111111111111111111111111111111111111111111111111111111111111", '
    '"answer": "Vera Example answers: café naïve — ✓ 📘", '
    '"created_at": "2031-03-04T10:00:00.000001+00:00", "stale": false}\n'
    '{"kind": "attempt", "v": 1, "attempt_id": "22222222-2222-4222-8222-222222222222", '
    '"event_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2", '
    '"lesson_uid": "0f3a6c1e-5b2d-4e7a-9c8b-1d2e3f4a5b6c", "page_id": "pg_vera0001", '
    '"question_id": "q_vera0002", '
    '"page_rev": "sha256:2222222222222222222222222222222222222222222222222222222222222222", '
    '"answer": "line one\\nline two\\ttabbed \\"quoted\\" back\\\\slash /slash", '
    '"created_at": "2031-03-04T10:00:00.000002+00:00", "stale": true}\n'
    '{"kind": "attempt", "v": 1, "attempt_id": "33333333-3333-4333-8333-333333333333", '
    '"event_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3", '
    '"lesson_uid": "0f3a6c1e-5b2d-4e7a-9c8b-1d2e3f4a5b6c", "page_id": "pg_vera0002", '
    '"question_id": "q_vera0003", '
    '"page_rev": "sha256:3333333333333333333333333333333333333333333333333333333333333333", '
    '"answer": "", "created_at": "2031-03-04T10:00:00.000003+00:00", "stale": false}\n'
    '{"kind": "question", "v": 1, "attempt_id": "44444444-4444-4444-8444-444444444444", '
    '"event_uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa4", '
    '"lesson_uid": "0f3a6c1e-5b2d-4e7a-9c8b-1d2e3f4a5b6c", "page_id": "pg_vera0002", '
    '"question_id": "q_vera0003", '
    '"page_rev": "sha256:4444444444444444444444444444444444444444444444444444444444444444", '
    '"answer": "Why does the tie order fall back to attempt_id?", '
    '"created_at": "2031-03-04T10:00:00.000003+00:00", "stale": false}\n'
).encode("utf-8")
GOLDEN_SHA256 = "cbd6bddb8411586a0f7d9b3d270455afea6306db06a125d30c8ea7df479c7791"

ASSESSMENT_LESSON_UID = "7b2e9d4c-3a1f-4c6e-8d5b-2f0a1c9e7b3d"
ASSESSMENT_LESSON_SLUG = "vera-golden-assessments"
ASSESSMENT_GENERATED_AT = "2031-03-04T10:00:00.000000+00:00"

ASSESSMENT_ROWS = [
    ("a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1", "e1e1e1e1-e1e1-4e1e-8e1e-e1e1e1e1e1e1",
     "sit-vera-1", "tutoring", "vera-as-1", "sha256:" + "1" * 64, "evidence",
     "developing", "attempts", None, None, '["closures", "captures"]',
     "Vera Example: closures explained.", None, None,
     "2031-03-04T10:00:00.000001+00:00"),
    ("a2a2a2a2-a2a2-4a2a-8a2a-a2a2a2a2a2a2", "e2e2e2e2-e2e2-4e2e-8e2e-e2e2e2e2e2e2",
     "sit-vera-1", "tutoring", "vera-as-2", "sha256:" + "2" * 64, "review",
     "partial", None, "11111111-1111-4111-8111-111111111111", "q_vera0001", None,
     "Vera Example: half the rule.", None, None,
     "2031-03-04T10:00:00.000002+00:00"),
    ("a3a3a3a3-a3a3-4a3a-8a3a-a3a3a3a3a3a3", "e3e3e3e3-e3e3-4e3e-8e3e-e3e3e3e3e3e3",
     "sit-vera-1", "tutoring", "vera-as-3", "sha256:" + "3" * 64, "summary",
     None, None, None, None, None,
     "Vera Example: first synthesis.", "Reopen the captures page.", None,
     "2031-03-04T10:00:00.000003+00:00"),
    ("a4a4a4a4-a4a4-4a4a-8a4a-a4a4a4a4a4a4", "e4e4e4e4-e4e4-4e4e-8e4e-e4e4e4e4e4e4",
     "sit-vera-1", "tutoring", "vera-as-4", "sha256:" + "4" * 64, "evidence",
     "passed", "mixed", None, None, '["closures"]',
     "Vera Example: closures now hold.", None,
     "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1",
     "2031-03-04T10:00:00.000004+00:00"),
    ("a5a5a5a5-a5a5-4a5a-8a5a-a5a5a5a5a5a5", "e5e5e5e5-e5e5-4e5e-8e5e-e5e5e5e5e5e5",
     "sit-vera-1", "tutoring", "vera-as-5", "sha256:" + "5" * 64, "review",
     "correct", None, "11111111-1111-4111-8111-111111111111", "q_vera0001", None,
     "Vera Example: on a second reading, correct.", None,
     "a2a2a2a2-a2a2-4a2a-8a2a-a2a2a2a2a2a2",
     "2031-03-04T10:00:00.000005+00:00"),
    ("a6a6a6a6-a6a6-4a6a-8a6a-a6a6a6a6a6a6", "e6e6e6e6-e6e6-4e6e-8e6e-e6e6e6e6e6e6",
     "sit-vera-1", "tutoring", "vera-as-6", "sha256:" + "6" * 64, "summary",
     None, None, None, None, None,
     "Vera Example: corrected synthesis.",
     "Predict the growth step on the slices page.",
     "a3a3a3a3-a3a3-4a3a-8a3a-a3a3a3a3a3a3",
     "2031-03-04T10:00:00.000006+00:00"),
    ("a7a7a7a7-a7a7-4a7a-8a7a-a7a7a7a7a7a7", "e7e7e7e7-e7e7-4e7e-8e7e-e7e7e7e7e7e7",
     None, "tutoring", "vera-as-7", "sha256:" + "7" * 64, "retraction",
     None, None, None, None, None,
     "Vera Example: the second reading was of the wrong attempt.", None,
     "a5a5a5a5-a5a5-4a5a-8a5a-a5a5a5a5a5a5",
     "2031-03-04T10:00:00.000007+00:00"),
    ("a8a8a8a8-a8a8-4a8a-8a8a-a8a8a8a8a8a8", "e8e8e8e8-e8e8-4e8e-8e8e-e8e8e8e8e8e8",
     "sit-vera-2", "exam", "vera-as-8", "sha256:" + "8" * 64, "review",
     "unclear", None, "22222222-2222-4222-8222-222222222222", "q_vera0002", None,
     "line one\nline two\ttabbed \"quoted\" back\\slash /slash", None, None,
     "2031-03-04T10:00:00.000008+00:00"),
    ("a9a9a9a9-a9a9-4a9a-8a9a-a9a9a9a9a9a9", "e9e9e9e9-e9e9-4e9e-8e9e-e9e9e9e9e9e9",
     None, "tutoring", "vera-as-9", "sha256:" + "9" * 64, "evidence",
     "seen", "live", None, None, '["ranges", "closures"]',
     "Vera Example: ranges covered — café naïve ✓ \U0001F4D8", None, None,
     "2031-03-04T10:00:00.000009+00:00"),
]
ASSESSMENT_COLUMNS = (
    "assessment_id, event_uuid, lesson_id, lesson_uid, sitting_id, mode, "
    "idempotency_key, fingerprint, kind, level, basis, attempt_id, "
    "question_id, concepts_json, note, next_action, supersedes, created_at"
)

ASSESSMENT_GOLDEN = (
    '{"kind": "assessments_meta", "v": 1, '
    '"lesson_uid": "7b2e9d4c-3a1f-4c6e-8d5b-2f0a1c9e7b3d", "as_of_seq": 9, '
    '"generated_at": "2031-03-04T10:00:00.000000+00:00"}\n'
    '{"seq": 6, "assessment_id": "a6a6a6a6-a6a6-4a6a-8a6a-a6a6a6a6a6a6", '
    '"kind": "summary", "attempt_id": null, "concepts": null, '
    '"event_uuid": "e6e6e6e6-e6e6-4e6e-8e6e-e6e6e6e6e6e6", '
    '"lesson_uid": "7b2e9d4c-3a1f-4c6e-8d5b-2f0a1c9e7b3d", '
    '"sitting_id": "sit-vera-1", "mode": "tutoring", "level": null, '
    '"basis": null, "question_id": null, '
    '"note": "Vera Example: corrected synthesis.", '
    '"next_action": "Predict the growth step on the slices page.", '
    '"supersedes": "a3a3a3a3-a3a3-4a3a-8a3a-a3a3a3a3a3a3", '
    '"created_at": "2031-03-04T10:00:00.000006+00:00"}\n'
    '{"seq": 8, "assessment_id": "a8a8a8a8-a8a8-4a8a-8a8a-a8a8a8a8a8a8", '
    '"kind": "review", "attempt_id": "22222222-2222-4222-8222-222222222222", '
    '"concepts": null, "event_uuid": "e8e8e8e8-e8e8-4e8e-8e8e-e8e8e8e8e8e8", '
    '"lesson_uid": "7b2e9d4c-3a1f-4c6e-8d5b-2f0a1c9e7b3d", '
    '"sitting_id": "sit-vera-2", "mode": "exam", "level": "unclear", '
    '"basis": null, "question_id": "q_vera0002", '
    '"note": "line one\\nline two\\ttabbed \\"quoted\\" back\\\\slash /slash", '
    '"next_action": null, "supersedes": null, '
    '"created_at": "2031-03-04T10:00:00.000008+00:00"}\n'
    '{"seq": 9, "assessment_id": "a9a9a9a9-a9a9-4a9a-8a9a-a9a9a9a9a9a9", '
    '"kind": "evidence", "attempt_id": null, "concepts": ["ranges", "closures"], '
    '"event_uuid": "e9e9e9e9-e9e9-4e9e-8e9e-e9e9e9e9e9e9", '
    '"lesson_uid": "7b2e9d4c-3a1f-4c6e-8d5b-2f0a1c9e7b3d", '
    '"sitting_id": null, "mode": "tutoring", "level": "seen", "basis": "live", '
    '"question_id": null, '
    '"note": "Vera Example: ranges covered — café naïve ✓ 📘", '
    '"next_action": null, "supersedes": null, '
    '"created_at": "2031-03-04T10:00:00.000009+00:00"}\n'
).encode("utf-8")
ASSESSMENT_GOLDEN_SHA256 = (
    "6bc100a18b94ee9fe794d92c7c1a86bd6907421f1589337ce46870d18aebfd03"
)


RUN_LESSON_UID = "7a1c2e3f-4b5d-4c6e-8f90-a1b2c3d4e5f6"
RUN_LESSON_SLUG = "vera-golden-runs"
RUN_FINISHES = [
    ("66666666-6666-4666-8666-666666666661", "vera-run-1", "sha256:" + "6" * 64,
     "hello from Vera\n",
     {"cause": "exit", "exit_code": 0, "truncated": False, "duration_ms": 125},
     datetime(2031, 3, 4, 10, 0, 0, 1, tzinfo=timezone.utc)),
    ("66666666-6666-4666-8666-666666666662", "vera-run-2", "sha256:" + "7" * 64,
     "line one\nline two\ttabbed \"quoted\" back\\slash café ✓ \U0001F4D8 \x00\x1b[0m",
     {"cause": "signal", "signal": "SIGKILL", "truncated": True, "duration_ms": 30000},
     datetime(2031, 3, 4, 10, 0, 45, 500000, tzinfo=timezone.utc)),
    ("66666666-6666-4666-8666-666666666663", "vera-run-3", "sha256:" + "8" * 64,
     "",
     {"cause": "exit", "exit_code": 1, "truncated": False, "duration_ms": 0},
     datetime(2031, 3, 4, 10, 1, 0, 0, tzinfo=timezone.utc)),
]
RUN_GOLDEN = (
    '{"kind":"run","v":1,"run_id":"66666666-6666-4666-8666-666666666661",'
    '"lesson_uid":"7a1c2e3f-4b5d-4c6e-8f90-a1b2c3d4e5f6","block_id":"blk_vera0001",'
    '"runner_id":"python-script-v1",'
    '"file_rev":"sha256:6666666666666666666666666666666666666666666666666666666666666666",'
    '"cause":"exit","exit_code":0,"signal":null,"duration_ms":125,"truncated":false,'
    '"started_at":"2031-03-04T09:59:59.875001+00:00",'
    '"finished_at":"2031-03-04T10:00:00.000001+00:00",'
    '"output_tail":"hello from Vera\\n","output_tail_truncated":false}\n'
    '{"kind":"run","v":1,"run_id":"66666666-6666-4666-8666-666666666662",'
    '"lesson_uid":"7a1c2e3f-4b5d-4c6e-8f90-a1b2c3d4e5f6","block_id":"blk_vera0001",'
    '"runner_id":"python-script-v1",'
    '"file_rev":"sha256:7777777777777777777777777777777777777777777777777777777777777777",'
    '"cause":"signal","exit_code":null,"signal":"SIGKILL","duration_ms":30000,'
    '"truncated":true,"started_at":"2031-03-04T10:00:15.500000+00:00",'
    '"finished_at":"2031-03-04T10:00:45.500000+00:00",'
    '"output_tail":"line one\\nline two\\ttabbed \\"quoted\\" back\\\\slash '
    'café ✓ 📘 \\u0000\\u001b[0m","output_tail_truncated":false}\n'
    '{"kind":"run","v":1,"run_id":"66666666-6666-4666-8666-666666666663",'
    '"lesson_uid":"7a1c2e3f-4b5d-4c6e-8f90-a1b2c3d4e5f6","block_id":"blk_vera0001",'
    '"runner_id":"python-script-v1",'
    '"file_rev":"sha256:8888888888888888888888888888888888888888888888888888888888888888",'
    '"cause":"exit","exit_code":1,"signal":null,"duration_ms":0,"truncated":false,'
    '"started_at":"2031-03-04T10:01:00.000000+00:00",'
    '"finished_at":"2031-03-04T10:01:00.000000+00:00",'
    '"output_tail":"","output_tail_truncated":false}\n'
).encode("utf-8")
RUN_GOLDEN_SHA256 = "3765eaf143d119b6ca0b9485d49be22afaa925d06147c1d09b819d2250f25541"


def _isolated_conn(monkeypatch, tmp_path):
    from app import db as db_module
    from app.services import assessments, attempts, lessons, runs

    monkeypatch.setattr(db_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "activity.sqlite")
    monkeypatch.setattr(db_module, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(lessons, "LESSONS_DIR", tmp_path / "lessons")
    monkeypatch.setattr(
        attempts, "PROJECTION_STATE_DIR", tmp_path / "attempt-projections"
    )
    monkeypatch.setattr(
        assessments, "PROJECTION_STATE_DIR", tmp_path / "assessment-projections"
    )
    monkeypatch.setattr(runs, "PROJECTION_STATE_DIR", tmp_path / "run-projections")
    db_module.init_db()
    return db_module.get_conn()


def _insert_lesson(conn, title: str, slug: str, uid: str) -> dict:
    from app.services import lessons

    conn.execute(
        "INSERT INTO lessons (title, slug, status, created_at, uid) "
        "VALUES (?, ?, 'studying', ?, ?)",
        (title, slug, "2031-01-01T00:00:00+00:00", uid),
    )
    lesson_id = conn.execute(
        "SELECT id FROM lessons WHERE slug = ?", (slug,)
    ).fetchone()[0]
    (Path(lessons.LESSONS_DIR) / slug).mkdir(parents=True)
    return {"id": lesson_id, "slug": slug, "uid": uid, "title": title}


def _insert_assessment_rows(conn, lesson: dict) -> None:
    for row in ASSESSMENT_ROWS:
        conn.execute(
            f"INSERT INTO lesson_assessments ({ASSESSMENT_COLUMNS}) "
            f"VALUES ({', '.join('?' * 18)})",
            (row[0], row[1], lesson["id"], lesson["uid"], *row[2:]),
        )
    conn.commit()


def test_rebuilt_attempt_projection_matches_the_golden_bytes(monkeypatch, tmp_path):
    from app.services import attempts, lessons

    assert hashlib.sha256(GOLDEN).hexdigest() == GOLDEN_SHA256

    conn = _isolated_conn(monkeypatch, tmp_path)
    try:
        lesson = _insert_lesson(
            conn, "Vera Example: projection golden", LESSON_SLUG, LESSON_UID
        )
        lesson_id = lesson["id"]
        for row in ROWS:
            conn.execute(
                "INSERT INTO lesson_attempts (attempt_id, event_uuid, lesson_id, "
                "lesson_uid, idempotency_key, page_id, question_id, page_rev, "
                "answer, stale, kind, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row[0], row[1], lesson_id, LESSON_UID, *row[2:]),
            )
        conn.commit()

        bundle = Path(lessons.LESSONS_DIR) / LESSON_SLUG
        assert attempts.reconcile_projection(conn, lesson)
        first = (bundle / attempts.PROJECTION_NAME).read_bytes()
        assert attempts.reconcile_projection(conn, lesson)
        second = (bundle / attempts.PROJECTION_NAME).read_bytes()
    finally:
        conn.close()

    assert first == GOLDEN
    assert second == GOLDEN


def test_rebuilt_assessment_projection_matches_the_golden_bytes(
    monkeypatch, tmp_path
):
    from app.services import assessments, lessons

    assert hashlib.sha256(ASSESSMENT_GOLDEN).hexdigest() == ASSESSMENT_GOLDEN_SHA256

    conn = _isolated_conn(monkeypatch, tmp_path)
    try:
        lesson = _insert_lesson(
            conn, "Vera Example: assessments golden",
            ASSESSMENT_LESSON_SLUG, ASSESSMENT_LESSON_UID,
        )
        _insert_assessment_rows(conn, lesson)
        bundle = Path(lessons.LESSONS_DIR) / ASSESSMENT_LESSON_SLUG
        with mock.patch.object(
            assessments, "_utc_now_iso", return_value=ASSESSMENT_GENERATED_AT
        ):
            assert assessments.reconcile_projection(conn, lesson)
            first = (bundle / assessments.PROJECTION_NAME).read_bytes()
            assessments._published.pop(ASSESSMENT_LESSON_UID, None)
            assert assessments.reconcile_projection(conn, lesson)
            second = (bundle / assessments.PROJECTION_NAME).read_bytes()
    finally:
        conn.close()

    assert first == ASSESSMENT_GOLDEN
    assert second == ASSESSMENT_GOLDEN


def _finished_job(lesson: dict, finish: tuple):
    from app import runner
    from app.services import lessons
    from app.services.runner_registry import RUNNER_REGISTRY

    run_id, key, file_rev, output, terminal, _finished_at = finish
    request = runner.RunnerRequest(
        block_id="blk_vera0001", file_rev=file_rev, idempotency_key=key,
        runner_id="python-script-v1", filename="main.py",
        snapshot=b"print('invented')\n",
        bundle_dir=str(Path(lessons.LESSONS_DIR) / lesson["slug"]),
        bundle_root=str(lessons.LESSONS_DIR),
        lesson_uid=lesson["uid"], lesson_id=lesson["id"], slug=lesson["slug"],
    )
    return runner.RunnerJob(
        job_id=run_id, request=request, spec=RUNNER_REGISTRY[request.runner_id],
        state=runner.FINISHED, finished_monotonic=time.monotonic(),
        events=[
            {"seq": 1, "event": "output", "stream": "stdout", "text": output},
            {"seq": 2, "event": "exit", **terminal},
        ],
    )


def test_recorded_run_projection_matches_the_golden_bytes(monkeypatch, tmp_path):
    from app.services import lessons, runs

    assert hashlib.sha256(RUN_GOLDEN).hexdigest() == RUN_GOLDEN_SHA256

    conn = _isolated_conn(monkeypatch, tmp_path)
    try:
        lesson = _insert_lesson(
            conn, "Vera Example: runs golden", RUN_LESSON_SLUG, RUN_LESSON_UID
        )
    finally:
        conn.close()
    with mock.patch.object(
        runs, "_finished_at", side_effect=[finish[5] for finish in RUN_FINISHES]
    ):
        for finish in RUN_FINISHES:
            assert runs._record_finish_sync(_finished_job(lesson, finish)) is True

    bundle = Path(lessons.LESSONS_DIR) / RUN_LESSON_SLUG
    assert (bundle / runs.PROJECTION_NAME).read_bytes() == RUN_GOLDEN
    assert not list(bundle.glob(".runs-*.tmp"))
    assert not list(bundle.glob(f"{runs.PROJECTION_NAME}.collision-*"))


def test_publish_clears_a_directory_at_the_target_name(tmp_path):
    from app.services import projection

    target = tmp_path / "state.jsonl"
    target.mkdir()
    dir_fd = os.open(tmp_path, projection.DIRECTORY_FLAGS)
    try:
        projection.publish(dir_fd, target.name, b"one\n", prefix=".state-")
        assert target.is_file() and target.read_bytes() == b"one\n"

        target.unlink()
        target.mkdir()
        (target / "junk.txt").write_text("agent artifact", encoding="utf-8")
        projection.publish(dir_fd, target.name, b"two\n", prefix=".state-")
    finally:
        os.close(dir_fd)

    aside = list(tmp_path.glob("state.jsonl.collision-*"))
    assert target.is_file() and target.read_bytes() == b"two\n"
    assert len(aside) == 1 and aside[0].is_dir()
    assert (aside[0] / "junk.txt").read_text(encoding="utf-8") == "agent artifact"
    assert not list(tmp_path.glob(".state-*.tmp"))


def test_publish_replaces_a_hard_linked_target(tmp_path):
    from app.services import projection

    target = tmp_path / "state.jsonl"
    target.write_bytes(b"before\n")
    other = tmp_path / "outside-copy.txt"
    os.link(target, other)
    assert os.stat(target).st_nlink == 2

    dir_fd = os.open(tmp_path, projection.DIRECTORY_FLAGS)
    try:
        projection.publish(dir_fd, target.name, b"after\n", prefix=".state-")
    finally:
        os.close(dir_fd)

    assert target.read_bytes() == b"after\n"
    assert os.stat(target).st_nlink == 1
    assert other.read_bytes() == b"before\n"
    aside = list(tmp_path.glob("state.jsonl.collision-*"))
    assert len(aside) == 1 and aside[0].read_bytes() == b"before\n"
    assert os.stat(other).st_nlink == 2


def test_file_lock_is_unavailable_while_held(monkeypatch, tmp_path):
    from app.services import assessments, attempts, projection

    conn = _isolated_conn(monkeypatch, tmp_path)
    try:
        lesson = _insert_lesson(
            conn, "Vera Example: lock held",
            ASSESSMENT_LESSON_SLUG, ASSESSMENT_LESSON_UID,
        )
        _insert_assessment_rows(conn, lesson)
        state_dir = tmp_path / "held"
        with projection.file_lock(state_dir, lesson):
            with pytest.raises(OSError):
                with projection.file_lock(state_dir, lesson):
                    pass
        assert (state_dir / f"{ASSESSMENT_LESSON_UID}.lock").is_file()

        with projection.file_lock(assessments.PROJECTION_STATE_DIR, lesson):
            assert assessments.reconcile_projection(conn, lesson) is False
        with projection.file_lock(attempts.PROJECTION_STATE_DIR, lesson):
            assert attempts.reconcile_projection(conn, lesson) is False
        assert assessments.reconcile_projection(conn, lesson) is True
        assert attempts.reconcile_projection(conn, lesson) is True
    finally:
        conn.close()


def test_reconcile_refuses_an_active_transaction(monkeypatch, tmp_path):
    from app.services import assessments, attempts, learner_memory, lessons

    conn = _isolated_conn(monkeypatch, tmp_path)
    try:
        lesson = _insert_lesson(
            conn, "Vera Example: in transaction",
            ASSESSMENT_LESSON_SLUG, ASSESSMENT_LESSON_UID,
        )
        _insert_assessment_rows(conn, lesson)
        bundle = Path(lessons.LESSONS_DIR) / ASSESSMENT_LESSON_SLUG
        assert attempts.reconcile_projection(conn, lesson)
        assert assessments.reconcile_projection(conn, lesson)
        assert learner_memory.reconcile_projection(conn, lesson)
        before = {p.name: p.read_bytes() for p in bundle.iterdir()}

        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO lesson_attempts (attempt_id, event_uuid, lesson_id, "
            "lesson_uid, idempotency_key, page_id, question_id, page_rev, "
            "answer, stale, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("66666666-6666-4666-8666-666666666666",
             "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa6", lesson["id"],
             ASSESSMENT_LESSON_UID, "vera-uncommitted-1", "pg_vera0001",
             "q_vera0001", "sha256:" + "6" * 64,
             "Vera Example: this row rolls back.", 0, "attempt",
             "2031-03-04T10:00:00.000010+00:00"),
        )
        conn.execute(
            f"INSERT INTO lesson_assessments ({ASSESSMENT_COLUMNS}) "
            f"VALUES ({', '.join('?' * 18)})",
            ("b1b1b1b1-b1b1-4b1b-8b1b-b1b1b1b1b1b1",
             "f1f1f1f1-f1f1-4f1f-8f1f-f1f1f1f1f1f1", lesson["id"],
             ASSESSMENT_LESSON_UID, None, "tutoring", "vera-uncommitted-2",
             "sha256:" + "b" * 64, "summary", None, None, None, None, None,
             "Vera Example: this synthesis rolls back.", None, None,
             "2031-03-04T10:00:00.000011+00:00"),
        )
        assert attempts.reconcile_projection(conn, lesson) is False
        assert assessments.reconcile_projection(conn, lesson) is False
        assert learner_memory.reconcile_projection(conn, lesson) is False
        assert {p.name: p.read_bytes() for p in bundle.iterdir()} == before
        conn.rollback()

        assert attempts.reconcile_projection(conn, lesson) is True
        assert assessments.reconcile_projection(conn, lesson) is True
        assert learner_memory.reconcile_projection(conn, lesson) is True
        assert {p.name: p.read_bytes() for p in bundle.iterdir()} == before
    finally:
        conn.close()
