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

import fcntl
import json
import os
import re
import sqlite3
import stat as stat_module
import tempfile
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..db import DATA_DIR, append_event
from . import bundle_schema, lessons

PROJECTION_NAME = "attempts.jsonl"
PROJECTION_STATE_DIR = DATA_DIR / "attempt-projections"
PROJECTION_STATE_VERSION = 1
PROJECTION_STATE_MAX_BYTES = 4096
RECORD_KIND = "attempt"
RECORD_VERSION = 1

MAX_ANSWER_BYTES = 32 * 1024   # §6.2: answer ≤ 32 KiB UTF-8
MAX_LINE_BYTES = 64 * 1024     # §6.2: whole projection line ≤ 64 KiB
MAX_KEY_LEN = 128              # §6.3: opaque client token ≤ 128 chars

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


def _state_paths(lesson: dict) -> tuple[Path, Path]:
    uid = lesson.get("uid")
    if not isinstance(uid, str) or bundle_schema.UUID_RE.match(uid) is None:
        raise OSError("lesson has no safe projection-state identity")
    return (
        PROJECTION_STATE_DIR / f"{uid}.json",
        PROJECTION_STATE_DIR / f"{uid}.lock",
    )


def _ensure_state_dir() -> None:
    PROJECTION_STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(PROJECTION_STATE_DIR)
    if not stat_module.S_ISDIR(st.st_mode):
        raise OSError("projection state root is not a directory")


@contextmanager
def _projection_file_lock(lesson: dict):
    """Private per-lesson cross-process exclusion for projection work.

    PR-57 round 10 used SQLite's database-wide writer lock. The immutable
    lesson uid now names a lock outside the agent-writable bundle, so only
    sibling projection work serializes; unrelated SQLite writers remain free.
    """
    _ensure_state_dir()
    _, lock_path = _state_paths(lesson)
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
            raise OSError("short write on projection file")
        view = view[count:]


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
    state_path, _ = _state_paths(lesson)
    try:
        fd = os.open(
            state_path,
            os.O_RDONLY | os.O_NONBLOCK
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
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
            and (tail_created is not None or tail_attempt is not None)
        )
        or (
            cursor_id > 0
            and (
                not isinstance(tail_created, str)
                or not isinstance(tail_attempt, str)
            )
        )
    ):
        return None
    return state


