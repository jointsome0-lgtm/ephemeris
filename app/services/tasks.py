"""Tasks — one-off to-dos and countdown events (sec21), TickTick-style.

A task lives in a list (defaulting to Inbox), may have a due date, a priority, and
a note; `kind='countdown'` marks an event we count down to. Completion is a
reversible toggle (completed_at timestamp ⇄ NULL) — nothing is hidden or lost, in
keeping with the recovery-not-shame goal (sec16.5). Each write appends its event
(sec14.1) in one transaction. Reads here back the smart lists (Today / Next 7 Days
/ Inbox / Completed) and per-list views.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date as _date, timedelta

from ..db import is_valid_date, now_iso, today_str
from . import lists as lists_svc

PRIORITIES = (0, 1, 2, 3)

# Seeded once on first run so Today/Lists aren't empty (sec17 seed pattern).
# (title, list_name|None=Inbox, due_offset_days|None, kind, completed)
SEED_TASKS = [
    ("Weekend", None, 2, "countdown", False),
    ("Reply to emails", "Inbox", 0, "task", False),
    ("Buy groceries", "Shopping", 0, "task", False),
    ("Read 10 pages", "Study", 0, "task", False),
    ("Plan the week", "Inbox", 0, "task", True),
]


class TaskError(ValueError):
    """A task write was rejected (empty title, bad date, unknown id, …)."""


def _event(conn: sqlite3.Connection, type_: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO events (timestamp, type, payload_version, payload_json) "
        "VALUES (?, ?, 1, ?)",
        (now_iso(), type_, json.dumps(payload, ensure_ascii=False)),
    )


# --- writes ----------------------------------------------------------------


def _clean(title: str | None, due_date: str | None, priority) -> tuple[str, str | None, int]:
    title = (title or "").strip()
    if not title:
        raise TaskError("task title can’t be empty")
    if len(title) > 500:
        raise TaskError("task title too long")
    due_date = (due_date or "").strip() or None
    if due_date is not None and not is_valid_date(due_date):
        raise TaskError("invalid due date (expected YYYY-MM-DD)")
    try:
        priority = int(priority)
    except (TypeError, ValueError):
        priority = 0
    if priority not in PRIORITIES:
        priority = 0
    return title, due_date, priority


def create_task(
    conn: sqlite3.Connection,
    title: str,
    list_id: int | None = None,
    due_date: str | None = None,
    kind: str = "task",
    priority: int = 0,
    note: str | None = None,
) -> int:
    title, due_date, priority = _clean(title, due_date, priority)
    if kind not in ("task", "countdown"):
        kind = "task"
    if list_id is None:
        list_id = lists_svc.inbox_id(conn)
    elif lists_svc.get_list(conn, list_id) is None:
        raise TaskError("unknown list")
    nxt = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM tasks WHERE list_id = ?", (list_id,)
    ).fetchone()[0]
    ts = now_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO tasks (title, list_id, note, due_date, priority, kind, sort_order, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title, list_id, note, due_date, priority, kind, nxt, ts),
        )
        task_id = cur.lastrowid
        _event(conn, "task_created", {
            "task_id": task_id, "title": title, "list_id": list_id,
            "due_date": due_date, "kind": kind, "priority": priority,
        })
    return task_id


def seed_if_empty(conn: sqlite3.Connection) -> int:
    """Insert sample tasks if there are none yet (lists must be seeded first)."""
    if conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]:
        return 0
    t0 = _date.fromisoformat(today_str())
    by_name = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM lists").fetchall()}
    n = 0
    for title, lname, off, kind, done in SEED_TASKS:
        lid = by_name.get(lname) if lname else lists_svc.inbox_id(conn)
        due = (t0 + timedelta(days=off)).isoformat() if off is not None else None
        tid = create_task(conn, title, list_id=lid, due_date=due, kind=kind)
        if done:
            toggle_complete(conn, tid)
        n += 1
    return n


def get_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT t.*, l.name AS list_name, l.emoji AS list_emoji
        FROM tasks t LEFT JOIN lists l ON l.id = t.list_id
        WHERE t.id = ?
        """,
        (task_id,),
    ).fetchone()


