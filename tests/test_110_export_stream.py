"""The JSONL export as a durable audit stream (#17, first half).

Three properties, each of which the old export lacked:

*Nothing is lost.* The filename carries `now_stamp()`, which resolves to the
second. Two exports inside one second used to be one file — the second silently
overwrote the first, and the directory listing gave no sign of it.

*Nothing is half-written.* The export was built in memory and dropped onto its
final name with `write_text`. An interruption left a truncated `events-*.jsonl`
that reads, to anyone looking at `data/exports/` later, exactly like a complete
backup.

*Nothing is delivered twice.* Every audit line now carries the source row's
`events.uuid` as `id`, so restoring the same file into the same database again
applies nothing instead of duplicating the whole stream.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.db import get_conn
from app.services import export

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def exports_dir(tmp_path, monkeypatch):
    """Redirect exports at the service module, which binds EXPORTS_DIR on import."""
    target = tmp_path / "exports"
    target.mkdir()
    monkeypatch.setattr(export, "EXPORTS_DIR", target)
    return target


def _conn():
    return get_conn()


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- nothing is lost -------------------------------------------------------


def test_two_exports_in_one_second_keep_both_files(client, exports_dir, monkeypatch):
    """`now_stamp()` has second resolution, so the collision is the normal case
    for two clicks in a row — not a theoretical one."""
    monkeypatch.setattr(export, "now_stamp", lambda: "2031-01-02-030405")
    conn = _conn()
    try:
        first, first_count = export.export_events(conn)
        second, second_count = export.export_events(conn)
    finally:
        conn.close()

    assert first.name == "events-2031-01-02-030405.jsonl"
    assert second.name == "events-2031-01-02-030405-2.jsonl"
    assert first.exists() and second.exists()
    assert first_count == second_count > 0
    assert {p.name for p in exports_dir.glob("events-*.jsonl")} == {
        first.name, second.name,
    }


def test_a_third_export_in_the_same_second_gets_its_own_name(
    client, exports_dir, monkeypatch
):
    monkeypatch.setattr(export, "now_stamp", lambda: "2031-02-03-040506")
    conn = _conn()
    try:
        names = [export.export_events(conn)[0].name for _ in range(3)]
    finally:
        conn.close()
    assert names == [
        "events-2031-02-03-040506.jsonl",
        "events-2031-02-03-040506-2.jsonl",
        "events-2031-02-03-040506-3.jsonl",
    ]


def test_an_export_never_overwrites_an_existing_file(client, exports_dir, monkeypatch):
    """The claim is a hard link, which refuses an occupied name outright."""
    monkeypatch.setattr(export, "now_stamp", lambda: "2031-03-04-050607")
    squatter = exports_dir / "events-2031-03-04-050607.jsonl"
    squatter.write_text("not an export\n", encoding="utf-8")
    conn = _conn()
    try:
        path, _count = export.export_events(conn)
    finally:
        conn.close()
    assert path.name == "events-2031-03-04-050607-2.jsonl"
    assert squatter.read_text(encoding="utf-8") == "not an export\n"


# --- nothing is half-written ----------------------------------------------


def test_a_failure_mid_write_leaves_no_partial_export(client, exports_dir, monkeypatch):
    """The old path wrote the whole text onto the final name; a failure there
    left a truncated file wearing the name of a finished backup."""
    def explode(conn):
        yield json.dumps({"id": "a", "type": "first"}) + "\n"
        yield json.dumps({"id": "b", "type": "second"}) + "\n"
        raise RuntimeError("export interrupted")

    monkeypatch.setattr(export, "iter_jsonl", explode)
    conn = _conn()
    try:
        with pytest.raises(RuntimeError, match="export interrupted"):
            export.export_events(conn)
    finally:
        conn.close()

    assert list(exports_dir.glob("events-*.jsonl")) == []
    # The staging file is cleaned up too, and could never be mistaken for an
    # export anyway: it is dot-prefixed and outside the events-*.jsonl glob.
    assert list(exports_dir.iterdir()) == []


def test_recent_exports_cannot_see_a_staging_file(client, exports_dir):
    """A leftover temp file must not surface on /export as a real backup."""
    (exports_dir / ".events-abc123.jsonl.tmp").write_text("partial", encoding="utf-8")
    conn = _conn()
    try:
        path, _count = export.export_events(conn)
    finally:
        conn.close()
    assert [entry["name"] for entry in export.recent_exports()] == [path.name]


def test_the_export_directory_is_synced_before_success_is_reported(
    client, exports_dir, monkeypatch
):
    """Fsyncing the file persists its bytes; the name they hang from is a
    directory change, and an unsynced one can vanish in a power loss after the
    route has already reported a durable backup."""
    synced_dirs: list[str] = []
    real_fsync = os.fsync

    def record(fd):
        if os.path.isdir(f"/proc/self/fd/{fd}"):
            synced_dirs.append(os.readlink(f"/proc/self/fd/{fd}"))
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", record)
    conn = _conn()
    try:
        export.export_events(conn)
    finally:
        conn.close()
    assert synced_dirs == [str(exports_dir)]


def test_the_finished_export_is_private(client, exports_dir):
    """Exports carry notes and check-in history (sec9): owner-only, always."""
    conn = _conn()
    try:
        path, _count = export.export_events(conn)
    finally:
        conn.close()
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_the_export_is_never_held_in_memory_whole(client, exports_dir):
    """iter_jsonl is a generator: the first line is available before the last
    row is read, which is what keeps the writer's footprint one line deep."""
    conn = _conn()
    try:
        stream = export.iter_jsonl(conn)
        first = next(stream)
        assert json.loads(first)["type"]
        stream.close()
    finally:
        conn.close()


