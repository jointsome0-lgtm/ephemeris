# Full backup and restore

Ephemeris has two recovery paths, and they are not interchangeable.

| | **Full backup** (this document) | **JSONL export** ([contract](restore-from-export.md)) |
|---|---|---|
| What it is | A byte copy of the ledger plus every other file the instance holds | The append-only audit stream, one event per line |
| Fidelity | Complete: every table, every column, every file | Partial and honestly documented — some tables cannot be reconstructed at all |
| Readable by | SQLite | Anything; it is plain text you can grep |
| Use it to | Recover the instance | Audit history, move a subset, feed another tool |

If the question is *"my disk died, how do I get my ledger back"*, the answer is a
full backup. The export is not a substitute and never claimed to be.

## What one backup is

Three files sharing one stamp, in `$ACTIVITY_DATA_DIR/backups/`:

```
activity-2026-08-02-031500.sqlite           the ledger, snapshotted
files-2026-08-02-031500.tar.gz              everything else under $ACTIVITY_DATA_DIR
activity-2026-08-02-031500.manifest.json    what the set is and what it hashes to
```

The manifest is the source of truth about the set: its format version, the
schema version (`PRAGMA user_version`) the snapshot carries, when it was
written, the size and SHA-256 of both other files, and the list of every file
inside the archive.

The archive is defined by **exclusion**, not by a list of known directories.
`lessons/` is the obvious one, but an instance also accumulates `migrations/`
(the only input `migrate_bundles --rollback` accepts), `lessons-attic/`,
`course-raw/`, projection caches, and whatever the next feature adds beside
them. Enumerating those by name would mean a backup that is silently incomplete
between edits, so what is left out is left out deliberately and the manifest
names it under `excluded`:

- `backups/` — this directory; including it would nest every set in the next.
- `exports/` — JSONL exports are generated *from* the database that is already
  in the set, so they cost size and add no recoverable state.
- `lesson-builds/*/node_modules/` — a lesson's **installed** packages, and only
  those. This is derived state: a build reinstalls it. It is skipped rather than
  archived because the archive carries no symlinks (below) and a package tree's
  internal links — the `.bin/` shims and the like — are symlinks, so archiving
  it would produce a restore that looks complete and does not run.

  The `package.json` and lockfile beside it **are** in the set. They are the
  record of what the lesson added, and the input the reinstall reads: after a
  restore, the lesson's next build runs `bun install` against them and comes
  back with the same packages. Dropping the whole workspace instead would leave
  a restored lesson importing packages it could no longer name.
- `*.pre-restore-*` — copies a forced restore preserved. They are scrap you
  delete once satisfied, not instance state, and archiving them would make every
  backup after a forced restore carry a second copy of the instance it replaced.
- `.restore-tmp-*` — the tree a restore builds before swapping it in. One only
  survives a restore that was killed; it holds a copy of a backup set, so it is
  reported and left for you rather than archived or deleted.
- the database and its `-wal` / `-shm` sidecars — the snapshot is the consistent
  copy of those, and restoring a live `-wal` beside it would be corruption
  dressed as completeness. Read from `ACTIVITY_DB`, not from the name
  `activity.sqlite`, so a renamed ledger is excluded too; the manifest's
  `excluded` list names the files this particular set left out. `activity.sqlite`
  is reserved either way, because that is the name a restore writes the snapshot
  to — so if you rename the database, the file it used to be is left out of the
  backup rather than restored over the snapshot that was verified.

**Regular files only.** The archive carries no symlink, in any directory: a link
is a target the walk has not proved is inside the instance, and following or
recreating one on restore is how a backup writes outside the tree it is meant to
restore. That rule is what makes an installed package tree the one skipped
subtree above rather than a directory this set carries in half — anywhere else
under the data directory, the instance's own state is regular files.

**The manifest is written last, by rename.** That one rule is the durability
contract: a manifest on disk is a promise that the two files it names are
complete and match their checksums, and nothing in `backups/` without one is a
backup. Nothing is ever written under its final name — each file is staged in
the same directory, fsynced, set to mode `0600`, and moved into place
atomically.

