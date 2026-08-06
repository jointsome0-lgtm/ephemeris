"""Read-back into the lesson page: `welcome.record` (#133 tier 2).

The bridge was write-only: a learner who reloaded a half-finished lesson met
blank controls, and the verdicts written about their answers lived only in the
Record panel under the iframe. Tier 2 hands the loaded document a snapshot of
what is already recorded for the questions it declares, inside the one welcome
per document — a field, never a new operation.

Three things this covers. What the snapshot says (declared-here questions only,
with the answer, its timestamp, its stale flag and the standing verdict). That
it is the SAME reading the Record panel on that page rendered, never a second
one. And that it is additive: a page that ignores `record` — every bundle
shipped before this — completes the identical handshake and records the
identical attempt, which is asserted by driving the emitted runtime, not by
reading the diff.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.db import get_conn
from app.routers import learn
from app.services import assessments, attempts, bundle_schema, lessons
from app.templating import templates

ROOT = Path(__file__).resolve().parent.parent

VERDICT_NOTE = (
    "The answer reads the unbuffered send as fire-and-forget, which is the "
    "one thing it is not: the send blocks until a receiver is ready."
)
REPLY_NOTE = "Yes — 'ready' here means a receiver is already at the channel."


def _page_rev(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _lesson_for_readback(title: str) -> dict:
    """A two-page lesson covering every case the snapshot has to decide.

    On page one: an answered+reviewed question, an ask-the-tutor control that
    was used and replied to, a declared question nobody touched, and a stale
    answer. On page two: an answered question that must not travel to page one.
    Plus an id that was answered and then retired from the manifest.
    """
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
    first = manifest["pages"][0]["id"]
    manifest["pages"].append({"id": "pg_rbsecond1", "path": "related/01-next.html",
                              "title": "Second stage"})
    manifest["questions"] = [
        {"id": "q_rbanswer1", "page": first, "kind": "prediction",
         "label": "The unbuffered send"},
        {"id": "q_rbask0001", "page": first, "kind": "ask_tutor",
         "label": "Ask about the unbuffered send"},
        {"id": "q_rbuntouch", "page": first, "kind": "prediction",
         "label": "Nobody answered this"},
        {"id": "q_rbstale01", "page": first, "kind": "prediction",
         "label": "Answered against an older revision"},
        {"id": "q_rbgone001", "page": first, "kind": "prediction",
         "label": "Retired after it was answered"},
        {"id": "q_rbother01", "page": "pg_rbsecond1", "kind": "prediction",
         "label": "Declared on the other page"},
    ]
    bundle_schema.write_manifest(lesson_dir / lessons.MANIFEST_NAME, manifest)
    index = lesson_dir / "index.html"
    index.write_text("<html>Read-back fixture, page one</html>", encoding="utf-8")
    second = lesson_dir / "related" / "01-next.html"
    second.write_text("<html>Read-back fixture, page two</html>", encoding="utf-8")

    conn = get_conn()
    try:
        answered = attempts.record_attempt(conn, lesson, {
            "question_id": "q_rbanswer1", "page_id": first,
            "page_rev": _page_rev(index),
            "answer": "It keeps running while nobody receives.",
            "idempotency_key": f"{lesson['slug']}-answered",
        })
        asked = attempts.record_attempt(conn, lesson, {
            "question_id": "q_rbask0001", "page_id": first,
            "page_rev": _page_rev(index),
            "answer": "What does 'ready' mean for the receiver here?",
            "idempotency_key": f"{lesson['slug']}-asked",
        })
        # §6.4: a revision that is not the page's current one records stale.
        attempts.record_attempt(conn, lesson, {
            "question_id": "q_rbstale01", "page_id": first,
            "page_rev": "sha256:" + "0" * 64,
            "answer": "Written against the page as it was yesterday.",
            "idempotency_key": f"{lesson['slug']}-stale",
        })
        attempts.record_attempt(conn, lesson, {
            "question_id": "q_rbgone001", "page_id": first,
            "page_rev": _page_rev(index),
            "answer": "An answer to a question that no longer exists.",
            "idempotency_key": f"{lesson['slug']}-gone",
        })
        attempts.record_attempt(conn, lesson, {
            "question_id": "q_rbother01", "page_id": "pg_rbsecond1",
            "page_rev": _page_rev(second),
            "answer": "This belongs to the second page.",
            "idempotency_key": f"{lesson['slug']}-other",
        })
        assessments.record_assessment(conn, lesson, {
            "kind": "review", "level": "partial",
            "attempt_id": answered["attempt_id"], "note": VERDICT_NOTE,
            "idempotency_key": f"{lesson['slug']}-review",
        })
        assessments.record_assessment(conn, lesson, {
            "kind": "review", "level": "unclear",
            "attempt_id": asked["attempt_id"], "note": REPLY_NOTE,
            "idempotency_key": f"{lesson['slug']}-reply",
        })
    finally:
        conn.close()

    # The retirement happens after the answer, so its history stands with no
    # declaration behind it — the panel keeps it, the snapshot must not.
    manifest["questions"] = [
        q for q in manifest["questions"] if q["id"] != "q_rbgone001"
    ]
    bundle_schema.write_manifest(lesson_dir / lessons.MANIFEST_NAME, manifest)
    return lesson


def _snapshot(client, lesson: dict, entry: str | None = None) -> dict:
    """The snapshot the /learn render put on the frame, parsed back."""
    params = {"lesson": lesson["id"]}
    if entry is not None:
        params["entry"] = entry
    body = client.get("/learn", params=params).text
    frame = body.split('id="lesson-preview-frame"', 1)[1].split("</iframe>", 1)[0]
    raw = frame.split('data-record="', 1)[1].split('"', 1)[0]
    # The attribute is ordinary escaped markup; the browser hands the runtime
    # the original JSON text back. Unescaping here is what the parser does.
    for encoded, plain in (("&#34;", '"'), ("&quot;", '"'), ("&#39;", "'"),
                           ("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&")):
        raw = raw.replace(encoded, plain)
    return json.loads(raw)


def _by_id(snapshot: dict) -> dict:
    return {row["question_id"]: row for row in snapshot["questions"]}


# --- what the snapshot says --------------------------------------------------


def test_the_snapshot_carries_the_answer_its_time_and_its_verdict(client):
    lesson = _lesson_for_readback("Readback Snapshot Fixture")
    rows = _by_id(_snapshot(client, lesson))

    answered = rows["q_rbanswer1"]
    assert answered["answer"] == "It keeps running while nobody receives."
    assert answered["answer_truncated"] is False
    assert answered["stale"] is False
    # The record's own timestamp, not a display string: the page formats it.
    conn = get_conn()
    try:
        created = conn.execute(
            "SELECT created_at FROM lesson_attempts WHERE question_id = ? "
            "AND lesson_id = ?", ("q_rbanswer1", lesson["id"])).fetchone()[0]
    finally:
        conn.close()
    assert answered["answered_at"] == created
    assert answered["verdict"] == {
        "level": "partial", "note": VERDICT_NOTE,
        "recorded_at": answered["verdict"]["recorded_at"],
    }
    assert answered["verdict"]["recorded_at"]

    # An ask-the-tutor control travels the same shape: its "verdict" is the
    # reply, which is what the page renders as the answer to what was asked.
    asked = rows["q_rbask0001"]
    assert asked["answer"] == "What does 'ready' mean for the receiver here?"
    assert asked["verdict"]["note"] == REPLY_NOTE

    # §6.4 staleness is the flag stored at record time, carried through.
    assert rows["q_rbstale01"]["stale"] is True
    assert rows["q_rbstale01"]["verdict"] is None


def test_only_the_questions_this_page_declares_travel_into_it(client):
    """Not the whole lesson, not a retired id, not an unanswered one.

    Each exclusion is a different rule: the second page's question is declared
    elsewhere, the retired one is declared nowhere, and the untouched one has
    nothing recorded to restore. A snapshot that simply handed over the panel's
    rows would carry all three.
    """
    lesson = _lesson_for_readback("Readback Scope Fixture")
    ids = set(_by_id(_snapshot(client, lesson)))
    assert ids == {"q_rbanswer1", "q_rbask0001", "q_rbstale01"}

    # The other page gets its own — and only its own.
    other = set(_by_id(_snapshot(client, lesson, entry="related/01-next.html")))
    assert other == {"q_rbother01"}

    # The Record panel still shows every one of them: the panel is the whole
    # lesson's record, the snapshot is one page's read-back.
    body = client.get(f"/learn?lesson={lesson['id']}").text
    for question_id in ("q_rbanswer1", "q_rbother01", "q_rbgone001"):
        assert f'id="rec-q-{question_id}"' in body


def test_the_snapshot_is_the_panel_reading_never_a_second_one(client, monkeypatch):
    """One read of the record feeds both surfaces.

    A snapshot taken from its own read could show the lesson page a verdict the
    panel beneath it does not render — and tier 1's unread cursor watermarks
    exactly the rows that WERE rendered, so a read-back that outran them would
    mark a verdict seen that the learner was never shown. The proof is that the
    render reads the record-panel state exactly once.
    """
    lesson = _lesson_for_readback("Readback Single Reading Fixture")
    calls = {"n": 0}
    original = learn._record_panel_db_state

    def counted(conn, lesson_id):
        calls["n"] += 1
        return original(conn, lesson_id)

    monkeypatch.setattr(learn, "_record_panel_db_state", counted)
    assert client.get(f"/learn?lesson={lesson['id']}").status_code == 200
    assert calls["n"] == 1, "the record is read once per /learn render"

    # …and the values are literally projected from those rows.
    conn = get_conn()
    try:
        fresh = lessons.get_lesson(conn, lesson["id"])
        fresh, read = lessons.with_bundle_info_read(fresh)
        panel = learn._record_panel(conn, fresh, manifest_read=read)
    finally:
        conn.close()
    declared_here = set(fresh["file"]["bridge_page"]["questions"])
    projected = learn._record_snapshot(panel, declared_here)
    rows = {row["question_id"]: row for row in panel["questions"]}
    for entry in projected["questions"]:
        row = rows[entry["question_id"]]
        assert entry["answer"] == row["attempt"]["answer"]
        assert entry["answered_at"] == row["attempt"]["created_at"]
        assert entry["stale"] == row["attempt"]["stale"]
        assert (entry["verdict"] is None) == (row["review"] is None)
        if row["review"] is not None:
            assert entry["verdict"]["note"] == row["review"]["note"]


def test_a_truncated_answer_says_so_instead_of_travelling_whole(client):
    """The boundary carries the panel's excerpt, and marks it as one.

    Nothing crosses into the iframe that the page around it does not already
    show. A page told the text is complete when it is not would resubmit a
    fragment over the learner's full answer on the next Check.
    """
    lesson = _lesson_for_readback("Readback Truncation Fixture")
    lesson_dir = Path(lessons.LESSONS_DIR) / lesson["slug"]
    long_answer = "The receiver rendezvous matters because " + "x" * 2000
    conn = get_conn()
    try:
        attempts.record_attempt(conn, lesson, {
            "question_id": "q_rbuntouch",
            "page_id": json.loads(
                (lesson_dir / lessons.MANIFEST_NAME).read_text(encoding="utf-8")
            )["pages"][0]["id"],
            "page_rev": _page_rev(lesson_dir / "index.html"),
            "answer": long_answer,
            "idempotency_key": f"{lesson['slug']}-long",
        })
    finally:
        conn.close()
    row = _by_id(_snapshot(client, lesson))["q_rbuntouch"]
    assert row["answer_truncated"] is True
    assert row["answer"] == long_answer[:attempts.PANEL_ANSWER_CHARS]
    assert len(row["answer"]) < len(long_answer)


def test_a_lesson_with_nothing_recorded_still_gets_an_empty_snapshot(client):
    """Empty is an answer; absent is a different one.

    `{"questions": []}` says this backend does read-back and has nothing to
    restore. No attribute at all says the backend predates read-back. A page
    cannot tell "nothing recorded" from "no support" if the two look alike.
    """
    conn = get_conn()
    try:
        lesson_id = lessons.create_lesson(conn, "Readback Empty Fixture")
        lesson = lessons.get_lesson(conn, lesson_id)
    finally:
        conn.close()
    assert _snapshot(client, lesson) == {"questions": []}


def test_the_snapshot_survives_the_attribute_intact(client):
    """Learner and tutor text is inserted as escaped markup, not as JSON.

    A quote in an answer would end the attribute if the value were emitted as
    trusted JSON; a `<` would open a tag. Both round-trip here, which is the
    same path the browser walks before the runtime parses the value.
    """
    lesson = _lesson_for_readback("Readback Escaping Fixture")
    hostile = '</iframe><script>"boom"</script> & <b>bold</b>'
    lesson_dir = Path(lessons.LESSONS_DIR) / lesson["slug"]
    conn = get_conn()
    try:
        attempts.record_attempt(conn, lesson, {
            "question_id": "q_rbuntouch",
            "page_id": json.loads(
                (lesson_dir / lessons.MANIFEST_NAME).read_text(encoding="utf-8")
            )["pages"][0]["id"],
            "page_rev": _page_rev(lesson_dir / "index.html"),
            "answer": hostile,
            "idempotency_key": f"{lesson['slug']}-hostile",
        })
    finally:
        conn.close()
    body = client.get(f"/learn?lesson={lesson['id']}").text
    # The raw text never reaches the document: the attribute holds entities.
    assert "</iframe><script>" not in body
    assert _by_id(_snapshot(client, lesson))["q_rbuntouch"]["answer"] == hostile


# --- the emitted runtime: additivity, driven rather than described -----------


_HARNESS_SETUP = """\
/* A minimal fake DOM: enough of `document`, `window` and the preview frame for
 * the real emitted runtime to initialise, arm and answer a handshake. */
