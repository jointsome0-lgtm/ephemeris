"""Ephemeris FastAPI app — daily execution surface + write contract.

Implements system-design.md sec15 (routes), sec16.4 (status & note write
contract — Mode A no-JS forms + Mode B fetch), and sec20 (security: same-origin
guard, Jinja autoescape only, no-auth LAN warning).

The Today and History screens share one day-view renderer; the week strip moves
between days. UI patterns follow docs/reference/ux-primitives.md (P2 sections
with counts, P3 one primary affordance per row, P10 bottom tabs) — pattern-level
only, our own styling/assets (sec7.3).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date as _date, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .db import get_conn, init_db, is_not_future, is_valid_date, now_iso, today_str
from .security import install_security
from .services import calendar_events, checkins, export, focus, items, lessons, lists, quickadd, stats, tasks
from .terminal import client_is_local, setup_terminal, shutdown_terminal

log = logging.getLogger("activity_ledger")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: migrate + seed once. (Replaces the deprecated on_event hook.)"""
    init_db()
    conn = get_conn()
    try:
        created = checkins.seed_if_empty(conn)
        lists.seed_if_empty(conn)          # Inbox + sample lists (before tasks)
        tasks.seed_if_empty(conn)          # sample tasks reference seeded lists
    finally:
        conn.close()
    if created:
        log.info("Seeded %d routine items", created)
    log.warning(
        "Ephemeris has NO AUTH (sec20): serve only on a trusted LAN; "
        "never expose to the public internet."
    )
    yield
    await shutdown_terminal()  # kill any persistent terminal shells on shutdown


app = FastAPI(title="Ephemeris", lifespan=_lifespan)
# Request perimeter (issue #15): trusted-host allowlist + central write guard
# for ALL unsafe methods + global security headers — see app/security.py.
install_security(app)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

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
        return d.strftime("%b %-d")
    return d.strftime("%b %-d, %Y")


def countdown_label(date_str: str | None, today: str | None = None) -> str:
    """Days-to-event label for a countdown card, e.g. '12 days left'."""
    if not date_str:
        return "no date"
    today = today or today_str()
    delta = (_date.fromisoformat(date_str) - _date.fromisoformat(today)).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if delta > 1:
        return f"{delta} days left"
    if delta == -1:
        return "Yesterday"
    return f"{-delta} days ago"


templates.env.globals.update(
    static_url=static_url,
    avatar=item_avatar,
    status_glyph=status_glyph,
    status_desc=status_desc,
    status_meta=STATUS_META,
    due_label=due_label,
    countdown_label=countdown_label,
    client_is_local=client_is_local,  # gates the terminal drawer in base.html
)

# Desktop / localhost-only terminal tab (app/terminal.py): PTY ↔ xterm.js over WS.
setup_terminal(app)


# --- security / validation (sec20, sec13.3) --------------------------------
# The same-origin write guard is no longer a per-route call: app/security.py
# enforces it in middleware for every unsafe-method request, so a new POST
# route is covered without remembering anything.


def _validated_write_date(date: str) -> str:
    if not is_valid_date(date):
        raise HTTPException(status_code=400, detail="invalid date (expected YYYY-MM-DD)")
    if not is_not_future(date):
        raise HTTPException(status_code=400, detail="date is in the future")
    return date


def _wants_json(request: Request) -> bool:
    return request.headers.get("x-partial") == "1"


def _redirect_for(date: str, anchor: str, flash: str | None = None) -> str:
    base = "/habits" if date == today_str() else f"/history?date={date}"
    if flash:
        sep = "&" if "?" in base else "?"
        base = f"{base}{sep}flash={quote(flash)}"
    return f"{base}#{anchor}" if anchor else base


def _with_flash(url: str, flash: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}flash={quote(flash)}"


def _safe_return(to: str | None, default: str = "/today") -> str:
    """A same-origin path to redirect back to after a task write (no open redirects)."""
    if to and to.startswith("/") and not to.startswith("//"):
        return to
    return default


# --- day view (shared by Today + History) ----------------------------------


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


