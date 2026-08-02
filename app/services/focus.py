"""Focus sessions — persisted Pomodoro / Stopwatch records (premium Focus view).

The Focus page timer is front-end JS; when a Pomodoro completes (or the user ends
a stopwatch span) the browser POSTs the finished session here so the Overview
stats (Today's/Total Pomo + Focus duration) and the Focus Record list stop being
static 0s. A session is one finished span of focused time; `mode='pomo'` rows
also count as one Pomodoro. Stats are READ-ONLY derived (sec14): we sum rows, we
never recompute them elsewhere. Each write appends its event (sec14.1) in one txn.
"""
from __future__ import annotations

import sqlite3
from datetime import date as _date, timedelta

from .. import limits
from ..db import append_event, now_iso, pretty_date, today_str

MODES = ("pomo", "stopwatch")
# A single session can't reasonably exceed a day; clamp bogus client values so a
# fat-fingered/replayed POST can't poison the all-time totals.
MAX_SECONDS = 24 * 60 * 60


class FocusError(ValueError):
    """A focus-session write was rejected (bad mode / non-positive duration)."""


# --- write -----------------------------------------------------------------


def _coerce_lesson_id(conn: sqlite3.Connection, value) -> int | None:
    """A focus session may name the lesson being studied. Accept only a positive id
    that points at a real, non-archived lesson; anything else (blank, junk, deleted,
    archived) stores as NULL so a bogus/stale value can't dangle."""
    try:
        lesson_id = int(value)
    except (TypeError, ValueError):
        return None
    if lesson_id <= 0:
        return None
    row = conn.execute(
        "SELECT 1 FROM lessons WHERE id = ? AND archived_at IS NULL", (lesson_id,)
    ).fetchone()
    return lesson_id if row else None


def record_session(conn: sqlite3.Connection, mode: str, seconds, note: str | None = None,
                   lesson_id=None) -> int:
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
    limits.check(note, limits.FOCUS_NOTE, "focus note", FocusError)
    lesson_id = _coerce_lesson_id(conn, lesson_id)
    ts = now_iso()
    day = today_str()
    with conn:
        cur = conn.execute(
            "INSERT INTO focus_sessions (mode, seconds, note, date, ended_at, created_at, lesson_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mode, seconds, note, day, ts, ts, lesson_id),
        )
        session_id = cur.lastrowid
        append_event(conn, "focus_session_recorded",
                     {"session_id": session_id, "mode": mode, "seconds": seconds,
                      "lesson_id": lesson_id})
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


# SELECT that carries the linked lesson's title alongside the session row, so a
# record row can name what was studied. LEFT JOIN keeps unattached sessions.
_RECORD_SELECT = (
    "SELECT fs.*, l.title AS lesson_title "
    "FROM focus_sessions fs LEFT JOIN lessons l ON l.id = fs.lesson_id "
)


def _record_view(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "mode": r["mode"],
        "mode_label": "Pomo" if r["mode"] == "pomo" else "Stopwatch",
        "duration_label": _dur_label(r["seconds"]),
        "time_label": _time_label(r["ended_at"]),
        "date": r["date"],
        "lesson_id": r["lesson_id"],
        "lesson_title": r["lesson_title"],
    }


def recent_sessions(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Most-recent finished sessions, newest first — the Focus Record list."""
    rows = conn.execute(
        _RECORD_SELECT + "ORDER BY fs.ended_at DESC, fs.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_record_view(r) for r in rows]


def get_session_view(conn: sqlite3.Connection, session_id: int) -> dict | None:
    """One session as a record-row dict (for the Mode-B live prepend)."""
    r = conn.execute(_RECORD_SELECT + "WHERE fs.id = ?", (session_id,)).fetchone()
    return _record_view(r) if r else None


# --- daily aggregates (Focus charts) ---------------------------------------


def _daily_title(d: _date, minutes: int, pomos: int) -> str:
    md = pretty_date(d, weekday=True)
    if not minutes and not pomos:
        return f"{md} · no focus"
    bits = [f"{minutes}m"]
    if pomos:
        bits.append(f"{pomos} pomo" + ("s" if pomos != 1 else ""))
    return md + " · " + " · ".join(bits)


def daily_totals(conn: sqlite3.Connection, days: int = 14) -> list[dict]:
    """Per-day focus totals for the last `days` days, oldest->newest, missing
    days zero-filled. Powers the 'Last N days' bar chart. `value` is the chart
    magnitude (minutes); `title` is the per-bar tooltip."""
    today = _date.fromisoformat(today_str())
    start = today - timedelta(days=days - 1)
    rows = conn.execute(
        "SELECT date, COALESCE(SUM(seconds),0) AS sec, "
        "COALESCE(SUM(CASE WHEN mode='pomo' THEN 1 ELSE 0 END),0) AS pomos "
        "FROM focus_sessions WHERE date >= ? GROUP BY date",
        (start.isoformat(),),
    ).fetchall()
    by_date = {r["date"]: (r["sec"], r["pomos"]) for r in rows}
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        sec, pomos = by_date.get(d.isoformat(), (0, 0))
        minutes = sec // 60
        out.append({
            "iso": d.isoformat(), "dow": d.strftime("%a"), "day": d.day,
            "seconds": sec, "minutes": minutes, "pomos": pomos,
            "is_today": d == today, "value": minutes,
            "title": _daily_title(d, minutes, pomos),
        })
    return out


def lesson_totals(conn: sqlite3.Connection, days: int | None = None,
                  limit: int = 6) -> list[dict]:
    """Top lessons by focused time (all-time, or the last `days` days). Only
    sessions that named a lesson count — the per-lesson Focus breakdown."""
    params: list = []
    where = "WHERE fs.lesson_id IS NOT NULL "
    if days:
        start = (_date.fromisoformat(today_str()) - timedelta(days=days - 1)).isoformat()
        where += "AND fs.date >= ? "
        params.append(start)
    rows = conn.execute(
        "SELECT l.id AS lesson_id, l.title AS title, "
        "COALESCE(SUM(fs.seconds),0) AS sec, "
        "COALESCE(SUM(CASE WHEN fs.mode='pomo' THEN 1 ELSE 0 END),0) AS pomos "
        "FROM focus_sessions fs JOIN lessons l ON l.id = fs.lesson_id "
        + where + "GROUP BY l.id ORDER BY sec DESC, l.id LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [{
        "lesson_id": r["lesson_id"], "title": r["title"],
        "seconds": r["sec"], "minutes": r["sec"] // 60, "pomos": r["pomos"],
        "label": _dur_label(r["sec"]),
    } for r in rows]


def lesson_total(conn: sqlite3.Connection, lesson_id: int) -> dict:
    """All focused time recorded against ONE lesson (schema v8's
    `focus_sessions.lesson_id`) — the Focus link the Learn record panel
    surfaces read-only, without ranking the lesson against the others."""
    row = conn.execute(
        "SELECT COALESCE(SUM(seconds),0) AS sec, COUNT(*) AS n "
        "FROM focus_sessions WHERE lesson_id = ?",
        (lesson_id,),
    ).fetchone()
    seconds = row["sec"]
    return {
        "seconds": seconds,
        "minutes": seconds // 60,
        "sessions": row["n"],
        "label": _dur_label(seconds),
    }


def focus_day_streak(daily: list[dict]) -> int:
    """Consecutive days with any focus, counting back from the last entry
    (today). Pure — operates on a daily_totals() list."""
    streak = 0
    for d in reversed(daily):
        if d["seconds"] > 0:
            streak += 1
        else:
            break
    return streak
