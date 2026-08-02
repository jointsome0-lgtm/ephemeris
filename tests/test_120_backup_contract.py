"""The full backup as a contract, and the marker that makes a restore stick (#17).

The JSONL export is an audit stream; it is honest about what it cannot carry
(docs/restore-from-export.md). This module covers the other half — the full
backup, which is the database itself plus the lesson bundles beside it — and the
one property both halves depend on: a restored installation must not be mistaken
for a brand new one.

Two failure modes are pinned here:

*A backup that only looks like one.* A snapshot written straight onto its final
name is indistinguishable, to anyone reading `data/backups/` a month later, from
one that was interrupted halfway. So the set is staged, checksummed, and claimed
by rename, with the manifest written last: the manifest's presence IS the claim
that the set is whole.

*A restore that quietly grows demo data.* Startup used to decide seeding per
table from `COUNT(*) == 0`, which cannot tell "never initialized" from "restored
from a ledger whose owner had deleted every task".
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Enter and leave the real lifespan: migrations, the seeding decision, and the
# runner service, exactly as `uvicorn app.main:app` would run them. In-process
# is not an option — app/settings.py resolves ACTIVITY_DATA_DIR once at import,
# so a second data directory needs a second interpreter.
_BOOT = """
import sys
sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app):
    pass
