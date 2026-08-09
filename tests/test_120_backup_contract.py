"""The full backup as a contract, and the marker that makes a restore stick (#17).

The JSONL export is an audit stream; it is honest about what it cannot carry
(docs/restore-from-export.md). This module covers the other half — the full
backup, which is the database itself plus every file beside it — and the
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
import time
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
    assert after["tasks"] == 0, "no demo tasks reappear in a restored ledger"
    assert query(data_dir, "SELECT name, kind FROM lists") == [("Inbox", "inbox")], (
        "the Inbox is rebuilt because two read routes require it, and nothing "
        "else: none of the five demo lists comes back"
    )
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

    Live paths are resolved lazily so recovery commands can run without app
    configuration. Rebinding the module attributes points a real backup run at
    a throwaway instance without importing the shared suite's settings.
    """
    sys.path.insert(0, str(ROOT))
    import scripts.backup_db as module

    monkeypatch.setattr(module, "DB_PATH", instance / "activity.sqlite")
    monkeypatch.setattr(module, "DATA_DIR", instance)
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
        backups / backup_db.instance_name(stamp),
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
    for role, path in (("database", members[0]), ("instance", members[1])):
        entry = manifest["files"][role]
        assert entry["name"] == path.name
        assert entry["bytes"] == path.stat().st_size
        assert entry["sha256"] == backup_db.sha256_of(path)
    assert sorted(manifest["instance_files"]) == [
        "lessons/demo-slug/index.html", "lessons/demo-slug/lesson.json",
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
    archive = instance / "backups" / manifest["files"]["instance"]["name"]
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

    intact = backup_db.archive_instance
    backup_db.archive_instance = boom
    try:
        with pytest.raises(OSError):
            backup_db.create_backup()
    finally:
        backup_db.archive_instance = intact

    backups = instance / "backups"
    assert backup_db.list_sets() == [], "no manifest, so no backup exists"
    assert not list(backups.glob("*.manifest.json"))
    assert list(backups.iterdir()), "the placeholder snapshot IS left behind"
    for path in backups.iterdir():
        with pytest.raises(backup_db.BackupError):
            backup_db.restore(path, tmp_path / "nowhere")

    # And the debris is swept by the next successful run's retention pass.
    backup_db.create_backup()
    stamps, deleted, left = backup_db.prune(1)
    assert stamps == [], "the only complete set is kept"
    assert deleted, "the abandoned name claim is removed"
    assert left == [], "and it was the only thing no manifest claimed"
    assert len(backup_db.list_sets()) == 1
    backup_db.verify(backup_db.list_sets()[0])


def test_keep_prunes_whole_sets_newest_first(backup_db, instance, monkeypatch):
    stamps = [f"2031-03-0{day}-000000" for day in range(1, 6)]
    for stamp in stamps:
        monkeypatch.setattr(backup_db, "now_stamp", lambda s=stamp: s)
        backup_db.create_backup()
    assert len(backup_db.list_sets()) == 5

    dropped, _, left = backup_db.prune(2)
    assert left == []

    assert dropped == stamps[:3]
    survivors = [path.name for path in backup_db.list_sets()]
    assert survivors == [backup_db.manifest_name(s) for s in stamps[3:]]
    assert sorted(p.name for p in (instance / "backups").iterdir()) == sorted(
        name
        for stamp in stamps[3:]
        for name in (backup_db.db_name(stamp), backup_db.instance_name(stamp),
                     backup_db.manifest_name(stamp))
    ), "sets are pruned in threes; no orphaned snapshot or archive survives"
    for path in backup_db.list_sets():
        backup_db.verify(path)


def test_keep_zero_prunes_nothing(backup_db, monkeypatch):
    for stamp in ("2031-04-01-000000", "2031-04-02-000000"):
        monkeypatch.setattr(backup_db, "now_stamp", lambda s=stamp: s)
        backup_db.create_backup()
    stamps, deleted, _ = backup_db.prune(0)
    assert (stamps, deleted) == ([], [])
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
    assert mode_of(target) == 0o700
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
    # Flip the bytes rather than zero them: the midpoint can land in a free page
    # that is already all zeros, and then "corruption" leaves the file — and its
    # checksum — byte-identical, so the test would pass itself.
    with open(snapshot, "r+b") as handle:
        middle = handle.seek(0, os.SEEK_END) // 2
        handle.seek(middle)
        original = handle.read(64)
        handle.seek(middle)
        handle.write(bytes(b ^ 0xFF for b in original))

    target = tmp_path / "recovered"
    with pytest.raises(backup_db.BackupError):
        backup_db.restore(manifest_path, target)
    assert not (target / "activity.sqlite").exists()


def test_an_instance_with_nothing_but_a_ledger_still_produces_three_files(
    backup_db, instance
):
    """So a reader never has to tell "there was nothing" from "it went missing"."""
    for path in sorted((instance / "lessons").rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    (instance / "lessons").rmdir()

    manifest_path = backup_db.create_backup()
    manifest = backup_db.verify(manifest_path)
    assert manifest["instance_files"] == []
    assert (instance / "backups" / manifest["files"]["instance"]["name"]).is_file()


# --- what the review round found -------------------------------------------


def test_retention_leaves_pre_manifest_snapshots_alone(
    backup_db, instance, monkeypatch
):
    """The earlier version of this script wrote `activity-<stamp>.sqlite` and no
    manifest at all. Those files are somebody's only restore points for
    everything older than this upgrade, and they are indistinguishable by name
    from an interrupted new-format run — so retention reports them instead of
    guessing."""
    backups = instance / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    legacy = backups / "activity-2020-01-01-000000.sqlite"
    legacy.write_bytes(b"SQLite format 3\x00" + b"\x00" * 512)

    for stamp in ("2031-05-01-000000", "2031-05-02-000000"):
        monkeypatch.setattr(backup_db, "now_stamp", lambda s=stamp: s)
        backup_db.create_backup()

    stamps, deleted, left = backup_db.prune(1)

    assert legacy.exists(), "a pre-manifest snapshot is never swept as debris"
    assert left == [legacy]
    assert legacy not in deleted
    assert stamps == ["2031-05-01-000000"]
    assert backup_db.unclaimed_files() == [legacy]


def test_a_lesson_file_that_vanishes_mid_run_is_recorded_not_guessed_at(
    backup_db, instance, monkeypatch
):
    """The lesson tree is not frozen while a backup runs. A file enumerated and
    then rewritten away must leave the manifest describing what the archive
    actually holds — the property --verify depends on."""
    names = backup_db.instance_files() + ["lessons/demo-slug/gone-before-we-read-it.html"]
    monkeypatch.setattr(backup_db, "instance_files", lambda: names)
    manifest_path = backup_db.create_backup()

    manifest = backup_db.verify(manifest_path)
    assert manifest["instance_files_vanished"] == [
        "lessons/demo-slug/gone-before-we-read-it.html"
    ]
    assert (
        "lessons/demo-slug/gone-before-we-read-it.html"
        not in manifest["instance_files"]
    )
    assert sorted(manifest["instance_files"]) == [
        "lessons/demo-slug/index.html", "lessons/demo-slug/lesson.json",
    ]


def test_verify_catches_an_archive_that_lost_a_lesson(backup_db, instance):
    """A checksum over a tar cannot tell you the tar holds what the manifest
    lists, so verify opens it and compares."""
    manifest_path = backup_db.create_backup()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["instance_files"].append("lessons/demo-slug/never-archived.html")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(backup_db.BackupError) as raised:
        backup_db.verify(manifest_path)
    assert "does not hold what the manifest lists" in str(raised.value)


def test_a_second_forced_restore_in_one_second_keeps_both_copies(
    backup_db, instance, tmp_path, monkeypatch
):
    """`now_stamp()` resolves to the second, so an immediate retry would reuse
    the first attempt's aside names and os.replace over the copies it had just
    preserved — deleting the data the --force promise says it keeps."""
    manifest_path = backup_db.create_backup()
    monkeypatch.setattr(backup_db, "now_stamp", lambda: "2031-06-01-000000")
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "activity.sqlite").write_text("first ledger", encoding="utf-8")
    first = backup_db.restore(manifest_path, target, force=True)
    second = backup_db.restore(manifest_path, target, force=True)

    assert first["moved_aside"] != second["moved_aside"], "distinct aside names"
    preserved = {
        path.read_text(encoding="utf-8", errors="replace")[:12]
        for path in target.iterdir()
        if ".pre-restore-" in path.name and path.is_file()
    }
    assert "first ledger" in preserved, "the original is still there after retry #2"
    assert counts(target) == counts(instance)


def test_the_archive_carries_every_directory_the_instance_grew(backup_db, instance):
    """Enumerated by exclusion, not by a list of known names.

    `lessons/` was the obvious one. `migrations/` holds the only input
    `migrate_bundles --rollback` accepts, `lessons-attic/` holds retired bundles,
    and the next feature will add another — a backup that has to be edited each
    time one appears is silently incomplete in between. Its own directory and
    the exports (regenerated from the database that is already in the set) are
    the only things left out.
    """
    (instance / "migrations" / "v1v2-2031-01-01-000000").mkdir(parents=True)
    (instance / "migrations" / "v1v2-2031-01-01-000000" / "rollback.json").write_text(
        json.dumps({"invented": True}), encoding="utf-8"
    )
    (instance / "lessons-attic").mkdir()
    (instance / "lessons-attic" / "retired.json").write_text("{}", encoding="utf-8")
    (instance / "exports").mkdir(exist_ok=True)
    (instance / "exports" / "events-2031-01-01-000000.jsonl").write_text(
        "", encoding="utf-8"
    )

    manifest = backup_db.verify(backup_db.create_backup())
    archived = set(manifest["instance_files"])

    assert "migrations/v1v2-2031-01-01-000000/rollback.json" in archived
    assert "lessons-attic/retired.json" in archived
    assert "lessons/demo-slug/lesson.json" in archived
    assert not any(name.startswith("exports/") for name in archived), (
        "exports are generated from the database that is already in the set"
    )
    assert not any(name.startswith("backups/") for name in archived), (
        "and the backup directory would otherwise nest every set in the next"
    )
    assert not any(name.startswith("activity.sqlite") for name in archived), (
        "the snapshot is the consistent copy of the database and its sidecars"
    )
    assert manifest["excluded"] == [
        "backups/", "exports/", "lesson-builds/*/node_modules/",
        "*.pre-restore-*", ".restore-tmp-*", "activity.sqlite*",
    ]


def test_the_installed_packages_are_skipped_and_their_manifest_is_not(
    backup_db, instance, tmp_path
):
    """A package tree is links as much as files, and this archive carries no link.

    `lesson-builds/<slug>/node_modules` is where a lesson build installs its
    packages. Carrying its regular files while dropping its `.bin/` shims — which
    are symlinks, as internal package links normally are — restored a package set
    that looked whole and did not run, and the manifest recorded the links
    neither as archived nor as excluded, so verification passed against an
    already incomplete list.

    So the installed copy is skipped and said out loud. Only the installed copy:
    the `package.json` and lockfile beside it are the record of what the lesson
    added and the input `bun install` needs, and a restore that dropped them
    would leave the rebuild with nothing to reinstall FROM — a restored lesson
    whose own sources import a package it can no longer name.
    """
    workspace = instance / "lesson-builds" / "invented-lesson"
    tree = workspace / "node_modules"
    (tree / "demo-tool").mkdir(parents=True)
    (tree / "demo-tool" / "index.js").write_text("//invented\n", encoding="utf-8")
    (tree / ".bin").mkdir()
    (tree / ".bin" / "demo-tool").symlink_to("../demo-tool/index.js")
    (workspace / "package.json").write_text(
        json.dumps({"name": "lesson-invented", "dependencies": {"demo-tool": "1.0.0"}}),
        encoding="utf-8",
    )
    (workspace / "bun.lock").write_text("//invented lockfile\n", encoding="utf-8")
    # The name is only skipped at its one shape. A bundle that happens to hold
    # one is ordinary instance state.
    decoy = instance / "lessons" / "demo-slug" / "node_modules"
    decoy.mkdir()
    (decoy / "kept.txt").write_text("authored\n", encoding="utf-8")

    manifest_path = backup_db.create_backup()
    manifest = backup_db.verify(manifest_path)
    archived = set(manifest["instance_files"])

    assert not any(
        name.startswith("lesson-builds/invented-lesson/node_modules/")
        for name in archived
    ), "a package tree cannot be carried faithfully, so it is not carried in half"
    assert "lesson-builds/*/node_modules/" in manifest["excluded"]
    assert "lesson-builds/invented-lesson/package.json" in archived, (
        "the record of what this lesson added is what a rebuild reinstalls from"
    )
    assert "lesson-builds/invented-lesson/bun.lock" in archived
    assert "lessons/demo-slug/node_modules/kept.txt" in archived, (
        "only the workspace's own tree is skipped, and only at that depth"
    )

    target = tmp_path / "recovered"
    backup_db.restore(manifest_path, target)

    restored = target / "lesson-builds" / "invented-lesson"
    assert json.loads((restored / "package.json").read_text(encoding="utf-8"))[
        "dependencies"
    ] == {"demo-tool": "1.0.0"}
    assert not (restored / "node_modules").exists(), (
        "the next build installs one; a restore that made the directory would "
        "be claiming it had recovered a package tree"
    )


def test_a_restore_rebuilds_every_directory_it_carried(backup_db, instance, tmp_path):
    (instance / "migrations").mkdir()
    (instance / "migrations" / "rollback.json").write_text("{}", encoding="utf-8")
    manifest_path = backup_db.create_backup()

    target = tmp_path / "recovered"
    result = backup_db.restore(manifest_path, target)

    assert result["restored"] == ["lessons", "migrations"]
    assert (target / "migrations" / "rollback.json").read_text(encoding="utf-8") == "{}"
    assert (target / "lessons" / "demo-slug" / "index.html").is_file()


def test_a_failed_restore_leaves_the_live_instance_where_it_was(
    backup_db, instance, tmp_path, monkeypatch
):
    """The copy and the extraction can fail halfway — a full disk is the ordinary
    way. Doing that in place would leave the target holding neither the old
    instance nor a usable new one."""
    manifest_path = backup_db.create_backup()
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "activity.sqlite").write_text("the live ledger", encoding="utf-8")
    (target / "lessons").mkdir()
    (target / "lessons" / "mine.txt").write_text("mine", encoding="utf-8")

    def boom(archive, dest):
        raise OSError("No space left on device")

    monkeypatch.setattr(backup_db, "_extract_instance", boom)
    with pytest.raises(OSError):
        backup_db.restore(manifest_path, target, force=True)

    assert (target / "activity.sqlite").read_text(encoding="utf-8") == "the live ledger"
    assert (target / "lessons" / "mine.txt").read_text(encoding="utf-8") == "mine"
    assert not any(".pre-restore-" in path.name for path in target.iterdir()), (
        "nothing was displaced, so nothing needs finding under an aside name"
    )
    assert not any(path.name.startswith(".restore-tmp-") for path in target.iterdir())


