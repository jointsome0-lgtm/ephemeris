"""Generated lesson STATE and the shared Record-panel snapshot (#134)."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from app.db import get_conn, now_iso
from app.routers import learn
from app.services import assessments, attempts, bundle_schema, focus, lessons


def _git(bundle: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(bundle), *args], check=True, capture_output=True,
        timeout=30,
    )


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
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    second = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert '- Current page (data): "related/state-next.html"' in second
    assert "`q_statealpha`: answered; verdict=partial" in second
    assert "`q_statebeta`: unanswered; verdict=none" in second
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
    _git(lesson_dir, "add", "-A")
    _git(lesson_dir, "commit", "-q", "-m", "invented stage checkpoint")

    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    first = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert (
        '- Stages written: 1 of 2 declared; last written stage (data): '
        '"related/state-next.html"; on it 0 of 1 questions answered and '
        "0 of 1 editor files changed from their starter"
    ) in first
    assert (
        "- Declared stages with no page file that can record work (missing, "
        "symlinked, or over the bridge size cap; write or repair them): "
        '"related/state-missing.html"'
    ) in first

    artifact.write_text('print("learner edited the stage")\n', encoding="utf-8")
    (lesson_dir / "related" / "state-missing.html").symlink_to("state-next.html")
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    second = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert "1 of 1 editor files changed from their starter" in second
    assert "- Stages written: 1 of 2 declared" in second
    assert '"related/state-missing.html"' in second

    (lesson_dir / "related" / "state-missing.html").unlink()
    with (lesson_dir / "related" / "state-missing.html").open("wb") as fh:
        fh.truncate(lessons.PAGE_IDENTITY_MAX_BYTES + 1)
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    third = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert "- Stages written: 1 of 2 declared" in third
    assert '"related/state-missing.html"' in third


def _stage_with_one_editor(title: str) -> tuple[dict, Path, Path]:
    lesson, lesson_dir, manifest = _lesson_with_state_surface(title)
    manifest["blocks"] = [{
        "id": "blk_stagecode",
        "page": "pg_statepage2",
        "kind": "editor",
        "language": "python",
        "file": "attempts/blk_stagecode/main.py",
    }]
    bundle_schema.write_manifest(lesson_dir / lessons.MANIFEST_NAME, manifest)
    artifact = lesson_dir / "attempts" / "blk_stagecode" / "main.py"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('print("stage")\n', encoding="utf-8")
    return lesson, lesson_dir, artifact


def test_state_says_the_count_is_unavailable_without_a_repository(
    client, monkeypatch
):
    lesson, lesson_dir, artifact = _stage_with_one_editor("No Repository Fixture")
    monkeypatch.setattr(lessons, "_ensure_bundle_repo", lambda lesson_dir: None)
    shutil.rmtree(lesson_dir / lessons.GIT_DIR_NAME)
    artifact.write_text('print("learner edited the stage")\n', encoding="utf-8")

    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    text = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert (
        '- Stages written: 1 of 1 declared; last written stage (data): '
        '"related/state-next.html"; on it 0 of 1 questions answered and '
        "editor files changed from their starter: count unavailable "
        "(the bundle has no git repository)"
    ) in text
    assert not (lesson_dir / lessons.GIT_DIR_NAME).exists()


def test_state_counts_only_editor_files_a_commit_has_seen(client):
    lesson, lesson_dir, artifact = _stage_with_one_editor("Untracked Editor Fixture")
    artifact.write_text('print("learner edited before any commit")\n', encoding="utf-8")

    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    text = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert "0 of 1 editor files changed from their starter" in text

    _git(lesson_dir, "add", "-A")
    _git(lesson_dir, "commit", "-q", "-m", "invented first checkpoint")
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    text = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert "0 of 1 editor files changed from their starter" in text

    artifact.write_text('print("learner edited again")\n', encoding="utf-8")
    _git(lesson_dir, "commit", "-q", "-a", "-m", "invented second checkpoint")
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    text = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert "1 of 1 editor files changed from their starter" in text, (
        "the comparison is against the file's FIRST commit, not its latest"
    )
    assert not hasattr(lessons, "_TextareaDefaults")
    assert not hasattr(lessons, "_page_starters")


def test_record_panel_keeps_the_shared_snapshot_counts_and_rendering(client):
    lesson, lesson_dir, manifest = _lesson_with_state_surface(
        "Record State Parity Fixture"
    )
    _record_answer_and_state(lesson, manifest)
    conn = get_conn()
    try:
        # A countdown that ran out is credited exactly its length, so backdating
        # the start past the target records 120 seconds through the real path.
        focus.start_run(conn, "countdown", "record-state-focus", target_seconds=120,
                        lesson_id=lesson["id"])
        with conn:
            conn.execute(
                "UPDATE focus_runs SET started_at = ? WHERE client_token = ?",
                ((datetime.fromisoformat(now_iso()) - timedelta(minutes=3))
                 .isoformat(timespec="seconds"), "record-state-focus"),
            )
        focus.finish_run(conn, "record-state-focus")
        lesson = lessons.get_lesson(conn, lesson["id"])
        read = lessons.read_bundle_readonly(lesson)
        db_state = lessons.record_panel_db_state(conn, lesson["id"])
        panel = learn._record_panel(
            conn, lesson, manifest_read=read, db_state=db_state
        )
    finally:
        conn.close()

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


def test_two_edited_editor_files_count_as_changed(client):
    lesson, lesson_dir, manifest = _lesson_with_state_surface("Two Editors Fixture")
    manifest["blocks"] = [
        {
            "id": "blk_stageone",
            "page": "pg_statepage2",
            "kind": "editor",
            "language": "python",
            "file": "attempts/blk_stageone/main.py",
        },
        {
            "id": "blk_stagetwo",
            "page": "pg_statepage2",
            "kind": "editor",
            "language": "go",
            "file": "attempts/blk_stagetwo/main.go",
        },
    ]
    bundle_schema.write_manifest(lesson_dir / lessons.MANIFEST_NAME, manifest)
    (lesson_dir / "related" / "state-next.html").write_text(
        '<html><textarea data-block="blk_stageone">print("one")\n</textarea>'
        '<textarea data-block="blk_stagetwo">package main\n</textarea></html>',
        encoding="utf-8",
    )
    one = lesson_dir / "attempts" / "blk_stageone" / "main.py"
    two = lesson_dir / "attempts" / "blk_stagetwo" / "main.go"
    for path, starter in ((one, 'print("one")\n'), (two, "package main\n")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(starter, encoding="utf-8")
    _git(lesson_dir, "add", "-A")
    _git(lesson_dir, "commit", "-q", "-m", "invented stage checkpoint")

    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    first = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert (
        '- Stages written: 1 of 1 declared; last written stage (data): '
        '"related/state-next.html"; on it 0 of 1 questions answered and '
        "0 of 2 editor files changed from their starter"
    ) in first

    one.write_text('print("one, edited by the learner")\n', encoding="utf-8")
    two.write_text("package main\n\nfunc main() {}\n", encoding="utf-8")
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    second = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert (
        '- Stages written: 1 of 1 declared; last written stage (data): '
        '"related/state-next.html"; on it 0 of 1 questions answered and '
        "2 of 2 editor files changed from their starter"
    ) in second
