"""Templating, redirect and shared view helpers used by app/main.py and the routers.

The Jinja environment has one owner so every surface renders with the same
globals: main.py registers them (`templates.env.globals.update(...)`) and the
routers import the same object. `_with_flash` / `_safe_return` live here for the
same reason — the redirect tails of the write contract (sec16.4) are used by
routes on both sides of the split (#24).
"""
from __future__ import annotations

from datetime import date as _date, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from .db import is_not_future, is_valid_date, today_str
from .services import checkins, stats

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _with_flash(url: str, flash: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}flash={quote(flash)}"


def _safe_return(to: str | None, default: str = "/today") -> str:
    """A same-origin path to redirect back to after a task write (no open redirects)."""
    if to and to.startswith("/") and not to.startswith("//"):
        return to
    return default


# --- shared view helpers (#24 cut 2) ---------------------------------------
# Each of these has callers on both sides of a cut: the routers may not import
# app.main (that would be a cycle), so the ones a moved route needs live here,
# verbatim, in the module the split already designates as shared.


def _validated_write_date(date: str) -> str:
    if not is_valid_date(date):
        raise HTTPException(status_code=400, detail="invalid date (expected YYYY-MM-DD)")
    if not is_not_future(date):
        raise HTTPException(status_code=400, detail="date is in the future")
    return date


def _wants_json(request: Request) -> bool:
    return request.headers.get("x-partial") == "1"


def _sunday_of(d: _date) -> _date:
    """The Sunday starting d's week — weeks are Sunday-first everywhere (the week
    strip, the month grid's firstweekday=6, and the calendar week view)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _week_strip(conn, active: str) -> list[dict]:
    """Sunday-start week containing `active`, with a per-day logged count."""
    d = _date.fromisoformat(active)
    today = _date.fromisoformat(today_str())
    start = _sunday_of(d)
    days = [start + timedelta(days=i) for i in range(7)]
    iso = [x.isoformat() for x in days]
    rows = conn.execute(
        f"SELECT date, COUNT(*) AS n FROM checkins "
        f"WHERE date IN ({','.join('?' * len(iso))}) GROUP BY date",
        iso,
    ).fetchall()
    counts = {r["date"]: r["n"] for r in rows}
    return [
        {
            "date": x.isoformat(),
            "dow": x.strftime("%a"),
            "day": x.day,
            "is_today": x == today,
            "is_active": x.isoformat() == active,
            "is_future": x > today,
            "logged": counts.get(x.isoformat(), 0),
        }
        for x in days
    ]


def _enrich_groups(raw_groups, hist: dict, strip: list[dict], today_d: _date):
    """Turn (group, [Row]) into (group, [dict]) with streaks + weekly dots.

    Each item's `week_dots` align 1:1 with the week strip columns, coloured by the
    four-status model so a row shows its last 7 days at a glance (sec16.2). Streaks
    follow services.stats (light_done keeps the chain; skipped is neutral)."""
    groups = []
    for group_name, items in raw_groups:
        out = []
        for it in items:
            smap = hist.get(it["id"], {})
            out.append({
                "id": it["id"],
                "title": it["title"],
                "group_name": it["group_name"],
                "emoji": it["emoji"],
                "status": it["status"],
                "note": it["note"],
                "current_streak": stats.current_streak_from(smap, today_d),
                "best_streak": stats.best_streak_from(smap, today_d),
                # all-time kept days (full/light) — the "⚡ N Day" total on each row
                "total": sum(1 for s in smap.values() if s in ("full_done", "light_done")),
                "week_dots": [
                    {
                        "date": sd["date"],
                        "status": smap.get(sd["date"]),
                        "is_future": sd["is_future"],
                        "is_active": sd["is_active"],
                    }
                    for sd in strip
                ],
            })
        groups.append((group_name, out))
    return groups


def _parse_month(month: str | None) -> tuple[int, int]:
    """Parse ?month=YYYY-MM, defaulting to the current month; reject garbage."""
    if month:
        try:
            y, m = month.split("-")
            y, m = int(y), int(m)
            if 1 <= m <= 12 and 1900 <= y <= 2999:
                return y, m
        except (ValueError, AttributeError):
            pass
    t = _date.fromisoformat(today_str())
    return t.year, t.month


def _habit_detail_ctx(conn, item_id: int, month: str | None, base: str) -> dict | None:
    """Shared context for the habit detail (full page + inline pane, sec16.6).

    `base` is the URL the month-paging controls return to (carrying the right
    selection), so the pane stays put when you page months."""
    item = conn.execute("SELECT * FROM routine_items WHERE id = ?", (item_id,)).fetchone()
    if item is None:
        return None
    year, mon = _parse_month(month)
    first = _date(year, mon, 1)
    prev_first = (first - timedelta(days=1)).replace(day=1)
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    today_d = _date.fromisoformat(today_str())
    sep = "&" if "?" in base else "?"
    today_row = checkins.get_checkin(conn, today_str(), item_id)
    return {
        "item": item,
        "current_streak": stats.current_streak(conn, item_id),
        "best_streak": stats.best_streak(conn, item_id),
        "total": stats.total_checkins(conn, item_id),
        "month_stats": stats.month_stats(conn, item_id, year, mon),
        "weeks": stats.month_calendar(conn, item_id, year, mon),
        "year_map": stats.year_map(conn, item_id),
        "log": stats.recent_log(conn, item_id),
        "month_label": first.strftime("%B %Y"),
        "month_prev_url": f"{base}{sep}month={prev_first.strftime('%Y-%m')}",
        "month_next_url": f"{base}{sep}month={next_first.strftime('%Y-%m')}",
        "can_next": (year, mon) < (today_d.year, today_d.month),
        "today": today_str(),
        # Today check-in control in the pane (sec31)
        "today_status": today_row["status"] if today_row else None,
        "today_note": (today_row["note"] if today_row else "") or "",
        "pane_return": base,
    }
