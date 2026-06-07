"""Activity Ledger FastAPI app — daily execution surface + write contract.

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
from datetime import date as _date, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .db import get_conn, init_db, is_not_future, is_valid_date, today_str
from .services import checkins, export, focus, items, lists, stats, tasks

log = logging.getLogger("activity_ledger")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Activity Ledger")
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
    avatar=item_avatar,
    status_glyph=status_glyph,
    status_desc=status_desc,
    status_meta=STATUS_META,
    due_label=due_label,
    countdown_label=countdown_label,
)


@app.on_event("startup")
def _startup() -> None:
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
        "Activity Ledger has NO AUTH (sec20): serve only on a trusted LAN; "
        "never expose to the public internet."
    )


# --- security / validation (sec20, sec13.3) --------------------------------


def _check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        return
    if urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(status_code=403, detail="cross-origin POST rejected")


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


def _week_strip(conn, active: str) -> list[dict]:
    """Sunday-start week containing `active`, with a per-day logged count."""
    d = _date.fromisoformat(active)
    today = _date.fromisoformat(today_str())
    start = d - timedelta(days=(d.weekday() + 1) % 7)  # back up to Sunday
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
    return templates.TemplateResponse(
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
                  flash: str | None = None, rail: str = "tasks"):
    """Render tasks.html: list-sidebar + sections + (optional) detail pane."""
    ctx = {
        "request": request,
        "rail": rail,
        "active": active,
        "page_title": page_title,
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
    return templates.TemplateResponse("tasks.html", ctx)


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
    """Substring search over task titles + notes."""
    q = (q or "").strip()
    conn = get_conn()
    try:
        results = tasks.search(conn, q) if q else []
    finally:
        conn.close()
    return templates.TemplateResponse(
        "search.html",
        {
            "request": request, "rail": "search", "q": q,
            "results": results, "today": today_str(), "cur_path": "/search",
        },
    )


# --- Calendar (month grid) + Eisenhower Matrix (sec: premium views) ---------


def _month_grid(conn, year: int, month: int) -> list[list[dict]]:
    """Always six Sunday-start weeks for the month (TickTick fixes the grid at 6
    rows so its height never jumps), each cell carrying its day's task events."""
    import calendar as _cal
    today_d = _date.fromisoformat(today_str())
    grid = _cal.Calendar(firstweekday=6)  # 6 = Sunday
    first_cell = grid.monthdatescalendar(year, month)[0][0]
    days = [first_cell + timedelta(days=i) for i in range(42)]  # 6 weeks, fixed
    by_date: dict[str, list] = {}
    for t in tasks.due_between(conn, days[0].isoformat(), days[-1].isoformat()):
        by_date.setdefault(t["due_date"], []).append(t)
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
                "events": [
                    {
                        "title": e["title"],
                        "kind": e["kind"],
                        "completed": e["completed_at"] is not None,
                        "priority": e["priority"],
                    }
                    for e in by_date.get(iso, [])
                ],
            })
        weeks.append(cells)
    return weeks


@app.get("/calendar")
def get_calendar(request: Request, month: str | None = None):
    year, mon = _parse_month(month)
    conn = get_conn()
    try:
        weeks = _month_grid(conn, year, mon)
    finally:
        conn.close()
    first = _date(year, mon, 1)
    prev_first = (first - timedelta(days=1)).replace(day=1)
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return templates.TemplateResponse(
        "calendar.html",
        {
            "request": request, "rail": "calendar",
            "month_label": first.strftime("%B %Y"),
            "weeks": weeks,
            "prev_url": f"/calendar?month={prev_first.strftime('%Y-%m')}",
            "next_url": f"/calendar?month={next_first.strftime('%Y-%m')}",
        },
    )


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
    quadrants = [{"title": title, "rows": buckets[p]} for p, title in _MATRIX_QUADRANTS]
    return templates.TemplateResponse(
        "matrix.html", {"request": request, "rail": "matrix", "quadrants": quadrants}
    )


@app.get("/focus")
def get_focus(request: Request):
    conn = get_conn()
    try:
        ov = focus.overview(conn)
        records = focus.recent_sessions(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(
        "focus.html",
        {"request": request, "rail": "focus", "ov": ov, "records": records},
    )


@app.post("/focus/session")
def post_focus_session(
    request: Request,
    mode: str = Form("pomo"),
    seconds: int = Form(...),
    note: str = Form(""),
    return_to: str = Form("/focus"),
):
    """Record a finished Pomodoro / stopwatch span. Mode B returns refreshed stats."""
    _check_origin(request)
    json_mode = _wants_json(request)
    conn = get_conn()
    try:
        sid = focus.record_session(conn, mode, seconds, note=note)
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


# --- Export (sec15.4 / sec18.1): one-button JSONL backup of the event ledger -


@app.get("/export")
def get_export(request: Request):
    """One-button export page: shows the event count + recent export files."""
    conn = get_conn()
    try:
        count = export.event_count(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(
        "export.html",
        {"request": request, "rail": "export",
         "event_count": count, "recent": export.recent_exports()},
    )


@app.post("/export/jsonl")
def post_export_jsonl(request: Request):
    """Write data/exports/events-<stamp>.jsonl AND stream it back as a download."""
    _check_origin(request)
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
        return templates.TemplateResponse("habits.html", ctx)
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
    return templates.TemplateResponse("habit.html", ctx)


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
    _check_origin(request)
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
    _check_origin(request)
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
    _check_origin(request)
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
    return_to: str = Form("/today"),
):
    _check_origin(request)
    conn = get_conn()
    try:
        tasks.create_task(conn, title, list_id=list_id, due_date=(due_date or None), kind=kind)
    except tasks.TaskError as exc:
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    finally:
        conn.close()
    return RedirectResponse(_safe_return(return_to), status_code=303)


@app.post("/tasks/{task_id}/complete")
def post_task_complete(request: Request, task_id: int, return_to: str = Form("/today")):
    _check_origin(request)
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
    _check_origin(request)
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
    _check_origin(request)
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
    _check_origin(request)
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
    _check_origin(request)
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
    _check_origin(request)
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
    return templates.TemplateResponse(
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
    _check_origin(request)
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
    _check_origin(request)
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
    _check_origin(request)
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
    _check_origin(request)
    conn = get_conn()
    try:
        items.reactivate_item(conn, item_id)
    except items.ItemError as exc:
        return _items_redirect(str(exc))
    finally:
        conn.close()
    return _items_redirect()
