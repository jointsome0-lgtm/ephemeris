"""JSONL export — serialize the audit stream plus series snapshots (sec18.1).

Contract (decided for v0, sec15.4 / sec18.1): export is the append-only
`events` table serialized to JSONL, one event per line, ORDER BY id, plus one
`calendar_event_series` snapshot line per `calendar_events` row (sec32 §8).
Habits, check-ins, and daily notes are semantically replayable. Calendar rows
come from complete snapshots, but links to unjournaled list rows may not survive
restore. Other typed tables have known journaling gaps, and event IDs are not
exported, so this is not a full-fidelity database backup or an idempotent
delivery format. See docs/restore-from-export.md.
SQLite stays the source of truth. Output lands in db.EXPORTS_DIR
(`data/exports/`, git-ignored; may contain private notes — sec9).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..db import EXPORTS_DIR, now_iso, now_stamp


def event_count(conn: sqlite3.Connection) -> int:
    """How many events are ready to export."""
    return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def build_jsonl(conn: sqlite3.Connection) -> tuple[str, int]:
    """Render export JSONL: events first, then calendar series snapshots."""
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
    # sec32 §8: calendar_events SERIES rows ride along as their own record type.
    # The audit stream alone can't rebuild a series (update events journal only
    # id+title), so the source-of-truth rows — including soft-archived ones — are
    # snapshotted at export time. Occurrences are never exported (expanded on read).
    exported_at = now_iso()
    for s in conn.execute("SELECT * FROM calendar_events ORDER BY id").fetchall():
        lines.append(json.dumps(
            {
                "timestamp": exported_at,
                "type": "calendar_event_series",
                "payload_version": 1,
                "payload": {k: s[k] for k in s.keys()},
            },
            ensure_ascii=False,
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
