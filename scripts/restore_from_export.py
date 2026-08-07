#!/usr/bin/env python3
"""Restore the reconstructible subset of an Ephemeris JSONL export.

The current export is not a full database snapshot, so this command always
reports the tables and metadata the stream cannot reproduce.

It is idempotent when the export carries event ids (issue #17).  Each audit line
holds the source row's stable `events.uuid` as its `id`; a line whose id is
already in the target is skipped rather than inserted again, and calendar series
snapshots upsert on their own id.  Redelivering the same file into the same
target therefore applies nothing and changes nothing.

An export written before ids joined the envelope carries no such receipt.  It
still restores into a FRESH target exactly as it always did — restored rows get
new local uuids — but it cannot be redelivered into a populated one, because
there is nothing to recognize an already-applied line by.

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
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CALENDAR_SNAPSHOT_TYPE = "calendar_event_series"
# Mirrors app/settings.py: `<ACTIVITY_DATA_DIR>/activity.sqlite`. Read before
# app.db is imported, because importing it binds the path to the environment.
DB_FILENAME = "activity.sqlite"

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
        "task_status_changed",
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
    uuid: str | None = None
    """The export's `id`: the source row's `events.uuid`, or None for a
    calendar snapshot and for any export written before ids were carried."""


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
        # Test for the KEY, not the value. Absent means a pre-#17 export, which
        # is allowed. Present but unusable is a corrupt receipt — and `null` is
        # present: read as absent it would pass for a legacy line, take a fresh
        # local uuid at restore, and silently discard the source identity that
        # every later redelivery depends on. iter_jsonl omits the key rather
        # than writing null, so nothing this app produces takes that branch.
        if "id" in value:
            uuid = value["id"]
            if not isinstance(uuid, str) or not uuid:
                raise RestoreError(
                    f"line {line_no}: id must be a non-empty string when present"
                )
        else:
            uuid = None
        records.append(Record(line_no, timestamp, type_, version, payload, uuid))
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
    # A snapshot is complete state keyed by the series id, so redelivery means
    # "this row now reads like this" — an upsert, never a second row. The export
    # holds one snapshot per series, so within a single file this never fires.
    updates = ", ".join(
        f'"{name}" = excluded."{name}"' for name in schema_columns if name != "id"
    )
    conn.execute(
        f"INSERT INTO calendar_events ({quoted}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}",
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

    A target that already holds a database is redelivered into instead of
    refused: records it already carries are skipped, the rest applied. That path
    needs the export's event ids, so a pre-#17 file is still fresh-target only.

    The fresh path builds in a sibling staging directory and moves into place
    only on success, so a replay failure cannot leave a half-created target
    that would block the retry behind the fresh-target guard.
    """
    if (target / DB_FILENAME).is_file():
        return _redeliver_into(records, target)
    _ensure_fresh_target(target)
    # Unique name via mkdtemp: never collides with (or deletes) anything
    # pre-existing; sibling of target so the final rename stays on one filesystem.
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(
        prefix=f"{target.name}.restore-tmp-", dir=target.parent
    ))
    try:
        result = _build_into(records, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if target.exists():
        target.rmdir()  # verified empty by the guard; rename needs the name free
    staging.rename(target)
    return result


def _open_target(data_dir: Path):
    """Bind app.db to `data_dir`, create or upgrade the schema, and connect.

    Call once per process: app.db resolves these paths at import time, so a
    second call with a different directory would reuse the first binding.
    """
    # ACTIVITY_DB must not escape the target.
    os.environ["ACTIVITY_DATA_DIR"] = str(data_dir)
    os.environ.pop("ACTIVITY_DB", None)
    sys.path.insert(0, str(ROOT))
    from app import db  # noqa: E402

    db.init_db()
    return db, db.get_conn()


def _build_into(records: list[Record], staging: Path) -> dict[str, Any]:
    db, conn = _open_target(staging)
    try:
        return _apply_records(db, conn, records, redelivery=False)
    finally:
        conn.close()


def _redeliver_into(records: list[Record], target: Path) -> dict[str, Any]:
    missing = [
        r.line for r in records
        if r.type != CALENDAR_SNAPSHOT_TYPE and r.uuid is None
    ]
    if missing:
        raise RestoreError(
            f"target already holds a database, but {len(missing)} audit record(s) "
            f'carry no "id" (first at line {missing[0]}). Redelivery recognizes an '
            "already-applied record by its id; restore this export into a fresh "
            "target instead."
        )
    db, conn = _open_target(target)
    try:
        return _apply_records(db, conn, records, redelivery=True)
    finally:
        conn.close()


def _require_shared_history(
    conn: sqlite3.Connection, audit_records: list[Record], seen: set[str]
) -> None:
    """Refuse a target that is not an earlier delivery of THIS stream.

    Redelivery is only ever a CONTINUATION: everything the target already holds
    must appear in the incoming export. Sharing merely *some* history is not
    enough, because `calendar_event_series` lines are absolute state rather than
    a diff — they upsert whatever the file says, whether or not the audit half
    had anything left to apply.

    Three targets fail that test, and each would be corrupted silently:

    - one built from a pre-#17 export, which minted its own local uuids: a later
      export of the very same source database matches none of them, so the whole
      prefix would land a second time;
    - an unrelated history, which shares nothing at all, yet whose
      `calendar_events` ids are small integers that collide readily — the
      snapshot upsert would overwrite real series belonging to someone else;
    - one that is AHEAD of this export, or that diverged from it. Every audit
      line is skipped and the run reports "0 applied", which reads as "nothing
      changed" — while the older snapshots quietly revert calendar titles,
      recurrence rules and archive state to what they were at export time.

    An empty ledger is the one honest exception: nothing to contradict, nothing
    to overwrite.
    """
    if conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0:
        return
    incoming = {r.uuid for r in audit_records if r.uuid is not None}
    unmatched = seen - incoming
    if not unmatched:
        return
    if not (incoming & seen):
        raise RestoreError(
            "target already holds a database, but it shares no event id with this "
            "export, so it is not an earlier delivery of this stream. Restoring "
            "here would duplicate the whole history and could overwrite unrelated "
            "calendar series. Use a fresh target. (A target built from a pre-#17 "
            "export cannot be redelivered into: its rows were given new local ids "
            "at restore time.)"
        )
    raise RestoreError(
        f"target holds {len(unmatched)} event(s) this export does not contain, so "
        "it is ahead of this file or has diverged from it. Redelivery only ever "
        "moves forward: applying this export would revert calendar series to their "
        "state at export time while reporting that nothing was applied. Deliver a "
        "newer export, or use a fresh target."
    )


def _require_same_envelope(conn: sqlite3.Connection, record: Record) -> None:
    """A receipt identifies an event only if it describes the SAME event.

    Skipping is justified by "the target already has this record". A damaged,
    hand-edited or concatenated export can carry one id on two different
    envelopes, and then the skip discards a real event together with its typed
    replay while reporting it as already delivered — silent loss, in the one tool
    whose job is not to lose anything. Everything else here refuses malformed
    input loudly; a receipt collision is refused the same way.

    The row is fetched by its unique index, one lookup per skipped record, rather
    than holding the target's payloads in memory beside the export's.
    """
    row = conn.execute(
        "SELECT timestamp, type, payload_version, payload_json FROM events "
        "WHERE uuid = ?",
        (record.uuid,),
    ).fetchone()
    if row is None:  # unreachable: `seen` is read from this same connection
        return
    try:
        stored_payload = json.loads(row["payload_json"])
    except (TypeError, ValueError):
        # iter_jsonl() deliberately exports an unparseable payload as
        # {"_raw": <stored text>}. Normalize the stored side the same way, or a
        # row with a corrupt payload could never be redelivered into the very
        # database the export came from: both sides describe the same event.
        stored_payload = {"_raw": row["payload_json"]}
    # Payloads compare as parsed objects: key order is not part of the event.
    if (
        row["timestamp"] == record.timestamp
        and row["type"] == record.type
        and row["payload_version"] == record.payload_version
        and stored_payload == record.payload
    ):
        return
    raise RestoreError(
        f"line {record.line}: id {record.uuid} is already present in the target "
        f"with a different envelope (stored {row['type']} at {row['timestamp']}, "
        f"incoming {record.type} at {record.timestamp}). Two different events "
        "cannot share one receipt; skipping this line would discard it silently."
    )


def _apply_records(
    db: Any, conn: sqlite3.Connection, records: list[Record], *, redelivery: bool
) -> dict[str, Any]:
    """Insert every audit record the target does not already hold, replay what
    it supports, and upsert the calendar snapshots."""
    type_counts = Counter(r.type for r in records if r.type != CALENDAR_SNAPSHOT_TYPE)
    unknown_types = sorted(set(type_counts) - KNOWN_EVENT_TYPES)
    unresolved_links: list[int] = []
    audit_records = [r for r in records if r.type != CALENDAR_SNAPSHOT_TYPE]
    snapshots = [r for r in records if r.type == CALENDAR_SNAPSHOT_TYPE]
    # Held in memory rather than queried per record: one id lookup per line
    # against a growing table is the only part of this that scales with both
    # sides at once. `events.uuid` is uniquely indexed, so the set is exact.
    applied = 0
    skipped = 0
    # db.immediate (#22), not `with conn:`: this is a read-modify-write. It reads
    # the target's receipts, decides in Python what to apply, and writes that back.
    # sqlite3's implicit BEGIN is DEFERRED and never precedes a SELECT, so with
    # `with conn:` the app could commit a calendar update between the read and the
    # write — after which this run would skip the audit prefix it no longer covers
    # and upsert an older snapshot over the newer series. The lock goes up front,
    # and every check below, including foreign keys, is inside it.
    with db.immediate(conn):
        seen = {
            row["uuid"]
            for row in conn.execute("SELECT uuid FROM events WHERE uuid IS NOT NULL")
        }
        if redelivery:
            _require_shared_history(conn, audit_records, seen)
        for record in audit_records:
            if record.uuid is not None and record.uuid in seen:
                # Already delivered. Its replay ran when it was applied, so
                # skipping the insert must skip the replay with it — that is
                # what makes redelivery a no-op rather than a double-apply.
                # But only an identical envelope is the same event: see below.
                _require_same_envelope(conn, record)
                skipped += 1
                continue
            conn.execute(
                "INSERT INTO events (uuid, timestamp, type, payload_version, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    record.uuid,
                    record.timestamp,
                    record.type,
                    record.payload_version,
                    json.dumps(record.payload, ensure_ascii=False),
                ),
            )
            if record.uuid is not None:
                seen.add(record.uuid)
            applied += 1
            if record.type in ROUTINE_EVENT_TYPES:
                _replay_routine(conn, record)
            elif record.type in CHECKIN_EVENT_TYPES:
                _replay_checkin(conn, record)
            elif record.type in NOTE_EVENT_TYPES:
                _replay_note(conn, record)
        for record in snapshots:
            _insert_calendar_snapshot(conn, record, unresolved_links)
        sequence_bumps = _bump_id_sequences(conn, records)
        # A pre-#17 export carries no ids, so its rows land with a NULL uuid and
        # are stamped here — fresh local identity, like restored autoincrement ids.
        db.backfill_event_uuids(conn)

        # The target is an initialized installation, not a fresh one (schema
        # v16). Startup used to decide that per table, by row count, and this
        # command deliberately leaves `lists` and `tasks` empty — so the first
        # app start seeded demo rows into restored history and appended their
        # events to the very audit stream this file exists to preserve. Written
        # only when absent, so a redelivery keeps the target's original
        # initialization time.
        if db.meta_get(conn, db.SEEDED_AT) is None:
            db.meta_set(conn, db.SEEDED_AT, db.now_iso())

        # Inside the transaction: raising here rolls the delivery back. Run after
        # the commit and a target that fails the check would keep every event
        # this run applied while the command exits RESTORE FAILED — an outcome
        # the fresh path hid, because its whole staging directory was discarded.
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RestoreError(
                f"restored database failed foreign_key_check: {len(fk_errors)} row(s)"
            )

    # The deliberately-unrestored tables are counted too, not assumed empty: in
    # redelivery mode the target may already hold real rows, and a recovery tool
    # that prints "0 rows" over live data is worse than one that prints nothing.
    row_counts = {
        table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in (
            "events",
            "routine_items",
            "checkins",
            "daily_notes",
            "calendar_events",
            *PARTIAL_TABLE_EVENTS,
            "tags",
            "task_tags",
        )
    }

    return {
        "rows": row_counts,
        "types": type_counts,
        "unknown_types": unknown_types,
        "unresolved_calendar_list_links": unresolved_links,
        "sequence_bumps": sequence_bumps,
        "applied": applied,
        "skipped": skipped,
        "redelivery": redelivery,
        "identified": all(r.uuid is not None for r in audit_records),
    }


