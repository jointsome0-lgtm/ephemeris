"""Calendar events — timed, recurring blocks for the Calendar week/month views (sec32).

A row IS the series: concrete occurrences are expanded on read from the recurrence
rule (`occurs_on`), never materialised. This keeps recurring slots out of the task
smart-lists / Matrix and gives them no completion semantics — a class *happens*, it
isn't "done" (that's the Habit tab's job). Series are soft-archived, never hard-
deleted, so they stay joinable to their audit events (sec14.1). Each write appends
its event in the same transaction, same convention as tasks.py / items.py.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date as _date, timedelta
from typing import NamedTuple

from ..db import append_event, is_valid_date, now_iso
from . import lists as lists_svc

FREQS = ("once", "daily", "weekly")

# 'HH:MM' 24h, 00:00..23:59 — minute precision only (sec32 non-goals).
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class CalendarEventError(ValueError):
    """A calendar-event write was rejected (empty title, bad time/date/weekday mask, …)."""


def is_valid_hhmm(s: str | None) -> bool:
    """True for a real 'HH:MM' wall-clock string (00:00..23:59) — the same rule
    _clean enforces; public for routes validating time-shaped query params."""
    return bool(s and _TIME_RE.match(s))


# --- recurrence engine (sec32 §4) — pure & reusable ------------------------
# Deliberately free of DB coupling so a future "recurring tasks" feature can
# reuse it: a series is any mapping (sqlite3.Row or dict) carrying the fields
# _parse_rule reads — start_date, end_date, exdates, freq, byweekday, interval_n.


def _as_date(v) -> _date:
    return v if isinstance(v, _date) else _date.fromisoformat(v)


def _monday(d: _date) -> _date:
    """The Monday of d's week (date.weekday(): Mon=0 … Sun=6)."""
    return d - timedelta(days=d.weekday())


def _exdates_set(raw: str | None) -> set[str]:
    """Stored exdates (JSON text | NULL) → a set of 'YYYY-MM-DD'."""
    return set(json.loads(raw)) if raw else set()


class _Rule(NamedTuple):
    """A series' recurrence fields parsed once, so the day-by-day expansion walk
    doesn't redo the date + exdates-JSON parsing on every probed day."""
    start: _date
    end: _date | None
    freq: str
    interval: int
    mask: str            # 7-char Mon..Sun '01' mask; only meaningful for weekly
    exdates: set[str]


def _parse_rule(ev) -> _Rule:
    end = ev["end_date"]
    return _Rule(start=_as_date(ev["start_date"]),
                 end=_as_date(end) if end else None,
                 freq=ev["freq"], interval=ev["interval_n"],
                 mask=ev["byweekday"] or "", exdates=_exdates_set(ev["exdates"]))


