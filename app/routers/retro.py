"""Retro routes (#24 cut 4): owner-typed retrospectives over approximate
periods (docs/retro-spec.md, issue #49).

A pure move out of app/main.py, where the block was the file tail: one router
included with no prefix at the position it occupied. URLs and registration
order are unchanged. Every write journals a full-snapshot event, which is what
the future selfos→exp2res adapter consumes from the JSONL export. The write
contract follows sec16.4 (Mode A form + Mode B fetch), same as /daily-note.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..db import get_db
from ..services import retro
from ..templating import _wants_json, _with_flash, templates

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


@router.post("/retro")
def post_retro_create(
    request: Request,
    period: str = Form(""),
    precision: str = Form("month"),
    confidence: str = Form("medium"),
    project: str = Form(""),
    text: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    json_mode = _wants_json(request)
    try:
        row = retro.create_entry(conn, period=period, precision=precision,
                                 confidence=confidence, project=project, text=text)
    except retro.RetroError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _retro_redirect(flash=str(exc))
    if json_mode:
        return JSONResponse({"ok": True, "id": row["id"], "uuid": row["uuid"]})
    return _retro_redirect()


@router.post("/retro/{entry_id}/edit")
def post_retro_edit(
    request: Request,
    entry_id: int,
    period: str = Form(""),
    precision: str = Form("month"),
    confidence: str = Form("medium"),
    project: str = Form(""),
    text: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    json_mode = _wants_json(request)
    try:
        row = retro.update_entry(conn, entry_id, period=period, precision=precision,
                                 confidence=confidence, project=project, text=text)
    except retro.RetroError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _retro_redirect(flash=str(exc))
    if json_mode:
        return JSONResponse({"ok": True, "id": row["id"], "uuid": row["uuid"]})
    return _retro_redirect()


@router.post("/retro/{entry_id}/archive")
def post_retro_archive(request: Request, entry_id: int,
                       conn: sqlite3.Connection = Depends(get_db)):
    json_mode = _wants_json(request)
    try:
        retro.archive_entry(conn, entry_id)
    except retro.RetroError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _retro_redirect(flash=str(exc))
    if json_mode:
        return JSONResponse({"ok": True})
    return _retro_redirect()


@router.post("/retro/{entry_id}/unarchive")
def post_retro_unarchive(request: Request, entry_id: int,
                         conn: sqlite3.Connection = Depends(get_db)):
    json_mode = _wants_json(request)
    try:
        retro.unarchive_entry(conn, entry_id)
    except retro.RetroError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _retro_redirect(archived=True, flash=str(exc))
    if json_mode:
        return JSONResponse({"ok": True})
    return _retro_redirect(archived=True)
