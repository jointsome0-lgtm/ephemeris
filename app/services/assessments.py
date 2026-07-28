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
`seq` is the rowid, and `sitting_id` is resolved from the session's write
capability (s3) — the token names the lesson and the sitting, the body never
does. A request without a capability is the owner/manual path: still admitted
inside the documented loopback perimeter, with no sitting recorded.

Not bridge-gated, deliberately (D-S1-4): assessments require no interactive
profile and read no manifest on the admission path. The tutor's memory must
work on every lesson, including `legacy-display` bundles that can never record
attempts — gating it on the bridge would disable tutor memory exactly where it
starts.

Ordering: the rowid, exposed as `seq`, is the sole recency authority;
`created_at` is UTC-microsecond display metadata. Rows are append-only — a
wrong record is corrected by a later row naming it in `supersedes` (or by a
`retraction`), and `active_state` folds that into the current view.

Projection (s2): `assessments.jsonl` at the bundle root is the ACTIVE-STATE
read model — the next tutor's resume artifact, not a history log. It is
rewritten in full after the transaction commits and never inside it; a
projection failure never fails the durable write, and the response says
`projected` or `pending` honestly (docs/lesson-assessments-api.md).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat as stat_module
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..db import DATA_DIR, append_event
from . import bundle_schema, lessons

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
PROJECTION_PROJECTED = "projected"

# The bundle projection (D-S1-5, spec §6.5). The lock file lives OUTSIDE the
# agent-writable bundle and is this file's own — the attempts projection keeps
# its separate lock, so assessment work never makes an attempt write report
# `pending` and the freshly drained #58 machinery is not touched.
PROJECTION_NAME = "assessments.jsonl"
PROJECTION_STATE_DIR = DATA_DIR / "assessment-projections"
META_KIND = "assessments_meta"
META_VERSION = 1

# Rate limit (D-S1-3): 30 per lesson per 60 s with the attempts-style refund
# table — replays and key conflicts are not new writes and get their slot back,
# refusals of new writes stay charged. In-process memory by design: the
# deployment model is ONE worker (loopback systemd unit), so this is an abuse
# damper, not a security boundary.
RATE_WINDOW_SECONDS = 60.0
RATE_MAX_PER_WINDOW = 30

# The write capability (D-S1-3 / D-S2-2, slice s3). The lesson-agent terminal
# session hands its token back in this header; the registry that answers for it
# lives in `app/terminal.py` and dies with the session and the process. A
# request without the header is the owner/manual path and is still admitted —
# it simply records no sitting.
CAPABILITY_HEADER = "X-Ephemeris-Assess-Token"

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