def test_a_mid_swap_failure_removes_new_entries_before_restoring_old_ones(
    backup_db, instance, tmp_path, monkeypatch
):
    """A staged entry already installed at an old path must not block rollback."""
    manifest_path = backup_db.create_backup()
    target = tmp_path / "occupied"
    (target / "lessons").mkdir(parents=True)
    (target / "activity.sqlite").write_text("the old ledger", encoding="utf-8")
    (target / "lessons" / "mine.txt").write_text("the old lesson", encoding="utf-8")
    original_replace = backup_db.os.replace
    install_count = 0

    def fail_second_install(source, destination):
        nonlocal install_count
        source = Path(source)
        destination = Path(destination)
        if source.parent.name.startswith(backup_db.RESTORE_TMP) and destination.parent == target:
            install_count += 1
            if install_count == 2:
                raise OSError("simulated failure in the staged-entry swap")
        return original_replace(source, destination)

    monkeypatch.setattr(backup_db.os, "replace", fail_second_install)
    with pytest.raises(OSError, match="staged-entry swap"):
        backup_db.restore(manifest_path, target, force=True)

    assert (target / "activity.sqlite").read_text(encoding="utf-8") == "the old ledger"
    assert (target / "lessons" / "mine.txt").read_text(encoding="utf-8") == (
        "the old lesson"
    )
    assert not any(backup_db.ASIDE_MARK in path.name for path in target.iterdir())
    assert not any(path.name.startswith(backup_db.RESTORE_TMP) for path in target.iterdir())


