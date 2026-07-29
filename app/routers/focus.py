"""Focus routes (#24 cut 4): the Focus tab and the session write.

A pure move out of app/main.py. The block was contiguous there, so it is one
router included with no prefix at the position it occupied. URLs and
registration order are unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..db import get_conn
from ..services import focus, lessons
from ..templating import _safe_return, _wants_json, _with_flash, templates

router = APIRouter()  # GET /focus, POST /focus/session


@router.get("/focus")
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


@router.post("/focus/session")
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