def _render_day(request: Request, date: str, nav_active: str, flash: str | None,
                rail: str = "habit"):
    conn = get_conn()
    try:
        raw_groups = checkins.today_view(conn, date)
        daily_note = checkins.get_daily_note(conn, date)
        strip = _week_strip(conn, date)
        hist = stats.all_histories(conn)
    finally:
        conn.close()
    d = _date.fromisoformat(date)
    groups = _enrich_groups(raw_groups, hist, strip, _date.fromisoformat(today_str()))
    total = sum(len(items) for _, items in groups)
    done = sum(
        1 for _, items in groups for it in items
        if it["status"] in ("full_done", "light_done")
    )
    return templates.TemplateResponse(request,
        "today.html",
        {
            "request": request,
            "date": date,
            "weekday": d.strftime("%A"),
            "pretty_date": d.strftime("%b %-d"),
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
        return {"sel": "task", "sel_id": sid, "task": task, "close_url": request.url.path}
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
                  flash: str | None = None, rail: str = "tasks", pulse=None):
    """Render tasks.html: list-sidebar + sections + (optional) detail pane."""
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
    ctx.update(_selection_ctx(conn, request, sel, month))
    return templates.TemplateResponse(request,"tasks.html", ctx)


def _habit_rows(conn, today: str) -> list[dict]:
    """Active habits as compact task-style rows with today's status + streak."""
    hist = stats.all_histories(conn)
    today_d = _date.fromisoformat(today)
    rows = []
    for _group, group_items in checkins.today_view(conn, today):
        for it in group_items:
            smap = hist.get(it["id"], {})
            rows.append({
                "id": it["id"],
                "title": it["title"],
                "status": it["status"],
                "current_streak": stats.current_streak_from(smap, today_d),
            })
    return rows


# --- routes ----------------------------------------------------------------


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/")
@app.get("/today")
def get_today(request: Request, sel: str | None = None, month: str | None = None,
              flash: str | None = None):
    """Today as a task list (sec21): Countdown / Habit / Tasks / Completed."""
    conn = get_conn()
    try:
        today = today_str()
        sections = [
            {"title": "Countdown", "kind": "countdown", "rows": tasks.countdowns(conn, today)},
            {"title": "Habit", "kind": "habit", "rows": _habit_rows(conn, today)},
            {"title": "Tasks", "kind": "task", "rows": tasks.today_tasks(conn, today)},
            {"title": "Completed", "kind": "task", "rows": tasks.completed_on(conn, today)},
        ]
        return _render_tasks(
            request, conn, page_title="Today", active="today", sections=sections,
            show_add=True, add_list_id=lists.inbox_id(conn), add_list_name="Inbox",
            add_due=today, sel=sel, month=month, flash=flash,
            pulse=stats.week_pulse(conn, today),
        )
    finally:
        conn.close()


@app.get("/next7")
def get_next7(request: Request, sel: str | None = None, month: str | None = None,
              flash: str | None = None):
    conn = get_conn()
    try:
        today = today_str()
        by_day: dict[str, list] = {}
        for t in tasks.next7(conn, today):
            by_day.setdefault(t["due_date"], []).append(t)
        sections = [
            {"title": due_label(day, today), "kind": "task", "rows": rows}
            for day, rows in sorted(by_day.items())
        ]
        return _render_tasks(
            request, conn, page_title="Next 7 Days", active="next7", sections=sections,
            show_add=True, add_list_id=lists.inbox_id(conn), add_list_name="Inbox",
            add_due=today, sel=sel, month=month, flash=flash,
        )
    finally:
        conn.close()


@app.get("/list/{list_id}")
def get_list_view(request: Request, list_id: int, sel: str | None = None,
                  month: str | None = None, flash: str | None = None):
    conn = get_conn()
    try:
        lst = lists.get_list(conn, list_id)
        if lst is None or lst["archived_at"] is not None:
            raise HTTPException(status_code=404, detail="unknown list")
        every = tasks.list_tasks(conn, list_id, include_done=True)
        sections = [
            {"title": "Tasks", "kind": "task", "rows": [t for t in every if not t["completed_at"]]},
            {"title": "Completed", "kind": "task", "rows": [t for t in every if t["completed_at"]]},
        ]
        return _render_tasks(
            request, conn, page_title=f'{lst["emoji"] or ""} {lst["name"]}'.strip(),
            active=f"list-{list_id}", sections=sections, show_add=True,
            add_list_id=list_id, add_list_name=lst["name"], add_due=None,
            sel=sel, month=month, flash=flash,
        )
    finally:
        conn.close()


@app.get("/completed")
def get_completed(request: Request, sel: str | None = None, month: str | None = None,
                  flash: str | None = None):
    conn = get_conn()
    try:
        sections = [{"title": "Completed", "kind": "task", "rows": tasks.recent_completed(conn, 200)}]
        return _render_tasks(
            request, conn, page_title="Completed", active="completed", sections=sections,
            show_add=False, sel=sel, month=month, flash=flash,
        )
    finally:
        conn.close()


@app.get("/trash")
def get_trash(request: Request):
    """Trash placeholder — tasks are reversible toggles, so nothing is hard-deleted."""
    conn = get_conn()
    try:
        return _render_tasks(
            request, conn, page_title="Trash", active="trash", sections=[], show_add=False,
        )
    finally:
        conn.close()


@app.get("/countdown")
def get_countdown(request: Request, sel: str | None = None, month: str | None = None,
                  flash: str | None = None):
    """Countdown events (kind='countdown'), nearest first — the rail's ⏳ tab."""
    conn = get_conn()
    try:
        sections = [{"title": "Countdown", "kind": "countdown", "rows": tasks.countdowns(conn)}]
        return _render_tasks(
            request, conn, page_title="Countdown", active="countdown", sections=sections,
            show_add=True, add_list_id=lists.inbox_id(conn), add_list_name="Inbox",
            add_kind="countdown", sel=sel, month=month, flash=flash, rail="countdown",
        )
    finally:
        conn.close()


@app.get("/search")
def get_search(request: Request, q: str = ""):
    """Substring search over task titles + notes, plus Learn lessons."""
    q = (q or "").strip()
    conn = get_conn()
    try:
        results = tasks.search(conn, q) if q else []
        lesson_hits = lessons.search(conn, q) if q else []
    finally:
        conn.close()
    return templates.TemplateResponse(request,
        "search.html",
        {
            "request": request, "rail": "search", "q": q,
            "results": results, "lessons": lesson_hits,
            "today": today_str(), "cur_path": "/search",
        },
    )


# --- Calendar (month grid) + Eisenhower Matrix (sec: premium views) ---------


def _task_chip(t) -> dict:
    """A due task/countdown as a calendar chip — shared by the month + week views
    (the templates' chip class ladder reads exactly these keys)."""
    return {
        "title": t["title"], "kind": t["kind"],
        "completed": t["completed_at"] is not None, "priority": t["priority"],
    }


def _month_grid(conn, year: int, month: int) -> list[list[dict]]:
    """Always six Sunday-start weeks for the month (TickTick fixes the grid at 6
    rows so its height never jumps), each cell carrying its day's task events."""
    import calendar as _cal
    today_d = _date.fromisoformat(today_str())
    grid = _cal.Calendar(firstweekday=6)  # 6 = Sunday
    first_cell = grid.monthdatescalendar(year, month)[0][0]
    days = [first_cell + timedelta(days=i) for i in range(42)]  # 6 weeks, fixed
    win_start, win_end = days[0].isoformat(), days[-1].isoformat()
    by_date: dict[str, list] = {}
    # Calendar events first so they sort above tasks in a cell (sec32 §13.6);
    # occurrences_between already returns all-day-first then by start_time.
    for o in calendar_events.occurrences_between(conn, win_start, win_end):
        by_date.setdefault(o["date"], []).append({**o, "kind": "event"})
    for t in tasks.due_between(conn, win_start, win_end):
        by_date.setdefault(t["due_date"], []).append(_task_chip(t))
    weeks: list[list[dict]] = []
    for w in range(6):
        cells = []
        for d in days[w * 7:(w + 1) * 7]:
            iso = d.isoformat()
            cells.append({
                "day": d.day,
                "date": iso,
                "in_month": d.month == month and d.year == year,
                "is_today": d == today_d,
                "month_abbr": d.strftime("%b"),
                "events": by_date.get(iso, []),
            })
        weeks.append(cells)
    return weeks


def _event_modal_ctx(conn, self_url: str, ev: str | None, on: str | None,
                     add: str | None = None, at: str | None = None) -> dict:
    """Context for the event modals on both calendar views (sec32 M3): the create
    modal always needs lists + today; ?ev=<id> opens the edit modal for that series
    (silently ignored if unknown/archived/garbage), with ?on=<date> carrying the
    clicked occurrence so the modal can offer Skip for exactly that day.
    ?add=<date>&at=<HH:MM> (the week grid's empty slots, M4) opens the CREATE modal
    prefilled instead — ignored when an edit modal is already being opened."""
    ctx = {"self_url": self_url, "today": today_str(),
           "cal_lists": lists.list_lists(conn),
           "edit_ev": None, "edit_exdates": [], "on": None,
           "new_date": None, "new_time": None}
    try:
        event_id = int(ev) if ev else None
    except ValueError:
        event_id = None
    if event_id is not None:
        row = calendar_events.get_event(conn, event_id)
        if row is not None and row["archived_at"] is None:
            ctx.update(edit_ev=row,
                       edit_exdates=calendar_events.exdates_of(row),
                       on=on if is_valid_date(on) else None)
    if ctx["edit_ev"] is None and is_valid_date(add):
        ctx.update(new_date=add,
                   new_time=at if calendar_events.is_valid_hhmm(at) else None)
    return ctx


# --- command palette (Ctrl/⌘K) index ----------------------------------------
_PALETTE_VIEWS = [
    {"label": "Tasks", "href": "/today", "icon": "tasks"},
    {"label": "Calendar", "href": "/calendar", "icon": "calendar"},
    {"label": "Focus", "href": "/focus", "icon": "focus"},
    {"label": "Matrix", "href": "/matrix", "icon": "matrix"},
    {"label": "Habits", "href": "/habits", "icon": "habit"},
    {"label": "Countdown", "href": "/countdown", "icon": "countdown"},
    {"label": "Learn", "href": "/learn", "icon": "learn"},
    {"label": "Search", "href": "/search", "icon": "search"},
    {"label": "Export", "href": "/export", "icon": "download"},
]
_PALETTE_ACTIONS = [
    {"label": "New task", "hint": "n", "shortcut": "n"},
    {"label": "Toggle theme", "hint": "t", "shortcut": "t"},
    {"label": "Keyboard shortcuts", "hint": "?", "shortcut": "?"},
]


@app.get("/palette.json")
def get_palette():
    """Index the command palette pulls at open: views, lists, habits, lessons, actions."""
    conn = get_conn()
    try:
        list_rows = lists.list_lists(conn)
        habit_rows = [r for r in items.list_items(conn) if r["active"]]
        try:
            lesson_rows = lessons.list_lessons(conn)
        except lessons.LessonError:
            lesson_rows = []
    finally:
        conn.close()
    return JSONResponse({
        "views": _PALETTE_VIEWS,
        "lists": [{"label": r["name"], "href": f"/list/{r['id']}",
                   "emoji": r["emoji"], "count": r["open_count"]} for r in list_rows],
        "habits": [{"label": r["title"], "href": f"/habit/{r['id']}",
                    "emoji": r["emoji"]} for r in habit_rows],
        "lessons": [{"label": r["title"], "href": _learn_url(lesson_id=r["id"])}
                    for r in lesson_rows],
        "actions": _PALETTE_ACTIONS,
    })


@app.get("/calendar")
def get_calendar(request: Request, month: str | None = None, ev: str | None = None,
                 on: str | None = None, flash: str | None = None):
    year, mon = _parse_month(month)
    first = _date(year, mon, 1)
    self_url = f"/calendar?month={first.strftime('%Y-%m')}"
    conn = get_conn()
    try:
        weeks = _month_grid(conn, year, mon)
        modal = _event_modal_ctx(conn, self_url, ev, on)
    finally:
        conn.close()
    prev_first = (first - timedelta(days=1)).replace(day=1)
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return templates.TemplateResponse(request,
        "calendar.html",
        {
            "request": request, "rail": "calendar",
            "month_label": first.strftime("%B %Y"),
            "weeks": weeks, "flash": flash, **modal,
            "prev_url": f"/calendar?month={prev_first.strftime('%Y-%m')}",
            "next_url": f"/calendar?month={next_first.strftime('%Y-%m')}",
        },
    )


# Timed week grid geometry: a fixed px-per-hour scale the template multiplies by.
_WEEK_HOUR_PX = 48          # height of one hour row
_WEEK_MIN_BLOCK_PX = 22     # floor so a 15-min slot stays legible (sec32 §6.1)
_WEEK_BAND = (6, 23)        # default visible band 06:00–23:00, expands to fit


def _week_ctx(conn, sun: _date) -> dict:
    """Build the Sunday-start week beginning at `sun` — the caller snaps via
    _sunday_of (firstweekday=6, matching the month grid): 7 day columns, an
    all-day row (all-day events + due tasks), and the timed grid with overlap
    columns (sec32 §6/§6.1)."""
    week_days = [sun + timedelta(days=i) for i in range(7)]
    start_iso, end_iso = week_days[0].isoformat(), week_days[-1].isoformat()
    occs = calendar_events.occurrences_between(conn, start_iso, end_iso)

    tasks_by_date: dict[str, list] = {}
    for t in tasks.due_between(conn, start_iso, end_iso):
        tasks_by_date.setdefault(t["due_date"], []).append(_task_chip(t))

    allday: dict[str, list] = {}
    timed: dict[str, list] = {}
    for o in occs:
        (timed if calendar_events.is_timed(o) else allday) \
            .setdefault(o["date"], []).append(o)

    # Lay each day out first — the engine owns all minute math (layout_day drops
    # all-day items and annotates canonical start_min/end_min, defaulting an open
    # end to +30 min) — so the band below always covers what actually renders.
    laid = {d.isoformat(): calendar_events.layout_day(timed.get(d.isoformat(), []))
            for d in week_days}

    # Visible band: default 06:00–23:00, widened (floor/ceil to the hour) to fit
    # any earlier/later timed occurrence anywhere in the week.
    band_start, band_end = _WEEK_BAND[0] * 60, _WEEK_BAND[1] * 60
    for o in (b for blocks in laid.values() for b in blocks):
        band_start = min(band_start, o["start_min"] // 60 * 60)
        band_end = max(band_end, -(-o["end_min"] // 60) * 60)  # ceil to the hour
    band_end = min(24 * 60, band_end)  # an open-ended 23:5x event (+30 min) ceils past midnight
    ppm = _WEEK_HOUR_PX / 60.0

    today_iso = today_str()
    # Current-time line (M4): rendered in today's column only, and only while
    # "now" falls inside the visible band (the band never widens just for it).
    now_top = None
    if week_days[0].isoformat() <= today_iso <= week_days[-1].isoformat():
        hhmm = now_iso()[11:16]  # wall-clock in the ledger zone (sec13.3)
        now_min = int(hhmm[:2]) * 60 + int(hhmm[3:])
        if band_start <= now_min <= band_end:
            now_top = round((now_min - band_start) * ppm, 1)
    days = []
    for d in week_days:
        iso = d.isoformat()
        blocks = []
        for o in laid[iso]:
            top = (o["start_min"] - band_start) * ppm
            height = max((o["end_min"] - o["start_min"]) * ppm, _WEEK_MIN_BLOCK_PX)
            blocks.append({
                "title": o["title"], "emoji": o["emoji"], "event_id": o["event_id"],
                "start_time": o["start_time"], "end_time": o["end_time"],
                "top": round(top, 1), "height": round(height, 1),
                "left": round(o["left"] * 100, 3), "width": round(o["width"] * 100, 3),
            })
        days.append({
            "date": iso, "dow": d.strftime("%a"), "dom": d.day,
            "is_today": iso == today_iso,
            "allday": allday.get(iso, []), "tasks": tasks_by_date.get(iso, []),
            "blocks": blocks,
        })

    hours = [{"label": f"{h:02d}:00", "top": round((h * 60 - band_start) * ppm, 1)}
             for h in range(band_start // 60, band_end // 60)]
    return {
        "days": days, "hours": hours, "now_top": now_top,
        "grid_h": int(round((band_end - band_start) * ppm)), "hour_px": _WEEK_HOUR_PX,
    }


def _parse_date(s: str | None) -> _date:
    """Parse ?date=YYYY-MM-DD, defaulting to today; reject garbage (same canonical
    date check as every other route — db.is_valid_date)."""
    return _date.fromisoformat(s if is_valid_date(s) else today_str())


@app.get("/calendar/week")
def get_calendar_week(request: Request, date: str | None = None, ev: str | None = None,
                      on: str | None = None, add: str | None = None,
                      at: str | None = None, flash: str | None = None):
    sun = _sunday_of(_parse_date(date))
    self_url = f"/calendar/week?date={sun.isoformat()}"
    conn = get_conn()
    try:
        ctx = _week_ctx(conn, sun)
        ctx.update(_event_modal_ctx(conn, self_url, ev, on, add, at), flash=flash)
    finally:
        conn.close()
    last = sun + timedelta(days=6)
    if sun.month == last.month:
        label = f"{sun.strftime('%b')} {sun.day}–{last.day}, {sun.year}"
    elif sun.year == last.year:
        label = f"{sun.strftime('%b')} {sun.day} – {last.strftime('%b')} {last.day}, {sun.year}"
    else:
        label = f"{sun.strftime('%b %-d, %Y')} – {last.strftime('%b %-d, %Y')}"
    ctx.update({
        "request": request, "rail": "calendar", "week_label": label,
        "prev_url": f"/calendar/week?date={(sun - timedelta(days=7)).isoformat()}",
        "next_url": f"/calendar/week?date={(sun + timedelta(days=7)).isoformat()}",
    })
    return templates.TemplateResponse(request, "calendar_week.html", ctx)


# --- Calendar-event writes (sec32 M3): create / update / archive / skip ----


def _wd_mask(wd: list[str]) -> str:
    """The form's 7 weekday checkboxes (values '0'..'6', Mon..Sun) → the stored
    byweekday mask. The service nulls it for non-weekly freqs and rejects an
    all-zero mask on weekly, so the route just assembles."""
    return "".join("1" if str(i) in wd else "0" for i in range(7))


def _events_redirect(return_to: str, flash: str | None = None) -> RedirectResponse:
    url = _safe_return(return_to, "/calendar")
    return RedirectResponse(_with_flash(url, flash) if flash else url, status_code=303)


@app.post("/calendar/events")
def post_event_create(
    request: Request,
    title: str = Form(...),
    emoji: str = Form(""),
    list_id: str = Form(""),
    note: str = Form(""),
    all_day: str | None = Form(None),
    start_time: str = Form(""),
    end_time: str = Form(""),
    freq: str = Form("once"),
    wd: list[str] = Form([]),
    interval_n: str = Form("1"),
    start_date: str = Form(...),
    end_date: str = Form(""),
    return_to: str = Form("/calendar"),
):
    conn = get_conn()
    try:
        calendar_events.create_event(
            conn, title, start_date=start_date, freq=freq, byweekday=_wd_mask(wd),
            interval_n=interval_n, all_day=bool(all_day), start_time=start_time,
            end_time=end_time, end_date=end_date, list_id=list_id, emoji=emoji, note=note,
        )
    except calendar_events.CalendarEventError as exc:
        return _events_redirect(return_to, str(exc))
    finally:
        conn.close()
    return _events_redirect(return_to)


@app.post("/calendar/events/{event_id}")
def post_event_update(
    request: Request,
    event_id: int,
    title: str = Form(...),
    emoji: str = Form(""),
    list_id: str = Form(""),
    note: str = Form(""),
    all_day: str | None = Form(None),
    start_time: str = Form(""),
    end_time: str = Form(""),
    freq: str = Form("once"),
    wd: list[str] = Form([]),
    interval_n: str = Form("1"),
    start_date: str = Form(...),
    end_date: str = Form(""),
    return_to: str = Form("/calendar"),
):
    """Update the whole series ("All events" — v1 has no per-occurrence override;
    use Skip for one day). exdates survive the edit (the service preserves them)."""
    conn = get_conn()
    try:
        calendar_events.update_event(
            conn, event_id, title=title, emoji=emoji, list_id=list_id, note=note,
            all_day=bool(all_day), start_time=start_time, end_time=end_time,
            freq=freq, byweekday=_wd_mask(wd), interval_n=interval_n,
            start_date=start_date, end_date=end_date,
        )
    except calendar_events.CalendarEventError as exc:
        return _events_redirect(return_to, str(exc))
    finally:
        conn.close()
    return _events_redirect(return_to)


@app.post("/calendar/events/{event_id}/archive")
def post_event_archive(request: Request, event_id: int, return_to: str = Form("/calendar")):
    conn = get_conn()
    try:
        calendar_events.archive_event(conn, event_id)  # soft: series stays in the ledger
    except calendar_events.CalendarEventError as exc:
        return _events_redirect(return_to, str(exc))
    finally:
        conn.close()
    return _events_redirect(return_to)


@app.post("/calendar/events/{event_id}/skip")
def post_event_skip(request: Request, event_id: int, date: str = Form(...),
                    return_to: str = Form("/calendar")):
    conn = get_conn()
    try:
        calendar_events.skip_occurrence(conn, event_id, date)
    except calendar_events.CalendarEventError as exc:
        return _events_redirect(return_to, str(exc))
    finally:
        conn.close()
    return _events_redirect(return_to)


@app.post("/calendar/events/{event_id}/unskip")
def post_event_unskip(request: Request, event_id: int, date: str = Form(...),
                      return_to: str = Form("/calendar")):
    conn = get_conn()
    try:
        calendar_events.unskip_occurrence(conn, event_id, date)
    except calendar_events.CalendarEventError as exc:
        return _events_redirect(return_to, str(exc))
    finally:
        conn.close()
    return _events_redirect(return_to)


@app.post("/calendar/events/{event_id}/move")
def post_event_move(request: Request, event_id: int, date: str = Form(...),
                    return_to: str = Form("/calendar")):
    """Drag-and-drop a non-recurring event to another day (Mode A/B)."""
    json_mode = _wants_json(request)
    conn = get_conn()
    try:
        calendar_events.move_event(conn, event_id, date)
        if json_mode:
            return JSONResponse({"ok": True, "event_id": event_id, "date": date})
    except calendar_events.CalendarEventError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _events_redirect(return_to, str(exc))
    finally:
        conn.close()
    return _events_redirect(return_to)


# Eisenhower quadrants keyed by our priority field (high→urgent+important … none→neither)
_MATRIX_QUADRANTS = [
    (3, "Urgent & Important"),
    (2, "Not Urgent & Important"),
    (1, "Urgent & Unimportant"),
    (0, "Not Urgent & Unimportant"),
]


@app.get("/matrix")
def get_matrix(request: Request):
    conn = get_conn()
    try:
        buckets: dict[int, list] = {3: [], 2: [], 1: [], 0: []}
        for t in tasks.all_open(conn):
            buckets.get(t["priority"], buckets[0]).append(t)
    finally:
        conn.close()
    # within a quadrant, manual order (sort_order) is primary so drag-reorder shows
    for rows in buckets.values():
        rows.sort(key=lambda t: (t["sort_order"], t["id"]))
    quadrants = [{"title": title, "priority": p, "rows": buckets[p]} for p, title in _MATRIX_QUADRANTS]
    return templates.TemplateResponse(request,
        "matrix.html", {"request": request, "rail": "matrix", "quadrants": quadrants}
    )


@app.get("/learn")
def get_learn(
    request: Request,
    status: str | None = None,
    archived: int = 0,
    lesson: int | None = None,
    entry: str | None = None,
    flash: str | None = None,
):
    show_archived = bool(archived)
    conn = get_conn()
    try:
        try:
            rows = lessons.list_lessons(conn, status=status, archived_only=show_archived)
        except lessons.LessonError:
            status = None
            rows = lessons.list_lessons(conn, archived_only=show_archived)
        counts = lessons.counts(conn)
        selected = None
        selected_entry = None
        if lesson is not None:
            selected = next((row for row in rows if row["id"] == lesson), None)
            if selected is not None:
                selected_entry = entry
        if selected is None and rows:
            selected = rows[0]
        selected = lessons.with_bundle_info(selected, entry=selected_entry)
        if selected:
            lessons.mark_opened(conn, selected["id"], selected["entry"])
    finally:
        conn.close()
    selected_id = selected["id"] if selected else None
    for row in rows:
        row["selected"] = row["id"] == selected_id
        row["href"] = _learn_url(status=status, archived=show_archived, lesson_id=row["id"])
    if selected:
        selected["file_url"] = _lesson_preview_url(selected["id"], selected["entry"])
        selected["preview_url"] = _lesson_preview_url(
            selected["id"],
            selected["entry"],
            exists=selected["file"]["exists"],
        )
        selected["preview_meta_url"] = _lesson_preview_url(selected["id"], selected["entry"], meta=True)
        for page in selected["pages"]:
            page["href"] = _learn_url(
                status=status,
                archived=show_archived,
                lesson_id=selected["id"],
                entry=page["entry"],
            )
    self_url = _learn_url(
        status=status,
        archived=show_archived,
        lesson_id=selected_id,
        entry=selected["entry"] if selected else None,
    )
    return templates.TemplateResponse(request, "learn.html", {
        "request": request,
        "rail": "learn",
        "rows": rows,
        "status_filter": status,
        "show_archived": show_archived,
        "counts": counts,
        "status_tabs": [{"key": key, "label": lessons.STATUS_LABELS[key]} for key in lessons.STATUSES],
        "selected": selected,
        "self_url": self_url,
        "flash": flash,
    })


def _learn_url(
    *,
    status: str | None = None,
    archived: bool = False,
    lesson_id: int | None = None,
    entry: str | None = None,
) -> str:
    query: list[tuple[str, str]] = []
    if status:
        query.append(("status", status))
    if archived:
        query.append(("archived", "1"))
    if lesson_id is not None:
        query.append(("lesson", str(lesson_id)))
    if entry:
        query.append(("entry", entry))
    return "/learn" + (f"?{urlencode(query)}" if query else "")


_LESSON_PREVIEW_CSP = (
    "sandbox allow-scripts allow-forms allow-popups allow-downloads; "
    "default-src 'self' data: blob: https:; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: https:; "
    "style-src 'self' 'unsafe-inline' data: https:; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data: https:; "
    "connect-src 'self' data: blob: https:; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
)


def _lesson_preview_url(
    lesson_id: int,
    entry: str | None,
    *,
    exists: bool = True,
    meta: bool = False,
) -> str:
    if not entry:
        entry = lessons.DEFAULT_ENTRY
    if meta:
        return f"/learn/lessons/{lesson_id}/preview-meta?{urlencode([('entry', entry)])}"
    if not exists:
        return f"/learn/lessons/{lesson_id}/preview?{urlencode([('entry', entry)])}"
    return f"/learn/lessons/{lesson_id}/files/{quote(entry, safe='/')}"


def _lesson_or_404(conn, lesson_id: int) -> dict:
    lesson = lessons.get_lesson(conn, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="unknown lesson")
    return lesson


@app.get("/learn/lessons/{lesson_id}/files/{resource:path}")
def get_lesson_bundle_file(lesson_id: int, resource: str):
    conn = get_conn()
    try:
        lesson = _lesson_or_404(conn, lesson_id)
        try:
            info = lessons.bundle_resource_info(lesson, resource)
        except lessons.LessonError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    if not info["exists"]:
        raise HTTPException(status_code=404, detail="lesson file not found")
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if info["active"]:
        headers["Content-Security-Policy"] = _LESSON_PREVIEW_CSP
        headers["X-Lesson-Preview-Version"] = info["version"]
    return FileResponse(info["path"], media_type=info["media_type"], headers=headers)


@app.get("/learn/lessons/{lesson_id}/preview")
def get_lesson_preview(lesson_id: int, entry: str | None = None):
    conn = get_conn()
    try:
        lesson = _lesson_or_404(conn, lesson_id)
        try:
            html, info = lessons.preview_html(lesson, entry)
        except lessons.LessonError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": _LESSON_PREVIEW_CSP,
            "X-Content-Type-Options": "nosniff",
            "X-Lesson-Preview-Version": info["version"],
        },
    )


@app.get("/learn/lessons/{lesson_id}/preview-meta")
def get_lesson_preview_meta(lesson_id: int, entry: str | None = None):
    conn = get_conn()
    try:
        lesson = _lesson_or_404(conn, lesson_id)
        try:
            info = lessons.lesson_file_info(lesson, entry)
        except lessons.LessonError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    return JSONResponse({
        "ok": True,
        "exists": info["exists"],
        "version": info["version"],
        "path": info["path"],
        "preview_url": _lesson_preview_url(lesson_id, info["entry"], exists=info["exists"]),
        "file_url": _lesson_preview_url(lesson_id, info["entry"]),
    })


@app.post("/learn/lessons")
def post_lesson_create(
    request: Request,
    title: str = Form(...),
    source_url: str = Form(""),
    return_to: str = Form("/learn"),
):
    conn = get_conn()
    try:
        lesson_id = lessons.create_lesson(conn, title, source_url)
    except lessons.LessonError as exc:
        return RedirectResponse(
            _with_flash(_safe_return(return_to, "/learn"), str(exc)), status_code=303
        )
    finally:
        conn.close()
    return RedirectResponse(f"/learn?lesson={lesson_id}", status_code=303)


@app.post("/learn/lessons/{lesson_id}/entry")
def post_lesson_entry(
    request: Request,
    lesson_id: int,
    entry: str = Form(...),
    return_to: str = Form("/learn"),
):
    conn = get_conn()
    try:
        lessons.set_current_entry(conn, lesson_id, entry)
    except lessons.LessonError as exc:
        return RedirectResponse(
            _with_flash(_safe_return(return_to, f"/learn?lesson={lesson_id}"), str(exc)),
            status_code=303,
        )
    finally:
        conn.close()
    return RedirectResponse(_safe_return(return_to, f"/learn?lesson={lesson_id}"), status_code=303)


@app.post("/learn/lessons/{lesson_id}/status")
def post_lesson_status(
    request: Request,
    lesson_id: int,
    status: str = Form(...),
    return_to: str = Form("/learn"),
):
    conn = get_conn()
    try:
        lessons.set_status(conn, lesson_id, status)
    except lessons.LessonError as exc:
        return RedirectResponse(
            _with_flash(_safe_return(return_to, "/learn"), str(exc)), status_code=303
        )
    finally:
        conn.close()
    return RedirectResponse(_safe_return(return_to, "/learn"), status_code=303)


@app.post("/learn/lessons/{lesson_id}/archive")
def post_lesson_archive(request: Request, lesson_id: int, return_to: str = Form("/learn")):
    conn = get_conn()
    try:
        lessons.archive_lesson(conn, lesson_id)
    except lessons.LessonError as exc:
        return RedirectResponse(
            _with_flash(_safe_return(return_to, "/learn"), str(exc)), status_code=303
        )
    finally:
        conn.close()
    return RedirectResponse(_safe_return(return_to, "/learn"), status_code=303)


@app.post("/learn/lessons/{lesson_id}/restore")
def post_lesson_restore(request: Request, lesson_id: int, return_to: str = Form("/learn")):
    conn = get_conn()
    try:
        lessons.restore_lesson(conn, lesson_id)
    except lessons.LessonError as exc:
        return RedirectResponse(
            _with_flash(_safe_return(return_to, "/learn"), str(exc)), status_code=303
        )
    finally:
        conn.close()
    return RedirectResponse(_safe_return(return_to, "/learn"), status_code=303)


@app.get("/focus")
def get_focus(request: Request):
    conn = get_conn()
    try:
        ov = focus.overview(conn)
        records = focus.recent_sessions(conn)
        lesson_opts = lessons.list_lessons(conn)
        daily = focus.daily_totals(conn)
        lesson_focus = focus.lesson_totals(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(request,
        "focus.html",
        {"request": request, "rail": "focus", "ov": ov, "records": records,
         "lessons": lesson_opts, "daily": daily, "lesson_focus": lesson_focus,
         "focus_streak": focus.focus_day_streak(daily)},
    )


@app.post("/focus/session")
def post_focus_session(
    request: Request,
    mode: str = Form("pomo"),
    seconds: int = Form(...),
    note: str = Form(""),
    lesson_id: str = Form(""),
    return_to: str = Form("/focus"),
):
    """Record a finished Pomodoro / stopwatch span. Mode B returns refreshed stats."""
    json_mode = _wants_json(request)
    conn = get_conn()
    try:
        sid = focus.record_session(conn, mode, seconds, note=note, lesson_id=lesson_id)
        if json_mode:
            return JSONResponse({
                "ok": True,
                "overview": focus.overview(conn),
                "record": focus.get_session_view(conn, sid),
            })
    except focus.FocusError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return RedirectResponse(
            _with_flash(_safe_return(return_to, "/focus"), str(exc)), status_code=303
        )
    finally:
        conn.close()
    return RedirectResponse(_safe_return(return_to, "/focus"), status_code=303)


# --- Export (sec15.4 / sec18.1): event stream + calendar series JSONL backup -


@app.get("/export")
def get_export(request: Request):
    """One-button export page: shows the event count + recent export files."""
    conn = get_conn()
    try:
        count = export.event_count(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(request,
        "export.html",
        {"request": request, "rail": "export",
         "event_count": count, "recent": export.recent_exports()},
    )


@app.post("/export/jsonl")
def post_export_jsonl(request: Request):
    """Write data/exports/events-<stamp>.jsonl AND stream it back as a download."""
    conn = get_conn()
    try:
        path, text, _count = export.export_events(conn)
    finally:
        conn.close()
    return Response(
        content=text,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


# --- Habit tab (TickTick-style: list + inline detail pane, sec31) ----------


def _habit_selection_ctx(conn, request: Request, sel: str | None, month: str | None,
                         edit: bool = False) -> dict:
    """Parse ?sel=habit-N into the detail-pane context (with optional edit mode)."""
    none = {"sel": None, "sel_id": None}
    if not sel:
        return none
    kind, _, raw = sel.partition("-")
    if kind != "habit":
        return none
    try:
        sid = int(raw)
    except ValueError:
        return none
    ctx = _habit_detail_ctx(conn, sid, month, f"{request.url.path}?sel=habit-{sid}")
    if ctx is None:
        return none
    ctx.update(sel="habit", sel_id=sid, pane=True, close_url=request.url.path, edit=edit)
    return ctx


def _render_habits(request: Request, sel=None, month=None, edit=False, flash=None):
    conn = get_conn()
    try:
        today = today_str()
        raw_groups = checkins.today_view(conn, today)
        strip = _week_strip(conn, today)
        hist = stats.all_histories(conn)
        groups = _enrich_groups(raw_groups, hist, strip, _date.fromisoformat(today))
        ctx = {
            "request": request, "rail": "habit", "date": today, "today": today,
            "pretty_date": _date.fromisoformat(today).strftime("%b %-d"),
            "week": strip, "groups": groups, "flash": flash,
            "daily_note": checkins.get_daily_note(conn, today),
            "sections": items.list_sections(conn),
            "default_section": (groups[0][0] if groups else items.DEFAULT_GROUP),
        }
        ctx.update(_habit_selection_ctx(conn, request, sel, month, edit))
        return templates.TemplateResponse(request,"habits.html", ctx)
    finally:
        conn.close()


@app.get("/habits")
def get_habits(request: Request, sel: str | None = None, month: str | None = None,
               edit: int = 0, flash: str | None = None):
    return _render_habits(request, sel=sel, month=month, edit=bool(edit), flash=flash)


@app.get("/history")
def get_history(request: Request, date: str | None = None, flash: str | None = None):
    date = date or today_str()
    date = _validated_write_date(date)  # valid + not future
    nav = "today" if date == today_str() else "history"
    return _render_day(request, date, nav, flash, rail="habit")


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


@app.get("/habit/{item_id}")
def get_habit(request: Request, item_id: int, month: str | None = None):
    """Per-item detail page (sec16.6): stat cards + monthly heatmap + habit log.

    Mirrors TickTick's habit detail pane in PATTERN only; uses our four-status
    model so the heatmap is richer than a binary done/not-done grid (sec7.3).
    The same partial renders inline on the tasks view via ?sel=habit-{id}."""
    conn = get_conn()
    try:
        ctx = _habit_detail_ctx(conn, item_id, month, f"/habit/{item_id}")
    finally:
        conn.close()
    if ctx is None:
        raise HTTPException(status_code=404, detail="unknown item")
    ctx.update(request=request, rail="habit")
    return templates.TemplateResponse(request,"habit.html", ctx)


def _checkin_state(conn, date: str, item_id: int) -> dict:
    row = checkins.get_checkin(conn, date, item_id)
    smap = stats.history(conn, item_id)
    today_d = _date.fromisoformat(today_str())
    return {
        "ok": True,
        "item_id": item_id,
        "date": date,
        "status": row["status"] if row else None,
        "note": (row["note"] if row else "") or "",
        # so Mode B can refresh the row's streak + total + that day's ring without a reload
        "current_streak": stats.current_streak_from(smap, today_d),
        "best_streak": stats.best_streak_from(smap, today_d),
        "total": sum(1 for s in smap.values() if s in ("full_done", "light_done")),
    }


@app.post("/checkins")
def post_checkin(
    request: Request,
    date: str = Form(...),
    routine_item_id: int = Form(...),
    status: str | None = Form(None),
    note: str | None = Form(None),
    return_to: str | None = Form(None),
):
    date = _validated_write_date(date)
    anchor = f"item-{routine_item_id}"
    json_mode = _wants_json(request)

    def dest(flash: str | None = None) -> str:
        # compact habit rows on the tasks view pass return_to to stay put;
        # the rich day view omits it and falls back to the habit day route.
        if return_to:
            url = _safe_return(return_to)
            return f"{_with_flash(url, flash) if flash else url}#{anchor}"
        return _redirect_for(date, anchor, flash=flash)

    conn = get_conn()
    try:
        if status is not None and status != "":
            checkins.apply_status(conn, date, routine_item_id, status)
        elif note is not None:
            checkins.upsert_checkin(conn, date, routine_item_id, note=note)
        else:
            raise HTTPException(status_code=400, detail="nothing to update")
        if json_mode:
            return JSONResponse(_checkin_state(conn, date, routine_item_id))
    except checkins.CheckinError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return RedirectResponse(dest(str(exc)), status_code=303)
    finally:
        conn.close()
    return RedirectResponse(dest(), status_code=303)


@app.post("/daily-note")
def post_daily_note(
    request: Request,
    date: str = Form(...),
    text: str = Form(""),
):
    date = _validated_write_date(date)
    conn = get_conn()
    try:
        checkins.upsert_daily_note(conn, date, text)
    finally:
        conn.close()
    if _wants_json(request):
        return JSONResponse({"ok": True, "date": date})
    return RedirectResponse(_redirect_for(date, "daily-note"), status_code=303)


# --- Tasks write contract (sec21) ------------------------------------------


@app.post("/lists")
def post_list_create(request: Request, name: str = Form(...), emoji: str = Form("")):
    """Create a user list from the sidebar's + modal, then open it."""
    conn = get_conn()
    try:
        list_id = lists.create_list(conn, name, emoji=emoji)
    except lists.ListError as exc:
        return RedirectResponse(_with_flash("/today", str(exc)), status_code=303)
    finally:
        conn.close()
    return RedirectResponse(f"/list/{list_id}", status_code=303)


@app.post("/tasks")
def post_task_create(
    request: Request,
    title: str = Form(...),
    list_id: int | None = Form(None),
    due_date: str | None = Form(None),
    kind: str = Form("task"),
    smart: str = Form(""),
    return_to: str = Form("/today"),
):
    priority = 0
    parsed_label = ""
    if smart in ("1", "true", "on"):
        p = quickadd.parse(title, today_str())
        title = p["title"] or title
        due_date = p["due_date"] or due_date
        priority = p["priority"]
        bits = []
        if p["due_date"]:
            bits.append(due_label(p["due_date"]))
        if priority:
            bits.append("!" + {3: "1", 2: "2", 1: "3"}[priority])
        parsed_label = " · ".join(bits)
    conn = get_conn()
    try:
        tasks.create_task(conn, title, list_id=list_id, due_date=(due_date or None),
                          kind=kind, priority=priority)
    except tasks.TaskError as exc:
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    finally:
        conn.close()
    if _wants_json(request):
        return JSONResponse({"ok": True, "label": parsed_label})
    dest = _safe_return(return_to)
    if parsed_label:
        dest = _with_flash(dest, f"Added · {parsed_label}")
    return RedirectResponse(dest, status_code=303)


@app.post("/tasks/{task_id}/complete")
def post_task_complete(request: Request, task_id: int, return_to: str = Form("/today")):
    json_mode = _wants_json(request)
    conn = get_conn()
    try:
        now_done = tasks.toggle_complete(conn, task_id)
        if json_mode:
            return JSONResponse({"ok": True, "task_id": task_id, "completed": now_done})
    except tasks.TaskError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    finally:
        conn.close()
    return RedirectResponse(_safe_return(return_to), status_code=303)


@app.post("/tasks/{task_id}/update")
def post_task_update(
    request: Request,
    task_id: int,
    title: str = Form(...),
    note: str = Form(""),
    due_date: str = Form(""),
    priority: int = Form(0),
    list_id: int = Form(...),
    return_to: str = Form("/today"),
):
    conn = get_conn()
    try:
        tasks.update_task(
            conn, task_id, title=title, note=note,
            due_date=(due_date or None), priority=priority, list_id=list_id,
        )
    except tasks.TaskError as exc:
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    finally:
        conn.close()
    return RedirectResponse(_safe_return(return_to), status_code=303)


@app.post("/tasks/{task_id}/move")
def post_task_move(
    request: Request,
    task_id: int,
    priority: str = Form(""),
    after: str = Form(""),
    before: str = Form(""),
    return_to: str = Form("/matrix"),
):
    """Drag-and-drop reposition (matrix): `priority` = target quadrant (optional),
    `after`/`before` = the task ids now above/below the drop slot."""
    json_mode = _wants_json(request)

    def _int_or_none(v: str):
        v = (v or "").strip()
        return int(v) if v.lstrip("-").isdigit() else None

    conn = get_conn()
    try:
        res = tasks.move_task(
            conn, task_id, priority=_int_or_none(priority),
            after_id=_int_or_none(after), before_id=_int_or_none(before),
        )
        if json_mode:
            return JSONResponse({"ok": True, "task_id": task_id, **res})
    except tasks.TaskError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    finally:
        conn.close()
    return RedirectResponse(_safe_return(return_to), status_code=303)


# --- Habit tab writes (sec31): create / edit / archive / delete ------------


@app.post("/habits")
def post_habit_create(
    request: Request,
    title: str = Form(...),
    group_name: str = Form(""),
    emoji: str = Form(""),
    frequency: str = Form("daily"),
    goal: str = Form("achieve_all"),
    goal_days: str = Form("forever"),
    start_date: str = Form(""),
    reminder: str = Form(""),
    constant_reminder: str | None = Form(None),
    return_to: str = Form("/habits"),
):
    conn = get_conn()
    try:
        items.create_item(
            conn, title, group_name, emoji=emoji, frequency=frequency, goal=goal,
            goal_days=goal_days, start_date=(start_date or None),
            reminder=(reminder or None), constant_reminder=bool(constant_reminder),
        )
    except items.ItemError as exc:
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    finally:
        conn.close()
    return RedirectResponse(_safe_return(return_to), status_code=303)


@app.post("/habits/{item_id}/edit")
def post_habit_edit(
    request: Request,
    item_id: int,
    title: str = Form(...),
    group_name: str = Form(""),
    emoji: str = Form(""),
    frequency: str = Form("daily"),
    goal: str = Form("achieve_all"),
    goal_days: str = Form("forever"),
    start_date: str = Form(""),
    reminder: str = Form(""),
    constant_reminder: str | None = Form(None),
    return_to: str = Form("/habits"),
):
    conn = get_conn()
    try:
        items.update_item(
            conn, item_id, title, group_name, emoji=emoji, frequency=frequency,
            goal=goal, goal_days=goal_days, start_date=(start_date or None),
            reminder=(reminder or None), constant_reminder=bool(constant_reminder),
        )
    except items.ItemError as exc:
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    finally:
        conn.close()
    return RedirectResponse(_safe_return(return_to), status_code=303)


@app.post("/habits/{item_id}/archive")
def post_habit_archive(request: Request, item_id: int, return_to: str = Form("/habits")):
    conn = get_conn()
    try:
        items.deactivate_item(conn, item_id)  # soft retire; history kept
    except items.ItemError as exc:
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    finally:
        conn.close()
    return RedirectResponse(_safe_return(return_to), status_code=303)


@app.post("/habits/{item_id}/delete")
def post_habit_delete(request: Request, item_id: int, return_to: str = Form("/habits")):
    conn = get_conn()
    try:
        items.delete_item(conn, item_id)  # hard delete (events keep the audit trail)
    except items.ItemError as exc:
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    finally:
        conn.close()
    return RedirectResponse(_safe_return(return_to), status_code=303)


# --- Manage Items (sec15.3) ------------------------------------------------


def _items_redirect(flash: str | None = None) -> RedirectResponse:
    url = "/items" + (f"?flash={quote(flash)}" if flash else "")
    return RedirectResponse(url, status_code=303)


@app.get("/items")
def get_items(request: Request, flash: str | None = None):
    conn = get_conn()
    try:
        rows = items.list_items(conn)
    finally:
        conn.close()
    groups: list[tuple[str, list]] = []
    index: dict[str, list] = {}
    for r in rows:
        if not r["active"]:
            continue
        bucket = index.get(r["group_name"])
        if bucket is None:
            bucket = []
            index[r["group_name"]] = bucket
            groups.append((r["group_name"], bucket))
        bucket.append(r)
    inactive = [r for r in rows if not r["active"]]
    return templates.TemplateResponse(request,
        "items.html",
        {
            "request": request,
            "groups": groups,
            "inactive": inactive,
            "known_groups": list(index.keys()) or [items.DEFAULT_GROUP],
            "flash": flash,
            "nav_active": "items",
            "rail": "items",
        },
    )


@app.post("/items")
def post_item_create(request: Request, title: str = Form(...), group_name: str = Form("")):
    conn = get_conn()
    try:
        items.create_item(conn, title, group_name)
    except items.ItemError as exc:
        return _items_redirect(str(exc))
    finally:
        conn.close()
    return _items_redirect()


@app.post("/items/{item_id}/edit")
def post_item_edit(request: Request, item_id: int, title: str = Form(...), group_name: str = Form("")):
    conn = get_conn()
    try:
        items.update_item(conn, item_id, title, group_name)
    except items.ItemError as exc:
        return _items_redirect(str(exc))
    finally:
        conn.close()
    return _items_redirect()


@app.post("/items/{item_id}/deactivate")
def post_item_deactivate(request: Request, item_id: int):
    conn = get_conn()
    try:
        items.deactivate_item(conn, item_id)
    except items.ItemError as exc:
        return _items_redirect(str(exc))
    finally:
        conn.close()
    return _items_redirect()


@app.post("/items/{item_id}/reactivate")
def post_item_reactivate(request: Request, item_id: int):
    conn = get_conn()
    try:
        items.reactivate_item(conn, item_id)
    except items.ItemError as exc:
        return _items_redirect(str(exc))
    finally:
        conn.close()
    return _items_redirect()