def _write_state(lesson: dict, state: dict) -> None:
    _ensure_state_dir()
    state_path, _ = _state_paths(lesson)
    data = (
        json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    if len(data) > PROJECTION_STATE_MAX_BYTES:
        raise OSError("projection state exceeds its fixed bound")
    fd, tmp_name = tempfile.mkstemp(
        dir=PROJECTION_STATE_DIR, prefix=".attempt-state-"
    )
    try:
        try:
            _write_all(fd, data)
            os.fsync(fd)
            os.close(fd)
            fd = -1
        except BaseException:
            if fd >= 0:
                os.close(fd)
                fd = -1
            raise
        os.replace(tmp_name, state_path)
        parent_fd = os.open(
            PROJECTION_STATE_DIR,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


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


def _rebuild_projection(conn: sqlite3.Connection, lesson: dict) -> None:
    """Idempotent reconcile (§6.1): rewrite the whole projection from the
    authority in bounded memory: rows are rendered directly from the SQLite
    cursor into one fsynced temporary file, ascending created_at with ties by
    attempt_id, then atomically replaced. The rendered descriptor stays open
    across publication: its pre/post identity, size, and mtime must be stable,
    and its full post-replace seal must match the public name before the cursor
    is published. A crash or bundle-side rewrite therefore leaves missing or
    mismatched state and causes another safe rebuild, never a blind append."""
    path = _projection_path(lesson)
    try:
        st = os.lstat(path)
    except OSError:
        st = None
    if st is not None and stat_module.S_ISDIR(st.st_mode):
        try:
            os.rmdir(path)
        except OSError:
            os.rename(path, f"{path}.collision-{uuid4().hex[:8]}")
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".attempts-")
    cursor_id = 0
    tail_created_at = None
    tail_attempt_id = None
    try:
        rows = conn.execute(
            "SELECT * FROM lesson_attempts WHERE lesson_id = ? "
            "ORDER BY created_at, attempt_id",
            (lesson["id"],),
        )
        try:
            for sqlite_row in rows:
                row = dict(sqlite_row)
                line = _projection_line(row).encode("utf-8")
                _write_all(fd, line)
                cursor_id = max(cursor_id, row["id"])
                tail_created_at = row["created_at"]
                tail_attempt_id = row["attempt_id"]
        finally:
            rows.close()
        os.fsync(fd)
        rendered_st = os.fstat(fd)
        if (
            not stat_module.S_ISREG(rendered_st.st_mode)
            or rendered_st.st_nlink != 1
        ):
            raise OSError("unsafe rebuilt projection temp")
        os.replace(tmp_name, path)
        parent_fd = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        published_st = os.fstat(fd)
        if (
            (published_st.st_dev, published_st.st_ino, published_st.st_size,
             published_st.st_mtime_ns)
            != (rendered_st.st_dev, rendered_st.st_ino, rendered_st.st_size,
                rendered_st.st_mtime_ns)
        ):
            raise OSError("rebuilt projection changed during publication")
        state = {
            "v": PROJECTION_STATE_VERSION,
            "lesson_uid": lesson["uid"],
            "cursor_id": cursor_id,
            "tail_created_at": tail_created_at,
            "tail_attempt_id": tail_attempt_id,
            "file": _file_seal(published_st),
        }
        if not _seal_matches(os.lstat(path), state["file"]):
            raise OSError("rebuilt projection changed during publication")
        os.close(fd)
        fd = -1
        _write_state(lesson, state)
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def reconcile_projection(conn: sqlite3.Connection, lesson: dict) -> bool:
    """Public reconcile entry point (ops/tests). Returns True when the
    projection now matches the authority, False on filesystem failure or
    unavailable private cross-process lock."""
    if conn.in_transaction:
        return False
    with _bundle_lock(lesson["slug"]):
        try:
            with _projection_file_lock(lesson):
                _rebuild_projection(conn, lesson)
        except (OSError, sqlite3.Error):
            return False
    return True


def _project_attempt(conn: sqlite3.Connection, lesson: dict, row: dict) -> bool:
    """Synchronous projection append, called under the bundle lock after the
    transaction committed. The fast path consults a private durable cursor,
    selects at most two authority rows after it, verifies the projection's
    descriptor seal and single-link guard, and renders at most one new line.
    Every repair path holds the same private per-lesson flock while streaming
    its authority snapshot and publishing both file and cursor, preserving the
    PR-57 round-10 stale-rebuild exclusion without a SQLite writer lock."""
    if conn.in_transaction:
        return False
    try:
        with _projection_file_lock(lesson):
            return _project_attempt_locked(conn, lesson, row)
    except (OSError, sqlite3.Error):
        return False


def _project_attempt_locked(
    conn: sqlite3.Connection, lesson: dict, row: dict
) -> bool:
    del row  # the committed authority, not caller memory, supplies file bytes
    state = _read_state(lesson)
    if state is not None and _projection_matches_state(lesson, state):
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
                _write_all(fd, line)
                os.fsync(fd)
                after = os.fstat(fd)
                os.close(fd)
                fd = -1
                name_st = os.lstat(_projection_path(lesson))
                if not _seal_matches(name_st, _file_seal(after)):
                    raise OSError("projection name changed during append")
                _write_state(lesson, {
                    "v": PROJECTION_STATE_VERSION,
                    "lesson_uid": lesson["uid"],
                    "cursor_id": candidate["id"],
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

    # §6.3 replay precedes every record-time refusal — the rate limit
    # included (PR-57 rounds 1 & 9): the original write is already durable,
    # so a client retry must learn its attempt_id even when the manifest has
    # since rejected the bundle, retired the question, or the retry lands
    # with the window exhausted — validation below governs only NEW writes.
    # Replays and key conflicts consume no budget; the unmetered work is one
    # indexed SELECT, far cheaper than the manifest/hash path the rate
    # limit exists to protect.
    with _bundle_lock(lesson["slug"]):
        replay = _replay_or_conflict(conn, lesson, submission)
    if replay is not None:
        return replay

    rate_stamp: float | None = None
    try:
        rate_stamp = _check_rate(lesson["id"])
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
        # PR-57 rounds 2 & 11: a retry racing its own original request
        # (timeout resend) can see the key uncommitted at the early check
        # above, then hit a refusal here — the rate limit included — after
        # the original committed. The durable outcome still wins (§6.3) —
        # re-check before refusing. (A 429 that survives this re-check is
        # fine: it is transient by contract, and the next retry after
        # Retry-After finds the committed duplicate.)
        try:
            with _bundle_lock(lesson["slug"]):
                replay = _replay_or_conflict(conn, lesson, submission)
        except AttemptError:
            _refund_rate(lesson["id"], rate_stamp)  # conflict: not a new write
            raise
        if replay is not None:
            _refund_rate(lesson["id"], rate_stamp)
            return replay
        raise
    stale = _derive_stale(
        lesson, read, question, submission["page_id"], submission["page_rev"]
    )

    try:
        with _bundle_lock(lesson["slug"]):
            return _record_locked(conn, lesson, submission, stale, rate_stamp)
    except AttemptError as exc:
        if exc.code == "idempotency-conflict":  # not a new write (round 12)
            _refund_rate(lesson["id"], rate_stamp)
        raise


def _record_locked(
    conn: sqlite3.Connection,
    lesson: dict,
    submission: dict,
    stale: bool,
    rate_stamp: float | None,
) -> dict:
    """The bundle-locked write section of `record_attempt`. Every replay
    outcome refunds the window slot — it was not a new write (round 12)."""
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
                insert_cursor = conn.execute(
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
                conn, lesson, {
                    **row,
                    "id": insert_cursor.lastrowid,
                    "event_uuid": event_uuid,
                }
            )
            return {
                "result": "recorded",
                "attempt_id": attempt_id,
                "stale": stale,
                "attempt_number": attempt_number,
                "projection": "projected" if projected else "pending",
            }
    _refund_rate(lesson["id"], rate_stamp)
    return replay
