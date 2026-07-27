"""Lesson assessment recording — the authority half of the tutor-memory
channel (S-DESIGN D-S1-1 … D-S1-4, phase S slice s1).

`lesson_attempts` records what the learner did; this module records what the
TUTOR concluded. One table with a `kind` discriminator (`review`, `evidence`,
`summary`, `retraction`), one `lesson_assessment` ledger event, both written in
ONE transaction — the repo's standard write idiom, with no filesystem work
inside it (D-S1-5).

Trust model: the caller is the lesson-agent terminal session (curl or any
HTTP-capable tool inside the sandbox), admitted by the app perimeter
(`app/security.py`). It supplies no identity it does not own: `lesson_uid`
comes from the DB row, `question_id` is copied from the referenced attempt row,
`seq` is the rowid, and `sitting_id` stays NULL until the s3 write capability
resolves it server-side.

Not bridge-gated, deliberately (D-S1-4): assessments require no interactive
profile and read no manifest on the admission path. The tutor's memory must
work on every lesson, including `legacy-display` bundles that can never record
attempts — gating it on the bridge would disable tutor memory exactly where it
starts.

Ordering: the rowid, exposed as `seq`, is the sole recency authority;
`created_at` is UTC-microsecond display metadata. Rows are append-only — a
wrong record is corrected by a later row naming it in `supersedes` (or by a
`retraction`), and `active_state` folds that into the current view.

Projection: `assessments.jsonl` is slice s2. Until it lands every response
honestly reports `projection: "pending"` (docs/lesson-assessments-api.md).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from uuid import uuid4

from ..db import append_event
from . import bundle_schema

KINDS = ("review", "evidence", "summary", "retraction")
MODES = ("tutoring", "exam")
REVIEW_LEVELS = ("correct", "partial", "incorrect", "unclear")
# D-S1-2 / S-H6: ONE mutually exclusive current-judgment scale. `seen` = covered,
# no judgment yet; the rest are judgments. Practice volume is derivable from the
# attempts/runs record and is deliberately not restated here.
EVIDENCE_LEVELS = ("seen", "weak", "developing", "passed")
LEVELS_BY_KIND = {"review": REVIEW_LEVELS, "evidence": EVIDENCE_LEVELS}
# S-H3: what grounded an evidence judgment. `live` marks a non-replayable tutor
# observation and is admissible for ANY level — a single-user trust posture: the
# record is honest about its grounding rather than gatekept.
BASES = ("attempts", "artifacts", "runs", "live", "mixed")

MAX_NOTE_BYTES = 8 * 1024        # D-S1-3/S-M4: notes diagnose BY REFERENCE
MAX_NEXT_ACTION_BYTES = 512      # S-L1: one machine-readable next step
MAX_KEY_LEN = 128                # the attempts §6.3 bound
MAX_CONCEPTS = 8                 # D-S1-2: 1–8 opaque refs per evidence row

PROJECTION_PENDING = "pending"

# Rate limit (D-S1-3): 30 per lesson per 60 s with the attempts-style refund
# table — replays and key conflicts are not new writes and get their slot back,
# refusals of new writes stay charged. In-process memory by design: the
# deployment model is ONE worker (loopback systemd unit), so this is an abuse
# damper, not a security boundary.
RATE_WINDOW_SECONDS = 60.0
RATE_MAX_PER_WINDOW = 30

_monotonic = time.monotonic  # separable for tests
_rate_lock = threading.Lock()
_rate: dict[int, deque[float]] = {}

# One lock per lesson: the duplicate check and the transactional insert
# serialize in-process, so a retry racing its own original resolves as a
# duplicate instead of racing the UNIQUE constraint.
_lesson_locks_lock = threading.Lock()
_lesson_locks: dict[str, threading.RLock] = {}

# Accepted top-level request fields. Anything else is a 400 (strict, unlike the
# attempt endpoint's forward-compatible stance): a tutor typo such as
# "conepts" must fail loudly, not silently drop the mastery statement. Note
# what is absent: `question_id` (copied from the attempt row) and `sitting_id`
# (s3, resolved from the write capability) are never client-supplied.
_FIELDS = frozenset({
    "kind", "mode", "level", "basis", "attempt_id", "concepts",
    "note", "next_action", "supersedes", "idempotency_key",
})


class AssessmentError(Exception):
    """An assessment write was refused. `code` is the machine-readable reason
    (docs/lesson-assessments-api.md), `status` the HTTP status the route maps
    it to."""

    def __init__(self, code: str, status: int, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.status = status
        self.detail = detail


def _lesson_lock(slug: str) -> threading.RLock:
    with _lesson_locks_lock:
        lock = _lesson_locks.get(slug)
        if lock is None:
            lock = _lesson_locks[slug] = threading.RLock()
        return lock


def _reset_rate_limit() -> None:
    """Test hook: forget all rate-limit state."""
    with _rate_lock:
        _rate.clear()


def _check_rate(lesson_id: int) -> float:
    """Charge one window slot; returns the charged stamp so outcomes that turn
    out not to be new writes can refund it (the attempts idiom)."""
    now = _monotonic()
    with _rate_lock:
        window = _rate.setdefault(lesson_id, deque())
        while window and now - window[0] > RATE_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= RATE_MAX_PER_WINDOW:
            retry = max(1, int(RATE_WINDOW_SECONDS - (now - window[0])) + 1)
            raise AssessmentError("rate-limited", 429, f"retry after ~{retry}s")
        window.append(now)
        return now


def _refund_rate(lesson_id: int, stamp: float | None) -> None:
    if stamp is None:
        return
    with _rate_lock:
        window = _rate.get(lesson_id)
        if window is not None:
            try:
                window.remove(stamp)
            except ValueError:
                pass  # already expired out of the sliding window


def _utc_now_iso() -> str:
    """UTC ISO-8601 with microseconds — the attempts idiom. Display metadata
    only: `seq` (the rowid) is the recency authority, because the general
    `now_iso()` is second-precision and two writes in one second would
    otherwise have no causal order (S-M2)."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _utf8_len(value: str) -> int | None:
    """UTF-8 byte length, or None when the string is not encodable (lone
    surrogates from JSON \\uD800 escapes) — such a value could never be written
    to the ledger or the s2 projection. Same guard as the attempts writer."""
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _opt(payload: dict, name: str) -> object | None:
    """Optional field access: an explicit JSON `null` reads as absent, so a
    generator that emits every key uniformly is not punished for it. Nothing is
    ever coerced — a present non-null value is validated as given."""
    value = payload.get(name)
    return None if value is None else value


