"""Lesson attempt recording — the D4 backend of docs/learn-bundle-spec.md §6.

Authority and projection (§6.1): the `lesson_attempts` SQLite table is the
authority; each row is written in ONE transaction with its `lesson_attempt`
ledger event. `attempts.jsonl` at the bundle root is a synchronous, app-owned
projection so the study agent reads attempts as a plain file — it may lag or
be lost, and an idempotent reconcile pass rebuilds it from SQLite. A
projection failure never fails the authoritative write; the response
distinguishes recorded+projected / recorded+projection-pending / duplicate,
with `stale` as a flag on the record (§6.3 — late data is never dropped).

Trust model (D2 review gate, lesson-bridge-abi.md §4): possession of a bridge
port is NOT authority. Every write here re-validates against the record-time
manifest — the question must be declared (§4.3/§6.4), the lesson uid comes
from the DB row (never the client), and `stale` is derived server-side by
comparing the submitted load-time identity against the current binding and
the current page bytes on disk. The client supplies only: question_id,
page_id, page_rev (what it saw at load time), answer, idempotency_key.

No auto-agents: recording an attempt writes the row, the event, and the
projection line — it never wakes or notifies an agent (Check v1 is
save-only; a future agent subscribes to `lesson_attempt` events instead).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat as stat_module
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..db import append_event
from . import bundle_schema, lessons

PROJECTION_NAME = "attempts.jsonl"
RECORD_KIND = "attempt"
RECORD_VERSION = 1

MAX_ANSWER_BYTES = 32 * 1024   # §6.2: answer ≤ 32 KiB UTF-8
MAX_LINE_BYTES = 64 * 1024     # §6.2: whole projection line ≤ 64 KiB
MAX_KEY_LEN = 128              # §6.3: opaque client token ≤ 128 chars

PAGE_REV_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# Rate limit (D4 endpoint semantics): attempts are human-scale Check presses.
# Sliding window per lesson; every recording call consumes budget, valid or
# not, so a misbehaving page cannot grind the manifest/hash path either.
RATE_WINDOW_SECONDS = 60.0
RATE_MAX_PER_WINDOW = 20

_monotonic = time.monotonic  # separable for tests
_rate_lock = threading.Lock()
_rate: dict[int, deque[float]] = {}

# One lock per bundle: the duplicate check, the transactional insert, and the
# projection append serialize per lesson, so the projection's line count can
# be compared against the table without racing sibling writers in-process.
# RLock because reconcile is also a public entry point that takes it itself.
_bundle_locks_lock = threading.Lock()
_bundle_locks: dict[str, threading.RLock] = {}


class AttemptError(Exception):
    """An attempt write was refused. `code` is the machine-readable reason
    (docs/lesson-attempts-api.md), `status` the HTTP status the route maps
    it to."""

    def __init__(self, code: str, status: int, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.status = status
        self.detail = detail


def _bundle_lock(slug: str) -> threading.RLock:
    with _bundle_locks_lock:
        lock = _bundle_locks.get(slug)
        if lock is None:
            lock = _bundle_locks[slug] = threading.RLock()
        return lock


def _reset_rate_limit() -> None:
    """Test hook: forget all rate-limit state."""
    with _rate_lock:
        _rate.clear()


def _check_rate(lesson_id: int) -> None:
    now = _monotonic()
    with _rate_lock:
        window = _rate.setdefault(lesson_id, deque())
        while window and now - window[0] > RATE_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= RATE_MAX_PER_WINDOW:
            retry = max(1, int(RATE_WINDOW_SECONDS - (now - window[0])) + 1)
            raise AttemptError(
                "rate-limited", 429, f"retry after ~{retry}s"
            )
        window.append(now)


def _utc_now_iso() -> str:
    """§6.2: `created_at` is UTC ISO-8601 — the same string is stored in the
    row and echoed by the projection, so authority and file never disagree.
    Microsecond precision so same-second attempts still sort by time and the
    projection fast path (append at EOF) almost always matches the §6.1
    rebuild order; `_tail_precedes` guards the residual collisions."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _utf8_len(value: str) -> int | None:
    """UTF-8 byte length, or None when the string is not encodable (lone
    surrogates from JSON \\uD800 escapes) — such a value could never be
    written to the projection file or the ledger."""
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _clean_submission(payload: dict) -> dict:
    """Validate the client-supplied submission fields by grammar only —
    nothing here consults the manifest. Unknown payload fields are ignored
    (forward compatibility, same stance as the bridge ABI)."""
    question_id = payload.get("question_id")
    if not isinstance(question_id, str) or not bundle_schema.QUESTION_ID_RE.match(question_id):
        raise AttemptError("invalid-question-id", 400, "question_id must match q_[a-z0-9]{4,32}")
    page_id = payload.get("page_id")
    if not isinstance(page_id, str) or not bundle_schema.PAGE_ID_RE.match(page_id):
        raise AttemptError("invalid-page-id", 400, "page_id must match pg_[a-z0-9]{4,32}")
    page_rev = payload.get("page_rev")
    if not isinstance(page_rev, str) or not PAGE_REV_RE.match(page_rev):
        raise AttemptError("invalid-page-rev", 400, "page_rev must be sha256:<64 lowercase hex>")
    key = payload.get("idempotency_key")
    if (
        not isinstance(key, str)
        or not 1 <= len(key) <= MAX_KEY_LEN
        or _utf8_len(key) is None
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in key)
    ):
        raise AttemptError(
            "invalid-idempotency-key", 400,
            f"idempotency_key must be 1-{MAX_KEY_LEN} chars, no control characters",
        )
    answer = payload.get("answer")
    if not isinstance(answer, str):
        raise AttemptError("invalid-answer", 400, "answer must be a string")
    answer_bytes = _utf8_len(answer)
    if answer_bytes is None:
        raise AttemptError("invalid-answer", 400, "answer is not valid UTF-8 text")
    if answer_bytes > MAX_ANSWER_BYTES:
        raise AttemptError(
            "answer-too-large", 400, f"answer exceeds {MAX_ANSWER_BYTES} UTF-8 bytes"
        )
    return {
        "question_id": question_id,
        "page_id": page_id,
        "page_rev": page_rev,
        "idempotency_key": key,
        "answer": answer,
    }


