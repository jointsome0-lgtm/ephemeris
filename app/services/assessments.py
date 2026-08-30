"""Lesson assessment recording: what the tutor concluded, where
`lesson_attempts` records what the learner did.

One row plus one `lesson_assessment` ledger event per record, written in one
transaction with no filesystem work inside it. The caller supplies no identity
it does not own: `lesson_uid` comes from the DB row, `question_id` from the
referenced attempt row, `seq` is the rowid, `sitting_id` from the session's
write capability (none means the owner/manual path, admitted, no sitting).
Not bridge-gated, so tutor memory also works on `legacy-display` bundles.

Rows are append-only and `seq` is the sole recency authority; a later row
corrects an earlier one by naming it in `supersedes` (or by a `retraction`).
`assessments.jsonl` at the bundle root is the folded active state, rewritten
after the transaction commits and never inside it; a projection failure never
fails the durable write (docs/lesson-assessments-api.md).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat as stat_module
import threading
from datetime import datetime, timezone
from uuid import uuid4

from ..db import DATA_DIR, append_event
from . import bundle_schema, lessons, projection

KINDS = ("review", "evidence", "summary", "retraction")
MODES = ("tutoring", "exam")
REVIEW_LEVELS = ("correct", "partial", "incorrect", "unclear")
# `seen` = covered, no judgment yet; the rest are mutually exclusive judgments.
EVIDENCE_LEVELS = ("seen", "weak", "developing", "passed")
LEVELS_BY_KIND = {"review": REVIEW_LEVELS, "evidence": EVIDENCE_LEVELS}
# `live` is a non-replayable tutor observation, admissible for any level.
BASES = ("attempts", "artifacts", "runs", "live", "mixed")

MAX_NOTE_BYTES = 8 * 1024        # D-S1-3/S-M4: notes diagnose BY REFERENCE
MAX_NEXT_ACTION_BYTES = 512      # S-L1: one machine-readable next step
MAX_CONCEPTS = 8                 # D-S1-2: 1–8 opaque refs per evidence row

PROJECTION_PENDING = "pending"
PROJECTION_PROJECTED = "projected"

# The lock file lives outside the agent-writable bundle, separate from the
# attempts projection's, so assessment work never makes an attempt write `pending`.
PROJECTION_NAME = "assessments.jsonl"
PROJECTION_STATE_DIR = DATA_DIR / "assessment-projections"
META_KIND = "assessments_meta"
META_VERSION = 1

# The session write capability. The registry that answers for the token lives
# in `app/terminal.py` and dies with the session; no header is the owner path.
CAPABILITY_HEADER = "X-Ephemeris-Assess-Token"

# Fields outside this set are a 400 (strict, unlike the attempt endpoint): a
# tutor typo such as "conepts" must fail loudly. `question_id` and `sitting_id`
# are never client-supplied.
_FIELDS = frozenset({
    "kind", "mode", "level", "basis", "attempt_id", "concepts",
    "note", "next_action", "supersedes", "idempotency_key",
})


class AssessmentError(Exception):
    """A refused write: `code` per docs/lesson-assessments-api.md, `status` the HTTP status."""

    def __init__(self, code: str, status: int, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.status = status
        self.detail = detail


def _utc_now_iso() -> str:
    """Microsecond UTC, display only: `seq` (the rowid) is the recency authority."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _utf8_len(value: str) -> int | None:
    """UTF-8 byte length, or None for lone surrogates (JSON \\uD800 escapes)."""
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _opt(payload: dict, name: str) -> object | None:
    """An explicit JSON `null` reads as absent; a present value is never coerced."""
    value = payload.get(name)
    return None if value is None else value


def _text(
    value: object, code: str, oversize_code: str, name: str, limit: int
) -> str:
    """Non-blank UTF-8 text within its byte bound; whitespace-only is refused."""
    if not isinstance(value, str) or not value.strip():
        raise AssessmentError(code, 400, f"{name} must be non-blank text")
    size = _utf8_len(value)
    if size is None:
        raise AssessmentError(code, 400, f"{name} is not valid UTF-8 text")
    if size > limit:
        raise AssessmentError(
            oversize_code, 400, f"{name} exceeds {limit} UTF-8 bytes"
        )
    return value


def _uuid_ref(value: object, code: str, name: str) -> str:
    if not isinstance(value, str) or bundle_schema.UUID_RE.match(value) is None:
        raise AssessmentError(code, 400, f"{name} must be a UUID")
    return value