def _occurs(r: _Rule, d: _date) -> bool:
    """The heart of the feature (sec32 §4) — cheapest rejects first."""
    if d < r.start or (r.end is not None and d > r.end):
        return False
    if r.freq == "once":
        ok = d == r.start
    elif r.freq == "daily":
        ok = (d - r.start).days % r.interval == 0
    elif r.freq == "weekly":
        # interval counts *weeks*, anchored to the Monday of start_date's week
        ok = (len(r.mask) == 7 and r.mask[d.weekday()] == "1"
              and (_monday(d) - _monday(r.start)).days // 7 % r.interval == 0)
    else:
        ok = False
    return ok and d.isoformat() not in r.exdates


def occurs_on(ev, d: _date) -> bool:
    """Pure predicate: does series `ev` have a concrete occurrence on date `d`?
    One-shot parse + check; the expansion loop parses once and calls _occurs."""
    return _occurs(_parse_rule(ev), d)


# --- read API (sec32 §4) — lightweight dicts the templates consume ----------


def _occurrence(row: sqlite3.Row, d: _date) -> dict:
    return {
        "event_id": row["id"],
        "date": d.isoformat(),
        "all_day": bool(row["all_day"]),
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "title": row["title"],
        "emoji": row["emoji"],
        "color": row["color"],
        "list_id": row["list_id"],
        "note": row["note"],
    }


def occurrences_between(conn: sqlite3.Connection, start: str, end: str) -> list[dict]:
    """Every concrete occurrence in [start, end] across all non-archived series,
    sorted by (date, all_day DESC, start_time, event_id).

    The calendar windows are ≤ 42 days (month) or 7 (week), so a plain day-by-day
    walk per candidate series is fine — do not prematurely optimise (sec32 §4)."""
    if not (is_valid_date(start) and is_valid_date(end)):
        raise CalendarEventError("invalid window (expected YYYY-MM-DD)")
    s, e = _date.fromisoformat(start), _date.fromisoformat(end)
    if e < s:
        return []
    rows = conn.execute(
        "SELECT * FROM calendar_events "
        "WHERE archived_at IS NULL AND start_date <= ? "
        "AND (end_date IS NULL OR end_date >= ?)",
        (end, start),
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        rule = _parse_rule(row)  # dates + exdates parse once, not once per probed day
        d = s
        while d <= e:
            if _occurs(rule, d):
                out.append(_occurrence(row, d))
            d += timedelta(days=1)
    # all_day first (not False < not True), then chronological by start_time
    out.sort(key=lambda o: (o["date"], not o["all_day"], o["start_time"] or "", o["event_id"]))
    return out


def occurrences_on(conn: sqlite3.Connection, day: str) -> list[dict]:
    return occurrences_between(conn, day, day)


# --- week/day overlap layout (sec32 §6.1) — pure render geometry -----------
# A render-only concern: the engine, model and month grid are untouched. Given
# ONE day's TIMED occurrences it packs transitively-overlapping events into
# side-by-side columns (Google-Calendar style) instead of stacking them. Each
# occurrence comes back carrying start_min/end_min plus col/ncols/left/width
# (left & width are 0..1 fractions of the day column); the route turns those
# into pixels with its own band + px-per-minute.


def _min_of(hhmm: str | None) -> int | None:
    """'HH:MM' → minutes since midnight; None/'' → None."""
    if not hhmm:
        return None
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def is_timed(o) -> bool:
    """Does this occurrence render in the timed grid (vs the sticky all-day row)?
    The one owner of that boundary — the week route buckets with it and
    layout_day filters with it, so the two can never disagree."""
    return bool(not o["all_day"] and o["start_time"])


def layout_day(occs: list[dict]) -> list[dict]:
    """Assign overlap columns to ONE day's timed occurrences (sec32 §6.1).

    All-day / time-less items are dropped (they live in the sticky all-day row).
    A NULL or non-positive end defaults to a 30-minute block so events still
    collide correctly. Mutates and returns the surviving dicts."""
    items = [o for o in occs if is_timed(o)]
    for o in items:
        s = _min_of(o["start_time"])
        e = _min_of(o["end_time"])
        o["start_min"] = s
        o["end_min"] = e if (e is not None and e > s) else s + 30
    items.sort(key=lambda o: (o["start_min"], o["end_min"]))

    # 1. cluster: maximal runs of transitively-overlapping events
    clusters: list[list[dict]] = []
    cur: list[dict] = []
    cur_end: int | None = None
    for o in items:
        if cur and o["start_min"] < cur_end:
            cur.append(o)
        else:
            if cur:
                clusters.append(cur)
            cur = [o]
        cur_end = max(cur_end or o["end_min"], o["end_min"])
    if cur:
        clusters.append(cur)

    # 2. greedy column assignment within each cluster (events are start-sorted)
    for cluster in clusters:
        col_end: list[int] = []  # running end-time of the last event per column
        for o in cluster:
            c = next((i for i, end in enumerate(col_end) if end <= o["start_min"]), None)
            if c is None:
                c = len(col_end)
                col_end.append(o["end_min"])
            else:
                col_end[c] = o["end_min"]
            o["col"] = c
        ncols = len(col_end)
        for o in cluster:
            o["ncols"] = ncols
            o["width"] = 1.0 / ncols
            o["left"] = o["col"] / ncols
    return items


# --- validation ------------------------------------------------------------


def _clean(conn, *, title, start_date, freq, byweekday, interval_n, all_day,
           start_time, end_time, end_date, list_id, emoji, note, color) -> dict:
    title = (title or "").strip()
    if not title:
        raise CalendarEventError("event title can’t be empty")
    if len(title) > 500:
        raise CalendarEventError("event title too long")

    start_date = (start_date or "").strip()
    if not is_valid_date(start_date):
        raise CalendarEventError("invalid start date (expected YYYY-MM-DD)")
    end_date = (end_date or "").strip() or None
    if end_date is not None:
        if not is_valid_date(end_date):
            raise CalendarEventError("invalid end date (expected YYYY-MM-DD)")
        if end_date < start_date:
            raise CalendarEventError("end date is before start date")

    freq = freq if freq in FREQS else "once"
    try:
        interval_n = int(interval_n)
    except (TypeError, ValueError):
        interval_n = 1
    if interval_n < 1:
        interval_n = 1

    byweekday = (byweekday or "").strip() or None
    if freq == "weekly":
        if (not byweekday or len(byweekday) != 7
                or any(c not in "01" for c in byweekday) or "1" not in byweekday):
            raise CalendarEventError("a weekly event needs a 7-char Mon..Sun mask with ≥1 day")
    else:
        byweekday = None  # only meaningful when freq='weekly'

    # checkbox decoding (on/None → bool) is the route's job, same as every sibling
    all_day = bool(all_day)
    if all_day:
        start_time = end_time = None
    else:
        start_time = (start_time or "").strip()
        if not _TIME_RE.match(start_time):
            raise CalendarEventError("invalid start time (expected HH:MM)")
        end_time = (end_time or "").strip() or None
        if end_time is not None:
            if not _TIME_RE.match(end_time):
                raise CalendarEventError("invalid end time (expected HH:MM)")
            if end_time < start_time:
                raise CalendarEventError("end time is before start time")

    emoji = (emoji or "").strip()[:8] or None
    note = (note or "").strip() or None
    color = (color or "").strip() or None

    if list_id is not None and str(list_id).strip() != "":
        try:
            list_id = int(list_id)
        except (TypeError, ValueError):
            raise CalendarEventError("invalid list")
        if lists_svc.get_list(conn, list_id) is None:
            raise CalendarEventError("unknown list")
    else:
        list_id = None

    return {
        "title": title, "emoji": emoji, "list_id": list_id, "note": note,
        "all_day": int(all_day), "start_time": start_time, "end_time": end_time,
        "freq": freq, "byweekday": byweekday, "interval_n": interval_n,
        "start_date": start_date, "end_date": end_date, "color": color,
    }


# --- writes ----------------------------------------------------------------

# The editable columns, in one place: _clean validates exactly these, and the
# INSERT/UPDATE SQL is generated from the same tuple, so adding an event field
# is a single-listing change. exdates/created_at/updated_at are managed apart.
_COLS = ("title", "emoji", "list_id", "note", "all_day", "start_time", "end_time",
         "freq", "byweekday", "interval_n", "start_date", "end_date", "color")


def create_event(conn: sqlite3.Connection, title: str, *, start_date: str,
                 freq: str = "once", byweekday: str | None = None, interval_n: int = 1,
                 all_day: bool = False, start_time: str | None = None,
                 end_time: str | None = None, end_date: str | None = None,
                 list_id: int | None = None, emoji: str | None = None,
                 note: str | None = None, color: str | None = None) -> int:
    c = _clean(conn, title=title, start_date=start_date, freq=freq, byweekday=byweekday,
               interval_n=interval_n, all_day=all_day, start_time=start_time,
               end_time=end_time, end_date=end_date, list_id=list_id, emoji=emoji,
               note=note, color=color)
    ts = now_iso()
    with conn:
        cur = conn.execute(
            f"INSERT INTO calendar_events ({', '.join(_COLS)}, created_at) "
            f"VALUES ({', '.join('?' * (len(_COLS) + 1))})",
            [*(c[k] for k in _COLS), ts],
        )
        event_id = cur.lastrowid
        append_event(conn, "calendar_event_created", {
            "calendar_event_id": event_id, "title": c["title"], "freq": c["freq"],
            "byweekday": c["byweekday"], "interval_n": c["interval_n"],
            "start_date": c["start_date"], "end_date": c["end_date"],
            "all_day": c["all_day"], "start_time": c["start_time"], "end_time": c["end_time"],
        })
    return event_id


def update_event(conn: sqlite3.Connection, event_id: int, **fields) -> None:
    """Patch the whole series — only the supplied keys change, the rest keep their
    value. `exdates` (per-occurrence skips) are preserved; edit those via skip/unskip."""
    row = get_event(conn, event_id)
    if row is None:
        raise CalendarEventError("unknown event")
    c = _clean(conn, **{k: fields.get(k, row[k]) for k in _COLS})
    ts = now_iso()
    with conn:
        conn.execute(
            f"UPDATE calendar_events SET {', '.join(k + '=?' for k in _COLS)}, "
            "updated_at=? WHERE id=?",
            [*(c[k] for k in _COLS), ts, event_id],
        )
        append_event(conn, "calendar_event_updated", {"calendar_event_id": event_id, "title": c["title"]})


def archive_event(conn: sqlite3.Connection, event_id: int) -> None:
    """Soft-delete the whole series (sets archived_at); occurrences vanish from reads."""
    row = conn.execute("SELECT title FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise CalendarEventError("unknown event")
    ts = now_iso()
    with conn:
        conn.execute("UPDATE calendar_events SET archived_at = ? WHERE id = ?", (ts, event_id))
        append_event(conn, "calendar_event_archived", {"calendar_event_id": event_id, "title": row["title"]})


def get_event(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()


def exdates_of(ev) -> list[str]:
    """A series row's skipped dates, sorted — the edit modal lists these with
    per-date Restore buttons (a skipped occurrence has no chip to click)."""
    return sorted(_exdates_set(ev["exdates"]))


def list_events(conn: sqlite3.Connection, include_archived: bool = False) -> list[sqlite3.Row]:
    q = "SELECT * FROM calendar_events"
    if not include_archived:
        q += " WHERE archived_at IS NULL"
    q += " ORDER BY start_date, (start_time IS NULL) DESC, start_time, id"
    return conn.execute(q).fetchall()


# --- single-occurrence skip / restore (EXDATE, sec32 §4) -------------------


def skip_occurrence(conn: sqlite3.Connection, event_id: int, date: str) -> None:
    """Hide one occurrence of a series without touching the rule (append to exdates)."""
    if not is_valid_date(date):
        raise CalendarEventError("invalid date (expected YYYY-MM-DD)")
    row = get_event(conn, event_id)
    if row is None or row["archived_at"] is not None:
        raise CalendarEventError("unknown event")
    ex = _exdates_set(row["exdates"])
    if date in ex:
        return  # already skipped — idempotent, no event
    if not occurs_on(row, _date.fromisoformat(date)):
        raise CalendarEventError("date is not an occurrence of this event")
    ex.add(date)
    ts = now_iso()
    with conn:
        conn.execute("UPDATE calendar_events SET exdates = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(sorted(ex)), ts, event_id))
        append_event(conn, "calendar_occurrence_skipped", {"calendar_event_id": event_id, "date": date})


def unskip_occurrence(conn: sqlite3.Connection, event_id: int, date: str) -> None:
    """Restore a previously-skipped occurrence (remove it from exdates)."""
    if not is_valid_date(date):
        raise CalendarEventError("invalid date (expected YYYY-MM-DD)")
    row = get_event(conn, event_id)
    if row is None or row["archived_at"] is not None:
        raise CalendarEventError("unknown event")
    ex = _exdates_set(row["exdates"])
    if date not in ex:
        return  # nothing to restore — idempotent, no event
    ex.discard(date)
    ts = now_iso()
    with conn:
        conn.execute("UPDATE calendar_events SET exdates = ?, updated_at = ? WHERE id = ?",
                     (json.dumps(sorted(ex)) if ex else None, ts, event_id))
        append_event(conn, "calendar_occurrence_unskipped", {"calendar_event_id": event_id, "date": date})
