"""Record-panel context without a bridge ABI addition (#133 tier 1).

The panel is the only place a learner meets what the tutor concluded, so this
covers the three things that made it unreadable: evidence notes cut into a
tooltip, a verdict that arrives while the page is open and changes nothing,
and a retired question whose history stands with no link to where the question
went. Plus the manifest field the last of those needs, and the guard that lets
the LIVE process render this template before its own restart.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.db import get_conn
from app.routers import learn
from app.services import assessments, attempts, bundle_schema, lessons
from app.templating import templates

# Longer than the 200-character tooltip the note used to be truncated into, so
# "the whole note is on the page" is a claim the assertion can actually make.
LONG_NOTE = (
    "The prediction reads the channel as a buffer that keeps the sender "
    "running, which is the one thing an unbuffered channel does not do: the "
    "send blocks until a receiver is ready, and that rendezvous is what "
    "orders the two goroutines here. Re-run the fan-in exercise and watch "
    "which line prints first."
)


def _lesson_with_record(title: str) -> tuple[dict, Path, dict]:
    """A lesson whose first question is answered and reviewed, then RETIRED
    from the manifest in favour of a successor that declares `replaces`."""
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
    page_id = manifest["pages"][0]["id"]
    manifest["questions"] = [
        {"id": "q_recold001", "page": page_id, "kind": "prediction",
         "label": "Old wording"},
    ]
    bundle_schema.write_manifest(lesson_dir / lessons.MANIFEST_NAME, manifest)
    page = lesson_dir / "index.html"
    page.write_text("<html>Record context fixture</html>", encoding="utf-8")

    conn = get_conn()
    try:
        attempt = attempts.record_attempt(conn, lesson, {
            "question_id": "q_recold001",
            "page_id": page_id,
            "page_rev": "sha256:" + hashlib.sha256(page.read_bytes()).hexdigest(),
            "answer": "Invented answer to the old wording",
            "idempotency_key": f"{lesson['slug']}-attempt",
        })
        assessments.record_assessment(conn, lesson, {
            "kind": "review", "level": "partial",
            "attempt_id": attempt["attempt_id"], "note": LONG_NOTE,
            "idempotency_key": f"{lesson['slug']}-review",
        })
        assessments.record_assessment(conn, lesson, {
            "kind": "evidence", "level": "weak", "basis": "attempts",
            "concepts": ["unbuffered channels"], "note": LONG_NOTE,
            "idempotency_key": f"{lesson['slug']}-evidence",
        })
    finally:
        conn.close()

    # The rewrite: the durable id is retired forever, so the new wording
    # arrives under a new id and names its predecessor.
    manifest["questions"] = [
        {"id": "q_recnew001", "page": page_id, "kind": "prediction",
         "label": "New wording", "replaces": "q_recold001"},
    ]
    bundle_schema.write_manifest(lesson_dir / lessons.MANIFEST_NAME, manifest)
    return lesson, lesson_dir, manifest


def _panel(lesson: dict) -> dict:
    conn = get_conn()
    try:
        fresh = lessons.get_lesson(conn, lesson["id"])
        return learn._record_panel(
            conn, fresh,
            manifest_read=lessons.read_bundle_readonly(fresh),
            db_state=lessons.record_panel_db_state(conn, fresh["id"]),
        )
    finally:
        conn.close()


def _read(lesson_dir: Path, manifest: dict) -> bundle_schema.ManifestRead:
    bundle_schema.write_manifest(lesson_dir / lessons.MANIFEST_NAME, manifest)
    return bundle_schema.read_manifest_path(lesson_dir / lessons.MANIFEST_NAME)


# --- the manifest field (§4.3) ----------------------------------------------


def test_replaces_is_read_additively_and_never_moves_history(client):
    lesson, lesson_dir, manifest = _lesson_with_record("Replaces Read Fixture")
    read = bundle_schema.read_manifest_path(lesson_dir / lessons.MANIFEST_NAME)
    assert read.outcome == "ok"
    assert read.questions == [{
        "id": "q_recnew001", "page": manifest["pages"][0]["id"],
        "kind": "prediction", "label": "New wording", "replaces": "q_recold001",
    }]
    # Presentation only: the successor claim moves no attempt, and the retired
    # id stays unwritable.
    conn = get_conn()
    try:
        fresh = lessons.get_lesson(conn, lesson["id"])
        summary = attempts.lesson_attempt_summary(conn, fresh["id"])
    finally:
        conn.close()
    assert set(summary["latest_by_question"]) == {"q_recold001"}

    # Canonical serialization places it after `label` and round-trips.
    text = (lesson_dir / lessons.MANIFEST_NAME).read_text(encoding="utf-8")
    assert list(json.loads(text)["questions"][0]) == [
        "id", "page", "kind", "label", "replaces",
    ]
    bundle_schema.write_manifest(
        lesson_dir / lessons.MANIFEST_NAME, json.loads(text)
    )
    assert (lesson_dir / lessons.MANIFEST_NAME).read_text(encoding="utf-8") == text


def test_manifests_without_replaces_read_exactly_as_before(client):
    _lesson, lesson_dir, manifest = _lesson_with_record("Replaces Absent Fixture")
    manifest["questions"] = [
        {"id": "q_recplain1", "page": manifest["pages"][0]["id"]},
    ]
    read = _read(lesson_dir, manifest)
    assert read.outcome == "ok" and read.findings == []
    assert read.questions == [{
        "id": "q_recplain1", "page": manifest["pages"][0]["id"],
        "kind": bundle_schema.DEFAULT_QUESTION_KIND, "label": None,
        "replaces": None,
    }]
    assert "replaces" not in json.loads(
        (lesson_dir / lessons.MANIFEST_NAME).read_text(encoding="utf-8")
    )["questions"][0]


def test_a_replaces_that_is_not_a_retired_predecessor_is_dropped(client):
    _lesson, lesson_dir, manifest = _lesson_with_record("Replaces Refusal Fixture")
    page_id = manifest["pages"][0]["id"]

    for label, questions in {
        "not an id": [{"id": "q_recbad001", "page": page_id, "replaces": "nope"}],
        "not a string": [{"id": "q_recbad001", "page": page_id, "replaces": 7}],
        "itself": [{"id": "q_recbad001", "page": page_id,
                    "replaces": "q_recbad001"}],
        "a still-declared question": [
            {"id": "q_recbad001", "page": page_id, "replaces": "q_recbad002"},
            {"id": "q_recbad002", "page": page_id},
        ],
        "a predecessor two successors claim": [
            {"id": "q_recbad001", "page": page_id, "replaces": "q_recgone01"},
            {"id": "q_recbad002", "page": page_id, "replaces": "q_recgone01"},
        ],
    }.items():
        manifest["questions"] = questions
        read = _read(lesson_dir, manifest)
        # `invalid-value` is informational, exactly like a dropped label or
        # kind: the claim is refused, the question keeps its declaration, and
        # the bundle keeps rendering.
        assert read.outcome == "ok", label
        assert "invalid-value" in read.codes(), label
        assert [q["id"] for q in read.questions] == [
            q["id"] for q in questions
        ], label
        assert all(q["replaces"] is None for q in read.questions), label


# --- the panel and its rendering ---------------------------------------------


def test_the_retired_row_links_to_the_successor_that_replaces_it(client):
    lesson, _dir, _manifest = _lesson_with_record("Successor Link Fixture")
    panel = _panel(lesson)
    assert [q["question_id"] for q in panel["questions"]] == ["q_recnew001"]
    assert [q["question_id"] for q in panel["retired"]] == ["q_recold001"]
    assert panel["retired"][0]["successor"] == {
        "question_id": "q_recnew001", "label": "New wording",
    }
    assert panel["questions"][0]["successor"] is None
    assert panel["verdict_count"] == 1

    body = client.get(f"/learn?lesson={lesson['id']}").text
    assert 'id="rec-q-q_recold001"' in body
    assert 'href="#rec-q-q_recnew001"' in body
    # The anchor the link points at is a row this very page rendered.
    assert 'id="rec-q-q_recnew001"' in body


def test_a_dropped_successor_claim_leaves_the_retired_row_unlinked(client):
    lesson, lesson_dir, manifest = _lesson_with_record("Unlinked Retired Fixture")
    manifest["questions"][0]["replaces"] = "q_recold001"
    manifest["questions"].append({
        "id": "q_recrival1", "page": manifest["pages"][0]["id"],
        "replaces": "q_recold001",
    })
    bundle_schema.write_manifest(lesson_dir / lessons.MANIFEST_NAME, manifest)
    panel = _panel(lesson)
    assert panel["retired"][0]["question_id"] == "q_recold001"
    assert panel["retired"][0]["successor"] is None
    assert "href=\"#rec-q-" not in client.get(f"/learn?lesson={lesson['id']}").text


def test_a_still_named_but_unvalidated_question_never_takes_the_link(client):
    """A question the manifest still names but the reader could not validate is
    UNVALIDATED, not retired (§4.3: a predecessor is no longer declared). The
    reader can only compare a claim against what validated, so the panel — the
    layer that decides retirement — refuses the link."""
    lesson, lesson_dir, manifest = _lesson_with_record("Unvalidated Target Fixture")
    manifest["questions"] = [
        # still named, but dangling: dropped from the read model, not retired
        {"id": "q_recold001", "page": "pg_nosuchpage"},
        {"id": "q_recnew001", "page": manifest["pages"][0]["id"],
         "label": "New wording", "replaces": "q_recold001"},
    ]
    bundle_schema.write_manifest(lesson_dir / lessons.MANIFEST_NAME, manifest)
    panel = _panel(lesson)
    assert panel["retired"] == []
    unvalidated = [q for q in panel["questions"] if q["question_id"] == "q_recold001"]
    assert unvalidated and unvalidated[0]["unvalidated"] is True
    assert all(q["successor"] is None for q in panel["questions"])
    assert 'href="#rec-q-' not in client.get(f"/learn?lesson={lesson['id']}").text


def test_evidence_notes_render_in_full_instead_of_a_truncated_tooltip(client):
    lesson, _dir, _manifest = _lesson_with_record("Evidence Note Fixture")
    body = client.get(f"/learn?lesson={lesson['id']}").text
    assert LONG_NOTE in body
    # The old shape: the note inside a title="" attribute, cut at 200 chars
    # with Jinja's ellipsis. Neither may survive anywhere on the page.
    assert LONG_NOTE[:190] + "..." not in body
    assert 'title="weak · basis: attempts' not in body
    assert body.count(LONG_NOTE) == 2  # the verdict note and the chip note
    assert 'class="rec-chip rec-chip-fold rec-lv-weak"' in body


# --- the counts poll ---------------------------------------------------------


def test_record_counts_answers_the_poll_and_counts_unread_against_since(client):
    lesson, _dir, _manifest = _lesson_with_record("Record Counts Fixture")
    url = f"/learn/lessons/{lesson['id']}/record-counts"

    first = client.get(url).json()
    assert first["ok"] is True
    assert (first["attempts"], first["assessments"], first["verdicts"]) == (1, 2, 1)
    # The WHOLE counts line, so a focus session finished beside the panel does
    # not leave one stale cell behind.
    assert first["focus"] == "0m" and "focus_seconds" not in first
    # No baseline yet is nothing unread — not a whole history announced as new.
    assert first["unread"] == 0
    cursor = first["cursor"]
    assert cursor and client.get(url, params={"since": cursor}).json()["unread"] == 0

    conn = get_conn()
    try:
        fresh = lessons.get_lesson(conn, lesson["id"])
        summary = attempts.lesson_attempt_summary(conn, fresh["id"])
        attempt_id = summary["latest_by_question"]["q_recold001"]["attempt_id"]
        assessments.record_assessment(conn, fresh, {
            "kind": "review", "level": "correct", "attempt_id": attempt_id,
            "note": "A second reading of the same answer.",
            "idempotency_key": f"{fresh['slug']}-review-2",
        })
    finally:
        conn.close()

    after = client.get(url, params={"since": cursor}).json()
    assert after["unread"] == 1
    assert after["cursor"] > cursor
    # The standing fold, not the history: one verdict per answered question.
    assert after["verdicts"] == 1
    assert client.get(url, params={"since": after["cursor"]}).json()["unread"] == 0
    assert client.get(f"/learn/lessons/{lesson['id'] + 9999}/record-counts").status_code == 404


def test_the_unread_cursor_is_the_seq_authority_not_the_display_stamp(client,
                                                                     monkeypatch):
    """`created_at` is display metadata and two rows can share a microsecond;
    `seq` is the recency authority (assessments.py). A verdict that shares its
    stamp with an acknowledged one must still count as unread."""
    lesson, _dir, _manifest = _lesson_with_record("Record Cursor Fixture")
    url = f"/learn/lessons/{lesson['id']}/record-counts"
    frozen = "2026-08-05T00:00:00.000000+00:00"
    monkeypatch.setattr(assessments, "_utc_now_iso", lambda: frozen)

    conn = get_conn()
    try:
        fresh = lessons.get_lesson(conn, lesson["id"])
        attempt_id = attempts.lesson_attempt_summary(
            conn, fresh["id"]
        )["latest_by_question"]["q_recold001"]["attempt_id"]
        for n in (1, 2):
            assessments.record_assessment(conn, fresh, {
                "kind": "review", "level": "correct", "attempt_id": attempt_id,
                "note": f"Same-microsecond verdict {n}.",
                "idempotency_key": f"{fresh['slug']}-tie-{n}",
            })
        stamps = [row["created_at"] for row in conn.execute(
            "SELECT created_at FROM lesson_assessments WHERE lesson_id = ? "
            "AND kind = 'review'", (fresh["id"],))]
    finally:
        conn.close()
    assert stamps.count(frozen) == 2  # the collision the cursor must survive

    first = client.get(url).json()
    # Acknowledge the state as of the FIRST of the two identical stamps.
    conn = get_conn()
    try:
        seqs = sorted(row["id"] for row in conn.execute(
            "SELECT id FROM lesson_assessments WHERE lesson_id = ? "
            "AND kind = 'review'", (lesson["id"],)))
    finally:
        conn.close()
    assert client.get(
        url, params={"since": learn._record_cursor(seqs[-2])}
    ).json()["unread"] == 1
    # The assessment field leads the signal, so it is still exactly the newest
    # assessment rowid — the attempt watermark rides behind the separator.
    assert first["cursor"].startswith(learn._record_cursor(seqs[-1]) + "-")


def test_a_retraction_moves_the_cursor_so_a_removed_verdict_gets_refreshed(client):
    """A removal changes the panel as much as an arrival does. `unread` cannot
    carry it — there is no honest "1 new" for a row that just disappeared — so
    the cursor is the lesson's history watermark and advances anyway. A client
    holding an acknowledged baseline sees a cursor past it with nothing unread
    and re-fetches the body, instead of leaving the retracted verdict on screen
    under a header this same response already corrected to `0 verdicts`."""
    lesson, _dir, _manifest = _lesson_with_record("Record Retraction Fixture")
    url = f"/learn/lessons/{lesson['id']}/record-counts"
    seen = client.get(url).json()
    assert seen["verdicts"] == 1
    baseline = seen["cursor"]

    conn = get_conn()
    try:
        fresh = lessons.get_lesson(conn, lesson["id"])
        review_id = conn.execute(
            "SELECT assessment_id FROM lesson_assessments WHERE lesson_id = ? "
            "AND kind = 'review' ORDER BY id DESC LIMIT 1", (fresh["id"],)
        ).fetchone()[0]
        assessments.record_assessment(conn, fresh, {
            "kind": "retraction", "supersedes": review_id,
            "note": "That reading was of the wrong answer.",
            "idempotency_key": f"{fresh['slug']}-retract",
        })
    finally:
        conn.close()

    after = client.get(url, params={"since": baseline}).json()
    assert after["verdicts"] == 0        # the header the learner already sees
    assert after["unread"] == 0          # nothing arrived, so nothing to badge
    assert after["cursor"] > baseline    # but the record MOVED: refresh signal
    # And the acknowledged state settles: re-asking with the new cursor is quiet.
    settled = client.get(url, params={"since": after["cursor"]}).json()
    assert (settled["unread"], settled["cursor"]) == (0, after["cursor"])


def test_a_new_check_moves_the_cursor_so_the_rewritten_row_gets_refreshed(client):
    """The panel's rows are a join, so an attempt-only change is the same
    stale-body problem as a retraction: a Check recorded from the editor
    replaces the answer on screen and bumps the `attempts` count this same
    response updates, while touching no assessment row. The signal watermarks
    both tables, so it moves — and, being the trailing field, it moves without
    making the standing verdict look unread again."""
    lesson, lesson_dir, manifest = _lesson_with_record("Record Recheck Fixture")
    url = f"/learn/lessons/{lesson['id']}/record-counts"
    seen = client.get(url).json()
    baseline = seen["cursor"]
    assert seen["attempts"] == 1

    page = lesson_dir / "index.html"
    conn = get_conn()
    try:
        fresh = lessons.get_lesson(conn, lesson["id"])
        attempts.record_attempt(conn, fresh, {
            # The successor id: the fixture has already retired the old one,
            # and only a declared question accepts an attempt (§4.3/§6.4).
            "question_id": "q_recnew001",
            "page_id": manifest["pages"][0]["id"],
            "page_rev": "sha256:" + hashlib.sha256(page.read_bytes()).hexdigest(),
            "answer": "A second, better answer under the new wording",
            "idempotency_key": f"{fresh['slug']}-recheck",
        })
    finally:
        conn.close()

    after = client.get(url, params={"since": baseline}).json()
    assert after["attempts"] == 2         # the count the learner already sees
    assert after["unread"] == 0           # no verdict arrived: nothing to badge
    assert after["cursor"] > baseline     # but the rows MOVED: refresh signal
    # The assessment field is untouched, so the acknowledged verdict stays read
    # even against the pre-Check baseline.
    assert after["cursor"][:20] == baseline[:20]


def test_an_empty_record_baselines_so_the_first_verdict_still_announces(client):
    """The zero cursor is what "acknowledged an empty record" looks like: the
    client stores it, and the very first verdict of the lesson counts unread."""
    conn = get_conn()
    try:
        lesson_id = lessons.create_lesson(conn, "Empty Record Baseline Fixture")
        lesson = lessons.get_lesson(conn, lesson_id)
    finally:
        conn.close()
    url = f"/learn/lessons/{lesson_id}/record-counts"
    empty = client.get(url).json()
    assert (empty["verdicts"], empty["unread"], empty["cursor"]) == (0, 0, "")

    page_id = json.loads(
        (Path(lessons.LESSONS_DIR) / lesson["slug"] / lessons.MANIFEST_NAME)
        .read_text(encoding="utf-8")
    )["pages"][0]["id"]
    lesson_dir = Path(lessons.LESSONS_DIR) / lesson["slug"]
    manifest = json.loads(
        (lesson_dir / lessons.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    manifest["questions"] = [{"id": "q_recfirst1", "page": page_id}]
    bundle_schema.write_manifest(lesson_dir / lessons.MANIFEST_NAME, manifest)
    page = lesson_dir / "index.html"
    page.write_text("<html>First verdict fixture</html>", encoding="utf-8")
    conn = get_conn()
    try:
        attempt = attempts.record_attempt(conn, lesson, {
            "question_id": "q_recfirst1", "page_id": page_id,
            "page_rev": "sha256:" + hashlib.sha256(page.read_bytes()).hexdigest(),
            "answer": "First answer", "idempotency_key": f"{lesson['slug']}-a",
        })
        assessments.record_assessment(conn, lesson, {
            "kind": "review", "level": "correct",
            "attempt_id": attempt["attempt_id"], "note": "The first verdict.",
            "idempotency_key": f"{lesson['slug']}-r",
        })
    finally:
        conn.close()
    assert client.get(url, params={"since": "0"}).json()["unread"] == 1


def test_the_record_panel_carries_the_poll_target_and_the_unread_badge(client):
    lesson, _dir, _manifest = _lesson_with_record("Record Poll Wiring Fixture")
    body = client.get(f"/learn?lesson={lesson['id']}").text
    assert f'data-record-counts-url="/learn/lessons/{lesson["id"]}/record-counts"' in body
    assert f'data-record-key="{lesson["id"]}"' in body
    assert 'data-record-count="verdicts"' in body
    # The rendered panel carries the cursor its own rows read to, so the badge
    # acknowledges the snapshot the learner was shown and not whatever the
    # poll last reported. It watermarks BOTH tables the rows are joined from,
    # not the newest review: evidence, retractions and Checks move these rows
    # too, and the assessment field leads so `unread` can slice it back out.
    conn = get_conn()
    try:
        seq, attempt_seq = (conn.execute(
            f"SELECT MAX(id) FROM {table} WHERE lesson_id = ?",
            (lesson["id"],)).fetchone()[0]
            for table in ("lesson_assessments", "lesson_attempts"))
    finally:
        conn.close()
    signal = f"{learn._record_cursor(seq)}-{learn._record_cursor(attempt_seq)}"
    assert signal.startswith(learn._record_cursor(seq) + "-")
    assert f'data-record-cursor="{signal}"' in body
    assert client.get(f"/learn/lessons/{lesson['id']}/record-counts").json()[
        "cursor"] == signal
    # The badge ships hidden and empty: unread is this browser's state, and the
    # server has no opinion about what the learner has already seen.
    assert '<button type="button" class="rec-unread" id="rec-unread" hidden' in body

    # The author-level display rule needs its own hidden selector or the empty
    # pill would show through `badge.hidden`.
    css = Path("app/static/style.css").read_text(encoding="utf-8")
    assert ".rec-unread[hidden] { display: none; }" in css

    # #132: the sheet's state is restored INLINE, after the panel and before
    # every deferred script — the runtime below reads `open` to decide whether
    # these rows count as this browser's first look, and a script that runs
    # later would answer for a panel the learner had already chosen to shut.
    restore = body.rsplit("</details>", 1)[-1]
    assert (
        'localStorage.getItem("al-record-open")' in restore
        and 'rec.open = stored === "1"' in restore
        and 'localStorage.setItem("al-record-open", rec.open ? "1" : "0")' in restore
        and 'new ResizeObserver(fit).observe(wrap)' in restore
        and 'rec.style.setProperty("--rec-max"' in restore
    ), "the record sheet restores its own state and caps its own height"

    source = Path("app/static/src/learn-bridge.ts").read_text(encoding="utf-8")
    emitted = Path("app/static/learn-bridge.js").read_text(encoding="utf-8")
    for token in ("al-record-seen:", "recordCountsUrl", "rec-unread"):
        assert token in source and token in emitted
    # Two refusals with no runtime harness in this suite, asserted at the
    # source the emitted module is generated from: the refresh must prove the
    # fetched page still answers for THIS lesson before swapping its body in,
    # and a browser with storage blocked keeps the acknowledged cursor in
    # memory rather than reading first sight on every poll.
    for token in ('data-record-key") !== recordKey', "sessionSeen",
                  'data-record-cursor',
                  # Seeded through `writeSeen`, so the baseline OUTLIVES a
                  # visit that ends without a click: re-seeding from the next
                  # render would adopt anything written in between as seen.
                  # #132: and only a panel that was OPEN counts as a look — a
                  # closed sheet baselines at the zero cursor instead, so what
                  # is already recorded still gets announced.
                  '(recordPanel.open && recordPanel.dataset["recordCursor"])',
                  "readSeen() !== asked",
                  # A cursor past the baseline with nothing unread is a REMOVAL
                  # (or an evidence/summary write): refresh the rows quietly,
                  # under the one guard that keeps two body swaps apart.
                  "unread === 0 && latestCursor > asked",
                  "guardedRefresh",
                  # …and the quiet refresh acknowledges only as far as the
                  # zero-unread answer read, never as far as a body that may
                  # have picked up a verdict written after it: that verdict
                  # still gets its badge on the next tick.
                  "shown < latestCursor ? shown : latestCursor"):
        assert token in source and token in emitted
    # Tier 1 adds no bridge operation: reading the record INTO the lesson page
    # is tier 2, and the ABI is frozen additive-only by design.
    assert "record.get" not in source and "record.read" not in source


# --- the live-process guard --------------------------------------------------


def test_learn_html_renders_under_a_pre_133_router_context(client, monkeypatch):
    """Jinja re-reads templates per render, so the LIVE process renders this
    working-tree template with its own (older) context until the restart. Every
    field #133 adds is therefore `is defined`-guarded, and this proves it by
    rendering the same page from a context with those fields removed."""
    lesson, _dir, _manifest = _lesson_with_record("Template Guard Fixture")
    captured: dict = {}
    original = learn.templates.TemplateResponse

    def capture(request, name, context, *args, **kwargs):
        captured.update(context)
        return original(request, name, context, *args, **kwargs)

    monkeypatch.setattr(learn.templates, "TemplateResponse", capture)
    assert client.get(f"/learn?lesson={lesson['id']}").status_code == 200
    assert captured["selected"]["record"]["verdict_count"] == 1

    selected = dict(captured["selected"])
    selected.pop("record_counts_url")
    record = dict(selected["record"])
    record.pop("verdict_count")
    record.pop("cursor")
    record["questions"] = [
        {k: v for k, v in q.items() if k != "successor"} for q in record["questions"]
    ]
    record["retired"] = [
        {k: v for k, v in q.items() if k != "successor"} for q in record["retired"]
    ]
    selected["record"] = record
    old = templates.env.get_template("learn.html").render(
        {**captured, "selected": selected}
    )

    assert "Record" in old and "q_recold001" in old
    # The additions omit themselves rather than half-drawing: no poll target
    # (the route does not exist on that process), no verdicts count, no link.
    assert "data-record-counts-url" not in old
    assert 'data-record-count="verdicts"' not in old
    assert "data-record-cursor" not in old
    assert 'href="#rec-q-' not in old
    # What the old context DOES carry still renders, including the full note.
    assert LONG_NOTE in old
