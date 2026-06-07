"""Routine-item management (sec15.3, sec17).

Add / edit / deactivate / reactivate. Each write appends the matching event
(sec14.1) in one transaction. Deactivation is a SOFT retire (active=0 +
deactivated_at) so check-in history stays joinable — items are NEVER hard-deleted
(sec15.3).
"""
from __future__ import annotations

import json
import sqlite3

from ..db import now_iso

DEFAULT_GROUP = "Core Routine"

# Create-Habit option vocabularies (sec31). Stored on the routine_item; kept
# permissive — unknown values fall back to the default rather than erroring.
FREQUENCIES = ("daily", "weekdays", "weekly")
GOALS = ("achieve_all", "custom")
GOAL_DAYS = ("forever", "21", "30", "66", "100")

_UNSET = object()  # update_item sentinel: distinguishes "leave alone" from "set to None"


class ItemError(ValueError):
    """A management write was rejected (empty title, unknown id, …)."""


def _event(conn: sqlite3.Connection, type_: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO events (timestamp, type, payload_version, payload_json) "
        "VALUES (?, ?, 1, ?)",
        (now_iso(), type_, json.dumps(payload, ensure_ascii=False)),
    )


def list_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All items (active first), ordered for the Manage screen."""
    return conn.execute(
        "SELECT * FROM routine_items ORDER BY active DESC, group_name, sort_order, id"
    ).fetchall()


def get_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    """One routine item with all columns (for the habit edit form)."""
    return conn.execute("SELECT * FROM routine_items WHERE id = ?", (item_id,)).fetchone()


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
    if len(title) > 200:
        raise ItemError("title too long")
    return title, group_name


def _clean_habit_fields(emoji, frequency, goal, goal_days, start_date, reminder) -> dict:
    """Normalise the optional Create-Habit fields (sec31); unknowns → defaults."""
    emoji = (emoji or "").strip()[:8] or None
    frequency = frequency if frequency in FREQUENCIES else "daily"
    goal = goal if goal in GOALS else "achieve_all"
    goal_days = str(goal_days) if str(goal_days) in GOAL_DAYS else "forever"
    start_date = (start_date or "").strip() or None
    reminder = (reminder or "").strip() or None
    return {
        "emoji": emoji, "frequency": frequency, "goal": goal,
        "goal_days": goal_days, "start_date": start_date, "reminder": reminder,
    }


def create_item(
    conn: sqlite3.Connection,
    title: str,
    group_name: str,
    *,
    emoji: str | None = None,
    frequency: str = "daily",
    goal: str = "achieve_all",
    goal_days: str = "forever",
    start_date: str | None = None,
    reminder: str | None = None,
    constant_reminder: bool = False,
) -> int:
    title, group_name = _clean(title, group_name)
    h = _clean_habit_fields(emoji, frequency, goal, goal_days, start_date, reminder)
    ts = now_iso()
    h["start_date"] = h["start_date"] or ts[:10]  # default to creation date
    nxt = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM routine_items WHERE group_name = ?",
        (group_name,),
    ).fetchone()[0]
    with conn:
        cur = conn.execute(
            "INSERT INTO routine_items "
            "(title, group_name, active, sort_order, created_at, emoji, frequency, "
            " goal, goal_days, start_date, reminder, constant_reminder) "
            "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, group_name, nxt, ts, h["emoji"], h["frequency"], h["goal"],
             h["goal_days"], h["start_date"], h["reminder"], int(bool(constant_reminder))),
        )
        item_id = cur.lastrowid
        _event(conn, "routine_item_created", {
            "routine_item_id": item_id, "title": title, "group_name": group_name,
            "sort_order": nxt, **h, "constant_reminder": int(bool(constant_reminder)),
        })
    return item_id


def update_item(
    conn: sqlite3.Connection,
    item_id: int,
    title: str,
    group_name: str,
    *,
    emoji=_UNSET,
    frequency=_UNSET,
    goal=_UNSET,
    goal_days=_UNSET,
    start_date=_UNSET,
    reminder=_UNSET,
    constant_reminder=_UNSET,
) -> None:
    title, group_name = _clean(title, group_name)
    row = conn.execute("SELECT * FROM routine_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise ItemError("unknown item")
    # only the columns whose kw was supplied change; the rest keep their value
    pick = lambda v, col: row[col] if v is _UNSET else v  # noqa: E731
    h = _clean_habit_fields(
        pick(emoji, "emoji"), pick(frequency, "frequency"), pick(goal, "goal"),
        pick(goal_days, "goal_days"), pick(start_date, "start_date"), pick(reminder, "reminder"),
    )
    const = row["constant_reminder"] if constant_reminder is _UNSET else int(bool(constant_reminder))
    ts = now_iso()
    with conn:
        conn.execute(
            "UPDATE routine_items SET title=?, group_name=?, emoji=?, frequency=?, "
            "goal=?, goal_days=?, start_date=?, reminder=?, constant_reminder=?, updated_at=? "
            "WHERE id=?",
            (title, group_name, h["emoji"], h["frequency"], h["goal"], h["goal_days"],
             h["start_date"], h["reminder"], const, ts, item_id),
        )
        _event(conn, "routine_item_updated", {
            "routine_item_id": item_id, "title": title, "group_name": group_name,
            "sort_order": row["sort_order"], **h, "constant_reminder": const,
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
        _event(conn, "routine_item_deactivated", {
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
        _event(conn, "routine_item_updated", {
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
        conn.execute("DELETE FROM routine_items WHERE id = ?", (item_id,))
        _event(conn, "routine_item_deleted", {
            "routine_item_id": item_id,
            "title": row["title"],
            "checkins_removed": n,
        })
