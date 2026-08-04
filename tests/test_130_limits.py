"""How much one write may carry, and what the instance costs on disk (#23).

Four things had no ceiling before this slice, and each of them fails silently
rather than loudly:

*Free text.* Titles were capped — four numbers spelled inline in four services
— and the notes beside them were not. A paste of a megabyte into a task note
went into SQLite, into the ledger event, into every export written afterwards,
and into the `LIKE` that search runs over that column.

*The request body.* Only the Learn JSON endpoints bounded what they read.
Ordinary form POSTs — tasks, habits, calendar — read whatever arrived.

*The exports directory.* Every press of Export JSONL wrote a full
serialization of an append-only stream and kept every earlier one.

*Knowing any of it.* Whether last night's backup ran was a question you
answered by listing a private directory over SSH.

The order below follows that list. What is deliberately NOT here: pagination
for the history views, which #23 also asks for. It is a UI decision — what
happens to the scroll position, how "load more" behaves with no JavaScript —
not a limit, and answering it inside a limits change would be guessing at it.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app import limits, security
from app.db import get_conn, today_str
from app.services import (
    calendar_events as cal,
    checkins,
    export,
    focus,
    items,
    lists,
    storage,
    tasks,
)


def _conn() -> sqlite3.Connection:
    return get_conn()


def _flash(response) -> str:
    """The message a Mode A route redirected with, or "" if it redirected clean.

    Both spellings the app uses land in the same place: `_with_flash` appends
    `?flash=`, and the habit routes append it before their `#anchor`.
    """
    location = response.headers.get("location", "")
    query = parse_qs(urlsplit(location).query)
    return query.get("flash", [""])[0]


# --- 1. the field bounds ---------------------------------------------------
#
# The first case is a tripwire, not a behaviour test. Moving four inline
# integers into one module is a refactor exactly as long as the integers do not
# change on the way, and a silently narrowed title cap would show up as
# somebody's rejected task months later, not here.


def test_the_title_bounds_are_the_ones_that_already_shipped():
    """Pinned against `main` as of the move: tasks 500, habits 200, lists 100,
    events 500. A change here is a product decision and must fail this line
    before it reaches anyone's data."""
    assert (limits.TASK_TITLE, limits.ROUTINE_ITEM_TITLE,
            limits.LIST_NAME, limits.EVENT_TITLE) == (500, 200, 100, 500)


def test_the_note_bounds_are_generous_enough_to_be_uncontroversial():
    """Ten thousand characters is roughly four pages; the day's journal gets
    twice that. These exist to stop an accident, not to shape anyone's
    writing, and a future edit that tightens them past comfort should have to
    say so out loud."""
    assert limits.TASK_NOTE == limits.CHECKIN_NOTE == limits.EVENT_NOTE == 10_000
    assert limits.FOCUS_NOTE == 10_000
    assert limits.DAILY_NOTE == 20_000


def test_check_measures_characters_rather_than_bytes():
    """A cap counted in bytes would make the same sentence fit in English and
    overflow in Russian, with nothing on screen to explain the difference."""
    cyrillic = "я" * limits.TASK_NOTE           # 2 bytes each when encoded
    limits.check(cyrillic, limits.TASK_NOTE, "task note", ValueError)
    with pytest.raises(ValueError):
        limits.check(cyrillic + "я", limits.TASK_NOTE, "task note", ValueError)


def test_check_passes_absence_through():
    """Every caller decides for itself whether a missing value is allowed; the
    bound has an opinion only about length."""
    limits.check(None, 1, "x", ValueError)
    limits.check("", 1, "x", ValueError)


# Each row: what it is called, the bound, and a writer that raises the module's
# own error when the text is too long. Exactly-at-the-bound must pass and
# bound-plus-one must not, for all nine bounded fields.

def _service_cases(client):
    conn = _conn()
    list_id = lists.create_list(conn, "#23 Limits")
    task_id = tasks.create_task(conn, "#23 subject", list_id=list_id)
    item_id = items.create_item(conn, "#23 habit", "#23")
    event_id = cal.create_event(conn, "#23 event", start_date="2032-04-01", all_day=True)
    today = today_str()
    conn.close()

    def task_title(text):
        tasks.update_task(_conn(), task_id, title=text)

    def task_note(text):
        tasks.update_task(_conn(), task_id, note=text)

    def item_title(text):
        items.update_item(_conn(), item_id, text, "#23")

    def list_name(text):
        # There is no rename; a list's name is bounded where it is set.
        lists.create_list(_conn(), text)

    def event_title(text):
        cal.update_event(_conn(), event_id, title=text)

    def event_note(text):
        cal.update_event(_conn(), event_id, note=text)

    def focus_note(text):
        focus.record_session(_conn(), "pomo", 60, note=text)

    def checkin_note(text):
        checkins.upsert_checkin(_conn(), today, item_id, status="full_done", note=text)

    def daily_note(text):
        checkins.upsert_daily_note(_conn(), today, text)

    return [
        ("task title", limits.TASK_TITLE, task_title, tasks.TaskError),
        ("task note", limits.TASK_NOTE, task_note, tasks.TaskError),
        ("title", limits.ROUTINE_ITEM_TITLE, item_title, items.ItemError),
        ("list name", limits.LIST_NAME, list_name, lists.ListError),
        ("event title", limits.EVENT_TITLE, event_title, cal.CalendarEventError),
        ("event note", limits.EVENT_NOTE, event_note, cal.CalendarEventError),
        ("focus note", limits.FOCUS_NOTE, focus_note, focus.FocusError),
        ("check-in note", limits.CHECKIN_NOTE, checkin_note, checkins.CheckinError),
        ("daily note", limits.DAILY_NOTE, daily_note, checkins.CheckinError),
    ]


def test_every_bounded_field_accepts_its_bound_and_refuses_one_more(client):
    """The boundary itself, on the service that owns each field.

    Both sides matter. A cap that rejected its own stated length would be a
    lie in the error message; one that accepted a character past it would make
    the number decorative.
    """
    for label, bound, write, error in _service_cases(client):
        write("x" * bound)  # exactly at the bound: no exception
        with pytest.raises(error) as caught:
            write("x" * (bound + 1))
        assert str(caught.value) == f"{label} too long", label


# The same boundary through the routes, because a service that refuses text no
# route can send would be untested belief. Mode A answers with a flash on the
# redirect; Mode B (the `X-Partial` fetch paths) answers 422.


