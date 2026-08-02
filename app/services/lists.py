"""Lists — task containers (Inbox + user lists), TickTick-style (sec21).

A list groups tasks. There is exactly one built-in `inbox` (the default home for
tasks with no list); the rest are user lists with an emoji. Like routine items,
lists are SOFT-archived (archived_at), never hard-deleted, so a task's history
stays joinable. Each write appends its event (sec14.1) in one transaction.
"""
from __future__ import annotations

import sqlite3

from .. import limits
from ..db import append_event, now_iso

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


def ensure_inbox(conn: sqlite3.Connection) -> int:
    """The built-in Inbox, created if this database has none. Returns its id.

    Structure, not demo data, which is why startup calls this on EVERY boot
    while `seed_if_empty` runs once per installation (the `app_meta.seeded_at`
    marker, schema v16). `inbox_id()` is the default home for a task filed
    without a list and two read routes call it unconditionally, so a database
    without an Inbox does not render — it raises. A restore can legitimately
    produce exactly that: the JSONL stream does not journal list rows, so its
    target holds real history and no lists at all. Such a ledger is not a fresh
    installation wanting demo data; it is an initialized one missing an
    invariant, and only the invariant is restored here.
    """
    row = conn.execute(
        "SELECT id FROM lists WHERE kind = 'inbox' ORDER BY id LIMIT 1"
    ).fetchone()
    if row is not None:
        return row["id"]
    with conn:
        cur = conn.execute(
            "INSERT INTO lists (name, emoji, kind, sort_order, created_at) "
            "VALUES (?, ?, 'inbox', 0, ?)",
            (INBOX_NAME, "📥", now_iso()),
        )
    return cur.lastrowid


def seed_if_empty(conn: sqlite3.Connection) -> int:
    """Create the Inbox + sample lists if there are no lists yet."""
    n = conn.execute("SELECT COUNT(*) FROM lists").fetchone()[0]
    if n:
        return 0
    ensure_inbox(conn)
    ts = now_iso()
    with conn:
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


def require_writable_list(conn: sqlite3.Connection, list_id: int) -> sqlite3.Row:
    """The row, if a new or moved entity may be filed under this list (#22).

    `get_list` deliberately still answers for an archived list — the read paths
    need it, or an archived list's own page could not name itself. Writing into
    one is the case with no reader: `/list/{id}` 404s on an archived list, so a
    task or event filed there by a form the sidebar rendered before the archive
    lands somewhere the app will not show again. Refusing at the write is the
    narrow fix; the read side is untouched.

    Raises `ListError` for a missing list and for an archived one, with the
    missing case keeping the wording its callers already surface.
    """
    row = get_list(conn, list_id)
    if row is None:
        raise ListError("unknown list")
    if row["archived_at"] is not None:
        raise ListError("that list is archived")
    return row


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
    limits.check(name, limits.LIST_NAME, "list name", ListError)
    return name


def create_list(conn: sqlite3.Connection, name: str, emoji: str | None = None) -> int:
    name = _clean_name(name)
    emoji = (emoji or "").strip() or "•"
    ts = now_iso()
    with conn:
        # One statement, so the MAX runs under the INSERT's own write lock and
        # two lists created at once cannot share a sort_order (#22).
        cur = conn.execute(
            "INSERT INTO lists (name, emoji, kind, sort_order, created_at) "
            "SELECT ?, ?, 'list', COALESCE(MAX(sort_order), 0) + 10, ? "
            "FROM lists WHERE kind = 'list'",
            (name, emoji, ts),
        )
        list_id = cur.lastrowid
        append_event(conn, "list_created", {"list_id": list_id, "name": name, "emoji": emoji})
    return list_id
