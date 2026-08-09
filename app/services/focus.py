"""Focus time — one timer, server-owned, attached to what it was spent on (#75).

Two rows, two lifetimes. A **run** (`focus_runs`) is the timer that is going
right now: at most one, created when the user starts it, holding nothing but
`started_at` and how long it has been paused. A **session** (`focus_sessions`)
is a finished span, and the only thing statistics ever read.

The elapsed time is derived here from `started_at`, never sent by the browser
(#20): the drawer lives on every page, so its clock cannot survive in a
`setInterval` closure, and a client-supplied duration would be a number the
server has no way to check. `client_token` makes both edges idempotent — a
retried start reuses the run, a retried finish returns the session the first
call recorded instead of counting the span twice.

A session names at most one target — lesson, habit or task — so the time lands
where it is already meaningful; there is no global Focus dashboard to feed.
Stats stay READ-ONLY derived (sec14): we sum rows, never recompute them
elsewhere, and each write appends its event in one txn (sec14.1).
"""
from __future__ import annotations

import sqlite3
from datetime import date as _date, datetime, timedelta

from .. import limits
from ..db import append_event, now_iso, pretty_date, today_str

MODES = ("countdown", "open")
"""'countdown' runs to a length the user chose; 'open' just tracks until stopped.

The pre-#75 vocabulary was ('pomo','stopwatch') — a fixed 25-minute cycle and a
free-running clock. Schema v19 converted those rows; historical
`focus_session_recorded` payloads keep the old words, which is why nothing here
may assume a stored event's mode is one of these two.
"""

# A single session can't reasonably exceed a day: clamp a run that was started
# and forgotten (laptop closed on Friday, reopened on Monday) so it can't poison
# the totals with a weekend of "focus".
MAX_SECONDS = 24 * 60 * 60
MIN_TARGET_SECONDS = 60
MAX_TARGET_SECONDS = 8 * 60 * 60

# target kind -> (table, id column on focus_sessions/focus_runs, liveness filter)
_TARGETS = {
    "lesson": ("lessons", "lesson_id", "archived_at IS NULL"),
    "habit": ("routine_items", "habit_id", "active = 1"),
    "task": ("tasks", "task_id", "completed_at IS NULL"),
}
TARGET_KINDS = tuple(_TARGETS)


class FocusError(ValueError):
    """A focus write was rejected (bad mode/duration, or no such timer)."""


# --- targets ---------------------------------------------------------------


def _coerce_target(conn: sqlite3.Connection, kind: str, value) -> int | None:
    """Accept only a positive id pointing at a real, live row of `kind`.

    Anything else — blank, junk, deleted, archived, an already-finished task —
    stores as NULL, so a stale picker value attaches the time to nothing rather
    than dangling. Liveness is checked at write time only: a lesson archived
    later keeps the sessions it earned.
    """
    table, _column, alive = _TARGETS[kind]
    try:
        target_id = int(value)
    except (TypeError, ValueError):
        return None
    if target_id <= 0:
        return None
    row = conn.execute(
        f"SELECT 1 FROM {table} WHERE id = ? AND {alive}", (target_id,)
    ).fetchone()
    return target_id if row else None


_NO_TARGET = {"lesson_id": None, "habit_id": None, "task_id": None}


def _one_target(resolved: dict) -> dict:
    """A span of attention is spent on one thing: letting two ids through would
    double-count the same minutes in two per-target totals."""
    if sum(1 for v in resolved.values() if v is not None) > 1:
        raise FocusError("a timer attaches to one target at most")
    return resolved


def _resolve_targets(conn: sqlite3.Connection, lesson_id=None, habit_id=None,
                     task_id=None) -> dict:
    """The three target columns for a write, coerced against what exists now."""
    return _one_target({
        "lesson_id": _coerce_target(conn, "lesson", lesson_id),
        "habit_id": _coerce_target(conn, "habit", habit_id),
        "task_id": _coerce_target(conn, "task", task_id),
    })