def _row_response(
    conn: sqlite3.Connection, lesson: dict, row: sqlite3.Row | dict, result: str
) -> dict:
    """The endpoint's response body for one outcome, projection included.

    This is the projection seam. Both callers hold the committed authority row
    when they get here — the replay path reads it back from SQLite, the write
    path has just inserted it — and the projection runs from here, after the
    transaction, never inside it (D-S1-5). Routing the REPLAY path through the
    same seam is what D-S1-3 asks for: a lost-response retry heals a projection
    left pending, or a session-closing summary stays invisible to the next
    tutor. The rewrite renders the whole committed active state, so healing
    needs no record of which write was left unprojected."""
    return {
        "result": result,
        "assessment_id": row["assessment_id"],
        "seq": row["id"],
        "projection": _project(conn, lesson),
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
        return _row_response(conn, lesson, existing, "duplicate")
    raise AssessmentError(
        "idempotency-conflict", 409,
        "idempotency_key was already used for a different assessment",
    )


def _capability_registry_lookup(token: str) -> dict | None:
    """The narrow accessor into the terminal module's session registry.

    Imported at the point of use: `app/terminal.py` imports this package's
    lessons module, so a module-level import here would close a cycle. The
    terminal is also optional at runtime — an app started without it simply has
    no live capabilities, and every token then resolves to nothing.
    """
    from .. import terminal

    return terminal.resolve_assessment_capability(token)


def resolve_capability(lesson: dict, token: str | None) -> str | None:
    """The sitting a write came from, derived SERVER-SIDE (D-S1-3).

    No token → None: the owner/manual `curl` path stays admitted inside the
    documented loopback single-user perimeter, and its rows simply carry no
    sitting. This is a memo decision, not an oversight.

    A token that resolves → the lesson-agent session's SID, plus a hard check
    that the URL's lesson is the token's lesson. A token that does not resolve
    (never minted, or its session ended — including every token from before an
    app restart) → a visible 403. There is deliberately no silent fallback to
    the tokenless path: an agent whose writes have quietly lost their
    provenance would keep recording verdicts that no longer say where they came
    from, and the brief tells it to degrade openly instead.
    """
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
    """One ACTIVE summary per sitting (D-S0-1 / D-S1-2).

    A tutoring session closes with ONE synthesis; a second one is either a
    correction of the first — which says so in `supersedes` — or a mistake. The
    refusal names the row to supersede, so the agent can comply without
    querying anything.

    The rule is scoped to a sitting because that is what it is about: with no
    sitting (the owner path) there is nothing to be the second summary OF, and
    a lesson accumulates one summary per tutoring session by design. Runs
    inside the write transaction: the fold it reads must be the committed one
    this insert is ordered against.
    """
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


def record_assessment(
    conn: sqlite3.Connection,
    lesson: dict,
    payload: dict,
    capability_token: str | None = None,
) -> dict:
    """Record one assessment for `lesson` (a lessons service view dict).

    `capability_token` is the caller's session write capability, or None for the
    owner/manual path; the sitting it names is resolved here and never read from
    the request body.

    Returns the response body fields for the endpoint:
      recorded  -> {result, assessment_id, seq, projection}
      duplicate -> the original row's, same shape
    Refusals raise AssessmentError with a distinct code per
    docs/lesson-assessments-api.md."""
    submission = _clean_submission(payload)
    # Before any state work, like the rest of validation: a dead capability is a
    # fact about the request, and answering it costs no filesystem or DB work.
    # A replay is refused too — an agent retrying with a capability that has
    # since died must learn that, not receive a quiet duplicate.
    sitting_id = resolve_capability(lesson, capability_token)
    fingerprint = _fingerprint(submission)

    # Reconcile trigger (c) — first write per lesson per process (D-S1-5). The
    # write paths below rewrite the projection themselves, so this sweep exists
    # for the outcomes that do NOT write: a process that restarted while a
    # projection was pending heals it on the next assessment call for that
    # lesson even when the call turns out to be a refusal. It runs after
    # validation (a malformed body does no filesystem work) and once per lesson
    # per process whether or not it succeeds — the other two triggers are the
    # retry mechanism.
    _sweep_once(conn, lesson)

    # D-S1-3: the replay lookup precedes every mutable-state refusal — the
    # archive check, the attempt/supersedes references, and the rate limit
    # included. The original write is already durable, so a client retry must
    # learn its assessment_id even if the lesson was archived meanwhile or the
    # retry lands with the window exhausted; the refusals below govern only NEW
    # writes. Replays and conflicts consume no budget, so the work they can
    # repeat has to stay cheap: one indexed SELECT here, and a reconcile that
    # returns after reading a watermark unless the state actually moved since
    # this process last published (see `_published`).
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
                conn, lesson, submission, fingerprint, question_id, rate_stamp,
                sitting_id,
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
    sitting_id: str | None,
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
                        # The sitting comes from the write capability, resolved
                        # server-side; NULL is the owner/manual path.
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
                    # D-S1-1 echo policy: identity and the record itself.
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
            # Post-commit, outside the transaction: the projection reads the
            # freshly committed state itself rather than this row (D-S1-5).
            return _row_response(
                conn, lesson,
                {"assessment_id": assessment_id, "event_uuid": event_uuid,
                 "id": seq},
                "recorded",
            )
    _refund_rate(lesson["id"], rate_stamp)
    return replay


# --- read model: the active-state fold (D-S1-2) ------------------------------


# The columns `fold_rows` decides on, and nothing else. The fold has to visit
# every active row, but it KEEPS at most one per concept, one per attempt and
# one summary — so a reader that only wants the fold's winners can walk the
# lesson on these five columns and pay for `note` (8 KiB a row) once per row it
# will actually show. `row_view` builds on the same keys so the two shapes
# cannot drift.
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
    """One authority row as the record shape its consumers read: the s2
    projection line, the s4 panel, and the verifier. `seq` is the rowid;
    `concepts` is decoded back into a list (a stored value that no longer
    parses reads as no concepts rather than raising — the column is written
    only by this module, so that is a corrupt-database guard, not a
    contract)."""
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


# The deactivation lookup is correlated, so it needs `idx_assessments_lesson_
# supersedes` to stay bounded — without that index it rescans the lesson's whole
# history per row and the fold goes quadratic. verify asserts the query plan.
_ACTIVE_SQL = (
    "SELECT {columns} FROM lesson_assessments a WHERE a.lesson_id = ? "
    "AND NOT EXISTS (SELECT 1 FROM lesson_assessments s "
    "                WHERE s.lesson_id = a.lesson_id "
    "                  AND s.supersedes = a.assessment_id) "
    "ORDER BY a.id"
)
# One definition of "active", two column lists: whoever reads narrow rows reads
# exactly the rows the wide query would have returned.
ACTIVE_ROWS_SQL = _ACTIVE_SQL.format(columns="*")
ACTIVE_FOLD_KEYS_SQL = _ACTIVE_SQL.format(columns=FOLD_KEYS_COLUMNS)


def active_rows(conn: sqlite3.Connection, lesson_id: int) -> list[dict]:
    """Rows not targeted by any `supersedes`, ascending `seq` (D-S1-2).

    A retraction and a one-write correction deactivate their target the same
    way; `supersedes` is validated same-lesson at write time, so the join
    cannot reach across lessons."""
    rows = conn.execute(ACTIVE_ROWS_SQL, (lesson_id,)).fetchall()
    return [row_view(row) for row in rows]


def fold_rows(rows: list[dict]) -> dict:
    """The current-state fold over already-read ACTIVE rows, ascending `seq`.

    Pure, so the two readers that need it — the s2 projection and the s4 panel
    — share one definition of "current" instead of two that can drift.
    Retractions carry no state of their own: they only deactivate, so they
    never appear in the fold. Evidence spanning several concepts is the latest
    for each of them."""
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


def active_state(conn: sqlite3.Connection, lesson_id: int) -> dict:
    """The current-state fold consumed by the s2 projection and the s4 panel:
    the latest active evidence per concept, the latest active review per
    attempt, and the latest active summary — all by `seq`."""
    return fold_rows(active_rows(conn, lesson_id))


# The number of earlier readings behind each DISPLAYED review winner. The
# active fold has already selected exactly one standing winner per attempt, so
# pass those `(attempt_id, winner_id)` pairs through a bounded VALUES CTE
# instead of rediscovering winners for every historical attempt in the lesson.
#
# Only rows before that winner count. Reviews corrected by another review stay
# in the count — replaced readings are exactly what the marker acknowledges —
# while reviews struck by a retraction are excluded outright, in either write
# order. The correlated lookup rides `idx_assessments_lesson_supersedes`; the
# join seeks reviews through `idx_assessments_lesson_kind`.
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

# Leave ample room below SQLite's traditional 999-variable default: each
# hydration statement also binds the lesson id. The fold has no winner-count
# ceiling, so this is a statement-size bound, not a record-view bound.
_HYDRATE_IDS_PER_QUERY = 500
# Two variables per displayed winner plus the lesson id. Keep the fixed batch
# comfortably below SQLite's traditional 999-variable default.
_REVIEW_COUNTS_PER_QUERY = 250


def _hydrate(conn: sqlite3.Connection, lesson_id: int, state: dict) -> dict:
    """Re-read the fold's winners whole in bounded statements, then put them
    back where the narrow rows were. Same connection and no write between the
    reads, so they see the state the first query folded."""
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
    """Count earlier, non-retracted readings for displayed review winners."""
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
    """Everything the s4 record panel folds out of the authority rows (D-S3-1).

    Read-only: the D-S1-2 fold, how many active records stand behind it, and
    when each attempt's reviews were written. `active_count` counts records
    that carry state — a retraction is an active row but says only that
    another record was wrong, so counting it would inflate what the panel
    claims to know.

    This runs on every `/learn` render, and the active fold has no cardinality
    ceiling (spec §6.5 calls the projection a compaction, not a cap), so the
    walk is deliberately narrow: the fold decides on five small columns;
    evidence winners, the summary, and only review winners named by
    `review_attempt_ids` are read whole. An 8 KiB note is therefore paid for
    once per displayed record instead of once per active row or reviewed
    historical attempt.

    The helper owns a read snapshot when its caller does not already have one.
    `_record_panel` starts the wider snapshot that also covers its attempt and
    focus reads; direct callers still cannot mix fold/hydration/count versions.
    """
    own_snapshot = not conn.in_transaction
    if own_snapshot:
        conn.execute("BEGIN")
    try:
        keys = [_fold_keys(row) for row
                in conn.execute(ACTIVE_FOLD_KEYS_SQL, (lesson_id,)).fetchall()]
        state = fold_rows(keys)
        if review_attempt_ids is not None:
            state["reviews_by_attempt"] = {
                attempt_id: row
                for attempt_id, row in state["reviews_by_attempt"].items()
                if attempt_id in review_attempt_ids
            }
        state = _hydrate(conn, lesson_id, state)
        state["active_count"] = sum(
            1 for row in keys if row["kind"] != "retraction"
        )
        state["earlier_review_counts"] = _earlier_review_counts(
            conn, lesson_id, state["reviews_by_attempt"]
        )
        return state
    finally:
        if own_snapshot:
            conn.rollback()


# --- projection: `assessments.jsonl`, the active-state read model (D-S1-5) ---
#
# Purpose (spec §6.5): the file is the next tutor's RESUME ARTIFACT — the
# current state, not the history. Full history stays in SQLite and rides the
# JSONL export. Its size therefore tracks current state (concepts + reviewed
# attempts + 1), not lifetime writes, which is why this is a plain full
# rewrite: no append fast path, no cursor, no seal, no prefix verification,
# and none of the #58 attempts machinery.
#
# That is a compaction, not a cap (spec §6.5): a lesson that keeps naming new
# concepts and attempts keeps growing, and the rewrite is linear in the active
# fold. Repeating identical work is what has to be avoided, not the size — see
# `_published` below.
#
# Ordering invariant (S-H1): commit first, project after. The transaction is
# short and touches no filesystem; the entry point below refuses an
# in-transaction connection outright. The per-lesson flock is an app-private
# file outside the agent-writable bundle — never SQLite's writer lock — and the
# committed state is re-read FRESH under it, so whoever holds the lock renders
# the newest fold and a slow writer cannot publish an older file last.

_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)

