"""The Tasks kanban board (#53): the status column, its invariant, and /board.

Tasks had no notion of being in flight — a task was open or completed, and the
board's middle column had nowhere to live. Schema v18 adds `status`, bound to
the completion timestamp by one rule: status='done' ⇔ completed_at IS NOT NULL.
These cases pin that rule from every writer that can touch it (the checkbox,
the arrow forms, the drag handler's JSON call, the migration's backfill), pin
that the board is reachable and complete without JavaScript, and pin the guard
that keeps a merged template from linking a route the live process has not
restarted into yet.

Runs late in the cumulative suite because it creates and completes tasks, which
the earlier files' counts would otherwise have to absorb.
"""
from __future__ import annotations

import re

from conftest import events_of


def _mk_task(title: str, **kwargs) -> int:
    from app.db import get_conn
    from app.services import tasks

    conn = get_conn()
    try:
        return tasks.create_task(conn, title, **kwargs)
    finally:
        conn.close()


def _row(task_id: int):
    from app.db import get_conn

    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    finally:
        conn.close()


def _card(html: str, task_id: int) -> str:
    """The markup of one board card, as rendered."""
    start = html.find(f'id="task-{task_id}"')
    assert start != -1, f"task {task_id} has a card on the board"
    end = html.find("</article>", start)
    return html[start:end]


def _column_of(html: str, task_id: int) -> str:
    """Which column's section the card sits in."""
    card = html.find(f'id="task-{task_id}"')
    assert card != -1, f"task {task_id} has a card on the board"
    heads = [m for m in re.finditer(r'<section class="bcol" data-status="(\w+)"', html)]
    here = [m for m in heads if m.start() < card]
    assert here, "a card is always inside a column"
    return here[-1].group(1)


# --- the column and its invariant ------------------------------------------


def test_schema_v18_adds_the_status_column(client):
    from app.db import SCHEMA_VERSION, get_conn

    assert SCHEMA_VERSION >= 18, "the board's column landed in schema v18"
    conn = get_conn()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION, (
            "the test database is migrated to the running schema"
        )
        cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(tasks)")}
        assert "status" in cols, "tasks carries a status"
        assert cols["status"]["dflt_value"] == "'backlog'", (
            "a task starts in Backlog without anyone saying so"
        )
        # the CHECK is the last line of defence under every writer above it
        bad = None
        try:
            with conn:
                conn.execute(
                    "INSERT INTO tasks (title, status, created_at) VALUES ('x','shipped','t')"
                )
        except Exception as exc:  # sqlite3.IntegrityError
            bad = str(exc)
        assert bad and "CHECK" in bad.upper(), "an unknown column is refused by the schema"
        skewed = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE (completed_at IS NOT NULL AND status <> 'done') "
            "OR (completed_at IS NULL AND status = 'done')"
        ).fetchone()[0]
        assert skewed == 0, "every task the suite has written so far obeys the invariant"
    finally:
        conn.close()


def test_backfill_heals_what_the_pre_board_process_wrote(client):
    """The live service keeps completing tasks with the old code until it is
    restarted, so the boot repairs both directions of the skew it leaves."""
    from app.db import backfill_task_status, get_conn

    done_id = _mk_task("Board heal — completed")
    open_id = _mk_task("Board heal — reopened")
    conn = get_conn()
    try:
        with conn:
            # exactly what a pre-#53 complete and reopen leave behind
            conn.execute(
                "UPDATE tasks SET completed_at = '2020-01-01T09:00:00+03:00', "
                "status = 'backlog' WHERE id = ?", (done_id,)
            )
            conn.execute(
                "UPDATE tasks SET completed_at = NULL, status = 'done' WHERE id = ?",
                (open_id,),
            )
            healed = backfill_task_status(conn)
        assert healed == 2, "both skewed rows are repaired"
        with conn:
            again = backfill_task_status(conn)
        assert again == 0, "a consistent table is left alone (idempotent)"
    finally:
        conn.close()
    assert _row(done_id)["status"] == "done", "a completed task lands in Done"
    assert _row(open_id)["status"] == "backlog", "a reopened task goes back to Backlog"


