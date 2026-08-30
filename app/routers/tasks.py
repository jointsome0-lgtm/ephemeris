"""Tasks routes (#24 cut 3): the tasks surface (the Board, Today, Next 7 Days, a
single list, Completed, Trash, Search), the day view behind GET /history, and
the task write contract (sec21).

A pure move out of app/main.py. The surface was never contiguous there — the
day views, GET /history and the write routes sit at three different points of
the file, separated by other surfaces' routes and their include seams — so this
is three routers, each included with no prefix at the position its own block
occupied. URLs and registration order are unchanged.
"""
from __future__ import annotations

import sqlite3
from datetime import date as _date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..db import get_db, today_str
from ..services import checkins, lessons, lists, quickadd, stats, tasks
from ..templating import (
    _render_day, _render_tasks, _safe_return, _validated_write_date, _wants_json,
    _with_flash, due_label, templates,
)

views_router = APIRouter()    # GET /, /today, /board, /next7, /list/{list_id}, /completed, /trash, /search
history_router = APIRouter()  # GET /history
write_router = APIRouter()    # POST /lists, /tasks, /tasks/{task_id}/complete, /status, /update


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
                "current_streak": stats.current_streak_from(
                    smap, today_d, stats.as_start(it["start_date"])
                ),
            })
    return rows


@views_router.get("/")
@views_router.get("/today")
def get_today(request: Request, sel: str | None = None, month: str | None = None,
              flash: str | None = None,
              conn: sqlite3.Connection = Depends(get_db)):
    """Today as a task list (sec21): Habit / Tasks / Completed."""
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


@views_router.get("/board")
def get_board(request: Request, sel: str | None = None, month: str | None = None,
              flash: str | None = None,
              conn: sqlite3.Connection = Depends(get_db)):
    """The kanban board (#53) — the primary Tasks view: Backlog / Doing / Done.

    The older views (/today, /next7, /list/{id}, /completed) stay exactly as
    they were; the calendar and the day view still link into them. This one just
    became the destination the rail points at."""
    by_status = tasks.board(conn)
    columns = [
        # Done is the only capped column, and the cap travels to the page so the
        # drag handler evicts the overflow instead of showing one card more than
        # the server would have rendered.
        {"key": key, "title": title, "rows": by_status[key],
         "limit": tasks.DONE_LIMIT if key == "done" else 0}
        for key, title in tasks.BOARD_COLUMNS
    ]
    return _render_tasks(
        request, conn, page_title="Board", active="board", sections=[],
        show_add=True, add_list_id=lists.inbox_id(conn), add_list_name="Inbox",
        add_due=None, sel=sel, month=month, flash=flash,
        template="board.html", extra={"columns": columns},
    )


@views_router.get("/next7")
def get_next7(request: Request, sel: str | None = None, month: str | None = None,
              flash: str | None = None,
              conn: sqlite3.Connection = Depends(get_db)):
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


@views_router.get("/list/{list_id}")
def get_list_view(request: Request, list_id: int, sel: str | None = None,
                  month: str | None = None, flash: str | None = None,
                  conn: sqlite3.Connection = Depends(get_db)):
    lst = lists.get_list(conn, list_id)
    if lst is None or lst["archived_at"] is not None:
        raise HTTPException(status_code=404, detail="unknown list")
    every = tasks.list_tasks(conn, list_id)
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


@views_router.get("/completed")
def get_completed(request: Request, sel: str | None = None, month: str | None = None,
                  flash: str | None = None,
                  conn: sqlite3.Connection = Depends(get_db)):
    sections = [{"title": "Completed", "kind": "task", "rows": tasks.recent_completed(conn, 200)}]
    return _render_tasks(
        request, conn, page_title="Completed", active="completed", sections=sections,
        show_add=False, sel=sel, month=month, flash=flash,
    )