def _stored_targets(row: sqlite3.Row) -> dict:
    """The targets a RUNNING timer already carries, taken as given.

    Liveness was checked when the run started and must not be re-checked when
    it ends: finishing a task, archiving a lesson or retiring a habit while the
    timer is going is ordinary, and re-coercing would silently drop the target
    exactly then — losing the attribution for the work that just caused it.
    """
    return _one_target({
        "lesson_id": row["lesson_id"], "habit_id": row["habit_id"],
        "task_id": row["task_id"],
    })


def pickable_targets(conn: sqlite3.Connection) -> dict:
    """What the drawer's target picker offers: live lessons, active habits, open
    tasks. Fetched when the drawer opens rather than rendered into every page —
    the timer is global, but the picker's contents are not worth three extra
    queries on every request that only wanted a task list."""
    def rows(sql: str) -> list[dict]:
        return [{"id": r["id"], "title": r["title"]} for r in conn.execute(sql)]

    return {
        "lesson": rows(
            "SELECT id, title FROM lessons WHERE archived_at IS NULL "
            "ORDER BY (status = 'studying') DESC, title COLLATE NOCASE"
        ),
        "habit": rows(
            "SELECT id, title FROM routine_items WHERE active = 1 "
            "ORDER BY sort_order, id"
        ),
        # No LIMIT on the tasks: the picker is the only way to aim the timer at
        # one, and it has neither search nor pagination, so a cap would make
        # every task past it unfocusable rather than merely inconvenient.
        "task": rows(
            "SELECT id, title FROM tasks WHERE completed_at IS NULL "
            "ORDER BY due_date IS NULL, due_date, sort_order, id"
        ),
    }


def _target_view(r: sqlite3.Row) -> dict | None:
    """{'kind','id','title'} for whichever target a run/session row names."""
    for kind, (_table, column, _alive) in _TARGETS.items():
        if r[column] is not None:
            return {"kind": kind, "id": r[column], "title": r[f"{kind}_title"]}
    return None


# The join that carries every possible target's title alongside the row, so one
# view function can name what the time was spent on. LEFT JOINs keep unattached
# rows, and the titles are read live — a renamed habit renames its history.
_TARGET_JOIN = (
    "LEFT JOIN lessons l ON l.id = fs.lesson_id "
    "LEFT JOIN routine_items ri ON ri.id = fs.habit_id "
    "LEFT JOIN tasks tk ON tk.id = fs.task_id "
)
_TARGET_TITLES = (
    "l.title AS lesson_title, ri.title AS habit_title, tk.title AS task_title "
)


# --- the running timer -----------------------------------------------------