def test_restore_fsyncs_staging_before_swap_and_target_afterwards(
    backup_db, instance, tmp_path, monkeypatch
):
    manifest_path = backup_db.create_backup()
    target = tmp_path / "recovered"
    events: list[tuple[str, Path]] = []
    original_fsync_tree = backup_db._fsync_tree
    original_fsync_dir = backup_db._fsync_dir
    original_replace = backup_db.os.replace

    def record_tree(path):
        events.append(("tree", Path(path)))
        original_fsync_tree(path)

    def record_dir(path):
        events.append(("dir", Path(path)))
        original_fsync_dir(path)

    def record_replace(source, destination):
        events.append(("replace", Path(destination)))
        return original_replace(source, destination)

    monkeypatch.setattr(backup_db, "_fsync_tree", record_tree)
    monkeypatch.setattr(backup_db, "_fsync_dir", record_dir)
    monkeypatch.setattr(backup_db.os, "replace", record_replace)

    backup_db.restore(manifest_path, target)

    tree_index = next(i for i, event in enumerate(events) if event[0] == "tree")
    install_indices = [
        i for i, event in enumerate(events)
        if event[0] == "replace" and event[1].parent == target
    ]
    target_fsync_index = max(
        i for i, event in enumerate(events)
        if event == ("dir", target)
    )
    assert tree_index < min(install_indices)
    assert max(install_indices) < target_fsync_index
    assert events[-1] == ("dir", target.parent), (
        "a newly created target's parent entry is durable before success"
    )