def toggle_complete(conn: sqlite3.Connection, task_id: int) -> bool:
    """Flip completion. Returns the new completed state (True = now completed)."""
    row = conn.execute("SELECT completed_at, title FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise TaskError("unknown task")
    ts = now_iso()
    now_completed = row["completed_at"] is None
    with conn:
        conn.execute(
            "UPDATE tasks SET completed_at = ?, updated_at = ? WHERE id = ?",
            (ts if now_completed else None, ts, task_id),
        )
        _event(conn, "task_completed" if now_completed else "task_reopened",
               {"task_id": task_id, "title": row["title"]})
    return now_completed


def update_task(conn: sqlite3.Connection, task_id: int, **fields) -> None:
    """Patch title / note / due_date / priority / list_id (only provided keys)."""
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise TaskError("unknown task")
    title = fields.get("title", row["title"])
    due_date = fields.get("due_date", row["due_date"])
    priority = fields.get("priority", row["priority"])
    title, due_date, priority = _clean(title, due_date, priority)
    note = fields.get("note", row["note"])
    list_id = fields.get("list_id", row["list_id"])
    if list_id is not None and list_id != row["list_id"] and lists_svc.get_list(conn, list_id) is None:
        raise TaskError("unknown list")
    ts = now_iso()
    with conn:
        conn.execute(
            "UPDATE tasks SET title=?, note=?, due_date=?, priority=?, list_id=?, updated_at=? WHERE id=?",
            (title, note, due_date, priority, list_id, ts, task_id),
        )
        _event(conn, "task_updated", {"task_id": task_id, "title": title})


# --- reads (smart lists + per-list) ----------------------------------------

_SELECT = """
    SELECT t.*, l.name AS list_name, l.emoji AS list_emoji
    FROM tasks t LEFT JOIN lists l ON l.id = t.list_id
"""


def today_tasks(conn: sqlite3.Connection, today: str | None = None) -> list[sqlite3.Row]:
    """Incomplete tasks due today or overdue (kind='task')."""
    today = today or today_str()
    return conn.execute(
        _SELECT + "WHERE t.completed_at IS NULL AND t.kind='task' "
        "AND t.due_date IS NOT NULL AND t.due_date <= ? "
        "ORDER BY t.priority DESC, t.due_date, t.sort_order, t.id",
        (today,),
    ).fetchall()


def countdowns(conn: sqlite3.Connection, today: str | None = None) -> list[sqlite3.Row]:
    """Upcoming countdown events (kind='countdown'), nearest first."""
    today = today or today_str()
    return conn.execute(
        _SELECT + "WHERE t.completed_at IS NULL AND t.kind='countdown' "
        "ORDER BY (t.due_date IS NULL), t.due_date, t.id",
    ).fetchall()


def completed_on(conn: sqlite3.Connection, day: str | None = None) -> list[sqlite3.Row]:
    """Tasks completed on `day` (default today)."""
    day = day or today_str()
    return conn.execute(
        _SELECT + "WHERE substr(t.completed_at,1,10) = ? ORDER BY t.completed_at DESC",
        (day,),
    ).fetchall()


def next7(conn: sqlite3.Connection, today: str | None = None) -> list[sqlite3.Row]:
    today = today or today_str()
    end = (_date.fromisoformat(today) + timedelta(days=6)).isoformat()
    return conn.execute(
        _SELECT + "WHERE t.completed_at IS NULL AND t.kind='task' "
        "AND t.due_date IS NOT NULL AND t.due_date >= ? AND t.due_date <= ? "
        "ORDER BY t.due_date, t.priority DESC, t.sort_order",
        (today, end),
    ).fetchall()


def list_tasks(conn: sqlite3.Connection, list_id: int, include_done: bool = False) -> list[sqlite3.Row]:
    q = _SELECT + "WHERE t.list_id = ? AND t.kind='task'"
    if not include_done:
        q += " AND t.completed_at IS NULL"
    q += " ORDER BY t.completed_at IS NOT NULL, t.priority DESC, t.sort_order, t.id"
    return conn.execute(q, (list_id,)).fetchall()


def recent_completed(conn: sqlite3.Connection, limit: int = 100) -> list[sqlite3.Row]:
    return conn.execute(
        _SELECT + "WHERE t.completed_at IS NOT NULL ORDER BY t.completed_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


def due_between(conn: sqlite3.Connection, start: str, end: str) -> list[sqlite3.Row]:
    """Every task (any kind) with a due date in [start, end] — for the calendar grid."""
    return conn.execute(
        _SELECT + "WHERE t.due_date IS NOT NULL AND t.due_date >= ? AND t.due_date <= ? "
        "ORDER BY t.due_date, t.priority DESC, (t.kind='countdown') DESC, t.id",
        (start, end),
    ).fetchall()


def all_open(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every incomplete to-do (kind='task'), highest priority first — for the matrix."""
    return conn.execute(
        _SELECT + "WHERE t.completed_at IS NULL AND t.kind='task' "
        "ORDER BY t.priority DESC, t.due_date IS NULL, t.due_date, t.sort_order, t.id",
    ).fetchall()


def search(conn: sqlite3.Connection, query: str, limit: int = 100) -> list[sqlite3.Row]:
    """Tasks whose title or note contains `query` (case-insensitive substring)."""
    q = query.strip()
    if not q:
        return []
    # escape LIKE metacharacters so a literal % or _ in the query isn't a wildcard
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{esc}%"
    return conn.execute(
        _SELECT + "WHERE (t.title LIKE ? ESCAPE '\\' OR t.note LIKE ? ESCAPE '\\') "
        "ORDER BY t.completed_at IS NOT NULL, t.due_date IS NULL, t.due_date, t.priority DESC, t.id "
        "LIMIT ?",
        (like, like, limit),
    ).fetchall()


def today_count(conn: sqlite3.Connection, today: str | None = None) -> int:
    today = today or today_str()
    return conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE completed_at IS NULL AND kind='task' "
        "AND due_date IS NOT NULL AND due_date <= ?",
        (today,),
    ).fetchone()[0]


def next7_count(conn: sqlite3.Connection, today: str | None = None) -> int:
    today = today or today_str()
    end = (_date.fromisoformat(today) + timedelta(days=6)).isoformat()
    return conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE completed_at IS NULL AND kind='task' "
        "AND due_date IS NOT NULL AND due_date >= ? AND due_date <= ?",
        (today, end),
    ).fetchone()[0]