The database half is consistent by construction. The file half is a
file-by-file copy of a tree that a lesson agent may be writing into, so a bundle
rewritten mid-run can be captured mid-rewrite; a file that disappears between
enumeration and reading is dropped from both the archive and the manifest's
list, and named under `instance_files_vanished` so the seam is visible.
Point-in-time consistency across the whole tree would need a filesystem snapshot
(LVM, btrfs, ZFS), which is the operator's layer. If that matters to you, take
the backup when nothing is editing lessons — the timer's small hours are already
close to that.

A lesson's own git repository (`lessons/<slug>/.git`, #186) is part of that tree
and inherits the same seam, with one shape worth naming: a commit landing
between enumeration and archiving can put an updated branch ref in the set while
the objects it points at were never enumerated, and the restored repository then
reports a bad object rather than a partial file. The lesson's content is intact
— every page and learner file is a file of its own — so the fix is to drop the
history and let the app build a fresh repository on the next read:
`rm -rf <data-dir>/lessons/<slug>/.git`. Backups deliberately keep archiving
these repositories: a rare broken history that can be discarded in one command
is worth more than never restoring any history at all.

Before a snapshot is allowed to claim a name it is opened and run through a full
`PRAGMA integrity_check`. A backup that cannot be read fails the night it is
written, while the source still exists, instead of on the day it is needed.

The snapshot goes through SQLite's Online Backup API, which is transactionally
consistent even while the service is writing. **Do not** copy `activity.sqlite`
with `cp` — in WAL mode the recent writes live in a separate file and a plain
copy can capture a torn state.

## Taking a backup

```bash
export ACTIVITY_DATA_DIR=~/.local/share/ephemeris
uv run python -m scripts.backup_db              # write one set
uv run python -m scripts.backup_db --keep 20    # ...and keep only the 20 newest
uv run python -m scripts.backup_db --list       # what is on disk
```

The service can stay running. `--keep N` prunes whole sets, oldest first, and
identifies them by manifest — not by globbing `*.sqlite` — so a half-written run
is never mistaken for a backup worth keeping.

Two runs at once are safe: each reserves its name before it writes anything, and
holds that reservation until the whole set is on disk. A retention pass in one
process therefore leaves another's unfinished work alone — a manual run started
while the timer's is going does not disturb it, and neither loses its name to
the other. Both appear as complete sets when they finish.

The next backup also clears up after a run that was killed outright — power
loss, `SIGKILL`, a full disk — which leaves a half-written copy under a hidden
`.staged-*` name that no other listing shows. Cleanup happens before allocating
the new set, so debris that filled the backup filesystem cannot trap the timer
in a repeat-failure loop. Nothing else on the machine writes those names, so
they are removed rather than reported, and a run still using its own are left
alone.

**Retention deletes only what a manifest claims.** Files without one are listed
and left in place, because this directory may already hold `activity-*.sqlite`
snapshots taken by the earlier version of this script, which wrote no manifests
at all. Those are still restorable by hand — copy one to
`$ACTIVITY_DATA_DIR/activity.sqlite` with the service stopped — and nothing here
replaces them, so deleting them is your call, not the script's. Once you have
looked, `rm` them; new runs will not accumulate more.

### On a schedule

Two committed templates, copied and adjusted like the app's own unit — the
repository installs nothing:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/ephemeris-backup.service.example ~/.config/systemd/user/ephemeris-backup.service
cp deploy/ephemeris-backup.timer.example   ~/.config/systemd/user/ephemeris-backup.timer
# Edit ACTIVITY_DATA_DIR and WorkingDirectory in the .service copy to match yours.
systemctl --user daemon-reload
systemctl --user enable --now ephemeris-backup.timer
loginctl enable-linger "$USER"     # or nothing runs while you are logged out
```

Daily, `--keep 20`, `Persistent=true` so a run missed while the machine was
asleep happens at the next boot rather than being skipped.

**If your `ephemeris.service` copy sets `ACTIVITY_DB`, copy that line into the
backup unit too.** The timer runs a separate process that resolves the database
path itself; an unmirrored override sends it to `<data>/activity.sqlite`, which
either does not exist — the run fails — or is an older file, and then the
schedule backs up the wrong ledger every night without complaining. Each run
prints the path it actually read, so the journal settles it.

```bash
systemctl --user list-timers ephemeris-backup.timer   # when it next fires
journalctl --user -u ephemeris-backup -n 30           # what the last run did
```

## Checking that the backups are alive

A backup nobody has ever read is a hypothesis. `--verify` is the cheap test:
it re-hashes both files against the manifest, re-runs `integrity_check` on the
snapshot, checks the snapshot's real `PRAGMA user_version` against the schema
version the manifest claims, and opens the archive to confirm it holds exactly
the files the manifest lists. It writes nothing.

```bash
uv run python -m scripts.backup_db --verify \
  ~/.local/share/ephemeris/backups/activity-2026-08-02-031500.manifest.json
