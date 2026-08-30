"""Habit routes (#24 cut 2): the Habit tab, per-item detail, the check-in and
daily-note write contract (sec16.4/sec31), the habit writes and Manage Items.

A pure move out of app/main.py. The block was never contiguous there — GET
/history sits between the tab and the detail routes and stays in main.py until
cut 3 moves its `_render_day` renderer — so the surface is four routers, each
included at the position its own block occupied. URLs and registration order
are unchanged.
"""
from __future__ import annotations

import sqlite3
from datetime import date as _date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..db import get_db, pretty_date, today_str
from ..services import checkins, items, stats
from ..templating import (
    _enrich_groups, _habit_detail_ctx, _safe_return, _validated_write_date,
    _wants_json, _week_strip, _with_flash, templates,
)

router = APIRouter()            # GET /habits
detail_router = APIRouter()     # GET /habit/{item_id}, POST /checkins, /daily-note
write_router = APIRouter()      # POST /habits, /habits/{item_id}/*
items_router = APIRouter()      # GET/POST /items*


# --- Habit tab (TickTick-style: list + inline detail pane, sec31) ----------


def _habit_selection_ctx(conn, request: Request, sel: str | None, month: str | None,
                         edit: bool = False) -> dict:
    """Parse ?sel=habit-N into the detail-pane context (with optional edit mode)."""
    none = {"sel": None, "sel_id": None}
    if not sel:
        return none
    kind, _, raw = sel.partition("-")
    if kind != "habit":
        return none
    try:
        sid = int(raw)
    except ValueError:
        return none
    ctx = _habit_detail_ctx(conn, sid, month, f"{request.url.path}?sel=habit-{sid}")
    if ctx is None:
        return none
    ctx.update(sel="habit", sel_id=sid, pane=True, close_url=request.url.path, edit=edit)
    return ctx


def _render_habits(request: Request, conn, sel=None, month=None, edit=False, flash=None):
    today = today_str()
    raw_groups = checkins.today_view(conn, today)
    strip = _week_strip(conn, today)
    hist = stats.all_histories(conn)
    groups = _enrich_groups(raw_groups, hist, _date.fromisoformat(today))
    ctx = {
        "request": request, "rail": "habit", "date": today, "today": today,
        "pretty_date": pretty_date(_date.fromisoformat(today)),
        "week": strip, "groups": groups, "flash": flash,
        "daily_note": checkins.get_daily_note(conn, today),
        "sections": items.list_sections(conn),
        "default_section": (groups[0][0] if groups else items.DEFAULT_GROUP),
    }
    ctx.update(_habit_selection_ctx(conn, request, sel, month, edit))
    return templates.TemplateResponse(request,"habits.html", ctx)


@router.get("/habits")
def get_habits(request: Request, sel: str | None = None, month: str | None = None,
               edit: int = 0, flash: str | None = None,
               conn: sqlite3.Connection = Depends(get_db)):
    return _render_habits(request, conn, sel=sel, month=month, edit=bool(edit), flash=flash)


def _redirect_for(date: str, anchor: str, flash: str | None = None) -> str:
    base = "/habits" if date == today_str() else f"/history?date={date}"
    if flash:
        sep = "&" if "?" in base else "?"
        base = f"{base}{sep}flash={quote(flash)}"
    return f"{base}#{anchor}" if anchor else base


@detail_router.get("/habit/{item_id}")
def get_habit(request: Request, item_id: int, month: str | None = None,
              conn: sqlite3.Connection = Depends(get_db)):
    """Per-item detail page (sec16.6): stat cards + monthly heatmap + habit log.

    Mirrors TickTick's habit detail pane in PATTERN only; uses our four-status
    model so the heatmap is richer than a binary done/not-done grid (sec7.3).
    The same partial renders inline on the tasks view via ?sel=habit-{id}."""
    ctx = _habit_detail_ctx(conn, item_id, month, f"/habit/{item_id}")
    if ctx is None:
        raise HTTPException(status_code=404, detail="unknown item")
    ctx.update(request=request, rail="habit")
    return templates.TemplateResponse(request,"habit.html", ctx)


def _checkin_state(conn, date: str, item_id: int) -> dict:
    row = checkins.get_checkin(conn, date, item_id)
    smap = stats.history(conn, item_id)
    today_d = _date.fromisoformat(today_str())
    start = stats.item_start(conn, item_id)
    return {
        "ok": True,
        "item_id": item_id,
        "date": date,
        "status": row["status"] if row else None,
        "note": (row["note"] if row else "") or "",
        # so Mode B can refresh the row's streak + total + that day's ring without a reload
        "current_streak": stats.current_streak_from(smap, today_d, start),
        "best_streak": stats.best_streak_from(smap, today_d, start),
        "total": sum(1 for s in smap.values() if s in stats.KEPT),
    }


@detail_router.post("/checkins")
def post_checkin(
    request: Request,
    date: str = Form(...),
    routine_item_id: int = Form(...),
    status: str | None = Form(None),
    note: str | None = Form(None),
    return_to: str | None = Form(None),
    conn: sqlite3.Connection = Depends(get_db),
):
    date = _validated_write_date(date)
    anchor = f"item-{routine_item_id}"
    json_mode = _wants_json(request)

    def dest(flash: str | None = None) -> str:
        # compact habit rows on the tasks view pass return_to to stay put;
        # the rich day view omits it and falls back to the habit day route.
        if return_to:
            url = _safe_return(return_to)
            return f"{_with_flash(url, flash) if flash else url}#{anchor}"
        return _redirect_for(date, anchor, flash=flash)

    try:
        if status is not None and status != "":
            checkins.apply_status(conn, date, routine_item_id, status)
        elif note is not None:
            checkins.upsert_checkin(conn, date, routine_item_id, note=note)
        else:
            raise HTTPException(status_code=400, detail="nothing to update")
        if json_mode:
            return JSONResponse(_checkin_state(conn, date, routine_item_id))
    except checkins.CheckinError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return RedirectResponse(dest(str(exc)), status_code=303)
    return RedirectResponse(dest(), status_code=303)


