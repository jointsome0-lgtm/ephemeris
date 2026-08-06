"""The ask-the-tutor channel (#136).

A learner who does not understand the QUESTION had only the answer box to put
that in, so the confusion was recorded as a wrong answer, read as a broken
mental model, and never replied to. The reverse direction now travels the same
bridge operation, the same endpoint and the same authority row — and says so in
the one field the record shape already had.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.db import get_conn
from app.routers import learn
from app.services import assessments, attempts, bundle_schema, lessons
from app.templating import templates

ANSWER_ID = "q_askpredict"
ASK_ID = "q_askhelp001"


def _ask_lesson(title: str) -> tuple[dict, Path, str]:
    """A bundle with one ordinary question and one ask-the-tutor control."""
    conn = get_conn()
    try:
        lesson_id = lessons.create_lesson(conn, title)
        lesson = lessons.get_lesson(conn, lesson_id)
    finally:
        conn.close()
    lesson_dir = Path(lessons.LESSONS_DIR) / lesson["slug"]
    manifest_path = lesson_dir / lessons.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    page_id = manifest["pages"][0]["id"]
    manifest["questions"] = [
        {
            "id": ANSWER_ID, "page": page_id, "kind": "prediction",
            "label": "Invented prediction",
        },
        {
            "id": ASK_ID, "page": page_id, "kind": bundle_schema.ASK_TUTOR_KIND,
            "label": "Ask about the prediction",
        },
    ]
    bundle_schema.write_manifest(manifest_path, manifest)
    (lesson_dir / "index.html").write_text(
        "<html>Invented ask-tutor fixture</html>", encoding="utf-8"
    )
    return lesson, lesson_dir, page_id


def _rev(lesson_dir: Path) -> str:
    return "sha256:" + hashlib.sha256(
        (lesson_dir / "index.html").read_bytes()
    ).hexdigest()


def _retire(lesson_dir: Path, question_id: str) -> None:
    manifest_path = lesson_dir / lessons.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["questions"] = [
        q for q in manifest["questions"] if q["id"] != question_id
    ]
    bundle_schema.write_manifest(manifest_path, manifest)


def _rekind(lesson_dir: Path, question_id: str, kind: str) -> None:
    manifest_path = lesson_dir / lessons.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for question in manifest["questions"]:
        if question["id"] == question_id:
            question["kind"] = kind
    bundle_schema.write_manifest(manifest_path, manifest)


def _rows(lesson_id: int) -> dict[str, dict]:
    conn = get_conn()
    try:
        return {
            row["question_id"]: dict(row)
            for row in conn.execute(
                "SELECT * FROM lesson_attempts WHERE lesson_id = ? ORDER BY id",
                (lesson_id,),
            )
        }
    finally:
        conn.close()


def _events(lesson_uid: str) -> list[dict]:
    conn = get_conn()
    try:
        payloads = [
            json.loads(row["payload_json"])
            for row in conn.execute(
                "SELECT payload_json FROM events "
                "WHERE type = 'lesson_attempt' ORDER BY id"
            )
        ]
    finally:
        conn.close()
    return [p for p in payloads if p.get("lesson_uid") == lesson_uid]


def _submit(client, lesson: dict, lesson_dir: Path, page_id: str,
            question_id: str, answer: str, key: str) -> dict:
    attempts._reset_rate_limit()
    return client.post(
        f"/learn/lessons/{lesson['id']}/attempts",
        json={
            "question_id": question_id, "page_id": page_id,
            "page_rev": _rev(lesson_dir), "answer": answer,
            "idempotency_key": key,
        },
    )


def test_ask_tutor_records_a_question_and_leaves_every_other_kind_alone(client):
    lesson, lesson_dir, page_id = _ask_lesson("Ask Tutor Endpoint Fixture")

    answered = _submit(
        client, lesson, lesson_dir, page_id, ANSWER_ID,
        "Invented prediction: it prints hello.", "ask-fixture-answer",
    )
    asked = _submit(
        client, lesson, lesson_dir, page_id, ASK_ID,
        "Invented question: what does 'buffered' mean here?", "ask-fixture-ask",
    )
    assert (
        answered.status_code == 200 and asked.status_code == 200
        and answered.json()["result"] == "recorded"
        and asked.json()["result"] == "recorded"
        and asked.json()["projection"] == "projected"
        and asked.json()["stale"] is False
        and asked.json()["attempt_number"] == 1
        and asked.json()["kind"] == attempts.RECORD_KIND_QUESTION
        and answered.json()["kind"] == attempts.RECORD_KIND
    ), "a question to the tutor records through the unchanged attempt endpoint"

    # The replay a retried submission actually sees carries the direction too:
    # the learner who retries a question must not be told they answered one.
    replay = _submit(
        client, lesson, lesson_dir, page_id, ASK_ID,
        "Invented question: what does 'buffered' mean here?", "ask-fixture-ask",
    )
    assert (
        replay.json()["result"] == "duplicate"
        and replay.json()["kind"] == attempts.RECORD_KIND_QUESTION
        and "attempt_number" not in replay.json()
    ), "a duplicate names the direction of the record it replays"

    rows = _rows(lesson["id"])
    assert (
        rows[ANSWER_ID]["kind"] == attempts.RECORD_KIND
        and rows[ASK_ID]["kind"] == attempts.RECORD_KIND_QUESTION
    ), "the authority row stores the direction, derived from the manifest kind"

    lines = [
        json.loads(line) for line in
        (lesson_dir / attempts.PROJECTION_NAME)
        .read_text(encoding="utf-8").splitlines()
    ]
    by_question = {line["question_id"]: line for line in lines}
    assert (
        list(by_question[ASK_ID].keys()) == [
            "kind", "v", "attempt_id", "event_uuid", "lesson_uid", "page_id",
            "question_id", "page_rev", "answer", "created_at", "stale",
        ]
        and by_question[ASK_ID]["v"] == attempts.RECORD_VERSION
        and by_question[ASK_ID]["kind"] == "question"
        and by_question[ANSWER_ID]["kind"] == "attempt"
    ), "§6.2 gains no field: the existing `kind` carries the new value"

    payloads = {p["question_id"]: p for p in _events(lesson["uid"])}
    assert (
        payloads[ASK_ID]["kind"] == "question"
        and payloads[ANSWER_ID]["kind"] == "attempt"
        and "title" not in payloads[ASK_ID] and "pages" not in payloads[ASK_ID]
    ), "the ledger event echoes the direction and nothing more (§8)"

    # The client cannot classify its own write: a submission that ASKS for the
    # question kind on an ordinary question is ignored like any unknown field.
    forged = client.post(
        f"/learn/lessons/{lesson['id']}/attempts",
        json={
            "question_id": ANSWER_ID, "page_id": page_id,
            "page_rev": _rev(lesson_dir), "answer": "Invented second answer",
            "idempotency_key": "ask-fixture-forged", "kind": "question",
        },
    )
    assert (
        forged.status_code == 200
        and _rows(lesson["id"])[ANSWER_ID]["kind"] == attempts.RECORD_KIND
    ), "a page-supplied kind has no channel; the manifest decides"


def test_the_refusal_set_and_the_default_kind_are_unchanged(client):
    lesson, lesson_dir, page_id = _ask_lesson("Ask Tutor Refusal Fixture")
    manifest_path = lesson_dir / lessons.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Grammar-valid but unknown: §4.3 still degrades it to `free_text`, and a
    # free_text answer is an answer — only `ask_tutor` reverses the direction.
    manifest["questions"][0]["kind"] = "invented_kind"
    bundle_schema.write_manifest(manifest_path, manifest)

    degraded = _submit(
        client, lesson, lesson_dir, page_id, ANSWER_ID,
        "Invented answer under an unknown kind", "ask-refusal-degraded",
    )
    assert (
        degraded.status_code == 200
        and _rows(lesson["id"])[ANSWER_ID]["kind"] == attempts.RECORD_KIND
    ), "an unknown question kind still reads as free_text and records an answer"

    undeclared = _submit(
        client, lesson, lesson_dir, page_id, "q_neverdeclared",
        "Invented answer to nothing", "ask-refusal-undeclared",
    )
    malformed = _submit(
        client, lesson, lesson_dir, page_id, "not-a-question-id",
        "Invented answer", "ask-refusal-malformed",
    )
    assert (
        undeclared.status_code == 422
        and undeclared.json()["error"] == "unknown-question"
        and malformed.status_code == 400
        and malformed.json()["error"] == "invalid-question-id"
    ), "the refusal codes are exactly what they were before the new kind"


def test_the_record_panel_reads_a_question_as_a_question(client):
    lesson, lesson_dir, page_id = _ask_lesson("Ask Tutor Panel Fixture")
    _submit(
        client, lesson, lesson_dir, page_id, ANSWER_ID,
        "Invented prediction answer", "ask-panel-answer",
    )
    asked = _submit(
        client, lesson, lesson_dir, page_id, ASK_ID,
        "Invented question: why is this one different?", "ask-panel-ask",
    )

    html = client.get(f"/learn?lesson={lesson['id']}").text
    record = html.split('<details class="lesson-record"', 1)[-1]
    ask_row = record.split(f'id="rec-q-{ASK_ID}"', 1)[-1].split("</li>", 1)[0]
    answer_row = record.split(f'id="rec-q-{ANSWER_ID}"', 1)[-1].split("</li>", 1)[0]
    assert (
        "asked the tutor" in ask_row
        and "Waiting for the tutor to answer." in ask_row
        and "No verdict yet." not in ask_row
        and "asked the tutor" not in answer_row
        and "No verdict yet." in answer_row
    ), "the panel tells a question to the tutor from an answer awaiting a verdict"

    # The reply. It is an ordinary review — the panel leads with the fact that
    # the question was answered and keeps the level beside it.
    conn = get_conn()
    try:
        assessments.record_assessment(conn, lesson, {
            "kind": "review", "level": "unclear",
            "attempt_id": asked.json()["attempt_id"],
            "note": "Invented reply for the learner question",
            "idempotency_key": "ask-panel-reply",
        })
    finally:
        conn.close()
    replied = client.get(f"/learn?lesson={lesson['id']}").text
    replied_row = replied.split(
        f'id="rec-q-{ASK_ID}"', 1)[-1].split("</li>", 1)[0]
    assert (
        ">answered</span>" in replied_row
        and "Invented reply for the learner question" in replied_row
        and "Waiting for the tutor to answer." not in replied_row
    ), "a review on that attempt IS the reply, and reads as one"

    # Retiring the control cannot turn what the learner asked back into an
    # answer they got wrong: the row's direction comes from the RECORD.
    _retire(lesson_dir, ASK_ID)
    after = client.get(f"/learn?lesson={lesson['id']}").text
    retired_row = after.split(f'id="rec-q-{ASK_ID}"', 1)[-1].split("</li>", 1)[0]
    assert (
        "asked the tutor" in retired_row and "retired" in retired_row
    ), "a retired ask control keeps its recorded direction"


def test_state_asks_for_the_unanswered_questions_first(client):
    lesson, lesson_dir, page_id = _ask_lesson("Ask Tutor State Fixture")

    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    quiet = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert (
        "- FIRST: the learner asked you" not in quiet
        and f"`{ASK_ID}` (the learner asks YOU): nothing asked; "
            "answered_by_you=no" in quiet
        and f"`{ANSWER_ID}`: unanswered; verdict=none" in quiet
    ), "an unused ask control owes nothing: the line stays away"

    asked = _submit(
        client, lesson, lesson_dir, page_id, ASK_ID,
        "Invented question: what am I supposed to predict?", "ask-state-ask",
    )
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    pending = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    owed = pending.split("- FIRST:", 1)[-1].split("- Questions:", 1)[0]
    assert (
        "- FIRST: the learner asked you" in pending
        and "asked you 1 question nobody has answered" in pending
        and f"`{ASK_ID}`" in owed
        and asked.json()["attempt_id"] in owed
        and ANSWER_ID not in owed
        and f"`{ASK_ID}` (the learner asks YOU): asked; answered_by_you=no"
            in pending
    ), "an unanswered question to the tutor is the first thing STATE says"

    conn = get_conn()
    try:
        assessments.record_assessment(conn, lesson, {
            "kind": "review", "level": "unclear",
            "attempt_id": asked.json()["attempt_id"],
            "note": "Invented reply",
            "idempotency_key": "ask-state-reply",
        })
    finally:
        conn.close()
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    answered = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert (
        "- FIRST: the learner asked you" not in answered
        and f"`{ASK_ID}` (the learner asks YOU): asked; answered_by_you=yes"
            in answered
    ), "the review is what clears the debt"

    # The debt survives retirement for the same reason the panel row does.
    _retire(lesson_dir, ASK_ID)
    _submit(
        client, lesson, lesson_dir, page_id, ANSWER_ID,
        "Invented answer", "ask-state-answer",
    )
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    retired = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert (
        "- FIRST: the learner asked you" not in retired
        and f"`{ANSWER_ID}`: answered; verdict=none" in retired
    ), "an answered question stays answered once its control is retired"

    # And the contract the brief teaches is the one the app implements: the
    # kind literal a page must declare, the literal the projection then
    # carries, and the reply that clears the debt above.
    assert (
        '"kind": "ask_tutor"' in retired
        and "## Let the learner ask you back" in retired
        and f'`kind` is `"{attempts.RECORD_KIND}"` for an answer' in retired
        and f'`"{attempts.RECORD_KIND_QUESTION}"` for' in retired
        and "the same call is your REPLY" in retired
    ), "the pedagogy contract carries the control it now asks pages to build"


def test_every_unanswered_question_is_owed_an_answer_not_just_the_latest(client):
    """A learner who asks the same control twice is owed two replies. STATE
    counts the ATTEMPTS, so answering the newest does not bury the older one —
    the panel's latest-per-question fold would have."""
    lesson, lesson_dir, page_id = _ask_lesson("Ask Tutor Debt Fixture")
    first = _submit(
        client, lesson, lesson_dir, page_id, ASK_ID,
        "Invented first question", "ask-debt-1",
    )
    second = _submit(
        client, lesson, lesson_dir, page_id, ASK_ID,
        "Invented second question", "ask-debt-2",
    )

    conn = get_conn()
    try:
        assessments.record_assessment(conn, lesson, {
            "kind": "review", "level": "unclear",
            "attempt_id": second.json()["attempt_id"],
            "note": "Invented reply to the newest one only",
            "idempotency_key": "ask-debt-reply",
        })
    finally:
        conn.close()

    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    brief = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    owed = brief.split("- FIRST:", 1)[-1].split("- Questions:", 1)[0]
    assert (
        "- FIRST: the learner asked you" in brief
        and first.json()["attempt_id"] in owed
        and second.json()["attempt_id"] not in owed
    ), "the reviewed attempt drops out of the debt and the unreviewed one stays"