def _parse_iso(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _elapsed_seconds(row: sqlite3.Row, at: datetime | None = None) -> int:
    """Wall time since the run started, minus everything it spent paused.

    The only clock that counts. `at` is injectable so a finish can price the
    span with the same timestamp it stores.
    """
    started = _parse_iso(row["started_at"])
    if started is None:
        return 0
    at = at or _parse_iso(now_iso()) or started
    paused = int(row["paused_seconds"] or 0)
    paused_at = _parse_iso(row["paused_at"])
    if paused_at is not None:
        paused += max(0, int((at - paused_at).total_seconds()))
    return max(0, min(int((at - started).total_seconds()) - paused, MAX_SECONDS))


def _countdown_end(row: sqlite3.Row, target: int, now: datetime | None) -> str | None:
    """When a finished countdown actually ran out, as an ISO stamp.

    Only the totals are stored, not the pause history, so this reads as "the
    moment `target` seconds of running time had passed". That is exact because
    every stored pause provably started before the countdown ran out —
    `set_run_paused` refuses to pause one that already reached its target — so
    no post-completion idling can be added to the span. The ceiling is belt and
    braces: the span never ends in the future, or after a pause in flight.
    """
    started = _parse_iso(row["started_at"])
    if started is None:
        return None
    ended = started + timedelta(seconds=target + int(row["paused_seconds"] or 0))
    ceiling = _parse_iso(row["paused_at"]) or now
    if ceiling is not None and ended > ceiling:
        ended = ceiling
    return ended.isoformat(timespec="seconds")


def _run_view(row: sqlite3.Row | None, at: datetime | None = None) -> dict | None:
    """The whole state the drawer needs to render itself after any page load."""
    if row is None:
        return None
    elapsed = _elapsed_seconds(row, at)
    target_seconds = row["target_seconds"]
    return {
        "id": row["id"],
        "mode": row["mode"],
        "token": row["client_token"],
        "target_seconds": target_seconds,
        "started_at": row["started_at"],
        "paused": row["paused_at"] is not None,
        "elapsed": elapsed,
        "remaining": max(0, target_seconds - elapsed) if target_seconds else None,
        "done": bool(target_seconds and elapsed >= target_seconds),
        "note": row["note"],
        "target": _target_view(row),
    }


_RUN_SELECT = (
    "SELECT fs.*, " + _TARGET_TITLES + "FROM focus_runs fs " + _TARGET_JOIN
)


def active_run(conn: sqlite3.Connection) -> dict | None:
    """The timer that is running (or paused), if any. At most one exists."""
    row = conn.execute(_RUN_SELECT + "ORDER BY fs.id LIMIT 1").fetchone()
    return _run_view(row)


def _run_by_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return conn.execute(
        _RUN_SELECT + "WHERE fs.client_token = ?", (token,)
    ).fetchone()


def _clean_token(token) -> str:
    token = (token or "").strip()
    if not token or len(token) > 64:
        raise FocusError("invalid timer token")
    return token


def _clean_target_seconds(mode: str, value) -> int | None:
    """A countdown's chosen length. Open-ended tracking has none — and a bogus
    length is rejected rather than silently rounded, because that number is the
    user's whole intent for the session."""
    if mode == "open":
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        raise FocusError("choose how long the timer should run")
    if not (MIN_TARGET_SECONDS <= seconds <= MAX_TARGET_SECONDS):
        raise FocusError("timer length must be between 1 minute and 8 hours")
    return seconds


def start_run(conn: sqlite3.Connection, mode: str, token: str, *,
              target_seconds=None, note: str | None = None,
              lesson_id=None, habit_id=None, task_id=None) -> dict:
    """Open the timer. Returns the run view; the same token returns the same run.

    Starting is not journaled: an unfinished run is an intention, not a fact
    about the day. The event is appended when the span is recorded.
    """
    token = _clean_token(token)
    existing = _run_by_token(conn, token)
    if existing is not None:
        return _run_view(existing)
    if session_by_token(conn, token) is not None:
        # The token is spent: it already became a session. Opening a second run
        # on it would create a timer that can never be recorded — the session's
        # unique token would reject the finish, and the run would sit in the
        # singleton slot blocking every later start.
        raise FocusError("that timer already finished")
    if mode not in MODES:
        raise FocusError("unknown timer mode")
    target_seconds = _clean_target_seconds(mode, target_seconds)
    note = (note or "").strip() or None
    limits.check(note, limits.FOCUS_NOTE, "focus note", FocusError)
    targets = _resolve_targets(conn, lesson_id, habit_id, task_id)
    # The `slot` column is a UNIQUE singleton, so the "only one timer" rule is
    # the database's to enforce: two tabs starting at once would both clear a
    # SELECT-then-INSERT check, and only one of them can win this INSERT.
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO focus_runs (mode, target_seconds, note, started_at, "
                "lesson_id, habit_id, task_id, client_token) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (mode, target_seconds, note, now_iso(), targets["lesson_id"],
                 targets["habit_id"], targets["task_id"], token),
            )
            run_id = cur.lastrowid
    except sqlite3.IntegrityError:
        # Same race, seen from the other side: two clicks of the same button
        # share a token, so the loser is a repeat of a start that succeeded.
        raced = _run_by_token(conn, token)
        if raced is not None:
            return _run_view(raced)
        raise FocusError("a timer is already running")
    row = conn.execute(_RUN_SELECT + "WHERE fs.id = ?", (run_id,)).fetchone()
    return _run_view(row)


