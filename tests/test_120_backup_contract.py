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
import subprocess
import sys
from pathlib import Path

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