const config = JSON.parse(process.env["EPHEMERIS_BRIDGE_HARNESS"]);

export const posted = [];
export const fetchCalls = [];
const messageListeners = [];

export const child = {
  postMessage(message, targetOrigin, transfer) {
    posted.push({ message, targetOrigin, ports: transfer || [] });
  },
};

const frame = {
  dataset: {
    metaUrl: "/learn/lessons/1/preview-meta",
    attemptsUrl: "/learn/lessons/1/attempts",
    src: "/learn/lessons/1/preview",
    version: "v1",
    loaded: "1",
  },
  attrs: { src: "/learn/lessons/1/preview?v=v1", sandbox: "allow-scripts" },
  get src() { return this.attrs["src"]; },
  set src(value) { this.attrs["src"] = value; },
  getAttribute(name) { return this.attrs[name] ?? null; },
  setAttribute(name, value) { this.attrs[name] = value; },
  addEventListener() {},
  contentWindow: child,
};
if (config.record !== null) frame.dataset["record"] = config.record;

globalThis.document = {
  /* Hidden: the reload poll's own tick is not what this harness measures. */
  hidden: true,
  getElementById(id) { return id === "lesson-preview-frame" ? frame : null; },
  addEventListener() {},
};
globalThis.window = {
  location: { href: "http://testserver/learn?lesson=1" },
  addEventListener(type, cb) { if (type === "message") messageListeners.push(cb); },
};