def test_re_kinding_a_control_leaves_what_was_already_recorded_alone(client):
    """The manifest classifies a write at record time and never again. Turning
    an ordinary question into an ask control afterwards must not retro-label
    the answers already given to it — the row says what it was."""
    lesson, lesson_dir, page_id = _ask_lesson("Ask Tutor Re-kind Fixture")
    _submit(
        client, lesson, lesson_dir, page_id, ANSWER_ID,
        "Invented answer given while it was a prediction", "ask-rekind-answer",
    )

    _rekind(lesson_dir, ANSWER_ID, bundle_schema.ASK_TUTOR_KIND)

    html = client.get(f"/learn?lesson={lesson['id']}").text
    row = html.split(f'id="rec-q-{ANSWER_ID}"', 1)[-1].split("</li>", 1)[0]
    assert (
        "asked the tutor" not in row and "No verdict yet." in row
    ), "an answer stays an answer when its control changes kind under it"

    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    brief = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert (
        "- FIRST: the learner asked you" not in brief
        and f"`{ANSWER_ID}`: answered; verdict=none" in brief
    ), "and owes the tutor nothing, so STATE reports it the way it always did"

    # From here on it is an ask control, and the next write travels the other way.
    _submit(
        client, lesson, lesson_dir, page_id, ANSWER_ID,
        "Invented question asked after the change", "ask-rekind-ask",
    )
    assert _rows(lesson["id"])[ANSWER_ID]["kind"] == (
        attempts.RECORD_KIND_QUESTION
    ), "the new kind governs the writes that come after it"


