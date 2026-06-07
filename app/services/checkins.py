"""Check-in / daily-note write contract and Today reads.

Implements system-design.md sec16.4 (status & note write contract) and sec14.1
(every state change writes the table row AND its event in ONE transaction; the
event's item_title is an immutable snapshot at event time).

The caller passes an open connection (from db.get_conn) and is responsible for
closing it; each write here is wrapped in its own `with conn:` transaction.
"""
from __future__ import annotations

import json
import sqlite3

from ..db import STATUSES, now_iso


class CheckinError(ValueError):
    """A write was rejected by the contract (bad status, no status yet, etc.)."""


# --- seed (sec17) ----------------------------------------------------------

SEED_ITEMS = [
    ("Sleep", "Core Routine", 10),
    ("Food", "Core Routine", 20),
    ("Sport / show up", "Core Routine", 30),
    ("Evening walk", "Core Routine", 40),
    ("Daily output", "Core Routine", 50),
]


def _append_event(conn: sqlite3.Connection, type_: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO events (timestamp, type, payload_version, payload_json) "
        "VALUES (?, ?, 1, ?)",
        (now_iso(), type_, json.dumps(payload, ensure_ascii=False)),
    )


def seed_if_empty(conn: sqlite3.Connection) -> int:
    """Insert the seed habits if there are no routine items yet."""
    n = conn.execute("SELECT COUNT(*) FROM routine_items").fetchone()[0]
    if n:
        return 0
    ts = now_iso()
    with conn:
        for title, group, order in SEED_ITEMS:
            cur = conn.execute(
                "INSERT INTO routine_items "
                "(title, group_name, active, sort_order, created_at) "
                "VALUES (?, ?, 1, ?, ?)",
                (title, group, order, ts),
            )
            _append_event(conn, "routine_item_created", {
                "routine_item_id": cur.lastrowid,
                "title": title,
                "group_name": group,
                "sort_order": order,
            })
    return len(SEED_ITEMS)


# --- reads (Today) ---------------------------------------------------------


def _require_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    item = conn.execute(
        "SELECT id, title FROM routine_items WHERE id = ?", (item_id,)
    ).fetchone()
    if item is None:
        raise CheckinError(f"unknown routine_item_id: {item_id}")
    return item


def get_checkin(conn: sqlite3.Connection, date: str, item_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, status, note, created_at FROM checkins "
        "WHERE date = ? AND routine_item_id = ?",
        (date, item_id),
    ).fetchone()


def today_view(conn: sqlite3.Connection, date: str) -> list[tuple[str, list[sqlite3.Row]]]:
    """Active items + their check-in for `date`, grouped into ordered sections.

    Ordering is sec13.3: ORDER BY group_name, sort_order, id. Because group_name
    is the leading sort key, each group's rows are contiguous, so grouping by
    first appearance preserves the SQL order. NOTE: this makes group SECTIONS
    alphabetical — fine for the single seed group; revisit group ordering in
    Manage Items (M3) when more groups exist.
    """
    rows = conn.execute(
        """
        SELECT ri.id AS id, ri.title AS title, ri.group_name AS group_name,
               ri.emoji AS emoji, c.status AS status, c.note AS note
        FROM routine_items ri
        LEFT JOIN checkins c
          ON c.routine_item_id = ri.id AND c.date = ?
        WHERE ri.active = 1
        ORDER BY ri.group_name, ri.sort_order, ri.id
        """,
        (date,),
    ).fetchall()
    groups: list[tuple[str, list[sqlite3.Row]]] = []
    index: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        bucket = index.get(r["group_name"])
        if bucket is None:
            bucket = []
            index[r["group_name"]] = bucket
            groups.append((r["group_name"], bucket))
        bucket.append(r)
    return groups


def get_daily_note(conn: sqlite3.Connection, date: str) -> str:
    row = conn.execute(
        "SELECT text FROM daily_notes WHERE date = ?", (date,)
    ).fetchone()
    return (row["text"] if row and row["text"] is not None else "")


# --- writes (sec16.4) ------------------------------------------------------


def upsert_checkin(
    conn: sqlite3.Connection,
    date: str,
    item_id: int,
    status: str | None = None,
    note: str | None = None,
) -> str:
    """Upsert one check-in and append its event, atomically (sec16.4).

    - `status` is validated against the enum and only changes if provided.
    - `note` only changes if provided (an absent field leaves the column alone).
    - A note save with no existing row is rejected: the flow is status-first, so
      there are no note-only rows (status stays NOT NULL).

    Returns the resulting status.
    """
    if status is None and note is None:
        raise CheckinError("nothing to update (no status, no note)")
    if status is not None and status not in STATUSES:
        raise CheckinError(f"invalid status: {status!r}")
    item = _require_item(conn, item_id)
    existing = get_checkin(conn, date, item_id)
    ts = now_iso()
    with conn:  # row + event in ONE transaction; rollback both on failure
        if existing is None:
            if status is None:
                # no row for this (date, item) and no status -> status-first reject
                raise CheckinError("pick a status first")
            conn.execute(
                "INSERT INTO checkins "
                "(date, routine_item_id, status, note, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (date, item_id, status, note, ts, ts),
            )
            final_status, final_note = status, note
        else:
            final_status = status if status is not None else existing["status"]
            final_note = note if note is not None else existing["note"]
            conn.execute(
                "UPDATE checkins SET status = ?, note = ?, updated_at = ? WHERE id = ?",
                (final_status, final_note, ts, existing["id"]),
            )
        _append_event(conn, "routine_checkin_upserted", {
            "date": date,
            "routine_item_id": item_id,
            "item_title": item["title"],  # immutable snapshot at event time
            "status": final_status,
            "note": final_note,
        })
    return final_status


def clear_checkin(conn: sqlite3.Connection, date: str, item_id: int) -> bool:
    """Delete the check-in for (date, item) and append routine_checkin_cleared.

    Idempotent: returns False (and writes nothing) if there is no row to clear.
    """
    item = _require_item(conn, item_id)
    existing = get_checkin(conn, date, item_id)
    if existing is None:
        return False
    with conn:
        conn.execute("DELETE FROM checkins WHERE id = ?", (existing["id"],))
        _append_event(conn, "routine_checkin_cleared", {
            "date": date,
            "routine_item_id": item_id,
            "item_title": item["title"],
        })
    return True


def apply_status(conn: sqlite3.Connection, date: str, item_id: int, status: str) -> str:
    """Toggle semantics for a status tap (sec16.4): tapping the already-selected
    status clears the check-in; any other status sets it.

    Returns "cleared" or "set".
    """
    if status not in STATUSES:
        raise CheckinError(f"invalid status: {status!r}")
    existing = get_checkin(conn, date, item_id)
    if existing is not None and existing["status"] == status:
        clear_checkin(conn, date, item_id)
        return "cleared"
    upsert_checkin(conn, date, item_id, status=status)
    return "set"


def upsert_daily_note(conn: sqlite3.Connection, date: str, text: str) -> None:
    """Upsert the daily note for `date` and append daily_note_updated (sec16.4).

    Empty text stores an empty note; there is no separate clear event.
    """
    existing = conn.execute(
        "SELECT date FROM daily_notes WHERE date = ?", (date,)
    ).fetchone()
    ts = now_iso()
    with conn:
        if existing is None:
            conn.execute(
                "INSERT INTO daily_notes (date, text, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (date, text, ts, ts),
            )
        else:
            conn.execute(
                "UPDATE daily_notes SET text = ?, updated_at = ? WHERE date = ?",
                (text, ts, date),
            )
        _append_event(conn, "daily_note_updated", {"date": date, "text": text})