def test_retention_never_sweeps_a_name_claim_another_run_still_holds(
    backup_db, instance, monkeypatch
):
    """A placeholder is zero bytes and named by no manifest whether its run is
    dead or merely unfinished — the two are indistinguishable by inspection.

    Deleting a live one frees its stamp, and a run starting in the same second
    then claims a name someone else is still publishing under: two processes
    replace each other's members, and the manifest that survives describes a set
    that no longer checksums. The claim is held open for the life of the run, so
    retention can ask rather than guess, and the kernel releases it if the run
    dies.
    """
    monkeypatch.setattr(backup_db, "now_stamp", lambda: "2031-07-01-000000")
    backup_db.create_backup()
    stamp, claim_fd = backup_db._claim_stamp()   # stands in for a concurrent run
    placeholder = instance / "backups" / backup_db.db_name(stamp)
    try:
        assert placeholder.stat().st_size == 0
        _, deleted, left = backup_db.prune(1)
        assert placeholder.exists(), "a backup in progress is not debris"
        assert placeholder not in deleted
        assert left == [], "nor is it reported as something to look at"
        assert backup_db.unclaimed_files() == []
    finally:
        os.close(claim_fd)

    # The same file, once nobody owns it, is exactly the debris it looks like.
    assert backup_db.unclaimed_files() == [placeholder]
    _, deleted, left = backup_db.prune(1)
    assert placeholder in deleted and not placeholder.exists()
    assert left == []