const meta = {
  version: "v1",
  exists: true,
  bridge: true,
  preview_url: "/learn/lessons/1/preview",
  sandbox: "allow-scripts",
  bridge_page: {
    lesson_uid: "les_harness",
    page_id: "pg_harness01",
    page_rev: "sha256:" + "a".repeat(64),
    questions: ["q_harness01"],
    blocks: [],
  },
};

globalThis.fetch = async (url, init) => {
  const method = (init && init.method) || "GET";
  fetchCalls.push({ url: String(url), method, body: init && init.body });
  if (method === "POST") {
    return { ok: true, json: async () => ({
      ok: true, result: "recorded", attempt_id: "at_harness",
      stale: false, attempt_number: 1, projection: "written",
    }) };
  }
  return { ok: true, json: async () => meta };
};

/* The child's documented `ready`, posted the documented way: once to wake the
 * late-initialised runtime into binding, once as the retry it then answers. */
export const announce = async () => {
  const ready = {
    ephemeris: "lesson-bridge", type: "ready", abi: [1], want: config.want,
  };
  for (const cb of messageListeners) cb({ source: child, data: ready });
  await new Promise((resolve) => setTimeout(resolve, 30));
};

export { config };
"""

_HARNESS_RUN = """\
/* Static imports, in order: the fake DOM exists before the runtime evaluates. */
import { posted, fetchCalls, announce, config } from "./setup.mjs";
import "./learn-bridge.mjs";

