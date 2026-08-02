"""Full backup of the Ephemeris instance: the ledger, the lesson bundles, a manifest.

The JSONL export is an audit stream and says so (docs/restore-from-export.md).
This is the other thing: a byte-faithful copy of everything an instance holds,
written so that a set found on disk months later can be trusted without being
tried.

One backup is THREE files sharing one stamp, in `$ACTIVITY_DATA_DIR/backups/`:

    activity-<stamp>.sqlite           consistent snapshot of the ledger
    lessons-<stamp>.tar.gz            $ACTIVITY_DATA_DIR/lessons, verbatim
    activity-<stamp>.manifest.json    what the set is, and what it should hash to

The manifest is written LAST, by rename. That single ordering rule is the whole
durability contract: a manifest on disk is a promise that the two files it names
are complete and match their checksums, and anything in `backups/` that no
manifest names is debris from an interrupted run, which `--keep` is free to
remove. Nothing is ever written under its final name — every file is staged in
the same directory, fsynced, chmod 0600, and moved into place with os.replace.

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
import sys
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path

# Reuse the app's path resolution + timestamp so backups follow ACTIVITY_DB and
# match the rest of the ledger's clock (sec13.3).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import DATA_DIR, DB_PATH, now_iso, now_stamp  # noqa: E402

BACKUPS_DIR = DATA_DIR / "backups"
LESSONS_DIR = DATA_DIR / "lessons"
DB_FILENAME = "activity.sqlite"
LESSONS_DIRNAME = "lessons"

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


# --- naming ----------------------------------------------------------------


def db_name(stamp: str) -> str:
    return f"activity-{stamp}.sqlite"


def lessons_name(stamp: str) -> str:
    return f"lessons-{stamp}.tar.gz"


def manifest_name(stamp: str) -> str:
    return f"activity-{stamp}.manifest.json"


def stamp_of(manifest_filename: str) -> str:
    """The shared stamp inside a manifest's filename."""
    return manifest_filename[len("activity-"):-len(".manifest.json")]


def _claim_stamp() -> str:
    """Reserve a free stamp by creating the snapshot's name, empty and exclusive.

    `now_stamp()` resolves to the second, so two backups in one second are a
    real collision — the same one the JSONL export hit (#17, first half). O_EXCL
    settles it: the first run owns the bare stamp, the next gets `-2`. The
    placeholder is overwritten by os.replace at the end; if the run dies before
    that, it is a file no manifest claims, which is exactly what `--keep` sweeps
    up.
    """
    base = now_stamp()
    for n in range(1, 1000):
        stamp = base if n == 1 else f"{base}-{n}"
        try:
            fd = os.open(BACKUPS_DIR / db_name(stamp),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(fd)
        return stamp
    raise BackupError(f"every backup name for {base} is taken (999 tried)")


# --- primitives ------------------------------------------------------------


def sha256_of(path: Path) -> str:
    """Streaming digest: a year of lesson bundles need not fit in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _stage(suffix: str) -> Path:
    """A 0600 temporary file in BACKUPS_DIR — same filesystem, so os.replace is atomic.

    The dot prefix keeps staged files out of the `activity-*` / `lessons-*`
    globs, so a concurrent `--keep` cannot mistake a run in progress for debris.
    """
    fd, name = tempfile.mkstemp(dir=BACKUPS_DIR, prefix=".staged-", suffix=suffix)
    os.close(fd)
    return Path(name)


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


def lesson_files() -> list[str]:
    """Every regular file under the lessons directory, as sorted relative paths."""
    if not LESSONS_DIR.is_dir():
        return []
    found = [
        path.relative_to(LESSONS_DIR).as_posix()
        for path in LESSONS_DIR.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]
    return sorted(found)


def archive_lessons(dest: Path, names: list[str]) -> None:
    """Write the lesson bundles to `dest` as a gzip tar, entries in sorted order.

    An absent lessons directory produces an empty archive rather than no file:
    the set is three files whatever the instance holds, so a reader never has to
    tell "this instance had no lessons" from "the lessons file went missing".
    """
    with tarfile.open(dest, "w:gz") as tar:
        for name in names:
            tar.add(LESSONS_DIR / name, arcname=name, recursive=False)


def _extract_lessons(archive: Path, dest: Path) -> None:
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
    integrity_check, archive the lessons, hash both, and only then write the
    manifest — last, and by rename. Every earlier step can fail without leaving
    behind anything a reader would call a backup.
    """
    # 0700: the directory holds whole copies of a private ledger, and its own
    # mode is the only thing protecting the names inside it.
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = _claim_stamp()
    staged_db = _stage(".sqlite")
    staged_lessons = _stage(".tar.gz")
    staged_manifest = _stage(".json")
    try:
        snapshot(staged_db)
        schema_version = check_database(staged_db)

        names = lesson_files()
        archive_lessons(staged_lessons, names)

        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "created_at": now_iso(),
            "stamp": stamp,
            "schema_version": schema_version,
            "source": {"database": str(DB_PATH), "lessons": str(LESSONS_DIR)},
            "files": {
                "database": _file_entry(staged_db, db_name(stamp)),
                "lessons": _file_entry(staged_lessons, lessons_name(stamp)),
            },
            "lesson_files": names,
        }
        staged_manifest.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        _publish(staged_db, BACKUPS_DIR / db_name(stamp))
        _publish(staged_lessons, BACKUPS_DIR / lessons_name(stamp))
        # Last, and only now: the set becomes a backup the instant this exists.
        _publish(staged_manifest, BACKUPS_DIR / manifest_name(stamp))
        _fsync_dir(BACKUPS_DIR)
    finally:
        # After a successful publish these names are already gone; after a
        # failure they are the partial work, and nothing else refers to them.
        for staged in (staged_db, staged_lessons, staged_manifest):
            staged.unlink(missing_ok=True)
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
    for role in ("database", "lessons"):
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
    for role in ("database", "lessons"):
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
    # the file proves those bytes are still a database.
    check_database(directory / manifest["files"]["database"]["name"])
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


