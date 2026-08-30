"""Retro routes (#24 cut 4): owner-typed retrospectives over approximate
periods (docs/retro-spec.md, issue #49).

A pure move out of app/main.py, where the block was the file tail: one router
included with no prefix at the position it occupied. URLs and registration
order are unchanged. Every write journals a full-snapshot event, which is what
the future selfos→exp2res adapter consumes from the JSONL export. Every write
answers with a 303 redirect back to the list, carrying the error as a flash
when the service rejects it.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from ..db import get_db, snapshot
from ..services import focus, retro
from ..templating import _with_flash, templates

router = APIRouter()  # GET /retro, POST /retro, /retro/{entry_id}/*


def _retro_redirect(archived: bool = False, flash: str | None = None) -> RedirectResponse:
    url = "/retro" + ("?archived=1" if archived else "")
    return RedirectResponse(_with_flash(url, flash) if flash else url, status_code=303)


@router.get("/retro")
def get_retro(request: Request, archived: int = 0, edit: int | None = None,
              flash: str | None = None,
              conn: sqlite3.Connection = Depends(get_db)):
    show_archived = bool(archived)
    rows = retro.list_entries(conn, include_archived=show_archived)
    editing = retro.get_entry(conn, edit) if edit is not None else None
    if show_archived:
        rows = [r for r in rows if r["archived_at"] is not None]
    # The 14-day focus chart landed here when the Focus page was retired (#75):
    # the only Focus number that was not about one habit or lesson is the shape
    # of the fortnight, and this is the surface for looking back at one.
    # One snapshot for the three: a timer finishing in another tab between them
    # would otherwise print "25m today" over a chart whose today bar is still
    # empty, and the page would be arguing with itself.
    with snapshot(conn):
        daily = focus.daily_totals(conn)
        focus_streak = focus.focus_day_streak(daily)
        focus_ov = focus.overview(conn)
    return templates.TemplateResponse(request, "retro.html", {
        "request": request,
        "rail": "retro",
        "rows": rows,
        "show_archived": show_archived,
        "editing": editing,
        "confidences": retro.CONFIDENCES,
        "flash": flash,
        "focus_daily": daily,
        "focus_streak": focus_streak,
        "focus_ov": focus_ov,
    })


@router.post("/retro")
def post_retro_create(
    period: str = Form(""),
    precision: str = Form("month"),
    confidence: str = Form("medium"),
    project: str = Form(""),
    text: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        retro.create_entry(conn, period=period, precision=precision,
                                 confidence=confidence, project=project, text=text)
    except retro.RetroError as exc:
        return _retro_redirect(flash=str(exc))
    return _retro_redirect()


@router.post("/retro/{entry_id}/edit")
def post_retro_edit(
    entry_id: int,
    period: str = Form(""),
    precision: str = Form("month"),
    confidence: str = Form("medium"),
    project: str = Form(""),
    text: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        retro.update_entry(conn, entry_id, period=period, precision=precision,
                                 confidence=confidence, project=project, text=text)
    except retro.RetroError as exc:
        return _retro_redirect(flash=str(exc))
    return _retro_redirect()


@router.post("/retro/{entry_id}/archive")
def post_retro_archive(entry_id: int,
                       conn: sqlite3.Connection = Depends(get_db)):
    try:
        retro.archive_entry(conn, entry_id)
    except retro.RetroError as exc:
        return _retro_redirect(flash=str(exc))
    return _retro_redirect()


@router.post("/retro/{entry_id}/unarchive")
def post_retro_unarchive(entry_id: int,
                         conn: sqlite3.Connection = Depends(get_db)):
    try:
        retro.unarchive_entry(conn, entry_id)
    except retro.RetroError as exc:
        return _retro_redirect(archived=True, flash=str(exc))
    return _retro_redirect(archived=True)
