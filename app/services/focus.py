"""Focus sessions — persisted Pomodoro / Stopwatch records (premium Focus view).

The Focus page timer is front-end JS; when a Pomodoro completes (or the user ends
a stopwatch span) the browser POSTs the finished session here so the Overview
stats (Today's/Total Pomo + Focus duration) and the Focus Record list stop being
static 0s. A session is one finished span of focused time; `mode='pomo'` rows
also count as one Pomodoro. Stats are READ-ONLY derived (sec14): we sum rows, we
never recompute them elsewhere. Each write appends its event (sec14.1) in one txn.
"""
from __future__ import annotations

import json
import sqlite3

from ..db import now_iso, today_str

MODES = ("pomo", "stopwatch")
# A single session can't reasonably exceed a day; clamp bogus client values so a
# fat-fingered/replayed POST can't poison the all-time totals.
MAX_SECONDS = 24 * 60 * 60


class FocusError(ValueError):
    """A focus-session write was rejected (bad mode / non-positive duration)."""


def _event(conn: sqlite3.Connection, type_: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO events (timestamp, type, payload_version, payload_json) "
        "VALUES (?, ?, 1, ?)",
        (now_iso(), type_, json.dumps(payload, ensure_ascii=False)),
    )


# --- write -----------------------------------------------------------------


def record_session(conn: sqlite3.Connection, mode: str, seconds, note: str | None = None) -> int:
    """Persist one finished focus session; returns its id. Row + event in one txn."""
    if mode not in MODES:
        raise FocusError("unknown focus mode")
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        raise FocusError("invalid duration")
    if seconds <= 0:
        raise FocusError("duration must be positive")
    seconds = min(seconds, MAX_SECONDS)
    note = (note or "").strip() or None
    ts = now_iso()
    day = today_str()
    with conn:
        cur = conn.execute(
            "INSERT INTO focus_sessions (mode, seconds, note, date, ended_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (mode, seconds, note, day, ts, ts),
        )
        session_id = cur.lastrowid
        _event(conn, "focus_session_recorded",
               {"session_id": session_id, "mode": mode, "seconds": seconds})
    return session_id


# --- duration formatting (shared by stats + record rows) -------------------


def _dur(seconds: int) -> dict:
    """{'value','unit'} — minutes under an hour, else hours to one decimal."""
    seconds = int(seconds or 0)
    minutes = seconds // 60
    if minutes < 60:
        return {"value": minutes, "unit": "m"}
    hours = round(seconds / 3600, 1)
    if hours == int(hours):
        hours = int(hours)
    return {"value": hours, "unit": "h"}


def _dur_label(seconds: int) -> str:
    """Compact human duration for a record row, e.g. '25m' / '1h 5m' / '40s'."""
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m" if m else f"{h}h"
    if m:
        return f"{m}m"
    return f"{s}s"


def _time_label(iso: str | None) -> str:
    """'HH:MM' from an ISO-8601 'YYYY-MM-DDTHH:MM:SS+ZZ:ZZ' timestamp."""
    try:
        return iso.split("T", 1)[1][:5]
    except (AttributeError, IndexError):
        return ""


# --- reads -----------------------------------------------------------------


def overview(conn: sqlite3.Connection) -> dict:
    """Today's + all-time Pomodoro count and focus duration (derived, sec14)."""
    today = today_str()
    row = conn.execute(
        "SELECT "
        "  COALESCE(SUM(CASE WHEN mode='pomo' AND date=? THEN 1 ELSE 0 END), 0) AS today_pomo, "
        "  COALESCE(SUM(CASE WHEN date=? THEN seconds ELSE 0 END), 0)           AS today_sec, "
        "  COALESCE(SUM(CASE WHEN mode='pomo' THEN 1 ELSE 0 END), 0)            AS total_pomo, "
        "  COALESCE(SUM(seconds), 0)                                           AS total_sec "
        "FROM focus_sessions",
        (today, today),
    ).fetchone()
    return {
        "today_pomo": row["today_pomo"],
        "today_focus": _dur(row["today_sec"]),
        "total_pomo": row["total_pomo"],
        "total_focus": _dur(row["total_sec"]),
    }


def _record_view(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "mode": r["mode"],
        "mode_label": "Pomo" if r["mode"] == "pomo" else "Stopwatch",
        "duration_label": _dur_label(r["seconds"]),
        "time_label": _time_label(r["ended_at"]),
        "date": r["date"],
    }


def recent_sessions(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Most-recent finished sessions, newest first — the Focus Record list."""
    rows = conn.execute(
        "SELECT * FROM focus_sessions ORDER BY ended_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_record_view(r) for r in rows]


def get_session_view(conn: sqlite3.Connection, session_id: int) -> dict | None:
    """One session as a record-row dict (for the Mode-B live prepend)."""
    r = conn.execute("SELECT * FROM focus_sessions WHERE id = ?", (session_id,)).fetchone()
    return _record_view(r) if r else None
