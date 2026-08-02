"""Full backup of the Ephemeris instance: the ledger, its files, a manifest.

The JSONL export is an audit stream and says so (docs/restore-from-export.md).
This is the other thing: a byte-faithful copy of everything an instance holds,
written so that a set found on disk months later can be trusted without being
tried.

One backup is THREE files sharing one stamp, in `$ACTIVITY_DATA_DIR/backups/`:

    activity-<stamp>.sqlite           consistent snapshot of the ledger
    files-<stamp>.tar.gz              everything else under $ACTIVITY_DATA_DIR
    activity-<stamp>.manifest.json    what the set is, and what it should hash to

The manifest is written LAST, by rename. That single ordering rule is the whole
durability contract: a manifest on disk is a promise that the two files it names
are complete and match their checksums, and nothing in `backups/` without one is
a backup. Nothing is ever written under its final name — every file is staged in
the same directory, fsynced, chmod 0600, and moved into place with os.replace.

Retention deletes only what a manifest claims. A file with no manifest may be
debris from an interrupted run, but it may equally be a snapshot from the
pre-manifest version of this script, which used the same name; those are
reported and left alone rather than swept.

The snapshot itself goes through SQLite's Online Backup API, which is
transactionally consistent even while the service is writing; a plain file copy
of a WAL-mode database is not. The copy is then opened and run through
`PRAGMA integrity_check` before it is allowed to claim a name, so a backup can
fail loudly the night it is written instead of silently at recovery time.

Usage:
    python -m scripts.backup_db                       # write a set
    python -m scripts.backup_db --keep 20             # ...and prune to 20 sets
    python -m scripts.backup_db --list                # what is on disk
    python -m scripts.backup_db --verify MANIFEST     # re-check a set, write nothing
    python -m scripts.backup_db --restore MANIFEST --into DIR

Restore never touches a running service: stop it first
(`systemctl --user stop ephemeris`). See docs/backup-restore.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Keep recovery operations importable without live-instance configuration. A
# recovery host needs only the manifest and its two companions; importing
# app.db here would make --verify and --restore --into fail before argument
# parsing whenever ACTIVITY_DATA_DIR is absent. Backup/list operations load the
# app-owned paths at the point where they actually need them.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
DATA_DIR: Path | None = None
DB_PATH: Path | None = None
BACKUPS_DIR: Path | None = None
DB_FILENAME = "activity.sqlite"

ASIDE_MARK = ".pre-restore-"
"""Infix of the copies a forced restore preserves.

They are recovery scrap the operator deletes once satisfied — not instance
state — so neither half of this contract touches them: the archive walks past
them and a restore leaves them where they are."""

RESTORE_TMP = ".restore-tmp-"
"""Prefix of the directory a restore builds in before swapping it in.

One is left behind by a restore that was killed. It holds a copy of a backup
set — reproducible, never unique — so nothing here deletes it, but nothing here
archives or displaces it either: it is not instance state."""

STAGED = ".staged-"
"""Prefix of a backup's in-progress files inside `backups/`."""

EXCLUDED_DIRS = ("backups", "exports")
"""Top-level directories that are neither archived nor replaced by a restore.

`backups/` would nest every set inside the next one; `exports/` is generated
FROM the database that is already in the set. Everything else under the data
directory is instance state and belongs to both halves of this contract — the
archive puts it in, and a forced restore moves it aside."""

MANIFEST_VERSION = 1
"""Bumped when the manifest's shape changes. A reader that meets a version it
does not know refuses the set rather than guessing at a field it cannot see."""

# SQLite keeps two sidecars beside a WAL-mode database. They belong to THAT
# database file; leaving a stale pair beside a restored one is how a restore
# turns into corruption, so restore moves them aside explicitly.
_SIDECARS = ("-wal", "-shm")

_CHUNK = 1 << 20


class BackupError(RuntimeError):
    """A backup could not be written, verified, or restored."""


def _load_live_paths() -> None:
    """Resolve the configured instance only for operations that need it."""
    global DATA_DIR, DB_PATH, BACKUPS_DIR
    if DATA_DIR is not None and DB_PATH is not None and BACKUPS_DIR is not None:
        return
    from app.db import DATA_DIR as app_data_dir, DB_PATH as app_db_path

    DATA_DIR = app_data_dir
    DB_PATH = app_db_path
    BACKUPS_DIR = DATA_DIR / "backups"


def _now() -> datetime:
    """The app's sec13.3 clock rule, without importing live path settings."""
    timezone = os.environ.get("APP_TIMEZONE")
    return datetime.now(ZoneInfo(timezone)) if timezone else datetime.now().astimezone()


def now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def now_stamp() -> str:
    return _now().strftime("%Y-%m-%d-%H%M%S")


# --- naming ----------------------------------------------------------------


def db_name(stamp: str) -> str:
    return f"activity-{stamp}.sqlite"