# Payload key -> AUTOINCREMENT table whose id namespace the key belongs to.
# Scanned across every record (task events carry list_id, focus events carry
# lesson_id, calendar snapshots carry list_id).
_ID_NAMESPACES = {
    "task_id": "tasks",
    "list_id": "lists",
    "session_id": "focus_sessions",
    "lesson_id": "lessons",
}


def _bump_id_sequences(conn: sqlite3.Connection, records: list[Record]) -> dict[str, int]:
    """Advance sqlite_sequence for tables whose rows are not restored, so the
    first post-restore app writes cannot reuse ids already present in the
    retained audit stream (which would make later exports ambiguous)."""
    maxima: dict[str, int] = {}
    for record in records:
        for key, table in _ID_NAMESPACES.items():
            value = record.payload.get(key)
            if isinstance(value, int) and value > maxima.get(table, 0):
                maxima[table] = value
    for table, seq in maxima.items():
        row = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = ?", (table,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (table, seq)
            )
        elif row[0] < seq:
            conn.execute(
                "UPDATE sqlite_sequence SET seq = ? WHERE name = ?", (seq, table)
            )
    return maxima


def _untouched(count: int) -> str:
    """How a not-restored table reads: empty, or pre-existing and left alone."""
    return "0 rows" if count == 0 else f"{count} pre-existing rows (untouched)"


