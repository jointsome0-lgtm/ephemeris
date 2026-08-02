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
backup file is spelled. The newest manifest the *restore tooling* would accept
wins; older ones behind a corrupt or unrecognized file still answer the
question "when was the last good backup?".
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

MANIFEST_VERSION = 1
"""The manifest shape `scripts/backup_db.py` writes and can restore.

Spelled here rather than imported because `app/` must not depend on `scripts/`;
`tests/test_130_limits.py` reads the script's own constant and fails if the two
ever disagree.

The panel skips any other version for a specific reason: the question it
answers is "is there a backup I could restore?", and `load_manifest()` refuses
every version but its own. Reporting a set the bundled tooling would decline is
worse than reporting none — it suppresses the missing-backup warning and hides
an older set that actually works.
"""

_REQUIRED_MEMBERS = ("database", "instance")
"""The members `load_manifest` insists a set names before it will restore it.

Same tripwire as the version above: a role added there and not here would be a
panel that reports sets the tooling cannot use.
"""


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

    "Is one" means exactly what `scripts/backup_db.py::load_manifest` means by
    it — the current version, and both restorable members named — because the
    panel's claim is that the bundled tooling could restore what it reports. A
    manifest this function accepted but that tooling would refuse is worse than
    no manifest: it silences the missing-backup warning with a set nobody can
    open. Structure only; nothing is hashed and no member is stat'ed for
    integrity, because that is `--verify`, which reads gigabytes and belongs
    nowhere near a page render.

    The sizes come from the manifest's own `files` entries — what the set
    claimed to be when it was written — falling back to the bytes on disk for
    an entry that does not carry a number.
    """
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict):
        return None
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        return None
    created = _parse_moment(manifest.get("created_at"))
    if created is None:
        return None
    files = manifest.get("files")
    if not isinstance(files, dict):
        return None
    # The two members a restore needs. Anything else under `files` is summed
    # below but not required — a later version may add members, and the panel
    # would rather over-report a set's size than refuse to mention it.
    for role in _REQUIRED_MEMBERS:
        entry = files.get(role)
        if not isinstance(entry, dict) or not entry.get("name"):
            return None
    total = _size_of(path)
    for entry in files.values():
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
    """The newest backup set whose manifest can be read, or None if there is none.

    "Newest" is the `created_at` the set recorded, not the name it sorts under.
    They agree except when the wall clock steps backwards — a DST fallback, an
    NTP correction — and then the stamp in the filename is the thing that is
    wrong, while the timestamp inside is what the backup itself claims. Reading
    every manifest to find the maximum costs one small JSON per retained set,
    which is what `--keep` keeps small; stopping at the first name would answer
    the panel's one question wrongly twice a year.

    Aware and naive timestamps are compared through `astimezone()`, which reads
    a naive one as local time — the zone the backup was taken in, on the machine
    it was taken on.
    """
    if not BACKUPS_DIR.is_dir():
        return None
    sets = []
    for path in BACKUPS_DIR.glob("activity-*.manifest.json"):
        found = _read_manifest(path)
        if found is not None:
            sets.append(found)
    if not sets:
        return None
    # Name descending as the tie-break, so two sets stamped in the same second
    # still resolve the same way on every render.
    return max(sets, key=lambda s: (s["created"].astimezone(), s["name"]))


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


def _space_a_backup_needs(db_bytes: int, backup: dict | None) -> int:
    """Roughly what `scripts/backup_db.py` would have to fit on this volume.

    A fixed floor cannot answer this. The ledger and the instance files grow
    without a bound, and the backup stages a full copy of both *beside* them —
    so a 3 GB database on a volume with 2 GB free is a backup that cannot
    succeed, while a floor of 1 GB says nothing is wrong. The measured database
    is the honest lower bound and it is already in hand.

    The instance archive is estimated from the last set that was written, which
    is the only measurement of it this module can make without walking the data
    directory on every page render. When there is no previous set, the floor
    stands in for it — the same number that has to cover a first backup anyway.
    """
    instance_bytes = 0
    if backup is not None:
        # The manifest's total minus the database it names: whatever else that
        # set carried, at the size it carried it.
        instance_bytes = max(backup["bytes"] - db_bytes, 0)
    return max(limits.FREE_SPACE_FLOOR, db_bytes + instance_bytes)


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
    needed = _space_a_backup_needs(db_bytes, backup)
    if free_bytes is not None and free_bytes < needed:
        warnings.append(
            f"Only {export.human_size(free_bytes)} free on the data volume — "
            f"a backup set needs about {export.human_size(needed)}."
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
