"""Generated lesson STATE and the shared Record-panel snapshot (#134)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from app.db import get_conn
from app.routers import learn
from app.services import assessments, attempts, bundle_schema, focus, lessons


def _lesson_with_state_surface(title: str) -> tuple[dict, Path, dict]:
    conn = get_conn()
    try:
        lesson_id = lessons.create_lesson(conn, title)
        lesson = lessons.get_lesson(conn, lesson_id)
    finally:
        conn.close()
    lesson_dir = Path(lessons.LESSONS_DIR) / lesson["slug"]
    manifest = json.loads(
        (lesson_dir / lessons.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    first_page = manifest["pages"][0]["id"]
    manifest["pages"].append({
        "id": "pg_statepage2",
        "path": "related/state-next.html",
        "title": "State next",
    })
    manifest["questions"] = [
        {
            "id": "q_statealpha",
            "page": first_page,
            "kind": "prediction",
            "label": "State alpha",
        },
        {
            "id": "q_statebeta",
            "page": "pg_statepage2",
            "kind": "free_text",
            "label": "State beta",
        },
    ]
    manifest["blocks"] = [{
        "id": "blk_statecode",
        "page": first_page,
        "kind": "editor",
        "language": "python",
        "file": "attempts/blk_statecode/main.py",
        "runner_id": "python-script-v1",
    }]
    bundle_schema.write_manifest(lesson_dir / lessons.MANIFEST_NAME, manifest)
    (lesson_dir / "related").mkdir(exist_ok=True)
    (lesson_dir / "index.html").write_text(
        '<html><textarea>print("starter")\n</textarea></html>', encoding="utf-8"
    )
    (lesson_dir / "related" / "state-next.html").write_text(
        "<html>State next</html>", encoding="utf-8"
    )
    artifact = lesson_dir / "attempts" / "blk_statecode" / "main.py"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('print("starter")\n', encoding="utf-8")
    (lesson_dir / "attempts" / "notes.txt").write_text(
        "Invented terminal artifact\n", encoding="utf-8"
    )
    return lesson, lesson_dir, manifest


def _record_answer_and_state(lesson: dict, manifest: dict) -> None:
    page = Path(lessons.LESSONS_DIR) / lesson["slug"] / "index.html"
    page_rev = "sha256:" + hashlib.sha256(page.read_bytes()).hexdigest()
    conn = get_conn()
    try:
        result = attempts.record_attempt(conn, lesson, {
            "question_id": "q_statealpha",
            "page_id": manifest["pages"][0]["id"],
            "page_rev": page_rev,
            "answer": "Invented state answer",
            "idempotency_key": f"{lesson['slug']}-attempt",
        })
        assessments.record_assessment(conn, lesson, {
            "kind": "review",
            "level": "partial",
            "attempt_id": result["attempt_id"],
            "note": "Invented partial verdict",
            "idempotency_key": f"{lesson['slug']}-review",
        })
        assessments.record_assessment(conn, lesson, {
            "kind": "summary",
            "note": "Invented provisional summary",
            "idempotency_key": f"{lesson['slug']}-summary",
        })
        lessons.set_current_entry(conn, lesson["id"], "related/state-next.html")
    finally:
        conn.close()


def test_state_regenerates_from_current_db_and_never_serializes_token(
    client, monkeypatch
):
    lesson, lesson_dir, manifest = _lesson_with_state_surface(
        "Generated State Fixture"
    )
    secret = "state-token-value-must-never-appear"
    monkeypatch.setenv("EPHEMERIS_ASSESS_TOKEN", secret)

    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    first = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert "## STATE (generated; refreshed on every terminal open)" in first
    assert f'- Lesson title (data): "{lesson["title"]}"' in first
    assert f"- Lesson slug: `{lesson['slug']}`" in first
    assert '- Current page (data): "index.html"' in first
    assert "`q_statealpha`: unanswered; verdict=none" in first
    assert "`q_statebeta`: unanswered; verdict=none" in first
    assert "equal_to_starter=true" in first
    assert '"attempts/notes.txt": mtime=' in first
    assert "- Summary exists: no" in first
    assert "`EPHEMERIS_ASSESS_URL`, `EPHEMERIS_ASSESS_TOKEN`" in first
    assert secret not in first

    _record_answer_and_state(lesson, manifest)
    artifact = lesson_dir / "attempts" / "blk_statecode" / "main.py"
    artifact.write_text('print("learner changed this")\n', encoding="utf-8")
    os.utime(artifact, (1_900_000_000, 1_900_000_000))

    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    second = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert '- Current page (data): "related/state-next.html"' in second
    assert "`q_statealpha`: answered; verdict=partial" in second
    assert "`q_statebeta`: unanswered; verdict=none" in second
    assert "mtime=2030-03-17T17:46:40Z; equal_to_starter=false" in second
    assert "- Summary exists: yes" in second
    assert secret not in second
    assert first != second
    assert "write one early provisional resume brief" in second


def test_record_panel_keeps_the_shared_snapshot_counts_and_rendering(client):
    lesson, lesson_dir, manifest = _lesson_with_state_surface(
        "Record State Parity Fixture"
    )
    _record_answer_and_state(lesson, manifest)
    conn = get_conn()
    try:
        focus.record_session(conn, "pomo", 2 * 60, None, lesson["id"])
        lesson = lessons.get_lesson(conn, lesson["id"])
        read = lessons.read_bundle_readonly(lesson)
        db_state = lessons.record_panel_db_state(conn, lesson["id"])
        panel = learn._record_panel(
            conn, lesson, manifest_read=read, db_state=db_state
        )
    finally:
        conn.close()

    assert learn._record_panel_db_state is lessons.record_panel_db_state
    assert panel["counts"] == {
        "attempts": 1,
        "assessments": 2,
        "focus": "2m",
        "focus_seconds": 120,
    }
    assert panel["summary"]["note"] == "Invented provisional summary"
    assert panel["questions"][0]["review"]["level"] == "partial"

    html = client.get(f"/learn?lesson={lesson['id']}").text
    record = html.split('<details class="lesson-record"', 1)[-1]
    assert "1 attempt" in record
    assert "2 active" in record
    assert "2m focused" in record
    assert "Invented provisional summary" in record
