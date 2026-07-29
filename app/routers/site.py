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

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from ..db import get_conn
from ..services import items, lessons, lists
from .learn import _learn_url

favicon_router = APIRouter()  # GET /favicon.ico
palette_router = APIRouter()  # GET /palette.json


@favicon_router.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


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


@palette_router.get("/palette.json")
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
