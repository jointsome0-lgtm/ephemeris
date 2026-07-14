"""Focused JSONL export -> restore -> re-export proof on throwaway databases."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WORK = tempfile.TemporaryDirectory(prefix="ephemeris-restore-verify-")
WORK_DIR = Path(WORK.name)
SOURCE_DIR = WORK_DIR / "source"

# The real app must bind to the throwaway source before it is imported.
os.environ["ACTIVITY_DATA_DIR"] = str(SOURCE_DIR)
os.environ.pop("ACTIVITY_DB", None)
os.environ["EPHEMERIS_DISABLE_TERMINAL"] = "1"
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.db import DB_PATH, get_conn  # noqa: E402
from app.main import app  # noqa: E402


PASS = 0
FAIL = 0


def check(label: str, condition: bool, extra: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}" + (f"  -- {extra}" if extra else ""))


def rows(db_path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def state_projection(db_path: Path) -> dict[str, list[tuple]]:
    """Business-row equivalence; exact service-owned timestamps are not exported."""
    return {
        "routine_items": rows(
            db_path,
            """
            SELECT id, title, group_name, active, sort_order, emoji, frequency,
                   goal, goal_days, start_date, reminder, constant_reminder
            FROM routine_items ORDER BY id
            """,
        ),
        "checkins": rows(
            db_path,
            """
            SELECT date, routine_item_id, status, note
            FROM checkins ORDER BY date, routine_item_id
            """,
        ),
        "daily_notes": rows(
            db_path,
            "SELECT date, text FROM daily_notes ORDER BY date",
        ),
        "calendar_events": rows(
            db_path,
            """
            SELECT id, title, emoji, note, all_day, start_time, end_time, freq,
                   byweekday, interval_n, start_date, end_date, exdates, color,
                   created_at, updated_at, archived_at
            FROM calendar_events ORDER BY id
            """,
        ),
    }


def audit_records(records: list[dict]) -> list[dict]:
    return [record for record in records if record["type"] != "calendar_event_series"]


def calendar_snapshots(records: list[dict]) -> list[tuple]:
    # Envelope timestamps regenerate; unavailable list links explicitly normalize.
    normalized = []
    for record in records:
        if record["type"] != "calendar_event_series":
            continue
        payload = dict(record["payload"])
        payload["list_id"] = None
        normalized.append((
            record["payload_version"],
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        ))
    return sorted(
        normalized
    )


def run_restore(export_path: Path, target: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("ACTIVITY_DATA_DIR", None)
    env.pop("ACTIVITY_DB", None)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "restore_from_export.py"),
         str(export_path), str(target)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


_REEXPORT = """
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