def _only_for(kind: str, allowed: tuple[str, ...], code: str, name: str) -> None:
    if kind not in allowed:
        raise AssessmentError(code, 400, f"{name} is not valid for kind {kind!r}")


def _clean_concepts(value: object) -> list[str]:
    """§4.5 opaque refs, deduplicated so a repeated tag cannot inflate the fold."""
    if not isinstance(value, list) or not value:
        raise AssessmentError(
            "invalid-concepts", 400, "concepts must be a non-empty list of refs"
        )
    if len(value) > MAX_CONCEPTS:
        raise AssessmentError(
            "invalid-concepts", 400, f"at most {MAX_CONCEPTS} concepts",
        )
    cleaned: list[str] = []
    for concept in value:
        if not bundle_schema.valid_opaque_ref(concept) or _utf8_len(concept) is None:
            raise AssessmentError(
                "invalid-concepts", 400,
                "each concept is 1-200 characters of UTF-8 text, no control characters",
            )
        if concept not in cleaned:
            cleaned.append(concept)
    return cleaned


def _clean_submission(payload: dict) -> dict:
    """Pure validation: nothing here touches the database."""
    unknown = sorted(k for k in payload if k not in _FIELDS)
    if unknown:
        raise AssessmentError(
            "unknown-field", 400, f"unknown fields: {', '.join(unknown)}"
        )

    kind = payload.get("kind")
    if kind not in KINDS:
        raise AssessmentError(
            "invalid-kind", 400, f"kind must be one of {', '.join(KINDS)}"
        )

    mode = _opt(payload, "mode")
    if mode is None:
        mode = "tutoring"
    elif mode not in MODES:
        raise AssessmentError(
            "invalid-mode", 400, f"mode must be one of {', '.join(MODES)}"
        )

    level = _opt(payload, "level")
    levels = LEVELS_BY_KIND.get(kind)
    if levels is None:
        if level is not None:
            raise AssessmentError(
                "invalid-level", 400, f"level is not valid for kind {kind!r}"
            )
    elif level not in levels:
        raise AssessmentError(
            "invalid-level", 400,
            f"kind {kind!r} requires level in {', '.join(levels)}",
        )

    basis = _opt(payload, "basis")
    if kind == "evidence":
        if basis not in BASES:
            raise AssessmentError(
                "invalid-basis", 400,
                f"evidence requires basis in {', '.join(BASES)}",
            )
    elif basis is not None:
        raise AssessmentError(
            "invalid-basis", 400, "basis is only valid on evidence"
        )

    attempt_id = _opt(payload, "attempt_id")
    if attempt_id is not None:
        _only_for(kind, ("review", "evidence"), "invalid-attempt-id", "attempt_id")
        attempt_id = _uuid_ref(attempt_id, "invalid-attempt-id", "attempt_id")
    elif kind == "review":
        raise AssessmentError(
            "invalid-attempt-id", 400, "a review must reference one attempt_id"
        )

    concepts = _opt(payload, "concepts")
    if concepts is not None:
        _only_for(
            kind, ("review", "evidence", "summary"), "invalid-concepts", "concepts"
        )
        concepts = _clean_concepts(concepts)
    elif kind == "evidence":
        raise AssessmentError(
            "invalid-concepts", 400, "evidence must name 1-8 concepts"
        )

    note = _text(
        payload.get("note"), "invalid-note", "note-too-large", "note", MAX_NOTE_BYTES
    )

    next_action = _opt(payload, "next_action")
    if next_action is not None:
        _only_for(kind, ("summary",), "invalid-next-action", "next_action")
        next_action = _text(
            next_action, "invalid-next-action", "next-action-too-large",
            "next_action", MAX_NEXT_ACTION_BYTES,
        )

    supersedes = _opt(payload, "supersedes")
    if supersedes is not None:
        supersedes = _uuid_ref(supersedes, "invalid-supersedes", "supersedes")
    elif kind == "retraction":
        raise AssessmentError(
            "invalid-supersedes", 400,
            "a retraction must name the assessment it retracts",
        )

    key = payload.get("idempotency_key")
    if not projection.valid_idempotency_key(key):
        raise AssessmentError(
            "invalid-idempotency-key", 400,
            f"idempotency_key must be 1-{projection.MAX_KEY_LEN} chars, "
            "no control characters",
        )

    return {
        "kind": kind,
        "mode": mode,
        "level": level,
        "basis": basis,
        "attempt_id": attempt_id,
        "concepts": concepts,
        "note": note,
        "next_action": next_action,
        "supersedes": supersedes,
        "idempotency_key": key,
    }


