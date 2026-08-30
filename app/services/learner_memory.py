"""Cross-lesson learner memory — what the tutor of THIS lesson may know about
every other lesson the learner has already studied (#114).

`assessments.jsonl` answers "what did past sessions conclude about this
lesson"; `memory.jsonl` answers "what did they conclude anywhere else". Same
authority (`lesson_assessments`), same ownership shape: app-written,
agent-read-only, published atomically into the bundle root. It is a second
READ MODEL over data that was already course-wide, not a second store — which
is why no migration and no new write capability appear here.

Scope is every lesson with assessment records, not a path neighbourhood
(owner, 2026-08-11). Each entry carries the `path`/`step` its own manifest
declares and the list is sorted by tree-closeness to the current lesson, so
relevance filtering is the tutor's judgment, made on labelled data, rather
than a filter this module applies blindly.

Concept tags appear VERBATIM, attributed to their source lesson. Merging
near-synonyms across lessons is deliberately not attempted: the tags are free
text, and a wrong merge would silently claim the learner demonstrated
something they never did.

`format: 1` on the meta line versions the whole file. A later hierarchical or
summarized memory is format 2 — a replacement, derivable retroactively from
the authority table — so nothing here is scaffolding for it.

Staleness is part of the contract: the file is regenerated when a lesson-agent
terminal opens, and never fanned out. Evidence recorded in lesson A reaches
lesson B's bundle at B's next terminal open, not before.
"""
from __future__ import annotations

import json
import os
import sqlite3

from . import bundle_schema, lessons, projection
from .assessments import (
    ACTIVE_FOLD_KEYS_SQL,
    PROJECTION_STATE_DIR,
    _fold_keys,
    _hydrate,
    _utc_now_iso,
    fold_rows,
)

PROJECTION_NAME = "memory.jsonl"
META_KIND = "learner_memory_meta"
ENTRY_KIND = "lesson_memory"
# The whole file's format, not a per-record version: a reader that does not
# know this number must skip the file, not guess at its lines.
FORMAT_VERSION = 1
SCOPE = "all-studied-lessons"


