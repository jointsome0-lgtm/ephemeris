#!/usr/bin/env python3
"""Restore the reconstructible subset of a Tick-like JSONL export.

The current export is not a full database snapshot.  This command therefore
accepts only a fresh target and always reports the tables and metadata that the
stream cannot reproduce.

Usage:
    python scripts/restore_from_export.py EXPORT.jsonl TARGET_ACTIVITY_DATA_DIR
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CALENDAR_SNAPSHOT_TYPE = "calendar_event_series"

ROUTINE_EVENT_TYPES = {
    "routine_item_created",
    "routine_item_updated",
    "routine_item_deactivated",
    "routine_item_deleted",
}
CHECKIN_EVENT_TYPES = {"routine_checkin_upserted", "routine_checkin_cleared"}
NOTE_EVENT_TYPES = {"daily_note_updated"}
REPLAYED_EVENT_TYPES = ROUTINE_EVENT_TYPES | CHECKIN_EVENT_TYPES | NOTE_EVENT_TYPES

PARTIAL_TABLE_EVENTS = {
    "lists": {"list_created", "list_updated", "list_archived"},
    "tasks": {
        "task_created",
        "task_moved",
        "task_completed",
        "task_reopened",
        "task_updated",
    },
    "focus_sessions": {"focus_session_recorded"},
    "lessons": {
        "lesson_created",
        "lesson_entry_changed",
        "lesson_status_changed",
        "lesson_archived",
        "lesson_restored",
    },
}
KNOWN_EVENT_TYPES = REPLAYED_EVENT_TYPES | set().union(*PARTIAL_TABLE_EVENTS.values()) | {
    "calendar_event_created",
    "calendar_event_updated",
    "calendar_event_archived",
    "calendar_occurrence_skipped",
    "calendar_occurrence_unskipped",
}


class RestoreError(RuntimeError):
    """The export cannot be restored without guessing or corrupting state."""


@dataclass(frozen=True)
class Record:
    line: int
    timestamp: str
    type: str
    payload_version: int
    payload: dict[str, Any]


def _field(payload: dict[str, Any], name: str, record: Record) -> Any:
    if name not in payload:
        raise RestoreError(f"line {record.line}: {record.type} is missing payload.{name}")
    return payload[name]


def load_records(path: Path) -> list[Record]:
    """Parse and validate the complete input before creating the target DB."""
    if not path.is_file():
        raise RestoreError(f"export file does not exist: {path}")
    records: list[Record] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RestoreError(f"line {line_no}: invalid JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise RestoreError(f"line {line_no}: record must be a JSON object")
        timestamp = value.get("timestamp")
        type_ = value.get("type")
        version = value.get("payload_version")
        payload = value.get("payload")
        if not isinstance(timestamp, str) or not timestamp:
            raise RestoreError(f"line {line_no}: timestamp must be a non-empty string")
        if not isinstance(type_, str) or not type_:
            raise RestoreError(f"line {line_no}: type must be a non-empty string")
        if not isinstance(version, int):
            raise RestoreError(f"line {line_no}: payload_version must be an integer")
        if version != 1:
            raise RestoreError(
                f"line {line_no}: unsupported payload_version {version} for {type_}"
            )
        if not isinstance(payload, dict):
            raise RestoreError(f"line {line_no}: payload must be a JSON object")
        records.append(Record(line_no, timestamp, type_, version, payload))
    return records


def _replay_routine(conn: sqlite3.Connection, record: Record) -> None:
    p = record.payload
    item_id = _field(p, "routine_item_id", record)
    if record.type == "routine_item_created":
        conn.execute(
            """
            INSERT INTO routine_items
              (id, title, group_name, active, sort_order, created_at, emoji,
               frequency, goal, goal_days, start_date, reminder, constant_reminder)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                _field(p, "title", record),
                _field(p, "group_name", record),
                _field(p, "sort_order", record),
                record.timestamp,
                p.get("emoji"),
                p.get("frequency", "daily"),
                p.get("goal", "achieve_all"),
                p.get("goal_days", "forever"),
                p.get("start_date"),
                p.get("reminder"),
                int(bool(p.get("constant_reminder", 0))),
            ),
        )
        return

    row = conn.execute("SELECT active FROM routine_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise RestoreError(f"line {record.line}: {record.type} references unknown item {item_id}")

    if record.type == "routine_item_updated":
        column_for = {
            "title": "title",
            "group_name": "group_name",
            "sort_order": "sort_order",
            "emoji": "emoji",
            "frequency": "frequency",
            "goal": "goal",
            "goal_days": "goal_days",
            "start_date": "start_date",
            "reminder": "reminder",
            "constant_reminder": "constant_reminder",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for payload_name, column in column_for.items():
            if payload_name in p:
                assignments.append(f"{column} = ?")
                value = p[payload_name]
                values.append(int(bool(value)) if payload_name == "constant_reminder" else value)
        # Reactivation currently overloads a sparse routine_item_updated event.
        sparse_reactivation = row["active"] == 0 and set(p) == {
            "routine_item_id",
            "title",
            "group_name",
            "sort_order",
        }
        if sparse_reactivation:
            assignments.extend(("active = 1", "deactivated_at = NULL"))
        assignments.append("updated_at = ?")
        values.extend((record.timestamp, item_id))
        conn.execute(
            f"UPDATE routine_items SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
    elif record.type == "routine_item_deactivated":
        conn.execute(
            "UPDATE routine_items SET active = 0, deactivated_at = ? WHERE id = ?",
            (record.timestamp, item_id),
        )
    elif record.type == "routine_item_deleted":
        conn.execute("DELETE FROM checkins WHERE routine_item_id = ?", (item_id,))
        conn.execute("DELETE FROM routine_items WHERE id = ?", (item_id,))


def _replay_checkin(conn: sqlite3.Connection, record: Record) -> None:
    p = record.payload
    date = _field(p, "date", record)
    item_id = _field(p, "routine_item_id", record)
    if record.type == "routine_checkin_upserted":
        if conn.execute("SELECT 1 FROM routine_items WHERE id = ?", (item_id,)).fetchone() is None:
            raise RestoreError(
                f"line {record.line}: {record.type} references unknown item {item_id}"
            )
        conn.execute(
            """
            INSERT INTO checkins
              (date, routine_item_id, status, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(date, routine_item_id) DO UPDATE SET
              status = excluded.status,
              note = excluded.note,
              updated_at = excluded.updated_at
            """,
            (
                date,
                item_id,
                _field(p, "status", record),
                p.get("note"),
                record.timestamp,
                record.timestamp,
            ),
        )
    else:
        removed = conn.execute(
            "DELETE FROM checkins WHERE date = ? AND routine_item_id = ?",
            (date, item_id),
        ).rowcount
        if removed != 1:
            raise RestoreError(
                f"line {record.line}: {record.type} found no check-in for {date}/{item_id}"
            )


def _replay_note(conn: sqlite3.Connection, record: Record) -> None:
    p = record.payload
    conn.execute(
        """
        INSERT INTO daily_notes (date, text, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
          text = excluded.text,
          updated_at = excluded.updated_at
        """,
        (
            _field(p, "date", record),
            _field(p, "text", record),
            record.timestamp,
            record.timestamp,
        ),
    )


def _insert_calendar_snapshot(
    conn: sqlite3.Connection, record: Record, unresolved_links: list[int]
) -> None:
    schema_columns = [r["name"] for r in conn.execute("PRAGMA table_info(calendar_events)")]
    if set(record.payload) != set(schema_columns):
        missing = sorted(set(schema_columns) - set(record.payload))
        extra = sorted(set(record.payload) - set(schema_columns))
        raise RestoreError(
            f"line {record.line}: calendar snapshot/schema mismatch; "
            f"missing={missing}, extra={extra}"
        )
    payload = dict(record.payload)
    list_id = payload.get("list_id")
    if list_id is not None and conn.execute(
        "SELECT 1 FROM lists WHERE id = ?", (list_id,)
    ).fetchone() is None:
        # Keep the restored database FK-clean. The exact list link cannot be kept
        # because bootstrap list rows never entered the export stream.
        unresolved_links.append(list_id)
        payload["list_id"] = None
    quoted = ", ".join(f'"{name}"' for name in schema_columns)
    placeholders = ", ".join("?" for _ in schema_columns)
    conn.execute(
        f"INSERT INTO calendar_events ({quoted}) VALUES ({placeholders})",
        [payload[name] for name in schema_columns],
    )


def _ensure_fresh_target(target: Path) -> None:
    if target.exists() and not target.is_dir():
        raise RestoreError(f"target is not a directory: {target}")
    if target.exists():
        entries = list(target.iterdir())
        if entries:
            raise RestoreError(
                f"target ACTIVITY_DATA_DIR must be absent or empty: {target}"
            )


def restore(records: list[Record], target: Path) -> dict[str, Any]:
    """Build a fresh schema, preserve the audit stream, and replay supported state.

    The database is built in a sibling staging directory and moved into place
    only on success, so a replay failure cannot leave a half-created target
    that would block the retry behind the fresh-target guard.
    """
    _ensure_fresh_target(target)
    staging = target.parent / f"{target.name}.restore-tmp"
    if staging.exists():
        shutil.rmtree(staging)  # leftover staging from a previously failed run
    try:
        result = _build_into(records, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if target.exists():
        target.rmdir()  # verified empty by the guard; rename needs the name free
    staging.rename(target)
    return result


def _build_into(records: list[Record], staging: Path) -> dict[str, Any]:
    staging.mkdir(parents=True)

    # app.db resolves these at import time. ACTIVITY_DB must not escape the target.
    os.environ["ACTIVITY_DATA_DIR"] = str(staging)
    os.environ.pop("ACTIVITY_DB", None)
    sys.path.insert(0, str(ROOT))
    from app import db  # noqa: E402

    db.init_db()
    conn = db.get_conn()
    type_counts = Counter(r.type for r in records if r.type != CALENDAR_SNAPSHOT_TYPE)
    unknown_types = sorted(set(type_counts) - KNOWN_EVENT_TYPES)
    unresolved_links: list[int] = []
    audit_records = [r for r in records if r.type != CALENDAR_SNAPSHOT_TYPE]
    snapshots = [r for r in records if r.type == CALENDAR_SNAPSHOT_TYPE]
    try:
        with conn:
            for record in audit_records:
                conn.execute(
                    "INSERT INTO events (timestamp, type, payload_version, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        record.timestamp,
                        record.type,
                        record.payload_version,
                        json.dumps(record.payload, ensure_ascii=False),
                    ),
                )
                if record.type in ROUTINE_EVENT_TYPES:
                    _replay_routine(conn, record)
                elif record.type in CHECKIN_EVENT_TYPES:
                    _replay_checkin(conn, record)
                elif record.type in NOTE_EVENT_TYPES:
                    _replay_note(conn, record)
            for record in snapshots:
                _insert_calendar_snapshot(conn, record, unresolved_links)

        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RestoreError(f"restored database failed foreign_key_check: {len(fk_errors)} row(s)")
        row_counts = {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in (
                "events",
                "routine_items",
                "checkins",
                "daily_notes",
                "calendar_events",
            )
        }
    finally:
        conn.close()

    return {
        "rows": row_counts,
        "types": type_counts,
        "unknown_types": unknown_types,
        "unresolved_calendar_list_links": unresolved_links,
    }


def print_summary(target: Path, result: dict[str, Any]) -> None:
    rows = result["rows"]
    types: Counter[str] = result["types"]
    print("RESTORE STATUS: PARTIAL (limits in the current export contract)")
    print(f"TARGET: {target / 'activity.sqlite'}")
    print("RESTORED:")
    print(f"  events: {rows['events']} records (content/order; new local ids)")
    print(
        "  routine_items: "
        f"{rows['routine_items']} semantic rows (event timestamps replace row timestamps)"
    )
    print(
        "  checkins: "
        f"{rows['checkins']} semantic rows (new row ids; event-derived timestamps)"
    )
    print(
        "  daily_notes: "
        f"{rows['daily_notes']} semantic rows (event-derived timestamps)"
    )
    print(f"  calendar_events: {rows['calendar_events']} snapshot rows")

    print("PARTIAL / NOT RESTORED:")
    for table, event_types in PARTIAL_TABLE_EVENTS.items():
        retained = sum(types[name] for name in event_types)
        detail = {
            "lists": "bootstrap rows/kind/order/timestamps are absent",
            "tasks": "note/create order and update fields/side effects are absent",
            "focus_sessions": "note and authoritative row dates/timestamps are absent",
            "lessons": "open state is unjournaled; bundle files are outside JSONL",
        }[table]
        print(f"  {table}: 0 rows ({retained} audit events retained; {detail})")
    print("  tags: 0 rows (not journaled)")
    print("  task_tags: 0 rows (not journaled)")
    print("  data/lessons: not restored (filesystem content is not exported)")

    dropped = result["unresolved_calendar_list_links"]
    if dropped:
        print(
            "  calendar_events.list_id: "
            f"cleared on {len(dropped)} row(s); referenced lists were not reconstructible"
        )
    else:
        print("  calendar_events.list_id: no unresolved links")
    unknown = result["unknown_types"]
    if unknown:
        print(
            "  unknown event types: audit records retained, typed state not replayed: "
            + ", ".join(unknown)
        )
    else:
        print("  unknown event types: none")

    print("IDEMPOTENT REDELIVERY: NO")
    print("  Exported events omit their stable events.id; this importer is fresh-target only.")
    print("FIRST APP START: current demo lists/tasks will seed into their empty tables")
    print("  and append new task events; inspect this partial DB before launching the app.")
    print("FULL-FIDELITY RECOVERY: use a consistent SQLite backup, not JSONL alone.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore the reconstructible subset of a Tick-like JSONL export."
    )
    parser.add_argument("export_file", type=Path)
    parser.add_argument("target_activity_data_dir", type=Path)
    args = parser.parse_args()
    source = args.export_file.expanduser().resolve()
    target = args.target_activity_data_dir.expanduser().resolve()
    try:
        records = load_records(source)
        result = restore(records, target)
    except (OSError, sqlite3.Error, RestoreError) as exc:
        print(f"RESTORE FAILED: {exc}", file=sys.stderr)
        return 1
    print_summary(target, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
