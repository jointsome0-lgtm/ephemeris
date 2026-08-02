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
import sqlite3
from datetime import date, timedelta
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


def test_the_ceiling_cannot_be_set_below_the_largest_route_cap(monkeypatch):
    """An override may raise the perimeter, never lower it past the route caps.

    A ceiling under a Learn endpoint's own cap tightens nothing — that route
    already bounds itself — but it does start answering perfectly valid saves
    with this middleware's plain-text 413 instead of the typed JSON the lesson
    agent parses. So the setting is declined and the default stands.
    """
    for too_small in (1024, limits.LARGEST_ROUTE_CAP - 1):
        monkeypatch.setenv("EPHEMERIS_MAX_BODY_BYTES", str(too_small))
        assert security._body_ceiling() == limits.MAX_BODY_BYTES, too_small
    # Exactly at the largest cap is allowed: the route's own counter trips
    # first at that size, so nothing it accepts is refused out here.
    monkeypatch.setenv("EPHEMERIS_MAX_BODY_BYTES", str(limits.LARGEST_ROUTE_CAP))
    assert security._body_ceiling() == limits.LARGEST_ROUTE_CAP


# --- 3. export retention ---------------------------------------------------


@pytest.fixture
def exports_dir(tmp_path, monkeypatch):
    """Redirect exports at the service module, which binds EXPORTS_DIR on import."""
    target = tmp_path / "exports"
    target.mkdir()
    monkeypatch.setattr(export, "EXPORTS_DIR", target)
    return target


def test_retention_keeps_only_the_newest_thirty(client, exports_dir, monkeypatch):
    """Thirty-five exports in, thirty out — and the thirty that survive are the
    newest, not an arbitrary thirty."""
    conn = _conn()
    try:
        for n in range(35):
            monkeypatch.setattr(export, "now_stamp", lambda n=n: f"2033-01-01-{n:06d}")
            export.export_events(conn)
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

    removed = export.prune_exports()

    assert staged.exists()
    assert staged not in removed
    assert len(export.existing_exports()) == limits.EXPORT_KEEP


def test_retention_survives_a_file_it_cannot_remove(client, exports_dir, monkeypatch):
    """Housekeeping must not sink an export that already succeeded: the bytes
    are on disk and named, and the caller is owed the path."""
    for n in range(limits.EXPORT_KEEP + 2):
        (exports_dir / f"events-2036-04-04-{n:06d}.jsonl").write_text("{}\n")

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


def _write_manifest(directory, when: date, *, stamp: str | None = None,
                    db_bytes: int = 4096, files_bytes: int = 2048) -> None:
    """A manifest in the shape scripts/backup_db.py publishes."""
    stamp = stamp or f"{when.isoformat()}-120000"
    (directory / f"activity-{stamp}.manifest.json").write_text(json.dumps({
        "manifest_version": 1,
        "created_at": f"{when.isoformat()}T12:00:00+03:00",
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


def test_a_manifest_that_names_no_files_still_dates_the_set(client, backups_dir):
    """Absent `files` is not malformed — an older writer that listed no members
    still answers "when was the last backup?", which is the panel's question."""
    (backups_dir / "activity-2001-02-03-040506.manifest.json").write_text(
        json.dumps({"created_at": "2001-02-03T04:05:06+03:00"}), encoding="utf-8")

    found = storage.newest_backup()
    assert found is not None
    assert found["bytes"] > 0   # the manifest's own size, nothing claimed


def test_low_free_space_is_warned_about(client, backups_dir, monkeypatch):
    monkeypatch.setattr(storage, "free_space", lambda: limits.FREE_SPACE_FLOOR - 1)
    _write_manifest(backups_dir, date.fromisoformat(today_str()))
    conn = _conn()
    try:
        state = storage.status(conn)
    finally:
        conn.close()
    assert any("free on the data volume" in w for w in state["warnings"])


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
