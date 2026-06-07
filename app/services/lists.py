"""Lists — task containers (Inbox + user lists), TickTick-style (sec21).

A list groups tasks. There is exactly one built-in `inbox` (the default home for
tasks with no list); the rest are user lists with an emoji. Like routine items,
lists are SOFT-archived (archived_at), never hard-deleted, so a task's history
stays joinable. Each write appends its event (sec14.1) in one transaction.
"""
from __future__ import annotations

import json
import sqlite3

from ..db import now_iso

INBOX_NAME = "Inbox"

# Seeded once on first run so the sidebar isn't empty (sec17 seed pattern).
SEED_LISTS = [
    ("Welcome", "👋"),
    ("Exercise", "🏃"),
    ("Study", "📖"),
    ("Memo", "📝"),
    ("Shopping", "📦"),
]


class ListError(ValueError):
    """A list write was rejected (empty name, unknown id, deleting Inbox, …)."""


def _event(conn: sqlite3.Connection, type_: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO events (timestamp, type, payload_version, payload_json) "
        "VALUES (?, ?, 1, ?)",
        (now_iso(), type_, json.dumps(payload, ensure_ascii=False)),
    )


def seed_if_empty(conn: sqlite3.Connection) -> int:
    """Create the Inbox + sample lists if there are no lists yet."""
    n = conn.execute("SELECT COUNT(*) FROM lists").fetchone()[0]
    if n:
        return 0
    ts = now_iso()
    with conn:
        conn.execute(
            "INSERT INTO lists (name, emoji, kind, sort_order, created_at) "
            "VALUES (?, ?, 'inbox', 0, ?)",
            (INBOX_NAME, "📥", ts),
        )
        for i, (name, emoji) in enumerate(SEED_LISTS, start=1):
            conn.execute(
                "INSERT INTO lists (name, emoji, kind, sort_order, created_at) "
                "VALUES (?, ?, 'list', ?, ?)",
                (name, emoji, i * 10, ts),
            )
    return 1 + len(SEED_LISTS)


def inbox_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM lists WHERE kind = 'inbox' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        raise ListError("inbox missing")
    return row["id"]


def get_list(conn: sqlite3.Connection, list_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone()


def list_lists(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Active lists with an `open_count` of incomplete tasks, Inbox first."""
    return conn.execute(
        """
        SELECT l.*, (
            SELECT COUNT(*) FROM tasks t
            WHERE t.list_id = l.id AND t.completed_at IS NULL AND t.kind = 'task'
        ) AS open_count
        FROM lists l
        WHERE l.archived_at IS NULL
        ORDER BY (l.kind = 'inbox') DESC, l.sort_order, l.id
        """
    ).fetchall()


def _clean_name(name: str | None) -> str:
    name = (name or "").strip()
    if not name:
        raise ListError("list name can’t be empty")
    if len(name) > 100:
        raise ListError("list name too long")
    return name


def create_list(conn: sqlite3.Connection, name: str, emoji: str | None = None) -> int:
    name = _clean_name(name)
    emoji = (emoji or "").strip() or "•"
    nxt = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM lists WHERE kind = 'list'"
    ).fetchone()[0]
    ts = now_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO lists (name, emoji, kind, sort_order, created_at) "
            "VALUES (?, ?, 'list', ?, ?)",
            (name, emoji, nxt, ts),
        )
        list_id = cur.lastrowid
        _event(conn, "list_created", {"list_id": list_id, "name": name, "emoji": emoji})
    return list_id


def rename_list(conn: sqlite3.Connection, list_id: int, name: str, emoji: str | None = None) -> None:
    row = get_list(conn, list_id)
    if row is None:
        raise ListError("unknown list")
    name = _clean_name(name)
    emoji = (emoji or "").strip() or row["emoji"]
    ts = now_iso()
    with conn:
        conn.execute(
            "UPDATE lists SET name = ?, emoji = ?, updated_at = ? WHERE id = ?",
            (name, emoji, ts, list_id),
        )
        _event(conn, "list_updated", {"list_id": list_id, "name": name, "emoji": emoji})


def archive_list(conn: sqlite3.Connection, list_id: int) -> None:
    """Soft-archive a user list; its tasks move to Inbox so nothing is orphaned."""
    row = get_list(conn, list_id)
    if row is None:
        raise ListError("unknown list")
    if row["kind"] == "inbox":
        raise ListError("the Inbox can’t be deleted")
    ts = now_iso()
    inbox = inbox_id(conn)
    with conn:
        conn.execute("UPDATE tasks SET list_id = ? WHERE list_id = ?", (inbox, list_id))
        conn.execute("UPDATE lists SET archived_at = ? WHERE id = ?", (ts, list_id))
        _event(conn, "list_archived", {"list_id": list_id, "name": row["name"]})
