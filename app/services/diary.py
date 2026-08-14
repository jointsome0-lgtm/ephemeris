"""Diary entries — free-form dated journal entries (issue #2, docs/diary-spec.md).

The capture surface for the future selfos→exp2res diary feed. Ephemeris
stores, journals and exports each entry unchanged; downstream systems
interpret the text. The structured fields — `tags` (opaque strings),
`private` (bool), `atlas_ref` (opaque string) — ride every event payload into
the JSONL export verbatim: no interpretation, no routing, no export filtering.
The selfos adapter is the routing and privacy gate (selfos docs/tags.md);
in-text hashtags stay prose (capture-time lifting is #27's scope).

`private` is a one-way latch per that contract: it can be set at creation or
added later, never cleared on an existing entry — de-privatizing means
authoring a new non-private entry. Enforced here, not just in the UI.

Entries are per-entry, not per-day (a gap-question answer is an ordinary
entry; several may share a day), soft-archived, never hard-deleted, and every
write appends a full-snapshot event carrying the entry's uuid in the same
transaction — the export serializes payloads only, so the adapter's dedup key
must ride the payload (retro idiom, docs/retro-spec.md sec4).
"""
from __future__ import annotations

import json
import re
import sqlite3
from uuid import uuid4

from .. import limits
from ..db import append_event, is_valid_date, now_iso, today_str

# Same capture hygiene as retro (which mirrors exp2res): C0 controls except
# tab/newline/CR, plus DEL and the C1 block, are rejected in text and in the
# structural fields — this prose ships downstream through the same adapter.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


class DiaryError(ValueError):
    """A diary write was rejected (bad date, empty text, oversized field, …)."""


def _clean_date(entry_date: str | None) -> str:
    entry_date = (entry_date or "").strip()
    if not entry_date:
        return today_str()
    if not is_valid_date(entry_date):
        raise DiaryError("invalid date (expected YYYY-MM-DD)")
    if entry_date > today_str():
        raise DiaryError("diary date can’t be in the future")
    return entry_date


def _clean_text(text: str | None) -> str:
    text = text or ""
    if not text.strip():
        raise DiaryError("diary text can’t be empty")
    limits.check(text, limits.DIARY_TEXT, "diary text", DiaryError)
    if _CONTROL_RE.search(text):
        raise DiaryError("diary text contains control characters")
    return text


def parse_tags(raw: str | None) -> list[str]:
    """Comma-separated owner input → list of opaque tag strings.

    Trimmed, empties dropped, exact duplicates dropped keeping first
    occurrence. No case folding and no vocabulary check: which tags carry
    cross-system meaning is the selfos adapter's business, not ours.
    """
    tags: list[str] = []
    for part in (raw or "").split(","):
        tag = part.strip()
        if not tag or tag in tags:
            continue
        limits.check(tag, limits.DIARY_TAG, "diary tag", DiaryError)
        if _CONTROL_RE.search(tag):
            raise DiaryError("diary tag contains control characters")
        tags.append(tag)
    if len(tags) > limits.DIARY_TAGS_MAX:
        raise DiaryError(f"too many tags ({limits.DIARY_TAGS_MAX} max)")
    return tags