def test_a_stale_default_named_database_never_reaches_the_archive(
    backup_db, instance, tmp_path, monkeypatch
):
    """`activity.sqlite` is reserved whatever `ACTIVITY_DB` says.

    Rename the database and the file it used to be stays behind. Archived as
    ordinary content, it would be extracted over the verified snapshot in the
    staging directory — and `--verify` would still pass, because the archive
    does match its manifest. The restored ledger would simply be the wrong one.
    """
    renamed = instance / "ledger.sqlite"
    (instance / "activity.sqlite").rename(renamed)
    monkeypatch.setattr(backup_db, "DB_PATH", renamed)
    stale = instance / "activity.sqlite"
    stale.write_bytes(b"SQLite format 3\x00" + b"\x00" * 512)  # the pre-rename file

    manifest = backup_db.verify(backup_db.create_backup())
    assert not any(
        name.startswith("activity.sqlite") or name.startswith("ledger.sqlite")
        for name in manifest["instance_files"]
    )

    target = tmp_path / "recovered"
    backup_db.restore(backup_db.list_sets()[-1], target)

    assert (target / "activity.sqlite").read_bytes() != stale.read_bytes()
    assert counts(target)["lists"] > 0, "the restored ledger is the verified one"


def test_an_existing_backup_directory_is_tightened_not_trusted(backup_db, instance):
    """The previous version of this script made `backups/` under the operator's
    umask, and `mkdir(mode=...)` does nothing to a directory that already
    exists. A group-writable one lets another local user replace a set: they
    cannot read a 0600 file, but they can unlink it."""
    backups = instance / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    os.chmod(backups, 0o755)

    backup_db.create_backup()

    assert mode_of(backups) == 0o700