def _require_eligible(read: bundle_schema.ManifestRead) -> None:
    """Attempt writes are refused for a rejected manifest (§9.2), for an
    identity mismatch (§3 — resolved explicitly, never as a write side
    effect), and for every profile without the attempts affordance (§5:
    legacy-display and v1 carry none). `bridge_eligible` is exactly that
    predicate minus the mismatch, which forces legacy anyway — the split
    below only exists to give each refusal its own distinct code."""
    if read.rejected:
        raise AttemptError(
            "manifest-rejected", 409,
            "the lesson manifest is rejected; attempt writes are refused",
        )
    if "identity-mismatch" in read.codes():
        raise AttemptError(
            "identity-mismatch", 409,
            "manifest lesson_uid differs from the DB uid; resolve the mismatch first",
        )
    if not read.bridge_eligible:
        raise AttemptError(
            "attempts-unavailable", 409,
            "this lesson's manifest/profile grants no attempt affordance",
        )


def _derive_stale(
    lesson: dict,
    read: bundle_schema.ManifestRead,
    question: dict,
    page_id: str,
    page_rev: str,
) -> bool:
    """§6.4 record-time staleness, server-derived. The submitted load-time
    identity is only compared, never trusted: a question bound to a different
    page than submitted, changed page bytes, or an unknowable current
    revision (file missing/unreadable/symlinked) all record `stale`."""
    bound_page = question["page"]
    if page_id != bound_page:
        return True
    path = next((p["path"] for p in read.pages if p["id"] == bound_page), None)
    if path is None:  # unreachable: questions validate against surviving pages
        return True
    digest = lessons.hash_bundle_page(lesson, path)
    if digest is None:
        return True  # current revision unknowable — conservative flag (§6.4)
    return f"sha256:{digest}" != page_rev


def _projection_record(row: dict) -> dict:
    """§6.2 record shape, exact field order."""
    return {
        "kind": RECORD_KIND,
        "v": RECORD_VERSION,
        "attempt_id": row["attempt_id"],
        "event_uuid": row["event_uuid"],
        "lesson_uid": row["lesson_uid"],
        "page_id": row["page_id"],
        "question_id": row["question_id"],
        "page_rev": row["page_rev"],
        "answer": row["answer"],
        "created_at": row["created_at"],
        "stale": bool(row["stale"]),
    }


def _projection_line(row: dict) -> str:
    return json.dumps(_projection_record(row), ensure_ascii=False) + "\n"


def _projection_path(lesson: dict) -> Path:
    return lessons.LESSONS_DIR / lesson["slug"] / PROJECTION_NAME