def print_summary(target: Path, result: dict[str, Any]) -> None:
    rows = result["rows"]
    types: Counter[str] = result["types"]
    print("RESTORE STATUS: PARTIAL (limits in the current export contract)")
    print(f"TARGET: {target / DB_FILENAME}")
    print("MODE: " + (
        "REDELIVERY into an existing database"
        if result["redelivery"] else "FRESH build"
    ))
    print("RESTORED:")
    print(
        f"  events: {result['applied']} applied / {result['skipped']} skipped "
        f"(already present); {rows['events']} total in target"
    )
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
        print(f"  {table}: {_untouched(rows[table])} "
              f"({retained} audit events retained; {detail})")
    print(f"  tags: {_untouched(rows['tags'])} (not journaled)")
    print(f"  task_tags: {_untouched(rows['task_tags'])} (not journaled)")
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
    bumps = result["sequence_bumps"]
    if bumps:
        print(
            "  id namespaces advanced past retained audit ids: "
            + ", ".join(f"{table} -> {seq}" for table, seq in sorted(bumps.items()))
        )

    if result["identified"]:
        print("IDEMPOTENT REDELIVERY: YES")
        print("  Every audit record carries its stable events.uuid as \"id\". Running this")
        print("  file into this target again applies 0 records and changes nothing.")
    else:
        print("IDEMPOTENT REDELIVERY: NO")
        print("  This export predates event ids in the envelope, so restored rows take")
        print("  fresh local uuids and it can only be restored into a fresh target.")
    # Startup asks `app_meta.seeded_at`, which this run wrote, and not the row
    # counts — so the tables left empty above stay empty, however empty they are.
    print("FIRST APP START: no demo seeding. This target is marked initialized")
    print("  (app_meta.seeded_at), so startup adds no demo habits, lists, tasks or")
    print("  events to the restored stream; the empty tables above stay empty. It")
    print("  does create the built-in Inbox if there is none — structure, not demo")
    print("  data: the Today and Next-7 views require it, and it appends no event.")
    print("FULL-FIDELITY RECOVERY: use a full backup, not JSONL alone —")
    print("  python -m scripts.backup_db (see docs/backup-restore.md).")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore the reconstructible subset of an Ephemeris JSONL export."
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
