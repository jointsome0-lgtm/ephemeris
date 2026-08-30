"""Export, restore, re-export: the stream that comes back is the one that went in.

test_110 pins the export envelope and the importer's redelivery rules on a
hand-written stream; test_120 pins the initialization marker on a booted
ledger. This module closes the loop on a source instance written through the
real routes: export it, restore the file into two fresh targets, re-export each
and compare. The source has its own data directory, so every step that opens
an instance runs in a second interpreter (app/settings.py binds
ACTIVITY_DATA_DIR once at import), the way test_120 boots its instances.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import query

ROOT = Path(__file__).resolve().parent.parent

REPLAY_TRANSITIONS = {
    "routine_item_created",
    "routine_item_updated",
    "routine_item_deactivated",
    "routine_item_deleted",
    "routine_checkin_upserted",
    "routine_checkin_cleared",
    "daily_note_updated",
    "calendar_event_created",
    "calendar_event_updated",
    "calendar_occurrence_skipped",
    "task_updated",
}

ID_KEYS = {
    "task_id": "tasks",
    "list_id": "lists",
    "session_id": "focus_sessions",
    "lesson_id": "lessons",
    "habit_id": "routine_items",
}

_SEED = """
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient
from app.db import get_conn
from app.main import app


def id_of(sql):
    conn = get_conn()
    try:
        return conn.execute(sql).fetchone()["id"]
    finally:
        conn.close()


writes = []
with TestClient(app, base_url="http://localhost") as client:
    def post(path, data=None, expected=303):
        response = client.post(path, data=data, follow_redirects=False)
        writes.append([path, response.status_code, expected])

    post("/habits", {"title": "Demo Restore Walk", "group_name": "Demo Routine",
                     "emoji": "🧭", "start_date": "2024-01-01"})
    habit_id = id_of("SELECT id FROM routine_items WHERE title = 'Demo Restore Walk'")
    inbox_id = id_of("SELECT id FROM lists WHERE kind = 'inbox' ORDER BY id LIMIT 1")
    post(f"/habits/{habit_id}/edit", {"title": "Demo Restore Walk Revised",
                                      "group_name": "Demo Routine", "emoji": "🧭",
                                      "start_date": "2024-01-01"})
    post(f"/habits/{habit_id}/archive")
    post(f"/items/{habit_id}/reactivate")
    for status in ("full_done", "full_done", "light_done"):
        post("/checkins", {"date": "2024-01-15", "routine_item_id": habit_id,
                           "status": status})
    post("/checkins", {"date": "2024-01-15", "routine_item_id": habit_id,
                       "note": "Invented roundtrip note"})
    post("/daily-note", {"date": "2024-01-15", "text": "Invented roundtrip daily note"})
    post("/focus/timer/start", {"token": "demo-restore-span", "mode": "open",
                                "habit_id": str(habit_id)}, expected=200)
    time.sleep(1.1)
    post("/focus/timer/finish", {"token": "demo-restore-span"}, expected=200)
    post("/learn/lessons", {"title": "Demo Restore Lesson"})

    post("/habits", {"title": "Demo Disposable Habit", "group_name": "Demo Routine"})
    deleted_id = id_of("SELECT id FROM routine_items WHERE title = 'Demo Disposable Habit'")
    post("/checkins", {"date": "2024-01-16", "routine_item_id": deleted_id,
                       "status": "failed"})
    post(f"/habits/{deleted_id}/delete")

    post("/tasks", {"title": "Demo Partial Task", "list_id": inbox_id,
                    "due_date": "2024-02-01"})
    task_id = id_of("SELECT id FROM tasks WHERE title = 'Demo Partial Task'")
    post(f"/tasks/{task_id}/update", {
        "title": "Demo Partial Task Revised",
        "note": "This field is intentionally absent from task_updated",
        "due_date": "2024-02-02", "priority": "3", "list_id": str(inbox_id),
    })

    series = {"emoji": "🧪", "list_id": str(inbox_id), "freq": "weekly",
              "wd": ["0", "2"], "interval_n": "1",
              "start_date": "2024-01-01", "end_date": "2024-03-31"}
    post("/calendar/events", {**series, "title": "Demo Restore Session",
                              "note": "Invented calendar note",
                              "start_time": "10:00", "end_time": "10:45"})
    calendar_id = id_of("SELECT id FROM calendar_events WHERE title = 'Demo Restore Session'")
    post(f"/calendar/events/{calendar_id}/skip", {"date": "2024-01-08"})
    post(f"/calendar/events/{calendar_id}", {**series, "title": "Demo Restore Session Revised",
                                             "note": "Invented calendar note revised",
                                             "start_time": "10:15", "end_time": "11:00"})

    exported = client.post("/export/jsonl")
    Path(sys.argv[2]).write_text(exported.text, encoding="utf-8")