def _read_all(fd: int, size: int) -> bytes:
    """Whole-file positional read — the append offset is untouched. Bounded
    by the attempt volume itself: per-lesson, human-scale, §6.2-capped
    lines."""
    chunks = []
    offset = 0
    while offset < size:
        chunk = os.pread(fd, 1 << 16, offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _rebuild_projection(conn: sqlite3.Connection, lesson: dict) -> None:
    """Idempotent reconcile (§6.1): rewrite the whole projection from the
    authority — deduped by construction (one line per row), ascending
    created_at with ties by attempt_id, atomically replaced. Heals missing,
    truncated, torn, and planted-special-file states alike (os.replace
    swaps the name without ever opening the old one)."""
    rows = conn.execute(
        "SELECT * FROM lesson_attempts WHERE lesson_id = ? "
        "ORDER BY created_at, attempt_id",
        (lesson["id"],),
    ).fetchall()
    text = "".join(_projection_line(dict(row)) for row in rows)
    bundle_schema.atomic_write_text(_projection_path(lesson), text)


def reconcile_projection(conn: sqlite3.Connection, lesson: dict) -> bool:
    """Public reconcile entry point (ops/tests). Returns True when the
    projection now matches the authority, False on filesystem failure."""
    with _bundle_lock(lesson["slug"]):
        try:
            _rebuild_projection(conn, lesson)
        except OSError:
            return False
    return True


def _project_attempt(conn: sqlite3.Connection, lesson: dict, row: dict) -> bool:
    """Synchronous projection append, called under the bundle lock after the
    transaction committed. Fast path (PR-57 round 6: content-verified, not
    count-heuristic): the new row must sort last in the §6.1 authority
    order and the file's bytes must equal the rebuild of every earlier row
    exactly — then one O_APPEND + fsync line lands the same content the
    rebuild would produce. Anything else — torn tail, missing/misordered/
    foreign lines, a clock step backwards, a planted symlink/FIFO at the
    name — falls back to the reconcile rebuild. Returns False (projection
    pending) only when the filesystem refuses both; the authoritative
    write is already durable either way."""
    rows = conn.execute(
        "SELECT * FROM lesson_attempts WHERE lesson_id = ? "
        "ORDER BY created_at, attempt_id",
        (lesson["id"],),
    ).fetchall()
    appended = False
    if rows and rows[-1]["attempt_id"] == row["attempt_id"]:
        # The appended bytes come from the authority row, so a verified
        # prefix + this line is byte-identical to a full rebuild.
        line = _projection_line(dict(rows[-1]))
        prefix = "".join(
            _projection_line(dict(r)) for r in rows[:-1]
        ).encode("utf-8")
        try:
            fd = os.open(
                _projection_path(lesson),
                os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK
                | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
        except OSError:
            fd = -1
        if fd >= 0:
            try:
                st = os.fstat(fd)
                if (
                    stat_module.S_ISREG(st.st_mode)
                    and st.st_size == len(prefix)
                    and _read_all(fd, st.st_size) == prefix
                ):
                    # write(2) may land short (ENOSPC, rlimits): loop until
                    # the whole line is down — a partial append must never
                    # report `projected` (the rebuild below replaces the
                    # torn tail it leaves behind).
                    data = memoryview(line.encode("utf-8"))
                    while data:
                        n = os.write(fd, data)
                        if n <= 0:
                            raise OSError("short write on attempts.jsonl")
                        data = data[n:]
                    os.fsync(fd)
                    appended = True
            except OSError:
                appended = False
            finally:
                try:
                    os.close(fd)
                except OSError:
                    # close(2) can surface a delayed write error (NFS/FUSE
                    # ENOSPC/EIO). The fsync'd append may be moot then —
                    # count it as not appended and let the rebuild path
                    # decide; a projection problem must never fail the
                    # durable write.
                    appended = False
    if appended:
        return True
    try:
        _rebuild_projection(conn, lesson)
    except OSError:
        return False
    return True


def _replay_or_conflict(
    conn: sqlite3.Connection, lesson: dict, submission: dict
) -> dict | None:
    """Known-key handling (§6.3): a replay of the same submission returns the
    original attempt untouched; the same key with a different question/page
    is a client bug — distinct conflict, never coalesced. None = fresh key."""
    existing = conn.execute(
        "SELECT * FROM lesson_attempts WHERE lesson_id = ? AND idempotency_key = ?",
        (lesson["id"], submission["idempotency_key"]),
    ).fetchone()
    if existing is None:
        return None
    if (
        existing["question_id"] == submission["question_id"]
        and existing["page_id"] == submission["page_id"]
    ):
        return {
            "result": "duplicate",
            "attempt_id": existing["attempt_id"],
            "stale": bool(existing["stale"]),
        }
    raise AttemptError(
        "idempotency-conflict", 409,
        "idempotency_key was already used for a different question/page",
    )


def record_attempt(conn: sqlite3.Connection, lesson: dict, payload: dict) -> dict:
    """Record one attempt for `lesson` (a lessons service view dict).

    Returns the response body fields for the D4 endpoint:
      recorded  -> {result, attempt_id, stale, attempt_number, projection}
      duplicate -> {result, attempt_id, stale}
    Refusals raise AttemptError with a distinct code per
    docs/lesson-attempts-api.md."""
    submission = _clean_submission(payload)
    _check_rate(lesson["id"])

    # §6.3 replay precedes every record-time refusal (PR-57 round 1): the
    # original write is already durable, so a client retry must learn its
    # attempt_id even when the manifest has since rejected the bundle or
    # retired the question — validation below governs only NEW writes.
    with _bundle_lock(lesson["slug"]):
        replay = _replay_or_conflict(conn, lesson, submission)
    if replay is not None:
        return replay

    try:
        read = lessons.read_bundle(lesson)
        _require_eligible(read)
        if not lesson.get("uid"):  # unreachable post-v11 backfill; fail closed
            raise AttemptError("attempts-unavailable", 409, "lesson has no uid")

        question = next(
            (q for q in read.questions if q["id"] == submission["question_id"]), None
        )
        if question is None:
            # §4.3/§6.4: identity that no longer exists (or never did) is the
            # one thing that rejects — distinct from staleness, which records.
            raise AttemptError(
                "unknown-question", 422,
                "question_id is not declared in the lesson manifest",
            )
    except AttemptError:
        # PR-57 round 2: a retry racing its own original request (timeout
        # resend) can see the key uncommitted at the early check above, then
        # hit a refusal here after the original committed. The durable
        # outcome still wins (§6.3) — re-check before refusing.
        with _bundle_lock(lesson["slug"]):
            replay = _replay_or_conflict(conn, lesson, submission)
        if replay is not None:
            return replay
        raise
    stale = _derive_stale(
        lesson, read, question, submission["page_id"], submission["page_rev"]
    )

    with _bundle_lock(lesson["slug"]):
        # Re-check under the lock: another in-process writer may have landed
        # the same key between the early replay check and here.
        replay = _replay_or_conflict(conn, lesson, submission)
        if replay is None:
            attempt_id = str(uuid4())
            created_at = _utc_now_iso()
            row = {
                "attempt_id": attempt_id,
                "lesson_uid": lesson["uid"],
                "page_id": submission["page_id"],
                "question_id": submission["question_id"],
                "page_rev": submission["page_rev"],
                "answer": submission["answer"],
                "created_at": created_at,
                "stale": stale,
            }
            # §6.2 whole-line bound is a writer duty: an answer that fits the
            # 32 KiB budget can still escape past 64 KiB (newlines, quotes).
            # The event uuid is minted in the transaction below but has a
            # fixed serialized width, so this placeholder length is exact.
            probe = _projection_line({**row, "event_uuid": "0" * 36})
            if len(probe.encode("utf-8")) > MAX_LINE_BYTES:
                raise AttemptError(
                    "answer-too-large", 400,
                    f"projection record exceeds {MAX_LINE_BYTES} bytes",
                )
            try:
                with conn:
                    # ONE transaction (§6.1): the event and the row commit or
                    # roll back together; the row stores the event's uuid so
                    # the projection can echo it (§6.2).
                    event_uuid = append_event(conn, "lesson_attempt", {
                        # §8 echo policy: identity and the record itself —
                        # never title/path/step/concepts/pages.
                        "lesson_uid": lesson["uid"],
                        "lesson_id": lesson["id"],
                        "slug": lesson["slug"],
                        "attempt_id": attempt_id,
                        "page_id": submission["page_id"],
                        "question_id": submission["question_id"],
                        "page_rev": submission["page_rev"],
                        "answer": submission["answer"],
                        "stale": stale,
                    })
                    conn.execute(
                        "INSERT INTO lesson_attempts "
                        "(attempt_id, event_uuid, lesson_id, lesson_uid, "
                        " idempotency_key, page_id, question_id, page_rev, "
                        " answer, stale, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            attempt_id, event_uuid, lesson["id"], lesson["uid"],
                            submission["idempotency_key"],
                            submission["page_id"], submission["question_id"],
                            submission["page_rev"], submission["answer"],
                            int(stale), created_at,
                        ),
                    )
                    # attempt_number is the 1-based number of THIS attempt:
                    # counted inside the write transaction, while SQLite
                    # still excludes competing processes — after commit a
                    # sibling process could inflate the count (PR-57 r7).
                    attempt_number = conn.execute(
                        "SELECT COUNT(*) FROM lesson_attempts "
                        "WHERE lesson_id = ? AND question_id = ?",
                        (lesson["id"], submission["question_id"]),
                    ).fetchone()[0]
            except sqlite3.IntegrityError:
                # Same idempotency key landed from another PROCESS (a stale
                # second server; in-process writers serialize on the bundle
                # lock) — answer with its outcome instead of a 500.
                replay = _replay_or_conflict(conn, lesson, submission)
                if replay is None:
                    raise
            else:
                projected = _project_attempt(
                    conn, lesson, {**row, "event_uuid": event_uuid}
                )
                return {
                    "result": "recorded",
                    "attempt_id": attempt_id,
                    "stale": stale,
                    "attempt_number": attempt_number,
                    "projection": "projected" if projected else "pending",
                }
        return replay
