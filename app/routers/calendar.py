"""Calendar routes (#24 cut 2): the month grid, the timed week grid and the
calendar-event writes (sec32).

A pure move out of app/main.py — the router is included with no prefix at the
position these routes occupied, so every URL and the registration order are
exactly what defining them in main.py produced.
"""
from __future__ import annotations

import sqlite3
from datetime import date as _date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..db import get_db, is_valid_date, now_iso, pretty_date, today_str
from ..services import calendar_events, lists, tasks
from ..templating import (
    _parse_month, _safe_return, _sunday_of, _wants_json, _with_flash, templates,
)

router = APIRouter()


# --- Calendar (month grid) (sec: premium views) -----------------------------


def _task_chip(t) -> dict:
    """A due task as a calendar chip — shared by the month + week views
    (the templates' chip class ladder reads exactly these keys)."""
    return {
        "title": t["title"], "kind": t["kind"],
        "completed": t["completed_at"] is not None, "priority": t["priority"],
    }


def _month_grid(conn, year: int, month: int) -> list[list[dict]]:
    """Always six Sunday-start weeks for the month (TickTick fixes the grid at 6
    rows so its height never jumps), each cell carrying its day's task events."""
    import calendar as _cal
    today_d = _date.fromisoformat(today_str())
    grid = _cal.Calendar(firstweekday=6)  # 6 = Sunday
    first_cell = grid.monthdatescalendar(year, month)[0][0]
    days = [first_cell + timedelta(days=i) for i in range(42)]  # 6 weeks, fixed
    win_start, win_end = days[0].isoformat(), days[-1].isoformat()
    by_date: dict[str, list] = {}
    # Calendar events first so they sort above tasks in a cell (sec32 §13.6);
    # occurrences_between already returns all-day-first then by start_time.
    for o in calendar_events.occurrences_between(conn, win_start, win_end):
        by_date.setdefault(o["date"], []).append({**o, "kind": "event"})
    for t in tasks.due_between(conn, win_start, win_end):
        by_date.setdefault(t["due_date"], []).append(_task_chip(t))
    weeks: list[list[dict]] = []
    for w in range(6):
        cells = []
        for d in days[w * 7:(w + 1) * 7]:
            iso = d.isoformat()
            cells.append({
                "day": d.day,
                "date": iso,
                "in_month": d.month == month and d.year == year,
                "is_today": d == today_d,
                "month_abbr": d.strftime("%b"),
                "events": by_date.get(iso, []),
            })
        weeks.append(cells)
    return weeks


def _event_modal_ctx(conn, self_url: str, ev: str | None, on: str | None,
                     add: str | None = None, at: str | None = None) -> dict:
    """Context for the event modals on both calendar views (sec32 M3): the create
    modal always needs lists + today; ?ev=<id> opens the edit modal for that series
    (silently ignored if unknown/archived/garbage), with ?on=<date> carrying the
    clicked occurrence so the modal can offer Skip for exactly that day.
    ?add=<date>&at=<HH:MM> (the week grid's empty slots, M4) opens the CREATE modal
    prefilled instead — ignored when an edit modal is already being opened."""
    ctx = {"self_url": self_url, "today": today_str(),
           "cal_lists": lists.list_lists(conn),
           "edit_ev": None, "edit_exdates": [], "on": None,
           "new_date": None, "new_time": None}
    try:
        event_id = int(ev) if ev else None
    except ValueError:
        event_id = None
    if event_id is not None:
        row = calendar_events.get_event(conn, event_id)
        if row is not None and row["archived_at"] is None:
            ctx.update(edit_ev=row,
                       edit_exdates=calendar_events.exdates_of(row),
                       on=on if is_valid_date(on) else None)
    if ctx["edit_ev"] is None and is_valid_date(add):
        ctx.update(new_date=add,
                   new_time=at if calendar_events.is_valid_hhmm(at) else None)
    return ctx


@router.get("/calendar")
def get_calendar(request: Request, month: str | None = None, ev: str | None = None,
                 on: str | None = None, flash: str | None = None,
                 conn: sqlite3.Connection = Depends(get_db)):
    year, mon = _parse_month(month)
    first = _date(year, mon, 1)
    self_url = f"/calendar?month={first.strftime('%Y-%m')}"
    weeks = _month_grid(conn, year, mon)
    modal = _event_modal_ctx(conn, self_url, ev, on)
    prev_first = (first - timedelta(days=1)).replace(day=1)
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return templates.TemplateResponse(request,
        "calendar.html",
        {
            "request": request, "rail": "calendar",
            "month_label": first.strftime("%B %Y"),
            "weeks": weeks, "flash": flash, **modal,
            "prev_url": f"/calendar?month={prev_first.strftime('%Y-%m')}",
            "next_url": f"/calendar?month={next_first.strftime('%Y-%m')}",
        },
    )


# Timed week grid geometry: a fixed px-per-hour scale the template multiplies by.
_WEEK_HOUR_PX = 48          # height of one hour row
_WEEK_MIN_BLOCK_PX = 22     # floor so a 15-min slot stays legible (sec32 §6.1)
_WEEK_BAND = (6, 23)        # default visible band 06:00–23:00, expands to fit