def test_the_launch_button_names_a_tutor_this_machine_actually_has(
    client, monkeypatch
):
    """One click must land on a real program. The command is probed from the
    agent shell's PATH, and with no agent CLI installed there is no button."""
    installed: set[str] = set()
    monkeypatch.setattr(
        lessons, "_on_agent_path", lambda program: program in installed
    )
    # The whole tools row is behind the same local-only gate as the terminal.
    monkeypatch.setitem(
        templates.env.globals, "client_is_local", lambda request: True
    )

    installed.update({"codex", "aider"})
    assert lessons.tutor_launch_command() == (
        f'codex "{lessons.TUTOR_PROMPT}"'
    ), "the first installed CLI in preference order wins"
    installed.add("claude")
    assert lessons.tutor_launch_command() == f'claude "{lessons.TUTOR_PROMPT}"'

    lesson, _lesson_dir, _page_id = _ask_lesson("Ask Tutor Launch Fixture")
    present = client.get(f"/learn?lesson={lesson['id']}").text
    assert (
        'id="lesson-review-btn"' in present
        and f'data-term-command="claude &#34;{lessons.TUTOR_PROMPT}&#34;"'
            in present
    ), "the page offers the command it can keep"

    installed.clear()
    assert lessons.tutor_launch_command() is None
    absent = client.get(f"/learn?lesson={lesson['id']}").text
    assert (
        'id="lesson-review-btn"' not in absent
        and 'id="lesson-term-btn"' in absent
    ), "with nothing to launch the button is gone, the bare terminal stays"