def test_retention_sweeps_the_staged_files_a_killed_run_left(
    backup_db, instance, monkeypatch
):
    """A run killed between staging and publishing has no `finally` to run.

    What it leaves is a full copy of the database under a dot-prefixed name that
    every other listing here hides — invisible, once per interruption, until the
    disk fills. Nothing but `_stage` writes that name, so unlike an unclaimed
    snapshot there is nothing to weigh: unowned means wreckage.
    """
    monkeypatch.setattr(backup_db, "now_stamp", lambda: "2031-09-01-000000")
    backup_db.create_backup()
    abandoned, fd = backup_db._stage(".sqlite")
    abandoned.write_bytes(b"a half-copied database")
    os.close(fd)                                  # the run that owned it is gone
    live, live_fd = backup_db._stage(".tar.gz")   # and one that is still running
    try:
        assert backup_db.staging_debris() == [abandoned]

        _, deleted, left = backup_db.prune(1)

        assert abandoned in deleted and not abandoned.exists()
        assert live.exists(), "a backup in progress keeps its staged files"
        assert left == []
        assert len(backup_db.list_sets()) == 1
        backup_db.verify(backup_db.list_sets()[0])
    finally:
        os.close(live_fd)


def test_a_backup_sweeps_abandoned_staging_before_allocating_a_new_set(
    backup_db, instance, monkeypatch
):
    """Disk-filling wreckage must be removed before the next snapshot starts.

    Post-backup retention cannot self-heal when the debris itself leaves too
    little room to create the set needed to reach that retention pass. A staged
    file held by another live run must still survive the early sweep.
    """
    (instance / "backups").mkdir(mode=0o700)
    abandoned, abandoned_fd = backup_db._stage(".sqlite")
    abandoned.write_bytes(b"an abandoned database copy")
    os.close(abandoned_fd)
    live, live_fd = backup_db._stage(".tar.gz")
    original_claim_stamp = backup_db._claim_stamp

    def claim_after_cleanup():
        assert not abandoned.exists(), "wreckage is gone before name allocation"
        assert live.exists(), "a concurrent run still owns its staged file"
        return original_claim_stamp()

    monkeypatch.setattr(backup_db, "_claim_stamp", claim_after_cleanup)
    try:
        backup_db.create_backup()
    finally:
        os.close(live_fd)


def test_verify_and_restore_into_need_no_live_instance_configuration(
    backup_db, instance, tmp_path
):
    """A copied three-file set is sufficient on a recovery host."""
    manifest_path = backup_db.create_backup()
    env = os.environ.copy()
    env.pop("ACTIVITY_DATA_DIR", None)
    env.pop("ACTIVITY_DB", None)

    verified = subprocess.run(
        [sys.executable, "-m", "scripts.backup_db", "--verify", str(manifest_path)],
        cwd=ROOT, env=env, text=True, capture_output=True,
    )
    assert verified.returncode == 0, verified.stderr
    assert "VERIFIED" in verified.stdout

    target = tmp_path / "off-machine-restore"
    previous_umask = os.umask(0o022)
    try:
        restored = subprocess.run(
            [
                sys.executable, "-m", "scripts.backup_db",
                "--restore", str(manifest_path), "--into", str(target),
            ],
            cwd=ROOT, env=env, text=True, capture_output=True,
        )
    finally:
        os.umask(previous_umask)
    assert restored.returncode == 0, restored.stderr
    assert "RESTORED" in restored.stdout
    assert mode_of(target) == 0o700
    assert counts(target) == counts(instance)