def _fingerprint(submission: dict) -> str:
    """SHA-256 over the canonical validated submission, idempotency key excluded
    (it is the lookup, not the content). A key replayed with a different judgment
    must be a visible conflict, never a silent coalesce."""
    canonical = {
        name: value for name, value in submission.items()
        if name != "idempotency_key" and value is not None
    }
    blob = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _row_response(
    conn: sqlite3.Connection, lesson: dict, row: sqlite3.Row | dict, result: str
) -> dict:
    """The response body for one outcome. Both callers hold the committed row here
    and the projection runs after the transaction, never inside it; the replay
    path shares the seam so a lost-response retry heals a pending projection."""
    return {
        "result": result,
        "assessment_id": row["assessment_id"],
        "seq": row["id"],
        "projection": _project(conn, lesson),
    }


def _replay_or_conflict(
    conn: sqlite3.Connection, lesson: dict, submission: dict, fingerprint: str
) -> dict | None:
    """A replay of the same submission returns the original row; the same key with
    a different submission is a 409 conflict, never coalesced. None = fresh key."""
    existing = conn.execute(
        "SELECT * FROM lesson_assessments WHERE lesson_id = ? AND idempotency_key = ?",
        (lesson["id"], submission["idempotency_key"]),
    ).fetchone()
    if existing is None:
        return None
    if existing["fingerprint"] == fingerprint:
        return _row_response(conn, lesson, existing, "duplicate")
    raise AssessmentError(
        "idempotency-conflict", 409,
        "idempotency_key was already used for a different assessment",
    )


def _capability_registry_lookup(token: str) -> dict | None:
    """Imported at the point of use: `app/terminal.py` imports this package's
    lessons module, so a module-level import would close a cycle."""
    from .. import terminal

    return terminal.resolve_assessment_capability(token)


def resolve_capability(lesson: dict, token: str | None) -> str | None:
    """The sitting a write came from, derived server-side. No token is the owner
    path: admitted, no sitting. A token must name the URL's lesson; one that does
    not resolve (never minted, or its session ended, including before a restart)
    is a visible 403, with deliberately no silent fallback to the tokenless path."""
    if token is None:
        return None
    capability = _capability_registry_lookup(token.strip())
    if capability is None:
        raise AssessmentError(
            "invalid-capability", 403,
            "the session write capability is unknown or no longer live",
        )
    if capability.get("lesson_id") != lesson["id"]:
        raise AssessmentError(
            "capability-lesson-mismatch", 409,
            "this capability belongs to a different lesson",
        )
    return capability.get("sitting_id")


def _require_summary_slot(
    conn: sqlite3.Connection, lesson: dict, submission: dict, sitting_id: str | None
) -> None:
    """One active summary per sitting: a second one is either a correction naming
    the first in `supersedes` or a mistake, and the refusal names the row to
    supersede. No sitting (the owner path) means no rule. Runs inside the write
    transaction, so the fold it reads is the one this insert is ordered against."""
    if submission["kind"] != "summary" or sitting_id is None:
        return
    row = conn.execute(
        "SELECT a.assessment_id FROM lesson_assessments a "
        "WHERE a.lesson_id = ? AND a.sitting_id = ? AND a.kind = 'summary' "
        "AND NOT EXISTS (SELECT 1 FROM lesson_assessments s "
        "                WHERE s.lesson_id = a.lesson_id "
        "                  AND s.supersedes = a.assessment_id) "
        "ORDER BY a.id DESC LIMIT 1",
        (lesson["id"], sitting_id),
    ).fetchone()
    if row is None or submission["supersedes"] == row["assessment_id"]:
        return
    raise AssessmentError(
        "summary-exists", 409,
        "this sitting already has an active summary; supersede "
        f"{row['assessment_id']} to replace it",
    )


def _resolve_attempt(
    conn: sqlite3.Connection, lesson: dict, attempt_id: str | None
) -> str | None:
    """Must be this lesson's attempt; `question_id` is copied from its row."""
    if attempt_id is None:
        return None
    row = conn.execute(
        "SELECT question_id FROM lesson_attempts "
        "WHERE lesson_id = ? AND attempt_id = ?",
        (lesson["id"], attempt_id),
    ).fetchone()
    if row is None:
        raise AssessmentError(
            "unknown-attempt", 422,
            "attempt_id is not a recorded attempt of this lesson",
        )
    return row["question_id"]