def _text(
    value: object, code: str, oversize_code: str, name: str, limit: int
) -> str:
    """A required non-blank UTF-8 text field within its byte bound. Blank is
    refused where the schema only demands non-empty: a whitespace-only
    diagnosis is not a diagnosis, and the column is the record's whole point."""
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
    """§4.5 opaque refs: 1–200 chars, no control characters, never resolved.
    Deduplicated server-side preserving first occurrence (the manifest's own
    `duplicate-concept` rule), so a repeated tag cannot inflate the fold."""
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
    """Validate the client-supplied submission per D-S1-2. Nothing here touches
    the database: every check is grammar, vocabulary, or per-kind shape, so the
    canonical fingerprint below is a pure function of the request."""
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
    if (
        not isinstance(key, str)
        or not 1 <= len(key) <= MAX_KEY_LEN
        or _utf8_len(key) is None
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in key)
    ):
        raise AssessmentError(
            "invalid-idempotency-key", 400,
            f"idempotency_key must be 1-{MAX_KEY_LEN} chars, no control characters",
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
    """SHA-256 over the canonical form of the complete validated submission
    (D-S1-3 / S-M3): absent fields omitted, concepts already deduplicated in
    first-occurrence order, exact note/next_action bytes, sorted keys. The
    idempotency key is the lookup, not the content, so it is not part of what
    the key identifies.

    D4's "same key + same question/page" shortcut is deliberately NOT inherited:
    an assessment carries a free-text judgment, and a key replayed with a
    different judgment must be a visible conflict, never a silent coalesce."""
    canonical = {
        name: value for name, value in submission.items()
        if name != "idempotency_key" and value is not None
    }
    blob = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _row_response(row: sqlite3.Row | dict, result: str) -> dict:
    """The endpoint's response body for one outcome. `assessments.jsonl` is
    slice s2, so `projection` is `pending` here for every outcome — honestly,
    because nothing projects yet (docs/lesson-assessments-api.md records this
    as the s1 interim state).

    This is the projection seam. Both callers hold the committed authority row
    when they get here — the replay path reads it back from SQLite, the write
    path has just inserted it — which is exactly what s2's reconcile needs:
    per D-S1-3 a lost-response retry must heal a projection left pending, or a
    session-closing summary stays invisible to the next tutor."""
    return {
        "result": result,
        "assessment_id": row["assessment_id"],
        "seq": row["id"],
        "projection": PROJECTION_PENDING,
    }


def _replay_or_conflict(
    conn: sqlite3.Connection, lesson: dict, submission: dict, fingerprint: str
) -> dict | None:
    """Known-key handling (D-S1-3): a replay of the same submission returns the
    original row untouched; the same key carrying a different submission is a
    client bug — a distinct conflict, never coalesced. None = fresh key."""
    existing = conn.execute(
        "SELECT * FROM lesson_assessments WHERE lesson_id = ? AND idempotency_key = ?",
        (lesson["id"], submission["idempotency_key"]),
    ).fetchone()
    if existing is None:
        return None
    if existing["fingerprint"] == fingerprint:
        return _row_response(existing, "duplicate")
    raise AssessmentError(
        "idempotency-conflict", 409,
        "idempotency_key was already used for a different assessment",
    )


def _resolve_attempt(
    conn: sqlite3.Connection, lesson: dict, attempt_id: str | None
) -> str | None:
    """The referenced attempt must be a recorded attempt OF THIS LESSON, and
    its `question_id` is copied from the row — the client never supplies
    identity it does not own (the D2/D5 idiom applied to the agent)."""
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
    """`supersedes` must name an existing assessment of the same lesson. Append
    order then makes correction chains acyclic for free: a row can only name
    rows that already exist, so every chain runs strictly backwards in `seq`."""
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


def record_assessment(conn: sqlite3.Connection, lesson: dict, payload: dict) -> dict:
    """Record one assessment for `lesson` (a lessons service view dict).

    Returns the response body fields for the endpoint:
      recorded  -> {result, assessment_id, seq, projection}
      duplicate -> the original row's, same shape
    Refusals raise AssessmentError with a distinct code per
    docs/lesson-assessments-api.md."""
    submission = _clean_submission(payload)
    fingerprint = _fingerprint(submission)

    # D-S1-3: the replay lookup precedes every mutable-state refusal — the
    # archive check, the attempt/supersedes references, and the rate limit
    # included. The original write is already durable, so a client retry must
    # learn its assessment_id even if the lesson was archived meanwhile or the
    # retry lands with the window exhausted; the refusals below govern only NEW
    # writes. Replays and conflicts consume no budget: the unmetered work is
    # one indexed SELECT.
    with _lesson_lock(lesson["slug"]):
        replay = _replay_or_conflict(conn, lesson, submission, fingerprint)
    if replay is not None:
        return replay

    rate_stamp: float | None = None
    try:
        rate_stamp = _check_rate(lesson["id"])
        if lesson.get("archived"):
            # D-S1-4: the owner unarchives first — a distinct 409, never a
            # silent write into a lesson that has been put away. This is the
            # cheap early refusal on the caller's view of the lesson; the
            # binding one runs inside the write transaction (_record_locked).
            raise AssessmentError(
                "lesson-archived", 409,
                "this lesson is archived; restore it before recording",
            )
        if not lesson.get("uid"):  # unreachable post-v11 backfill; fail closed
            raise AssessmentError("lesson-unavailable", 409, "lesson has no uid")
        question_id = _resolve_attempt(conn, lesson, submission["attempt_id"])
        _require_supersedes(conn, lesson, submission["supersedes"])
    except AssessmentError:
        # A retry racing its own original (timeout resend) can see the key
        # uncommitted at the early check above and then hit a refusal here
        # after the original committed. The durable outcome still wins —
        # re-check before refusing.
        try:
            with _lesson_lock(lesson["slug"]):
                replay = _replay_or_conflict(conn, lesson, submission, fingerprint)
        except AssessmentError:
            _refund_rate(lesson["id"], rate_stamp)  # conflict: not a new write
            raise
        if replay is not None:
            _refund_rate(lesson["id"], rate_stamp)
            return replay
        raise

    try:
        with _lesson_lock(lesson["slug"]):
            return _record_locked(
                conn, lesson, submission, fingerprint, question_id, rate_stamp
            )
    except AssessmentError as exc:
        if exc.code == "idempotency-conflict":  # not a new write
            _refund_rate(lesson["id"], rate_stamp)
        raise


def _record_locked(
    conn: sqlite3.Connection,
    lesson: dict,
    submission: dict,
    fingerprint: str,
    question_id: str | None,
    rate_stamp: float | None,
) -> dict:
    """The lesson-locked write section of `record_assessment`."""
    # Re-check under the lock: another in-process writer may have landed the
    # same key between the early replay check and here.
    replay = _replay_or_conflict(conn, lesson, submission, fingerprint)
    if replay is None:
        assessment_id = str(uuid4())
        # The event echoes `seq`, which is the row's own rowid, so the row must
        # be inserted before the event — hence the uuid is minted here and
        # handed to the ledger writer rather than returned by it.
        event_uuid = str(uuid4())
        created_at = _utc_now_iso()
        concepts = submission["concepts"]
        try:
            # ONE transaction (D-S1-1): the row and its `lesson_assessment`
            # event commit or roll back together. No filesystem work runs
            # inside it (D-S1-5) — the s2 projection is post-commit work.
            #
            # BEGIN IMMEDIATE rather than the usual `with conn:`, because the
            # archive re-check below must be part of THIS transaction. Python's
            # sqlite3 opens its implicit transaction at the first DML statement,
            # so a SELECT inside `with conn:` would still read in autocommit and
            # leave the same window the caller's stale view has: lesson read,
            # archive committed elsewhere, assessment inserted anyway. Taking
            # the write lock up front gives the archive and this write a
            # definitive order.
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    "SELECT archived_at FROM lessons WHERE id = ?",
                    (lesson["id"],),
                ).fetchone()
                if current is None:
                    raise AssessmentError(
                        "unknown-lesson", 404, "the lesson no longer exists"
                    )
                if current["archived_at"] is not None:
                    # The binding refusal: whatever the caller's view said, the
                    # committed state at write time is what decides.
                    raise AssessmentError(
                        "lesson-archived", 409,
                        "this lesson is archived; restore it before recording",
                    )
                cursor = conn.execute(
                    "INSERT INTO lesson_assessments "
                    "(assessment_id, event_uuid, lesson_id, lesson_uid, "
                    " sitting_id, mode, idempotency_key, fingerprint, kind, "
                    " level, basis, attempt_id, question_id, concepts_json, "
                    " note, next_action, supersedes, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        assessment_id, event_uuid, lesson["id"], lesson["uid"],
                        # sitting_id stays NULL in s1: the terminal session's
                        # SID arrives with the s3 write capability, and it is
                        # derived server-side from that token — never from the
                        # request body.
                        None,
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
                    # D-S1-1 echo policy: identity and the record itself.
                    "lesson_uid": lesson["uid"],
                    "lesson_id": lesson["id"],
                    "slug": lesson["slug"],
                    "assessment_id": assessment_id,
                    "seq": seq,
                    "kind": submission["kind"],
                    "mode": submission["mode"],
                    "sitting_id": None,
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
            except BaseException:
                conn.rollback()
                raise
            conn.commit()
        except sqlite3.IntegrityError:
            # The same idempotency key landed from another PROCESS (a stale
            # second server; in-process writers serialize on the lesson lock) —
            # answer with its outcome instead of a 500.
            replay = _replay_or_conflict(conn, lesson, submission, fingerprint)
            if replay is None:
                raise
        else:
            return _row_response(
                {"assessment_id": assessment_id, "event_uuid": event_uuid,
                 "id": seq},
                "recorded",
            )
    _refund_rate(lesson["id"], rate_stamp)
    return replay


# --- read model: the active-state fold (D-S1-2) ------------------------------


def row_view(row: sqlite3.Row | dict) -> dict:
    """One authority row as the record shape its consumers read: the s2
    projection line, the s4 panel, and the verifier. `seq` is the rowid;
    `concepts` is decoded back into a list (a stored value that no longer
    parses reads as no concepts rather than raising — the column is written
    only by this module, so that is a corrupt-database guard, not a
    contract)."""
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
        "event_uuid": row["event_uuid"],
        "lesson_uid": row["lesson_uid"],
        "sitting_id": row["sitting_id"],
        "mode": row["mode"],
        "kind": row["kind"],
        "level": row["level"],
        "basis": row["basis"],
        "attempt_id": row["attempt_id"],
        "question_id": row["question_id"],
        "concepts": concepts,
        "note": row["note"],
        "next_action": row["next_action"],
        "supersedes": row["supersedes"],
        "created_at": row["created_at"],
    }


