"""Shared state for the cumulative Ephemeris verification suite."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Isolated DB before importing the app.
os.environ["ACTIVITY_DATA_DIR"] = tempfile.mkdtemp(prefix="al-verify-")
# Terminal is opt-in. The subprocess wiring probes assert the default-off
# wiring; the in-process app opts in so the terminal surface itself (trust gate,
# session ownership) is still exercised.
os.environ["EPHEMERIS_ENABLE_TERMINAL"] = "1"
# TestClient presents Host: testserver; force the allowlist to a known value
# (app/security.py reads it at import) so an ambient LAN setting can't 400
# every request under test.
os.environ["EPHEMERIS_TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1,::1"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from app.db import get_conn  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    mark = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{mark}] {label}" + (f"  -- {extra}" if extra and not cond else ""))


def events_of(type_: str) -> list:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT payload_json FROM events WHERE type = ? ORDER BY id", (type_,)
        ).fetchall()
    finally:
        conn.close()


def item_row(item_id: int):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM routine_items WHERE id = ?", (item_id,)
        ).fetchone()
    finally:
        conn.close()


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as value:
        yield value


@pytest.fixture(scope="session")
def suite_state() -> dict:
    return {}


def pytest_sessionfinish(session, exitstatus) -> None:
    print(f"\n{PASS} passed, {FAIL} failed")
    if FAIL and exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