# --- nothing is delivered twice -------------------------------------------


def test_every_audit_line_carries_its_row_uuid(client, exports_dir):
    conn = _conn()
    try:
        path, count = export.export_events(conn)
        stored = [row["uuid"] for row in conn.execute(
            "SELECT uuid FROM events ORDER BY id")]
    finally:
        conn.close()

    records = _lines(path)
    assert len(records) == count
    audit = [r for r in records if r["type"] != "calendar_event_series"]
    ids = [r["id"] for r in audit]
    assert ids == stored, "exported ids are the rows' own uuids, in row order"
    assert len(set(ids)) == len(ids), "every id is distinct"
    assert all(isinstance(value, str) and value for value in ids)


def test_id_is_the_first_key_of_an_audit_line(client, exports_dir):
    """Cosmetic but contractual: `id` leads the envelope, ahead of timestamp."""
    conn = _conn()
    try:
        path, _count = export.export_events(conn)
    finally:
        conn.close()
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert list(first)[:2] == ["id", "timestamp"]


def test_calendar_snapshots_carry_no_id(client, exports_dir):
    """Their identity is the series id already inside the payload, so restore
    upserts them rather than deduplicating on an envelope receipt."""
    response = client.post(
        "/calendar/events",
        data={"title": "Invented Export Series", "start_date": "2031-06-07",
              "all_day": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "flash=" not in (response.headers.get("location") or "")
    conn = _conn()
    try:
        path, _count = export.export_events(conn)
    finally:
        conn.close()
    snapshots = [r for r in _lines(path) if r["type"] == "calendar_event_series"]
    assert snapshots
    assert all("id" not in record for record in snapshots)
    assert all("id" in record["payload"] for record in snapshots)


def test_downloaded_export_is_byte_identical_to_the_file_on_disk(client, exports_dir):
    """The route sends the file itself, not a second rendering of it."""
    response = client.post("/export/jsonl")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")

    written = sorted(exports_dir.glob("events-*.jsonl"))
    assert len(written) == 1
    assert f'filename="{written[0].name}"' in response.headers["content-disposition"]
    assert response.content == written[0].read_bytes()


# --- restore: redelivery ---------------------------------------------------


def _restore(export_path: Path, target: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("ACTIVITY_DATA_DIR", None)
    env.pop("ACTIVITY_DB", None)
    env.pop("EPHEMERIS_ENABLE_TERMINAL", None)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "restore_from_export.py"),
         str(export_path), str(target)],
        cwd=ROOT, env=env, text=True, capture_output=True,
    )