@detail_router.post("/daily-note")
def post_daily_note(
    request: Request,
    date: str = Form(...),
    text: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    date = _validated_write_date(date)
    json_mode = _wants_json(request)
    try:
        checkins.upsert_daily_note(conn, date, text)
    except checkins.CheckinError as exc:
        # The only rejection this write has (the #23 length bound), answered the
        # way its sibling /checkins answers one: 422 in Mode B, flash in Mode A.
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return RedirectResponse(
            _redirect_for(date, "daily-note", flash=str(exc)), status_code=303)
    if json_mode:
        return JSONResponse({"ok": True, "date": date})
    return RedirectResponse(_redirect_for(date, "daily-note"), status_code=303)


# --- Habit tab writes (sec31): create / edit / archive / delete ------------


@write_router.post("/habits")
def post_habit_create(
    title: str = Form(...),
    group_name: str = Form(""),
    emoji: str = Form(""),
    start_date: str = Form(""),
    return_to: str = Form("/habits"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Create a habit from the four fields the form offers (#18)."""
    try:
        items.create_item(
            conn, title, group_name, emoji=emoji, start_date=(start_date or None),
        )
    except items.ItemError as exc:
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    return RedirectResponse(_safe_return(return_to), status_code=303)


@write_router.post("/habits/{item_id}/edit")
def post_habit_edit(
    item_id: int,
    title: str = Form(...),
    group_name: str = Form(""),
    emoji: str = Form(""),
    start_date: str = Form(""),
    return_to: str = Form("/habits"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Edit a habit's four fields (#18); the legacy columns are never touched."""
    try:
        items.update_item(
            conn, item_id, title, group_name, emoji=emoji,
            start_date=(start_date or None),
        )
    except items.ItemError as exc:
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    return RedirectResponse(_safe_return(return_to), status_code=303)


@write_router.post("/habits/{item_id}/archive")
def post_habit_archive(item_id: int, return_to: str = Form("/habits"),
                       conn: sqlite3.Connection = Depends(get_db)):
    try:
        items.deactivate_item(conn, item_id)  # soft retire; history kept
    except items.ItemError as exc:
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    return RedirectResponse(_safe_return(return_to), status_code=303)


@write_router.post("/habits/{item_id}/delete")
def post_habit_delete(item_id: int, return_to: str = Form("/habits"),
                      conn: sqlite3.Connection = Depends(get_db)):
    try:
        items.delete_item(conn, item_id)  # hard delete (events keep the audit trail)
    except items.ItemError as exc:
        return RedirectResponse(_with_flash(_safe_return(return_to), str(exc)), status_code=303)
    return RedirectResponse(_safe_return(return_to), status_code=303)


# --- Manage Items (sec15.3) ------------------------------------------------


def _items_redirect(flash: str | None = None) -> RedirectResponse:
    url = "/items" + (f"?flash={quote(flash)}" if flash else "")
    return RedirectResponse(url, status_code=303)


@items_router.get("/items")
def get_items(request: Request, flash: str | None = None,
              conn: sqlite3.Connection = Depends(get_db)):
    rows = items.list_items(conn)
    groups: list[tuple[str, list]] = []
    index: dict[str, list] = {}
    for r in rows:
        if not r["active"]:
            continue
        bucket = index.get(r["group_name"])
        if bucket is None:
            bucket = []
            index[r["group_name"]] = bucket
            groups.append((r["group_name"], bucket))
        bucket.append(r)
    inactive = [r for r in rows if not r["active"]]
    return templates.TemplateResponse(request,
        "items.html",
        {
            "request": request,
            "groups": groups,
            "inactive": inactive,
            "known_groups": list(index.keys()) or [items.DEFAULT_GROUP],
            "flash": flash,
            "rail": "items",
        },
    )


@items_router.post("/items")
def post_item_create(title: str = Form(...), group_name: str = Form(""),
                     conn: sqlite3.Connection = Depends(get_db)):
    try:
        items.create_item(conn, title, group_name)
    except items.ItemError as exc:
        return _items_redirect(str(exc))
    return _items_redirect()


@items_router.post("/items/{item_id}/edit")
def post_item_edit(item_id: int, title: str = Form(...),
                   group_name: str = Form(""),
                   conn: sqlite3.Connection = Depends(get_db)):
    try:
        items.update_item(conn, item_id, title, group_name)
    except items.ItemError as exc:
        return _items_redirect(str(exc))
    return _items_redirect()


@items_router.post("/items/{item_id}/deactivate")
def post_item_deactivate(item_id: int,
                         conn: sqlite3.Connection = Depends(get_db)):
    try:
        items.deactivate_item(conn, item_id)
    except items.ItemError as exc:
        return _items_redirect(str(exc))
    return _items_redirect()


@items_router.post("/items/{item_id}/reactivate")
def post_item_reactivate(item_id: int,
                         conn: sqlite3.Connection = Depends(get_db)):
    try:
        items.reactivate_item(conn, item_id)
    except items.ItemError as exc:
        return _items_redirect(str(exc))
    return _items_redirect()