print(json.dumps({"writes": writes, "export_status": exported.status_code,
                  "habit_id": habit_id, "task_id": task_id}))
"""

_REEXPORT = """
import sys
sys.path.insert(0, sys.argv[1])
from app.db import get_conn, init_db
from app.services.export import build_jsonl
init_db()
conn = get_conn()
try:
    text, _ = build_jsonl(conn)
finally:
    conn.close()
print(text, end="")
"""

_SERVE = """
import json
import sys
sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app, base_url="http://localhost") as client:
    print(json.dumps({path: client.get(path).status_code for path in sys.argv[2:]}))
"""


def _env(data_dir: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("ACTIVITY_DATA_DIR", None)
    env.pop("ACTIVITY_DB", None)
    env.pop("EPHEMERIS_ENABLE_TERMINAL", None)
    if data_dir is not None:
        env["ACTIVITY_DATA_DIR"] = str(data_dir)
    return env


def in_app(program: str, data_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run `program` in a fresh interpreter bound to `data_dir`."""
    run = subprocess.run(
        [sys.executable, "-c", program, str(ROOT), *args],
        cwd=ROOT, env=_env(data_dir), text=True, capture_output=True,
    )
    assert run.returncode == 0, run.stderr
    return run


def restore(export_path: Path, target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "restore_from_export.py"),
         str(export_path), str(target)],
        cwd=ROOT, env=_env(), text=True, capture_output=True,
    )


def records_of(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines()]


def audit(records: list[dict]) -> list[dict]:
    return [record for record in records if record["type"] != "calendar_event_series"]


def calendar_snapshots(records: list[dict]) -> list[tuple]:
    """Snapshots with the one documented loss normalized away: the list link,
    which restore clears because the referenced list is not reconstructible."""
    normalized = []
    for record in records:
        if record["type"] != "calendar_event_series":
            continue
        payload = {**record["payload"], "list_id": None}
        normalized.append((
            record["payload_version"],
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ))
    return sorted(normalized)


def state_projection(db: Path) -> dict[str, list[tuple]]:
    """The business rows; service-owned timestamps are not exported."""
    return {
        "routine_items": query(db, """
            SELECT id, title, group_name, active, sort_order, emoji, frequency,
                   goal, goal_days, start_date, reminder, constant_reminder
            FROM routine_items ORDER BY id
            """),
        "checkins": query(db, """
            SELECT date, routine_item_id, status, note
            FROM checkins ORDER BY date, routine_item_id
            """),
        "daily_notes": query(db, "SELECT date, text FROM daily_notes ORDER BY date"),
        "calendar_events": query(db, """
            SELECT id, title, emoji, note, all_day, start_time, end_time, freq,
                   byweekday, interval_n, start_date, end_date, exdates, color,
                   created_at, updated_at, archived_at
            FROM calendar_events ORDER BY id
            """),
    }


def count(db: Path, table: str) -> int:
    return query(db, f"SELECT COUNT(*) FROM {table}")[0][0]


@dataclass(frozen=True)
class Source:
    db: Path
    export_path: Path
    records: list[dict]
    habit_id: int
    task_id: int


@dataclass(frozen=True)
class Restored:
    db: Path
    run: subprocess.CompletedProcess[str]
    records: list[dict]


