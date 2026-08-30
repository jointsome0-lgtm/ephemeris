"""Routine-item management (sec15.3, sec17).

Add / edit / deactivate / reactivate / delete. Each write appends the matching
event (sec14.1) in one transaction.

Two removal paths, and they are not the same thing:

- Deactivation is a SOFT retire (active=0 + deactivated_at), so check-in history
  stays joinable and the item can be reactivated (sec15.3).
- ``delete_item`` is a HARD delete (sec31, the TickTick 'Delete'): it drops the
  check-in rows and the routine_item row. Only the live tables are pruned — the
  append-only events log keeps the audit trail, so the ledger survives.
"""
from __future__ import annotations

import sqlite3

from .. import limits
from ..db import append_event, now_iso

DEFAULT_GROUP = "Core Routine"

_UNSET = object()  # update_item sentinel: distinguishes "leave alone" from "set to None"


class ItemError(ValueError):
    """A management write was rejected (empty title, unknown id, …)."""


def list_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All items (active first), ordered for the Manage screen."""
    return conn.execute(
        "SELECT * FROM routine_items ORDER BY active DESC, group_name, sort_order, id"
    ).fetchall()


def list_sections(conn: sqlite3.Connection) -> list[str]:
    """Distinct section names (group_name) in display order, for the Section picker."""
    rows = conn.execute(
        "SELECT group_name FROM routine_items WHERE active = 1 "
        "GROUP BY group_name ORDER BY MIN(sort_order), group_name"
    ).fetchall()
    return [r["group_name"] for r in rows]


def _clean(title: str | None, group_name: str | None) -> tuple[str, str]:
    title = (title or "").strip()
    group_name = (group_name or "").strip() or DEFAULT_GROUP
    if not title:
        raise ItemError("title can’t be empty")
    limits.check(title, limits.ROUTINE_ITEM_TITLE, "title", ItemError)
    return title, group_name


def _clean_habit_fields(emoji, start_date) -> dict:
    """Normalise the optional Create-Habit fields (sec31)."""
    emoji = (emoji or "").strip()[:8] or None
    start_date = (start_date or "").strip() or None
    return {"emoji": emoji, "start_date": start_date}


def create_item(
    conn: sqlite3.Connection,
    title: str,
    group_name: str,
    *,
    emoji: str | None = None,
    start_date: str | None = None,
) -> int:
    title, group_name = _clean(title, group_name)
    h = _clean_habit_fields(emoji, start_date)
    ts = now_iso()
    # Default to the creation date: since #18 this is a real lower bound — the
    # habit is not listed, and does not accrue statistics, before this day.
    h["start_date"] = h["start_date"] or ts[:10]
    with conn:
        # One statement, so the MAX runs under the INSERT's own write lock and
        # two habits added to the same group cannot share a sort_order (#22).
        # The event still reports the number, read back by id inside the same
        # transaction rather than guessed before it.
        cur = conn.execute(
            "INSERT INTO routine_items "
            "(title, group_name, active, sort_order, created_at, emoji, start_date) "
            "SELECT ?, ?, 1, COALESCE(MAX(sort_order), 0) + 10, ?, ?, ? "
            "FROM routine_items WHERE group_name = ?",
            (title, group_name, ts, h["emoji"], h["start_date"], group_name),
        )
        item_id = cur.lastrowid
        nxt = conn.execute(
            "SELECT sort_order FROM routine_items WHERE id = ?", (item_id,)
        ).fetchone()[0]
        append_event(conn, "routine_item_created", {
            "routine_item_id": item_id, "title": title, "group_name": group_name,
            "sort_order": nxt, **h,
        })
    return item_id


def update_item(
    conn: sqlite3.Connection,
    item_id: int,
    title: str,
    group_name: str,
    *,
    emoji=_UNSET,
    start_date=_UNSET,
) -> None:
    title, group_name = _clean(title, group_name)
    row = conn.execute("SELECT * FROM routine_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise ItemError("unknown item")
    # only the columns whose kw was supplied change; the rest keep their value
    pick = lambda v, col: row[col] if v is _UNSET else v  # noqa: E731
    h = _clean_habit_fields(pick(emoji, "emoji"), pick(start_date, "start_date"))
    ts = now_iso()
    with conn:
        conn.execute(
            "UPDATE routine_items SET title=?, group_name=?, emoji=?, start_date=?, "
            "updated_at=? WHERE id=?",
            (title, group_name, h["emoji"], h["start_date"], ts, item_id),
        )
        append_event(conn, "routine_item_updated", {
            "routine_item_id": item_id, "title": title, "group_name": group_name,
            "sort_order": row["sort_order"], **h,
        })


def deactivate_item(conn: sqlite3.Connection, item_id: int) -> None:
    row = conn.execute(
        "SELECT title FROM routine_items WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        raise ItemError("unknown item")
    ts = now_iso()
    with conn:
        conn.execute(
            "UPDATE routine_items SET active = 0, deactivated_at = ? WHERE id = ?",
            (ts, item_id),
        )
        append_event(conn, "routine_item_deactivated", {
            "routine_item_id": item_id,
            "title": row["title"],
        })


def reactivate_item(conn: sqlite3.Connection, item_id: int) -> None:
    row = conn.execute(
        "SELECT title, group_name, sort_order FROM routine_items WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        raise ItemError("unknown item")
    ts = now_iso()
    with conn:
        conn.execute(
            "UPDATE routine_items SET active = 1, deactivated_at = NULL, updated_at = ? WHERE id = ?",
            (ts, item_id),
        )
        append_event(conn, "routine_item_updated", {
            "routine_item_id": item_id,
            "title": row["title"],
            "group_name": row["group_name"],
            "sort_order": row["sort_order"],
        })


def delete_item(conn: sqlite3.Connection, item_id: int) -> None:
    """Hard-delete a habit and its check-ins (sec31, the TickTick 'Delete').

    Unlike Archive (soft retire, history kept), Delete removes the rows. The
    append-only events log still preserves the audit trail (sec14.1), so the
    ledger is never truly destroyed — only the live tables are pruned."""
    row = conn.execute(
        "SELECT title FROM routine_items WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        raise ItemError("unknown item")
    with conn:  # checkins FK -> routine_items, so clear them first
        n = conn.execute(
            "DELETE FROM checkins WHERE routine_item_id = ?", (item_id,)
        ).rowcount
        # Focused time survives the habit it was spent on (schema v19, #75): the
        # span really happened, so the session is detached rather than deleted.
        # A run in flight has nothing to attribute yet and is simply unhooked.
        conn.execute(
            "UPDATE focus_sessions SET habit_id = NULL WHERE habit_id = ?", (item_id,)
        )
        conn.execute(
            "UPDATE focus_runs SET habit_id = NULL WHERE habit_id = ?", (item_id,)
        )
        conn.execute("DELETE FROM routine_items WHERE id = ?", (item_id,))
        append_event(conn, "routine_item_deleted", {
            "routine_item_id": item_id,
            "title": row["title"],
            "checkins_removed": n,
        })
