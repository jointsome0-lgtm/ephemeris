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

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .db import get_conn, init_db
from .routers.calendar import router as calendar_router
from .routers.export import router as export_router
from .routers.focus import router as focus_router
from .routers.habits import (
    detail_router as habit_detail_router, items_router, router as habits_router,
    write_router as habit_write_router,
)
from .routers.learn import router as learn_router, _learn_url
from .routers.retro import router as retro_router
from .routers.tasks import (
    history_router as tasks_history_router, views_router as tasks_views_router,
    write_router as tasks_write_router,
)
from .security import install_security
from .services import checkins, items, lessons, lists, runs, tasks
from .templating import BASE_DIR, templates
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


# --- Focus (app/routers/focus.py, #24 cut 4) -------------------------------
# Mounted with no prefix, at the position those routes used to occupy.
app.include_router(focus_router)


# --- Export (app/routers/export.py, #24 cut 4) -----------------------------
# Mounted with no prefix, at the position those routes used to occupy.
app.include_router(export_router)


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


# --- Retro (app/routers/retro.py, #24 cut 4) -------------------------------
# Mounted with no prefix, at the position those routes used to occupy.
app.include_router(retro_router)