def test_a_backup_ignores_an_interrupted_restores_staging_tree(
    backup_db, instance, tmp_path
):
    """A restore killed partway leaves `.restore-tmp-*` in the data directory,
    and a restore deliberately does not displace it.

    Archiving it would therefore put a name in the set that a later restore
    keeps — and this restore now refuses a set like that outright rather than
    discovering it mid-swap. So the instance would end up with backups it cannot
    restore, from one interrupted restore.
    """
    orphan = instance / ".restore-tmp-abandoned"
    (orphan / "lessons").mkdir(parents=True)
    (orphan / "activity.sqlite").write_text("a half-restored ledger", encoding="utf-8")

    manifest = backup_db.verify(backup_db.create_backup())
    assert not any(
        name.startswith(".restore-tmp-") for name in manifest["instance_files"]
    )

    result = backup_db.restore(backup_db.list_sets()[-1], instance, force=True)

    assert result["stale_staging"] == [".restore-tmp-abandoned"], (
        "reported, because it is invisible otherwise and holds a whole copy"
    )
    assert (orphan / "activity.sqlite").read_text(encoding="utf-8") == (
        "a half-restored ledger"
    ), "and left alone: deleting things in a data directory is not a restore's job"
    assert counts(instance)["lists"] > 0


def test_where_locks_are_unavailable_a_recent_claim_is_still_presumed_live(
    backup_db, instance, monkeypatch
):
    """The lock is imported at the point of use, so the recovery commands still
    work on a platform that has none — and on such a platform age answers the
    question instead, erring toward keeping the file."""
    monkeypatch.setattr(backup_db, "_flock", lambda: None)
    backups = instance / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    fresh = backups / backup_db.db_name("2031-08-01-000000")
    fresh.touch()
    stale = backups / backup_db.db_name("2020-08-01-000000")
    stale.touch()
    old = time.time() - backup_db._UNLOCKED_CLAIM_GRACE - 60
    os.utime(stale, (old, old))

    assert backup_db.unclaimed_files() == [stale]
    _, deleted, left = backup_db.prune(1)
    assert deleted == [stale] and left == []
    assert fresh.exists(), "a claim young enough to belong to a live run stays"


def test_a_backup_ignores_what_a_forced_restore_set_aside(
    backup_db, instance, tmp_path
):
    """Asides are recovery scrap, not instance state.

    Archiving them would double every backup taken after a forced restore, and
    the set would then carry a `lessons.pre-restore-*` directory that a later
    restore does NOT displace — so the swap would try to rename it over the
    non-empty copy it is keeping and fail with ENOTEMPTY, halfway through, on a
    half-replaced instance.
    """
    manifest_path = backup_db.create_backup()
    backup_db.restore(manifest_path, instance, force=True)
    asides = [path for path in instance.iterdir() if ".pre-restore-" in path.name]
    assert asides, "the forced restore preserved what it replaced"

    manifest = backup_db.verify(backup_db.create_backup())

    assert not any(
        ".pre-restore-" in name for name in manifest["instance_files"]
    ), "the next backup carries the instance, not the copies of what it replaced"
    assert "*.pre-restore-*" in manifest["excluded"]

    # And that set restores over the same instance without meeting them.
    result = backup_db.restore(
        backup_db.list_sets()[-1], instance, force=True
    )
    assert result["restored"] == ["lessons"]
    for aside in asides:
        assert aside.exists(), "a restore leaves earlier asides exactly alone"
    assert (instance / "lessons" / "demo-slug" / "index.html").is_file()


def test_a_renamed_database_is_still_excluded_from_the_archive(
    backup_db, instance, monkeypatch
):
    """`ACTIVITY_DB` may name any file, and both exclusions are derived from it.

    A live database matching neither the literal `activity.sqlite` nor its
    sidecars would otherwise be archived as ordinary content, and the restored
    directory would hold both the verified snapshot and an unchecked raw copy
    at the configured path — which is the one the next start opens.
    """
    renamed = instance / "ledger.sqlite"
    (instance / "activity.sqlite").rename(renamed)
    for suffix in ("-wal", "-shm"):
        (instance / f"ledger.sqlite{suffix}").write_text("stale", encoding="utf-8")
    monkeypatch.setattr(backup_db, "DB_PATH", renamed)

    manifest = backup_db.verify(backup_db.create_backup())

    assert not any(
        name.startswith("ledger.sqlite") for name in manifest["instance_files"]
    ), "the snapshot is the consistent copy; the raw file must not ride along"
    assert manifest["excluded"] == [
        "backups/", "exports/", "lesson-builds/*/node_modules/",
        "*.pre-restore-*", ".restore-tmp-*", "activity.sqlite*",
        "ledger.sqlite*",
    ], (
        "and the manifest names the file it actually left out"
    )


