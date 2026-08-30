"""Shared state for the cumulative Ephemeris verification suite."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# Isolated DB before importing the app.
os.environ["ACTIVITY_DATA_DIR"] = tempfile.mkdtemp(prefix="al-verify-")
# Terminal is opt-in. The subprocess wiring probes assert the default-off
# wiring; the in-process app opts in so the terminal surface itself (trust gate,
# session ownership) is still exercised.
os.environ["EPHEMERIS_ENABLE_TERMINAL"] = "1"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from app.db import get_conn  # noqa: E402

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


def query(db_path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def write_lesson_manifest(slug: str, uid: str, **extra) -> None:
    """A valid v2 manifest for `slug`, plus whatever the case declares."""
    from app.services import lessons as lessons_svc

    bundle = Path(lessons_svc.LESSONS_DIR) / slug
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "index.html").write_text("<p>page</p>", encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "lesson_uid": uid,
        "entry": "index.html",
        "pages": [{"id": "pg_seeded0001", "path": "index.html"}],
    }
    manifest.update(extra)
    (bundle / "lesson.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, base_url="http://localhost") as value:
        yield value


@pytest.fixture(scope="session")
def suite_state() -> dict:
    return {}


@pytest.fixture(scope="module")
def started_app(client):
    """The app has been through its startup before any test in the module."""