def _studied_lessons(
    conn: sqlite3.Connection, exclude_id: int
) -> list[dict]:
    """Every OTHER lesson that has assessment records, by id.

    The current lesson is excluded at the source: its own state is
    `assessments.jsonl`, in full, beside this file — repeating a compressed
    copy of it here would give the tutor two answers to one question."""
    rows = conn.execute(
        "SELECT id, uid, slug, title FROM lessons l WHERE l.id <> ? "
        "AND EXISTS (SELECT 1 FROM lesson_assessments a WHERE a.lesson_id = l.id) "
        "ORDER BY l.id",
        (exclude_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _address(lesson: dict) -> tuple[str | None, int | None, bool]:
    """The lesson's `path`/`step` as its own manifest declares them, plus
    whether that manifest contradicts this lesson's identity.

    `path`/`step` live only in `lesson.json` (#81), so they are read fresh
    here rather than mirrored into SQLite. A missing, unreadable or rejected
    manifest is not an error — the lesson simply has no address and sorts
    last. A manifest naming a DIFFERENT lesson is not trusted for an address
    either, and blocks publication when it is the current lesson's own."""
    try:
        read = lessons.read_bundle_readonly(lesson)
    except Exception:
        return None, None, False
    if read.rejected:
        # A rejected read carries no trusted identity (the `effective_profile`
        # idiom): it yields no address and gates nothing.
        return None, None, False
    uid = read.lesson_uid
    contradicts = isinstance(uid, str) and bool(uid) and uid != lesson.get("uid")
    if contradicts:
        return None, None, True
    path = read.path_ref if isinstance(read.path_ref, str) else None
    if not bundle_schema.split_path_ref(path):
        # An all-separator ref groups nowhere (§4.5), so it is no address at
        # all — the same answer the Learn track strip gives it.
        path = None
    step = read.step if isinstance(read.step, int) else None
    return path, (step if path is not None else None), False


def _lesson_state(conn: sqlite3.Connection, lesson_id: int) -> tuple[dict, int]:
    """One lesson's active fold, with only the rows this file prints read
    whole, plus its standing-review count.

    The §6.5 two-phase idiom, and this is the caller that most needs it: the
    fold has to visit every active row, but a lesson's history has no ceiling,
    every row can carry an 8 KiB note, and this runs across EVERY studied
    lesson at every terminal open. So the fold walks the narrow key columns,
    the reviews are counted and dropped before hydration — the file prints
    their number, never their notes — and only the evidence and summary
    winners are re-read whole."""
    rows = [
        _fold_keys(row)
        for row in conn.execute(ACTIVE_FOLD_KEYS_SQL, (lesson_id,)).fetchall()
    ]
    state = fold_rows(rows)
    reviews = len(state["reviews_by_attempt"])
    state["reviews_by_attempt"] = {}
    return _hydrate(conn, lesson_id, state), reviews


def _entry(
    lesson: dict, state: dict, reviews: int, path: str | None, step: int | None
) -> dict | None:
    """One studied lesson as the tutor reads it, or None when it has nothing
    standing left to say.

    Assessment-derived only: the active evidence level per concept, the latest
    summary, and how many of that lesson's attempts carry a standing verdict.
    Another lesson's `attempts.jsonl`, artifacts and learner files are that
    lesson's own volume and stay in that lesson's bundle — so the reviews are
    a COUNT: their attempt ids mean nothing outside the lesson that recorded
    them, and the count is what makes a session cut short after reviews but
    before any evidence read as studied rather than as untouched."""
    concepts = [
        {"concept": concept, "level": row["level"], "basis": row["basis"]}
        for concept, row in sorted(state["evidence_by_concept"].items())
    ]
    summary_row = state["summary"]
    summary = None if summary_row is None else {
        "note": summary_row["note"],
        "next_action": summary_row["next_action"],
        "created_at": summary_row["created_at"],
    }
    if not concepts and summary is None and not reviews:
        # Every record retracted or superseded into nothing: an entry here
        # would assert studied-ness the authority no longer stands behind.
        return None
    return {
        "kind": ENTRY_KIND,
        "slug": lesson["slug"],
        "lesson_uid": lesson["uid"],
        "title": lesson["title"],
        "path": path,
        "step": step,
        "concepts": concepts,
        "reviews": reviews,
        "summary": summary,
    }


def _shared_segments(left: str | None, right: str | None) -> int:
    """How many leading segments two addresses share.

    Split by the schema's own §4.5 splitter, which collapses empty segments:
    `a//b` and `a/b` are ONE address to the Learn tree, so they must be one
    address to this ranking too — otherwise a hand-edited manifest would sort
    the learner's closest lesson behind unrelated ones."""
    if not left or not right:
        return 0
    count = 0
    for one, other in zip(
        bundle_schema.split_path_ref(left), bundle_schema.split_path_ref(right)
    ):
        if one != other:
            break
        count += 1
    return count


def _sort_key(entry: dict, here: str | None) -> tuple:
    """Tree-closeness to the current lesson first, then the track's own order.

    Same course before same cluster before the rest; within one address, by
    `step`. A lesson with no address shares nothing and sorts behind every
    lesson that has one, however unrelated."""
    path, step = entry["path"], entry["step"]
    return (
        -_shared_segments(path, here),
        path is None,
        step is None,
        step or 0,
        entry["slug"],
    )


def _collect(
    conn: sqlite3.Connection, lesson: dict
) -> tuple[str | None, int | None, list[dict], bool]:
    """The rendered state: this lesson's address, the sorted entries, and
    whether this bundle's manifest contradicts this lesson.

    Both folds run inside ONE read snapshot, so the entries cannot mix two
    committed versions of the authority. Manifest reads are filesystem work
    and stay outside it."""
    here_path, here_step, contradicts = _address(lesson)
    conn.execute("BEGIN")
    try:
        others = _studied_lessons(conn, lesson["id"])
        states = {
            other["id"]: _lesson_state(conn, other["id"]) for other in others
        }
    finally:
        conn.rollback()  # nothing was written; just release the snapshot
    entries = []
    for other in others:
        path, step, _ = _address(other)
        state, reviews = states[other["id"]]
        entry = _entry(other, state, reviews, path, step)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda entry: _sort_key(entry, here_path))
    return here_path, here_step, entries, contradicts


def _render(
    lesson: dict, here_path: str | None, here_step: int | None, entries: list[dict]
) -> bytes:
    """One meta line, then one line per studied lesson, closest first."""
    lines = [{
        "kind": META_KIND,
        "format": FORMAT_VERSION,
        "scope": SCOPE,
        "lesson_uid": lesson["uid"],
        "slug": lesson["slug"],
        "path": here_path,
        "step": here_step,
        "entries": len(entries),
        "generated_at": _utc_now_iso(),
    }]
    lines.extend(entries)
    return "".join(
        json.dumps(line, ensure_ascii=False) + "\n" for line in lines
    ).encode("utf-8")


def _projection_exists(lesson: dict) -> bool:
    """Whether anything at all occupies the projection name (no-follow)."""
    try:
        os.lstat(lessons._lesson_dir(lesson["slug"]) / PROJECTION_NAME)
    except (OSError, lessons.LessonError):
        return False
    return True


def _publish(lesson: dict, data: bytes) -> None:
    """Stage the bytes in the bundle, then take the name atomically.

    The bundle root is opened once with `O_NOFOLLOW | O_DIRECTORY` and every
    later step is relative to that descriptor, so the published name cannot be
    redirected between the checks and the rename."""
    dir_fd = os.open(lessons._lesson_dir(lesson["slug"]), projection.DIRECTORY_FLAGS)
    try:
        projection.publish(dir_fd, PROJECTION_NAME, data, prefix=".memory-")
    finally:
        os.close(dir_fd)


def _rewrite_locked(conn: sqlite3.Connection, lesson: dict) -> bool:
    """Render the committed authority and publish it. Runs under the flock."""
    here_path, here_step, entries, contradicts = _collect(conn, lesson)
    if not entries and not _projection_exists(lesson):
        # Nothing studied anywhere else and nothing occupies the name: the
        # absent file already IS the state. This runs at every lesson-agent
        # terminal open and must not litter every bundle with an empty file.
        return True
    if contradicts:
        # The publication identity gate (S-H7): never write one lesson's
        # cross-lesson memory into a bundle whose manifest says it is another
        # lesson. Unlike the per-lesson projection there is no durable write
        # waiting on this — the file simply stays as it was.
        return False
    _publish(lesson, _render(lesson, here_path, here_step, entries))
    return True


def reconcile_projection(conn: sqlite3.Connection, lesson: dict) -> bool:
    """Rewrite `memory.jsonl` from the committed authority. Idempotent.

    Unconditional, unlike the per-lesson projection's seal check: this file is
    derived from OTHER lessons' state and their manifests, so an intact file
    is no evidence that it is still current. It is one small render per
    terminal open.

    Returns True when the bundle reflects the authority, False when it does
    not: an active transaction (filesystem work must never run inside one), an
    unavailable or busy lock, a bundle root that cannot be opened safely, a
    manifest whose identity contradicts the lesson, or any filesystem error.
    Derived and best-effort in every direction — never an exception at the
    caller."""
    if conn.in_transaction:
        return False
    try:
        # The per-lesson bundle-projection lock, shared with the assessment
        # projection: both publish into the same bundle root, and the terminal
        # open runs them one after the other, never nested.
        with projection.lesson_lock(lesson["slug"]):
            with projection.file_lock(PROJECTION_STATE_DIR, lesson):
                return _rewrite_locked(conn, lesson)
    except Exception:
        return False