def test_a_relative_data_dir_still_excludes_its_own_backups(
    backup_db, instance, monkeypatch
):
    """`ACTIVITY_DATA_DIR` may be configured relative to the working directory.

    Compared unresolved, a walked path and an absolute exclusion are simply
    different strings that never match — and the failure compounds silently:
    every run archives the sets before it, so each backup is larger than the
    last and holds copies of copies.
    """
    monkeypatch.chdir(instance.parent)
    relative = Path(instance.name)
    monkeypatch.setattr(backup_db, "DATA_DIR", relative)
    monkeypatch.setattr(backup_db, "DB_PATH", relative / "activity.sqlite")
    monkeypatch.setattr(backup_db, "BACKUPS_DIR", relative / "backups")
    (instance / "exports").mkdir(exist_ok=True)
    (instance / "exports" / "events-2031-01-01-000000.jsonl").write_text(
        "", encoding="utf-8"
    )

    backup_db.create_backup()  # a first set, for the second run to trip over
    manifest = backup_db.verify(backup_db.create_backup())

    assert not any(name.startswith("backups/") for name in manifest["instance_files"])
    assert not any(name.startswith("exports/") for name in manifest["instance_files"])
    assert not any(
        name.startswith("activity.sqlite") for name in manifest["instance_files"]
    )
    assert manifest["excluded"] == [
        "backups/", "exports/", "lesson-builds/*/node_modules/",
        "*.pre-restore-*", ".restore-tmp-*", "activity.sqlite*",
    ]


def test_a_forced_restore_displaces_instance_data_the_set_never_held(
    backup_db, instance, tmp_path
):
    """A restore promises the instance the set describes, not that instance
    merged with whatever the directory accumulated afterwards.

    Displacing only the names the archive carries leaves a `lessons/` tree
    created after the backup standing beside a database that knows nothing
    about it. `backups/` and `exports/` are the exception on both sides: never
    archived, so displacing them would destroy state no restore can give back.
    """
    for path in sorted((instance / "lessons").rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    (instance / "lessons").rmdir()
    manifest_path = backup_db.create_backup()
    assert backup_db.load_manifest(manifest_path)["instance_files"] == []

    target = tmp_path / "live"
    (target / "lessons" / "newer-slug").mkdir(parents=True)
    (target / "lessons" / "newer-slug" / "index.html").write_text(
        "written after the backup", encoding="utf-8"
    )
    (target / "activity.sqlite").write_text("the live ledger", encoding="utf-8")
    (target / "backups").mkdir()
    (target / "backups" / "someones-set.manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(backup_db.BackupError) as raised:
        backup_db.restore(manifest_path, target)
    assert "lessons" in str(raised.value), "the newer tree is seen as occupancy"

    result = backup_db.restore(manifest_path, target, force=True)

    assert not (target / "lessons").exists(), (
        "a tree the restored database has no rows for does not stay behind"
    )
    assert sorted(result["moved_aside"]) == sorted(
        path.name for path in target.iterdir() if ".pre-restore-" in path.name
    )
    kept = [path for path in target.iterdir() if path.name.startswith("lessons.")]
    assert kept and (kept[0] / "newer-slug" / "index.html").read_text(
        encoding="utf-8"
    ) == "written after the backup", "displaced, never deleted"
    assert (target / "backups" / "someones-set.manifest.json").is_file(), (
        "and the backup directory — often where the set being restored lives — "
        "is left exactly where it was"
    )
    assert counts(target) == counts(instance)


def test_verify_rejects_a_manifest_that_lies_about_the_schema_version(
    backup_db, instance
):
    """The checksums cover the member files, not the manifest describing them —
    and the schema version is what a reader consults to decide whether a set
    predates a migration."""
    manifest_path = backup_db.create_backup()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(backup_db.BackupError) as raised:
        backup_db.verify(manifest_path)
    assert "claims schema v999" in str(raised.value)