def test_learn_html_renders_under_a_pre_136_router_context(client, monkeypatch):
    """The live process renders this working-tree template against its OWN
    (older) context until the restart, so the flag #136 adds is `is defined`-
    guarded — proven here by rendering the same page without it."""
    lesson, lesson_dir, page_id = _ask_lesson("Ask Tutor Template Guard")
    _submit(
        client, lesson, lesson_dir, page_id, ASK_ID,
        "Invented question under the old context", "ask-guard-ask",
    )

    captured: dict = {}
    original = learn.templates.TemplateResponse

    def capture(request, name, context, *args, **kwargs):
        captured.update(context)
        return original(request, name, context, *args, **kwargs)

    monkeypatch.setattr(learn.templates, "TemplateResponse", capture)
    assert client.get(f"/learn?lesson={lesson['id']}").status_code == 200
    assert captured["selected"]["record"]["questions"], "the panel rendered"

    selected = dict(captured["selected"])
    record = dict(selected["record"])
    record["questions"] = [
        {k: v for k, v in q.items() if k != "ask_tutor"}
        for q in record["questions"]
    ]
    record["retired"] = [
        {k: v for k, v in q.items() if k != "ask_tutor"}
        for q in record["retired"]
    ]
    selected["record"] = record
    old = templates.env.get_template("learn.html").render(
        {**captured, "selected": selected}
    )
    assert (
        f'id="rec-q-{ASK_ID}"' in old
        and "asked the tutor" not in old
        and "Waiting for the tutor to answer." not in old
        and "No verdict yet." in old
    ), "without the flag every row simply renders the way it did before"


