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
from datetime import date as _date

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .db import get_conn, init_db, today_str
from .routers.calendar import router as calendar_router
from .routers.habits import (
    detail_router as habit_detail_router, items_router, router as habits_router,
    write_router as habit_write_router,
)
from .routers.learn import router as learn_router, _learn_url
from .security import install_security
from .services import (
    checkins, export, focus, items, lessons, lists, quickadd,
    retro, runs, stats, tasks,
)
from .templating import (
    BASE_DIR, _render_day, _render_tasks, _safe_return, _validated_write_date,
    _wants_json, _with_flash, due_label, templates,
)
from .terminal import client_is_local, setup_terminal, shutdown_terminal

log = logging.getLogger("activity_ledger")


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
    app.state.runner_service = runs.create_service()
    try:
        yield
    finally:
        await app.state.runner_service.shutdown()
        await shutdown_terminal()  # kill persistent terminal shells on shutdown


app = FastAPI(title="Ephemeris", lifespan=_lifespan)
# Request perimeter (issue #15): trusted-host allowlist + central write guard
# for ALL unsafe methods + global security headers — see app/security.py.
install_security(app)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# The display globals live in app/templating.py (#24 cut 3), which owns the
# Jinja environment. `client_is_local` stays here: it comes from app.terminal,
# and templating.py must not import that surface.
templates.env.globals.update(
    client_is_local=client_is_local,  # gates the terminal drawer in base.html
)

# Desktop / localhost-only terminal tab (app/terminal.py): PTY ↔ xterm.js over WS.
setup_terminal(app)


# --- security / validation (sec20, sec13.3) --------------------------------
# The same-origin write guard is no longer a per-route call: app/security.py
# enforces it in middleware for every unsafe-method request, so a new POST
# route is covered without remembering anything.


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
    """Today as a task list (sec21): Habit / Tasks / Completed."""
    conn = get_conn()
    try:
        today = today_str()
        sections = [
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


# --- command palette (Ctrl/⌘K) index ----------------------------------------
_PALETTE_VIEWS = [
    {"label": "Tasks", "href": "/today", "icon": "tasks"},
    {"label": "Calendar", "href": "/calendar", "icon": "calendar"},
    {"label": "Focus", "href": "/focus", "icon": "focus"},
    {"label": "Habits", "href": "/habits", "icon": "habit"},
    {"label": "Learn", "href": "/learn", "icon": "learn"},
    {"label": "Retro", "href": "/retro", "icon": "retro"},
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


# --- Calendar (app/routers/calendar.py, #24 cut 2) -------------------------
# Mounted with no prefix, at the position those routes used to occupy.
app.include_router(calendar_router)


# --- Learn (app/routers/learn.py, #24 cut 1) --------------------------------
# Mounted with no prefix, at the position those routes used to occupy: the URLs
# and the registration order are exactly what defining them here produced.
app.include_router(learn_router)


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


# --- Habit tab (app/routers/habits.py, #24 cut 2) --------------------------
# Mounted with no prefix, at the position those routes used to occupy.
app.include_router(habits_router)


@app.get("/history")
def get_history(request: Request, date: str | None = None, flash: str | None = None):
    date = date or today_str()
    date = _validated_write_date(date)  # valid + not future
    nav = "today" if date == today_str() else "history"
    return _render_day(request, date, nav, flash, rail="habit")


# --- Habit detail + check-in writes (app/routers/habits.py, #24 cut 2) -----
# Mounted with no prefix, at the position those routes used to occupy.
app.include_router(habit_detail_router)


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


# --- Habit tab writes (app/routers/habits.py, #24 cut 2) -------------------
# Mounted with no prefix, at the position those routes used to occupy.
app.include_router(habit_write_router)


# --- Manage Items (app/routers/habits.py, #24 cut 2) -----------------------
# Mounted with no prefix, at the position those routes used to occupy.
app.include_router(items_router)


# --- Retro (docs/retro-spec.md, issue #49) ---------------------------------
# Owner-typed retrospectives over approximate periods; every write journals a
# full-snapshot event, which is what the future selfos→exp2res adapter consumes
# from the JSONL export. Write contract follows sec16.4 (Mode A form + Mode B
# fetch), same as /daily-note.


def _retro_redirect(archived: bool = False, flash: str | None = None) -> RedirectResponse:
    url = "/retro" + ("?archived=1" if archived else "")
    return RedirectResponse(_with_flash(url, flash) if flash else url, status_code=303)


@app.get("/retro")
def get_retro(request: Request, archived: int = 0, edit: int | None = None,
              flash: str | None = None):
    show_archived = bool(archived)
    conn = get_conn()
    try:
        rows = retro.list_entries(conn, include_archived=show_archived)
        editing = retro.get_entry(conn, edit) if edit is not None else None
    finally:
        conn.close()
    if show_archived:
        rows = [r for r in rows if r["archived_at"] is not None]
    return templates.TemplateResponse(request, "retro.html", {
        "request": request,
        "rail": "retro",
        "rows": rows,
        "show_archived": show_archived,
        "editing": editing,
        "precisions": retro.PRECISIONS,
        "confidences": retro.CONFIDENCES,
        "flash": flash,
    })


@app.post("/retro")
def post_retro_create(
    request: Request,
    period: str = Form(""),
    precision: str = Form("month"),
    confidence: str = Form("medium"),
    project: str = Form(""),
    text: str = Form(""),
):
    json_mode = _wants_json(request)
    conn = get_conn()
    try:
        row = retro.create_entry(conn, period=period, precision=precision,
                                 confidence=confidence, project=project, text=text)
    except retro.RetroError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _retro_redirect(flash=str(exc))
    finally:
        conn.close()
    if json_mode:
        return JSONResponse({"ok": True, "id": row["id"], "uuid": row["uuid"]})
    return _retro_redirect()


@app.post("/retro/{entry_id}/edit")
def post_retro_edit(
    request: Request,
    entry_id: int,
    period: str = Form(""),
    precision: str = Form("month"),
    confidence: str = Form("medium"),
    project: str = Form(""),
    text: str = Form(""),
):
    json_mode = _wants_json(request)
    conn = get_conn()
    try:
        row = retro.update_entry(conn, entry_id, period=period, precision=precision,
                                 confidence=confidence, project=project, text=text)
    except retro.RetroError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _retro_redirect(flash=str(exc))
    finally:
        conn.close()
    if json_mode:
        return JSONResponse({"ok": True, "id": row["id"], "uuid": row["uuid"]})
    return _retro_redirect()


@app.post("/retro/{entry_id}/archive")
def post_retro_archive(request: Request, entry_id: int):
    json_mode = _wants_json(request)
    conn = get_conn()
    try:
        retro.archive_entry(conn, entry_id)
    except retro.RetroError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _retro_redirect(flash=str(exc))
    finally:
        conn.close()
    if json_mode:
        return JSONResponse({"ok": True})
    return _retro_redirect()


@app.post("/retro/{entry_id}/unarchive")
def post_retro_unarchive(request: Request, entry_id: int):
    json_mode = _wants_json(request)
    conn = get_conn()
    try:
        retro.unarchive_entry(conn, entry_id)
    except retro.RetroError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _retro_redirect(archived=True, flash=str(exc))
    finally:
        conn.close()
    if json_mode:
        return JSONResponse({"ok": True})
    return _retro_redirect(archived=True)
