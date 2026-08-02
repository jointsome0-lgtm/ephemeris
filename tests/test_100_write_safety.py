"""Concurrent-write safety and the archived-list write gate (#22).

Desktop and phone reach the same SQLite file, and until this slice every
read-modify-write here ran its read outside the writer lock: sqlite3's legacy
mode emits a DEFERRED `BEGIN` before the first DML and never before a `SELECT`.
Two devices could therefore both read "not skipped yet" and both write the whole
`exdates` document back, and the second write silently erased the first.

Nothing below spawns a thread. A race proved by racing is a race proved
sometimes; these cases interleave the two connections by hand, at the exact
instruction where the old code left its window open, so the outcome is the same
on every run and on every machine.
"""
from __future__ import annotations

import sqlite3

import pytest

# A far-future daily series, so nothing an earlier file in the cumulative suite
# collides with these dates and every date below is an occurrence.
SERIES_START = "2029-03-05"
SKIP_A = "2029-03-12"
SKIP_B = "2029-03-19"


def _gc():
    from app.db import get_conn

    return get_conn()


def _impatient(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Same connection, minus the five-second wait for a busy writer lock.

    The production timeout is right for production — a real second device
    should wait rather than fail. In a test it would only mean five idle
    seconds before the assertion that the block happened.
    """
    conn.execute("PRAGMA busy_timeout = 20")
    return conn


def _statements(conn: sqlite3.Connection, sink: list[str]):
    """Record every statement `conn` executes into `sink`."""
    conn.set_trace_callback(sink.append)
    return conn


# --- the lost EXDATE ------------------------------------------------------


def test_concurrent_skip_keeps_both_exdates(client):
    """`skip_occurrence` rewrites the whole `exdates` JSON from what it read.

    The old window: writer B reads the document, writer A commits its own skip,
    B writes its version back. B's document never mentioned A's date, so A's
    skip is gone with no error anywhere. The interleave is injected exactly
    there — inside B, immediately after B's read returns.
    """
    from app.services import calendar_events as cal

    a, b = _impatient(_gc()), _gc()
    try:
        event_id = cal.create_event(b, "#22 Concurrency Drill", start_date=SERIES_START,
                                    freq="daily", all_day=True)

        real_get_event = cal.get_event
        outcome: dict[str, str] = {}
        armed = [True]

        def get_event_then_let_a_run(conn, ev_id):
            row = real_get_event(conn, ev_id)
            if armed[0]:
                # Disarm first: A's own skip re-enters this patched name, and on
                # the unfixed code it gets far enough to do so.
                armed[0] = False
                # B has now read the document and has not yet written it back.
                try:
                    cal.skip_occurrence(a, ev_id, SKIP_A)
                    outcome["a"] = "committed"
                except sqlite3.OperationalError:
                    outcome["a"] = "blocked"
            return row

        cal.get_event = get_event_then_let_a_run
        try:
            cal.skip_occurrence(b, event_id, SKIP_B)
        finally:
            cal.get_event = real_get_event

        # The whole point: B's read is inside B's write transaction, so A never
        # gets between the read and the write. A is told it lost the lock,
        # which is an answer it can act on — unlike silence.
        assert outcome["a"] == "blocked", (
            "a competing skip is refused, not slipped between read and write"
            + "  -- " + outcome.get("a", "never ran")
        )

        after_b = set(cal.exdates_of(real_get_event(b, event_id)))
        assert after_b == {SKIP_B}, (
            "B's own skip committed" + "  -- " + str(sorted(after_b))
        )

        # A retries now that the lock is free, and keeps what B wrote.
        cal.skip_occurrence(a, event_id, SKIP_A)
        both = set(cal.exdates_of(real_get_event(_gc(), event_id)))
        assert both == {SKIP_A, SKIP_B}, (
            "the retried skip adds to the committed one instead of replacing it"
            + "  -- " + str(sorted(both))
        )
    finally:
        a.close()
        b.close()


# --- the lock is really taken --------------------------------------------


def test_protected_paths_issue_begin_immediate(client):
    """Cheap insurance against a later "simplification" back to `with conn:`.

    Each of these three paths decides its write from a value it read, so each
    must open with `BEGIN IMMEDIATE`. The trace callback sees the statements
    SQLite actually runs, so this cannot be satisfied by a comment.
    """
    from app.services import calendar_events as cal
    from app.services import tasks as tasks_svc

    conn = _gc()
    try:
        task_id = tasks_svc.create_task(conn, "#22 Lock Witness")
        event_id = cal.create_event(conn, "#22 Lock Witness Series",
                                    start_date=SERIES_START, freq="daily", all_day=True)

        for label, call in (
            ("toggle_complete", lambda: tasks_svc.toggle_complete(conn, task_id)),
            ("skip_occurrence", lambda: cal.skip_occurrence(conn, event_id, SKIP_A)),
            ("unskip_occurrence", lambda: cal.unskip_occurrence(conn, event_id, SKIP_A)),
        ):
            seen: list[str] = []
            _statements(conn, seen)
            try:
                call()
            finally:
                conn.set_trace_callback(None)
            assert any(s.strip().upper().startswith("BEGIN IMMEDIATE") for s in seen), (
                f"{label} opens its transaction with BEGIN IMMEDIATE"
                + "  -- " + str(seen)
            )
    finally:
        conn.close()


def test_immediate_refuses_to_nest_and_rolls_back(client):
    """The two ways to hold `immediate()` wrong, both of them silent failures.

    Nesting would raise SQLite's own "cannot start a transaction within a
    transaction", which names nothing the caller can act on; and a body that
    raises must leave nothing behind.
    """
    from app.db import immediate

    conn = _gc()
    try:
        with immediate(conn):
            with pytest.raises(RuntimeError, match="already in a transaction"):
                with immediate(conn):
                    pass

        with pytest.raises(ZeroDivisionError):
            with immediate(conn):
                conn.execute(
                    "INSERT INTO lists (name, emoji, kind, sort_order, created_at) "
                    "VALUES ('#22 Rollback Witness', '•', 'list', 9990, '2029-03-05T00:00:00')"
                )
                1 / 0

        assert not conn.in_transaction, "a failed body leaves no open transaction"
        left = conn.execute(
            "SELECT COUNT(*) FROM lists WHERE name = '#22 Rollback Witness'"
        ).fetchone()[0]
        assert left == 0, "a failed body is rolled back" + "  -- " + str(left)
    finally:
        conn.close()


# --- sort_order ------------------------------------------------------------


def _create_with_a_rival(table: str, make, mine: sqlite3.Connection,
                         rival: sqlite3.Connection, seen: list[str]) -> tuple[int, int]:
    """Run `make(mine)` with a competing `make(rival)` landing mid-creation.

    The injection point is the trace callback for the INSERT: the statement is
    about to run and has therefore taken no lock yet, which is exactly the
    window the old two-statement version left open between reading
    `MAX(sort_order)` and using it. The rival commits inside that window.
    """
    state: dict[str, object] = {"armed": True, "rival_id": None, "rival_error": None}

    def inject(sql: str) -> None:
        seen.append(sql)
        if state["armed"] and sql.strip().upper().startswith(f"INSERT INTO {table.upper()} "):
            state["armed"] = False
            try:
                state["rival_id"] = make(rival)
            except Exception as exc:  # sqlite3 swallows callback exceptions
                state["rival_error"] = exc

    mine.set_trace_callback(inject)
    try:
        my_id = make(mine)
    finally:
        mine.set_trace_callback(None)
    assert state["rival_error"] is None, (
        f"the rival {table} creation itself succeeded"
        + "  -- " + repr(state["rival_error"])
    )
    assert state["rival_id"] is not None, f"the rival {table} creation was injected"
    return state["rival_id"], my_id


def test_sort_order_is_computed_inside_the_insert(client):
    """`MAX(sort_order) + 10` never reaches Python, so it cannot go stale.

    Two creations against the same target used to read the same MAX and write
    the same number. Folding the read into the INSERT closes the window without
    a transaction at all: one statement already runs under the lock it takes.
    """
    from app.services import items as items_svc
    from app.services import lists as lists_svc
    from app.services import tasks as tasks_svc

    conn, rival = _gc(), _gc()
    try:
        target_list = lists_svc.create_list(conn, "#22 Sort Order Target", emoji="•")
        group = "#22 Sort Order Group"
        counter = iter(range(100))

        makers = {
            "tasks": lambda c: tasks_svc.create_task(
                c, f"#22 Ordered {next(counter)}", list_id=target_list),
            "lists": lambda c: lists_svc.create_list(
                c, f"#22 Ordered List {next(counter)}", emoji="•"),
            "routine_items": lambda c: items_svc.create_item(
                c, f"#22 Ordered Habit {next(counter)}", group),
        }

        seen: list[str] = []
        made = {table: _create_with_a_rival(table, make, conn, rival, seen)
                for table, make in makers.items()}

        for table, ids in made.items():
            orders = [conn.execute(f"SELECT sort_order FROM {table} WHERE id = ?", (i,)).fetchone()[0]
                      for i in ids]
            assert len(set(orders)) == len(orders), (
                f"a {table} row created against a rival still gets its own sort_order"
                + "  -- " + str(orders)
            )

        standalone = [s for s in seen
                      if s.strip().upper().startswith("SELECT") and "MAX(SORT_ORDER)" in s.upper()]
        assert standalone == [], (
            "no sort_order is read into Python ahead of its INSERT"
            + "  -- " + str(standalone)
        )

        # The habit's creation event still reports the number the row got — it
        # is read back by id now instead of being computed in advance.
        payload = conn.execute(
            "SELECT payload_json FROM events WHERE type = 'routine_item_created' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        row_order = conn.execute(
            "SELECT sort_order FROM routine_items WHERE id = ?", (made["routine_items"][-1],)
        ).fetchone()[0]
        assert f'"sort_order": {row_order}' in payload, (
            "routine_item_created reports the sort_order the row actually holds"
            + "  -- " + payload
        )
    finally:
        conn.close()
        rival.close()


# --- archived lists --------------------------------------------------------


def _archive(conn: sqlite3.Connection, list_id: int) -> int:
    """Archive an existing list.

    There is no route that archives a list yet — the column and the archived
    read paths are older than any UI for them. The write gate is what makes a
    later archive action safe to add, so the state is set up directly.
    """
    with conn:
        conn.execute("UPDATE lists SET archived_at = ? WHERE id = ?",
                     ("2029-03-05T00:00:00+03:00", list_id))
    return list_id


def _archived_list_id(conn: sqlite3.Connection, name: str = "#22 Archived Target") -> int:
    """A list that exists and is archived."""
    from app.services import lists as lists_svc

    return _archive(conn, lists_svc.create_list(conn, name, emoji="•"))


def test_services_refuse_to_file_into_an_archived_list(client):
    from app.services import calendar_events as cal
    from app.services import lists as lists_svc
    from app.services import tasks as tasks_svc

    conn = _gc()
    try:
        archived = _archived_list_id(conn)

        # The read path is deliberately unchanged: an archived list still
        # answers, or its own archived view could not name it.
        assert lists_svc.get_list(conn, archived) is not None, (
            "get_list still answers for an archived list"
        )
        with pytest.raises(lists_svc.ListError, match="archived"):
            lists_svc.require_writable_list(conn, archived)
        with pytest.raises(lists_svc.ListError, match="unknown list"):
            lists_svc.require_writable_list(conn, 10_000_019)

        with pytest.raises(tasks_svc.TaskError, match="archived"):
            tasks_svc.create_task(conn, "#22 Stale Form Task", list_id=archived)
        with pytest.raises(cal.CalendarEventError, match="archived"):
            cal.create_event(conn, "#22 Stale Form Event", start_date=SERIES_START,
                             all_day=True, list_id=archived)

        # Moving an existing task in is the same write, from the edit form.
        movable = tasks_svc.create_task(conn, "#22 Movable Task")
        with pytest.raises(tasks_svc.TaskError, match="archived"):
            tasks_svc.update_task(conn, movable, title="#22 Movable Task", list_id=archived)

        # Nothing landed.
        stranded = conn.execute(
            "SELECT (SELECT COUNT(*) FROM tasks WHERE list_id = ?) "
            "     + (SELECT COUNT(*) FROM calendar_events WHERE list_id = ?)",
            (archived, archived),
        ).fetchone()[0]
        assert stranded == 0, (
            "no row is filed under the archived list" + "  -- " + str(stranded)
        )
    finally:
        conn.close()


def test_rows_already_in_a_list_stay_editable_after_it_is_archived(client):
    """The gate covers filing INTO a list, not living in one.

    Archiving a list does not archive what it holds: those tasks and events stay
    active and stay listed. Every edit path refills the columns the caller did
    not supply from the current row, `list_id` among them, so a gate that fired
    on an unchanged association would strand each of them at its next title
    edit, skip or drag.
    """
    from app.services import calendar_events as cal
    from app.services import lists as lists_svc
    from app.services import tasks as tasks_svc

    conn = _gc()
    try:
        home = lists_svc.create_list(conn, "#22 Archived Later", emoji="•")
        task_id = tasks_svc.create_task(conn, "#22 Resident Task", list_id=home)
        event_id = cal.create_event(conn, "#22 Resident Event", start_date=SERIES_START,
                                    freq="daily", all_day=True, list_id=home)
        # A one-off event is what the calendar's drag-and-drop moves.
        once_id = cal.create_event(conn, "#22 Resident Drag", start_date=SERIES_START,
                                   freq="once", all_day=True, list_id=home)
        _archive(conn, home)

        tasks_svc.update_task(conn, task_id, title="#22 Resident Task, renamed")
        assert tasks_svc.toggle_complete(conn, task_id) is True, "a resident task still completes"

        cal.update_event(conn, event_id, title="#22 Resident Event, renamed")
        cal.skip_occurrence(conn, event_id, SKIP_A)
        assert cal.exdates_of(cal.get_event(conn, event_id)) == [SKIP_A], (
            "a resident event still takes a skip"
        )

        cal.move_event(conn, once_id, "2029-03-21")
        assert cal.get_event(conn, once_id)["start_date"] == "2029-03-21", (
            "a resident event still drags to another day"
        )

        # Moving OUT of the archived list is still a write into the target, so
        # the target is what gets checked — not the list being left.
        elsewhere = lists_svc.create_list(conn, "#22 Rescue List", emoji="•")
        tasks_svc.update_task(conn, task_id, title="#22 Resident Task, moved",
                              list_id=elsewhere)
        assert tasks_svc.get_task(conn, task_id)["list_id"] == elsewhere, (
            "a task moves out of an archived list"
        )
        cal.update_event(conn, event_id, list_id=elsewhere)
        assert cal.get_event(conn, event_id)["list_id"] == elsewhere, (
            "an event moves out of an archived list"
        )
    finally:
        conn.close()


def test_routes_answer_an_archived_list_exactly_as_a_missing_one(client):
    """The refusal must arrive as the app's existing "bad list" answer.

    A 500 would be a worse bug than the one being fixed, so each surface is
    compared against the answer it already gives for a list id that was never
    real — same status, same destination, only the flash text differs.
    """
    conn = _gc()
    try:
        archived = _archived_list_id(conn)
    finally:
        conn.close()
    missing = 10_000_019

    def shape(response):
        location = response.headers.get("location", "")
        return response.status_code, location.split("?")[0]

    for path, payload in (
        ("/tasks", {"title": "#22 Stale Form", "return_to": "/today"}),
        ("/calendar/events", {"title": "#22 Stale Form", "start_date": SERIES_START,
                              "freq": "once", "interval_n": "1", "return_to": "/calendar"}),
    ):
        answers = {}
        for label, list_id in (("archived", archived), ("missing", missing)):
            r = client.post(path, data={**payload, "list_id": str(list_id)},
                            follow_redirects=False)
            answers[label] = (shape(r), r.headers.get("location", ""))
        assert answers["archived"][0] == answers["missing"][0], (
            f"POST {path} answers an archived list as it answers a missing one"
            + "  -- " + str(answers)
        )
        assert "flash=" in answers["archived"][1], (
            f"POST {path} explains the refusal" + "  -- " + answers["archived"][1]
        )

    # The JSON quick-add path has its own branch; it must refuse the same way.
    json_answers = {}
    for label, list_id in (("archived", archived), ("missing", missing)):
        r = client.post("/tasks", data={"title": "#22 Stale Form", "list_id": str(list_id),
                                        "return_to": "/today"},
                        headers={"x-partial": "1"}, follow_redirects=False)
        json_answers[label] = (r.status_code, r.json().get("ok"))
    assert json_answers["archived"] == json_answers["missing"] == (422, False), (
        "the quick-add JSON branch refuses both the same way"
        + "  -- " + str(json_answers)
    )