def _require_supersedes(
    conn: sqlite3.Connection, lesson: dict, supersedes: str | None
) -> None:
    """Same lesson only; append order then keeps correction chains acyclic."""
    if supersedes is None:
        return
    row = conn.execute(
        "SELECT 1 FROM lesson_assessments WHERE lesson_id = ? AND assessment_id = ?",
        (lesson["id"], supersedes),
    ).fetchone()
    if row is None:
        raise AssessmentError(
            "unknown-supersedes", 422,
            "supersedes is not an assessment of this lesson",
        )


def record_assessment(
    conn: sqlite3.Connection,
    lesson: dict,
    payload: dict,
    capability_token: str | None = None,
) -> dict:
    """Record one assessment for `lesson` (a lessons service view dict) and
    return {result, assessment_id, seq, projection}, `result` being `recorded`
    or `duplicate`. `capability_token` is None on the owner/manual path.
    Refusals raise AssessmentError with a code per docs/lesson-assessments-api.md."""
    submission = _clean_submission(payload)
    # A dead capability refuses even a replay: an agent retrying with a dead
    # capability must learn that, not receive a quiet duplicate.
    sitting_id = resolve_capability(lesson, capability_token)
    fingerprint = _fingerprint(submission)

    # Once per lesson per process, after validation: heals a projection left
    # pending by a restart even when this call turns out to be a refusal.
    _sweep_once(conn, lesson)

    def replay() -> dict | None:
        return _replay_or_conflict(conn, lesson, submission, fingerprint)

    def validate() -> str | None:
        if lesson.get("archived"):
            # The cheap early refusal on the caller's view of the lesson; the
            # binding one runs inside the write transaction (_record_locked).
            raise AssessmentError(
                "lesson-archived", 409,
                "this lesson is archived; restore it before recording",
            )
        if not lesson.get("uid"):  # unreachable post-v11 backfill; fail closed
            raise AssessmentError("lesson-unavailable", 409, "lesson has no uid")
        question_id = _resolve_attempt(conn, lesson, submission["attempt_id"])
        _require_supersedes(conn, lesson, submission["supersedes"])
        return question_id

    def write(question_id: str | None) -> dict:
        return _record_locked(
            conn, lesson, submission, fingerprint, question_id, sitting_id,
        )

    def project(row: dict) -> dict:
        return _row_response(conn, lesson, row, "recorded")

    # The replay lookup precedes the archive and reference refusals: the
    # original write is durable, so a retry must learn its assessment_id even
    # if the lesson was archived meanwhile. The refusals govern only new writes.
    return projection.record(
        conn, lesson, AssessmentError,
        replay=replay, validate=validate, write=write, project=project,
    )


def _record_locked(
    conn: sqlite3.Connection,
    lesson: dict,
    submission: dict,
    fingerprint: str,
    question_id: str | None,
    sitting_id: str | None,
) -> dict:
    """The write transaction of `record_assessment`: runs under the lesson lock,
    inside `BEGIN IMMEDIATE`, and returns the row identity the response echoes."""
    assessment_id = str(uuid4())
    # The event echoes `seq`, the row's rowid, so the row is inserted first and
    # the event uuid is minted here rather than returned by the ledger writer.
    event_uuid = str(uuid4())
    created_at = _utc_now_iso()
    concepts = submission["concepts"]
    # The writer lock is already held (BEGIN IMMEDIATE), so the archive re-check
    # below and this write have a definitive order.
    current = conn.execute(
        "SELECT archived_at FROM lessons WHERE id = ?",
        (lesson["id"],),
    ).fetchone()
    if current is None:
        raise AssessmentError(
            "unknown-lesson", 404, "the lesson no longer exists"
        )
    if current["archived_at"] is not None:
        # The binding refusal: the committed state at write time decides.
        raise AssessmentError(
            "lesson-archived", 409,
            "this lesson is archived; restore it before recording",
        )
    _require_summary_slot(conn, lesson, submission, sitting_id)
    cursor = conn.execute(
        "INSERT INTO lesson_assessments "
        "(assessment_id, event_uuid, lesson_id, lesson_uid, "
        " sitting_id, mode, idempotency_key, fingerprint, kind, "
        " level, basis, attempt_id, question_id, concepts_json, "
        " note, next_action, supersedes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            assessment_id, event_uuid, lesson["id"], lesson["uid"],
            sitting_id,
            submission["mode"], submission["idempotency_key"],
            fingerprint, submission["kind"], submission["level"],
            submission["basis"], submission["attempt_id"],
            question_id,
            json.dumps(concepts, ensure_ascii=False)
            if concepts is not None else None,
            submission["note"], submission["next_action"],
            submission["supersedes"], created_at,
        ),
    )
    seq = cursor.lastrowid
    append_event(conn, "lesson_assessment", {
        "lesson_uid": lesson["uid"],
        "lesson_id": lesson["id"],
        "slug": lesson["slug"],
        "assessment_id": assessment_id,
        "seq": seq,
        "kind": submission["kind"],
        "mode": submission["mode"],
        "sitting_id": sitting_id,
        "level": submission["level"],
        "basis": submission["basis"],
        "attempt_id": submission["attempt_id"],
        "question_id": question_id,
        "concepts": concepts,
        "note": submission["note"],
        "next_action": submission["next_action"],
        "supersedes": submission["supersedes"],
        "created_at": created_at,
    }, event_uuid=event_uuid)
    return {"assessment_id": assessment_id, "event_uuid": event_uuid, "id": seq}


