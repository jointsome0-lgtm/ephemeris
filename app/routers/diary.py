"""Diary routes (issue #2, docs/diary-spec.md): per-entry dated journal.

Same write contract as /retro (sec16.4: Mode A no-JS form + 303 PRG with
flash; Mode B `x-partial: 1` → JSON, errors 422). Every write journals a
full-snapshot event — that is what the future selfos→exp2res adapter consumes
from the JSONL export. The gap-questions strip is config-only coupling: when
SELFOS_EXP2RES_URL is set the template renders that URL in an iframe, and this
process never fetches or parses it.
"""
from __future__ import annotations

import sqlite3
from datetime import date as _date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import settings
from ..db import get_db, pretty_date, today_str
from ..services import diary
from ..templating import _wants_json, _with_flash, templates

router = APIRouter()  # GET /diary, POST /diary, /diary/{entry_id}/*

_TRUE_VALUES = {"1", "on", "true", "yes"}


def _diary_redirect(archived: bool = False, flash: str | None = None) -> RedirectResponse:
    url = "/diary" + ("?archived=1" if archived else "")
    return RedirectResponse(_with_flash(url, flash) if flash else url, status_code=303)


def _day_label(day_iso: str, today_iso: str) -> str:
    if day_iso == today_iso:
        return "Today"
    day = _date.fromisoformat(day_iso)
    if (_date.fromisoformat(today_iso) - day).days == 1:
        return "Yesterday"
    return pretty_date(day, weekday=True, year=True)


@router.get("/diary")
def get_diary(request: Request, archived: int = 0, edit: int | None = None,
              flash: str | None = None,
              conn: sqlite3.Connection = Depends(get_db)):
    show_archived = bool(archived)
    rows = diary.list_entries(conn, include_archived=show_archived)
    if show_archived:
        rows = [r for r in rows if r["archived_at"] is not None]
    editing = diary.get_entry(conn, edit) if edit is not None else None
    today = today_str()
    days: list[dict] = []
    for r in rows:
        if not days or days[-1]["date"] != r["entry_date"]:
            days.append({"date": r["entry_date"],
                         "label": _day_label(r["entry_date"], today),
                         "entries": []})
        days[-1]["entries"].append({"row": r, "tags": diary.entry_tags(r)})
    return templates.TemplateResponse(request, "diary.html", {
        "request": request,
        "rail": "diary",
        "days": days,
        "count": len(rows),
        "show_archived": show_archived,
        "editing": editing,
        "editing_tags": ", ".join(diary.entry_tags(editing)) if editing else "",
        "today": today,
        "flash": flash,
        "exp2res_url": settings.settings.exp2res_url,
    })


def _write_args(entry_date: str, text: str, tags: str, private: str,
                atlas_ref: str) -> dict:
    return {
        "entry_date": entry_date,
        "text": text,
        "tags": tags,
        "private": private.strip().lower() in _TRUE_VALUES,
        "atlas_ref": atlas_ref,
    }


@router.post("/diary")
def post_diary_create(
    request: Request,
    entry_date: str = Form(""),
    text: str = Form(""),
    tags: str = Form(""),
    private: str = Form(""),
    atlas_ref: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    json_mode = _wants_json(request)
    try:
        row = diary.create_entry(
            conn, **_write_args(entry_date, text, tags, private, atlas_ref))
    except diary.DiaryError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _diary_redirect(flash=str(exc))
    if json_mode:
        return JSONResponse({"ok": True, "id": row["id"], "uuid": row["uuid"]})
    return _diary_redirect()


@router.post("/diary/{entry_id}/edit")
def post_diary_edit(
    request: Request,
    entry_id: int,
    entry_date: str = Form(""),
    text: str = Form(""),
    tags: str = Form(""),
    private: str = Form(""),
    atlas_ref: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    json_mode = _wants_json(request)
    try:
        row = diary.update_entry(
            conn, entry_id, **_write_args(entry_date, text, tags, private, atlas_ref))
    except diary.DiaryError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _diary_redirect(flash=str(exc))
    if json_mode:
        return JSONResponse({"ok": True, "id": row["id"], "uuid": row["uuid"]})
    return _diary_redirect()


@router.post("/diary/{entry_id}/archive")
def post_diary_archive(request: Request, entry_id: int,
                       conn: sqlite3.Connection = Depends(get_db)):
    json_mode = _wants_json(request)
    try:
        diary.archive_entry(conn, entry_id)
    except diary.DiaryError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _diary_redirect(flash=str(exc))
    if json_mode:
        return JSONResponse({"ok": True})
    return _diary_redirect()


@router.post("/diary/{entry_id}/unarchive")
def post_diary_unarchive(request: Request, entry_id: int,
                         conn: sqlite3.Connection = Depends(get_db)):
    json_mode = _wants_json(request)
    try:
        diary.unarchive_entry(conn, entry_id)
    except diary.DiaryError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _diary_redirect(archived=True, flash=str(exc))
    if json_mode:
        return JSONResponse({"ok": True})
    return _diary_redirect(archived=True)