def _query(db_path: Path, sql: str) -> list[tuple]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _counts(db_path: Path) -> dict[str, int]:
    return {
        table: _query(db_path, f"SELECT COUNT(*) FROM {table}")[0][0]
        for table in ("events", "routine_items", "checkins",
                      "daily_notes", "calendar_events")
    }


# A hand-written stream rather than this suite's own export: the cumulative test
# database is not fully replayable (it holds a cleared check-in whose upsert
# predates the events table's current shape), and redelivery is a property of the
# importer, which deserves an input it fully controls. verify_restore.py proves
# the same property end to end on the real app's curated fixture data.
_STREAM = [
    {
        "id": "11111111-1111-4111-8111-111111111111",
        "timestamp": "2031-05-06T07:08:09+03:00",
        "type": "routine_item_created",
        "payload_version": 1,
        "payload": {"routine_item_id": 4001, "title": "Invented Redelivery Walk",
                    "group_name": "Invented Routine", "sort_order": 10},
    },
    {
        "id": "22222222-2222-4222-8222-222222222222",
        "timestamp": "2031-05-06T07:09:09+03:00",
        "type": "routine_checkin_upserted",
        "payload_version": 1,
        "payload": {"date": "2031-05-06", "routine_item_id": 4001,
                    "status": "full_done", "note": "Invented note"},
    },
    {
        "id": "33333333-3333-4333-8333-333333333333",
        "timestamp": "2031-05-06T07:10:09+03:00",
        "type": "daily_note_updated",
        "payload_version": 1,
        "payload": {"date": "2031-05-06", "text": "Invented daily note"},
    },
]


