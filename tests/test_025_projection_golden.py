"""Golden bytes for the attempts projection (#225).

The rows below are invented. The expected text was rendered by the attempts
service before the shared projection module existed; a rebuild from the same
rows must still produce exactly these bytes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

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


def test_rebuilt_attempt_projection_matches_the_golden_bytes(monkeypatch, tmp_path):
    from app import db as db_module
    from app.services import attempts, lessons

    assert hashlib.sha256(GOLDEN).hexdigest() == GOLDEN_SHA256

    monkeypatch.setattr(db_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "activity.sqlite")
    monkeypatch.setattr(db_module, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(lessons, "LESSONS_DIR", tmp_path / "lessons")
    monkeypatch.setattr(
        attempts, "PROJECTION_STATE_DIR", tmp_path / "attempt-projections"
    )
    db_module.init_db()

    conn = db_module.get_conn()
    try:
        conn.execute(
            "INSERT INTO lessons (title, slug, status, created_at, uid) "
            "VALUES (?, ?, 'studying', ?, ?)",
            ("Vera Example: projection golden", LESSON_SLUG,
             "2031-01-01T00:00:00+00:00", LESSON_UID),
        )
        lesson_id = conn.execute(
            "SELECT id FROM lessons WHERE slug = ?", (LESSON_SLUG,)
        ).fetchone()[0]
        for row in ROWS:
            conn.execute(
                "INSERT INTO lesson_attempts (attempt_id, event_uuid, lesson_id, "
                "lesson_uid, idempotency_key, page_id, question_id, page_rev, "
                "answer, stale, kind, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (row[0], row[1], lesson_id, LESSON_UID, *row[2:]),
            )
        conn.commit()

        lesson = {"id": lesson_id, "slug": LESSON_SLUG, "uid": LESSON_UID}
        bundle = Path(lessons.LESSONS_DIR) / LESSON_SLUG
        bundle.mkdir(parents=True)
        assert attempts.reconcile_projection(conn, lesson)
        first = (bundle / attempts.PROJECTION_NAME).read_bytes()
        assert attempts.reconcile_projection(conn, lesson)
        second = (bundle / attempts.PROJECTION_NAME).read_bytes()
    finally:
        conn.close()

    assert first == GOLDEN
    assert second == GOLDEN