await announce();
await announce();

const welcome = posted.find((entry) => entry.message?.type === "welcome");
const result = {
  postedCount: posted.length,
  targetOrigin: welcome ? welcome.targetOrigin : null,
  ports: welcome ? welcome.ports.length : 0,
  keys: welcome ? Object.keys(welcome.message).sort() : null,
  message: welcome ? welcome.message : null,
  attempt: null,
  attemptBodies: [],
};

/* The old-page path: a document that reads only abi/lesson/capabilities off the
 * welcome and then records an answer over the transferred port. */
if (welcome && config.attempt) {
  const port = welcome.ports[0];
  result.attempt = await new Promise((resolve) => {
    port.onmessage = (event) => resolve(event.data);
    port.postMessage({
      op: "attempt", v: 1, request_id: "req-harness-1",
      question_id: config.attempt, answer: "an answer from a page that ignores record",
    });
    setTimeout(() => resolve(null), 5000);
  });
  result.attemptBodies = fetchCalls
    .filter((call) => call.method === "POST")
    .map((call) => JSON.parse(call.body));
}

process.stdout.write(JSON.stringify(result));
process.exit(0);
"""


def _drive_bridge(tmp_path: Path, *, want, record, attempt=None) -> dict:
    """Run the committed `learn-bridge.js` through one handshake under node."""
    if shutil.which("node") is None:  # pragma: no cover - CI always has node
        pytest.skip("node is required to drive the emitted bridge runtime")
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "setup.mjs").write_text(_HARNESS_SETUP, encoding="utf-8")
    (tmp_path / "run.mjs").write_text(_HARNESS_RUN, encoding="utf-8")
    (tmp_path / "learn-bridge.mjs").write_text(
        (ROOT / "app" / "static" / "learn-bridge.js").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", str(tmp_path / "run.mjs")],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
        env={**os.environ, "EPHEMERIS_BRIDGE_HARNESS": json.dumps(
            {"want": want, "record": record, "attempt": attempt})},
    )
    assert completed.returncode == 0, (
        "bridge harness exits clean  -- " + completed.stdout + completed.stderr
    )
    return json.loads(completed.stdout)


SNAPSHOT = json.dumps({"questions": [{
    "question_id": "q_harness01",
    "answer": "the recorded answer",
    "answer_truncated": False,
    "answered_at": "2026-08-05T09:00:00Z",
    "stale": False,
    "verdict": {"level": "partial", "note": "the standing verdict",
                "recorded_at": "2026-08-05T10:00:00Z"},
}]}, separators=(",", ":"))


def test_the_welcome_hands_the_snapshot_to_a_page_that_records_answers(tmp_path):
    result = _drive_bridge(tmp_path, want=["attempts"], record=SNAPSHOT)
    assert result["keys"] == ["abi", "capabilities", "ephemeris", "lesson", "record", "type"]
    assert result["message"]["capabilities"] == ["attempts"]
    assert result["message"]["record"] == json.loads(SNAPSHOT)
    # The membrane itself is untouched: one welcome, one port, and the payload
    # still goes to the specific contentWindow rather than a named origin.
    assert result["postedCount"] == 1 and result["ports"] == 1
    assert result["targetOrigin"] == "*"


def test_a_page_that_ignores_record_completes_the_handshake_unchanged(tmp_path):
    """The additivity proof, driven rather than described.

    Every bundle shipped before this reads `abi`, `lesson` and `capabilities`
    off the welcome and never looks for `record`. Such a page must see the same
    grant and record the same attempt with the snapshot present as without it —
    the parent-derived body included, since that is what actually lands.
    """
    with_record = _drive_bridge(
        tmp_path / "with", want=["attempts"], record=SNAPSHOT,
        attempt="q_harness01")
    without = _drive_bridge(
        tmp_path / "without", want=["attempts"], record=None,
        attempt="q_harness01")

    for result in (with_record, without):
        assert result["message"]["abi"] == 1
        assert result["message"]["lesson"] == {
            "lesson_uid": "les_harness", "page_id": "pg_harness01",
            "page_rev": "sha256:" + "a" * 64,
        }
        assert result["message"]["capabilities"] == ["attempts"]
        assert result["attempt"]["op"] == "attempt"
        assert result["attempt"]["result"] == "recorded"
        assert result["attempt"]["request_id"] == "req-harness-1"

    assert with_record["attempt"] == without["attempt"]
    assert with_record["attemptBodies"] == without["attemptBodies"]
    assert with_record["attemptBodies"] == [{
        "question_id": "q_harness01",
        "page_id": "pg_harness01",
        "page_rev": "sha256:" + "a" * 64,
        "answer": "an answer from a page that ignores record",
        "idempotency_key": "req-harness-1",
    }]
    # And the ONLY difference between the two welcomes is the added field.
    assert without["keys"] == ["abi", "capabilities", "ephemeris", "lesson", "type"]
    assert "record" not in without["message"]


def test_a_backend_without_the_snapshot_sends_the_pre_133_welcome(tmp_path):
    """The static can run ahead of the Python half (the live process renders
    working-tree templates until its restart). No attribute, no field — not an
    empty one, which a page could not tell from a truly empty record."""
    result = _drive_bridge(tmp_path, want=["attempts"], record=None)
    assert "record" not in result["message"]
    assert result["keys"] == ["abi", "capabilities", "ephemeris", "lesson", "type"]


def test_a_page_that_did_not_ask_for_attempts_is_told_nothing(tmp_path):
    """Read-back travels with the grant that would write the next answer.

    A display page records no answers, so it has none to restore and none of
    the learner's text crosses into it. One negotiation governs both
    directions instead of a second, silent one.
    """
    result = _drive_bridge(tmp_path, want=[], record=SNAPSHOT)
    assert result["message"]["capabilities"] == []
    assert "record" not in result["message"]


@pytest.mark.parametrize("case,broken", [
    ("garbage", "not json at all"),
    ("array", "[1,2,3]"),
    ("wrong-questions", '{"questions":7}'),
    ("empty-string", ""),
])
def test_a_broken_snapshot_degrades_to_no_read_back(tmp_path, case, broken):
    """It must not throw during module init: the same block owns the reload
    poll and the handshake, so a parse error there would leave the whole
    preview surface dead rather than merely un-restored."""
    result = _drive_bridge(tmp_path / case, want=["attempts"], record=broken)
    assert result["message"] is not None, "the handshake still completes"
    assert "record" not in result["message"]


def test_the_source_and_the_committed_emit_carry_the_same_rules():
    """#42: the reviewed artifact is the served artifact."""
    source = (ROOT / "app" / "static" / "src" / "learn-bridge.ts").read_text(
        encoding="utf-8")
    emitted = (ROOT / "app" / "static" / "learn-bridge.js").read_text(
        encoding="utf-8")
    for token in ('frame.dataset["record"]',
                  'capabilities.includes("attempts")',
                  "welcome.record = recordSnapshot"):
        assert token in source and token in emitted, token
    # Still no read OPERATION: the port stays write-only by design (ABI §2.1).
    for absent in ("record.get", "record.read", '"record.'):
        assert absent not in source and absent not in emitted, absent


# --- the live-process guard --------------------------------------------------


def test_learn_html_renders_under_a_pre_tier2_router_context(client, monkeypatch):
    """Jinja re-reads templates per render, so the LIVE process renders this
    working-tree template with its own (older) context until the restart. The
    attribute is therefore `is defined`-guarded, and this proves it by
    rendering the same page from a context without the new key."""
    lesson = _lesson_for_readback("Readback Template Guard Fixture")
    captured: dict = {}
    original = learn.templates.TemplateResponse

    def capture(request, name, context, *args, **kwargs):
        captured.update(context)
        return original(request, name, context, *args, **kwargs)

    monkeypatch.setattr(learn.templates, "TemplateResponse", capture)
    assert client.get(f"/learn?lesson={lesson['id']}").status_code == 200
    assert "record_snapshot" in captured["selected"]

    selected = dict(captured["selected"])
    selected.pop("record_snapshot")
    old = templates.env.get_template("learn.html").render(
        {**captured, "selected": selected}
    )
    assert "data-record=" not in old
    # Everything else about the page — the frame, the panel — still renders.
    assert 'id="lesson-preview-frame"' in old
    assert 'id="rec-q-q_rbanswer1"' in old