# Reconcile trigger (c): the lessons already swept in this process. Membership
# is recorded before the sweep runs, so a failing sweep is not retried on every
# subsequent write — triggers (a) and (b) are the retry mechanism.
_swept_lock = threading.Lock()
_swept: set[int] = set()

# What this process last PUBLISHED, per lesson uid: the watermark it rendered
# and the identity of the file it left behind (device, inode, size, mtime,
# ctime). It exists
# to keep a reconcile that would republish identical bytes from doing the
# work — an idempotent replay is deliberately outside the rate budget (D-S1-3:
# a retry must learn its assessment_id even when the window is exhausted), so
# without this a client looping one duplicate key would drive an unlimited
# number of full rewrites and fsyncs of a file that grows with the lesson's
# active state.
#
# The skip is verified, not assumed, because the replay heal (D-S1-3) has to
# keep healing: only a watermark this process published itself counts (one
# left `pending`, or written before a restart, is never skipped), and the
# published file must still be there with its full metadata seal. Device and
# ctime are load-bearing: a same-size in-place edit can preserve inode and
# restore mtime, but cannot restore the kernel change time. The agent owns the
# bundle and can delete or replace the file; that is a rewrite, not a skip.
# Metadata only — the file's bytes are never read here or anywhere else in
# this module.
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


