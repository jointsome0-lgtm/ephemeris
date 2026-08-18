"""Site-wide odds and ends (#24 cut 5): the favicon stub and the command-palette
index — the two routes that belonged to no feature tab and were the last
handlers left in app/main.py.

A pure move out of app/main.py. The two were never contiguous there — the Tasks
day views sit between them — so the surface is two routers, each included at the
position its own route occupied. URLs and registration order are unchanged.

`_learn_url` comes from app.routers.learn: the palette links a lesson the same
way the Learn tab does, and the dependency runs one way (site -> learn), so
there is no cycle.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .. import settings
from ..db import get_db
from ..security import embed_frame_csp
from ..services import items, lessons, lists
from ..templating import TASKS_HOME, mirror_home, templates
from .learn import _learn_url

favicon_router = APIRouter()  # GET /favicon.ico
palette_router = APIRouter()  # GET /palette.json
mirror_router = APIRouter()   # GET /mirror


@favicon_router.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


# --- exp2res Mirror embed (#128) --------------------------------------------
# The Diary questions strip a second time, as its own surface: one configured
# peer URL in a fully sandboxed iframe, nothing built and nothing parsed here
# (Integration v1, selfos#25). Config-gated end to end: without the URL the
# route 404s and the nav never shows it.


@mirror_router.get("/mirror")
def get_mirror(request: Request):
    url = settings.settings.exp2res_mirror_url
    if url is None:
        raise HTTPException(status_code=404)
    response = templates.TemplateResponse(request, "mirror.html", {
        "request": request,
        "rail": "mirror",
        "mirror_url": url,
    })
    response.headers["Content-Security-Policy"] = embed_frame_csp(url)
    return response


# --- command palette (Ctrl/⌘K) index ----------------------------------------
_PALETTE_VIEWS = [
    # the same destination the rail's Tasks icon has (#53), from its one owner
    {"label": "Tasks", "href": TASKS_HOME, "icon": "tasks"},
    {"label": "Calendar", "href": "/calendar", "icon": "calendar"},
    {"label": "Habits", "href": "/habits", "icon": "habit"},
    {"label": "Learn", "href": "/learn", "icon": "learn"},
    {"label": "Retro", "href": "/retro", "icon": "retro"},
    {"label": "Diary", "href": "/diary", "icon": "diary"},
    {"label": "Search", "href": "/search", "icon": "search"},
    {"label": "Export", "href": "/export", "icon": "download"},
]
_PALETTE_ACTIONS = [
    {"label": "New task", "hint": "n", "shortcut": "n"},
    # Focus left the views list with its page (#75): the timer is a drawer, so
    # it is something to do from here, not somewhere to go.
    {"label": "Focus timer", "hint": "g f", "shortcut": "f"},
    {"label": "Toggle theme", "hint": "t", "shortcut": "t"},
    {"label": "Keyboard shortcuts", "hint": "?", "shortcut": "?"},
]


def _palette_views() -> list[dict]:
    """The rail's destinations in rail order. Mirror rides the same render-time
    config gate as its rail icon (#128): listed only while a URL is set."""
    views = []
    for view in _PALETTE_VIEWS:
        views.append(view)
        if view["label"] == "Diary" and mirror_home():
            views.append({"label": "Mirror", "href": mirror_home(), "icon": "mirror"})
    return views


@palette_router.get("/palette.json")
def get_palette(conn: sqlite3.Connection = Depends(get_db)):
    """Index the command palette pulls at open: views, lists, habits, lessons, actions."""
    list_rows = lists.list_lists(conn)
    habit_rows = [r for r in items.list_items(conn) if r["active"]]
    try:
        lesson_rows = lessons.list_lessons(conn)
    except lessons.LessonError:
        lesson_rows = []
    return JSONResponse({
        "views": _palette_views(),
        "lists": [{"label": r["name"], "href": f"/list/{r['id']}",
                   "emoji": r["emoji"], "count": r["open_count"]} for r in list_rows],
        "habits": [{"label": r["title"], "href": f"/habit/{r['id']}",
                    "emoji": r["emoji"]} for r in habit_rows],
        "lessons": [{"label": r["title"], "href": _learn_url(lesson_id=r["id"])}
                    for r in lesson_rows],
        "actions": _PALETTE_ACTIONS,
    })