def test_the_checkbox_moves_the_card(client):
    """Completion owns the column, from the one writer that predates it."""
    task_id = _mk_task("Board — checkbox owns Done")
    r = client.post(f"/tasks/{task_id}/complete", data={"return_to": "/board"},
                    follow_redirects=False)
    assert r.status_code == 303, "the complete toggle still answers Mode A"
    row = _row(task_id)
    assert row["status"] == "done" and row["completed_at"], "completing lands it in Done"
    client.post(f"/tasks/{task_id}/complete", data={"return_to": "/board"},
                follow_redirects=False)
    row = _row(task_id)
    assert row["status"] == "backlog" and row["completed_at"] is None, (
        "reopening returns it to Backlog"
    )


# --- the write route --------------------------------------------------------


def test_status_route_moves_a_task_through_the_columns(client):
    task_id = _mk_task("Board — three columns")
    completed_before = len(events_of("task_completed"))
    reopened_before = len(events_of("task_reopened"))
    moves_before = len(events_of("task_status_changed"))

    r = client.post(f"/tasks/{task_id}/status", data={"status": "doing", "return_to": "/board"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/board", (
        "Mode A returns to the board"
    )
    row = _row(task_id)
    assert row["status"] == "doing" and row["completed_at"] is None, (
        "in flight is not finished"
    )

    r = client.post(f"/tasks/{task_id}/status", data={"status": "done"},
                    headers={"X-Partial": "1"})
    assert r.status_code == 200 and r.json() == {
        "ok": True, "task_id": task_id, "status": "done", "completed": True
    }, "Mode B tells the drag handler what happened"
    row = _row(task_id)
    assert row["status"] == "done" and row["completed_at"], "Done completes the task"

    r = client.post(f"/tasks/{task_id}/status", data={"status": "backlog"},
                    headers={"X-Partial": "1"})
    assert r.json()["completed"] is False, "leaving Done reopens the task"
    assert _row(task_id)["completed_at"] is None, "and clears the timestamp"

    assert len(events_of("task_completed")) == completed_before + 1, (
        "a drag into Done journals the same event the checkbox does"
    )
    assert len(events_of("task_reopened")) == reopened_before + 1, (
        "and dragging out journals the reopen"
    )
    assert len(events_of("task_status_changed")) == moves_before + 3, (
        "each column move is journalled in its own right"
    )


def test_status_route_refuses_a_column_that_does_not_exist(client):
    task_id = _mk_task("Board — bad column")
    r = client.post(f"/tasks/{task_id}/status", data={"status": "shipped"},
                    headers={"X-Partial": "1"})
    assert r.status_code == 422 and r.json()["ok"] is False, "Mode B rejects it"
    r = client.post(f"/tasks/{task_id}/status", data={"status": "shipped", "return_to": "/board"},
                    follow_redirects=False)
    assert r.status_code == 303 and "flash=" in r.headers["location"], (
        "Mode A says so in a flash instead of a stack trace"
    )
    assert _row(task_id)["status"] == "backlog", "and nothing moved"

    r = client.post("/tasks/999999/status", data={"status": "doing"},
                    headers={"X-Partial": "1"})
    assert r.status_code == 422, "an unknown task is refused the same way"


# --- the page ---------------------------------------------------------------


def test_board_renders_the_three_columns(client):
    task_id = _mk_task("Board — render me")
    r = client.get("/board")
    assert r.status_code == 200, "GET /board 200"
    html = r.text
    for status, title in (("backlog", "Backlog"), ("doing", "Doing"), ("done", "Done")):
        assert f'data-status="{status}"' in html and title in html, (
            f"the {title} column is on the page"
        )
    assert 'action="/tasks"' in html, "quick-add survives on the board"
    assert _column_of(html, task_id) == "backlog", "a new task waits in Backlog"
    assert f'href="?sel=task-{task_id}"' in html, "a card opens the detail pane"
    r = client.get(f"/board?sel=task-{task_id}")
    assert 'action="/tasks/%d/update"' % task_id in r.text, (
        "the shared task detail pane renders on the board too"
    )


def test_quick_add_on_the_board_lands_in_backlog(client):
    r = client.post("/tasks", data={"title": "Board quick-add", "return_to": "/board"},
                    follow_redirects=False)
    assert r.status_code == 303, "POST /tasks 303"
    from app.db import get_conn

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM tasks WHERE title = 'Board quick-add'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row["status"] == "backlog", "quick-add fills the Backlog"


def test_the_board_is_usable_without_javascript(client):
    """Every move the drag handler makes has a plain form behind it."""
    task_id = _mk_task("Board — no JS")
    card = _card(client.get("/board").text, task_id)
    assert f'action="/tasks/{task_id}/status"' in card, "the card posts its own status"
    values = re.findall(r'name="status" value="(\w*)"', card)
    assert values == ["", "doing"], (
        "a Backlog card offers only the move it has: rightwards into Doing"
    )
    # submit exactly what that form carries
    r = client.post(f"/tasks/{task_id}/status", data={"status": "doing", "return_to": "/board"},
                    follow_redirects=False)
    assert r.status_code == 303, "the form's own POST is enough to move the card"
    html = client.get("/board").text
    assert _column_of(html, task_id) == "doing", "and the board shows it there"
    assert re.findall(r'name="status" value="(\w*)"', _card(html, task_id)) == [
        "backlog", "done"
    ], "the middle column offers both directions"


def test_done_column_is_capped(client):
    """The Done column is a receipt of recent work, not the whole archive."""
    from app.db import get_conn
    from app.services import tasks

    conn = get_conn()
    try:
        first = tasks.create_task(conn, "Board — done cap A")
        second = tasks.create_task(conn, "Board — done cap B")
        tasks.set_status(conn, first, "done")
        tasks.set_status(conn, second, "done")
        capped = tasks.board(conn, done_limit=1)
        assert len(capped["done"]) == 1, "the cap is honoured"
        assert capped["done"][0]["id"] == second, "and keeps the newest completion"
        assert all(t["completed_at"] is None for t in capped["backlog"] + capped["doing"]), (
            "no completed task hides in an open column"
        )
    finally:
        conn.close()
    assert f'data-limit="{tasks.DONE_LIMIT}"' in client.get("/board").text, (
        "the cap travels to the page, so the drag handler evicts the same overflow"
    )


def test_the_move_event_is_known_to_the_restore_tool(client):
    """An event type the recovery path does not know is reported as unsupported
    and dropped from its counts, which would make every export written after a
    board move read as damaged."""
    import importlib.util
    import sys

    from conftest import ROOT

    spec = importlib.util.spec_from_file_location(
        "_restore_board_events", ROOT / "scripts" / "restore_from_export.py"
    )
    restore = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = restore  # the @dataclass needs to resolve its module
    spec.loader.exec_module(restore)
    assert "task_status_changed" in restore.PARTIAL_TABLE_EVENTS["tasks"], (
        "a board move is a task event the restore tool counts"
    )
    assert "task_status_changed" in restore.KNOWN_EVENT_TYPES, "and knows"


# --- the live-restart guard -------------------------------------------------


def test_rail_points_at_today_until_the_board_route_is_running(client):
    """A merged template renders in the OLD process before the restart, and its
    routers have no /board. The rail link is a Jinja global for exactly that
    reason: absent, it falls back to the route that process does serve."""
    from app.templating import TASKS_HOME, templates

    live = client.get("/board").text
    assert f'href="{TASKS_HOME}"' in live, "the running app points the rail at the board"

    palette = client.get("/palette.json").json()["views"]
    assert [v["href"] for v in palette if v["label"] == "Tasks"] == [TASKS_HOME], (
        "the command palette reaches the same Tasks surface the rail does"
    )

    templates.env.globals.pop("tasks_home")
    try:
        old = client.get("/today").text
        assert "/board" not in old, "nothing links the board before the restart"
        assert 'href="/today" title="Tasks"' in old, "the rail keeps its old destination"
    finally:
        templates.env.globals["tasks_home"] = TASKS_HOME
    assert 'href="/board"' in client.get("/today").text, "and the global is back"