def _reset_sweep_state() -> None:
    """Test hook: forget which lessons this process has swept or published."""
    with _swept_lock:
        _swept.clear()
    with _published_lock:
        _published.clear()


def _lock_path(lesson: dict) -> Path:
    """The app-private lock file for this lesson's assessment projection.

    Named by the immutable lesson uid and kept outside the bundle, so a lesson
    dir the agent can write cannot influence the lock. A lesson without a
    usable uid has no safe lock name — that is an unavailable lock, i.e. a
    pending projection, never a write into a guessed path."""
    uid = lesson.get("uid")
    if not isinstance(uid, str) or bundle_schema.UUID_RE.match(uid) is None:
        raise OSError("lesson has no safe projection-lock identity")
    return PROJECTION_STATE_DIR / f"{uid}.lock"


@contextmanager
def _projection_file_lock(lesson: dict):
    """Private per-lesson cross-process exclusion for assessment projection
    work (the attempts `_projection_file_lock` idiom, its own lock file).

    Non-blocking: a busy lock is an unavailable lock, which callers report as
    `projection: pending` — the row is already durable and the next reconcile
    trigger heals the file. Two open descriptors of the same lock file conflict
    even inside one process, so this also serializes threads that did not take
    the in-process lesson lock."""
    # Unix-only, and this module is on main.py's import chain, so fcntl is
    # imported here rather than at module level. A platform without it is an
    # unavailable lock, not a crash: callers degrade to pending, exactly as the
    # attempts projection does.
    try:
        import fcntl
    except ImportError as exc:
        raise OSError("advisory file locking (fcntl.flock) is unavailable") from exc

    lock_path = _lock_path(lesson)
    PROJECTION_STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not stat_module.S_ISDIR(os.lstat(PROJECTION_STATE_DIR).st_mode):
        raise OSError("projection lock root is not a directory")
    fd = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | os.O_NONBLOCK
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        st = os.fstat(fd)
        if not stat_module.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise OSError("unsafe projection lock file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError("short write on the assessment projection")
        view = view[count:]


def _fold_records(
    conn: sqlite3.Connection, lesson_id: int, already_seq: int | None = None
) -> tuple[int, list[dict] | None]:
    """The lines the file projects: the active-state fold, deduplicated by
    `seq` and ascending.

    One evidence row can cover several concepts and would appear once per
    concept in the fold's index — it is ONE record and is written once.
    Retractions are active rows that carry no state of their own, so the fold
    never yields them; superseded and retracted rows never appear at all.

    `as_of_seq` is the newest rowid of the lesson's whole history, not of the
    fold: it is the authority watermark the rendered state was taken at, so a
    retraction (which removes a line) still advances it.

    Both reads share ONE snapshot. A sibling process — the documented
    rolling-restart overlap — can commit between two autocommit statements,
    and the file would then advertise a watermark it does not contain: the
    fold would miss the new row while `as_of_seq` named it, and the sibling's
    own write would report `pending` after losing the non-blocking lock, so
    the inconsistency could outlive the request. The transaction is a
    read-only snapshot (WAL: it blocks no writer) and is closed before any
    filesystem work begins.

    `already_seq` short-circuits inside that snapshot: the watermark is read
    first, and when it matches what this process last published the fold is
    never materialized at all. Rows are insert-only, so the watermark is an
    exact version stamp of the fold — an unchanged `MAX(id)` cannot hide a
    changed active state."""
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
    """The file's bytes: one `assessments_meta` line, then one line per active
    record, ascending `seq` (spec §6.5). Each record line is the full authority
    record — the same shape the ledger event echoes — so a reader needs no
    join to know what was concluded."""
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


def _identity_contradicts(lesson: dict) -> bool:
    """The publication identity gate (S-H7): never publish lesson A's
    conclusions into a bundle whose manifest says it is lesson B.

    A readable manifest carrying a `lesson_uid` that differs from the DB uid
    blocks publication; the row still commits and the projection stays pending
    until the contradiction is resolved. A missing, v1/legacy, or rejected
    manifest does NOT block — the slug directory is the DB's own mapping, and
    demanding a valid v2 manifest would silence exactly the legacy lessons the
    assessment channel exists for (D-S1-4). The read is the PURE one (D-F1-2):
    projecting never creates a directory, a skeleton manifest, or a file.

    A rejected read carries no trusted identity, so it never gates. The
    reader can assign `lesson_uid` and only afterwards accumulate a rejecting
    finding — an empty `pages` list, a duplicate id — and honouring that
    half-parsed value would block the projection permanently on exactly the
    broken manifests the rule above says must publish. This is the
    `effective_profile` idiom (§9.2): consumers read nothing but findings out
    of a rejected manifest."""
    read = lessons.read_bundle_readonly(lesson)
    if read.rejected:
        return False
    uid = read.lesson_uid
    return isinstance(uid, str) and bool(uid) and uid != lesson.get("uid")


def _projection_exists(lesson: dict) -> bool:
    """Whether anything at all occupies the projection name (no-follow)."""
    try:
        os.lstat(lessons._lesson_dir(lesson["slug"]) / PROJECTION_NAME)
    except (OSError, lessons.LessonError):
        return False
    return True


def _clear_collision(dir_fd: int) -> str | None:
    """Make the projection name replaceable, never adopting what sits there.

    A regular single-link file is the normal case and is left for `os.replace`.
    Anything else — a directory (which `os.replace` cannot overwrite), a
    symlink, a multi-link file, a device or socket — is a foreign object: an
    empty directory is removed, everything else is moved aside under a unique
    name. Deterministic, never silent adoption, and never a permanent
    projection-pending state (the attempts collision idiom). The file's content
    is never read, so a planted file cannot influence what is published."""
    try:
        st = os.lstat(PROJECTION_NAME, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    if stat_module.S_ISREG(st.st_mode) and st.st_nlink == 1:
        return None
    if stat_module.S_ISDIR(st.st_mode):
        try:
            os.rmdir(PROJECTION_NAME, dir_fd=dir_fd)
            return None
        except OSError:
            pass  # non-empty: move it aside with its content intact
    aside = f"{PROJECTION_NAME}.collision-{uuid4().hex[:8]}"
    os.rename(PROJECTION_NAME, aside, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    return aside


def _stage_temp(dir_fd: int, data: bytes) -> tuple[str, int]:
    """Write the rendered bytes to a fresh 0600 temp file in the bundle and
    fsync it. `O_EXCL` means an attacker-planted name is never opened.

    Returns the name and the still-open descriptor: the caller publishes under
    it, so it must be able to ask the staged inode what it has become."""
    for _ in range(20):
        name = f".assessments-{uuid4().hex}.tmp"
        try:
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=dir_fd,
            )
        except FileExistsError:
            continue
        try:
            _write_all(fd, data)
            os.fsync(fd)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(name, dir_fd=dir_fd)
            except OSError:
                pass
            raise
        return name, fd
    raise OSError("could not allocate an assessment projection temp file")


def _publish(lesson: dict, data: bytes) -> os.stat_result:
    """Atomically replace the projection over the verified bundle root,
    and report the identity of the file left behind.

    The bundle directory is opened once with `O_NOFOLLOW | O_DIRECTORY` — a
    symlinked or non-directory bundle root refuses here rather than being
    followed — and every later step is relative to that descriptor, so the
    published name cannot be redirected between the checks and the rename."""
    dir_fd = os.open(lessons._lesson_dir(lesson["slug"]), _DIRECTORY_FLAGS)
    try:
        # Stage first, clear second: a failure while rendering must not leave
        # the reader with the published file already moved out of the way.
        temp_name, temp_fd = _stage_temp(dir_fd, data)
        try:
            # The temp carries a visible name in a directory the lesson agent
            # can write, so a link planted there would survive the rename and
            # publish a multiply-linked projection — exactly the shape
            # `_clear_collision` refuses to accept on the way in. The staged
            # descriptor is still open, so ask the inode itself.
            if os.fstat(temp_fd).st_nlink != 1:
                raise OSError("the staged assessment projection gained a link")
            _clear_collision(dir_fd)
            os.replace(
                temp_name, PROJECTION_NAME, src_dir_fd=dir_fd, dst_dir_fd=dir_fd
            )
            # the descriptor now names the published file: its identity is
            # what a later skip decision is checked against
            published = os.fstat(temp_fd)
            closing_fd, temp_fd = temp_fd, -1
            os.close(closing_fd)
        except BaseException:
            if temp_fd >= 0:
                try:
                    os.close(temp_fd)
                except OSError:
                    pass
            try:
                os.unlink(temp_name, dir_fd=dir_fd)
            except OSError:
                pass
            raise
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return published


def _rewrite_locked(
    conn: sqlite3.Connection, lesson: dict, force: bool = False
) -> bool:
    """Render the committed state and publish it. Runs under the flock."""
    uid = lesson.get("uid")
    already_seq = None
    if not force and isinstance(uid, str) and uid:
        with _published_lock:
            stamp = _published.get(uid)
        # The identity gate runs on the skip path too. The manifest can be
        # rewritten between two calls at the same watermark, and S-H7 governs
        # what the RESPONSE may claim, not only what gets written: a bundle
        # that now names another lesson is `pending` whether or not this
        # process would have had bytes to publish. One small manifest read is
        # still nothing against the rewrite it replaces.
        if stamp is not None and _projection_unchanged(lesson, stamp):
            if _identity_contradicts(lesson):
                # Answer here rather than falling through as a cache miss: the
                # fall-through would fold the whole active state only to refuse
                # on the same ground below, and replays that reach this are
                # unmetered.
                return False
            already_seq = stamp[0]
    as_of_seq, records = _fold_records(conn, lesson["id"], already_seq)
    if records is None:
        # Same watermark as the bytes this process published: republishing
        # would produce the same file but for the meta line's `generated_at`.
        return True
    if as_of_seq == 0 and not _projection_exists(lesson):
        # Nothing was ever recorded for this lesson and nothing occupies the
        # name: the absent file already IS the state. Reconcile runs at every
        # lesson-agent terminal open, and it must not litter every bundle —
        # including the legacy ones — with an empty projection.
        return True
    if _identity_contradicts(lesson):
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


def reconcile_projection(
    conn: sqlite3.Connection, lesson: dict, force: bool = False
) -> bool:
    """Rewrite `assessments.jsonl` from the committed authority.

    The single projection entry point: the write path, the replay heal, the
    first-write sweep, and the lesson-agent terminal open all land here.
    Idempotent by construction — it renders current state, so running it twice
    publishes the same bytes but for the meta line's `generated_at`.

    A reconcile that would republish exactly what this process last published
    does nothing instead (it does not even materialize the fold). `force`
    remains available to focused integrity probes that deliberately require a
    fresh render; production callers rely on the metadata seal to detect a
    missing or changed file.

    Returns True when the bundle now reflects the authority, False when it does
    not: an active transaction (filesystem work must never run inside one), an
    unavailable or busy lock, a bundle root that cannot be opened safely, a
    manifest whose identity contradicts the lesson, or any filesystem error.
    False is the honest `projection: pending` — never an exception past a
    durable write."""
    if conn.in_transaction:
        return False
    try:
        # The in-process lesson lock first (it is re-entrant, and the write
        # path already holds it), so concurrent same-lesson requests in this
        # worker queue instead of losing the non-blocking flock to each other.
        with _lesson_lock(lesson["slug"]):
            with _projection_file_lock(lesson):
                return _rewrite_locked(conn, lesson, force)
    except Exception:
        # Deliberately every exception, not a curated list. The projection is
        # derived and best-effort; the durable write it follows has already
        # committed. Any failure here — a filesystem error, an unencodable row
        # written by something other than this module, a bug in the renderer —
        # must degrade to `pending` and heal at the next trigger, never turn a
        # successful write into a 500.
        return False


def _project(conn: sqlite3.Connection, lesson: dict) -> str:
    """The response's `projection` field for one outcome."""
    return (
        PROJECTION_PROJECTED if reconcile_projection(conn, lesson)
        else PROJECTION_PENDING
    )


def _sweep_once(conn: sqlite3.Connection, lesson: dict) -> None:
    """Reconcile trigger (c): once per lesson per process, best effort."""
    lesson_id = lesson.get("id")
    if not isinstance(lesson_id, int):
        return
    with _swept_lock:
        if lesson_id in _swept:
            return
        _swept.add(lesson_id)
    reconcile_projection(conn, lesson)