@pytest.fixture(scope="module")
def work(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("restore-roundtrip")


@pytest.fixture(scope="module")
def source(work) -> Source:
    """An instance written through the real routes, and the export it produced."""
    data_dir = work / "source"
    export_path = work / "demo-export.jsonl"
    seeded = json.loads(in_app(_SEED, data_dir, str(export_path)).stdout)
    assert all(status == expected for _, status, expected in seeded["writes"]), (
        seeded["writes"]
    )
    assert seeded["export_status"] == 200
    records = records_of(export_path.read_text(encoding="utf-8"))
    assert records
    return Source(data_dir / "activity.sqlite", export_path, records,
                  seeded["habit_id"], seeded["task_id"])


@pytest.fixture(scope="module")
def restored(work, source) -> list[Restored]:
    """The export restored into two fresh targets, each re-exported."""
    results = []
    for name in ("restored-one", "restored-two"):
        target = work / name
        run = restore(source.export_path, target)
        assert run.returncode == 0, run.stderr or run.stdout
        records = records_of(in_app(_REEXPORT, target).stdout)
        results.append(Restored(target / "activity.sqlite", run, records))
    return results


def test_the_source_export_exercises_every_replay_transition(source):
    """What the equality proofs below are worth depends on the stream holding
    every transition the importer replays, including the awkward ones."""
    events = audit(source.records)
    assert REPLAY_TRANSITIONS <= {record["type"] for record in events}

    task_updates = [
        record for record in source.records
        if record["type"] == "task_updated"
        and record["payload"].get("task_id") == source.task_id
    ]
    assert len(task_updates) == 1
    assert set(task_updates[0]["payload"]) == {"task_id", "title"}, (
        "the documented non-restorable update: the payload carries the title only"
    )

    habit = [record for record in events
             if record["payload"].get("routine_item_id") == source.habit_id]
    deactivations = [i for i, record in enumerate(habit)
                     if record["type"] == "routine_item_deactivated"]
    sparse_reactivations = [
        i for i, record in enumerate(habit)
        if record["type"] == "routine_item_updated"
        and set(record["payload"]) == {"routine_item_id", "title", "group_name", "sort_order"}
    ]
    assert len(deactivations) == 1 and len(sparse_reactivations) == 1
    assert sparse_reactivations[0] > deactivations[0]
    assert query(
        source.db,
        f"SELECT active, deactivated_at FROM routine_items WHERE id = {source.habit_id}",
    ) == [(1, None)]

    checkins = [record for record in habit
                if record["type"] == "routine_checkin_upserted"
                and record["payload"].get("date") == "2024-01-15"]
    assert checkins[-1]["payload"]["status"] == "light_done"
    assert checkins[-1]["payload"]["note"] == "Invented roundtrip note"
    assert query(
        source.db,
        "SELECT status, note FROM checkins "
        f"WHERE routine_item_id = {source.habit_id} AND date = '2024-01-15'",
    ) == [("light_done", "Invented roundtrip note")]


def test_a_fresh_restore_reports_its_partial_status(restored):
    for each in restored:
        assert "RESTORE STATUS: PARTIAL" in each.run.stdout
        assert "MODE: FRESH build" in each.run.stdout
        assert "  tasks: 0 rows" in each.run.stdout
        assert "IDEMPOTENT REDELIVERY: YES" in each.run.stdout


def test_the_re_exported_stream_equals_the_source(source, restored):
    """Every audit line, in order, with its id; every calendar snapshot but
    for the list link. Not a count, not a sample: the whole stream."""
    for each in restored:
        assert audit(each.records) == audit(source.records)
        assert calendar_snapshots(each.records) == calendar_snapshots(source.records)


def test_reconstructible_state_matches_row_for_row(source, restored):
    expected = state_projection(source.db)
    for each in restored:
        assert state_projection(each.db) == expected
        for table in ("lists", "tasks", "focus_sessions", "lessons", "tags", "task_tags"):
            assert count(each.db, table) == 0, (
                f"{table} is not replayed, so it stays empty instead of being fabricated"
            )


def test_the_calendar_list_link_is_the_only_loss_and_is_reported(source, restored):
    linked = "SELECT COUNT(*) FROM calendar_events WHERE list_id IS NOT NULL"
    assert query(source.db, linked) == [(1,)]
    for each in restored:
        assert query(each.db, linked) == [(0,)]
        assert "calendar_events.list_id: cleared on 1 row(s)" in each.run.stdout


def test_id_namespaces_advance_past_every_retained_id(source, restored):
    """A table the restore leaves empty still had rows in the source, and the
    retained stream names their ids. The first post-restore write must not take
    one of those ids back, or the next export becomes ambiguous. The habit
    namespace is the one a focus event names: its rows are replayed, but the
    bootstrap habits are inserted without an event, so a focus span on one of
    them is the only thing that says the id was taken."""
    floors: dict[str, int] = {}
    for record in source.records:
        for key, table in ID_KEYS.items():
            value = record.get("payload", {}).get(key)
            if isinstance(value, int):
                floors[table] = max(floors.get(table, 0), value)
    assert set(floors) == set(ID_KEYS.values()), "the fixture names every namespace"

    for each in restored:
        sequences = dict(query(each.db, "SELECT name, seq FROM sqlite_sequence"))
        for table, floor in floors.items():
            assert sequences.get(table, 0) >= floor, (table, floor, sequences)
        reported = next(
            line for line in each.run.stdout.splitlines()
            if line.startswith("  id namespaces advanced past retained audit ids: ")
        ).split(": ", 1)[1]
        assert dict(
            (table, int(seq)) for table, seq in
            (bump.split(" -> ") for bump in reported.split(", "))
        ) == floors, "the namespaces this test knows are the ones the restore bumped"


def test_a_failed_replay_cleans_its_staging_and_the_target_accepts_a_retry(work, source):
    """A restore builds under `<target>.restore-tmp-*` beside the target. A
    failed one must take that down along with the half-built target, must not
    block the retry, and must leave alone a sibling that merely shares the
    naming convention."""
    target = work / "restored-failed"
    bad_export = work / "bad-export.jsonl"
    bad_export.write_text(json.dumps({
        "timestamp": "2024-01-15T00:00:00",
        "type": "calendar_event_series",
        "payload_version": 1,
        "payload": {"bogus_column": 1},
    }) + "\n", encoding="utf-8")
    decoy = work / "restored-failed.restore-tmp"
    decoy.mkdir()
    (decoy / "keep-me.txt").write_text("unrelated data\n", encoding="utf-8")

    failed = restore(bad_export, target)
    assert failed.returncode != 0
    assert not target.exists()
    assert not list(work.glob("restored-failed.restore-tmp-*"))

    retry = restore(source.export_path, target)
    assert retry.returncode == 0, retry.stderr or retry.stdout
    assert "RESTORE STATUS: PARTIAL" in retry.stdout
    assert (decoy / "keep-me.txt").read_text(encoding="utf-8") == "unrelated data\n"


def test_restore_marks_the_target_initialized(restored):
    """The marker test_120 pins is written by the restore itself, so the first
    app start on the target has nothing to seed."""
    for each in restored:
        assert "FIRST APP START: no demo seeding" in each.run.stdout
        marker = query(each.db, "SELECT value FROM app_meta WHERE key = 'seeded_at'")
        assert marker and marker[0][0]


def test_an_export_retaining_no_habits_still_leaves_an_initialized_target(work):
    """`routine_items` is the table the check-in seeder counts; an empty one
    after restore must read as restored, not as new."""
    export_path = work / "no-habits-export.jsonl"
    export_path.write_text(json.dumps({
        "timestamp": "2024-01-15T00:00:00",
        "type": "daily_note_updated",
        "payload_version": 1,
        "payload": {"date": "2024-01-15", "text": "Invented note"},
    }) + "\n", encoding="utf-8")
    target = work / "restored-no-habits"

    run = restore(export_path, target)

    assert run.returncode == 0, run.stderr or run.stdout
    assert "FIRST APP START: no demo seeding" in run.stdout
    marker = query(target / "activity.sqlite",
                   "SELECT value FROM app_meta WHERE key = 'seeded_at'")
    assert marker and marker[0][0]


def test_two_fresh_restores_are_deterministic(restored):
    one, two = restored
    assert audit(one.records) == audit(two.records)
    assert calendar_snapshots(one.records) == calendar_snapshots(two.records)
    assert state_projection(one.db) == state_projection(two.db)
    assert count(one.db, "events") == count(two.db, "events")


def test_the_restored_instance_serves_its_pages_with_its_stream_intact(work, source):
    """The projections above read the database. This asks what an operator
    asks: does the app RENDER on a restored ledger. Two read routes call
    `lists.inbox_id()` unconditionally, so a restored database without one
    opens and then raises on its own home page. And the boot that served them
    ran startup for real, so the stream is compared whole afterwards: not one
    event added, and not one rewritten."""
    target = work / "restored-then-booted"
    assert restore(source.export_path, target).returncode == 0

    served = json.loads(in_app(_SERVE, target, "/", "/next7", "/today").stdout)

    assert served == {"/": 200, "/next7": 200, "/today": 200}
    booted = records_of(in_app(_REEXPORT, target).stdout)
    assert audit(booted) == audit(source.records)