# --- read model: the active-state fold ---------------------------------------


# The columns `fold_rows` decides on: a reader can walk these five and pay for
# `note` (8 KiB a row) only per winner it will show.
FOLD_KEYS_COLUMNS = "a.id, a.assessment_id, a.kind, a.attempt_id, a.concepts_json"


def _fold_keys(row: sqlite3.Row | dict) -> dict:
    concepts = row["concepts_json"]
    if isinstance(concepts, str):
        try:
            decoded = json.loads(concepts)
        except ValueError:
            decoded = None
        concepts = decoded if isinstance(decoded, list) else None
    return {
        "seq": row["id"],
        "assessment_id": row["assessment_id"],
        "kind": row["kind"],
        "attempt_id": row["attempt_id"],
        "concepts": concepts,
    }


def row_view(row: sqlite3.Row | dict) -> dict:
    """One authority row as the record shape its consumers read. A `concepts_json`
    that no longer parses reads as no concepts (a corrupt-database guard)."""
    return {
        **_fold_keys(row),
        "event_uuid": row["event_uuid"],
        "lesson_uid": row["lesson_uid"],
        "sitting_id": row["sitting_id"],
        "mode": row["mode"],
        "level": row["level"],
        "basis": row["basis"],
        "question_id": row["question_id"],
        "note": row["note"],
        "next_action": row["next_action"],
        "supersedes": row["supersedes"],
        "created_at": row["created_at"],
    }


# The correlated deactivation lookup needs `idx_assessments_lesson_supersedes`
# to stay bounded; without it the fold goes quadratic. verify asserts the plan.
_ACTIVE_SQL = (
    "SELECT {columns} FROM lesson_assessments a WHERE a.lesson_id = ? "
    "AND NOT EXISTS (SELECT 1 FROM lesson_assessments s "
    "                WHERE s.lesson_id = a.lesson_id "
    "                  AND s.supersedes = a.assessment_id) "
    "ORDER BY a.id"
)
# One definition of "active", two column lists.
ACTIVE_ROWS_SQL = _ACTIVE_SQL.format(columns="*")
ACTIVE_FOLD_KEYS_SQL = _ACTIVE_SQL.format(columns=FOLD_KEYS_COLUMNS)


def active_rows(conn: sqlite3.Connection, lesson_id: int) -> list[dict]:
    """Rows not targeted by any `supersedes`, ascending `seq`."""
    rows = conn.execute(ACTIVE_ROWS_SQL, (lesson_id,)).fetchall()
    return [row_view(row) for row in rows]


def fold_rows(rows: list[dict]) -> dict:
    """The current-state fold over active rows, ascending `seq`; pure, so the
    projection and the panel share one definition of "current"."""
    evidence_by_concept: dict[str, dict] = {}
    reviews_by_attempt: dict[str, dict] = {}
    summary: dict | None = None
    for row in rows:  # ascending seq: later rows win
        if row["kind"] == "evidence":
            for concept in row["concepts"] or ():
                evidence_by_concept[concept] = row
        elif row["kind"] == "review":
            reviews_by_attempt[row["attempt_id"]] = row
        elif row["kind"] == "summary":
            summary = row
    return {
        "evidence_by_concept": evidence_by_concept,
        "reviews_by_attempt": reviews_by_attempt,
        "summary": summary,
    }