def set_run_paused(conn: sqlite3.Connection, token: str, paused: bool) -> dict:
    """Pause or resume the run. Pausing stamps `paused_at`; resuming folds that
    interval into `paused_seconds`, so elapsed time stays a pure function of
    stored timestamps and survives a reload taken mid-pause."""
    token = _clean_token(token)
    row = _run_by_token(conn, token)
    if row is None:
        raise FocusError("that timer is no longer running")
    if (row["paused_at"] is not None) == paused:
        return _run_view(row)
    target = int(row["target_seconds"] or 0)
    if paused and target and _elapsed_seconds(row) >= target:
        # A countdown that already ran out has nothing left to pause. Allowing it
        # would fold the idle time between "ran out" and "resumed" into
        # `paused_seconds`, and `_countdown_end` would then date the span from
        # after it truly ended — enough to move an overnight session onto the
        # next day's Retro bar. The drawer resyncs on a refusal and finishes it.
        raise FocusError("that countdown already finished — stop it to record it")
    with conn:
        if paused:
            conn.execute("UPDATE focus_runs SET paused_at = ? WHERE id = ?",
                         (now_iso(), row["id"]))
        else:
            gap = 0
            paused_at = _parse_iso(row["paused_at"])
            now = _parse_iso(now_iso())
            if paused_at is not None and now is not None:
                gap = max(0, int((now - paused_at).total_seconds()))
            conn.execute(
                "UPDATE focus_runs SET paused_at = NULL, paused_seconds = ? "
                "WHERE id = ?",
                (int(row["paused_seconds"] or 0) + gap, row["id"]),
            )
    return _run_view(
        conn.execute(_RUN_SELECT + "WHERE fs.id = ?", (row["id"],)).fetchone()
    )


def discard_run(conn: sqlite3.Connection, token: str) -> bool:
    """Throw the running timer away without recording anything. True if there was
    one. Not journaled — nothing happened that the ledger should remember."""
    token = _clean_token(token)
    with conn:
        cur = conn.execute("DELETE FROM focus_runs WHERE client_token = ?", (token,))
    return cur.rowcount > 0


def finish_run(conn: sqlite3.Connection, token: str) -> dict:
    """Stop the timer and record the span. Idempotent by token.

    The duration is computed here, from the run's own timestamps. A countdown is
    capped at the length the user asked for: a tab left open into minute 30 of a
    25-minute timer means 25 minutes of intent, not 30 of credit.
    """
    token = _clean_token(token)
    row = _run_by_token(conn, token)
    if row is None:
        recorded = session_by_token(conn, token)
        if recorded is not None:
            return recorded
        raise FocusError("that timer is no longer running")
    now = _parse_iso(now_iso())
    seconds = _elapsed_seconds(row, now)
    target = int(row["target_seconds"] or 0)
    # A paused clock stopped counting when it was paused, so that is when the
    # span ended — Stop pressed the next morning writes down last night's work,
    # not a session that shows a time nothing was worked at.
    ended_at = row["paused_at"]
    if target and seconds >= target:
        # The countdown ended when it ran out, not when the user came back to a
        # sleeping laptop. Stamping the return time would credit yesterday's
        # session to today and put it on the wrong bar of the Retro chart.
        seconds = target
        ended_at = _countdown_end(row, target, now)
    if seconds <= 0:
        # Stop pressed inside the same second as Start. The run stays: the drawer
        # keeps its own copy either way, and discarding it here would leave a
        # ticking timer the server no longer knows about.
        raise FocusError("nothing to record yet")
    try:
        session_id = record_session(
            conn, row["mode"], seconds,
            target_seconds=row["target_seconds"], note=row["note"],
            started_at=row["started_at"], ended_at=ended_at, token=token,
            targets=_stored_targets(row), run_id=row["id"],
        )
    except (sqlite3.IntegrityError, FocusError) as exc:
        # A double-click on Stop: both calls read the run, one wrote the session.
        # The loser fails either on the token's unique index or on finding the
        # run already claimed — and the token is the idempotency key, so it
        # returns the winner's row instead of a 500 or a 422.
        recorded = session_by_token(conn, token)
        if recorded is not None:
            return recorded
        if not isinstance(exc, sqlite3.IntegrityError):
            raise
        # Nothing was recorded, so the conflict is the other race: the target was
        # hard-deleted between reading the run and writing the session, and the
        # stale id no longer has a row to point at. The time was still spent —
        # record it detached, exactly as deleting a habit does to its history.
        session_id = record_session(
            conn, row["mode"], seconds,
            target_seconds=row["target_seconds"], note=row["note"],
            started_at=row["started_at"], ended_at=ended_at, token=token,
            targets=_NO_TARGET, run_id=row["id"],
        )
        return get_session_view(conn, session_id)
    return get_session_view(conn, session_id)