def prune(keep: int) -> tuple[list[str], list[Path]]:
    """Keep the `keep` newest sets; return (dropped stamps, deleted paths).

    Sets are dropped whole, manifest first, so a set being deleted is never
    momentarily indistinguishable from a complete one. Files no manifest claims
    are debris from an interrupted run and go with them; the dot-prefixed
    staging files of a run in progress match no glob here.
    """
    if keep <= 0:
        return [], []
    manifests = list_sets()
    doomed = manifests[:-keep] if len(manifests) > keep else []
    survivors = manifests[len(doomed):]

    deleted: list[Path] = []
    stamps: list[str] = []
    for path in doomed:
        stamp = stamp_of(path.name)
        stamps.append(stamp)
        path.unlink(missing_ok=True)       # first: the set stops being a set
        deleted.append(path)
        for name in (db_name(stamp), lessons_name(stamp)):
            member = BACKUPS_DIR / name
            if member.exists():
                member.unlink()
                deleted.append(member)

    claimed = set()
    for path in survivors:
        stamp = stamp_of(path.name)
        claimed.update((db_name(stamp), lessons_name(stamp)))
    orphans = sorted(
        list(BACKUPS_DIR.glob("activity-*.sqlite"))
        + list(BACKUPS_DIR.glob("lessons-*.tar.gz"))
    )
    for orphan in orphans:
        if orphan.name not in claimed:
            orphan.unlink()
            deleted.append(orphan)
    return stamps, deleted


# --- restoring -------------------------------------------------------------


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
    target.mkdir(parents=True, exist_ok=True)

    db_target = target / DB_FILENAME
    lessons_target = target / LESSONS_DIRNAME
    occupied = [path for path in (db_target, lessons_target) if path.exists()]
    if occupied and not force:
        raise BackupError(
            "target already holds "
            + ", ".join(path.name for path in occupied)
            + f" — refusing to overwrite {target}.\n"
            "  Restore into an empty directory, or pass --force to move the "
            "existing files aside (they are kept, not deleted)."
        )

    moved: list[str] = []
    stamp = now_stamp()
    # The sidecars go first: a -wal left beside a moved-away database would be
    # replayed into the restored one, which is corruption dressed as recovery.
    sidecars = [db_target.with_name(DB_FILENAME + suffix) for suffix in _SIDECARS]
    for path in [p for p in sidecars if p.exists()] + occupied:
        aside = path.with_name(f"{path.name}.pre-restore-{stamp}")
        os.replace(path, aside)
        moved.append(aside.name)

    shutil.copyfile(directory / manifest["files"]["database"]["name"], db_target)
    os.chmod(db_target, 0o600)
    _extract_lessons(directory / manifest["files"]["lessons"]["name"], lessons_target)
    return {
        "manifest": manifest,
        "database": db_target,
        "lessons": lessons_target,
        "moved_aside": moved,
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
    print(f"  lessons:  {files['lessons']['name']} "
          f"({_human(files['lessons']['bytes'])}, "
          f"{len(manifest['lesson_files'])} files)")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Full backup of the Ephemeris instance (database + lessons).",
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
                    help=f"restore destination (default: {DATA_DIR})")
    ap.add_argument("--force", action="store_true",
                    help="restore over an occupied directory, moving what is "
                         "there aside as *.pre-restore-<stamp>")
    args = ap.parse_args()

    action = ("VERIFY" if args.verify else "RESTORE" if args.restore else "BACKUP")
    try:
        if args.list:
            sets = list_sets()
            if not sets:
                print(f"no backup sets in {BACKUPS_DIR}")
            for path in reversed(sets):
                print(path.name)
                _print_set(load_manifest(path))
            return 0

        if args.verify:
            manifest = verify(args.verify.expanduser())
            print(f"VERIFIED {args.verify}")
            _print_set(manifest)
            return 0

        if args.restore:
            target = (args.into or DATA_DIR).expanduser()
            result = restore(args.restore.expanduser(), target, force=args.force)
            print(f"RESTORED into {target}")
            _print_set(result["manifest"])
            for name in result["moved_aside"]:
                print(f"  kept the previous file as {name}")
            print("  start the service now: systemctl --user start ephemeris")
            return 0

        path = create_backup()
        print(f"backed up {DB_PATH} -> {path.parent}")
        _print_set(load_manifest(path))
        print(f"  manifest: {path.name}")
        if args.keep > 0:
            stamps, deleted = prune(args.keep)
            for stamp in stamps:
                print(f"pruned backup set {stamp}")
            unclaimed = len(deleted) - 3 * len(stamps)
            if unclaimed > 0:
                print(f"removed {unclaimed} file(s) belonging to no manifest")
    except (BackupError, OSError, sqlite3.Error, tarfile.TarError) as exc:
        print(f"{action} FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