def history_watermark(conn: sqlite3.Connection, lesson_id: int) -> int:
    """The newest rowid of the lesson's whole history, or 0. Rows are insert-only,
    so `MAX(id)` is an exact version of the active state, and unlike a fold-derived
    cursor it also moves when a retraction removes a row from view. Read it inside
    the caller's snapshot, so watermark and fold come from one committed version."""
    row = conn.execute(
        "SELECT MAX(id) FROM lesson_assessments WHERE lesson_id = ?",
        (lesson_id,),
    ).fetchone()
    return int(row[0] or 0)


def active_state(conn: sqlite3.Connection, lesson_id: int) -> dict:
    return fold_rows(active_rows(conn, lesson_id))


# Earlier readings behind each displayed review winner, via a bounded VALUES CTE
# of `(attempt_id, winner_id)` pairs. Reviews corrected by another review stay in
# the count; reviews struck by a retraction are excluded, in either write order.
# Rides `idx_assessments_lesson_supersedes` and `idx_assessments_lesson_kind`.
_EARLIER_REVIEW_COUNTS_SQL = (
    "WITH winners(attempt_id, winner_id) AS (VALUES {winners}) "
    "SELECT w.attempt_id AS attempt_id, COUNT(r.id) AS earlier_count "
    "FROM winners w "
    "LEFT JOIN lesson_assessments r "
    "  ON r.lesson_id = ? AND r.kind = 'review' "
    " AND r.attempt_id = w.attempt_id AND r.id < w.winner_id "
    " AND NOT EXISTS (SELECT 1 FROM lesson_assessments t "
    "                 WHERE t.lesson_id = r.lesson_id "
    "                   AND t.kind = 'retraction' "
    "                   AND t.supersedes = r.assessment_id) "
    "GROUP BY w.attempt_id"
)

# Statement-size bounds under SQLite's traditional 999-variable default; each
# statement also binds the lesson id, and a review winner costs two variables.
_HYDRATE_IDS_PER_QUERY = 500
_REVIEW_COUNTS_PER_QUERY = 250


def _hydrate(conn: sqlite3.Connection, lesson_id: int, state: dict) -> dict:
    """Re-read the winners whole; no write intervenes, so one state throughout."""
    wanted = {row["seq"] for row in state["evidence_by_concept"].values()}
    wanted |= {row["seq"] for row in state["reviews_by_attempt"].values()}
    if state["summary"] is not None:
        wanted.add(state["summary"]["seq"])
    if not wanted:
        return state
    wanted_ordered = sorted(wanted)
    full: dict[int, dict] = {}
    for start in range(0, len(wanted_ordered), _HYDRATE_IDS_PER_QUERY):
        chunk = wanted_ordered[start:start + _HYDRATE_IDS_PER_QUERY]
        marks = ",".join("?" for _ in chunk)
        full.update({
            row["id"]: row_view(row) for row in conn.execute(
                "SELECT * FROM lesson_assessments WHERE lesson_id = ? "
                f"AND id IN ({marks})", (lesson_id, *chunk)).fetchall()
        })
    return {
        "evidence_by_concept": {concept: full[row["seq"]] for concept, row
                                in state["evidence_by_concept"].items()},
        "reviews_by_attempt": {attempt: full[row["seq"]] for attempt, row
                               in state["reviews_by_attempt"].items()},
        "summary": full[state["summary"]["seq"]] if state["summary"] else None,
    }


def _earlier_review_counts(
    conn: sqlite3.Connection,
    lesson_id: int,
    reviews_by_attempt: dict,
) -> dict[str, int]:
    winners = sorted(
        (attempt_id, row["seq"])
        for attempt_id, row in reviews_by_attempt.items()
    )
    counts: dict[str, int] = {}
    for start in range(0, len(winners), _REVIEW_COUNTS_PER_QUERY):
        chunk = winners[start:start + _REVIEW_COUNTS_PER_QUERY]
        values = ",".join("(?, ?)" for _ in chunk)
        params = tuple(value for pair in chunk for value in pair)
        counts.update({
            row["attempt_id"]: row["earlier_count"]
            for row in conn.execute(
                _EARLIER_REVIEW_COUNTS_SQL.format(winners=values),
                (*params, lesson_id),
            ).fetchall()
        })
    return counts


