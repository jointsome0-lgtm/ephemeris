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

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .db import get_conn, init_db
from .routers.calendar import router as calendar_router
from .routers.habits import (
    detail_router as habit_detail_router, items_router, router as habits_router,
    write_router as habit_write_router,
)
from .routers.learn import router as learn_router, _learn_url
from .routers.tasks import (
    history_router as tasks_history_router, views_router as tasks_views_router,
    write_router as tasks_write_router,
)
from .security import install_security
from .services import checkins, export, focus, items, lessons, lists, retro, runs, tasks
from .templating import BASE_DIR, _safe_return, _wants_json, _with_flash, templates
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


# --- routes ----------------------------------------------------------------


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


# --- Tasks + day views (app/routers/tasks.py, #24 cut 3) -------------------
# Mounted with no prefix, at the position those routes used to occupy.
app.include_router(tasks_views_router)


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


# --- GET /history (app/routers/tasks.py, #24 cut 3) ------------------------
# It renders through _render_day, so it moved with the day view; mounted at
# its original position, between the Habit tab and the habit detail routes.
app.include_router(tasks_history_router)


# --- Habit detail + check-in writes (app/routers/habits.py, #24 cut 2) -----
# Mounted with no prefix, at the position those routes used to occupy.
app.include_router(habit_detail_router)


# --- Tasks write contract (app/routers/tasks.py, #24 cut 3) ----------------
# Mounted with no prefix, at the position those routes used to occupy.
app.include_router(tasks_write_router)


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