def test_the_kind_column_lands_on_a_populated_pre_v17_database(tmp_path):
    """The live database is not empty when this migration reaches it — it holds
    the attempts that motivated the issue. Those rows are answers by definition
    (nothing else could record before the column existed), so the default is
    the migration's whole answer for them, and it must run without a rewrite.
    """
    import sqlite3

    from app import db

    path = tmp_path / "pre-v17.sqlite"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(
            "CREATE TABLE lessons (id INTEGER PRIMARY KEY);"
            + db._SCHEMA_V12
            + "INSERT INTO lessons(id) VALUES (1);"
            "INSERT INTO lesson_attempts (attempt_id, event_uuid, lesson_id,"
            " lesson_uid, idempotency_key, page_id, question_id, page_rev,"
            " answer, stale, created_at) VALUES"
            " ('invented-attempt', 'invented-event', 1, 'invented-uid',"
            "  'invented-key', 'pg_invented1', 'q_invented1', 'sha256:0',"
            "  'Invented pre-migration answer', 0, '2026-01-01T00:00:00+00:00');"
            "PRAGMA user_version = 16;"
        )
        db._migrate_to_17(conn)
        conn.commit()
        assert [dict(row) for row in conn.execute(
            "SELECT id, kind FROM lesson_attempts"
        )] == [{"id": 1, "kind": attempts.RECORD_KIND}], (
            "every row written before the column existed is an answer"
        )

        # A hand-repaired database that already has the column converges.
        db._migrate_to_17(conn)

        # And the authority stays structurally valid under any second writer:
        # the two kinds the app knows are the two the schema permits.
        insert = (
            "INSERT INTO lesson_attempts (attempt_id, event_uuid, lesson_id,"
            " lesson_uid, idempotency_key, page_id, question_id, page_rev,"
            " answer, stale, created_at, kind) VALUES"
            " (?, 'invented-event', 1, 'invented-uid', ?, 'pg_invented1',"
            "  'q_invented2', 'sha256:0', 'Invented', 0, 't', ?)"
        )
        conn.execute(insert, ("invented-b", "key-b", attempts.RECORD_KIND_QUESTION))
        try:
            conn.execute(insert, ("invented-c", "key-c", "invented_kind"))
            raise AssertionError("the kind column accepted a value nothing writes")
        except sqlite3.IntegrityError:
            pass
    finally:
        conn.close()