def _clean_atlas_ref(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    ref = raw.strip()
    limits.check(ref, limits.DIARY_ATLAS_REF, "atlas ref", DiaryError)
    if _CONTROL_RE.search(ref):
        raise DiaryError("atlas ref contains control characters")
    return ref


def _snapshot(row: sqlite3.Row) -> dict:
    """The full-entry event payload — the wire format the future adapter reads.

    The export serializes timestamp/type/payload_version/payload only, so the
    stable identity (`diary_uuid`) and every structured field must ride here.
    """
    return {
        "diary_uuid": row["uuid"],
        "diary_id": row["id"],
        "entry_date": row["entry_date"],
        "text": row["text"],
        "tags": json.loads(row["tags_json"]),
        "private": bool(row["private"]),
        "atlas_ref": row["atlas_ref"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "archived_at": row["archived_at"],
    }


def create_entry(conn: sqlite3.Connection, *, text: str, entry_date: str | None = None,
                 tags: str | None = None, private: bool = False,
                 atlas_ref: str | None = None) -> sqlite3.Row:
    entry_uuid = str(uuid4())
    ts = now_iso()
    values = (
        entry_uuid,
        _clean_date(entry_date),
        _clean_text(text),
        json.dumps(parse_tags(tags), ensure_ascii=False),
        1 if private else 0,
        _clean_atlas_ref(atlas_ref),
        ts,
    )
    with conn:
        cur = conn.execute(
            "INSERT INTO diary_entries (uuid, entry_date, text, tags_json, "
            "private, atlas_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        row = get_entry(conn, cur.lastrowid)
        append_event(conn, "diary_entry_created", _snapshot(row))
    return row


def update_entry(conn: sqlite3.Connection, entry_id: int, *, text: str,
                 entry_date: str | None = None, tags: str | None = None,
                 private: bool = False, atlas_ref: str | None = None) -> sqlite3.Row:
    """Full-form re-submit; the uuid never changes, so downstream latest-wins
    consumers follow the edit. `private` is a one-way latch: an already-private
    entry stays private whatever the form says (selfos docs/tags.md — clearing
    the flag would have no routing effect, so offering it would be a lie)."""
    existing = get_entry(conn, entry_id)
    if existing is None:
        raise DiaryError("unknown diary entry")
    ts = now_iso()
    values = (
        _clean_date(entry_date),
        _clean_text(text),
        json.dumps(parse_tags(tags), ensure_ascii=False),
        1 if (existing["private"] or private) else 0,
        _clean_atlas_ref(atlas_ref),
        ts,
    )
    with conn:
        conn.execute(
            "UPDATE diary_entries SET entry_date=?, text=?, tags_json=?, "
            "private=?, atlas_ref=?, updated_at=? WHERE id=?",
            (*values, entry_id),
        )
        row = get_entry(conn, entry_id)
        append_event(conn, "diary_entry_updated", _snapshot(row))
    return row


def archive_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    """Soft-delete; the state check rides the UPDATE so a concurrent
    double-archive stays an idempotent no-op (retro idiom)."""
    if get_entry(conn, entry_id) is None:
        raise DiaryError("unknown diary entry")
    ts = now_iso()
    with conn:
        cur = conn.execute(
            "UPDATE diary_entries SET archived_at = ? "
            "WHERE id = ? AND archived_at IS NULL", (ts, entry_id))
        if cur.rowcount == 0:
            return
        append_event(conn, "diary_entry_archived", _snapshot(get_entry(conn, entry_id)))


def unarchive_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    if get_entry(conn, entry_id) is None:
        raise DiaryError("unknown diary entry")
    with conn:
        cur = conn.execute(
            "UPDATE diary_entries SET archived_at = NULL "
            "WHERE id = ? AND archived_at IS NOT NULL", (entry_id,))
        if cur.rowcount == 0:
            return
        append_event(conn, "diary_entry_unarchived", _snapshot(get_entry(conn, entry_id)))


def get_entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM diary_entries WHERE id = ?", (entry_id,)).fetchone()


def entry_tags(row: sqlite3.Row) -> list[str]:
    """The stored tag list of one row, for display."""
    return json.loads(row["tags_json"])


def list_entries(conn: sqlite3.Connection, include_archived: bool = False) -> list[sqlite3.Row]:
    """Newest day first; within a day, creation order — a day reads top to
    bottom the way it was written."""
    q = "SELECT * FROM diary_entries"
    if not include_archived:
        q += " WHERE archived_at IS NULL"
    q += " ORDER BY entry_date DESC, id ASC"
    return conn.execute(q).fetchall()