def panel_state(
    conn: sqlite3.Connection,
    lesson_id: int,
    *,
    review_attempt_ids: set[str] | None = None,
) -> dict:
    """Everything the record panel folds out of the authority rows, read-only.
    `active_count` excludes retractions, which only say another record was wrong.
    This runs on every `/learn` render and the fold has no cardinality ceiling, so
    the fold decides on five narrow columns and only the winners (reviews limited
    to `review_attempt_ids`) are read whole. Owns a read snapshot when the caller
    does not already hold one, so fold, hydration and counts cannot mix versions."""
    own_snapshot = not conn.in_transaction
    if own_snapshot:
        conn.execute("BEGIN")
    try:
        keys = [_fold_keys(row) for row
                in conn.execute(ACTIVE_FOLD_KEYS_SQL, (lesson_id,)).fetchall()]
        state = fold_rows(keys)
        # Before the display filter narrows the fold: "a question attempt no
        # review answers" must see reviews on attempts the panel does not display.
        reviewed_attempt_ids = set(state["reviews_by_attempt"])
        if review_attempt_ids is not None:
            state["reviews_by_attempt"] = {
                attempt_id: row
                for attempt_id, row in state["reviews_by_attempt"].items()
                if attempt_id in review_attempt_ids
            }
        state = _hydrate(conn, lesson_id, state)
        # After the hydration, which rebuilds the dict from the fold's own keys.
        state["reviewed_attempt_ids"] = reviewed_attempt_ids
        state["active_count"] = sum(
            1 for row in keys if row["kind"] != "retraction"
        )
        state["earlier_review_counts"] = _earlier_review_counts(
            conn, lesson_id, state["reviews_by_attempt"]
        )
        # From the same snapshot as the fold: a reader polling for change must
        # notice a removal, not only a new standing record.
        state["watermark"] = history_watermark(conn, lesson_id)
        return state
    finally:
        if own_snapshot:
            conn.rollback()


# --- projection: `assessments.jsonl`, the active-state read model ------------
# The file is the next tutor's resume artifact: current state, not history (spec
# §6.5), so it is a plain full rewrite, linear in the active fold: a compaction,
# not a cap. Commit first, project after: the entry point refuses an
# in-transaction connection, and the committed state is re-read fresh under the
# per-lesson flock, so a slow writer cannot publish an older file last.

# Lessons already swept in this process. Membership is recorded before the
# sweep runs, so a failing sweep is not retried on every write.
_swept_lock = threading.Lock()
_swept: set[int] = set()

# What this process last published, per lesson uid: the watermark it rendered
# and the identity of the file it left (device, inode, size, mtime, ctime). An
# idempotent replay is outside the rate budget, so without this a client looping
# one duplicate key would drive unlimited full rewrites and fsyncs. The skip is
# verified: only a watermark this process published itself counts, and the file
# must still carry its full seal. Device and ctime are load-bearing: a same-size
# in-place edit can preserve inode and restore mtime, not the kernel change time.
_published_lock = threading.Lock()
_published: dict[str, tuple[int, int, int, int, int, int]] = {}


def _projection_unchanged(
    lesson: dict, stamp: tuple[int, int, int, int, int, int]
) -> bool:
    """Is the published file still exactly the one this process left?"""
    try:
        st = os.lstat(lessons._lesson_dir(lesson["slug"]) / PROJECTION_NAME)
    except OSError:
        return False
    return (
        stat_module.S_ISREG(st.st_mode)
        and st.st_nlink == 1
        and (
            st.st_dev,
            st.st_ino,
            st.st_size,
            st.st_mtime_ns,
            st.st_ctime_ns,
        ) == stamp[1:]
    )


def _fold_records(
    conn: sqlite3.Connection, lesson_id: int, already_seq: int | None = None
) -> tuple[int, list[dict] | None]:
    """The lines the file projects: the active-state fold, deduplicated by `seq`
    and ascending. `as_of_seq` is the newest rowid of the whole history, not of
    the fold, so a retraction still advances it. Both reads share one read-only
    snapshot, closed before any filesystem work: a sibling process committing
    between two autocommit statements would otherwise leave the file advertising
    a watermark it does not contain. `already_seq` short-circuits inside that
    snapshot, so a watermark this process already published skips the fold."""
    conn.execute("BEGIN")
    try:
        as_of_seq = conn.execute(
            "SELECT MAX(id) FROM lesson_assessments WHERE lesson_id = ?",
            (lesson_id,),
        ).fetchone()[0]
        if already_seq is not None and int(as_of_seq or 0) == already_seq:
            return already_seq, None
        state = active_state(conn, lesson_id)
    finally:
        conn.rollback()  # nothing was written; just release the snapshot
    by_seq: dict[int, dict] = {}
    for row in state["evidence_by_concept"].values():
        by_seq[row["seq"]] = row
    for row in state["reviews_by_attempt"].values():
        by_seq[row["seq"]] = row
    if state["summary"] is not None:
        by_seq[state["summary"]["seq"]] = state["summary"]
    return int(as_of_seq or 0), [by_seq[seq] for seq in sorted(by_seq)]


