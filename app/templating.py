"""Templating, redirect and shared view helpers used by app/main.py and the routers.

The Jinja environment has one owner so every surface renders with the same
globals: this module registers them (`templates.env.globals.update(...)`, #24
cut 3) and the routers import the same object. `client_is_local` is the one
global still registered by main.py — it comes from app.terminal, which this
module deliberately does not import. `_with_flash` / `_safe_return` live here
for the same reason as the rest — the redirect tails of the write contract
(sec16.4) are used by routes on both sides of the split (#24).
"""
from __future__ import annotations

import math
from datetime import date as _date, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from . import settings
from .db import is_not_future, is_valid_date, pretty_date, today_str
from .services import checkins, focus, lists, stats, tasks

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
            start = stats.as_start(it["start_date"])
            out.append({
                "id": it["id"],
                "title": it["title"],
                "group_name": it["group_name"],
                "emoji": it["emoji"],
                "status": it["status"],
                "note": it["note"],
                "current_streak": stats.current_streak_from(smap, today_d, start),
                "best_streak": stats.best_streak_from(smap, today_d, start),
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
        # a habit whose start_date is still ahead cannot be checked in (#18)
        "not_started": bool(item["start_date"] and today_str() < item["start_date"]),
        # Focused time recorded against this habit (#75). Per-target stats
        # live where the target lives, now that the Focus page is gone.
        "focus_total": focus.habit_total(conn, item_id),
        # Today check-in control in the pane (sec31)
        "today_status": today_row["status"] if today_row else None,
        "today_note": (today_row["note"] if today_row else "") or "",
        "pane_return": base,
    }


# --- shared view helpers + Jinja globals (#24 cut 3) -----------------------
# Moved verbatim from app/main.py. The display helpers below are registered as
# Jinja globals right here, so the environment has exactly one owner; the sole
# exception is `client_is_local`, which comes from app.terminal and is still
# registered by main.py — this module must not import that surface.


def static_url(path: str) -> str:
    """Versioned URL for a static asset: /static/<path>?v=<mtime>. StaticFiles
    sends no Cache-Control, so browsers cache heuristically; keying the URL on the
    file's own mtime forces a refetch after an edit/deploy. A render-time call
    (not a frozen global), so it stays fresh on a running server, and each asset
    gets its own token — adding one needs no registry, just {{ static_url(...) }}."""
    try:
        v = int((BASE_DIR / "static" / path).stat().st_mtime)
    except OSError:
        v = 0
    return f"/static/{path}?v={v}"


# Status display metadata (sec16.5): a distinct glyph per status so state reads
# without color too. Order = how positive→negative the outcome is.
STATUS_META = [
    {"key": "full_done", "label": "Full", "glyph": "✓"},
    {"key": "light_done", "label": "Light", "glyph": "◐"},
    {"key": "skipped", "label": "Skip", "glyph": "–"},
    {"key": "failed", "label": "Fail", "glyph": "✕"},
]
_GLYPH = {s["key"]: s["glyph"] for s in STATUS_META}

# Short human description shown as a row's meta line once it's been logged
# (replaces the redundant group name — the section header already shows that).
STATUS_DESC = {
    "full_done": "Done",
    "light_done": "Light · chain kept",
    "skipped": "Skipped",
    "failed": "Missed",
}


def status_glyph(status: str | None) -> str:
    return _GLYPH.get(status or "", "")


def status_desc(status: str | None) -> str:
    return STATUS_DESC.get(status or "", "")


# Emoji avatars derived from the item title (our own mapping; no copied assets).
_EMOJI_MAP = [
    (("sleep", "rest", "bed"), "😴"),
    (("food", "eat", "meal", "breakfast", "lunch", "dinner"), "🍽️"),
    (("sport", "gym", "workout", "train", "exercise", "show up"), "🏋️"),
    (("walk",), "🚶"),
    (("run", "jog"), "🏃"),
    (("output", "write", "writ", "code", "coding", "build", "ship"), "💻"),
    (("clean", "tidy", "chore"), "🧹"),
    (("read", "book"), "📖"),
    (("study", "learn", "course", "rustlings", "rust", "typescript", "codecrafters"), "📚"),
    (("water", "hydrate", "drink"), "💧"),
    (("medit", "mindful", "calm", "breath"), "🧘"),
    (("journal", "reflect", "review"), "📓"),
]


def item_avatar(title: str) -> dict:
    """Return an emoji avatar, or a colored letter avatar when nothing matches."""
    t = title.lower()
    for keys, emoji in _EMOJI_MAP:
        if any(k in t for k in keys):
            return {"emoji": emoji, "letter": None, "hue": 0}
    letter = (title.strip()[:1] or "?").upper()
    hue = sum(ord(c) for c in title) % 360
    return {"emoji": None, "letter": letter, "hue": hue}


def due_label(date_str: str | None, today: str | None = None) -> str:
    """Friendly relative due date for a task row, e.g. Today / Tomorrow / Mon."""
    if not date_str:
        return ""
    today = today or today_str()
    d = _date.fromisoformat(date_str)
    delta = (d - _date.fromisoformat(today)).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if delta == -1:
        return "Yesterday"
    if -7 < delta < 0:
        return f"{-delta}d ago"
    if 1 < delta <= 7:
        return d.strftime("%a")
    if d.year == _date.fromisoformat(today).year:
        return pretty_date(d)
    return pretty_date(d, year=True)


# Where the Tasks rail icon points (#53). A Jinja global rather than a per-route
# context variable so every surface agrees, and so the LIVE service — which
# renders these templates from the working tree while still running the pre-#53
# routers, i.e. without GET /board — resolves it as undefined and keeps sending
# people to /today instead of a 404. Templates must therefore read it as
# `tasks_home | default('/today')`, and `{% if tasks_home is defined %}` is what
# guards board-only chrome.
TASKS_HOME = "/board"

# Where the Diary rail icon points (#2). A global for the same live-skew
# reason as TASKS_HOME: the running pre-#2 service renders base.html from the
# working tree without this module's update, resolves `diary_home` as
# undefined, and the template hides the link instead of serving a 404.
DIARY_HOME = "/diary"


def mirror_home() -> str | None:
    """Where the Mirror rail icon points (#128), or None when no exp2res
    mirror URL is configured — the nav entry disappears entirely instead of
    linking a 404. A callable, unlike TASKS_HOME/DIARY_HOME, because this
    gate is runtime config, not just deploy skew; the `is defined` guard in
    base.html still covers the live pre-#128 process the same way."""
    return "/mirror" if settings.settings.exp2res_mirror_url else None


# The mass of a Learn cluster is the rolled-up lesson count #166 computes, and
# it picks the spectral class its branch is drawn in. The thresholds are far
# apart deliberately (owner, 2026-08-09): a platform root sums every course
# under it, so the ladder is climbed over years and a small install is all red
# dwarfs on purpose.
STAR_BODIES: tuple[tuple[int, str, str], ...] = (
    (20, "star-m", "red dwarf"),
    (50, "star-k", "orange dwarf"),
    (110, "star-g", "yellow dwarf"),
    (230, "star-f", "white star"),
    (999, "star-b", "blue giant"),
)
# Past this a cluster is a black hole, and grows logarithmically: a body that
# size does not visibly swell when you add a course, and a linear rule would
# have the disc eating the panel by the second thousand.
BLACK_HOLE_MASS = 1000
BLACK_HOLE_MIN_PX, BLACK_HOLE_MAX_PX, BLACK_HOLE_PER_DOUBLING = 12, 30, 3


def star_body(total: int | None) -> dict:
    """The celestial body a cluster of `total` lessons renders as.

    `size` is None for a star — those take their size from their class — and a
    pixel diameter for a black hole, which keeps growing after the ladder ends.
    """
    mass = max(0, int(total or 0))
    if mass < BLACK_HOLE_MASS:
        for limit, cls, label in STAR_BODIES:
            if mass <= limit:
                return {"cls": cls, "label": label, "size": None}
        return {"cls": STAR_BODIES[-1][1], "label": STAR_BODIES[-1][2], "size": None}
    doublings = math.log2(mass / BLACK_HOLE_MASS)
    px = min(BLACK_HOLE_MAX_PX, BLACK_HOLE_MIN_PX + BLACK_HOLE_PER_DOUBLING * doublings)
    return {"cls": "star-bh", "label": "black hole", "size": round(px, 1)}


templates.env.globals.update(
    static_url=static_url,
    tasks_home=TASKS_HOME,
    diary_home=DIARY_HOME,
    mirror_home=mirror_home,
    star_body=star_body,
    avatar=item_avatar,
    status_glyph=status_glyph,
    status_desc=status_desc,
    status_meta=STATUS_META,
    due_label=due_label,
)


# --- day view (shared by Today + History) ----------------------------------


def _render_day(request: Request, conn, date: str, nav_active: str, flash: str | None,
                rail: str = "habit"):
    raw_groups = checkins.today_view(conn, date)
    daily_note = checkins.get_daily_note(conn, date)
    strip = _week_strip(conn, date)
    hist = stats.all_histories(conn)
    d = _date.fromisoformat(date)
    groups = _enrich_groups(raw_groups, hist, strip, _date.fromisoformat(today_str()))
    total = sum(len(items) for _, items in groups)
    done = sum(
        1 for _, items in groups for it in items
        if it["status"] in ("full_done", "light_done")
    )
    return templates.TemplateResponse(request,
        "habit_day.html",
        {
            "request": request,
            "date": date,
            "weekday": d.strftime("%A"),
            "pretty_date": pretty_date(d),
            "is_today": date == today_str(),
            "groups": groups,
            "daily_note": daily_note,
            "week": strip,
            "done": done,
            "total": total,
            "flash": flash,
            "nav_active": nav_active,
            "rail": rail,
        },
    )


# --- tasks view (Today / lists / smart lists, sec21) -----------------------


def _selection_ctx(conn, request: Request, sel: str | None, month: str | None) -> dict:
    """Parse ?sel=task-N / habit-N into the detail-pane context (or empty)."""
    none = {"sel": None, "sel_id": None}
    if not sel:
        return none
    kind, _, raw = sel.partition("-")
    try:
        sid = int(raw)
    except ValueError:
        return none
    if kind == "task":
        task = tasks.get_task(conn, sid)
        if task is None:
            return none
        return {"sel": "task", "sel_id": sid, "task": task,
                "focus_total": focus.task_total(conn, sid),
                "close_url": request.url.path}
    if kind == "habit":
        ctx = _habit_detail_ctx(conn, sid, month, f"{request.url.path}?sel=habit-{sid}")
        if ctx is None:
            return none
        ctx.update(sel="habit", sel_id=sid, pane=True, close_url=request.url.path)
        return ctx
    return none


def _render_tasks(request: Request, conn, *, page_title: str, active: str, sections: list,
                  show_add: bool, add_list_id=None, add_list_name: str = "",
                  add_due: str | None = None, add_kind: str = "task",
                  sel: str | None = None, month: str | None = None,
                  flash: str | None = None, rail: str = "tasks", pulse=None,
                  template: str = "tasks.html", extra: dict | None = None):
    """Render a tasks surface: list-sidebar + body + (optional) detail pane.

    `template` and `extra` are what the board (#53) needs on top: it is the same
    page furniture — sidebar, quick-add, counts, `?sel=` pane — with columns
    instead of sections, so it renders through this one owner rather than
    growing a second copy of the context."""
    ctx = {
        "request": request,
        "rail": rail,
        "active": active,
        "page_title": page_title,
        "pulse": pulse,
        "sections": sections,
        "show_add": show_add,
        "add_list_id": add_list_id,
        "add_list_name": add_list_name,
        "add_due": add_due,
        "add_kind": add_kind,
        "today": today_str(),
        "cur_path": request.url.path,
        "in_list": active.startswith("list-"),
        "flash": flash,
        # list-sidebar
        "lists": lists.list_lists(conn),
        "today_count": tasks.today_count(conn),
        "next7_count": tasks.next7_count(conn),
    }
    ctx.update(extra or {})
    ctx.update(_selection_ctx(conn, request, sel, month))
    return templates.TemplateResponse(request, template, ctx)
