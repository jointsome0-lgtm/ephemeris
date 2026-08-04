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
from ..db import BACKUPS_DIR, DATA_DIR, DB_PATH, app_tz, pretty_date, today_str
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

_MAX_CLAIMED_BYTES = 2**63 - 1
"""The largest size a manifest entry may claim before it stops being a size.

A manifest's `bytes` field is a copy of what `stat()` reported for that member
when the set was written — `scripts/backup_db.py::verify` compares the two
directly — so the range a file size can take IS the range this field can take.
That makes the bound the type's rather than a taste judgement: no `st_size`
is negative, and none exceeds a signed 64-bit offset.

The line has to be drawn somewhere below "any integer JSON can spell", because
the panel formats this number and `export.human_size` starts with `float(n)`,
which raises `OverflowError` past roughly 1e308. Drawing it here rather than in
the formatter keeps the check where the value stops being trusted input.
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
    caller moves on to the next-newest set.

    The result is always aware and always in the LEDGER's zone — `APP_TIMEZONE`
    when set, the host's otherwise, exactly what `db._now` uses. That is the
    zone `today_str()` answers in, and the panel subtracts one from the other
    to age a backup: normalizing to the host zone instead would put the two
    dates in different calendars whenever the app is configured for a zone the
    machine is not in, and the stale warning would arrive a day early or late
    around midnight. It is also the only way candidates from different writers
    can be compared at all.

    A value carrying no offset is READ AS the ledger's zone, not converted into
    it from the host's. `astimezone()` on a naive datetime silently supplies the
    machine's zone first, which is the same day-early/day-late error one layer
    down: `2040-01-01T00:30:00` in a `America/New_York` ledger on a Moscow host
    would be dated 2039-12-31. The writer stamps an offset
    (`scripts/backup_db.py::now_iso`), so this governs hand-written and foreign
    manifests — which is precisely the input nothing else has vouched for.

    Normalizing here rather than at the comparison is what
    makes "unusable timestamp" one answer instead of two: a value near the
    datetime boundary parses cleanly and then raises on conversion, and that
    must be a skipped manifest, not a 500 on a page whose whole promise is to
    survive a damaged file.
    """
    if not isinstance(value, str):
        return None
    tz = app_tz()
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None and tz is not None:
            moment = moment.replace(tzinfo=tz)
        # astimezone(None) is the host zone, which is what app_tz() means by
        # "unset" — so this is one expression for both configurations, and the
        # branch above is a no-op in the one where they are the same zone.
        return moment.astimezone(tz)
    except (ValueError, OverflowError, OSError):
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

    An entry that carries a number no file could ever have (see
    `_MAX_CLAIMED_BYTES`) is damage rather than a measurement, and it makes the
    whole manifest unreadable — the same answer as a truncated one. Formatting
    it instead is how a single hand-edited digit turns every `GET /export` into
    a 500, warnings and older sets and all.
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
    sizes = {}
    for role, entry in files.items():
        if not isinstance(entry, dict):
            continue
        claimed = entry.get("bytes")
        name = entry.get("name")
        if isinstance(claimed, int):
            if not 0 <= claimed <= _MAX_CLAIMED_BYTES:
                return None
            sizes[role] = claimed
        elif isinstance(name, str):
            sizes[role] = _size_of(path.parent / name)
        total += sizes.get(role, 0)
    return {
        "name": path.name,
        "created": created,
        "when": f"{pretty_date(created.date(), year=True)} {created:%H:%M}",
        "bytes": total,
        # Kept apart from the total because the space estimate needs one member
        # without the other: the database it pairs with is the LIVE one, which
        # has grown since, while everything else is only measurable from here.
        "beside_database": total - sizes.get("database", 0),
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

    Timestamps arrive aware and in the ledger's zone from `_parse_moment`,
    which reads an offset-less one as that same zone rather than the host's, so
    every candidate is comparable and none of them can raise here.
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
    # Ordered by absolute instant, not by the datetime itself: two aware values
    # sharing one tzinfo are compared by WALL time (a documented rule), so
    # 01:15 after a fall-back would lose to the 01:30 that came before it —
    # exactly the hour when this function's answer is hardest to check by eye.
    # Name descending as the tie-break, so two sets stamped in the same second
    # still resolve the same way on every render.
    return max(sets, key=lambda s: (s["created"].timestamp(), s["name"]))


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

    The two halves come from different moments on purpose. The database is
    measured live, because that is the copy the next backup will stage; what
    sat beside it is taken from the old manifest as recorded there. Subtracting
    today's database from the old total instead would cancel out exactly the
    growth this warning exists to notice — a ledger that has gone from 1 GB to
    20 GB beside a 10 GB archive would report 20 GB needed instead of 30.
    """
    return max(limits.FREE_SPACE_FLOOR,
               db_bytes + (backup["beside_database"] if backup else 0))


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
