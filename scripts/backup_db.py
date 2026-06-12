"""Consistent, timestamped backup of the live SQLite ledger.

The database runs in WAL mode under a live service, so a plain file copy can
capture an inconsistent snapshot (recent writes still live in the -wal file).
This uses SQLite's Online Backup API (`Connection.backup`) instead, which takes
a transactionally consistent copy even while the app is reading/writing.

Usage:
    python -m scripts.backup_db                 # snapshot -> data/backups/
    python -m scripts.backup_db --keep 20       # prune to the 20 newest
    python -m scripts.backup_db --out /path.db  # explicit destination file

Backups land in data/backups/, which is already covered by the data/ gitignore
rule, so private runtime data never reaches the public Git layer.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

# Reuse the app's path resolution + timestamp so backups follow ACTIVITY_DB and
# match the rest of the ledger's clock (sec13.3).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import DB_PATH, DATA_DIR, now_stamp  # noqa: E402

BACKUPS_DIR = DATA_DIR / "backups"


def backup(dest: Path) -> Path:
    """Write a consistent snapshot of DB_PATH to dest via the Online Backup API."""
    if not DB_PATH.exists():
        raise SystemExit(f"no database at {DB_PATH}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # closing(), not `with sqlite3.connect(...)` — sqlite3's connection context
    # manager scopes a transaction, it does NOT close the connection.
    with closing(sqlite3.connect(DB_PATH)) as src, closing(sqlite3.connect(dest)) as out:
        src.backup(out)              # atomic, consistent even under concurrent writes
    return dest


def prune(keep: int) -> list[Path]:
    """Delete all but the `keep` newest auto-named backups; return removed paths."""
    snaps = sorted(
        BACKUPS_DIR.glob("activity-*.sqlite"),
        key=lambda p: p.name,        # name carries the timestamp, so lexicographic == chronological
    )
    removed = snaps[:-keep] if keep > 0 else []
    for p in removed:
        p.unlink()
    return removed


def main() -> None:
    ap = argparse.ArgumentParser(description="Back up the SQLite ledger consistently.")
    ap.add_argument("--out", type=Path, help="explicit destination file (skips pruning)")
    ap.add_argument("--keep", type=int, default=0,
                    help="keep only the N newest auto-named backups (0 = keep all)")
    args = ap.parse_args()

    dest = args.out or (BACKUPS_DIR / f"activity-{now_stamp()}.sqlite")
    out = backup(dest)
    size_kb = out.stat().st_size / 1024
    print(f"backed up {DB_PATH} -> {out} ({size_kb:.0f} KB)")

    if args.out is None and args.keep > 0:
        for p in prune(args.keep):
            print(f"pruned old backup {p.name}")


if __name__ == "__main__":
    main()
