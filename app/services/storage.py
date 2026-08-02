"""What this instance is costing on disk, and what about that is worrying (#23).

The app writes three kinds of file and, until this module, could report on none
of them: the SQLite ledger it grows forever, the JSONL exports it writes on
demand, and the full backup sets `scripts/backup_db.py` leaves in
`data/backups/`. The only way to know whether last night's backup ran was to
list a private directory over SSH.

So: one read-only summary, rendered as a section of `/export`. It reads and
never writes — no directory is created, no backup is triggered, nothing is
pruned — because it is reached by a GET, and a GET in this app is
side-effect-free by contract (app/security.py leaves safe methods unguarded on
exactly that promise).

Backups are read through their manifests, which is the same rule
`scripts/backup_db.py` states for itself: a set exists when its manifest does,
and the manifest names its own members, so this module never has to know how a
backup file is spelled. The newest manifest that parses wins; older ones behind
a corrupt file still answer the question "when was the last good backup?".
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .. import limits
from ..db import BACKUPS_DIR, DATA_DIR, DB_PATH, pretty_date, today_str
from . import export

# SQLite in WAL mode is three files, and the -wal one is not a rounding error:
# it holds every page written since the last checkpoint. A "database size" that
# ignored it would understate the ledger by however much has been written since
# the app last quieted down.
_SIDECARS = ("-wal", "-shm")


def _size_of(path: Path) -> int:
    """`path`'s size in bytes, or 0 if it is not there to be measured."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _database_bytes() -> int:
    return _size_of(DB_PATH) + sum(
        _size_of(DB_PATH.with_name(DB_PATH.name + suffix)) for suffix in _SIDECARS
    )


def _parse_moment(value: object) -> datetime | None:
    """A manifest's `created_at` as a datetime, or None if unusable.

    A manifest written by a future version, by hand, or truncated mid-write is
    data from outside this module; it answers None rather than raising, and the
    caller moves on to the next-newest set. Only the local calendar day and
    clock time are ever read off the result, so an offset-less timestamp from
    an older writer needs no repair to be usable.
    """
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _read_manifest(path: Path) -> dict | None:
    """One backup set summarized from its manifest, or None if it is not one.

    The sizes come from the manifest's own `files` entries — what the set
    claimed to be when it was written — falling back to the bytes on disk for
    an entry that does not carry a number. Nothing is hashed or verified here:
    that is `scripts/backup_db.py --verify`, which reads gigabytes and belongs
    nowhere near a page render.
    """
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict):
        return None
    created = _parse_moment(manifest.get("created_at"))
    if created is None:
        return None
    files = manifest.get("files")
    total = _size_of(path)
    for entry in (files or {}).values():
        if not isinstance(entry, dict):
            continue
        claimed = entry.get("bytes")
        name = entry.get("name")
        if isinstance(claimed, int) and claimed >= 0:
            total += claimed
        elif isinstance(name, str):
            total += _size_of(path.parent / name)
    return {
        "name": path.name,
        "created": created,
        "when": f"{pretty_date(created.date(), year=True)} {created:%H:%M}",
        "bytes": total,
        "size_h": export.human_size(total),
    }


def newest_backup() -> dict | None:
    """The newest backup set whose manifest can be read, or None if there is none."""
    if not BACKUPS_DIR.is_dir():
        return None
    for path in sorted(BACKUPS_DIR.glob("activity-*.manifest.json"),
                       key=lambda p: p.name, reverse=True):
        found = _read_manifest(path)
        if found is not None:
            return found
    return None


def free_space() -> int | None:
    """Free bytes on the filesystem holding the data directory, or None.

    None rather than zero when the directory cannot be measured: a panel that
    reported "0 B free" for a missing mount would raise exactly the alarm the
    real thing raises, for the wrong reason. The rest of the panel is still
    worth showing.
    """
    try:
        return shutil.disk_usage(DATA_DIR).free
    except OSError:
        return None


def status(conn: sqlite3.Connection) -> dict:
    """Everything the /export status panel shows, already formatted.

    Formatted here rather than in the template because the same numbers decide
    the warnings: a template that ran the thresholds itself would be a second
    place where "stale" and "low" are defined.
    """
    backup = newest_backup()
    exports = export.existing_exports()
    export_bytes = sum(_size_of(path) for path in exports)
    db_bytes = _database_bytes()

    free_bytes = free_space()

    warnings: list[str] = []
    age_days: int | None = None
    if backup is None:
        warnings.append(
            "No backup set has been written yet — the JSONL export below is an "
            "audit stream, not a full backup. Run scripts/backup_db.py."
        )
    else:
        # Whole calendar days in the ledger's own zone (sec13.3), not elapsed
        # hours: last night's backup should read as one day old on a panel
        # whose threshold is counted in days, not as zero.
        age_days = max((date.fromisoformat(today_str()) - backup["created"].date()).days, 0)
        if age_days > limits.BACKUP_STALE_DAYS:
            warnings.append(
                f"The newest backup set is {age_days} days old "
                f"(over {limits.BACKUP_STALE_DAYS}). Run scripts/backup_db.py."
            )
    if free_bytes is not None and free_bytes < limits.FREE_SPACE_FLOOR:
        warnings.append(
            f"Only {export.human_size(free_bytes)} free on the data volume — "
            "below the 1 GB a backup set needs room for."
        )

    return {
        "db_size_h": export.human_size(db_bytes),
        "event_count": export.event_count(conn),
        "backup": backup,
        "backup_age_days": age_days,
        "export_count": len(exports),
        "export_size_h": export.human_size(export_bytes),
        "export_keep": limits.EXPORT_KEEP,
        "free_size_h": None if free_bytes is None else export.human_size(free_bytes),
        "warnings": warnings,
    }
