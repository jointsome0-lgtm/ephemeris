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

Two directions, one record (#136): a row is either the learner's answer to a
question the lesson asked (`kind` = `attempt`) or the learner asking the tutor
something (`kind` = `question`). Which one is derived here from the declared
question's manifest kind, never from the submission — the page has no channel
to classify its own write, and none is added.

No auto-agents: recording an attempt writes the row, the event, and the
projection line — it never wakes or notifies an agent (Check v1 is
save-only; a future agent subscribes to `lesson_attempt` events instead).

The lock registry, the idempotency-key grammar, the eligibility check, the
file lock, the publisher and the record skeleton are `projection`'s; this
module keeps the attempts record shape, its cursor sidecar and the
post-rebuild digest.
"""
from __future__ import annotations

import hashlib
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

from ..db import DATA_DIR, append_event
from . import bundle_schema, lessons, projection

PROJECTION_NAME = "attempts.jsonl"
PROJECTION_STATE_DIR = DATA_DIR / "attempt-projections"
PROJECTION_STATE_VERSION = 1
PROJECTION_STATE_MAX_BYTES = 4096
RECORD_KIND = "attempt"
# The other direction of the same channel (#136): the learner asking the tutor
# something, submitted through the SAME bridge op and endpoint. It is a value
# of the `kind` field §6.2 already defines, not a new field — a reader that
# predates it sees a record version it knows with a kind it does not, which is
# the one place the record shape left for exactly this.
RECORD_KIND_QUESTION = "question"
RECORD_VERSION = 1

MAX_ANSWER_BYTES = 32 * 1024   # §6.2: answer ≤ 32 KiB UTF-8
MAX_LINE_BYTES = 64 * 1024     # §6.2: whole projection line ≤ 64 KiB

PAGE_REV_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")

# Rate limit (D4 endpoint semantics): attempts are human-scale Check presses.
# Sliding window per lesson; a recording call consumes budget whether it
# records or refuses (so a misbehaving page cannot grind the manifest/hash
# path), except replay/conflict outcomes, which refund theirs (round 12).
# The window is in-process memory by design: the deployment model is ONE
# worker (loopback systemd unit) — an abuse damper, not a security boundary
# (docs/lesson-attempts-api.md documents the per-process scope).
RATE_WINDOW_SECONDS = 60.0
RATE_MAX_PER_WINDOW = 20

_monotonic = time.monotonic  # separable for tests
_rate_lock = threading.Lock()
_rate: dict[int, deque[float]] = {}


class AttemptError(Exception):
    """An attempt write was refused. `code` is the machine-readable reason
    (docs/lesson-attempts-api.md), `status` the HTTP status the route maps
    it to."""

    def __init__(self, code: str, status: int, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.status = status
        self.detail = detail


def _check_rate(lesson_id: int) -> float:
    """Charge one window slot; returns the charged stamp so outcomes that
    turn out not to be new writes can refund it (PR-57 round 12)."""
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
        return now


def _refund_rate(lesson_id: int, stamp: float | None) -> None:
    """PR-57 round 12: a request that resolves as a replay or a key
    conflict was not a new write — its slot is returned, so retries racing
    a slow original cannot starve the next real attempt. Refusals of NEW
    writes stay charged (the budget guards the manifest/hash path)."""
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
    """§6.2: `created_at` is UTC ISO-8601 — the same string is stored in the
    row and echoed by the projection, so authority and file never disagree.
    Microsecond precision so same-second attempts still sort by time and
    the new row lands last in the §6.1 order — the content-verified fast
    path in `_project_attempt` then almost never falls back to a rebuild."""
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
    if not projection.valid_idempotency_key(key):
        raise AttemptError(
            "invalid-idempotency-key", 400,
            f"idempotency_key must be 1-{projection.MAX_KEY_LEN} chars, no control characters",
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


def _record_kind(question: dict) -> str:
    """Which direction this record travels, from the RECORD-TIME manifest.

    The page has no channel to say what it is submitting and no say if it had
    one (the D2 trust model): the declared question's kind is the only input,
    and it is read once, here, then frozen in the row. A later re-kinding or
    retirement of the question therefore cannot reclassify records already
    written — the same reason `page_rev` is compared rather than adopted.
    """
    return (
        RECORD_KIND_QUESTION
        if question.get("kind") == bundle_schema.ASK_TUTOR_KIND
        else RECORD_KIND
    )


def _projection_record(row: dict) -> dict:
    """§6.2 record shape, exact field order."""
    return {
        # Pre-#136 rows carry no `kind`; the column defaults them to `attempt`,
        # and a row dict reaching here without the key at all (a caller's own
        # mapping) is an answer for the same reason.
        "kind": row.get("kind") or RECORD_KIND,
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


def _state_path(lesson: dict) -> Path:
    return PROJECTION_STATE_DIR / f"{projection.safe_uid(lesson)}.json"


def _file_seal(st: os.stat_result) -> dict:
    return {
        "dev": st.st_dev,
        "ino": st.st_ino,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "ctime_ns": st.st_ctime_ns,
    }


def _seal_matches(st: os.stat_result, seal: dict) -> bool:
    return (
        stat_module.S_ISREG(st.st_mode)
        and st.st_nlink == 1
        and all(
            isinstance(seal.get(name), int)
            and seal[name] == value
            for name, value in (
                ("dev", st.st_dev),
                ("ino", st.st_ino),
                ("size", st.st_size),
                ("mtime_ns", st.st_mtime_ns),
                ("ctime_ns", st.st_ctime_ns),
            )
        )
    )


def _read_state(lesson: dict) -> dict | None:
    try:
        fd = os.open(_state_path(lesson), projection.READ_FLAGS)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if (
            not stat_module.S_ISREG(st.st_mode)
            or st.st_nlink != 1
            or st.st_size > PROJECTION_STATE_MAX_BYTES
        ):
            return None
        chunks = []
        remaining = st.st_size + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) != st.st_size:
            return None
        state = json.loads(raw)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    finally:
        os.close(fd)
    if not isinstance(state, dict):
        return None
    cursor_id = state.get("cursor_id")
    cursor_attempt = state.get("cursor_attempt_id")
    tail_created = state.get("tail_created_at")
    tail_attempt = state.get("tail_attempt_id")
    seal = state.get("file")
    if (
        state.get("v") != PROJECTION_STATE_VERSION
        or state.get("lesson_uid") != lesson.get("uid")
        or isinstance(cursor_id, bool)
        or not isinstance(cursor_id, int)
        or cursor_id < 0
        or not isinstance(seal, dict)
        or (
            cursor_id == 0
            and (
                cursor_attempt is not None
                or tail_created is not None
                or tail_attempt is not None
            )
        )
        or (
            cursor_id > 0
            and (
                not isinstance(cursor_attempt, str)
                or
                not isinstance(tail_created, str)
                or not isinstance(tail_attempt, str)
            )
        )
    ):
        return None
    return state


def _write_state(lesson: dict, state: dict) -> None:
    projection.ensure_state_dir(PROJECTION_STATE_DIR)
    state_path = _state_path(lesson)
    data = (
        json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    if len(data) > PROJECTION_STATE_MAX_BYTES:
        raise OSError("projection state exceeds its fixed bound")
    dir_fd = os.open(PROJECTION_STATE_DIR, projection.DIRECTORY_FLAGS)
    try:
        projection.publish(dir_fd, state_path.name, data, prefix=".attempt-state-")
    finally:
        os.close(dir_fd)


def _projection_fd(lesson: dict, flags: int) -> int:
    return os.open(
        _projection_path(lesson),
        flags | os.O_NONBLOCK
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )


def _projection_matches_state(lesson: dict, state: dict) -> bool:
    try:
        fd = _projection_fd(lesson, os.O_RDONLY)
    except OSError:
        return False
    try:
        return _seal_matches(os.fstat(fd), state["file"])
    finally:
        os.close(fd)


def _cursor_matches_authority(
    conn: sqlite3.Connection, lesson: dict, state: dict
) -> bool:
    """Verify both durable cursor anchors against the current SQLite truth.

    This makes a sidecar left ahead by a database restore repair input instead
    of letting an empty ``id > cursor`` query bless stale projected rows.
    """
    if state["cursor_id"] == 0:
        return conn.execute(
            "SELECT 1 FROM lesson_attempts WHERE lesson_id = ? LIMIT 1",
            (lesson["id"],),
        ).fetchone() is None
    cursor_anchor = conn.execute(
        "SELECT 1 FROM lesson_attempts "
        "WHERE lesson_id = ? AND id = ? AND attempt_id = ?",
        (
            lesson["id"],
            state["cursor_id"],
            state["cursor_attempt_id"],
        ),
    ).fetchone()
    tail_anchor = conn.execute(
        "SELECT 1 FROM lesson_attempts "
        "WHERE lesson_id = ? AND created_at = ? AND attempt_id = ?",
        (
            lesson["id"],
            state["tail_created_at"],
            state["tail_attempt_id"],
        ),
    ).fetchone()
    return cursor_anchor is not None and tail_anchor is not None


def _rebuild_projection(conn: sqlite3.Connection, lesson: dict) -> None:
    """Idempotent reconcile (§6.1): rewrite the whole projection from the
    authority in bounded memory: rows are rendered directly from the SQLite
    cursor into the staged file, ascending created_at with ties by
    attempt_id, then atomically published. The rendered descriptor stays open
    across publication: the published bytes are hashed back against what was
    rendered, and the full post-replace seal must match the public name before
    the cursor is published. A crash or bundle-side rewrite therefore leaves
    missing or mismatched state and causes another safe rebuild, never a
    blind append."""
    path = _projection_path(lesson)
    cursor = {
        "cursor_id": 0,
        "cursor_attempt_id": None,
        "tail_created_at": None,
        "tail_attempt_id": None,
    }
    rendered_hash = hashlib.sha256()

    def render(fd: int) -> None:
        rows = conn.execute(
            "SELECT * FROM lesson_attempts WHERE lesson_id = ? "
            "ORDER BY created_at, attempt_id",
            (lesson["id"],),
        )
        try:
            for sqlite_row in rows:
                row = dict(sqlite_row)
                line = _projection_line(row).encode("utf-8")
                projection.write_all(fd, line)
                rendered_hash.update(line)
                if row["id"] > cursor["cursor_id"]:
                    cursor["cursor_id"] = row["id"]
                    cursor["cursor_attempt_id"] = row["attempt_id"]
                cursor["tail_created_at"] = row["created_at"]
                cursor["tail_attempt_id"] = row["attempt_id"]
        finally:
            rows.close()

    def verify(fd: int, published_st: os.stat_result) -> None:
        remaining = published_st.st_size
        offset = 0
        published_hash = hashlib.sha256()
        while remaining > 0:
            chunk = os.pread(fd, min(remaining, 1 << 20), offset)
            if not chunk:
                raise OSError("rebuilt projection changed during publication")
            published_hash.update(chunk)
            offset += len(chunk)
            remaining -= len(chunk)
        if published_hash.digest() != rendered_hash.digest():
            raise OSError("rebuilt projection changed during publication")

    dir_fd = os.open(path.parent, projection.DIRECTORY_FLAGS)
    try:
        published_st = projection.publish(
            dir_fd, PROJECTION_NAME, render, prefix=".attempts-", verify=verify
        )
    finally:
        os.close(dir_fd)
    state = {
        "v": PROJECTION_STATE_VERSION,
        "lesson_uid": lesson["uid"],
        **cursor,
        "file": _file_seal(published_st),
    }
    if not _seal_matches(os.lstat(path), state["file"]):
        raise OSError("rebuilt projection changed during publication")
    _write_state(lesson, state)


def reconcile_projection(conn: sqlite3.Connection, lesson: dict) -> bool:
    """Public reconcile entry point (ops/tests). Returns True when the
    projection now matches the authority, False on filesystem failure or
    unavailable private cross-process lock."""
    if conn.in_transaction:
        return False
    with projection.lesson_lock(lesson["slug"]):
        try:
            with projection.file_lock(PROJECTION_STATE_DIR, lesson):
                _rebuild_projection(conn, lesson)
        except (OSError, sqlite3.Error):
            return False
    return True


def _projection_is_current(conn: sqlite3.Connection, lesson: dict) -> bool:
    """Whether the published file already IS the authority's projection.

    The same three tests the write fast path makes before it appends — a
    readable sidecar, a matching descriptor seal, cursor anchors that still
    exist in SQLite — plus "nothing recorded after the cursor". Callers hold
    the projection lock, so a True here means the file needs no work at all.
    """
    state = _read_state(lesson)
    if (
        state is None
        or not _projection_matches_state(lesson, state)
        or not _cursor_matches_authority(conn, lesson, state)
    ):
        return False
    return conn.execute(
        "SELECT 1 FROM lesson_attempts WHERE lesson_id = ? AND id > ? LIMIT 1",
        (lesson["id"], state["cursor_id"]),
    ).fetchone() is None


def reconcile_projection_if_stale(
    conn: sqlite3.Connection, lesson: dict
) -> bool:
    """Reconcile, but pay for a rewrite only when there is one to make.

    The terminal-open trigger (#136) runs on a lesson whose projection is
    almost always already current, and `reconcile_projection` above rebuilds
    unconditionally — O(history) reads, serialization, fsync and a full-file
    hash on every open. This verifies first, under the same lock, and rebuilds
    exactly when the verification fails: a missing, mutated, truncated or
    behind file. Same return contract as `reconcile_projection`.
    """
    if conn.in_transaction:
        return False
    with projection.lesson_lock(lesson["slug"]):
        try:
            with projection.file_lock(PROJECTION_STATE_DIR, lesson):
                if not _projection_is_current(conn, lesson):
                    _rebuild_projection(conn, lesson)
        except (OSError, sqlite3.Error):
            return False
    return True


def _project_attempt(conn: sqlite3.Connection, lesson: dict, row: dict) -> bool:
    """Synchronous projection append, called under the lesson lock after the
    transaction committed. The fast path consults a private durable cursor,
    selects at most two authority rows after it, verifies the projection's
    descriptor seal and single-link guard, and renders at most one new line.
    Every repair path holds the same private per-lesson flock while streaming
    its authority snapshot and publishing both file and cursor, preserving the
    PR-57 round-10 stale-rebuild exclusion without a SQLite writer lock."""
    if conn.in_transaction:
        return False
    try:
        with projection.file_lock(PROJECTION_STATE_DIR, lesson):
            return _project_attempt_locked(conn, lesson, row)
    except (OSError, sqlite3.Error):
        return False


def _project_attempt_locked(
    conn: sqlite3.Connection, lesson: dict, row: dict
) -> bool:
    del row  # the committed authority, not caller memory, supplies file bytes
    state = _read_state(lesson)
    if (
        state is not None
        and _projection_matches_state(lesson, state)
        and _cursor_matches_authority(conn, lesson, state)
    ):
        unseen = conn.execute(
            "SELECT * FROM lesson_attempts "
            "WHERE lesson_id = ? AND id > ? ORDER BY id LIMIT 2",
            (lesson["id"], state["cursor_id"]),
        ).fetchall()
        if not unseen:
            return True
        candidate = dict(unseen[0])
        tail = (state["tail_created_at"], state["tail_attempt_id"])
        candidate_key = (candidate["created_at"], candidate["attempt_id"])
        if len(unseen) == 1 and (
            state["cursor_id"] == 0 or candidate_key > tail
        ):
            line = _projection_line(candidate).encode("utf-8")
            fd = -1
            appended = False
            try:
                fd = _projection_fd(lesson, os.O_RDWR | os.O_APPEND)
                before = os.fstat(fd)
                if not _seal_matches(before, state["file"]):
                    raise OSError("projection changed before append")
                expected_size = before.st_size + len(line)
                written_st = projection.write_all(fd, line)
                if (
                    not stat_module.S_ISREG(written_st.st_mode)
                    or written_st.st_nlink != 1
                    or (written_st.st_dev, written_st.st_ino)
                    != (before.st_dev, before.st_ino)
                    or written_st.st_size != expected_size
                ):
                    raise OSError("projection changed during append")
                os.fsync(fd)
                after = os.fstat(fd)
                if (
                    not _seal_matches(after, _file_seal(written_st))
                    or os.pread(fd, len(line), before.st_size) != line
                ):
                    raise OSError("projection changed after append")
                closing_fd = fd
                fd = -1
                os.close(closing_fd)
                name_st = os.lstat(_projection_path(lesson))
                if not _seal_matches(name_st, _file_seal(after)):
                    raise OSError("projection name changed during append")
                _write_state(lesson, {
                    "v": PROJECTION_STATE_VERSION,
                    "lesson_uid": lesson["uid"],
                    "cursor_id": candidate["id"],
                    "cursor_attempt_id": candidate["attempt_id"],
                    "tail_created_at": candidate["created_at"],
                    "tail_attempt_id": candidate["attempt_id"],
                    "file": _file_seal(name_st),
                })
                appended = True
            except OSError:
                appended = False
            finally:
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        appended = False
            if appended:
                return True
    try:
        _rebuild_projection(conn, lesson)
    except OSError:
        return False
    return True


# --- read model: what the s4 record panel joins against (D-S3-1) -------------
#
# Read-only, and deliberately outside everything above: no lock, no projection,
# no transaction. The write path, its cursor/seal state and `attempts.jsonl`
# are untouched by anything below.

# The answer is the learner's own text (≤ 32 KiB). The panel shows enough of it
# to recognise which answer a verdict judged; the whole of it stays in the DB,
# the export and `attempts.jsonl` rather than in every /learn render.
PANEL_ANSWER_CHARS = 400

# One row per question — the newest by rowid, which is the insertion authority
# the same way `seq` is for assessments (`created_at` can tie at microsecond
# resolution, rowids cannot). The grouping seeks through
# `idx_attempts_lesson_question`.
#
# The excerpt is bounded in SQLite, not in Python: the number of questions a
# lesson has ever been answered on is not bounded by the manifest (a durable id
# that leaves it keeps its attempts), so selecting whole answer bodies would
# materialize up to 32 KiB per question to show 400 characters of each.
#
# Bounded in BYTES, through a BLOB cast, because SQLite's TEXT string functions
# are NUL-terminated: `length(answer)` counts only up to the first U+0000 and
# `substr(answer, ...)` returns only the run before it. An answer is validated
# as UTF-8 within a byte cap and nothing rejects a NUL inside it, so a
# character-wise excerpt would silently render such an answer as its first few
# characters, with no truncation marker. The BLOB functions are byte-exact and
# NUL-blind; the budget covers the widest UTF-8 encoding of the character bound
# so the requested characters are always present, and Python does the decoding
# and the character-wise cut.
PANEL_ANSWER_BYTES = PANEL_ANSWER_CHARS * 4 + 3

_LATEST_PER_QUESTION_SQL = (
    "SELECT attempt_id, question_id, page_id, page_rev, stale, kind, created_at, "
    "       substr(CAST(answer AS BLOB), 1, ?) AS answer_head, "
    "       length(CAST(answer AS BLOB)) AS answer_bytes "
    "FROM lesson_attempts WHERE id IN "
    "(SELECT MAX(id) FROM lesson_attempts WHERE lesson_id = ? GROUP BY question_id) "
    "ORDER BY id"
)


def _panel_attempt_view(row: sqlite3.Row) -> dict:
    head = row["answer_head"]
    # 'ignore' drops the partial character the byte bound may have split; the
    # bound is wide enough that a whole character is never lost from the cut.
    text = head.decode("utf-8", "ignore") if isinstance(head, bytes) else str(head)
    excerpt = text[:PANEL_ANSWER_CHARS]
    return {
        "attempt_id": row["attempt_id"],
        "question_id": row["question_id"],
        "page_id": row["page_id"],
        "page_rev": row["page_rev"],
        "answer": excerpt,
        "answer_truncated": (
            len(excerpt) < len(text) or row["answer_bytes"] > len(head or b"")
        ),
        "stale": bool(row["stale"]),
        # What the record IS, straight from the authority column — the panel
        # and the generated STATE both need a question to the tutor to read as
        # one even after the manifest stopped declaring it.
        "kind": row["kind"] or RECORD_KIND,
        "created_at": row["created_at"],
    }


def latest_is_question(attempt: dict | None) -> bool:
    """Whether a panel attempt view is a question the learner asked the tutor.

    One owner for the test, because three readers ask it (the Record panel, the
    generated STATE, and the tests).
    """
    return bool(attempt) and attempt.get("kind") == RECORD_KIND_QUESTION


def row_is_question(attempt: dict | None, declared_kind: str | None) -> bool:
    """Which direction a displayed row travels, over BOTH of its inputs.

    The recorded kind decides whenever there is a record, and the manifest is
    consulted only for a control nobody has used yet. Not an OR (PR #149): a
    question re-kinded to `ask_tutor` while keeping its durable id would
    otherwise relabel the ordinary answers already recorded under it as things
    the learner asked — and the mirror case, a control re-kinded away, would
    turn the learner's question back into a wrong answer. The record-time
    direction is frozen with the record, so a reader must not re-derive it.
    """
    if attempt is not None:
        return latest_is_question(attempt)
    return declared_kind == bundle_schema.ASK_TUTOR_KIND


# How many open questions the generated STATE names before it stops counting
# and says "and N more". A tutor who owes more than this has a session's work
# in front of them either way, and the brief stays readable.
OPEN_QUESTIONS_SHOWN = 12

# How much of each question travels INTO the brief. The tutor is told to answer
# these before anything else, so the brief carries the text itself rather than
# only pointing at `attempts.jsonl` (review round 2): the projection is a
# repairable copy — a write can land `pending`, and the reconcile pass runs
# after the brief is written — while the authority column is always there. The
# full text stays one file away for a question longer than this.
STATE_QUESTION_CHARS = 400
STATE_QUESTION_BYTES = STATE_QUESTION_CHARS * 4 + 3


def _open_question_view(row: sqlite3.Row) -> dict:
    head = row["answer_head"]
    # Same cut as the panel view: 'ignore' drops the partial character the byte
    # bound may have split, and the newlines are collapsed because this lands
    # inside one Markdown bullet.
    text = head.decode("utf-8", "ignore") if isinstance(head, bytes) else str(head)
    excerpt = " ".join(text[:STATE_QUESTION_CHARS].split())
    return {
        "attempt_id": row["attempt_id"],
        "question_id": row["question_id"],
        "created_at": row["created_at"],
        "asked": excerpt,
        "asked_truncated": (
            len(text) > STATE_QUESTION_CHARS
            or row["answer_bytes"] > len(head or b"")
        ),
    }


def open_questions(
    conn: sqlite3.Connection,
    lesson_id: int,
    reviewed_attempt_ids: set[str],
    limit: int = OPEN_QUESTIONS_SHOWN,
) -> tuple[list[dict], int]:
    """Every question to the tutor that no active review answers, oldest first.

    NOT the latest-per-question read above (PR #149): one ask-the-tutor control
    is asked through again and again, so grouping by question would let a reply
    to today's question silently close yesterday's. The debt is per ATTEMPT —
    the same grain a `review` names — so nothing the learner asked can be
    abandoned by being asked twice.

    Bounded without bounding the truth: the cursor is streamed over narrow
    columns — the question text itself is cut to `STATE_QUESTION_BYTES` by
    SQLite, not read whole — and at most `limit` rows are ever retained, while
    the rest are counted rather than listed. So a lesson with a long question
    history costs the walk, never the memory, and STATE still reports the true
    size of the debt. Returns (rows, total_open).
    """
    open_rows: list[dict] = []
    total = 0
    rows = conn.execute(
        "SELECT attempt_id, question_id, created_at, "
        "       substr(CAST(answer AS BLOB), 1, ?) AS answer_head, "
        "       length(CAST(answer AS BLOB)) AS answer_bytes "
        "FROM lesson_attempts WHERE lesson_id = ? AND kind = ? ORDER BY id",
        (STATE_QUESTION_BYTES, lesson_id, RECORD_KIND_QUESTION),
    )
    try:
        for row in rows:
            if row["attempt_id"] in reviewed_attempt_ids:
                continue
            total += 1
            if len(open_rows) < limit:
                open_rows.append(_open_question_view(row))
    finally:
        rows.close()
    return open_rows, total


def lesson_attempt_summary(conn: sqlite3.Connection, lesson_id: int) -> dict:
    """How many attempts a lesson has recorded, and the latest attempt per
    question — the join the record panel hangs verdicts on.

    `total` is every row (it backs the watermark and the panel's "is there
    anything here" test); `questions` is how many of them travel the other way,
    so a counter the learner reads can say "attempts" about attempts only
    (#136 review round 3). Both come from one statement — two COUNTs over the
    same scan cost less than a second query and cannot disagree.
    """
    total, questions = conn.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE kind = ?) "
        "FROM lesson_attempts WHERE lesson_id = ?",
        (RECORD_KIND_QUESTION, lesson_id),
    ).fetchone()
    latest = {}
    rows = conn.execute(
        _LATEST_PER_QUESTION_SQL, (PANEL_ANSWER_BYTES, lesson_id)
    ).fetchall()
    for row in rows:
        latest[row["question_id"]] = _panel_attempt_view(row)
    # The version of this half of the panel, for readers that must notice the
    # rows CHANGE and not only that the total went up. Rows here are
    # insert-only — a re-answer is a new attempt, never an edit — so `MAX(id)`
    # is an exact stamp, the same argument the assessment watermark rests on.
    watermark = conn.execute(
        "SELECT MAX(id) FROM lesson_attempts WHERE lesson_id = ?", (lesson_id,)
    ).fetchone()[0]
    return {
        "total": total,
        "questions": questions,
        "answers": total - questions,
        "latest_by_question": latest,
        "watermark": int(watermark or 0),
    }


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
            # What the record IS, so the confirmation the learner reads can
            # say "question" for a question — on the replay too, which is the
            # response a retried submission actually sees (#136).
            "kind": existing["kind"] or RECORD_KIND,
        }
    raise AttemptError(
        "idempotency-conflict", 409,
        "idempotency_key was already used for a different question/page",
    )


def record_attempt(conn: sqlite3.Connection, lesson: dict, payload: dict) -> dict:
    """Record one attempt for `lesson` (a lessons service view dict).

    Returns the response body fields for the D4 endpoint:
      recorded  -> {result, attempt_id, stale, kind, attempt_number, projection}
      duplicate -> {result, attempt_id, stale, kind}
    Refusals raise AttemptError with a distinct code per
    docs/lesson-attempts-api.md.

    The skeleton is `projection.record`: §6.3 replay precedes every record-time
    refusal, the rate limit included, so a client retry learns its attempt_id
    even when the manifest has since rejected the bundle or the window is
    exhausted. The window slot is charged in `validate` and refunded for every
    outcome that was not a new write (a replay or a key conflict, round 12);
    refusals of new writes stay charged.
    """
    submission = _clean_submission(payload)
    rate_stamp: float | None = None

    def replay() -> dict | None:
        return _replay_or_conflict(conn, lesson, submission)

    def validate() -> tuple[bool, str]:
        nonlocal rate_stamp
        rate_stamp = _check_rate(lesson["id"])
        read = lessons.read_bundle(lesson)
        projection.require_eligible(
            read, AttemptError, "attempts-unavailable", "attempt"
        )
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
        stale = _derive_stale(
            lesson, read, question, submission["page_id"], submission["page_rev"]
        )
        kind = _record_kind(question)
        # §6.2 whole-line bound is a writer duty: an answer that fits the
        # 32 KiB budget can still escape past 64 KiB (newlines, quotes). The
        # ids and the timestamp have a fixed serialized width, so these
        # placeholders make the probe exact.
        probe = _projection_line({
            **submission,
            "attempt_id": "0" * 36,
            "event_uuid": "0" * 36,
            "lesson_uid": lesson["uid"],
            "created_at": _utc_now_iso(),
            "stale": stale,
            "kind": kind,
        })
        if len(probe.encode("utf-8")) > MAX_LINE_BYTES:
            raise AttemptError(
                "answer-too-large", 400,
                f"projection record exceeds {MAX_LINE_BYTES} bytes",
            )
        return stale, kind

    def write(prepared: tuple[bool, str]) -> tuple[dict, int]:
        stale, kind = prepared
        attempt_id = str(uuid4())
        created_at = _utc_now_iso()
        event_uuid = append_event(conn, "lesson_attempt", {
            # §8 echo policy: identity and the record itself — never
            # title/path/step/concepts/pages.
            "lesson_uid": lesson["uid"],
            "lesson_id": lesson["id"],
            "slug": lesson["slug"],
            "attempt_id": attempt_id,
            "page_id": submission["page_id"],
            "question_id": submission["question_id"],
            "page_rev": submission["page_rev"],
            "answer": submission["answer"],
            "stale": stale,
            "kind": kind,
        })
        insert_cursor = conn.execute(
            "INSERT INTO lesson_attempts "
            "(attempt_id, event_uuid, lesson_id, lesson_uid, "
            " idempotency_key, page_id, question_id, page_rev, "
            " answer, stale, kind, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attempt_id, event_uuid, lesson["id"], lesson["uid"],
                submission["idempotency_key"],
                submission["page_id"], submission["question_id"],
                submission["page_rev"], submission["answer"],
                int(stale), kind, created_at,
            ),
        )
        # attempt_number is the 1-based number of THIS attempt: counted inside
        # the write transaction, while SQLite still excludes competing
        # processes — after commit a sibling process could inflate the count.
        attempt_number = conn.execute(
            "SELECT COUNT(*) FROM lesson_attempts "
            "WHERE lesson_id = ? AND question_id = ?",
            (lesson["id"], submission["question_id"]),
        ).fetchone()[0]
        row = {
            "id": insert_cursor.lastrowid,
            "attempt_id": attempt_id,
            "event_uuid": event_uuid,
            "lesson_uid": lesson["uid"],
            "page_id": submission["page_id"],
            "question_id": submission["question_id"],
            "page_rev": submission["page_rev"],
            "answer": submission["answer"],
            "created_at": created_at,
            "stale": stale,
            "kind": kind,
        }
        return row, attempt_number

    def project(committed: tuple[dict, int]) -> dict:
        row, attempt_number = committed
        return {
            "result": "recorded",
            "attempt_id": row["attempt_id"],
            "stale": row["stale"],
            "kind": row["kind"],
            "attempt_number": attempt_number,
            "projection": (
                "projected" if _project_attempt(conn, lesson, row) else "pending"
            ),
        }

    try:
        result = projection.record(
            conn, lesson, AttemptError,
            replay=replay, validate=validate, write=write, project=project,
        )
    except AttemptError as exc:
        if exc.code == "idempotency-conflict":
            _refund_rate(lesson["id"], rate_stamp)
        raise
    if result["result"] == "duplicate":
        _refund_rate(lesson["id"], rate_stamp)
    return result