def _render(lesson: dict, as_of_seq: int, records: list[dict]) -> bytes:
    """One `assessments_meta` line, then one full authority record per active
    row, ascending `seq` (spec §6.5)."""
    lines = [{
        "kind": META_KIND,
        "v": META_VERSION,
        "lesson_uid": lesson["uid"],
        "as_of_seq": as_of_seq,
        "generated_at": _utc_now_iso(),
    }]
    lines.extend(records)
    return "".join(
        json.dumps(line, ensure_ascii=False) + "\n" for line in lines
    ).encode("utf-8")


def _publish(lesson: dict, data: bytes) -> os.stat_result:
    """The bundle root is opened once with `O_NOFOLLOW | O_DIRECTORY` and every
    later step is relative to that descriptor, so the published name cannot be
    redirected between the checks and the rename."""
    dir_fd = os.open(lessons._lesson_dir(lesson["slug"]), projection.DIRECTORY_FLAGS)
    try:
        return projection.publish(
            dir_fd, PROJECTION_NAME, data, prefix=".assessments-"
        )
    finally:
        os.close(dir_fd)


def _rewrite_locked(conn: sqlite3.Connection, lesson: dict) -> bool:
    """Render the committed state and publish it. Runs under the flock."""
    uid = lesson.get("uid")
    already_seq = None
    if isinstance(uid, str) and uid:
        with _published_lock:
            stamp = _published.get(uid)
        # The identity gate runs on the skip path too: the manifest can change
        # between two calls at the same watermark, and a bundle that now names
        # another lesson is `pending` whether or not there are bytes to publish.
        if stamp is not None and _projection_unchanged(lesson, stamp):
            if projection.identity_contradicts(lesson):
                # Answered here: the fall-through would fold the whole active
                # state only to refuse on the same ground below.
                return False
            already_seq = stamp[0]
    as_of_seq, records = _fold_records(conn, lesson["id"], already_seq)
    if records is None:
        # Same watermark as the bytes this process published.
        return True
    if as_of_seq == 0 and not projection.projection_exists(lesson, PROJECTION_NAME):
        # Nothing recorded and nothing occupies the name: the absent file is the
        # state. Reconcile must not litter every bundle with an empty projection.
        return True
    if projection.identity_contradicts(lesson):
        return False
    st = _publish(lesson, _render(lesson, as_of_seq, records))
    if isinstance(uid, str) and uid:
        with _published_lock:
            _published[uid] = (
                as_of_seq,
                st.st_dev,
                st.st_ino,
                st.st_size,
                st.st_mtime_ns,
                st.st_ctime_ns,
            )
    return True


def reconcile_projection(conn: sqlite3.Connection, lesson: dict) -> bool:
    """Rewrite `assessments.jsonl` from the committed authority: the single
    projection entry point, idempotent. True when the bundle now reflects the
    authority; False (the honest `projection: pending`, never an exception past
    a durable write) on an active transaction, an unavailable or busy lock, a
    bundle root that cannot be opened safely, a manifest whose identity
    contradicts the lesson, or any filesystem error."""
    if conn.in_transaction:
        return False
    try:
        # The re-entrant in-process lock first, so concurrent same-lesson requests
        # in this worker queue instead of losing the non-blocking flock to each other.
        with projection.lesson_lock(lesson["slug"]):
            with projection.file_lock(PROJECTION_STATE_DIR, lesson):
                return _rewrite_locked(conn, lesson)
    except Exception:
        # Every exception: the durable write has already committed, so any
        # failure here degrades to `pending`, never turns the write into a 500.
        return False


def _project(conn: sqlite3.Connection, lesson: dict) -> str:
    return (
        PROJECTION_PROJECTED if reconcile_projection(conn, lesson)
        else PROJECTION_PENDING
    )


def _sweep_once(conn: sqlite3.Connection, lesson: dict) -> None:
    """Once per lesson per process, best effort."""
    lesson_id = lesson.get("id")
    if not isinstance(lesson_id, int):
        return
    with _swept_lock:
        if lesson_id in _swept:
            return
        _swept.add(lesson_id)
    reconcile_projection(conn, lesson)