def test_the_brief_carries_the_question_text_not_only_a_pointer(client):
    """`attempts.jsonl` is a repairable copy: a write can land `pending`, and
    the reconcile pass runs AFTER the brief is written. So the tutor is handed
    the question itself, out of the authority, and can answer with the file
    missing entirely."""
    lesson, lesson_dir, page_id = _ask_lesson("Ask Tutor Text Fixture")
    short = "Invented question: which line blocks?"
    long = "Invented long question. " + "Invented padding sentence. " * 40
    _submit(client, lesson, lesson_dir, page_id, ASK_ID, short, "ask-text-1")
    _rekind(lesson_dir, ANSWER_ID, bundle_schema.ASK_TUTOR_KIND)
    _submit(client, lesson, lesson_dir, page_id, ANSWER_ID, long, "ask-text-2")

    (lesson_dir / attempts.PROJECTION_NAME).unlink()
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    owed = (lesson_dir / lessons.AGENTS_FILENAME).read_text(
        encoding="utf-8"
    ).split("- FIRST:", 1)[-1].split("- Questions:", 1)[0]
    assert (
        short in owed
        and long[:300] in owed
        and long not in owed
        and "cut here" in owed
    ), "every open question is quoted, bounded, and marked where it was cut"

    # And the file the cut points at is back: the reconcile now runs BEFORE the
    # brief, so what STATE sends the tutor to exists by the time it is read.
    projected = (lesson_dir / attempts.PROJECTION_NAME).read_text(
        encoding="utf-8"
    )
    assert (
        short in projected and long in projected
    ), "the projection is repaired before the brief points at it"


def test_a_question_is_not_counted_as_an_attempt(client):
    """The panel's count line is the one number the learner reads. Asking for
    help is not a try at anything, so it is counted beside the attempts and
    never inside them."""
    lesson, lesson_dir, page_id = _ask_lesson("Ask Tutor Counter Fixture")
    _submit(
        client, lesson, lesson_dir, page_id, ANSWER_ID,
        "Invented answer", "ask-count-answer",
    )
    html = client.get(f"/learn?lesson={lesson['id']}").text
    record = html.split('<details class="lesson-record"', 1)[-1]
    assert (
        'data-record-count="attempts">1</span> attempts' in record
        and '<span class="rec-asked" hidden>' in record
        and 'data-record-count="questions">0</span> asked' in record
    ), "the chip is on the line but hidden until there is a question to show"

    _submit(
        client, lesson, lesson_dir, page_id, ASK_ID,
        "Invented question", "ask-count-ask",
    )
    html = client.get(f"/learn?lesson={lesson['id']}").text
    record = html.split('<details class="lesson-record"', 1)[-1]
    counts = client.get(f"/learn/lessons/{lesson['id']}/record-counts").json()
    assert (
        'data-record-count="attempts">1</span> attempts' in record
        and 'data-record-count="questions">1</span> asked' in record
        and '<span class="rec-asked" hidden>' not in record
        and counts["attempts"] == 1 and counts["questions"] == 1
    ), "the question shows up as asked, and the attempt count holds still"

    # And the runtime reveals that chip itself, so the first question of a
    # session lands on the open page instead of waiting for a reload.
    root = Path(__file__).resolve().parent.parent
    source = (root / "app" / "static" / "src" / "learn-bridge.ts").read_text(
        encoding="utf-8"
    )
    assert (
        'if (cell && name === "questions") {' in source
        and 'chip.hidden = value === "0"' in source
        and '"attempts", "questions", "assessments", "verdicts"' in source
    ), "the counts poll fills the asked cell and unhides it"


