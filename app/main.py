"""Ephemeris FastAPI app — lifespan and assembly.

Implements system-design.md sec15 (routes), sec16.4 (status & note write
contract — Mode A no-JS forms + Mode B fetch), and sec20 (security: same-origin
guard, Jinja autoescape only, no-auth loopback warning).

Every route now lives in app/routers/ (#24, cuts 1-5). What is left here is the
assembly: the startup/shutdown lifespan, the app object, the request perimeter,
the static mount, and the `include_router` calls in the exact order the handlers
used to be defined in — that order is the routing contract, so each router is
mounted at the position its own block occupied, never regrouped by feature.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .db import SEEDED_AT, get_conn, init_db, meta_get, meta_set, now_iso
from .routers.calendar import router as calendar_router
from .routers.export import router as export_router
from .routers.focus import router as focus_router
from .routers.habits import (
    detail_router as habit_detail_router, items_router, router as habits_router,
    write_router as habit_write_router,
)
from .routers.learn import router as learn_router
from .routers.diary import router as diary_router
from .routers.retro import router as retro_router
from .routers.site import favicon_router, mirror_router, palette_router
from .routers.tasks import (
    history_router as tasks_history_router, views_router as tasks_views_router,
    write_router as tasks_write_router,
)
from .security import install_security
from .services import checkins, lists, runs, tasks
from .templating import BASE_DIR, templates
from .terminal import client_is_local, setup_terminal, shutdown_terminal

log = logging.getLogger("activity_ledger")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup: migrate + seed once. (Replaces the deprecated on_event hook.)"""
    init_db()
    conn = get_conn()
    try:
        # The `app_meta` marker (schema v16), not the per-table row counts, is
        # what decides this. A restored database can legitimately hold empty
        # lists and tasks; seeding into it would mix demo rows into real
        # history and append their events to the audit stream. Whether the
        # seeders created anything is irrelevant — the marker records that
        # they had their turn, so an installation is initialized exactly once.
        created = 0
        if meta_get(conn, SEEDED_AT) is None:
            created = checkins.seed_if_empty(conn)
            lists.seed_if_empty(conn)      # Inbox + sample lists (before tasks)
            tasks.seed_if_empty(conn)      # sample tasks reference seeded lists
            with conn:
                meta_set(conn, SEEDED_AT, now_iso())
        # Every boot, seeded or not: the Inbox is structure, not demo data. A
        # restored ledger can hold real history and no lists at all, and two
        # read routes call lists.inbox_id() unconditionally — without this an
        # initialized-but-listless database opens and then raises on its home
        # page. Demo seeding above owns the fresh-install case, so on a first
        # run this finds the Inbox already there.
        lists.ensure_inbox(conn)
    finally:
        conn.close()
    if created:
        log.info("Seeded %d routine items", created)
    log.warning(
        "Ephemeris has NO AUTH (sec20): keep it bound to 127.0.0.1; "
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


# --- routes ----------------------------------------------------------------
# Every router is mounted with no prefix, at the position its own block of
# handlers occupied while they all lived here. The same-origin write guard is
# not a per-route call: app/security.py enforces it in middleware for every
# unsafe-method request (sec20, sec13.3), so a new POST route is covered
# without remembering anything.

app.include_router(favicon_router)        # app/routers/site.py     (#24 cut 5)
app.include_router(tasks_views_router)    # app/routers/tasks.py    (#24 cut 3)
app.include_router(palette_router)        # app/routers/site.py     (#24 cut 5)
app.include_router(calendar_router)       # app/routers/calendar.py (#24 cut 2)
app.include_router(learn_router)          # app/routers/learn.py    (#24 cut 1)
app.include_router(focus_router)          # app/routers/focus.py    (#24 cut 4)
app.include_router(export_router)         # app/routers/export.py   (#24 cut 4)
app.include_router(habits_router)         # app/routers/habits.py   (#24 cut 2)
app.include_router(tasks_history_router)  # app/routers/tasks.py    (#24 cut 3)
app.include_router(habit_detail_router)   # app/routers/habits.py   (#24 cut 2)
app.include_router(tasks_write_router)    # app/routers/tasks.py    (#24 cut 3)
app.include_router(habit_write_router)    # app/routers/habits.py   (#24 cut 2)
app.include_router(items_router)          # app/routers/habits.py   (#24 cut 2)
app.include_router(retro_router)          # app/routers/retro.py    (#24 cut 4)
app.include_router(diary_router)          # app/routers/diary.py    (#2)
app.include_router(mirror_router)         # app/routers/site.py     (#128)