def _write_stream(path: Path, records: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def test_redelivering_the_same_export_applies_nothing(tmp_path):
    source = _write_stream(tmp_path / "stream.jsonl", _STREAM)
    target = tmp_path / "restored"

    first = _restore(source, target)
    assert first.returncode == 0, first.stderr
    assert "MODE: FRESH build" in first.stdout
    assert "events: 3 applied / 0 skipped" in first.stdout
    assert "IDEMPOTENT REDELIVERY: YES" in first.stdout
    after_first = _counts(target / "activity.sqlite")
    assert after_first["events"] == 3
    assert after_first["routine_items"] == 1
    assert after_first["checkins"] == 1
    assert after_first["daily_notes"] == 1

    second = _restore(source, target)
    assert second.returncode == 0, second.stderr
    assert "MODE: REDELIVERY into an existing database" in second.stdout
    assert "events: 0 applied / 3 skipped" in second.stdout
    assert _counts(target / "activity.sqlite") == after_first, "no row was duplicated"


def test_redelivery_preserves_the_original_event_ids(tmp_path):
    """The receipt has to survive the restore, or the second run cannot match."""
    source = _write_stream(tmp_path / "stream.jsonl", _STREAM)
    target = tmp_path / "restored-ids"
    assert _restore(source, target).returncode == 0

    stored = [row[0] for row in _query(
        target / "activity.sqlite", "SELECT uuid FROM events ORDER BY id")]
    assert stored == [record["id"] for record in _STREAM]


def test_a_replayed_record_does_not_re_run_its_replay(tmp_path):
    """Skipping the insert must skip the replay: a second delivery of the item's
    creation would otherwise collide on its primary key, and a second delivery of
    a deletion would remove state the first one already accounted for."""
    source = _write_stream(tmp_path / "stream.jsonl", _STREAM)
    target = tmp_path / "restored-replay"
    assert _restore(source, target).returncode == 0

    edited = _query(target / "activity.sqlite",
                    "SELECT title FROM routine_items WHERE id = 4001")
    assert edited == [("Invented Redelivery Walk",)]
    assert _restore(source, target).returncode == 0
    assert _query(target / "activity.sqlite",
                  "SELECT id, title FROM routine_items") == [
        (4001, "Invented Redelivery Walk")]


def test_redelivery_upserts_calendar_snapshots_instead_of_duplicating(tmp_path):
    """A snapshot is complete state keyed by the series id, so a later export of
    the same series must update the row, never add a second one."""
    source = _write_stream(tmp_path / "stream.jsonl", _STREAM)
    target = tmp_path / "restored-calendar"
    assert _restore(source, target).returncode == 0

    columns = [row[1] for row in _query(
        target / "activity.sqlite", "PRAGMA table_info(calendar_events)")]
    series = dict.fromkeys(columns)
    series.update({"id": 7001, "title": "Invented Series", "all_day": 0,
                   "freq": "once", "interval_n": 1, "start_date": "2031-05-06",
                   "created_at": "2031-05-06T07:00:00+03:00"})
    snapshot = {"timestamp": "2031-05-06T08:00:00+03:00",
                "type": "calendar_event_series", "payload_version": 1,
                "payload": series}
    _write_stream(source, [*_STREAM, snapshot])
    assert _restore(source, target).returncode == 0
    assert _query(target / "activity.sqlite",
                  "SELECT id, title FROM calendar_events") == [(7001, "Invented Series")]

    revised = {**snapshot, "payload": {**series, "title": "Invented Series Revised"}}
    _write_stream(source, [*_STREAM, revised])
    assert _restore(source, target).returncode == 0
    assert _query(target / "activity.sqlite",
                  "SELECT id, title FROM calendar_events") == [
        (7001, "Invented Series Revised")]


def test_a_pre_id_export_still_restores_into_a_fresh_target(tmp_path):
    """Backward compatibility: files written before #17 have no `id` at all."""
    legacy = tmp_path / "legacy-export.jsonl"
    legacy.write_text(
        json.dumps({
            "timestamp": "2024-05-06T07:08:09+03:00",
            "type": "daily_note_updated",
            "payload_version": 1,
            "payload": {"date": "2024-05-06", "text": "Invented legacy note"},
        }) + "\n",
        encoding="utf-8",
    )
    target = tmp_path / "restored-legacy"
    run = _restore(legacy, target)

    assert run.returncode == 0, run.stderr
    assert "IDEMPOTENT REDELIVERY: NO" in run.stdout
    assert "events: 1 applied / 0 skipped" in run.stdout
    assert _counts(target / "activity.sqlite")["daily_notes"] == 1


def test_a_pre_id_export_is_refused_on_redelivery(tmp_path):
    """Without receipts there is no way to tell a replay from new history, so
    the old fresh-target-only rule still governs those files."""
    legacy = tmp_path / "legacy-twice.jsonl"
    legacy.write_text(
        json.dumps({
            "timestamp": "2024-05-06T07:08:09+03:00",
            "type": "daily_note_updated",
            "payload_version": 1,
            "payload": {"date": "2024-05-06", "text": "Invented legacy note"},
        }) + "\n",
        encoding="utf-8",
    )
    target = tmp_path / "restored-legacy-twice"
    assert _restore(legacy, target).returncode == 0

    again = _restore(legacy, target)
    assert again.returncode != 0
    assert 'carry no "id"' in again.stderr
    assert _counts(target / "activity.sqlite")["daily_notes"] == 1


def test_a_legacy_restored_target_refuses_a_modern_redelivery(tmp_path):
    """The trap behind the fresh-target rule: restoring a pre-#17 export mints
    NEW local uuids, so a later export of the same source database shares none of
    them. Every record would read as new and the whole history would land twice."""
    legacy = _write_stream(
        tmp_path / "legacy.jsonl",
        [{k: v for k, v in record.items() if k != "id"} for record in _STREAM],
    )
    target = tmp_path / "restored-legacy-then-modern"
    assert _restore(legacy, target).returncode == 0
    before = _counts(target / "activity.sqlite")

    modern = _write_stream(tmp_path / "modern.jsonl", _STREAM)
    run = _restore(modern, target)

    assert run.returncode != 0
    assert "shares no event id with this export" in run.stderr
    assert _counts(target / "activity.sqlite") == before, "nothing was applied"


def test_an_unrelated_target_is_refused_before_any_snapshot_lands(tmp_path):
    """Holding a database is not evidence of shared history. Calendar series ids
    are small integers that collide readily, and a snapshot upsert onto someone
    else's series id is silent data loss."""
    other = _write_stream(tmp_path / "other.jsonl", [{
        "id": "99999999-9999-4999-8999-999999999999",
        "timestamp": "2031-05-06T07:08:09+03:00",
        "type": "daily_note_updated",
        "payload_version": 1,
        "payload": {"date": "2031-05-06", "text": "A different history"},
    }])
    target = tmp_path / "restored-other-history"
    assert _restore(other, target).returncode == 0

    columns = [row[1] for row in _query(
        target / "activity.sqlite", "PRAGMA table_info(calendar_events)")]
    series = dict.fromkeys(columns)
    series.update({"id": 1, "title": "Invented Intruder", "all_day": 0,
                   "freq": "once", "interval_n": 1, "start_date": "2031-05-06",
                   "created_at": "2031-05-06T07:00:00+03:00"})
    intruder = _write_stream(tmp_path / "intruder.jsonl", [*_STREAM, {
        "timestamp": "2031-05-06T08:00:00+03:00",
        "type": "calendar_event_series", "payload_version": 1, "payload": series,
    }])

    run = _restore(intruder, target)
    assert run.returncode != 0
    assert "shares no event id with this export" in run.stderr
    assert _query(target / "activity.sqlite", "SELECT COUNT(*) FROM calendar_events") \
        == [(0,)]
    assert _query(target / "activity.sqlite",
                  "SELECT text FROM daily_notes") == [("A different history",)]


def test_an_older_export_cannot_revert_a_target_that_is_ahead(tmp_path):
    """Snapshots are absolute state, not a diff: they upsert whatever the file
    says even when every audit line is skipped. Delivering an older export into a
    newer target would report "0 applied" — reading as "nothing changed" — while
    quietly reverting calendar titles, recurrence and archive state."""
    columns_probe = tmp_path / "probe"
    assert _restore(_write_stream(tmp_path / "s.jsonl", _STREAM), columns_probe).returncode == 0
    columns = [row[1] for row in _query(
        columns_probe / "activity.sqlite", "PRAGMA table_info(calendar_events)")]

    def series(title):
        row = dict.fromkeys(columns)
        row.update({"id": 7001, "title": title, "all_day": 0, "freq": "once",
                    "interval_n": 1, "start_date": "2031-05-06",
                    "created_at": "2031-05-06T07:00:00+03:00"})
        return {"timestamp": "2031-05-06T08:00:00+03:00",
                "type": "calendar_event_series", "payload_version": 1, "payload": row}

    older = _write_stream(tmp_path / "older.jsonl", [*_STREAM, series("Original Title")])
    newer = _write_stream(tmp_path / "newer.jsonl", [*_STREAM, {
        "id": "55555555-5555-4555-8555-555555555555",
        "timestamp": "2031-05-08T07:08:09+03:00",
        "type": "daily_note_updated",
        "payload_version": 1,
        "payload": {"date": "2031-05-08", "text": "Invented later note"},
    }, series("Renamed Later")])

    target = tmp_path / "restored-ahead"
    assert _restore(newer, target).returncode == 0
    assert _query(target / "activity.sqlite",
                  "SELECT title FROM calendar_events") == [("Renamed Later",)]

    run = _restore(older, target)
    assert run.returncode != 0
    assert "ahead of this file or has diverged" in run.stderr
    assert _query(target / "activity.sqlite",
                  "SELECT title FROM calendar_events") == [("Renamed Later",)], \
        "the older snapshot must not have reverted the series"


def test_a_receipt_on_two_different_events_is_refused(tmp_path):
    """A damaged or concatenated export can put one id on two envelopes. Skipping
    the second as 'already delivered' would discard a real event and its replay."""
    collided = _write_stream(tmp_path / "collided.jsonl", [
        _STREAM[0],
        {**_STREAM[2], "id": _STREAM[0]["id"]},
    ])
    run = _restore(collided, tmp_path / "restored-collision")
    assert run.returncode != 0
    assert "is already present in the target with a different envelope" in run.stderr


def test_an_identical_duplicate_line_is_still_idempotent(tmp_path):
    """The collision check compares the envelope, so a genuinely repeated line —
    the concatenation of a file with itself — is still just a skip."""
    doubled = _write_stream(tmp_path / "doubled.jsonl", [*_STREAM, *_STREAM])
    target = tmp_path / "restored-doubled"
    run = _restore(doubled, target)
    assert run.returncode == 0, run.stderr
    assert "events: 3 applied / 3 skipped" in run.stdout
    assert _counts(target / "activity.sqlite")["events"] == 3


def test_the_summary_never_claims_zero_over_pre_existing_rows(tmp_path):
    """`lists`/`tasks`/`tags` are deliberately not restored. In redelivery mode
    the target may already hold real rows, and reporting them as 0 makes a
    recovery report materially false."""
    source = _write_stream(tmp_path / "stream.jsonl", _STREAM)
    target = tmp_path / "restored-with-rows"
    first = _restore(source, target)
    assert first.returncode == 0
    assert "  lists: 0 rows" in first.stdout

    import sqlite3

    conn = sqlite3.connect(target / "activity.sqlite")
    try:
        conn.execute(
            "INSERT INTO lists (id, name, kind, sort_order, created_at) "
            "VALUES (91, 'Invented', 'list', 10, '2031-05-06T07:00:00+03:00')"
        )
        conn.commit()
    finally:
        conn.close()

    second = _restore(source, target)
    assert second.returncode == 0, second.stderr
    assert "  lists: 1 pre-existing rows (untouched)" in second.stdout
    assert "  lists: 0 rows" not in second.stdout
    assert _query(target / "activity.sqlite", "SELECT name FROM lists") == [("Invented",)]


def test_a_corrupt_id_is_refused_rather_than_ignored(tmp_path):
    """Ignoring an unusable receipt would turn a redelivery into a duplicate."""
    bad = tmp_path / "bad-id-export.jsonl"
    bad.write_text(
        json.dumps({
            "id": 17,
            "timestamp": "2024-05-06T07:08:09+03:00",
            "type": "daily_note_updated",
            "payload_version": 1,
            "payload": {"date": "2024-05-06", "text": "Invented note"},
        }) + "\n",
        encoding="utf-8",
    )
    run = _restore(bad, tmp_path / "restored-bad-id")
    assert run.returncode != 0
    assert "id must be a non-empty string" in run.stderr


def test_redelivery_applies_only_the_records_that_are_new(tmp_path):
    """The point of the receipt: an export taken later shares a prefix with the
    earlier one, and only its tail should land."""
    source = _write_stream(tmp_path / "stream.jsonl", _STREAM)
    target = tmp_path / "restored-incremental"
    assert _restore(source, target).returncode == 0

    grown = [*_STREAM, {
        "id": "44444444-4444-4444-8444-444444444444",
        "timestamp": "2031-05-07T07:08:09+03:00",
        "type": "daily_note_updated",
        "payload_version": 1,
        "payload": {"date": "2031-05-07", "text": "Invented note after the first export"},
    }]
    _write_stream(source, grown)

    run = _restore(source, target)
    assert run.returncode == 0, run.stderr
    assert "events: 1 applied / 3 skipped" in run.stdout
    assert _counts(target / "activity.sqlite")["events"] == 4
    # The tail was replayed, not merely stored: the new note reached typed state.
    assert _query(
        target / "activity.sqlite",
        "SELECT text FROM daily_notes WHERE date = '2031-05-07'",
    ) == [("Invented note after the first export",)]
