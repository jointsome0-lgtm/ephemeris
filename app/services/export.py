"""JSONL export — serialize the append-only events ledger (sec15.4 / sec18.1).

Contract (decided for v0, sec18.1): export is an **events replay** — the full
`events` table serialized to JSONL, one event per line, ORDER BY id. Because every
check-in, daily note, task and habit change is already journaled as an event
(sec14.1), this single stream inherently includes them all; current-state tables
are derivable from it and are NOT separately exported in v0. SQLite stays the
source of truth — this file is a portable backup. Output lands in db.EXPORTS_DIR
(`data/exports/`, git-ignored; may contain private notes — sec9).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..db import EXPORTS_DIR, now_stamp


def event_count(conn: sqlite3.Connection) -> int:
    """How many events are ready to export."""
    return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def build_jsonl(conn: sqlite3.Connection) -> tuple[str, int]:
    """Render every event as one JSON line (ORDER BY id). Returns (text, count)."""
    rows = conn.execute(
        "SELECT timestamp, type, payload_version, payload_json FROM events ORDER BY id"
    ).fetchall()
    lines: list[str] = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"])
        except (TypeError, ValueError):
            # A malformed payload shouldn't sink the whole export; keep the raw text.
            payload = {"_raw": r["payload_json"]}
        # Per-line shape matches sec18.1: payload is a nested OBJECT, not a string.
        lines.append(json.dumps(
            {
                "timestamp": r["timestamp"],
                "type": r["type"],
                "payload_version": r["payload_version"],
                "payload": payload,
            },
            ensure_ascii=False,  # keep emoji / unicode notes readable
        ))
    text = "".join(line + "\n" for line in lines)
    return text, len(lines)


def export_events(conn: sqlite3.Connection) -> tuple[Path, str, int]:
    """Write data/exports/events-<stamp>.jsonl and return (path, text, count)."""
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    text, count = build_jsonl(conn)
    path = EXPORTS_DIR / f"events-{now_stamp()}.jsonl"
    path.write_text(text, encoding="utf-8")
    return path, text, count


def _human_size(n: int) -> str:
    """Friendly byte size, e.g. 412 B / 6.4 KB / 1.2 MB."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return (f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}")
        size /= 1024
    return f"{n} B"


def recent_exports(limit: int = 8) -> list[dict]:
    """Previously written export files, newest first (name + human size)."""
    if not EXPORTS_DIR.exists():
        return []
    files = sorted(EXPORTS_DIR.glob("events-*.jsonl"),
                   key=lambda p: p.name, reverse=True)[:limit]
    return [{"name": f.name, "size_h": _human_size(f.stat().st_size)} for f in files]