# --- write -----------------------------------------------------------------


def record_session(conn: sqlite3.Connection, mode: str, seconds, *,
                   target_seconds=None, note: str | None = None,
                   started_at: str | None = None, ended_at: str | None = None,
                   token: str | None = None,
                   lesson_id=None, habit_id=None, task_id=None,
                   targets: dict | None = None,
                   run_id: int | None = None) -> int:
    """Persist one finished span; returns its id. Row, run deletion and event in
    one txn — the run must not outlive the session it became, and a session must
    not exist without its ledger entry.

    `targets` is the already-resolved column set a finishing run hands over; any
    other caller passes raw ids and gets them coerced against live rows."""
    if mode not in MODES:
        raise FocusError("unknown timer mode")
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        raise FocusError("invalid duration")
    if seconds <= 0:
        raise FocusError("duration must be positive")
    seconds = min(seconds, MAX_SECONDS)
    note = (note or "").strip() or None
    limits.check(note, limits.FOCUS_NOTE, "focus note", FocusError)
    if targets is None:
        targets = _resolve_targets(conn, lesson_id, habit_id, task_id)
    ts = now_iso()
    # `ended_at` is when the span finished, `created_at` when it was written
    # down; they differ for a countdown finalised after the fact, and the day the
    # session counts towards follows the former.
    ended_at = ended_at or ts
    with conn:
        # The run is claimed BEFORE the session is written: Stop and Discard can
        # both be in flight, and whichever deletes the run owns the span. A
        # finish that finds it already gone is recording time the user threw
        # away, so it rolls back instead.
        if run_id is not None and not conn.execute(
            "DELETE FROM focus_runs WHERE id = ?", (run_id,)
        ).rowcount:
            raise FocusError("that timer is no longer running")
        cur = conn.execute(
            "INSERT INTO focus_sessions (mode, seconds, target_seconds, note, date, "
            "started_at, ended_at, created_at, lesson_id, habit_id, task_id, client_token) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mode, seconds, target_seconds, note, ended_at[:10], started_at, ended_at, ts,
             targets["lesson_id"], targets["habit_id"], targets["task_id"], token),
        )
        session_id = cur.lastrowid
        append_event(conn, "focus_session_recorded",
                     {"session_id": session_id, "mode": mode, "seconds": seconds,
                      "target_seconds": target_seconds, "note": note, **targets})
    return session_id


# --- duration formatting (shared by stats + record rows) -------------------


def _dur(seconds: int) -> dict:
    """{'value','unit'} — minutes under an hour, else hours to one decimal."""
    seconds = int(seconds or 0)
    minutes = seconds // 60
    if minutes < 60:
        return {"value": minutes, "unit": "m"}
    hours = round(seconds / 3600, 1)
    if hours == int(hours):
        hours = int(hours)
    return {"value": hours, "unit": "h"}


def _dur_label(seconds: int) -> str:
    """Compact human duration for a record row, e.g. '25m' / '1h 5m' / '40s'."""
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m" if m else f"{h}h"
    if m:
        return f"{m}m"
    return f"{s}s"


def _time_label(iso: str | None) -> str:
    """'HH:MM' from an ISO-8601 'YYYY-MM-DDTHH:MM:SS+ZZ:ZZ' timestamp."""
    try:
        return iso.split("T", 1)[1][:5]
    except (AttributeError, IndexError):
        return ""


# --- reads -----------------------------------------------------------------

# Pre-#75 rows keep their own words (schema v19 converted the table, but a
# database restored from an older export can still surface them through the
# audit stream), so the map answers for four modes and the lookup has a default.
_MODE_LABELS = {"countdown": "Timer", "open": "Open",
                "pomo": "Pomo", "stopwatch": "Stopwatch"}