def _week_ctx(conn, sun: _date) -> dict:
    """Build the Sunday-start week beginning at `sun` — the caller snaps via
    _sunday_of (firstweekday=6, matching the month grid): 7 day columns, an
    all-day row (all-day events + due tasks), and the timed grid with overlap
    columns (sec32 §6/§6.1)."""
    week_days = [sun + timedelta(days=i) for i in range(7)]
    start_iso, end_iso = week_days[0].isoformat(), week_days[-1].isoformat()
    occs = calendar_events.occurrences_between(conn, start_iso, end_iso)

    tasks_by_date: dict[str, list] = {}
    for t in tasks.due_between(conn, start_iso, end_iso):
        tasks_by_date.setdefault(t["due_date"], []).append(_task_chip(t))

    allday: dict[str, list] = {}
    timed: dict[str, list] = {}
    for o in occs:
        (timed if calendar_events.is_timed(o) else allday) \
            .setdefault(o["date"], []).append(o)

    # Lay each day out first — the engine owns all minute math (layout_day drops
    # all-day items and annotates canonical start_min/end_min, defaulting an open
    # end to +30 min) — so the band below always covers what actually renders.
    laid = {d.isoformat(): calendar_events.layout_day(timed.get(d.isoformat(), []))
            for d in week_days}

    # Visible band: default 06:00–23:00, widened (floor/ceil to the hour) to fit
    # any earlier/later timed occurrence anywhere in the week.
    band_start, band_end = _WEEK_BAND[0] * 60, _WEEK_BAND[1] * 60
    for o in (b for blocks in laid.values() for b in blocks):
        band_start = min(band_start, o["start_min"] // 60 * 60)
        band_end = max(band_end, -(-o["end_min"] // 60) * 60)  # ceil to the hour
    band_end = min(24 * 60, band_end)  # an open-ended 23:5x event (+30 min) ceils past midnight
    ppm = _WEEK_HOUR_PX / 60.0

    today_iso = today_str()
    # Current-time line (M4): rendered in today's column only, and only while
    # "now" falls inside the visible band (the band never widens just for it).
    now_top = None
    if week_days[0].isoformat() <= today_iso <= week_days[-1].isoformat():
        hhmm = now_iso()[11:16]  # wall-clock in the ledger zone (sec13.3)
        now_min = int(hhmm[:2]) * 60 + int(hhmm[3:])
        if band_start <= now_min <= band_end:
            now_top = round((now_min - band_start) * ppm, 1)
    days = []
    for d in week_days:
        iso = d.isoformat()
        blocks = []
        for o in laid[iso]:
            top = (o["start_min"] - band_start) * ppm
            height = max((o["end_min"] - o["start_min"]) * ppm, _WEEK_MIN_BLOCK_PX)
            blocks.append({
                "title": o["title"], "emoji": o["emoji"], "event_id": o["event_id"],
                "start_time": o["start_time"], "end_time": o["end_time"],
                "top": round(top, 1), "height": round(height, 1),
                "left": round(o["left"] * 100, 3), "width": round(o["width"] * 100, 3),
            })
        days.append({
            "date": iso, "dow": d.strftime("%a"), "dom": d.day,
            "is_today": iso == today_iso,
            "allday": allday.get(iso, []), "tasks": tasks_by_date.get(iso, []),
            "blocks": blocks,
        })

    hours = [{"label": f"{h:02d}:00", "top": round((h * 60 - band_start) * ppm, 1)}
             for h in range(band_start // 60, band_end // 60)]
    return {
        "days": days, "hours": hours, "now_top": now_top,
        "grid_h": int(round((band_end - band_start) * ppm)), "hour_px": _WEEK_HOUR_PX,
    }


def _parse_date(s: str | None) -> _date:
    """Parse ?date=YYYY-MM-DD, defaulting to today; reject garbage (same canonical
    date check as every other route — db.is_valid_date)."""
    return _date.fromisoformat(s if is_valid_date(s) else today_str())


@router.get("/calendar/week")
def get_calendar_week(request: Request, date: str | None = None, ev: str | None = None,
                      on: str | None = None, add: str | None = None,
                      at: str | None = None, flash: str | None = None,
                      conn: sqlite3.Connection = Depends(get_db)):
    sun = _sunday_of(_parse_date(date))
    self_url = f"/calendar/week?date={sun.isoformat()}"
    ctx = _week_ctx(conn, sun)
    ctx.update(_event_modal_ctx(conn, self_url, ev, on, add, at), flash=flash)
    last = sun + timedelta(days=6)
    if sun.month == last.month:
        label = f"{sun.strftime('%b')} {sun.day}–{last.day}, {sun.year}"
    elif sun.year == last.year:
        label = f"{sun.strftime('%b')} {sun.day} – {last.strftime('%b')} {last.day}, {sun.year}"
    else:
        label = f"{pretty_date(sun, year=True)} – {pretty_date(last, year=True)}"
    ctx.update({
        "request": request, "rail": "calendar", "week_label": label,
        "prev_url": f"/calendar/week?date={(sun - timedelta(days=7)).isoformat()}",
        "next_url": f"/calendar/week?date={(sun + timedelta(days=7)).isoformat()}",
    })
    return templates.TemplateResponse(request, "calendar_week.html", ctx)


# --- Calendar-event writes (sec32 M3): create / update / archive / skip ----


def _wd_mask(wd: list[str]) -> str:
    """The form's 7 weekday checkboxes (values '0'..'6', Mon..Sun) → the stored
    byweekday mask. The service nulls it for non-weekly freqs and rejects an
    all-zero mask on weekly, so the route just assembles."""
    return "".join("1" if str(i) in wd else "0" for i in range(7))


def _events_redirect(return_to: str, flash: str | None = None) -> RedirectResponse:
    url = _safe_return(return_to, "/calendar")
    return RedirectResponse(_with_flash(url, flash) if flash else url, status_code=303)


@router.post("/calendar/events")
def post_event_create(
    request: Request,
    title: str = Form(...),
    emoji: str = Form(""),
    list_id: str = Form(""),
    note: str = Form(""),
    all_day: str | None = Form(None),
    start_time: str = Form(""),
    end_time: str = Form(""),
    freq: str = Form("once"),
    wd: list[str] = Form([]),
    interval_n: str = Form("1"),
    start_date: str = Form(...),
    end_date: str = Form(""),
    return_to: str = Form("/calendar"),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        calendar_events.create_event(
            conn, title, start_date=start_date, freq=freq, byweekday=_wd_mask(wd),
            interval_n=interval_n, all_day=bool(all_day), start_time=start_time,
            end_time=end_time, end_date=end_date, list_id=list_id, emoji=emoji, note=note,
        )
    except calendar_events.CalendarEventError as exc:
        return _events_redirect(return_to, str(exc))
    return _events_redirect(return_to)


@router.post("/calendar/events/{event_id}")
def post_event_update(
    request: Request,
    event_id: int,
    title: str = Form(...),
    emoji: str = Form(""),
    list_id: str = Form(""),
    note: str = Form(""),
    all_day: str | None = Form(None),
    start_time: str = Form(""),
    end_time: str = Form(""),
    freq: str = Form("once"),
    wd: list[str] = Form([]),
    interval_n: str = Form("1"),
    start_date: str = Form(...),
    end_date: str = Form(""),
    return_to: str = Form("/calendar"),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Update the whole series ("All events" — v1 has no per-occurrence override;
    use Skip for one day). exdates survive the edit (the service preserves them)."""
    try:
        calendar_events.update_event(
            conn, event_id, title=title, emoji=emoji, list_id=list_id, note=note,
            all_day=bool(all_day), start_time=start_time, end_time=end_time,
            freq=freq, byweekday=_wd_mask(wd), interval_n=interval_n,
            start_date=start_date, end_date=end_date,
        )
    except calendar_events.CalendarEventError as exc:
        return _events_redirect(return_to, str(exc))
    return _events_redirect(return_to)


@router.post("/calendar/events/{event_id}/archive")
def post_event_archive(request: Request, event_id: int,
                       return_to: str = Form("/calendar"),
                       conn: sqlite3.Connection = Depends(get_db)):
    try:
        calendar_events.archive_event(conn, event_id)  # soft: series stays in the ledger
    except calendar_events.CalendarEventError as exc:
        return _events_redirect(return_to, str(exc))
    return _events_redirect(return_to)


@router.post("/calendar/events/{event_id}/skip")
def post_event_skip(request: Request, event_id: int, date: str = Form(...),
                    return_to: str = Form("/calendar"),
                    conn: sqlite3.Connection = Depends(get_db)):
    try:
        calendar_events.skip_occurrence(conn, event_id, date)
    except calendar_events.CalendarEventError as exc:
        return _events_redirect(return_to, str(exc))
    return _events_redirect(return_to)


@router.post("/calendar/events/{event_id}/unskip")
def post_event_unskip(request: Request, event_id: int, date: str = Form(...),
                      return_to: str = Form("/calendar"),
                      conn: sqlite3.Connection = Depends(get_db)):
    try:
        calendar_events.unskip_occurrence(conn, event_id, date)
    except calendar_events.CalendarEventError as exc:
        return _events_redirect(return_to, str(exc))
    return _events_redirect(return_to)


@router.post("/calendar/events/{event_id}/move")
def post_event_move(request: Request, event_id: int, date: str = Form(...),
                    return_to: str = Form("/calendar"),
                    conn: sqlite3.Connection = Depends(get_db)):
    """Drag-and-drop a non-recurring event to another day (Mode A/B)."""
    json_mode = _wants_json(request)
    try:
        calendar_events.move_event(conn, event_id, date)
        if json_mode:
            return JSONResponse({"ok": True, "event_id": event_id, "date": date})
    except calendar_events.CalendarEventError as exc:
        if json_mode:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        return _events_redirect(return_to, str(exc))
    return _events_redirect(return_to)