# The deactivation lookup is correlated, so it needs `idx_assessments_lesson_
# supersedes` to stay bounded — without that index it rescans the lesson's whole
# history per row and the fold goes quadratic. verify asserts the query plan.
ACTIVE_ROWS_SQL = (
    "SELECT * FROM lesson_assessments a WHERE a.lesson_id = ? "
    "AND NOT EXISTS (SELECT 1 FROM lesson_assessments s "
    "                WHERE s.lesson_id = a.lesson_id "
    "                  AND s.supersedes = a.assessment_id) "
    "ORDER BY a.id"
)


def active_rows(conn: sqlite3.Connection, lesson_id: int) -> list[dict]:
    """Rows not targeted by any `supersedes`, ascending `seq` (D-S1-2).

    A retraction and a one-write correction deactivate their target the same
    way; `supersedes` is validated same-lesson at write time, so the join
    cannot reach across lessons."""
    rows = conn.execute(ACTIVE_ROWS_SQL, (lesson_id,)).fetchall()
    return [row_view(row) for row in rows]


def active_state(conn: sqlite3.Connection, lesson_id: int) -> dict:
    """The current-state fold consumed by the s2 projection and the s4 panel:
    the latest active evidence per concept, the latest active review per
    attempt, and the latest active summary — all by `seq`.

    Retractions carry no state of their own: they only deactivate, so they
    never appear in the fold. Evidence spanning several concepts is the latest
    for each of them."""
    evidence_by_concept: dict[str, dict] = {}
    reviews_by_attempt: dict[str, dict] = {}
    summary: dict | None = None
    for row in active_rows(conn, lesson_id):  # ascending seq: later rows win
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