"""


def _app_env(data_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["ACTIVITY_DATA_DIR"] = str(data_dir)
    env.pop("ACTIVITY_DB", None)
    env.pop("EPHEMERIS_ENABLE_TERMINAL", None)  # opt-in; stay at the default (off)
    env["EPHEMERIS_TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1,::1"
    return env


def boot_app(data_dir: Path) -> subprocess.CompletedProcess[str]:
    """Start and stop the real app against `data_dir`."""
    return subprocess.run(
        [sys.executable, "-c", _BOOT, str(ROOT)],
        cwd=ROOT, env=_app_env(data_dir), text=True, capture_output=True,
    )


def query(data_dir: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(data_dir / "activity.sqlite")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def counts(data_dir: Path) -> dict[str, int]:
    return {
        table: query(data_dir, f"SELECT COUNT(*) FROM {table}")[0][0]
        for table in ("routine_items", "lists", "tasks", "events")
    }


# --- the initialization marker --------------------------------------------


def test_a_fresh_data_dir_is_seeded_exactly_once(tmp_path):
    """The marker must not cost the first-run experience: an empty directory
    still gets its demo habits, lists and tasks, and gets them one time."""
    data_dir = tmp_path / "fresh"
    assert boot_app(data_dir).returncode == 0
    after_first = counts(data_dir)
    assert after_first["lists"] > 0 and after_first["tasks"] > 0

    marker = query(data_dir, "SELECT value FROM app_meta WHERE key = 'seeded_at'")
    assert marker and marker[0][0], "the first boot records that it seeded"

    assert boot_app(data_dir).returncode == 0
    assert counts(data_dir) == after_first, "a second boot adds nothing"


def test_a_restored_ledger_with_empty_tables_is_never_reseeded(tmp_path):
    """The regression this whole marker exists for.

    A restored database can legitimately hold real history and empty `lists`
    and `tasks` — either because the owner deleted them, or because the JSONL
    restore deliberately leaves insufficiently journaled tables empty. Row
    counts cannot tell that apart from a first run, so the old startup poured
    demo lists and tasks into restored history and appended their events to
    the audit stream. This test fails on the pre-#17 startup path.
    """
    data_dir = tmp_path / "restored"
    assert boot_app(data_dir).returncode == 0
    conn = sqlite3.connect(data_dir / "activity.sqlite")
    try:
        conn.execute("DELETE FROM tasks")  # before lists: tasks.list_id references it
        conn.execute("DELETE FROM lists")
        conn.commit()
    finally:
        conn.close()
    before = counts(data_dir)
    assert before["lists"] == 0 and before["tasks"] == 0

    assert boot_app(data_dir).returncode == 0

    after = counts(data_dir)
    assert after["lists"] == 0, "no demo lists reappear in a restored ledger"
    assert after["tasks"] == 0, "no demo tasks reappear in a restored ledger"
    assert after["events"] == before["events"], (
        "and nothing is appended to the audit stream the restore preserved"
    )


def test_deleting_every_habit_survives_a_restart(tmp_path):
    """Same defect through the other seeder: `routine_items` is the table
    `checkins.seed_if_empty` counts, and an owner who retires every habit is
    not asking for the demo ones back on the next restart."""
    data_dir = tmp_path / "no-habits"
    assert boot_app(data_dir).returncode == 0
    conn = sqlite3.connect(data_dir / "activity.sqlite")
    try:
        conn.execute("DELETE FROM checkins")
        conn.execute("DELETE FROM routine_items")
        conn.commit()
    finally:
        conn.close()

    assert boot_app(data_dir).returncode == 0
    assert counts(data_dir)["routine_items"] == 0


# --- the full backup set ---------------------------------------------------


@pytest.fixture
def instance(tmp_path):
    """A real Ephemeris data directory: a booted ledger plus a lesson bundle."""
    data_dir = tmp_path / "instance"
    assert boot_app(data_dir).returncode == 0
    bundle = data_dir / "lessons" / "demo-slug"
    bundle.mkdir(parents=True)
    (bundle / "lesson.json").write_text(
        json.dumps({"version": 2, "title": "Invented Demo Lesson"}), encoding="utf-8"
    )
    (bundle / "index.html").write_text("<h1>Invented Demo</h1>\n", encoding="utf-8")
    return data_dir


@pytest.fixture
def backup_db(monkeypatch, instance):
    """scripts/backup_db bound to `instance`.

    The module resolves its paths from app.db at import, which under pytest
    means the shared suite directory. Rebinding the module attributes is what
    points a real backup run at a throwaway instance.
    """
    sys.path.insert(0, str(ROOT))
    import scripts.backup_db as module

    monkeypatch.setattr(module, "DB_PATH", instance / "activity.sqlite")
    monkeypatch.setattr(module, "DATA_DIR", instance)
    monkeypatch.setattr(module, "LESSONS_DIR", instance / "lessons")
    monkeypatch.setattr(module, "BACKUPS_DIR", instance / "backups")
    return module


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_backup_is_three_files_that_agree_with_each_other(backup_db, instance):
    manifest_path = backup_db.create_backup()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    backups = instance / "backups"

    stamp = manifest["stamp"]
    members = [
        backups / backup_db.db_name(stamp),
        backups / backup_db.lessons_name(stamp),
        manifest_path,
    ]
    assert manifest_path.name == backup_db.manifest_name(stamp)
    assert all(path.is_file() for path in members), "all three files land together"
    assert sorted(p.name for p in backups.iterdir()) == sorted(p.name for p in members), (
        "and nothing else is left behind — no staging file, no placeholder"
    )

    assert manifest["manifest_version"] == backup_db.MANIFEST_VERSION
    assert manifest["schema_version"] == query(instance, "PRAGMA user_version")[0][0]
    assert manifest["created_at"]
    for role, path in (("database", members[0]), ("lessons", members[1])):
        entry = manifest["files"][role]
        assert entry["name"] == path.name
        assert entry["bytes"] == path.stat().st_size
        assert entry["sha256"] == backup_db.sha256_of(path)
    assert sorted(manifest["lesson_files"]) == [
        "demo-slug/index.html", "demo-slug/lesson.json",
    ]

    assert all(mode_of(path) == 0o600 for path in members), (
        "a backup is a whole copy of a private ledger; nobody else may read it"
    )
    assert mode_of(backups) == 0o700

    assert backup_db.verify(manifest_path)["stamp"] == stamp


def test_two_backups_in_one_second_are_two_backups(backup_db, monkeypatch):
    """`now_stamp()` resolves to the second — the same collision the JSONL
    export had, where the second write silently replaced the first."""
    monkeypatch.setattr(backup_db, "now_stamp", lambda: "2031-01-02-030405")
    first = backup_db.create_backup()
    second = backup_db.create_backup()
    assert first.name == "activity-2031-01-02-030405.manifest.json"
    assert second.name == "activity-2031-01-02-030405-2.manifest.json"
    assert first.exists() and second.exists()
    assert len(backup_db.list_sets()) == 2
    for path in (first, second):
        backup_db.verify(path)


def test_verify_catches_a_backup_that_rotted_on_disk(backup_db, instance):
    """The point of the checksums: damage is found while the source still
    exists, not on the day the backup is needed."""
    manifest_path = backup_db.create_backup()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot = instance / "backups" / manifest["files"]["database"]["name"]

    with open(snapshot, "r+b") as handle:
        handle.seek(handle.seek(0, os.SEEK_END) // 2)
        handle.write(b"\xde\xad\xbe\xef")

    with pytest.raises(backup_db.BackupError) as raised:
        backup_db.verify(manifest_path)
    assert "checksum mismatch" in str(raised.value)


def test_verify_catches_a_truncated_backup(backup_db, instance):
    manifest_path = backup_db.create_backup()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive = instance / "backups" / manifest["files"]["lessons"]["name"]
    with open(archive, "r+b") as handle:
        handle.truncate(handle.seek(0, os.SEEK_END) // 2)

    with pytest.raises(backup_db.BackupError) as raised:
        backup_db.verify(manifest_path)
    assert "bytes, manifest says" in str(raised.value)


def test_verify_refuses_a_manifest_from_a_future_format(backup_db, instance):
    manifest_path = backup_db.create_backup()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manifest_version"] = backup_db.MANIFEST_VERSION + 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(backup_db.BackupError):
        backup_db.verify(manifest_path)


def test_an_interrupted_run_leaves_nothing_a_restore_will_accept(
    backup_db, instance, tmp_path
):
    """The manifest is written last, so a run that dies before it produces
    debris — never a set that reads as complete."""
    def boom(dest, names):
        raise OSError("simulated failure between the snapshot and the manifest")

    intact = backup_db.archive_lessons
    backup_db.archive_lessons = boom
    try:
        with pytest.raises(OSError):
            backup_db.create_backup()
    finally:
        backup_db.archive_lessons = intact

    backups = instance / "backups"
    assert backup_db.list_sets() == [], "no manifest, so no backup exists"
    assert not list(backups.glob("*.manifest.json"))
    assert list(backups.iterdir()), "the placeholder snapshot IS left behind"
    for path in backups.iterdir():
        with pytest.raises(backup_db.BackupError):
            backup_db.restore(path, tmp_path / "nowhere")

    # And the debris is swept by the next successful run's retention pass.
    backup_db.create_backup()
    stamps, deleted = backup_db.prune(1)
    assert stamps == [], "the only complete set is kept"
    assert deleted, "the interrupted run's leftovers are removed"
    assert len(backup_db.list_sets()) == 1
    backup_db.verify(backup_db.list_sets()[0])


def test_keep_prunes_whole_sets_newest_first(backup_db, instance, monkeypatch):
    stamps = [f"2031-03-0{day}-000000" for day in range(1, 6)]
    for stamp in stamps:
        monkeypatch.setattr(backup_db, "now_stamp", lambda s=stamp: s)
        backup_db.create_backup()
    assert len(backup_db.list_sets()) == 5

    dropped, _ = backup_db.prune(2)

    assert dropped == stamps[:3]
    survivors = [path.name for path in backup_db.list_sets()]
    assert survivors == [backup_db.manifest_name(s) for s in stamps[3:]]
    assert sorted(p.name for p in (instance / "backups").iterdir()) == sorted(
        name
        for stamp in stamps[3:]
        for name in (backup_db.db_name(stamp), backup_db.lessons_name(stamp),
                     backup_db.manifest_name(stamp))
    ), "sets are pruned in threes; no orphaned snapshot or archive survives"
    for path in backup_db.list_sets():
        backup_db.verify(path)


def test_keep_zero_prunes_nothing(backup_db, monkeypatch):
    for stamp in ("2031-04-01-000000", "2031-04-02-000000"):
        monkeypatch.setattr(backup_db, "now_stamp", lambda s=stamp: s)
        backup_db.create_backup()
    assert backup_db.prune(0) == ([], [])
    assert len(backup_db.list_sets()) == 2


# --- restoring -------------------------------------------------------------


def test_restore_rebuilds_an_instance_that_boots_without_migrating_or_seeding(
    backup_db, instance, tmp_path
):
    """The whole contract in one pass: a set written from a live instance,
    restored into an empty directory, opens as the same instance — same schema
    version, same rows, same lesson bundles — and a real app boot on it adds
    nothing."""
    manifest_path = backup_db.create_backup()
    before = counts(instance)
    schema_before = query(instance, "PRAGMA user_version")[0][0]

    target = tmp_path / "recovered"
    result = backup_db.restore(manifest_path, target)

    assert result["moved_aside"] == []
    assert mode_of(target / "activity.sqlite") == 0o600
    assert counts(target) == before
    assert query(target, "PRAGMA user_version")[0][0] == schema_before
    assert (target / "lessons" / "demo-slug" / "lesson.json").read_text(
        encoding="utf-8"
    ) == (instance / "lessons" / "demo-slug" / "lesson.json").read_text(encoding="utf-8")

    assert boot_app(target).returncode == 0
    assert counts(target) == before, "a restored instance is already initialized"
    assert query(target, "PRAGMA user_version")[0][0] == schema_before, (
        "and already at the running schema, so the boot migrates nothing"
    )
    assert query(target, "SELECT value FROM app_meta WHERE key = 'seeded_at'") == (
        query(instance, "SELECT value FROM app_meta WHERE key = 'seeded_at'")
    ), "including the initialization time it was originally given"


def test_restore_refuses_an_occupied_target_until_told_otherwise(
    backup_db, instance, tmp_path
):
    manifest_path = backup_db.create_backup()
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "activity.sqlite").write_text("someone else's ledger", encoding="utf-8")

    with pytest.raises(backup_db.BackupError) as raised:
        backup_db.restore(manifest_path, target)
    assert "--force" in str(raised.value)
    assert (target / "activity.sqlite").read_text(encoding="utf-8") == (
        "someone else's ledger"
    ), "a refused restore writes nothing at all"


def test_force_keeps_what_it_replaces(backup_db, instance, tmp_path):
    """A restore aimed at the wrong directory has to stay recoverable, so the
    files it displaces are moved aside — including the WAL sidecars, which
    would otherwise be replayed into the restored database."""
    manifest_path = backup_db.create_backup()
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "activity.sqlite").write_text("someone else's ledger", encoding="utf-8")
    (target / "activity.sqlite-wal").write_text("stale wal", encoding="utf-8")
    (target / "lessons").mkdir()
    (target / "lessons" / "theirs.txt").write_text("theirs", encoding="utf-8")

    result = backup_db.restore(manifest_path, target, force=True)

    assert len(result["moved_aside"]) == 3
    kept = {path.name: path for path in target.iterdir() if ".pre-restore-" in path.name}
    assert len(kept) == 3
    assert any(
        path.read_text(encoding="utf-8") == "someone else's ledger"
        for path in kept.values() if path.is_file()
    )
    assert not (target / "activity.sqlite-wal").exists(), "no stale WAL survives"
    assert not (target / "lessons" / "theirs.txt").exists()
    assert (target / "lessons" / "demo-slug" / "index.html").is_file()
    assert counts(target) == counts(instance)


def test_restore_refuses_a_damaged_set_before_touching_the_target(
    backup_db, instance, tmp_path
):
    manifest_path = backup_db.create_backup()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshot = instance / "backups" / manifest["files"]["database"]["name"]
    with open(snapshot, "r+b") as handle:
        handle.seek(handle.seek(0, os.SEEK_END) // 2)
        handle.write(b"\x00" * 64)

    target = tmp_path / "recovered"
    with pytest.raises(backup_db.BackupError):
        backup_db.restore(manifest_path, target)
    assert not (target / "activity.sqlite").exists()


def test_an_instance_without_lessons_still_produces_a_three_file_set(
    backup_db, instance
):
    """So a reader never has to tell "no lessons" from "the file went missing"."""
    for path in sorted((instance / "lessons").rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    (instance / "lessons").rmdir()

    manifest_path = backup_db.create_backup()
    manifest = backup_db.verify(manifest_path)
    assert manifest["lesson_files"] == []
    assert (instance / "backups" / manifest["files"]["lessons"]["name"]).is_file()