def overview(conn: sqlite3.Connection) -> dict:
    """Today's and all-time focused time (derived, sec14).

    The Pomodoro counts this used to headline are gone with the page that showed
    them (#75): a count of 25-minute cycles measured the ritual, duration
    measures the thing itself.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(seconds), 0) AS today_sec, COUNT(*) AS today_n "
        "FROM focus_sessions WHERE date = ?",
        (today_str(),),
    ).fetchone()
    total = conn.execute(
        "SELECT COALESCE(SUM(seconds), 0) AS sec FROM focus_sessions"
    ).fetchone()
    return {
        "today_focus": _dur(row["today_sec"]),
        "today_seconds": row["today_sec"],
        "today_sessions": row["today_n"],
        "total_focus": _dur(total["sec"]),
        "total_seconds": total["sec"],
    }


def pomodoro_counts(conn: sqlite3.Connection) -> dict:
    """The two tallies `overview` dropped, for the legacy route's answer only.

    A pre-#75 page reads `today_pomo` / `total_pomo` off every response and
    would print `undefined` without them. What used to be a `pomo` row is now a
    25-minute countdown; a database restored from an older export can still
    hold the original word, so both spellings count.

    Deletable with the rest of the compat layer — docs/system-design.md sec34.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN date = ? THEN 1 ELSE 0 END), 0) AS today_pomo, "
        "COUNT(*) AS total_pomo FROM focus_sessions "
        "WHERE mode = 'pomo' OR (mode = 'countdown' AND target_seconds = 1500)",
        (today_str(),),
    ).fetchone()
    return {"today_pomo": row["today_pomo"], "total_pomo": row["total_pomo"]}


_RECORD_SELECT = (
    "SELECT fs.*, " + _TARGET_TITLES + "FROM focus_sessions fs " + _TARGET_JOIN
)


def _record_view(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "mode": r["mode"],
        "mode_label": _MODE_LABELS.get(r["mode"], "Focus"),
        "duration_label": _dur_label(r["seconds"]),
        "seconds": r["seconds"],
        "time_label": _time_label(r["ended_at"]),
        "date": r["date"],
        # The note is the only place a span says what it actually was. With the
        # Focus page gone this list is the one surface that can show it back.
        "note": r["note"],
        "target": _target_view(r),
    }