def test_opening_a_terminal_does_not_rewrite_an_intact_projection(client):
    """A lesson's attempt history has no ceiling, and a terminal open is not a
    reason to rewrite the projection of an authority that has not moved."""
    lesson, lesson_dir, page_id = _ask_lesson("Ask Tutor Reconcile Fixture")
    _submit(
        client, lesson, lesson_dir, page_id, ASK_ID,
        "Invented question before the terminal", "ask-reconcile-1",
    )
    projection = lesson_dir / attempts.PROJECTION_NAME
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    before = projection.stat()
    for _ in range(3):
        assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    after = projection.stat()
    assert (
        (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    ), "an intact projection is verified, never republished"

    # A file that IS stale still gets repaired on the next open.
    projection.write_text("invented corruption\n", encoding="utf-8")
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    assert (
        "Invented question before the terminal"
        in projection.read_text(encoding="utf-8")
    ), "a mutated projection is rebuilt from the authority"


def test_the_review_button_rides_the_existing_terminal_input_path():
    """Scope guard for the one-click launch: it opens the lesson's agent tab
    and types into it. No endpoint, no second way to execute anything."""
    root = Path(__file__).resolve().parent.parent
    source = (root / "app" / "static" / "src" / "terminal.ts").read_text(
        encoding="utf-8"
    )
    emitted = (root / "app" / "static" / "terminal.js").read_text(encoding="utf-8")
    template = (root / "app" / "templates" / "learn.html").read_text(
        encoding="utf-8"
    )

    assert (
        "lesson-review-btn" in template
        and 'data-term-command="{{ selected.tutor_command }}"' in template
        and "lesson-review-btn" in source
        and "openLessonTab(slug" in source
    ), "the button is wired to the lesson's own agent tab"
    # The text reaches the shell through xterm's paste path — the same one the
    # paste button uses — and reaches it as ONE line, so it stops at the prompt.
    assert (
        "tab.term.paste(text)" in source
        and "replace(/[\\r\\n]+/g, ' ')" in source
        and "new WebSocket" not in source.split("function typeCommand", 1)[-1]
            .split("function closeActiveTab", 1)[0]
    ), "typing a command adds no transport of its own"
    # And only into a shell this click started: a session already open may
    # have an editor or an agent in the foreground, which would take the text
    # as content (review round 2).
    handler = source.split("if (reviewBtn) {", 1)[-1].split("\n  }", 1)[0]
    assert (
        "var created = openLessonTab(slug" in handler
        and "if (created && tab && tab.id === activeId) typeCommand" in handler
        and "): boolean {" in source.split("function openLessonTab", 1)[-1]
            .split("\n", 1)[0]
    ), "a reused lesson session is brought forward, never typed into"
    assert "lesson-review-btn" in emitted, (
        "the committed .js is re-emitted from the .ts (npm run build)"
    )


def test_the_learner_is_told_a_question_was_sent_not_an_answer_recorded():
    """The confirmation is parent-owned (bridge ABI §3.1) and now reads the
    direction the server derived, so the app stops calling the learner's
    question an attempt in the one place they actually look."""
    root = Path(__file__).resolve().parent.parent
    source = (root / "app" / "static" / "src" / "learn-bridge.ts").read_text(
        encoding="utf-8"
    )
    emitted = (root / "app" / "static" / "learn-bridge.js").read_text(
        encoding="utf-8"
    )
    assert (
        'const asked = rec["kind"] === "question";' in source
        and 'toast("question sent to the tutor")' in source
        and 'toast(asked ? "question already sent"' in source
        and 'if (typeof rec["kind"] === "string") reply["kind"]' in source
    ), "the toast and the reply both carry the server-derived direction"
    assert "question sent to the tutor" in emitted, (
        "the committed .js is re-emitted from the .ts (npm run build)"
    )
