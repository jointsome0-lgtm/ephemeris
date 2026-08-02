"""JSONL export — serialize the audit stream plus series snapshots (sec18.1).

Contract (decided for v0, sec15.4 / sec18.1): export is the append-only
`events` table serialized to JSONL, one event per line, ORDER BY id, plus one
`calendar_event_series` snapshot line per `calendar_events` row (sec32 §8).
Habits, check-ins, and daily notes are semantically replayable. Calendar rows
come from complete snapshots, but links to unjournaled list rows may not survive
restore. Other typed tables have known journaling gaps, so this is not a
full-fidelity database backup.

It IS an idempotent delivery format (issue #17): every audit line carries the
event's stable `events.uuid` (schema v9) as its `id`, so a consumer that has
already seen a line can recognize it on redelivery instead of duplicating it.
`scripts/restore_from_export.py` is that consumer. See
docs/restore-from-export.md.

SQLite stays the source of truth. Output lands in db.EXPORTS_DIR
(`data/exports/`, git-ignored; may contain private notes — sec9), which retains
the `limits.EXPORT_KEEP` newest files and drops the rest after every write
(issue #23). That is safe precisely because of the contract above: the stream is
append-only, so the newest export contains everything its predecessors did. The
full backups in `data/backups/` are a different mechanism with a different rule
— manual `scripts/backup_db.py --keep N` — because a backup set is a point in
time that the current database cannot reproduce.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

from .. import limits
from ..db import EXPORTS_DIR, now_iso, now_stamp


def event_count(conn: sqlite3.Connection) -> int:
    """How many events are ready to export."""
    return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def iter_jsonl(conn: sqlite3.Connection) -> Iterator[str]:
    """Yield export JSONL line by line: events first, then calendar snapshots.

    A generator rather than a list because the audit stream has no ceiling — it
    only ever grows — and the writer below needs to hold one line at a time, not
    the whole history twice (list of lines plus the joined text).
    """
    # Row-at-a-time: iterating the cursor keeps one row in Python at a time,
    # where .fetchall() would materialize every event before the first is written.
    for r in conn.execute(
        "SELECT uuid, timestamp, type, payload_version, payload_json "
        "FROM events ORDER BY id"
    ):
        try:
            payload = json.loads(r["payload_json"])
        except (TypeError, ValueError):
            # A malformed payload shouldn't sink the whole export; keep the raw text.
            payload = {"_raw": r["payload_json"]}
        # Per-line shape matches sec18.1: payload is a nested OBJECT, not a string.
        # `id` is the row's stable uuid and leads the line (issue #17). It is
        # omitted, not null, for a row a pre-v9 process appended after the
        # migration and before init_db() healed it — an absent receipt reads as
        # "this line cannot be deduplicated", which is exactly what it means.
        record: dict[str, object] = {} if r["uuid"] is None else {"id": r["uuid"]}
        record.update({
            "timestamp": r["timestamp"],
            "type": r["type"],
            "payload_version": r["payload_version"],
            "payload": payload,
        })
        yield json.dumps(
            record,
            ensure_ascii=False,  # keep emoji / unicode notes readable
        ) + "\n"
    # sec32 §8: calendar_events SERIES rows ride along as their own record type.
    # The audit stream alone can't rebuild a series (update events journal only
    # id+title), so the source-of-truth rows — including soft-archived ones — are
    # snapshotted at export time. Occurrences are never exported (expanded on read).
    # These carry no `id` receipt: their identity is the business key already in
    # the payload (the series id), so restore upserts them instead of skipping.
    exported_at = now_iso()
    for s in conn.execute("SELECT * FROM calendar_events ORDER BY id"):
        yield json.dumps(
            {
                "timestamp": exported_at,
                "type": "calendar_event_series",
                "payload_version": 1,
                "payload": {k: s[k] for k in s.keys()},
            },
            ensure_ascii=False,
        ) + "\n"


def build_jsonl(conn: sqlite3.Connection) -> tuple[str, int]:
    """The whole export as one string, for a caller that needs it in memory.

    Only verify_restore.py's re-export proof does, and it compares two exports
    line for line. The HTTP path never calls this — export_events() streams
    straight to disk.
    """
    lines = list(iter_jsonl(conn))
    return "".join(lines), len(lines)


def _claim_name(staged: Path, stamp: str) -> Path:
    """Hard-link the finished `staged` file onto the first free export name.

    `os.link` refuses an existing destination, which is the O_EXCL guarantee
    applied to a rename: the name is claimed atomically and an export can never
    overwrite an earlier one. `now_stamp()` only resolves to the second, so two
    exports inside one second are a real collision, not a theoretical one; the
    second gets `-2`, the next `-3`.
    """
    for n in range(1, 1000):
        suffix = "" if n == 1 else f"-{n}"
        path = EXPORTS_DIR / f"events-{stamp}{suffix}.jsonl"
        try:
            os.link(staged, path)
        except FileExistsError:
            continue
        return path
    raise OSError(f"every export name for {stamp} is taken (999 tried)")


def _fsync_dir(path: Path) -> None:
    """Flush a directory's own metadata: the entries created and removed in it.

    Fsyncing the export's file descriptor persists its CONTENTS. The link that
    gives those bytes a public name is a change to the directory, and survives a
    power loss only once the directory itself is synced — otherwise the route
    can report a durable backup that is gone after a reboot.
    """
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def export_events(conn: sqlite3.Connection) -> tuple[Path, int]:
    """Write data/exports/events-<stamp>.jsonl atomically; return (path, count).

    The export streams into a temporary file in the same directory, is fsynced,
    and only then claims its public name. So a crash mid-write leaves at most a
    dot-prefixed `.events-*.tmp` — never a truncated `events-*.jsonl`, which
    would look exactly like a complete backup to anyone reading the directory
    later, including recent_exports() below.
    """
    if not EXPORTS_DIR.is_dir():
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        # A directory's own NAME is an entry in its parent. Syncing only the leaf
        # would make the first export durable inside a directory that can itself
        # vanish in the same power loss.
        _fsync_dir(EXPORTS_DIR.parent)
    stamp = now_stamp()
    # mkstemp creates 0600, which the final name inherits through the link:
    # exports hold private notes (sec9) and are never group- or world-readable.
    fd, staged_name = tempfile.mkstemp(
        dir=EXPORTS_DIR, prefix=".events-", suffix=".jsonl.tmp")
    staged = Path(staged_name)
    try:
        count = 0
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for line in iter_jsonl(conn):
                handle.write(line)
                count += 1
            handle.flush()
            os.fsync(handle.fileno())
        path = _claim_name(staged, stamp)
    finally:
        # After a successful link the bytes live under both names; dropping the
        # temporary one leaves the export. After a failure it drops the partial.
        staged.unlink(missing_ok=True)
    # Both directory changes — the new name and the dropped temporary one — are
    # persisted before this call reports an export the caller can rely on.
    _fsync_dir(EXPORTS_DIR)
    prune_exports(keep=path)
    return path, count


def existing_exports() -> list[Path]:
    """Every finished export, newest first.

    One ordering, used by retention, by the recent list and by the status
    panel, so what the page calls "the newest 30" is exactly what retention
    keeps. The stamp is the whole variable part of the name and sorts
    chronologically; a within-the-second collision suffix ("-2") sorts just
    before its base name, which is immaterial — they describe the same second.
    The `events-*` glob cannot match a `.events-*.jsonl.tmp` still being
    written, so an export in progress is neither counted nor deleted.
    """
    if not EXPORTS_DIR.is_dir():
        return []
    return sorted(EXPORTS_DIR.glob("events-*.jsonl"),
                  key=lambda p: p.name, reverse=True)


def _in_flight(path: Path) -> bool:
    """Was `path` written recently enough that a response may still be reading it?

    Deliberately crude: an mtime, compared against the same clock that named the
    file. That makes it symmetric — during a backward step the pre-existing
    exports look "in the future" and are held too, which is the safe direction
    for a rule whose whole job is not to delete something being served.

    A file that cannot be stat'ed has already vanished; call it in flight and
    let the next run confirm.
    """
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return True
    return age < limits.EXPORT_GRACE


def prune_exports(keep: Path | None = None) -> list[Path]:
    """Delete all but the `limits.EXPORT_KEEP` newest exports; return what went.

    Runs after every export, because that is the only moment the directory
    grows and the only moment anyone is watching. Retention is safe to
    automate here in a way it is not for backups: every export is a full
    serialization of an append-only stream, so the newest file contains
    everything the ones behind it did. A backup set is not reproducible that
    way, which is why `scripts/backup_db.py` keeps its manual `--keep`.

    `keep` is the export the caller is about to hand back, and it is treated as
    the newest whatever its name says. Usually that is what the ordering
    concludes anyway; it stops being true when the wall clock steps backwards —
    a DST fallback, an NTP correction — and the fresh file is stamped an hour
    behind thirty existing ones. Ordering alone would then delete the export
    the route is about to stream, turning a clock adjustment into a failed
    download. Being written is the stronger evidence of newness than a name.

    `keep` protects one path, and during that same rolled-back hour a second
    export is a second path nobody passed here — its own request is still
    streaming it while this one prunes. So the rule is widened rather than
    threaded through both requests: nothing touched within EXPORT_GRACE of now
    is removed, which covers every export still being delivered without any
    shared state to keep, and needs no opinion about when a response finishes.
    Only that window is affected: retention resumes as soon as it closes, and
    it deletes then what it declined to delete now.

    Where this deliberately stops: once the grace has passed, eviction order is
    the filename again, so exports written during a rolled-back hour are
    evicted before slightly older ones. Nothing is lost by that — each of the
    thirty is a full serialization of the same stream, the live database still
    holds every event, and the next press writes a current one. Fixing the
    order would mean persisting a sequence outside the filenames, which is a
    second source of truth about the directory to keep correct forever, for a
    once-a-year hour in which the answer is only unaesthetic. Deliberate skip
    (#125 review round 6); reopen it as its own change if the ordering ever
    matters to something.

    Best-effort by construction: a file that cannot be unlinked is left where
    it is rather than sinking an export that already succeeded. The next run
    tries again.
    """
    surviving = existing_exports()
    if keep is not None and keep in surviving:
        surviving.remove(keep)
        surviving.insert(0, keep)
    doomed = [p for p in surviving[limits.EXPORT_KEEP:] if not _in_flight(p)]
    if not doomed:
        return []
    removed = []
    for path in doomed:
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path)
    if removed:
        _fsync_dir(EXPORTS_DIR)
    return removed


def human_size(n: int) -> str:
    """Friendly byte size, e.g. 412 B / 6.4 KB / 1.2 MB.

    Public because the storage panel (services/storage.py) shows the same kind
    of number beside these files and must not spell the rounding twice.
    """
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return (f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}")
        size /= 1024
    return f"{n} B"


def recent_exports(limit: int = 8) -> list[dict]:
    """Previously written export files, newest first (name + human size).

    A file that vanishes between the listing and its stat is dropped rather
    than raised: now that retention deletes, `GET /export` rendering this list
    can overlap a `POST /export/jsonl` pruning it, and one unlucky interleaving
    must not turn the page into a 500 over a file the reader was going to be
    told about and no longer needs to be.
    """
    found = []
    for path in existing_exports()[:limit]:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        found.append({"name": path.name, "size_h": human_size(size)})
    return found