def recent_sessions(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Most-recent finished spans, newest first — what the drawer lists."""
    rows = conn.execute(
        _RECORD_SELECT + "ORDER BY fs.ended_at DESC, fs.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_record_view(r) for r in rows]


def get_session_view(conn: sqlite3.Connection, session_id: int) -> dict | None:
    """One session as a record-row dict (what a finish returns to the drawer)."""
    r = conn.execute(_RECORD_SELECT + "WHERE fs.id = ?", (session_id,)).fetchone()
    return _record_view(r) if r else None


def session_by_token(conn: sqlite3.Connection, token: str) -> dict | None:
    """The session a given timer already recorded, if the finish was retried."""
    r = conn.execute(_RECORD_SELECT + "WHERE fs.client_token = ?", (token,)).fetchone()
    return _record_view(r) if r else None


# --- daily aggregates (the Retro focus block) ------------------------------


def _daily_title(d: _date, minutes: int) -> str:
    md = pretty_date(d, weekday=True)
    return f"{md} · no focus" if not minutes else f"{md} · {minutes}m"


def daily_totals(conn: sqlite3.Connection, days: int = 14) -> list[dict]:
    """Per-day focus totals for the last `days` days, oldest->newest, missing
    days zero-filled. Powers the 'Last N days' bar chart. `value` is the chart
    magnitude (minutes); `title` is the per-bar tooltip."""
    today = _date.fromisoformat(today_str())
    start = today - timedelta(days=days - 1)
    rows = conn.execute(
        "SELECT date, COALESCE(SUM(seconds),0) AS sec "
        "FROM focus_sessions WHERE date >= ? GROUP BY date",
        (start.isoformat(),),
    ).fetchall()
    by_date = {r["date"]: r["sec"] for r in rows}
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        sec = by_date.get(d.isoformat(), 0)
        minutes = sec // 60
        out.append({
            "iso": d.isoformat(), "dow": d.strftime("%a"), "day": d.day,
            "seconds": sec, "minutes": minutes,
            "is_today": d == today, "value": minutes,
            "title": _daily_title(d, minutes),
        })
    return out


def lesson_totals(conn: sqlite3.Connection, days: int | None = None,
                  limit: int = 6) -> list[dict]:
    """Top lessons by focused time (all-time, or the last `days` days). Only
    sessions that named a lesson count — the per-lesson Focus breakdown."""
    params: list = []
    where = "WHERE fs.lesson_id IS NOT NULL "
    if days:
        start = (_date.fromisoformat(today_str()) - timedelta(days=days - 1)).isoformat()
        where += "AND fs.date >= ? "
        params.append(start)
    rows = conn.execute(
        "SELECT l.id AS lesson_id, l.title AS title, "
        "COALESCE(SUM(fs.seconds),0) AS sec "
        "FROM focus_sessions fs JOIN lessons l ON l.id = fs.lesson_id "
        + where + "GROUP BY l.id ORDER BY sec DESC, l.id LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [{
        "lesson_id": r["lesson_id"], "title": r["title"],
        "seconds": r["sec"], "minutes": r["sec"] // 60,
        "label": _dur_label(r["sec"]),
    } for r in rows]


def _target_total(conn: sqlite3.Connection, column: str, target_id: int) -> dict:
    row = conn.execute(
        "SELECT COALESCE(SUM(seconds),0) AS sec, COUNT(*) AS n, "
        "COALESCE(SUM(CASE WHEN date = ? THEN seconds ELSE 0 END),0) AS today_sec "
        f"FROM focus_sessions WHERE {column} = ?",
        (today_str(), target_id),
    ).fetchone()
    return {
        "seconds": row["sec"],
        "minutes": row["sec"] // 60,
        "sessions": row["n"],
        "today_seconds": row["today_sec"],
        "today_label": _dur_label(row["today_sec"]),
        "label": _dur_label(row["sec"]),
    }


def lesson_total(conn: sqlite3.Connection, lesson_id: int) -> dict:
    """All focused time recorded against ONE lesson — the Focus link the Learn
    record panel surfaces read-only, without ranking it against the others."""
    return _target_total(conn, "lesson_id", lesson_id)


def habit_total(conn: sqlite3.Connection, habit_id: int) -> dict:
    """All focused time recorded against ONE habit, plus today's share — the
    per-target stats that replaced the global Focus dashboard (#75)."""
    return _target_total(conn, "habit_id", habit_id)


def task_total(conn: sqlite3.Connection, task_id: int) -> dict:
    """All focused time recorded against ONE task. A task is offered as a timer
    target, so the time spent on it has to be readable on the task itself."""
    return _target_total(conn, "task_id", task_id)


def habit_totals(conn: sqlite3.Connection) -> dict[int, dict]:
    """{habit_id: {'seconds','label','today_seconds','today_label'}} for every
    habit that has recorded time — one query for a whole list of rows."""
    rows = conn.execute(
        "SELECT habit_id, COALESCE(SUM(seconds),0) AS sec, "
        "COALESCE(SUM(CASE WHEN date = ? THEN seconds ELSE 0 END),0) AS today_sec "
        "FROM focus_sessions WHERE habit_id IS NOT NULL GROUP BY habit_id",
        (today_str(),),
    ).fetchall()
    return {r["habit_id"]: {
        "seconds": r["sec"], "label": _dur_label(r["sec"]),
        "today_seconds": r["today_sec"], "today_label": _dur_label(r["today_sec"]),
    } for r in rows}


def focus_day_streak(daily: list[dict]) -> int:
    """Consecutive days with any focus, counting back from the last entry
    (today). Pure — operates on a daily_totals() list."""
    streak = 0
    for d in reversed(daily):
        if d["seconds"] > 0:
            streak += 1
        else:
            break
    return streak