@views_router.get("/trash")
def get_trash(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Trash placeholder — tasks are reversible toggles, so nothing is hard-deleted."""
    return _render_tasks(
        request, conn, page_title="Trash", active="trash", sections=[], show_add=False,
    )


@views_router.get("/search")
def get_search(request: Request, q: str = "",
               conn: sqlite3.Connection = Depends(get_db)):
    """Substring search over task titles + notes, plus Learn lessons."""
    q = (q or "").strip()
    results = tasks.search(conn, q) if q else []
    lesson_hits = lessons.search(conn, q) if q else []
    return templates.TemplateResponse(request,
        "search.html",
        {
            "request": request, "rail": "search", "q": q,
            "results": results, "lessons": lesson_hits,
            "today": today_str(), "cur_path": "/search",
        },
    )


@history_router.get("/history")
def get_history(request: Request, date: str | None = None, flash: str | None = None,
                conn: sqlite3.Connection = Depends(get_db)):
    date = date or today_str()
    date = _validated_write_date(date)  # valid + not future
    return _render_day(request, conn, date, flash, rail="habit")


# --- Tasks write contract (sec21) ------------------------------------------


@write_router.post("/lists")
def post_list_create(name: str = Form(...), emoji: str = Form(""),
                     conn: sqlite3.Connection = Depends(get_db)):
    """Create a user list from the sidebar's + modal, then open it."""
    try:
        list_id = lists.create_list(conn, name, emoji=emoji)
    except lists.ListError as exc:
        return RedirectResponse(_with_flash("/today", str(exc)), status_code=303)
    return RedirectResponse(f"/list/{list_id}", status_code=303)


@write_router.post("/tasks")
def post_task_create(
    request: Request,
    title: str = Form(...),
    list_id: int | None = Form(None),
    due_date: str | None = Form(None),
    smart: str = Form(""),
    return_to: str = Form("/today"),
    conn: sqlite3.Connection = Depends(get_db),
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
    try:
        tasks.create_task(conn, title, list_id=list_id, due_date=(due_date or None),
                          priority=priority)
    except tasks.TaskError as exc:
        if _wants_json(request):
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    if _wants_json(request):
        return JSONResponse({"ok": True, "label": parsed_label})
    dest = _safe_return(return_to)
    if parsed_label:
        dest = _with_flash(dest, f"Added · {parsed_label}")
    return RedirectResponse(dest, status_code=303)


@write_router.post("/tasks/{task_id}/complete")
def post_task_complete(request: Request, task_id: int, return_to: str = Form("/today"),
                       conn: sqlite3.Connection = Depends(get_db)):
    json_mode = _wants_json(request)
    try:
        now_done = tasks.toggle_complete(conn, task_id)
        if json_mode:
            return JSONResponse({"ok": True, "task_id": task_id, "completed": now_done})
    except tasks.TaskError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    return RedirectResponse(_safe_return(return_to), status_code=303)


@write_router.post("/tasks/{task_id}/status")
def post_task_status(request: Request, task_id: int, status: str = Form(...),
                     return_to: str = Form("/board"),
                     conn: sqlite3.Connection = Depends(get_db)):
    """Move a task to another board column (#53).

    Both board paths land here: the per-card arrow forms (Mode A, works with
    JavaScript off) and the drag handler's `X-Partial` fetch (Mode B). The
    status is validated in the service, so an edited form value is a 422 / flash
    rather than a stored column nobody can render."""
    json_mode = _wants_json(request)
    try:
        moved = tasks.set_status(conn, task_id, status)
    except tasks.TaskError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return RedirectResponse(_with_flash(_safe_return(return_to, "/board"), str(exc)),
                                status_code=303)
    if json_mode:
        return JSONResponse({"ok": True, "task_id": task_id, **moved})
    return RedirectResponse(_safe_return(return_to, "/board"), status_code=303)


@write_router.post("/tasks/{task_id}/update")
def post_task_update(
    task_id: int,
    title: str = Form(...),
    note: str = Form(""),
    due_date: str = Form(""),
    priority: int = Form(0),
    list_id: int = Form(...),
    return_to: str = Form("/today"),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        tasks.update_task(
            conn, task_id, title=title, note=note,
            due_date=(due_date or None), priority=priority, list_id=list_id,
        )
    except tasks.TaskError as exc:
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    return RedirectResponse(_safe_return(return_to), status_code=303)