def instance_name(stamp: str) -> str:
    return f"files-{stamp}.tar.gz"


def manifest_name(stamp: str) -> str:
    return f"activity-{stamp}.manifest.json"


def stamp_of(manifest_filename: str) -> str:
    """The shared stamp inside a manifest's filename."""
    return manifest_filename[len("activity-"):-len(".manifest.json")]


def _claim_stamp() -> tuple[str, int]:
    """Reserve a free stamp by creating the snapshot's name, empty and locked.

    Returns (stamp, fd). The caller closes the fd when the run is over — that
    close is what releases the claim, so it is also released by the kernel if
    the process dies.

    `now_stamp()` resolves to the second, so two backups in one second are a
    real collision — the same one the JSONL export hit (#17, first half). O_EXCL
    settles it: the first run owns the bare stamp, the next gets `-2`. The
    placeholder is overwritten by os.replace at the end; if the run dies before
    that, it is a file no manifest claims, which is exactly what `--keep` sweeps
    up.

    The flock is what keeps retention from sweeping a claim that is still being
    used. A `--keep` pass in another process cannot tell a placeholder belonging
    to a run in progress from one an interrupted run abandoned — both are zero
    bytes and neither is named by a manifest — and deleting the live one frees
    the stamp for a third run to claim, at which point two processes publish
    over each other's member names and the surviving manifest describes a set
    that no longer checksums. An open lock says "mine, still", and says it
    without a timeout to guess at.
    """
    base = now_stamp()
    for n in range(1, 1000):
        stamp = base if n == 1 else f"{base}-{n}"
        try:
            fd = os.open(BACKUPS_DIR / db_name(stamp),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        lock = _flock()
        if lock is not None:
            try:
                lock.flock(fd, lock.LOCK_EX | lock.LOCK_NB)
            except BaseException:
                os.close(fd)
                raise
        return stamp, fd
    raise BackupError(f"every backup name for {base} is taken (999 tried)")


def _flock():
    """`fcntl`, or None where the platform has no advisory locks.

    Imported at the point of use, like every other flock in this repository
    (app/services/attempts.py, app/sandbox.py). A module-level import would make
    the whole CLI — including `--verify` and `--restore`, which are the tools
    somebody reaches for on a bad day — fail to import over one guarantee that
    only matters when two backups overlap.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Unix everywhere we run
        return None
    return fcntl


_UNLOCKED_CLAIM_GRACE = 86400
"""Seconds a placeholder is presumed live where locks are unavailable.

A day, because no backup run takes one and nothing here should delete a claim
that might still be in use. It buys back, coarsely, what `_flock` cannot give
on such a platform."""


def _is_live_claim(path: Path) -> bool:
    """Is this placeholder still held by a running backup?

    The mirror of the flock in `_claim_stamp`: if the lock can be taken, no
    process holds it, and the file is debris. Asked without ever writing to the
    file, and released immediately — this is a question, not a claim.

    Without flock there is nobody to ask, so age answers instead: recent means
    presumed live. Coarse, but it errs toward keeping a file rather than freeing
    a name another run is publishing under.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    lock = _flock()
    try:
        if lock is None:
            age = time.time() - path.stat().st_mtime
            return age < _UNLOCKED_CLAIM_GRACE
        try:
            lock.flock(fd, lock.LOCK_EX | lock.LOCK_NB)
        except OSError:
            return True
    finally:
        os.close(fd)
    return False


# --- primitives ------------------------------------------------------------


def sha256_of(path: Path) -> str:
    """Streaming digest: a year of lesson bundles need not fit in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _stage(suffix: str) -> tuple[Path, int]:
    """A 0600 temporary file in BACKUPS_DIR — same filesystem, so os.replace is atomic.

    Returns (path, fd). The dot prefix keeps staged files out of the
    `activity-*` / `files-*` globs, and the fd is held — and locked, like a name
    claim — for the life of the run, because these files are the one kind of
    debris worth several hundred megabytes each. A run killed between staging
    and publishing leaves them behind with no `finally` to clean up, so
    retention has to be able to tell "another backup is writing this" from
    "nobody has owned this since the power came back".
    """
    fd, name = tempfile.mkstemp(dir=BACKUPS_DIR, prefix=STAGED, suffix=suffix)
    lock = _flock()
    if lock is not None:
        try:
            lock.flock(fd, lock.LOCK_EX | lock.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
    return Path(name), fd


def _fsync_file(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    """Persist the directory's own entries — the renames, not the bytes."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(staged: Path, final: Path) -> None:
    """Give staged bytes their public name: 0600, fsynced, then renamed."""
    os.chmod(staged, 0o600)
    _fsync_file(staged)
    os.replace(staged, final)


def snapshot(dest: Path) -> None:
    """Copy DB_PATH to dest through the Online Backup API (consistent under writes)."""
    if not DB_PATH.exists():
        raise BackupError(f"no database at {DB_PATH}")
    # closing(), not `with sqlite3.connect(...)` — sqlite3's connection context
    # manager scopes a transaction, it does NOT close the connection.
    with closing(sqlite3.connect(DB_PATH)) as src, closing(sqlite3.connect(dest)) as out:
        src.backup(out)


def check_database(path: Path) -> int:
    """Run a FULL `PRAGMA integrity_check` on `path`; return its schema version.

    `integrity_check`, not `quick_check`: the cheap one skips exactly the
    cross-page and index consistency a silently damaged snapshot would show, and
    a file nobody opens until recovery day is the one place worth the full read.
    """
    try:
        with closing(sqlite3.connect(path)) as conn:
            result = [row[0] for row in conn.execute("PRAGMA integrity_check")]
            version = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error as exc:
        # Damage bad enough that SQLite will not even open the file reads as a
        # failed check, not as a different kind of problem.
        raise BackupError(f"{path.name} is not a readable database: {exc}") from exc
    if result != ["ok"]:
        raise BackupError(
            f"integrity_check failed on {path.name}: " + "; ".join(result[:5])
        )
    return version


def instance_files() -> list[str]:
    """Every file of the instance that is not the database, as sorted rel paths.

    Enumerated by exclusion, not by a list of known directories. `lessons/` was
    the obvious one, but an instance also accumulates `migrations/` (the only
    input `migrate_bundles --rollback` accepts), `lessons-attic/`, `course-raw/`
    and whatever the next feature adds beside them — and a backup that has to be
    edited every time one appears is a backup that is silently incomplete
    between edits. Two things are left out, both on purpose:

    - `backups/` — this directory. Including it would nest every set inside the
      next one.
    - `exports/` — JSONL exports are generated FROM the database that is already
      in the set, so they cost size and add no recoverable state.
    - `*.pre-restore-*` — what a forced restore preserved. A restore leaves
      those alone (`restore_owned`), so archiving them would make every backup
      after a forced restore carry a second copy of the instance it replaced,
      and a later restore of that set would try to rename an archived aside over
      the very copy it is keeping: ENOTEMPTY, mid-swap, on a half-replaced
      instance. Excluding them keeps this list and `restore_owned` exact
      complements, which is what makes the swap collision-free by construction.

    The database and its WAL sidecars are excluded too: the snapshot is the
    consistent copy of those, and restoring a live `-wal` beside it would be
    corruption dressed as completeness.
    """
    root = DATA_DIR.resolve()
    if not root.is_dir():
        return []
    skip_dirs = _excluded_dirs(root)
    skip_files = _excluded_db_names(root)
    found = []
    # os.walk, not rglob: pruning `dirnames` in place is what keeps this out of
    # `backups/` entirely rather than walking it and discarding the results —
    # the archive is being written in there as this runs. It also never follows
    # a directory symlink, so a link pointing at an ancestor cannot loop.
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        top = here == root
        dirnames[:] = [
            name for name in dirnames
            if (here / name).resolve() not in skip_dirs
            and not (top and _restore_keeps(name))
            and not (here / name).is_symlink()
        ]
        for name in filenames:
            path = here / name
            if top and _restore_keeps(name):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative in skip_files:
                continue
            found.append(relative)
    return sorted(found)


def _restore_keeps(name: str) -> bool:
    """Is this top-level name one that a restore leaves standing?

    The single predicate behind both halves of the contract: `instance_files`
    skips exactly these, and `restore_owned` displaces everything but these.
    Written once because the two must agree — a name the archive carries and a
    restore keeps is a rename onto an occupied path, thrown in the middle of the
    swap.

    `backups/` and `exports/` are the standing exclusions; the rest is
    interrupted-recovery scrap, which is state about a restore rather than state
    of the instance.
    """
    return (
        name in EXCLUDED_DIRS
        or ASIDE_MARK in name
        or name.startswith(RESTORE_TMP)
    )


def _excluded_dirs(root: Path) -> set[Path]:
    """Resolved directories the archive never enters.

    Resolved on BOTH sides of every comparison. `ACTIVITY_DATA_DIR` may be
    configured as a relative path, in which case a walked path and an absolute
    exclusion never compare equal — and the failure is silent and compounding:
    each run archives its own staging files and the previous sets, so backups
    nest inside backups.
    """
    return {BACKUPS_DIR.resolve()} | {(root / name).resolve() for name in EXCLUDED_DIRS}


def _excluded_db_names(root: Path) -> set[str]:
    """Relative names of every database file the archive must not carry.

    Two of them, and both are needed:

    - whatever `ACTIVITY_DB` currently points at, if it is under `root`. A
      differently-named live database would otherwise be archived as ordinary
      content, and the restored directory would hold both the verified snapshot
      and an unchecked raw copy at the configured path — the one a restart with
      the same override opens.
    - `activity.sqlite` itself, always, because that is the name a restore
      writes the snapshot to. Rename the database and the file it used to be
      stays behind; archived, it would be extracted straight over the verified
      snapshot in the staging directory, and `--verify` would still pass — it
      checks that the archive matches its manifest, which it would.

    A stale default-named file is therefore never backed up. That is the right
    trade: it is a leftover of a rename, the manifest says so under `excluded`,
    and the alternative is a restore that silently produces the wrong ledger.
    """
    names = {DB_FILENAME}
    try:
        names.add(DB_PATH.resolve().relative_to(root).as_posix())
    except ValueError:
        pass  # configured outside the data directory; the walk never saw it
    return {name for base in names for name in (base, *(base + s for s in _SIDECARS))}


def excluded_summary() -> list[str]:
    """How the manifest describes what it left out, for a reader months later.

    Derived from the same values the walk used, so a set taken with a renamed
    `ACTIVITY_DB` says which file it means instead of naming a default that was
    never on this disk.
    """
    root = DATA_DIR.resolve()
    databases = {DB_FILENAME}
    try:
        databases.add(DB_PATH.resolve().relative_to(root).as_posix())
    except ValueError:
        # The database lives outside the data directory; only the name a restore
        # would write to is reserved.
        pass
    return (
        [f"{name}/" for name in EXCLUDED_DIRS]
        + [f"*{ASIDE_MARK}*", f"{RESTORE_TMP}*"]
        + [f"{name}*" for name in sorted(databases)]
    )


def archive_instance(dest: Path, names: list[str]) -> tuple[list[str], list[str]]:
    """Write the instance's files to `dest` as a gzip tar, entries sorted.

    Returns (archived, vanished). The tree is not frozen while this runs — a
    lesson agent or the app may be writing into it — so a name enumerated a
    moment ago can be gone by the time it is read. Such a file is dropped from
    the archive AND from the manifest's list rather than failing the whole run,
    which keeps the manifest a description of what the archive actually holds:
    the one property `--verify` depends on.

    What this deliberately does NOT claim is a point-in-time view. The database
    half is consistent by construction (the Online Backup API); this half is a
    file-by-file copy, so a bundle rewritten mid-run can be captured mid-rewrite.
    Making it atomic would need a filesystem snapshot, which is the operator's
    layer, not this script's — docs/backup-restore.md says so, and the vanished
    list is recorded in the manifest so the seam is visible rather than assumed
    away.

    An instance with nothing but a database produces an empty archive rather
    than no file: the set is three files whatever it holds, so a reader never
    has to tell "there was nothing to archive" from "the archive went missing".
    """
    archived: list[str] = []
    vanished: list[str] = []
    with tarfile.open(dest, "w:gz") as tar:
        for name in names:
            try:
                tar.add(DATA_DIR / name, arcname=name, recursive=False)
            except FileNotFoundError:
                vanished.append(name)
                continue
            archived.append(name)
    return archived, vanished


def _extract_instance(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        # `data` refuses absolute paths, `..` escapes, links out of the tree and
        # device nodes. Backported to 3.10.12 / 3.11.4; an older patch release
        # gets the plain extract, which is why the archive's checksum is
        # verified before this runs.
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, filter="data")
        else:  # pragma: no cover - only on pre-3.10.12 patch releases
            tar.extractall(dest)


# --- writing a backup ------------------------------------------------------


def _file_entry(staged: Path, name: str) -> dict:
    return {
        "name": name,
        "bytes": staged.stat().st_size,
        "sha256": sha256_of(staged),
    }


def create_backup() -> Path:
    """Write one complete backup set; return the manifest path.

    Order is the contract: stage the snapshot, prove it opens and passes
    integrity_check, archive the instance files, hash both, and only then write the
    manifest — last, and by rename. Every earlier step can fail without leaving
    behind anything a reader would call a backup.
    """
    _load_live_paths()
    # 0700: the directory holds whole copies of a private ledger, and its own
    # mode is the only thing protecting the names inside it — and, if it is
    # group-writable, the sets themselves: another local user cannot read a 0600
    # file but can still unlink it and put their own there.
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkdir's mode applies only when it creates the directory. On an upgrade
    # this directory already exists, made by the previous version of this script
    # under whatever umask the operator had, so the mode has to be asserted
    # rather than requested.
    if stat.S_IMODE(BACKUPS_DIR.stat().st_mode) & 0o077:
        os.chmod(BACKUPS_DIR, 0o700)
    # Sweep wreckage before allocating another snapshot/archive. If an
    # interrupted run filled the filesystem with hidden staged copies, waiting
    # until post-backup retention means the next run cannot make enough progress
    # to reach the cleanup that would let it self-heal.
    sweep_staging_debris()
    stamp, claim_fd = _claim_stamp()
    staged_db, db_fd = _stage(".sqlite")
    staged_files, files_fd = _stage(".tar.gz")
    staged_manifest, manifest_fd = _stage(".json")
    try:
        snapshot(staged_db)
        schema_version = check_database(staged_db)

        archived, vanished = archive_instance(staged_files, instance_files())

        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "created_at": now_iso(),
            "stamp": stamp,
            "schema_version": schema_version,
            "source": {"database": str(DB_PATH), "data_dir": str(DATA_DIR)},
            "files": {
                "database": _file_entry(staged_db, db_name(stamp)),
                "instance": _file_entry(staged_files, instance_name(stamp)),
            },
            "instance_files": archived,
            # Enumerated, then gone before it could be read — a lesson bundle
            # being rewritten while the backup ran. Recorded, not hidden.
            "instance_files_vanished": vanished,
            # Self-describing, so a reader never has to guess whether an absent
            # path was excluded by contract or lost by accident.
            "excluded": excluded_summary(),
        }
        staged_manifest.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        _publish(staged_db, BACKUPS_DIR / db_name(stamp))
        _publish(staged_files, BACKUPS_DIR / instance_name(stamp))
        # Last, and only now: the set becomes a backup the instant this exists.
        _publish(staged_manifest, BACKUPS_DIR / manifest_name(stamp))
        _fsync_dir(BACKUPS_DIR)
    finally:
        # After a successful publish these names are already gone; after a
        # failure they are the partial work, and nothing else refers to them.
        for staged in (staged_db, staged_files, staged_manifest):
            staged.unlink(missing_ok=True)
        # Last: the stamp and the staged files stay owned until everything
        # published under them is on disk. Released here, or by the kernel if
        # this process dies — which is what makes the leftovers of a killed run
        # recognizable as debris rather than as somebody's work in progress.
        for fd in (db_fd, files_fd, manifest_fd, claim_fd):
            os.close(fd)
    return BACKUPS_DIR / manifest_name(stamp)


