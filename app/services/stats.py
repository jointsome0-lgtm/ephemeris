"""Habit statistics — streaks, weekly history, and monthly heatmap (read-only).

Everything here is DERIVED from the `checkins` table (the source of truth, sec14);
nothing is written. These power the TickTick-style habit surface: per-row streak
stats + 7-day dots on the day view (sec16.2) and the habit detail view's stat
cards + monthly calendar heatmap (sec16.6).

Streak semantics — Activity Ledger's four-status model. `light_done` keeping the
chain is the differentiator TickTick's binary model cannot express (sec16.5):

    full_done / light_done   KEEP the chain (a "light" day still counts).
    skipped                  NEUTRAL — a conscious rest day: preserves the streak
                             but adds 0 (the "skip day" pattern).
    failed / empty past day  BREAK the streak.
    empty `today`            PENDING — does not break (the day isn't over yet).
"""
from __future__ import annotations

import calendar as _cal
import sqlite3
from datetime import date as _date, timedelta

from ..db import today_str

KEPT = ("full_done", "light_done")


# --- raw history fetches ---------------------------------------------------


def history(conn: sqlite3.Connection, item_id: int) -> dict[str, str]:
    """{date_iso: status} for one item across ALL time."""
    rows = conn.execute(
        "SELECT date, status FROM checkins WHERE routine_item_id = ? ORDER BY date",
        (item_id,),
    ).fetchall()
    return {r["date"]: r["status"] for r in rows}


def all_histories(conn: sqlite3.Connection) -> dict[int, dict[str, str]]:
    """{item_id: {date_iso: status}} for every item — one query, for batch use
    on the day view (streaks + weekly dots for many rows without N queries)."""
    rows = conn.execute(
        "SELECT routine_item_id AS id, date, status FROM checkins ORDER BY date"
    ).fetchall()
    out: dict[int, dict[str, str]] = {}
    for r in rows:
        out.setdefault(r["id"], {})[r["date"]] = r["status"]
    return out


# --- streak math (pure, operate on a {iso: status} map) --------------------


def current_streak_from(smap: dict[str, str], today: _date) -> int:
    """Consecutive kept days counting back from `today` (see module docstring)."""
    if not smap:
        return 0
    earliest = min(smap)
    streak = 0
    d = today
    first = True
    while d.isoformat() >= earliest:
        s = smap.get(d.isoformat())
        if s in KEPT:
            streak += 1
        elif s == "skipped":
            pass  # neutral
        elif s is None and first:
            pass  # today not logged yet — pending, don't break
        else:
            break  # failed, or an empty day in the past
        first = False
        d -= timedelta(days=1)
    return streak


def best_streak_from(smap: dict[str, str], today: _date) -> int:
    """Longest kept run ever observed (failed/empty past days reset it)."""
    if not smap:
        return 0
    d = _date.fromisoformat(min(smap))
    best = run = 0
    while d <= today:
        s = smap.get(d.isoformat())
        if s in KEPT:
            run += 1
            best = max(best, run)
        elif s == "skipped":
            pass  # neutral: hold the run, don't extend it
        elif s is None and d == today:
            pass  # today pending doesn't break the historical run
        else:
            run = 0
        d += timedelta(days=1)
    return best


# --- per-item convenience (detail view) ------------------------------------


def current_streak(conn: sqlite3.Connection, item_id: int, today: str | None = None) -> int:
    return current_streak_from(history(conn, item_id), _date.fromisoformat(today or today_str()))


def best_streak(conn: sqlite3.Connection, item_id: int, today: str | None = None) -> int:
    return best_streak_from(history(conn, item_id), _date.fromisoformat(today or today_str()))


def total_checkins(conn: sqlite3.Connection, item_id: int) -> int:
    """Number of KEPT days (full_done or light_done) for this item, all-time."""
    row = conn.execute(
        "SELECT COUNT(*) FROM checkins WHERE routine_item_id = ? AND status IN (?, ?)",
        (item_id, *KEPT),
    ).fetchone()
    return row[0]


def status_counts(conn: sqlite3.Connection, item_id: int) -> dict[str, int]:
    """{status: count} across all time (for the detail breakdown)."""
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM checkins WHERE routine_item_id = ? GROUP BY status",
        (item_id,),
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def month_stats(
    conn: sqlite3.Connection, item_id: int, year: int, month: int, today: str | None = None
) -> dict:
    """Kept-day count and check-in rate for one month.

    Rate denominator = elapsed days (today's day-of-month for the current month,
    the full month for past months, 0 for future months) so an in-progress month
    isn't unfairly penalised for days that haven't happened yet.
    """
    today_d = _date.fromisoformat(today or today_str())
    days_in_month = _cal.monthrange(year, month)[1]
    rows = conn.execute(
        "SELECT date, status FROM checkins "
        "WHERE routine_item_id = ? AND date >= ? AND date <= ?",
        (item_id, _date(year, month, 1).isoformat(), _date(year, month, days_in_month).isoformat()),
    ).fetchall()
    kept = sum(1 for r in rows if r["status"] in KEPT)
    if (year, month) == (today_d.year, today_d.month):
        applicable = today_d.day
    elif _date(year, month, days_in_month) < today_d:
        applicable = days_in_month
    else:
        applicable = 0  # future month
    rate = round(kept / applicable * 100) if applicable else 0
    return {"kept": kept, "applicable": applicable, "rate": rate, "days_in_month": days_in_month}


def month_calendar(
    conn: sqlite3.Connection, item_id: int, year: int, month: int, today: str | None = None
) -> list[list[dict]]:
    """Sunday-start weeks of cells for the monthly heatmap. Each cell:
    {day, date, in_month, status, is_today, is_future}."""
    today_d = _date.fromisoformat(today or today_str())
    days_in_month = _cal.monthrange(year, month)[1]
    rows = conn.execute(
        "SELECT date, status FROM checkins "
        "WHERE routine_item_id = ? AND date >= ? AND date <= ?",
        (item_id, _date(year, month, 1).isoformat(), _date(year, month, days_in_month).isoformat()),
    ).fetchall()
    smap = {r["date"]: r["status"] for r in rows}
    grid = _cal.Calendar(firstweekday=6)  # 6 = Sunday
    weeks: list[list[dict]] = []
    for week in grid.monthdatescalendar(year, month):
        cells = []
        for d in week:
            iso = d.isoformat()
            in_month = d.month == month and d.year == year
            cells.append({
                "day": d.day,
                "date": iso,
                "in_month": in_month,
                "status": smap.get(iso) if in_month else None,
                "is_today": d == today_d,
                "is_future": d > today_d,
            })
        weeks.append(cells)
    return weeks


def recent_log(conn: sqlite3.Connection, item_id: int, limit: int = 20) -> list[sqlite3.Row]:
    """Most-recent check-ins that carry a note — the 'Habit Log' (sec16.6)."""
    return conn.execute(
        "SELECT date, status, note FROM checkins "
        "WHERE routine_item_id = ? AND note IS NOT NULL AND trim(note) <> '' "
        "ORDER BY date DESC LIMIT ?",
        (item_id, limit),
    ).fetchall()