def test_an_over_long_task_note_is_refused_by_its_route(client):
    conn = _conn()
    task_id = tasks.create_task(conn, "#23 route subject")
    conn.close()
    ok = client.post(f"/tasks/{task_id}/update", data={
        "title": "#23 route subject", "note": "x" * limits.TASK_NOTE,
        "list_id": 1, "return_to": "/today"}, follow_redirects=False)
    too_long = client.post(f"/tasks/{task_id}/update", data={
        "title": "#23 route subject", "note": "x" * (limits.TASK_NOTE + 1),
        "list_id": 1, "return_to": "/today"}, follow_redirects=False)
    assert ok.status_code == 303 and _flash(ok) == ""
    assert too_long.status_code == 303 and _flash(too_long) == "task note too long"

    conn = _conn()
    stored = conn.execute("SELECT note FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    assert len(stored["note"]) == limits.TASK_NOTE, "the refused note was not stored"


def test_an_over_long_daily_note_is_refused_by_its_route(client):
    """This route had no failure branch at all before the bound existed, so the
    bound and the branch that reports it are one change."""
    today = today_str()
    ok = client.post("/daily-note", data={"date": today, "text": "x" * limits.DAILY_NOTE},
                     headers={"X-Partial": "1"})
    too_long = client.post("/daily-note",
                           data={"date": today, "text": "x" * (limits.DAILY_NOTE + 1)},
                           headers={"X-Partial": "1"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert too_long.status_code == 422
    assert too_long.json() == {"ok": False, "error": "daily note too long"}

    conn = _conn()
    stored = checkins.get_daily_note(conn, today)
    conn.close()
    assert len(stored) == limits.DAILY_NOTE


def test_an_over_long_checkin_note_is_refused_by_its_route(client):
    conn = _conn()
    item_id = items.create_item(conn, "#23 route habit", "#23")
    conn.close()
    today = today_str()
    body = {"date": today, "routine_item_id": item_id}
    # The flow is status-first: a note-only write with no row yet is refused by
    # the older rule, so establish the row before the bound is what decides.
    seeded = client.post("/checkins", data={**body, "status": "full_done"},
                         headers={"X-Partial": "1"})
    ok = client.post("/checkins", data={**body, "note": "x" * limits.CHECKIN_NOTE},
                     headers={"X-Partial": "1"})
    too_long = client.post(
        "/checkins", data={**body, "note": "x" * (limits.CHECKIN_NOTE + 1)},
        headers={"X-Partial": "1"})
    assert seeded.status_code == 200 and ok.status_code == 200
    assert too_long.status_code == 422
    assert too_long.json()["error"] == "check-in note too long"

    conn = _conn()
    stored = checkins.get_checkin(conn, today, item_id)
    conn.close()
    assert len(stored["note"]) == limits.CHECKIN_NOTE


def test_an_over_long_focus_note_is_refused_by_its_route(client):
    too_long = client.post("/focus/session", data={
        "mode": "pomo", "seconds": 60, "note": "x" * (limits.FOCUS_NOTE + 1)},
        headers={"X-Partial": "1"})
    assert too_long.status_code == 422
    assert too_long.json()["error"] == "focus note too long"


def test_an_over_long_event_note_is_refused_by_its_route(client):
    too_long = client.post("/calendar/events", data={
        "title": "#23 route event", "start_date": "2032-05-01", "all_day": "on",
        "note": "x" * (limits.EVENT_NOTE + 1), "return_to": "/calendar"},
        follow_redirects=False)
    assert too_long.status_code == 303
    assert _flash(too_long) == "event note too long"


def test_an_over_long_habit_title_is_refused_by_its_route(client):
    too_long = client.post("/habits", data={
        "title": "x" * (limits.ROUTINE_ITEM_TITLE + 1), "group_name": "#23",
        "return_to": "/habits"}, follow_redirects=False)
    assert too_long.status_code == 303 and _flash(too_long) == "title too long"


def test_an_over_long_list_name_is_refused_by_its_route(client):
    too_long = client.post("/lists", data={"name": "x" * (limits.LIST_NAME + 1)},
                           follow_redirects=False)
    assert too_long.status_code == 303 and _flash(too_long) == "list name too long"


def test_a_task_note_that_predates_the_cap_does_not_block_unrelated_edits(client):
    """A cap governs what is written next, not what is already stored.

    Every note in the database predates this cap — there was no cap. Both task
    writers refill unsupplied columns from the current row, so measuring the
    note on every update would mean a task with a long old note could not be
    renamed, rescheduled or moved until the user destroyed the text. Rejecting
    old data to accept a due-date change is not a limit, it is a hostage.
    """
    conn = _conn()
    try:
        task_id = tasks.create_task(conn, "#23 grandfathered", note="short")
        legacy = "x" * (limits.TASK_NOTE + 500)
        conn.execute("UPDATE tasks SET note = ? WHERE id = ?", (legacy, task_id))
        conn.commit()

        # Unrelated edits: the over-cap note rides along untouched.
        tasks.update_task(conn, task_id, title="#23 renamed")
        tasks.update_task(conn, task_id, due_date=today_str())
        row = tasks.get_task(conn, task_id)
        assert row["title"] == "#23 renamed"
        assert row["note"] == legacy, "the old note must survive an unrelated edit"

        # Writing a new over-cap note is still refused.
        with pytest.raises(tasks.TaskError, match="task note too long"):
            tasks.update_task(conn, task_id, note="y" * (limits.TASK_NOTE + 1))
        # And shortening it to something legal works, which is the way out.
        tasks.update_task(conn, task_id, note="brief")
        assert tasks.get_task(conn, task_id)["note"] == "brief"
    finally:
        conn.close()


def test_an_event_note_that_predates_the_cap_does_not_block_a_drag(client):
    """The same rule on the calendar, where the refill also carries a drag:
    `move_event` is an `update_event` with one new field, so a legacy note
    would have made an old event undraggable."""
    conn = _conn()
    try:
        event_id = cal.create_event(conn, title="#23 legacy event",
                                    start_date=today_str(), all_day=True)
        legacy = "x" * (limits.EVENT_NOTE + 500)
        conn.execute("UPDATE calendar_events SET note = ? WHERE id = ?",
                     (legacy, event_id))
        conn.commit()

        moved_to = (date.fromisoformat(today_str()) + timedelta(days=3)).isoformat()
        cal.move_event(conn, event_id, moved_to)
        row = cal.get_event(conn, event_id)
        assert row["start_date"] == moved_to
        assert row["note"] == legacy

        with pytest.raises(cal.CalendarEventError, match="event note too long"):
            cal.update_event(conn, event_id, note="y" * (limits.EVENT_NOTE + 1))
    finally:
        conn.close()


def test_a_legacy_event_note_with_stray_whitespace_still_does_not_block_a_drag(
        client):
    """The comparison normalizes both sides, or it would not be a comparison.

    The stored note is stripped on its way in — except in rows written before
    the strip and the cap existed, which is precisely the population this
    grandfathering serves. Comparing a stripped refill against a raw stored
    value would call an untouched note "changed" over a trailing newline and
    refuse the drag anyway.
    """
    conn = _conn()
    try:
        event_id = cal.create_event(conn, title="#23 whitespace event",
                                    start_date=today_str(), all_day=True)
        legacy = "  " + "x" * (limits.EVENT_NOTE + 500) + "\n"
        conn.execute("UPDATE calendar_events SET note = ? WHERE id = ?",
                     (legacy, event_id))
        conn.commit()

        moved_to = (date.fromisoformat(today_str()) + timedelta(days=4)).isoformat()
        cal.move_event(conn, event_id, moved_to)
        assert cal.get_event(conn, event_id)["start_date"] == moved_to
    finally:
        conn.close()


# --- 2. the request-body ceiling -------------------------------------------

_OVER = b"x" * (3 * 1024 * 1024)   # comfortably past the 2 MiB ceiling


def test_the_ceiling_clears_every_per_route_cap():
    """The perimeter is a ceiling OVER the route caps, never a replacement.

    If it ever sank below one, that route's precise typed refusal would be
    replaced by the perimeter's blunt plain-text 413 — a silent contract change
    for the lesson agent, which reads the error code. Read from the routes
    rather than restated, so a route that raises its own cap fails here.
    """
    from app.routers import learn
    from app.services import artifacts, runs

    route_caps = {
        "artifact": artifacts.MAX_BODY_BYTES,
        "run": runs.MAX_BODY_BYTES,
        "attempt": learn._ATTEMPT_MAX_BODY,
        "assessment": learn._ASSESSMENT_MAX_BODY,
    }
    assert limits.MAX_BODY_BYTES > max(route_caps.values()), route_caps
    assert security.MAX_BODY_BYTES == limits.MAX_BODY_BYTES, (
        "the suite must run at the default ceiling; EPHEMERIS_MAX_BODY_BYTES is set"
    )
    # The floor under any override is the same number, spelled once in limits
    # because the perimeter must not import a router to learn it.
    assert limits.LARGEST_ROUTE_CAP == max(route_caps.values()), route_caps


def test_an_oversized_form_post_is_refused(client):
    """The honest-client path: Content-Length says three megabytes, so the
    refusal happens before a byte of the body is pulled."""
    answer = client.post("/tasks", data={"title": "#23 " + "x" * (3 * 1024 * 1024)},
                         headers={"X-Partial": "1"})
    assert answer.status_code == 413
    assert "too large" in answer.text


def test_an_oversized_body_is_refused_without_a_content_length(client):
    """The authority is the streaming count, not the header.

    A chunked request declares no length at all. If the header were the only
    check, this is the request that would walk straight past it — and it is the
    one an attacker sends.
    """
    # Many modest fields, not one enormous one: a single 3 MiB field would be
    # refused by Starlette's own 1 MB per-field bound long before the whole-body
    # count got anywhere near the ceiling, and this case would then be passing
    # for somebody else's reason.
    def chunks():
        yield b"title=%2323+chunked"
        for n in range(400):
            yield b"&f%d=%s" % (n, b"x" * 8000)

    answer = client.post("/tasks", content=chunks(),
                         headers={"Content-Type": "application/x-www-form-urlencoded",
                                  "X-Partial": "1"})
    assert answer.status_code == 413


def test_a_cut_off_body_never_reaches_the_route(client):
    """The property that makes cutting the body safe at all.

    The middleware stops feeding chunks at the ceiling. If the route were
    simply handed the prefix, a 3 MiB paste would create a task from whatever
    happened to arrive first — an oversized write turned into a wrong one. The
    app is handed a disconnect instead, so no route can act on a short body.
    """
    def chunks():
        yield b"title=%2323+truncation+probe"
        for n in range(400):        # modest fields, as above
            yield b"&f%d=%s" % (n, b"x" * 8000)

    answer = client.post("/tasks", content=chunks(),
                         headers={"Content-Type": "application/x-www-form-urlencoded",
                                  "X-Partial": "1"})
    conn = _conn()
    made = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE title LIKE '%truncation probe%'"
    ).fetchone()[0]
    conn.close()
    assert answer.status_code == 413
    assert made == 0, "a body cut off at the ceiling was written anyway"


def test_a_body_inside_the_ceiling_still_writes(client):
    """A large-but-legal body is not collateral damage: most of a megabyte of
    form data is under the ceiling and must behave exactly as it did before."""
    answer = client.post("/tasks", data={
        "title": "#23 inside the ceiling", "note_pad": "x" * (900 * 1024)},
        headers={"X-Partial": "1"})
    conn = _conn()
    made = conn.execute(
        "SELECT COUNT(*) FROM tasks WHERE title = '#23 inside the ceiling'"
    ).fetchone()[0]
    conn.close()
    assert answer.status_code == 200 and made == 1


def test_starlette_still_owns_the_per_field_bound(client):
    """Not this middleware's doing, and recorded so nobody removes the ceiling
    believing this bound already covers it — or adds a second copy of it.

    Starlette's form parser refuses a single field over 1 MB by itself, well
    under the 2 MiB whole-body ceiling. The two answer different questions: one
    field cannot be a megabyte, and all of them together cannot be two.
    """
    answer = client.post("/tasks", data={
        "title": "#23 one huge field", "pad": "x" * (1024 * 1024 + 1)},
        headers={"X-Partial": "1"})
    assert answer.status_code == 400
    assert "maximum size" in answer.text


@pytest.mark.parametrize("url, size, code", [
    ("/learn/lessons/999999/blocks/b/file", 512 * 1024 + 1, "payload-too-large"),
    ("/learn/lessons/999999/attempts", 256 * 1024 + 1, "payload-too-large"),
    ("/learn/lessons/999999/assessments", 64 * 1024 + 1, "payload-too-large"),
])
def test_a_learn_endpoint_keeps_its_own_refusal(client, url, size, code):
    """Every one of these is over its own cap and under the perimeter's, so the
    route decides — and answers in its own typed JSON vocabulary, which the
    lesson agent's retry logic reads. The perimeter must be invisible here."""
    answer = client.post(url, content=b"{" + b"x" * size,
                         headers={"Content-Type": "application/json"})
    assert answer.status_code == 413
    assert answer.json()["error"] == code


def test_the_websocket_handshake_meets_no_body_counter(client):
    """The terminal's upgrade must reach its own gate untouched — a handshake
    has no method and no body, and wrapping its receive would sit between the
    PTY pump and the socket for the life of the session."""
    from starlette.websockets import WebSocketDisconnect

    refused_by_the_terminal_gate = False
    try:
        with client.websocket_connect("/terminal/ws"):
            pass
    except WebSocketDisconnect as exc:
        # 1008 from app/terminal.py's loopback-peer check: the perimeter passed
        # the handshake through to it rather than answering itself.
        refused_by_the_terminal_gate = exc.code == 1008
    assert refused_by_the_terminal_gate


def test_safe_methods_and_handshakes_get_the_original_receive():
    """Stated directly against the middleware, because the consequence — an SSE
    response or a WS session held open behind a wrapper — is invisible in a
    status code. Only unsafe methods get a counter."""
    seen = {}

    async def spy(scope, receive, send):
        seen[scope["type"], scope.get("method")] = receive
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    middleware = security.SecurityMiddleware(spy)
    base = {"headers": [(b"host", b"testserver")], "scheme": "http"}

    async def drive():
        await middleware({"type": "http", "method": "GET", **base}, receive, send)
        await middleware({"type": "websocket", **base}, receive, send)
        await middleware({"type": "http", "method": "POST", **base}, receive, send)

    asyncio.run(drive())

    assert seen["http", "GET"] is receive
    assert seen["websocket", None] is receive
    assert seen["http", "POST"] is not receive, "an unsafe method IS counted"


def test_a_streamed_response_passes_through_chunk_by_chunk():
    """An SSE response is many `http.response.body` messages with
    `more_body: True`. The middleware wraps `send` to add headers; a wrapper
    that coalesced or dropped the tail would turn a live run's output into one
    silent block at the end."""
    async def sse(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        for n in range(3):
            await send({"type": "http.response.body",
                        "body": f"data: {n}\n\n".encode(), "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(security.SecurityMiddleware(sse)(
        {"type": "http", "method": "GET", "scheme": "http",
         "headers": [(b"host", b"testserver")]}, receive, send))

    bodies = [m for m in sent if m["type"] == "http.response.body"]
    assert [m["body"] for m in bodies] == [b"data: 0\n\n", b"data: 1\n\n",
                                           b"data: 2\n\n", b""]
    assert [m.get("more_body", False) for m in bodies] == [True, True, True, False]


def _headers(mapping: dict):
    from starlette.datastructures import Headers

    return Headers(raw=[(k.encode(), v.encode()) for k, v in mapping.items()])


def test_a_junk_content_length_is_left_to_the_route(client):
    """The Learn endpoints answer a malformed Content-Length in their own
    vocabulary. The perimeter deliberately declines to have an opinion, so
    those answers are unchanged."""
    answer = client.post("/learn/lessons/999999/attempts", content=b"{}",
                         headers={"Content-Type": "application/json",
                                  "Content-Length": "not-a-number"})
    # httpx recomputes a real Content-Length for a bytes body, so the header
    # never reaches the app malformed; the direct-ASGI case below is the one
    # that can state it.
    assert answer.status_code in (400, 404, 422)
    assert security._declared_length(_headers({"content-length": "not-a-number"})) is None
    assert security._declared_length(_headers({"content-length": "-1"})) is None
    assert security._declared_length(_headers({})) is None
    assert security._declared_length(_headers({"content-length": "17"})) == 17


def test_a_bad_ceiling_setting_falls_back_to_the_constant(monkeypatch):
    """A typo in a unit file must not be how this protection switches off."""
    for junk in ("", "nonsense", "0", "-5"):
        monkeypatch.setenv("EPHEMERIS_MAX_BODY_BYTES", junk)
        assert security._body_ceiling() == limits.MAX_BODY_BYTES, junk
    monkeypatch.setenv("EPHEMERIS_MAX_BODY_BYTES", str(8 * 1024 * 1024))
    assert security._body_ceiling() == 8 * 1024 * 1024


def test_the_ceiling_cannot_be_set_down_near_the_largest_route_cap(monkeypatch):
    """An override may raise the perimeter, never lower it onto the route caps.

    A ceiling under a Learn endpoint's own cap tightens nothing — that route
    already bounds itself — but it does start answering oversized saves with
    this middleware's plain-text 413 instead of the typed JSON the lesson agent
    parses. Equality is refused for the same reason, and so is anything within
    a chunk of it: this counter withholds the whole delivery that crosses it,
    so two limits closer together than one chunk are crossed together and only
    the outer one answers. The setting is declined and the default stands.
    """
    floor = limits.LARGEST_ROUTE_CAP + limits.BODY_CEILING_HEADROOM
    for too_small in (1024, limits.LARGEST_ROUTE_CAP, limits.LARGEST_ROUTE_CAP + 1,
                      floor - 1):
        monkeypatch.setenv("EPHEMERIS_MAX_BODY_BYTES", str(too_small))
        assert security._body_ceiling() == limits.MAX_BODY_BYTES, too_small
    monkeypatch.setenv("EPHEMERIS_MAX_BODY_BYTES", str(floor))
    assert security._body_ceiling() == floor
    # The shipped default has to satisfy its own rule.
    assert limits.MAX_BODY_BYTES >= floor


# --- 3. export retention ---------------------------------------------------


@pytest.fixture
def exports_dir(tmp_path, monkeypatch):
    """Redirect exports at the service module, which binds EXPORTS_DIR on import."""
    target = tmp_path / "exports"
    target.mkdir()
    monkeypatch.setattr(export, "EXPORTS_DIR", target)
    return target


def _age(directory, seconds: int = 3600) -> None:
    """Back-date every export in `directory` so retention stops sparing it.

    Retention leaves anything written in the last `limits.EXPORT_GRACE` seconds
    alone, because a file that new may still be streaming to a browser. Exports
    made in a test loop are all "just written", so the loop has to say out loud
    that time passed between them — which is what happens in life, where the
    button is pressed by a person.
    """
    past = time.time() - seconds
    for path in directory.glob("events-*.jsonl"):
        os.utime(path, (past, past))


def test_retention_keeps_only_the_newest_thirty(client, exports_dir, monkeypatch):
    """Thirty-five exports in, thirty out — and the thirty that survive are the
    newest, not an arbitrary thirty."""
    conn = _conn()
    try:
        for n in range(35):
            monkeypatch.setattr(export, "now_stamp", lambda n=n: f"2033-01-01-{n:06d}")
            export.export_events(conn)
            _age(exports_dir)   # the next press is not in the same second
    finally:
        conn.close()

    left = sorted(p.name for p in exports_dir.glob("events-*.jsonl"))
    assert len(left) == limits.EXPORT_KEEP == 30
    assert left[0] == "events-2033-01-01-000005.jsonl"
    assert left[-1] == "events-2033-01-01-000034.jsonl"


def test_retention_never_removes_the_export_it_just_wrote(client, exports_dir,
                                                          monkeypatch):
    """The file the route is about to hand to the browser survives retention —
    asserted rather than assumed, because a FileResponse over a deleted path is
    a 500 at download time."""
    conn = _conn()
    try:
        for n in range(limits.EXPORT_KEEP + 3):
            monkeypatch.setattr(export, "now_stamp", lambda n=n: f"2034-02-02-{n:06d}")
            path, _count = export.export_events(conn)
            assert path.exists(), path.name
    finally:
        conn.close()


def test_a_clock_that_steps_backwards_does_not_delete_the_new_export(
        client, exports_dir, monkeypatch):
    """Being written beats being named: the export just produced is the newest
    one however its stamp sorts.

    The wall clock is not monotonic — a DST fallback or an NTP correction can
    stamp a fresh export an hour behind thirty existing ones. Ordering by name
    alone would then put it in the doomed slice and unlink it between the write
    and the FileResponse, so pressing Export would fail exactly when the clock
    happened to move.
    """
    for n in range(limits.EXPORT_KEEP):
        (exports_dir / f"events-2037-05-05-{n:06d}.jsonl").write_text("{}\n")
    _age(exports_dir)

    conn = _conn()
    try:
        # An hour earlier than every file already there.
        monkeypatch.setattr(export, "now_stamp", lambda: "2037-05-04-235959")
        path, _count = export.export_events(conn)
    finally:
        conn.close()

    assert path.exists(), "the export the caller was handed was pruned"
    assert len(export.existing_exports()) == limits.EXPORT_KEEP
    # It stayed by displacing the oldest of the thirty, not by growing the set.
    assert not (exports_dir / "events-2037-05-05-000000.jsonl").exists()


def test_an_export_in_progress_is_neither_counted_nor_deleted(client, exports_dir):
    """A staged `.events-*.jsonl.tmp` belongs to a run that has not finished.
    Retention walking it would delete another process's work in progress."""
    staged = exports_dir / ".events-abc.jsonl.tmp"
    staged.write_text("half an export")
    for n in range(limits.EXPORT_KEEP + 2):
        (exports_dir / f"events-2035-03-03-{n:06d}.jsonl").write_text("{}\n")
    _age(exports_dir)

    removed = export.prune_exports()

    assert staged.exists()
    assert staged not in removed
    assert len(export.existing_exports()) == limits.EXPORT_KEEP


def test_retention_survives_a_file_it_cannot_remove(client, exports_dir, monkeypatch):
    """Housekeeping must not sink an export that already succeeded: the bytes
    are on disk and named, and the caller is owed the path."""
    for n in range(limits.EXPORT_KEEP + 2):
        (exports_dir / f"events-2036-04-04-{n:06d}.jsonl").write_text("{}\n")
    _age(exports_dir)

    def refuse(self, *a, **kw):
        raise PermissionError("read-only medium")

    monkeypatch.setattr("pathlib.Path.unlink", refuse)
    assert export.prune_exports() == []
    assert len(export.existing_exports()) == limits.EXPORT_KEEP + 2


# --- 4. the storage status panel -------------------------------------------


@pytest.fixture
def backups_dir(tmp_path, monkeypatch):
    """Point the panel at an invented backups directory; nothing writes here."""
    target = tmp_path / "backups"
    target.mkdir()
    monkeypatch.setattr(storage, "BACKUPS_DIR", target)
    return target


@pytest.fixture
def host_zone():
    """Move the HOST's local zone for one test, and put it back afterwards.

    Not the same knob as `app_tz`, and that is the point: everything the panel
    dates is supposed to come out in the LEDGER's zone no matter what the
    machine's is, so proving it takes a machine that disagrees. `TZ` plus
    `time.tzset()` is what `datetime.astimezone()` reads for a naive value.
    """
    previous = os.environ.get("TZ")

    def move(name: str) -> None:
        os.environ["TZ"] = name
        time.tzset()

    try:
        yield move
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def _at(day: date, hour: int) -> str:
    """`day` at `hour` o'clock, written the way the backup script writes it.

    In the LEDGER's zone, with that zone's offset — not a hardcoded one. The
    panel dates a backup in `app_tz()` and subtracts it from `today_str()`, so
    a fixture that pinned "+03:00" would land on the previous day on a UTC
    machine and make the age off by one there and only there.
    """
    tz = storage.app_tz()
    moment = datetime(day.year, day.month, day.day, hour)
    return moment.replace(tzinfo=tz).isoformat() if tz else moment.astimezone().isoformat()


def _write_manifest(directory, when: date, *, stamp: str | None = None,
                    db_bytes: int = 4096, files_bytes: int = 2048) -> None:
    """A manifest in the shape scripts/backup_db.py publishes."""
    stamp = stamp or f"{when.isoformat()}-120000"
    (directory / f"activity-{stamp}.manifest.json").write_text(json.dumps({
        "manifest_version": 1,
        "created_at": _at(when, 12),
        "stamp": stamp,
        "files": {
            "database": {"name": f"activity-{stamp}.sqlite", "bytes": db_bytes,
                         "sha256": "0" * 64},
            "instance": {"name": f"files-{stamp}.tar.gz", "bytes": files_bytes,
                         "sha256": "0" * 64},
        },
    }, indent=2), encoding="utf-8")


def test_the_panel_warns_when_no_backup_has_ever_been_written(client, backups_dir):
    conn = _conn()
    try:
        state = storage.status(conn)
    finally:
        conn.close()
    assert state["backup"] is None
    assert any("No backup set" in w for w in state["warnings"])

    page = client.get("/export")
    assert page.status_code == 200
    assert "No backup set has been written yet" in page.text
    assert "storage-warn" in page.text


def test_a_backup_from_today_raises_no_warning(client, backups_dir):
    _write_manifest(backups_dir, date.fromisoformat(today_str()))
    conn = _conn()
    try:
        state = storage.status(conn)
    finally:
        conn.close()
    assert state["backup_age_days"] == 0
    assert state["backup"]["size_h"]
    assert not [w for w in state["warnings"] if "backup" in w]

    page = client.get("/export")
    assert "Newest backup" in page.text
    assert "storage-warn" not in page.text


def test_a_week_old_backup_is_reported_stale(client, backups_dir):
    _write_manifest(backups_dir, date.fromisoformat(today_str()) - timedelta(days=8))
    conn = _conn()
    try:
        state = storage.status(conn)
    finally:
        conn.close()
    assert state["backup_age_days"] == 8
    assert any("8 days old" in w for w in state["warnings"])

    page = client.get("/export")
    assert "storage-warn" in page.text and "8 days old" in page.text


def test_the_stale_threshold_is_exclusive_at_seven_days(client, backups_dir):
    """Seven days is the last day that is still fine; the eighth is not. Stated
    because an off-by-one here is a warning that never fires or one that always
    does."""
    _write_manifest(backups_dir, date.fromisoformat(today_str()) - timedelta(days=7))
    conn = _conn()
    try:
        state = storage.status(conn)
    finally:
        conn.close()
    assert state["backup_age_days"] == limits.BACKUP_STALE_DAYS
    assert state["warnings"] == []


def test_the_newest_readable_manifest_wins_over_a_corrupt_newer_one(client,
                                                                    backups_dir):
    """A truncated manifest is not a backup — but it must not hide the good set
    behind it, which is exactly the moment the answer matters most."""
    good = date.fromisoformat(today_str()) - timedelta(days=1)
    _write_manifest(backups_dir, good, stamp="2000-01-01-000001")
    (backups_dir / "activity-2999-01-01-000001.manifest.json").write_text("{trunc")

    found = storage.newest_backup()
    assert found is not None
    assert found["name"] == "activity-2000-01-01-000001.manifest.json"


@pytest.mark.parametrize("files", [["a", "b"], "activity.sqlite", 7, True])
def test_a_manifest_whose_files_field_is_not_a_mapping_is_skipped(
        client, backups_dir, files):
    """Valid JSON, readable date, nonsense members — still not a backup set.

    A manifest hand-edited, half-written, or produced by a future writer can
    put anything under `files`. Iterating it as a mapping would raise out of a
    page render and turn every GET /export into a 500 — while a perfectly good
    older set sat behind it, which is the case the fallback exists for.
    """
    good = date.fromisoformat(today_str()) - timedelta(days=1)
    _write_manifest(backups_dir, good, stamp="2000-01-01-000001")
    (backups_dir / "activity-2999-01-01-000002.manifest.json").write_text(
        json.dumps({"manifest_version": 1,
                    "created_at": "2999-01-01T00:00:02+03:00",
                    "files": files}), encoding="utf-8")

    found = storage.newest_backup()
    assert found is not None
    assert found["name"] == "activity-2000-01-01-000001.manifest.json"

    page = client.get("/export")
    assert page.status_code == 200


@pytest.mark.parametrize("files", [
    {},
    {"database": {"name": "activity-x.sqlite", "bytes": 10}},
    {"database": {"name": "activity-x.sqlite"}, "instance": {"bytes": 10}},
    {"database": {"name": ""}, "instance": {"name": "files-x.tar.gz"}},
    {"database": "activity-x.sqlite", "instance": "files-x.tar.gz"},
])
def test_a_manifest_that_names_no_restorable_set_is_skipped(client, backups_dir,
                                                            files):
    """A manifest is a backup only when it names both members with names.

    `load_manifest` refuses each of these, so the set behind them cannot be
    verified or restored — and a panel that counted them would answer "you have
    a backup" on the strength of a file that only describes one.
    """
    good = date.fromisoformat(today_str()) - timedelta(days=1)
    _write_manifest(backups_dir, good, stamp="2000-01-01-000001")
    (backups_dir / "activity-2999-01-01-000004.manifest.json").write_text(
        json.dumps({"manifest_version": storage.MANIFEST_VERSION,
                    "created_at": "2999-01-01T00:00:04+03:00",
                    "files": files}), encoding="utf-8")

    found = storage.newest_backup()
    assert found is not None
    assert found["name"] == "activity-2000-01-01-000001.manifest.json"


@pytest.mark.parametrize("claimed", [10**400, -1])
def test_a_manifest_claiming_an_impossible_size_is_skipped(client, backups_dir,
                                                           claimed):
    """`bytes` mirrors a `stat()`, so a number no `stat()` can return is damage.

    Reporting it is not the mild half of the failure. `export.human_size` starts
    with `float(n)`, which raises `OverflowError` past roughly 1e308, and every
    candidate is read on every render — so one hand-edited digit takes down the
    whole `GET /export`, warnings included, while a good older set sits behind
    it. A negative claim is quieter and just as wrong: believed, it makes the
    damaged set look smaller than the one it hides.
    """
    good = date.fromisoformat(today_str()) - timedelta(days=1)
    _write_manifest(backups_dir, good, stamp="2000-01-01-000001")
    _write_manifest(backups_dir, date.fromisoformat(today_str()),
                    stamp="2999-01-01-000006", db_bytes=claimed)

    found = storage.newest_backup()
    assert found is not None
    assert found["name"] == "activity-2000-01-01-000001.manifest.json"
    assert client.get("/export").status_code == 200


def test_an_impossible_size_leaves_the_no_backup_warning_standing(client,
                                                                  backups_dir):
    """With nothing behind it, a skipped set has to read as no backup at all.

    The fallback in the test above could be satisfied by a page that renders
    the older set and swallows everything else; this is the case where there is
    no older set, and the panel's whole job is to say so out loud.
    """
    _write_manifest(backups_dir, date.fromisoformat(today_str()),
                    stamp="2999-01-01-000007", files_bytes=10**400)

    conn = _conn()
    try:
        state = storage.status(conn)
    finally:
        conn.close()
    assert state["backup"] is None
    assert any("No backup set" in w for w in state["warnings"])

    page = client.get("/export")
    assert page.status_code == 200
    assert "No backup set has been written yet" in page.text


def test_the_newest_backup_is_the_one_that_says_it_is_newest(client, backups_dir):
    """The timestamp inside the manifest decides, not the stamp in the name.

    A clock that steps backwards — DST, an NTP correction — writes the next
    backup under a lexically older name while its own `created_at` correctly
    records the later moment. Trusting the name would report the wrong set, its
    wrong size, and an age a day too large, twice a year.
    """
    older = date.fromisoformat(today_str()) - timedelta(days=2)
    _write_manifest(backups_dir, older, stamp="2038-06-01-030000")
    # Named an hour behind the one above, created an hour after it.
    (backups_dir / "activity-2038-06-01-020000.manifest.json").write_text(
        json.dumps({"manifest_version": storage.MANIFEST_VERSION,
                    "created_at": _at(date.fromisoformat(today_str()), 2),
                    "stamp": "2038-06-01-020000",
                    "files": {
                        "database": {"name": "activity-2038-06-01-020000.sqlite",
                                     "bytes": 4096},
                        "instance": {"name": "files-2038-06-01-020000.tar.gz",
                                     "bytes": 2048},
                    }}), encoding="utf-8")

    found = storage.newest_backup()
    assert found is not None
    assert found["name"] == "activity-2038-06-01-020000.manifest.json"

    conn = _conn()
    try:
        assert storage.status(conn)["backup_age_days"] == 0
    finally:
        conn.close()


def test_a_backup_is_dated_in_the_ledgers_zone_not_the_hosts(client, backups_dir,
                                                             monkeypatch):
    """The panel subtracts the backup's date from `today_str()`, and those two
    must be dates in the same calendar.

    `today_str()` answers in APP_TIMEZONE when it is set. Reading the manifest
    in the host's zone instead would put the two on different days around
    midnight whenever the app is configured for a zone the machine is not in —
    the stale warning arriving a day early, or a day late.
    """
    from zoneinfo import ZoneInfo

    # A moment that is one calendar day earlier in Tokyo than in New York.
    (backups_dir / "activity-2042-03-03-000000.manifest.json").write_text(
        json.dumps({"manifest_version": storage.MANIFEST_VERSION,
                    "created_at": "2042-03-03T14:00:00+00:00",
                    "files": {"database": {"name": "a.sqlite", "bytes": 1},
                              "instance": {"name": "b.tar.gz", "bytes": 1}}}),
        encoding="utf-8")

    monkeypatch.setattr(storage, "app_tz", lambda: ZoneInfo("Asia/Tokyo"))
    assert storage.newest_backup()["created"].date() == date(2042, 3, 3)

    monkeypatch.setattr(storage, "app_tz", lambda: ZoneInfo("America/New_York"))
    assert storage.newest_backup()["created"].date() == date(2042, 3, 3)

    monkeypatch.setattr(storage, "app_tz", lambda: ZoneInfo("Pacific/Kiritimati"))
    assert storage.newest_backup()["created"].date() == date(2042, 3, 4)


def test_two_backups_inside_a_dst_fallback_are_ordered_by_instant(client,
                                                                   backups_dir,
                                                                   monkeypatch):
    """Aware datetimes sharing one tzinfo compare by WALL time, by documented
    rule — so the hour that repeats is the hour this ordering could invert.

    01:15 after a fall-back is a later instant than the 01:30 before it, and
    both normalize into the same ZoneInfo. Comparing the datetimes would
    silently pick the earlier set, at the wrong size and on the wrong date, in
    exactly the hour nobody is checking the panel against a stopwatch.
    """
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(storage, "app_tz", lambda: ZoneInfo("America/New_York"))
    for name, moment in (("activity-2042-11-02-013000.manifest.json",
                          "2042-11-02T01:30:00-04:00"),   # earlier instant
                         ("activity-2042-11-02-011500.manifest.json",
                          "2042-11-02T01:15:00-05:00")):  # later instant
        (backups_dir / name).write_text(json.dumps({
            "manifest_version": storage.MANIFEST_VERSION,
            "created_at": moment,
            "files": {"database": {"name": "a.sqlite", "bytes": 1},
                      "instance": {"name": "b.tar.gz", "bytes": 1}}}),
            encoding="utf-8")

    found = storage.newest_backup()
    assert found["name"] == "activity-2042-11-02-011500.manifest.json"


def test_a_naive_timestamp_does_not_break_the_comparison(client, backups_dir):
    """One manifest written without an offset must not make the panel raise.

    Aware and naive datetimes are not orderable, and every candidate is now
    compared rather than short-circuited at the first name — so a single
    offset-less `created_at` would be a TypeError out of a page render.
    """
    _write_manifest(backups_dir, date.fromisoformat(today_str()) - timedelta(days=1),
                    stamp="2039-01-01-010000")
    (backups_dir / "activity-2039-01-01-020000.manifest.json").write_text(
        json.dumps({"manifest_version": storage.MANIFEST_VERSION,
                    "created_at": f"{today_str()}T02:00:00",   # no offset
                    "files": {"database": {"name": "a.sqlite", "bytes": 1},
                              "instance": {"name": "b.tar.gz", "bytes": 1}}}),
        encoding="utf-8")

    found = storage.newest_backup()
    assert found is not None
    assert found["name"] == "activity-2039-01-01-020000.manifest.json"


def test_an_offset_less_timestamp_is_read_in_the_ledgers_zone(client, backups_dir,
                                                              monkeypatch,
                                                              host_zone):
    """A `created_at` with no offset is a wall clock, and the panel must read it
    in the calendar it dates everything else in.

    `astimezone()` on a naive datetime supplies the HOST's zone before
    converting, so 00:30 under an America/New_York ledger taken on a Moscow
    machine would be dated the previous day — the age off by one, the stale
    warning early or late, on exactly the input the writer's own offset does not
    cover. Asserted against two different host zones, because a rule that only
    holds on this machine is the bug it replaced.
    """
    from zoneinfo import ZoneInfo

    ledger = ZoneInfo("America/New_York")
    monkeypatch.setattr(storage, "app_tz", lambda: ledger)
    _write_boundary_manifest(backups_dir, "2040-01-01T00:30:00")

    for host in ("Europe/Moscow", "Pacific/Kiritimati"):
        host_zone(host)
        created = storage.newest_backup()["created"]
        assert created.date() == date(2040, 1, 1)
        assert created == datetime(2040, 1, 1, 0, 30, tzinfo=ledger)


def test_a_same_second_collision_is_ordered_by_its_number(client, exports_dir):
    """`_claim_name` writes the second export of one second as `-2`, and `-`
    sorts before `.` — so by string order the plain name would look newer than
    the file written after it, and be listed first and evicted last.

    Two exports in one second are not hypothetical: the stamp resolves to the
    second, which is why the suffix exists at all.
    """
    names = ["events-2043-09-09-121212.jsonl",
             "events-2043-09-09-121212-2.jsonl",
             "events-2043-09-09-121212-10.jsonl",
             "events-2043-09-09-121213.jsonl"]
    for name in names:
        (exports_dir / name).write_text("{}\n")

    assert [p.name for p in export.existing_exports()] == [
        "events-2043-09-09-121213.jsonl",       # the later second, first
        "events-2043-09-09-121212-10.jsonl",    # then that second, newest back
        "events-2043-09-09-121212-2.jsonl",
        "events-2043-09-09-121212.jsonl",
    ]


def test_retention_spares_an_export_another_request_is_still_streaming(
        client, exports_dir, monkeypatch):
    """`keep` protects one path. A second, overlapping export is a path this
    call was never told about.

    Two exports inside a backward clock step: each request protects its own
    file, and each other's file sorts near the bottom. The one that prunes
    second would unlink the first one's export while its response is still
    reading it. Recency covers both without either request knowing the other
    exists.
    """
    for n in range(limits.EXPORT_KEEP):
        (exports_dir / f"events-2041-08-08-{n:06d}.jsonl").write_text("{}\n")
    _age(exports_dir)

    conn = _conn()
    try:
        # Request one: written under a rolled-back clock, still streaming.
        monkeypatch.setattr(export, "now_stamp", lambda: "2041-08-07-235958")
        first, _ = export.export_events(conn)
        # Request two, a moment later, also behind the retained set.
        monkeypatch.setattr(export, "now_stamp", lambda: "2041-08-07-235959")
        second, _ = export.export_events(conn)
    finally:
        conn.close()

    assert first.exists(), "the first request's download was deleted under it"
    assert second.exists()


def _write_boundary_manifest(backups_dir, moment: str) -> None:
    (backups_dir / "activity-2999-01-01-000005.manifest.json").write_text(
        json.dumps({"manifest_version": storage.MANIFEST_VERSION,
                    "created_at": moment,
                    "files": {"database": {"name": "a.sqlite", "bytes": 1},
                              "instance": {"name": "b.tar.gz", "bytes": 1}}}),
        encoding="utf-8")


@pytest.mark.parametrize("moment", [
    "0001-01-01T00:00:00+03:00",     # converting to UTC falls off the bottom
    "9999-12-31T23:59:59-11:00",     # and this one off the top
])
def test_a_timestamp_that_cannot_be_normalized_skips_its_manifest(
        client, backups_dir, monkeypatch, moment):
    """Parsing and converting are two chances to fail, and the second one is the
    dangerous one.

    A boundary timestamp parses cleanly and then raises when it is moved into
    the ledger's zone — and because every candidate is compared, one such file
    would 500 every GET /export while a perfectly good older set sat behind it.
    So "cannot be normalized" is decided once, where "cannot be parsed" is.

    The zone is pinned, because which values overflow depends on which
    direction the conversion moves them; the behaviour under test does not.
    """
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(storage, "app_tz", lambda: ZoneInfo("UTC"))
    good = date.fromisoformat(today_str()) - timedelta(days=1)
    _write_manifest(backups_dir, good, stamp="2000-01-01-000001")
    _write_boundary_manifest(backups_dir, moment)

    found = storage.newest_backup()
    assert found is not None
    assert found["name"] == "activity-2000-01-01-000001.manifest.json"
    assert client.get("/export").status_code == 200


@pytest.mark.parametrize("moment", ["0001-01-01T00:00:00",
                                    "9999-12-31T23:59:59.999999"])
def test_a_boundary_timestamp_never_reaches_the_page_as_an_error(
        client, backups_dir, moment):
    """Whether a naive boundary value survives conversion depends on which zone
    the host is in. Whether the page survives it must not.

    So this asserts the invariant rather than the outcome: read it, render it,
    and never 500 — on the machine this runs on and on the one it doesn't.
    """
    _write_manifest(backups_dir, date.fromisoformat(today_str()) - timedelta(days=1),
                    stamp="2000-01-01-000001")
    _write_boundary_manifest(backups_dir, moment)

    storage.newest_backup()   # must not raise
    assert client.get("/export").status_code == 200


def test_an_export_pruned_mid_render_does_not_sink_the_page(client, exports_dir,
                                                            monkeypatch):
    """Retention deletes now, so a GET listing the directory can race a POST
    pruning it. The reader loses one line, not the page."""
    for n in range(3):
        (exports_dir / f"events-2040-07-07-{n:06d}.jsonl").write_text("{}\n")
    vanishing = exports_dir / "events-2040-07-07-000001.jsonl"

    real_stat = Path.stat

    def stat_after_a_prune(self, *args, **kwargs):
        if self == vanishing:
            vanishing.unlink(missing_ok=True)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_after_a_prune)
    names = [item["name"] for item in export.recent_exports()]
    assert names == ["events-2040-07-07-000002.jsonl", "events-2040-07-07-000000.jsonl"]


def test_the_panel_accepts_exactly_what_the_restore_tooling_accepts(tmp_path):
    """A tripwire, not behaviour: the panel spells the version and the member
    roles rather than importing them, because app/ must not depend on scripts/.
    If the backup format is bumped and this is not, the panel starts calling
    every new backup unreadable — the loudest possible way to be wrong, but
    only if it is caught here rather than on the page.
    """
    import scripts.backup_db as backup_db

    assert storage.MANIFEST_VERSION == backup_db.MANIFEST_VERSION

    # The roles are not a constant over there, they are the loop in
    # load_manifest — so prove the agreement by handing it a manifest that
    # names exactly the members this module requires, and one that does not.
    _write_manifest(tmp_path, date.fromisoformat(today_str()),
                    stamp="2000-01-01-000001")
    manifest_path = tmp_path / "activity-2000-01-01-000001.manifest.json"
    assert backup_db.load_manifest(manifest_path)
    assert storage._read_manifest(manifest_path) is not None

    for role in storage._REQUIRED_MEMBERS:
        body = json.loads(manifest_path.read_text(encoding="utf-8"))
        del body["files"][role]
        thinned = tmp_path / f"activity-2000-01-01-00000{role[0]}.manifest.json"
        thinned.write_text(json.dumps(body), encoding="utf-8")
        with pytest.raises(backup_db.BackupError):
            backup_db.load_manifest(thinned)
        assert storage._read_manifest(thinned) is None, role


@pytest.mark.parametrize("version", [2, "1", None, 0])
def test_a_manifest_from_an_unsupported_version_is_not_reported_as_a_backup(
        client, backups_dir, version):
    """The panel answers "is there a backup I could restore?", and
    `backup_db.load_manifest()` refuses every version but its own.

    So a newer set the bundled tooling would decline must not be shown as the
    newest backup: it would silence the missing-backup warning and hide the
    older set that actually restores — telling the user they are covered on the
    one day they are not.
    """
    good = date.fromisoformat(today_str()) - timedelta(days=1)
    _write_manifest(backups_dir, good, stamp="2000-01-01-000001")
    body = {"created_at": "2999-01-01T00:00:03+03:00", "files": {}}
    if version is not None:
        body["manifest_version"] = version
    (backups_dir / "activity-2999-01-01-000003.manifest.json").write_text(
        json.dumps(body), encoding="utf-8")

    found = storage.newest_backup()
    assert found is not None
    assert found["name"] == "activity-2000-01-01-000001.manifest.json"


def test_low_free_space_is_warned_about(client, backups_dir, monkeypatch):
    monkeypatch.setattr(storage, "free_space", lambda: limits.FREE_SPACE_FLOOR - 1)
    _write_manifest(backups_dir, date.fromisoformat(today_str()))
    conn = _conn()
    try:
        state = storage.status(conn)
    finally:
        conn.close()
    assert any("free on the data volume" in w for w in state["warnings"])


def test_the_space_a_backup_needs_grows_with_the_database(client, backups_dir,
                                                          monkeypatch):
    """A fixed floor cannot answer "will the next backup fit?".

    The ledger and the instance files have no bound, and a backup stages a copy
    of both on the same volume. Three gigabytes of database with two free is a
    backup that cannot finish, while a 1 GB floor reports all clear — the one
    reading that matters, wrong in the reassuring direction.
    """
    three_gb = 3 * 1024 ** 3
    monkeypatch.setattr(storage, "_database_bytes", lambda: three_gb)
    monkeypatch.setattr(storage, "free_space", lambda: 2 * 1024 ** 3)
    _write_manifest(backups_dir, date.fromisoformat(today_str()))

    conn = _conn()
    try:
        state = storage.status(conn)
    finally:
        conn.close()
    assert any("free on the data volume" in w for w in state["warnings"])

    # And the floor still applies to a small instance, where the measured need
    # is a rounding error next to the room a backup should have.
    assert storage._space_a_backup_needs(1024, None) == limits.FREE_SPACE_FLOOR
    assert storage._space_a_backup_needs(three_gb, None) == three_gb


def test_the_estimate_adds_the_old_archive_to_the_live_database(client,
                                                                backups_dir):
    """The two halves are measured at different moments, and must be.

    The database is the live one, because that is the copy the next backup
    stages. What sat beside it can only be read off the last manifest.
    Deriving the archive by subtracting today's database from the old total
    would cancel exactly the growth this warning exists to notice: a ledger
    that went from 1 GB to 20 GB beside a 10 GB archive would be reported as
    needing 20 GB rather than 30, and 25 GB free would look fine.
    """
    gb = 1024 ** 3
    _write_manifest(backups_dir, date.fromisoformat(today_str()),
                    db_bytes=1 * gb, files_bytes=10 * gb)
    backup = storage.newest_backup()
    assert backup["beside_database"] >= 10 * gb   # the manifest's own size too

    needed = storage._space_a_backup_needs(20 * gb, backup)
    assert needed >= 30 * gb, "the old database size must not stand in for today's"


def test_the_panel_writes_nothing(client, backups_dir, exports_dir):
    """It is reached by a GET, and every GET in this app is side-effect-free by
    contract — app/security.py leaves safe methods outside the write guard on
    exactly that promise."""
    conn = _conn()
    try:
        before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        storage.status(conn)
        client.get("/export")
        after = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()
    assert before == after
    assert list(backups_dir.iterdir()) == []
    assert list(exports_dir.iterdir()) == []
