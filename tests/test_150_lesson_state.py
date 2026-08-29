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
    assert (
        '- Stages written: 1 of 1 declared; last written stage (data): '
        '"related/state-next.html"; on it 0 of 1 questions answered and '
        "0 of 0 editor files changed from their starter"
    ) in first
    assert "## Pacing: one stage ahead of the learner" in first
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
    assert (
        '- Stages written: 1 of 1 declared; last written stage (data): '
        '"related/state-next.html"; on it 0 of 1 questions answered and '
        "0 of 0 editor files changed from their starter"
    ) in second
    assert secret not in second
    assert first != second
    assert "write one early provisional resume brief" in (
        lesson_dir / lessons.REFERENCE_DIR_NAME / "record.md"
    ).read_text(encoding="utf-8"), (
        "the verdict-writing contract rides along as a companion (#195)"
    )


def test_state_stage_line_skips_missing_pages_and_counts_changed_editor_files(
    client,
):
    lesson, lesson_dir, manifest = _lesson_with_state_surface("Stage Line Fixture")
    manifest["pages"].append({
        "id": "pg_statemissing",
        "path": "related/state-missing.html",
        "title": "Declared but never written",
    })
    manifest["blocks"].append({
        "id": "blk_stagecode",
        "page": "pg_statepage2",
        "kind": "editor",
        "language": "python",
        "file": "attempts/blk_stagecode/main.py",
        "runner_id": "python-script-v1",
    })
    bundle_schema.write_manifest(lesson_dir / lessons.MANIFEST_NAME, manifest)
    (lesson_dir / "related" / "state-next.html").write_text(
        '<html><textarea data-block="blk_stagecode">print("stage")\n'
        "</textarea></html>",
        encoding="utf-8",
    )
    artifact = lesson_dir / "attempts" / "blk_stagecode" / "main.py"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('print("stage")\n', encoding="utf-8")

    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    first = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert (
        '- Stages written: 1 of 2 declared; last written stage (data): '
        '"related/state-next.html"; on it 0 of 1 questions answered and '
        "0 of 1 editor files changed from their starter"
    ) in first
    assert (
        "- Declared stages with no readable page file (write or repair them; "
        'nothing can be recorded on a placeholder): "related/state-missing.html"'
    ) in first

    artifact.write_text('print("learner edited the stage")\n', encoding="utf-8")
    (lesson_dir / "related" / "state-missing.html").symlink_to("state-next.html")
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    second = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert "1 of 1 editor files changed from their starter" in second
    assert "- Stages written: 1 of 2 declared" in second
    assert '"related/state-missing.html"' in second


def test_starter_flag_survives_a_page_that_also_holds_answer_textareas(client):
    """A real pedagogy page mixes answer/output textareas with the editor one
    (lesson thank-go-concurrency-1-2: 8 textareas, 1 editor block). Only the
    `data-block` marker identifies the starter there; pairing by document
    order would take an answer textarea for it."""
    lesson, lesson_dir, _manifest = _lesson_with_state_surface("Mixed Page Fixture")
    artifact = lesson_dir / "attempts" / "blk_statecode" / "main.py"

    unmarked = (
        '<html><textarea data-q="q_statealpha"></textarea>'
        '<textarea>print("starter")\n</textarea>'
        '<textarea readonly>run output</textarea></html>'
    )
    (lesson_dir / "index.html").write_text(unmarked, encoding="utf-8")
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    text = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert '"attempts/blk_statecode/main.py": mtime=' in text
    assert "equal_to_starter=unknown" in text
    assert "equal_to_starter=true" not in text

    marked = unmarked.replace(
        '<textarea>print("starter")', '<textarea data-block="blk_statecode">print("starter")'
    )
    (lesson_dir / "index.html").write_text(marked, encoding="utf-8")
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    text = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert '"attempts/blk_statecode/main.py": mtime=' in text
    assert "equal_to_starter=true" in text

    artifact.write_text('print("learner changed this")\n', encoding="utf-8")
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    text = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert "equal_to_starter=false" in text
    assert 'data-block="blk_<id>"' in (
        lesson_dir / lessons.REFERENCE_DIR_NAME / "bridge.md"
    ).read_text(encoding="utf-8"), (
        "the starter-marker convention rides along as a companion (#195)"
    )


def test_record_panel_keeps_the_shared_snapshot_counts_and_rendering(client):
    lesson, lesson_dir, manifest = _lesson_with_state_surface(
        "Record State Parity Fixture"
    )
    _record_answer_and_state(lesson, manifest)
    conn = get_conn()
    try:
        focus.record_session(conn, "open", 2 * 60, lesson_id=lesson["id"])
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
        "questions": 0,
        "assessments": 2,
        "focus": "2m",
        "focus_seconds": 120,
    }
    assert panel["summary"]["note"] == "Invented provisional summary"
    assert panel["questions"][0]["review"]["level"] == "partial"

    html = client.get(f"/learn?lesson={lesson['id']}").text
    record = html.split('<details class="lesson-record"', 1)[-1]
    assert 'data-record-count="attempts">1</span> attempts' in record
    assert 'data-record-count="assessments">2</span> active' in record
    assert 'data-record-count="focus">2m</span> focused' in record
    assert "Invented provisional summary" in record
