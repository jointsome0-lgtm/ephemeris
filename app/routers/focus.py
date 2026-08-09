"""Focus timer routes (#75): the drawer's state read and its four writes.

There is no Focus page any more. The timer lives in a drawer present on every
surface, so these routes serve JSON to `focus-timer.js` instead of rendering a
destination, and the running timer is read back from the server after each page
load — that read is the whole reason the state survives navigation (#20).

They sit under `/focus/timer/*`, not `/focus/sessions/*` as #20 sketched: a
running timer is not a session. It becomes one — a row in `focus_sessions` —
only when it is finished, which is what `finish` returns.

No Mode A (no-JS form) twin, unlike the rest of the write contract (sec16.4): a
timer without JS cannot tick, so there is no no-JS surface here to keep working.
The global same-origin write guard (app/security.py) covers these POSTs like
every other.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse

from ..db import get_db
from ..services import focus

router = APIRouter()  # GET /focus/timer[/targets], POST /focus/timer/*


def _state(conn: sqlite3.Connection, **extra) -> JSONResponse:
    """One shape for every answer: whatever is running, plus today's totals and
    the recent spans, so the drawer never needs a second call to redraw."""
    return JSONResponse({
        "ok": True,
        "run": focus.active_run(conn),
        "overview": focus.overview(conn),
        "recent": focus.recent_sessions(conn, limit=8),
        **extra,
    })


def _rejected(exc: focus.FocusError) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)


@router.post("/focus/session")
def post_focus_session_legacy(
    request: Request,
    mode: str = Form("pomo"),
    seconds: int = Form(...),
    note: str = Form(""),
    lesson_id: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    """The pre-#75 write, kept for the restart window only.

    The mirror of `focus.html`: that template protects a NEW checkout served by
    an OLD process, this protects an OLD page still open in a browser after the
    restart. Its `app.js` posts here when a Pomodoro completes, and a 404 would
    silently drop a span the user really did spend. The old vocabulary is
    translated once, here, rather than being kept alive in the schema.

    Deletable together with `app/templates/focus.html`, once no pre-#75 page can
    still be open — see docs/system-design.md sec34.
    """
    try:
        sid = focus.record_session(
            conn, "countdown" if mode == "pomo" else "open", seconds,
            target_seconds=1500 if mode == "pomo" else None,
            note=note, lesson_id=lesson_id,
        )
    except focus.FocusError as exc:
        return _rejected(exc)
    # The answer is in the old page's vocabulary too, not just the old URL: it
    # reads `today_pomo` / `total_pomo` off the overview and `lesson_title` off
    # the record. Handing it the new shapes would blank its counters and mislabel
    # the row it appends — a compatibility route that only half-answers is worse
    # than none, because the write succeeded and the page says otherwise.
    ov, rec = focus.overview(conn), focus.get_session_view(conn, sid)
    pomo = focus.pomodoro_counts(conn)
    return JSONResponse({
        "ok": True,
        "overview": {**ov, **pomo},
        "record": {
            **rec,
            "mode": mode,
            "mode_label": "Pomo" if mode == "pomo" else "Stopwatch",
            "lesson_id": rec["target"]["id"] if rec["target"] else None,
            "lesson_title": rec["target"]["title"] if rec["target"] else None,
        },
    })


@router.get("/focus/timer")
def get_focus_timer(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """The drawer's bootstrap: what is running right now, if anything."""
    return _state(conn)


@router.get("/focus/timer/targets")
def get_focus_targets(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """The target picker's options — fetched when the drawer opens, so no other
    page pays for three lists it will probably never show."""
    return JSONResponse({"ok": True, "targets": focus.pickable_targets(conn)})


@router.post("/focus/timer/start")
def post_focus_start(
    request: Request,
    token: str = Form(...),
    mode: str = Form("countdown"),
    target_seconds: str = Form(""),
    note: str = Form(""),
    lesson_id: str = Form(""),
    habit_id: str = Form(""),
    task_id: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        focus.start_run(conn, mode, token, target_seconds=target_seconds or None,
                        note=note, lesson_id=lesson_id, habit_id=habit_id,
                        task_id=task_id)
    except focus.FocusError as exc:
        return _rejected(exc)
    return _state(conn)


@router.post("/focus/timer/pause")
def post_focus_pause(
    request: Request,
    token: str = Form(...),
    paused: int = Form(1),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        focus.set_run_paused(conn, token, bool(paused))
    except focus.FocusError as exc:
        return _rejected(exc)
    return _state(conn)


@router.post("/focus/timer/finish")
def post_focus_finish(
    request: Request,
    token: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Record the span. Retrying with the same token returns the session the
    first call recorded instead of counting the time twice."""
    try:
        recorded = focus.finish_run(conn, token)
    except focus.FocusError as exc:
        return _rejected(exc)
    return _state(conn, recorded=recorded)


@router.post("/focus/timer/discard")
def post_focus_discard(
    request: Request,
    token: str = Form(...),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Drop a timer without recording it — a mis-start is not focused time."""
    try:
        focus.discard_run(conn, token)
    except focus.FocusError as exc:
        return _rejected(exc)
    return _state(conn)