# --- reading a backup ------------------------------------------------------


def load_manifest(path: Path) -> dict:
    """Parse and sanity-check a manifest; raise BackupError on anything unusable."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BackupError(f"unreadable manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise BackupError(f"manifest is not an object: {path}")
    version = manifest.get("manifest_version")
    if version != MANIFEST_VERSION:
        raise BackupError(
            f"manifest version {version!r} is not {MANIFEST_VERSION}: {path}"
        )
    for role in ("database", "instance"):
        entry = manifest.get("files", {}).get(role)
        if not isinstance(entry, dict) or not entry.get("name"):
            raise BackupError(f"manifest names no {role} file: {path}")
    return manifest


def verify(manifest_path: Path) -> dict:
    """Prove a set is whole and undamaged. Reads only; returns the manifest.

    This is the periodic test-restore in its cheap form: run it on a schedule
    and a backup that rotted — truncated, half-copied, bit-flipped — is found
    while the source it was taken from still exists, not on the day it is
    needed.
    """
    manifest = load_manifest(manifest_path)
    directory = manifest_path.parent
    for role in ("database", "instance"):
        entry = manifest["files"][role]
        path = directory / entry["name"]
        if not path.is_file():
            raise BackupError(f"{role} file is missing: {path}")
        size = path.stat().st_size
        if size != entry["bytes"]:
            raise BackupError(
                f"{role} file is {size} bytes, manifest says {entry['bytes']}: {path}"
            )
        digest = sha256_of(path)
        if digest != entry["sha256"]:
            raise BackupError(
                f"{role} file checksum mismatch: {path}\n"
                f"  manifest {entry['sha256']}\n  on disk  {digest}"
            )
    # Checksums prove the bytes are the bytes that were written. Only opening
    # the files proves those bytes are still a database and still an archive of
    # what the manifest says — a checksum over a tar cannot tell you the tar
    # holds the lesson files the manifest lists.
    version = check_database(directory / manifest["files"]["database"]["name"])
    if version != manifest.get("schema_version"):
        # The checksums cover the member files, not the manifest describing
        # them. Without this, a manifest edited to claim schema 999 over a
        # schema-16 snapshot passes verification and is believed by whoever
        # reads it to decide whether this set predates a migration.
        raise BackupError(
            f"manifest claims schema v{manifest.get('schema_version')} but the "
            f"snapshot is at v{version}: {manifest_path}"
        )
    archive = directory / manifest["files"]["instance"]["name"]
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = sorted(tar.getnames())
    except tarfile.TarError as exc:
        raise BackupError(f"{archive.name} is not a readable archive: {exc}") from exc
    if members != sorted(manifest["instance_files"]):
        missing = sorted(set(manifest["instance_files"]) - set(members))
        extra = sorted(set(members) - set(manifest["instance_files"]))
        raise BackupError(
            f"{archive.name} does not hold what the manifest lists: "
            f"{len(missing)} missing, {len(extra)} unexpected"
            + (f" (first missing: {missing[0]})" if missing else "")
        )
    return manifest


def list_sets() -> list[Path]:
    """Complete backup sets, oldest first.

    Manifest-driven: a set exists when its manifest does, so a half-written run
    is never counted as a backup. The stamp is the name's whole variable part
    and sorts chronologically.
    """
    if not BACKUPS_DIR.is_dir():
        return []
    return sorted(BACKUPS_DIR.glob("activity-*.manifest.json"), key=lambda p: p.name)


def unclaimed_files() -> list[Path]:
    """Backup-shaped files in BACKUPS_DIR that no manifest names.

    Two very different things land here and only one is disposable:

    - abandoned name claims — the empty placeholder `_claim_stamp` leaves when a
      run dies before publishing anything, which is always exactly zero bytes;
    - **snapshots written by the pre-manifest version of this script**, which
      used this very name and shipped no manifest at all. Those are somebody's
      only restore points for everything older than this upgrade.

    Nothing here can tell the second kind from an interrupted new-format run, so
    retention does not guess: it removes the empty placeholders and reports the
    rest, leaving the operator to look and decide. The dot-prefixed staging
    files of a run in progress match no glob here.

    A placeholder another run still holds is not listed at all: it is neither
    debris nor an old snapshot, just a backup that has not finished. Reporting
    it would alarm the reader, and deleting it — which is what `--keep` does to
    everything else that is empty — would hand its stamp to the next run.
    """
    if not BACKUPS_DIR.is_dir():
        return []
    claimed = set()
    for path in list_sets():
        stamp = stamp_of(path.name)
        claimed.update((db_name(stamp), instance_name(stamp)))
    return sorted(
        path
        for path in list(BACKUPS_DIR.glob("activity-*.sqlite"))
        + list(BACKUPS_DIR.glob("files-*.tar.gz"))
        if path.name not in claimed and not _is_live_claim(path)
    )


def staging_debris() -> list[Path]:
    """`.staged-*` files in BACKUPS_DIR that no run is holding open.

    Unlike an unclaimed `activity-*.sqlite`, there is nothing to weigh here: the
    name is produced by `_stage` and by nothing else — no operator, no earlier
    version of this script — so a staged file nobody owns is the wreckage of a
    run that was killed before its `finally` could run. Left alone it is
    invisible (the dot prefix keeps it out of every other listing) and can be as
    large as the database, once per interruption, until the disk fills.
    """
    if not BACKUPS_DIR.is_dir():
        return []
    return sorted(
        path for path in BACKUPS_DIR.glob(f"{STAGED}*") if not _is_live_claim(path)
    )


def sweep_staging_debris() -> list[Path]:
    """Remove only abandoned staged files; live runs retain their locks."""
    deleted = staging_debris()
    for path in deleted:
        path.unlink(missing_ok=True)
    return deleted


def prune(keep: int) -> tuple[list[str], list[Path], list[Path]]:
    """Keep the `keep` newest sets.

    Returns (dropped stamps, deleted paths, unclaimed paths left in place).
    Sets are dropped whole, manifest first, so a set being deleted is never
    momentarily indistinguishable from a complete one. What no manifest claims
    is left alone except for what only this script could have written and only
    by dying: empty name claims and staged files nobody holds — see
    `unclaimed_files` and `staging_debris`.
    """
    if keep <= 0:
        return [], [], unclaimed_files()
    manifests = list_sets()
    doomed = manifests[:-keep] if len(manifests) > keep else []

    deleted: list[Path] = []
    stamps: list[str] = []
    for path in doomed:
        stamp = stamp_of(path.name)
        stamps.append(stamp)
        path.unlink(missing_ok=True)       # first: the set stops being a set
        deleted.append(path)
        for name in (db_name(stamp), instance_name(stamp)):
            member = BACKUPS_DIR / name
            if member.exists():
                member.unlink()
                deleted.append(member)

    deleted.extend(sweep_staging_debris())

    left: list[Path] = []
    for path in unclaimed_files():
        if path.suffix == ".sqlite" and path.stat().st_size == 0:
            path.unlink()                  # an abandoned name claim, nothing more
            deleted.append(path)
        else:
            left.append(path)
    return stamps, deleted, left


# --- restoring -------------------------------------------------------------


def restore_owned(target: Path) -> list[Path]:
    """Everything in `target` that a restore replaces, top level only.

    The complement of what the backup side excludes, and deliberately so: a
    restore promises the instance the set describes, not a merge of that set
    with whatever the directory happened to accumulate afterwards. Enumerating
    only the names the archive carries would leave a `lessons/` tree created
    after the backup — or present when the backup held none — sitting beside a
    database that knows nothing about it, which is a hybrid instance wearing the
    word "restored".

    `backups/` and `exports/` stay: the first is usually where the set being
    restored lives, and both are excluded from the archive, so displacing them
    would destroy state no restore can give back. Previously preserved copies
    stay too, or a second forced restore would move aside the first one's.
    """
    if not target.is_dir():
        return []
    return sorted(
        path for path in target.iterdir() if not _restore_keeps(path.name)
    )


def _reserve_asides(paths: list[Path], stamp: str) -> list[Path]:
    """A free `.pre-restore-<stamp>` name for each path, none colliding.

    Chosen against both the filesystem and the names already handed out in this
    call, so a second forced restore in the same second cannot land on the
    first one's preserved copies.
    """
    taken: set[Path] = set()
    reserved: list[Path] = []
    for path in paths:
        for n in range(1, 1000):
            suffix = "" if n == 1 else f"-{n}"
            candidate = path.with_name(f"{path.name}.pre-restore-{stamp}{suffix}")
            if candidate not in taken and not candidate.exists():
                break
        else:
            raise BackupError(f"no free name to preserve {path.name} under")
        taken.add(candidate)
        reserved.append(candidate)
    return reserved


def restore(manifest_path: Path, target: Path, *, force: bool = False) -> dict:
    """Rebuild an instance directory from a verified set. Never touches a service.

    The caller stops the app first — this overwrites the database file a running
    process holds open, and SQLite has no defence against that. The check cannot
    live here: nothing in a data directory says whether a process is using it.

    Existing artifacts are moved aside, never deleted. A restore aimed at the
    wrong directory is a mistake, and a mistake must stay recoverable.
    """
    manifest = verify(manifest_path)
    directory = manifest_path.parent
    target_existed = target.exists()
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not target_existed:
        # Be explicit even though 0700 is already immune to a permissive umask:
        # the restored files are private instance state, and their source modes
        # commonly rely on the containing data directory for confidentiality.
        os.chmod(target, 0o700)

    db_target = target / DB_FILENAME
    tops = sorted({name.split("/", 1)[0] for name in manifest["instance_files"]})
    # A restore killed partway leaves its staging tree behind. Nothing here
    # deletes it — deleting things in a data directory is not a restore's job —
    # but it is worth a sentence in the output, because it is invisible
    # otherwise and holds a whole second copy of a backup.
    stale_staging = sorted(
        path.name for path in target.iterdir() if path.name.startswith(RESTORE_TMP)
    )
    # Everything the restore takes over, not just the names this set happens to
    # carry — see restore_owned().
    occupied = restore_owned(target)
    if occupied and not force:
        shown = [path.name for path in occupied[:5]]
        if len(occupied) > 5:
            shown.append(f"and {len(occupied) - 5} more")
        raise BackupError(
            "target already holds "
            + ", ".join(shown)
            + f" — refusing to overwrite {target}.\n"
            "  Restore into an empty directory, or pass --force to move the "
            "existing files aside (they are kept, not deleted)."
        )

    # Build BOTH restored artifacts before displacing anything. A copy or an
    # extraction can fail halfway — a full disk is the ordinary way — and doing
    # it in place would leave the target holding neither the old instance (moved
    # aside under names the failed run never reported) nor a usable new one.
    staging = Path(tempfile.mkdtemp(dir=target, prefix=RESTORE_TMP))
    try:
        # Extract FIRST, then lay the snapshot down. The archive cannot carry a
        # database — `_excluded_db_names` reserves the name — but if a set from
        # some other version ever did, the file the restore promises is the one
        # that was verified, not one that happened to be extracted later.
        _extract_instance(directory / manifest["files"]["instance"]["name"], staging)
        staged_db = staging / DB_FILENAME
        shutil.copyfile(directory / manifest["files"]["database"]["name"], staged_db)
        os.chmod(staged_db, 0o600)

        # Everything moves aside before anything moves in, which is also what
        # takes the WAL sidecars out of the way: a `-wal` left beside a replaced
        # database would be replayed into it, corruption dressed as recovery.
        # Every destination is chosen BEFORE anything moves, because
        # `now_stamp()` resolves to the second: an immediate retry of a forced
        # restore would otherwise pick the same aside names and replace straight
        # over the copies the first attempt had just preserved.
        displaced = occupied
        # Every destination has to be free once the displacement is done. It is,
        # by construction — the archive carries nothing a restore keeps — but a
        # rename onto a non-empty directory fails with ENOTEMPTY, and it would
        # fail in the middle of the swap, with the instance half replaced and
        # the rollback only able to undo the moves. One stat per entry buys the
        # failure BEFORE anything moves.
        keeping = {path.name for path in target.iterdir()} - {
            path.name for path in displaced
        }
        clash = sorted(path.name for path in staging.iterdir()
                       if path.name in keeping)
        if clash:
            raise BackupError(
                f"this set carries {', '.join(clash)}, which a restore does not "
                f"displace — refusing to restore it into {target} rather than "
                "fail halfway through the swap"
            )
        asides = _reserve_asides(displaced, now_stamp())
        moved: list[Path] = []
        try:
            for path, aside in zip(displaced, asides):
                os.replace(path, aside)
                moved.append(aside)
            # Renames within one directory: the swap itself cannot half-fail the
            # way the copies above can.
            for path in sorted(staging.iterdir()):
                os.replace(path, target / path.name)
        except BaseException:
            for path, aside in zip(displaced, moved):
                if aside.exists() and not path.exists():
                    os.replace(aside, path)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {
        "manifest": manifest,
        "database": db_target,
        "restored": tops,
        "moved_aside": [path.name for path in moved],
        "stale_staging": stale_staging,
    }


# --- CLI -------------------------------------------------------------------


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def _print_set(manifest: dict) -> None:
    files = manifest["files"]
    print(f"  schema v{manifest['schema_version']}, written {manifest['created_at']}")
    print(f"  database: {files['database']['name']} "
          f"({_human(files['database']['bytes'])})")
    print(f"  files:    {files['instance']['name']} "
          f"({_human(files['instance']['bytes'])}, "
          f"{len(manifest['instance_files'])} files)")
    vanished = manifest.get("instance_files_vanished") or []
    if vanished:
        print(f"  NOTE: {len(vanished)} file(s) were rewritten or removed "
              "while this ran and are not in the archive:")
        for name in vanished[:5]:
            print(f"    {name}")


def _report_unclaimed(paths: list[Path]) -> None:
    if not paths:
        return
    print(f"{len(paths)} file(s) here belong to no manifest and were LEFT IN PLACE:")
    for path in paths:
        print(f"  {path.name} ({_human(path.stat().st_size)})")
    print("  These are either snapshots from the pre-manifest version of this")
    print("  script — still restorable by hand, and not replaced by anything")
    print("  here — or leftovers of an interrupted run. Retention will not")
    print("  guess which; delete them yourself once you have looked.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Full backup of the Ephemeris instance (database + its files).",
        epilog="Stop the service before restoring: systemctl --user stop ephemeris",
    )
    ap.add_argument("--keep", type=int, default=0, metavar="N",
                    help="after writing, keep only the N newest backup sets")
    ap.add_argument("--list", action="store_true",
                    help="list the backup sets on disk and exit")
    ap.add_argument("--verify", type=Path, metavar="MANIFEST",
                    help="re-check one set's checksums and integrity; writes nothing")
    ap.add_argument("--restore", type=Path, metavar="MANIFEST",
                    help="restore one verified set (stop the service first)")
    ap.add_argument("--into", type=Path, metavar="DIR",
                    help="restore destination (default: $ACTIVITY_DATA_DIR)")
    ap.add_argument("--force", action="store_true",
                    help="restore over an occupied directory, moving what is "
                         "there aside as *.pre-restore-<stamp>")
    args = ap.parse_args()

    action = ("VERIFY" if args.verify else "RESTORE" if args.restore else "BACKUP")
    try:
        if args.list:
            _load_live_paths()
            sets = list_sets()
            if not sets:
                print(f"no backup sets in {BACKUPS_DIR}")
            for path in reversed(sets):
                print(path.name)
                _print_set(load_manifest(path))
            _report_unclaimed(unclaimed_files())
            debris = staging_debris()
            if debris:
                total = sum(path.stat().st_size for path in debris)
                print(f"{len(debris)} unfinished file(s) from interrupted runs "
                      f"({_human(total)}); the next --keep removes them.")
            return 0

        if args.verify:
            manifest = verify(args.verify.expanduser())
            print(f"VERIFIED {args.verify}")
            _print_set(manifest)
            return 0

        if args.restore:
            if args.into is None:
                _load_live_paths()
            target = (args.into or DATA_DIR).expanduser()
            result = restore(args.restore.expanduser(), target, force=args.force)
            print(f"RESTORED into {target}")
            _print_set(result["manifest"])
            for name in result["moved_aside"]:
                print(f"  kept the previous file as {name}")
            for name in result["stale_staging"]:
                print(f"  NOTE: {name} is an interrupted restore's staging copy. "
                      "Nothing uses it; delete it when you like.")
            print("  start the service now: systemctl --user start ephemeris")
            return 0

        path = create_backup()
        print(f"backed up {DB_PATH} -> {path.parent}")
        _print_set(load_manifest(path))
        print(f"  manifest: {path.name}")
        if args.keep > 0:
            stamps, _, left = prune(args.keep)
            for stamp in stamps:
                print(f"pruned backup set {stamp}")
            _report_unclaimed(left)
    except (BackupError, OSError, sqlite3.Error, tarfile.TarError) as exc:
        print(f"{action} FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