```

Verify the newest set after any interesting event (a migration, a disk scare, a
move to new hardware), and once in a while verify an *old* one — silent
corruption is found by reading, not by writing. The expensive test is a real
restore into a scratch directory, below; do that once a quarter or so.

## Restoring

**Stop the service first.** A restore overwrites the database file the running
process holds open, and SQLite has no defence against that. The script cannot
check this for you: nothing in a data directory says whether a process is using
it.

```bash
systemctl --user stop ephemeris
uv run python -m scripts.backup_db \
  --restore ~/.local/share/ephemeris/backups/activity-2026-08-02-031500.manifest.json \
  --into ~/.local/share/ephemeris
systemctl --user start ephemeris
```

`--restore` verifies the whole set before it writes anything, so a damaged
backup fails with the target untouched. It then builds both halves in a staging
directory inside the target and swaps them in by rename once they are complete,
so a failure partway — a full disk is the ordinary one — leaves the existing
instance where it was rather than half-replaced. The staged files and directory
entries are fsynced before that swap, and the target-directory renames are
fsynced before the command reports success.

A target directory created by the restore is mode `0700`; archive members can
therefore keep relying on the private instance root instead of becoming visible
under a typical `022` umask. An existing target keeps its operator-chosen mode.

By default it **refuses** a directory that already holds anything of its own.
Pass `--force` to restore anyway; what is there is moved aside as
`*.pre-restore-<stamp>` and kept, never deleted. Remove those by hand once you
are satisfied — later backups will not carry them, and later restores will not
touch them.

`--force` displaces **everything the backup side would have archived**, not only
the names this particular set carries — the two lists are the same list. A
`lessons/` tree created after the backup was taken would otherwise stay put
beside a database that has no rows for it, and the result is a hybrid wearing
the word "restored". The WAL sidecars go the same way, which is the point: left
in place they would be replayed into the restored database. What stays is what
the archive never held — `backups/` (often where the set you are restoring
lives) and `exports/` — plus anything already moved aside by an earlier
restore.

To rehearse a restore without risking the live instance, restore into a scratch
directory and start the app against it:

```bash
uv run python -m scripts.backup_db --restore .../activity-....manifest.json --into /tmp/rehearsal
ACTIVITY_DATA_DIR=/tmp/rehearsal uv run uvicorn app.main:app --port 8001 --no-proxy-headers
```

### A restored instance is not a new one

Startup seeds demo habits, lists and tasks exactly once per installation, and it
decides that from the `app_meta.seeded_at` marker (schema v16) — not from row
counts. This matters because a legitimate ledger can have empty tables: an owner
who deleted every task, or a JSONL restore, which deliberately leaves
insufficiently journaled tables empty. Before the marker, the first start after
such a restore poured demo rows into real history and appended their events to
the audit stream.

The marker lives in the database, so a full backup carries it automatically and
a restored instance starts up unchanged. Only a genuinely empty data directory
is ever seeded.

## What the owner still does by hand

**Off-machine copies.** Everything above protects against a corrupt file, a bad
migration, or a mistaken deletion. None of it protects against losing the
machine. A backup that only ever exists on the disk being backed up is one
accident away from nothing, so copy `backups/` somewhere else — another disk,
another host, whatever encrypted remote you already trust — on whatever rhythm
matches how much work you are willing to lose.

The files are self-describing and already `0600`; a manifest plus its two
companions is all a restore needs, and `--verify` works on a copy anywhere,
including on the far side of the transfer. This repository deliberately ships no
code for it: where the second copy lives is infrastructure, and hardcoding a
destination here would put a private location in a public repository.

Backups contain the whole private ledger — notes, tasks, lesson work. They are
covered by the `data/` gitignore rule and must never reach the public Git layer.