def reexport(target: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ACTIVITY_DATA_DIR"] = str(target)
    env.pop("ACTIVITY_DB", None)
    env["EPHEMERIS_DISABLE_TERMINAL"] = "1"
    return subprocess.run(
        [sys.executable, "-c", _REEXPORT],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


_BOOT_AND_REEXPORT = """
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app) as client:
    response = client.post('/export/jsonl')
    response.raise_for_status()
    print(response.text, end='')
"""


def boot_app_and_reexport(target: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ACTIVITY_DATA_DIR"] = str(target)
    env.pop("ACTIVITY_DB", None)
    env["EPHEMERIS_DISABLE_TERMINAL"] = "1"
    return subprocess.run(
        [sys.executable, "-c", _BOOT_AND_REEXPORT],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


with TestClient(app) as client:
    # All fixtures are invented demo data and all writes go through real routes.
    response = client.post(
        "/habits",
        data={
            "title": "Demo Restore Walk",
            "group_name": "Demo Routine",
            "emoji": "🧭",
            "frequency": "weekdays",
            "goal": "achieve_all",
            "goal_days": "30",
            "start_date": "2024-01-01",
            "reminder": "08:15",
            "constant_reminder": "1",
        },
        follow_redirects=False,
    )
    check("real app creates restore-demo habit", response.status_code == 303)
    conn = get_conn()
    try:
        habit_id = conn.execute(
            "SELECT id FROM routine_items WHERE title = 'Demo Restore Walk'"
        ).fetchone()["id"]
        inbox_id = conn.execute(
            "SELECT id FROM lists WHERE kind = 'inbox' ORDER BY id LIMIT 1"
        ).fetchone()["id"]
    finally:
        conn.close()

    response = client.post(
        f"/habits/{habit_id}/edit",
        data={
            "title": "Demo Restore Walk Revised",
            "group_name": "Demo Routine",
            "emoji": "🧭",
            "frequency": "daily",
            "goal": "achieve_all",
            "goal_days": "forever",
            "start_date": "2024-01-01",
            "reminder": "",
        },
        follow_redirects=False,
    )
    check("real app updates restore-demo habit", response.status_code == 303)
    critical_responses = [
        client.post(f"/habits/{habit_id}/archive", follow_redirects=False),
        client.post(f"/items/{habit_id}/reactivate", follow_redirects=False),
    ]

    # Exercise set -> clear -> set plus a note-only update.
    for status in ("full_done", "full_done", "light_done"):
        critical_responses.append(client.post(
            "/checkins",
            data={"date": "2024-01-15", "routine_item_id": habit_id, "status": status},
            follow_redirects=False,
        ))
    critical_responses.append(client.post(
        "/checkins",
        data={
            "date": "2024-01-15",
            "routine_item_id": habit_id,
            "note": "Invented roundtrip note",
        },
        follow_redirects=False,
    ))
    critical_responses.append(client.post(
        "/daily-note",
        data={"date": "2024-01-15", "text": "Invented roundtrip daily note"},
        follow_redirects=False,
    ))

    # Exercise hard-delete replay, including its check-in cascade.
    critical_responses.append(client.post(
        "/habits",
        data={"title": "Demo Disposable Habit", "group_name": "Demo Routine"},
        follow_redirects=False,
    ))
    conn = get_conn()
    try:
        deleted_id = conn.execute(
            "SELECT id FROM routine_items WHERE title = 'Demo Disposable Habit'"
        ).fetchone()["id"]
    finally:
        conn.close()
    critical_responses.append(client.post(
        "/checkins",
        data={"date": "2024-01-16", "routine_item_id": deleted_id, "status": "failed"},
        follow_redirects=False,
    ))
    critical_responses.append(
        client.post(f"/habits/{deleted_id}/delete", follow_redirects=False)
    )

    # This deliberately creates a known non-restorable task update.
    critical_responses.append(client.post(
        "/tasks",
        data={"title": "Demo Partial Task", "list_id": inbox_id, "due_date": "2024-02-01"},
        follow_redirects=False,
    ))
    conn = get_conn()
    try:
        task_id = conn.execute(
            "SELECT id FROM tasks WHERE title = 'Demo Partial Task'"
        ).fetchone()["id"]
    finally:
        conn.close()
    critical_responses.append(client.post(
        f"/tasks/{task_id}/update",
        data={
            "title": "Demo Partial Task Revised",
            "note": "This field is intentionally absent from task_updated",
            "due_date": "2024-02-02",
            "priority": "3",
            "list_id": str(inbox_id),
        },
        follow_redirects=False,
    ))

    response = client.post(
        "/calendar/events",
        data={
            "title": "Demo Restore Session",
            "emoji": "🧪",
            "list_id": str(inbox_id),
            "note": "Invented calendar note",
            "start_time": "10:00",
            "end_time": "10:45",
            "freq": "weekly",
            "wd": ["0", "2"],
            "interval_n": "1",
            "start_date": "2024-01-01",
            "end_date": "2024-03-31",
        },
        follow_redirects=False,
    )
    check("real app creates restore-demo calendar series", response.status_code == 303)
    conn = get_conn()
    try:
        calendar_id = conn.execute(
            "SELECT id FROM calendar_events WHERE title = 'Demo Restore Session'"
        ).fetchone()["id"]
    finally:
        conn.close()
    critical_responses.append(client.post(
        f"/calendar/events/{calendar_id}/skip",
        data={"date": "2024-01-08"},
        follow_redirects=False,
    ))
    critical_responses.append(client.post(
        f"/calendar/events/{calendar_id}",
        data={
            "title": "Demo Restore Session Revised",
            "emoji": "🧪",
            "list_id": str(inbox_id),
            "note": "Invented calendar note revised",
            "start_time": "10:15",
            "end_time": "11:00",
            "freq": "weekly",
            "wd": ["0", "2"],
            "interval_n": "1",
            "start_date": "2024-01-01",
            "end_date": "2024-03-31",
        },
        follow_redirects=False,
    ))
    check(
        "all replay-transition fixture writes succeed",
        all(response.status_code == 303 for response in critical_responses),
        str([response.status_code for response in critical_responses]),
    )

    exported = client.post("/export/jsonl")
    check("real app exports JSONL", exported.status_code == 200 and bool(exported.text.strip()))
    export_text = exported.text


export_path = WORK_DIR / "demo-export.jsonl"
export_path.write_text(export_text, encoding="utf-8")
source_records = [json.loads(line) for line in export_text.splitlines()]
source_projection = state_projection(DB_PATH)
source_event_types = {record["type"] for record in audit_records(source_records)}
check(
    "source export exercises every supported replay transition",
    {
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
    } <= source_event_types,
    str(sorted(source_event_types)),
)
task_updates = [
    record for record in source_records if record["type"] == "task_updated"
    and record["payload"].get("task_id") == task_id
]
check(
    "proof fixture confirms task_updated payload gap",
    len(task_updates) == 1 and set(task_updates[0]["payload"]) == {"task_id", "title"},
    str(task_updates),
)
habit_events = [
    (index, record)
    for index, record in enumerate(audit_records(source_records))
    if record["payload"].get("routine_item_id") == habit_id
]
deactivation_positions = [
    index for index, record in habit_events
    if record["type"] == "routine_item_deactivated"
]
sparse_reactivations = [
    (index, record) for index, record in habit_events
    if record["type"] == "routine_item_updated"
    and set(record["payload"]) == {
        "routine_item_id", "title", "group_name", "sort_order"
    }
]
check(
    "proof fixture confirms sparse reactivation after deactivation",
    rows(DB_PATH, f"SELECT active, deactivated_at FROM routine_items WHERE id = {habit_id}")
    == [(1, None)]
    and len(deactivation_positions) == 1
    and len(sparse_reactivations) == 1
    and sparse_reactivations[0][0] > deactivation_positions[0],
    str(habit_events),
)
checkin_events = [
    record for record in audit_records(source_records)
    if record["type"] == "routine_checkin_upserted"
    and record["payload"].get("routine_item_id") == habit_id
    and record["payload"].get("date") == "2024-01-15"
]
check(
    "proof fixture confirms final check-in note payload and state",
    rows(
        DB_PATH,
        "SELECT status, note FROM checkins "
        f"WHERE routine_item_id = {habit_id} AND date = '2024-01-15'",
    ) == [("light_done", "Invented roundtrip note")]
    and checkin_events[-1]["payload"].get("status") == "light_done"
    and checkin_events[-1]["payload"].get("note") == "Invented roundtrip note",
    str(checkin_events),
)

targets = [WORK_DIR / "restored-one", WORK_DIR / "restored-two"]
restore_runs = [run_restore(export_path, target) for target in targets]
for index, run in enumerate(restore_runs, start=1):
    check(
        f"restore run {index} succeeds with explicit partial status",
        run.returncode == 0
        and "RESTORE STATUS: PARTIAL" in run.stdout
        and "tasks: 0 rows" in run.stdout
        and "calendar_events.list_id: cleared on 1 row(s)" in run.stdout
        and "IDEMPOTENT REDELIVERY: NO" in run.stdout,
        run.stderr or run.stdout,
    )

failed_target = WORK_DIR / "restored-failed"
bad_export = WORK_DIR / "bad-export.jsonl"
bad_export.write_text(
    json.dumps({
        "timestamp": "2024-01-15T00:00:00",
        "type": "calendar_event_series",
        "payload_version": 1,
        "payload": {"bogus_column": 1},
    }) + "\n",
    encoding="utf-8",
)
decoy = WORK_DIR / "restored-failed.restore-tmp"
decoy.mkdir()
(decoy / "keep-me.txt").write_text("unrelated data\n", encoding="utf-8")
failed_run = run_restore(bad_export, failed_target)
check(
    "failed replay leaves neither target nor staging behind",
    failed_run.returncode != 0
    and not failed_target.exists()
    and not any(WORK_DIR.glob("restored-failed.restore-tmp-*")),
    failed_run.stderr or failed_run.stdout,
)
retry_run = run_restore(export_path, failed_target)
check(
    "retry into the same target succeeds after a failed run",
    retry_run.returncode == 0 and "RESTORE STATUS: PARTIAL" in retry_run.stdout,
    retry_run.stderr or retry_run.stdout,
)
check(
    "pre-existing sibling matching the staging convention is preserved",
    (decoy / "keep-me.txt").read_text(encoding="utf-8") == "unrelated data\n",
)

no_habits_export = WORK_DIR / "no-habits-export.jsonl"
no_habits_export.write_text(
    json.dumps({
        "timestamp": "2024-01-15T00:00:00",
        "type": "daily_note_updated",
        "payload_version": 1,
        "payload": {"date": "2024-01-15", "text": "Invented note"},
    }) + "\n",
    encoding="utf-8",
)
no_habits_run = run_restore(no_habits_export, WORK_DIR / "restored-no-habits")
check(
    "summary warns about habit seeding when no live habits were restored",
    no_habits_run.returncode == 0
    and "demo habits will seed" in no_habits_run.stdout,
    no_habits_run.stderr or no_habits_run.stdout,
)

reexports = [reexport(target) for target in targets]
for index, run in enumerate(reexports, start=1):
    check(f"re-export run {index} succeeds", run.returncode == 0, run.stderr)

restored_records = [
    [json.loads(line) for line in run.stdout.splitlines()] if run.returncode == 0 else []
    for run in reexports
]
check(
    "audit event stream matches after restore",
    all(audit_records(records) == audit_records(source_records) for records in restored_records),
)
check(
    "calendar snapshots match except documented list link/export timestamp",
    all(calendar_snapshots(records) == calendar_snapshots(source_records)
        for records in restored_records),
)

_ID_KEYS = {"task_id": "tasks", "list_id": "lists",
            "session_id": "focus_sessions", "lesson_id": "lessons"}
expected_seq_floor: dict[str, int] = {}
for record in source_records:
    for key, table in _ID_KEYS.items():
        value = record.get("payload", {}).get(key)
        if isinstance(value, int):
            expected_seq_floor[table] = max(expected_seq_floor.get(table, 0), value)
restored_seqs = {
    name: seq
    for name, seq in rows(targets[0] / "activity.sqlite",
                          "SELECT name, seq FROM sqlite_sequence")
}
check(
    "skipped-table id namespaces advance past retained audit ids",
    bool(expected_seq_floor)
    and all(restored_seqs.get(table, 0) >= floor
            for table, floor in expected_seq_floor.items()),
    f"expected >= {expected_seq_floor}, got {restored_seqs}",
)

restored_projections = [state_projection(target / "activity.sqlite") for target in targets]
check(
    "reconstructible state projections match row-wise",
    all(projection == source_projection for projection in restored_projections),
)
check(
    "derived kept-day count recomputes identically",
    all(
        sum(1 for row in projection["checkins"] if row[2] in ("full_done", "light_done"))
        == sum(1 for row in source_projection["checkins"]
               if row[2] in ("full_done", "light_done"))
        for projection in restored_projections
    ),
)
check(
    "unsupported typed tables stay empty instead of being silently fabricated",
    all(
        rows(target / "activity.sqlite", "SELECT COUNT(*) FROM lists")[0][0] == 0
        and rows(target / "activity.sqlite", "SELECT COUNT(*) FROM tasks")[0][0] == 0
        and rows(target / "activity.sqlite", "SELECT COUNT(*) FROM focus_sessions")[0][0] == 0
        and rows(target / "activity.sqlite", "SELECT COUNT(*) FROM lessons")[0][0] == 0
        and rows(target / "activity.sqlite", "SELECT COUNT(*) FROM tags")[0][0] == 0
        and rows(target / "activity.sqlite", "SELECT COUNT(*) FROM task_tags")[0][0] == 0
        for target in targets
    ),
)
check(
    "linked calendar loss is explicit and limited to list_id",
    rows(DB_PATH, "SELECT COUNT(*) FROM calendar_events WHERE list_id IS NOT NULL")[0][0] == 1
    and all(
        rows(target / "activity.sqlite",
             "SELECT COUNT(*) FROM calendar_events WHERE list_id IS NOT NULL")[0][0] == 0
        for target in targets
    ),
)
check(
    "two fresh restores are deterministic",
    audit_records(restored_records[0]) == audit_records(restored_records[1])
    and calendar_snapshots(restored_records[0]) == calendar_snapshots(restored_records[1])
    and restored_projections[0] == restored_projections[1],
)

# Prove the operational limit too: a normal first app boot seeds the deliberately
# empty list/task tables and appends new task events. This is warned by restore.
boot_target = WORK_DIR / "restored-then-booted"
boot_restore = run_restore(export_path, boot_target)
check(
    "restore warns that first app boot mutates the partial DB",
    boot_restore.returncode == 0 and "FIRST APP START" in boot_restore.stdout,
    boot_restore.stderr or boot_restore.stdout,
)
booted = boot_app_and_reexport(boot_target)
check("restored DB opens through the real app", booted.returncode == 0, booted.stderr)
booted_records = (
    [json.loads(line) for line in booted.stdout.splitlines()]
    if booted.returncode == 0 else []
)
source_audit = audit_records(source_records)
booted_audit = audit_records(booted_records)
added_after_boot = booted_audit[len(source_audit):]
check(
    "first app boot preserves the stream prefix and adds only documented seed events",
    booted_audit[:len(source_audit)] == source_audit
    and len(added_after_boot) == 6
    and {record["type"] for record in added_after_boot} <= {"task_created", "task_completed"},
    str([record.get("type") for record in added_after_boot]),
)

print(f"\n{PASS} passed, {FAIL} failed")
WORK.cleanup()
raise SystemExit(1 if FAIL else 0)
