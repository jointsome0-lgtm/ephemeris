# Bundle migration tool — adversarial security review

**Scope:** queue-listed commit `ec3c112` — new
`scripts/migrate_bundles.py` plus its C4 checks in `verify.py`. The complete
tool, the §10 mapping contract, the v1/v2 reader and canonical/atomic writer it
calls, the lesson/DB path and identity owners, the live preview/file consumers,
and the earlier C3 report were read. Same-branch follow-ups `a7acb6` and
`40a7888` appeared during this review; both were diffed from their parents and
probed separately where they intersect the findings below.

**Context:** the tool is an explicit local CLI and registers no listener. v0
still has no authentication, and the supported live posture remains direct
loopback. Runtime manifests and rollback material are private instance data;
rewritten manifests are subsequently consumed by the live Learn preview and
file routes.

**Method:** diffed `ec3c112` from its parent, traced every filesystem path from
SQLite/CLI/rollback data to its read or replacement, compared the generated v2
object field-by-field with `docs/learn-bundle-spec.md` §10, treated verifier
cases as claims, and ran invented temporary-tree probes against the exact
commit. The probes covered a manifest edit between plan and apply, a traversal
slug stored in a synthetic v11 DB, a symlinked rollback bundle, an absolute
rollback-copy path/link, a raw dropped-item collision, and the `a7acb6` /
`40a7888` fixes.

**Verdict:** `ec3c112` has one Medium and five Low findings. `a7acb6` resolves
the Medium stale-plan overwrite for the documented non-hostile concurrency
case. `40a7888` resolves the dropped-item collision (L5) and removes direct use
of the ledger's copy path, but L2 remains reachable through a symlink at the
derived copy name. Four Low findings therefore remain on committed head
`40a7888`. The rest of the §10 mapping conforms, and the earlier C3
reader/runtime fixes have not regressed. Do not treat the C4
migration/rollback operation as cleared for general use yet.

## Findings (severity-ranked)

### M1 — Apply can silently overwrite a manifest changed after planning (Medium, confirmed in `ec3c112`; resolved in `a7acb6`)

`run()` plans every lesson before it applies the first one
(`ec3c112:scripts/migrate_bundles.py:437-472`). In the queued commit,
`apply_plan()` writes the rollback copy from `plan.old_bytes` and immediately
replaces the live manifest without first confirming that the live bytes are
still those planned bytes (`ec3c112:scripts/migrate_bundles.py:332-357`). A
study agent or app writer can therefore publish an edit during a long run; the
migration silently discards that edit, and rollback cannot recover it because
the copy contains the still-older planned bytes.

An exact-commit probe planned an invented v1 manifest, added an unknown
`x_agent_edit` field, then applied the plan. Apply returned no error, the edit
was absent from the v2 output, and it was absent from the rollback copy.

`a7acb6` re-reads the manifest immediately before creating rollback state and
refuses if the bytes differ (`a7acb6:scripts/migrate_bundles.py:342-369`). An
independent exact-follow-up probe confirmed refusal, preservation of the edit,
and creation of no misleading rollback copy. This resolves the ordinary
plan/apply gap under the repository's existing same-user TOCTOU posture. The
tool should still be run with the service and lesson agents stopped; the check
is not a cross-process lock.

### L1 — Forward migration and rollback do not preserve the lessons-root containment boundary (Low, confirmed)

The runtime path owner rejects any slug outside the strict grammar and rejects
an existing bundle unless it is a real direct child of the resolved lessons
root (`app/services/lessons.py:102-105`, `app/services/lessons.py:671-687`).
The migration bypasses both controls: `_load_lessons()` accepts every DB slug,
`run()` joins it directly to `LESSONS_DIR`, and apply joins it again
(`ec3c112:scripts/migrate_bundles.py:413-440`,
`ec3c112:scripts/migrate_bundles.py:471-472`). `plan_bundle()` rejects a link at
the supplied bundle path but does not establish that the path is a direct child
of the lessons root (`ec3c112:scripts/migrate_bundles.py:265-285`).

An invented v11 DB row with slug `../../escape` made the tool report a
successful migration and replace an `escape/lesson.json` outside the private
lessons root. Normal app-created rows have safe slugs, so this requires a
corrupt, legacy-edited, or imported DB and is Low under the private-instance
posture; it nevertheless turns a DB data field that the runtime refuses into a
filesystem write path.

Rollback validates the slug grammar but never rejects a symlink at
`LESSONS_DIR/<slug>` before its no-follow final-file read and same-directory
atomic replacement (`ec3c112:scripts/migrate_bundles.py:380-409`). `O_NOFOLLOW`
protects only the final `lesson.json` component, not a linked parent directory.
An invented symlinked bundle caused rollback to replace
`lesson.json` in an outside target and report `[restored]`.

Validate every DB slug before any join, reuse the runtime's direct-child and
bundle-symlink check immediately before forward and rollback writes, and use a
directory-descriptor/no-follow write boundary if stronger concurrent-link
resistance is required.

### L2 — Rollback does not open its original-byte copy through a bounded no-follow boundary (Low, confirmed; partially fixed in `40a7888`)

After validating only `entry["slug"]`, rollback reads original bytes from
`rollback_dir / entry["file"]` (`ec3c112:scripts/migrate_bundles.py:383-407`).
An absolute `file` value discards `rollback_dir`; a relative value may traverse
out of it. The hash is only an integrity check against values in that same
ledger, so it does not establish that the bytes came from the migration-created
copy. A crafted or accidentally edited rollback directory can make the tool
read an arbitrary local path, block on a special file, or transplant arbitrary
UTF-8 bytes into the selected lesson when the current-manifest hash also
matches the ledger.

An exact-commit probe used an invented absolute source path; rollback accepted
it, replaced the lesson manifest, and reported success. `40a7888` correctly
derives `<slug>.lesson.json` instead of consuming the ledger path
(`40a7888:scripts/migrate_bundles.py:400-430`), closing the direct absolute /
`..` form. It still calls `Path.read_bytes()` on that derived name, however,
which follows a symlink and can block on a special file. An exact-`40a7888`
probe placed an invented symlink at the derived copy name; rollback followed
its outside source, restored those bytes, and reported success. Open the copy
nonblocking with `O_NOFOLLOW`, require a bounded regular descriptor, and
validate the complete ledger shape before processing entries.

### L3 — Page hashing reads each declared HTML file into memory without a byte bound (Low, confirmed)

`_hash_pages()` calls `Path.read_bytes()` and hands the complete allocation to
`sha256()` (`ec3c112:scripts/migrate_bundles.py:131-145`). The schema caps page
count and path length, but not page-file size. One very large declared page can
therefore exhaust the migration process or the host during planning; the same
unbounded allocation runs again after the manifest has been replaced. This is
a local/private-input availability issue, hence Low, but agent-authored and
imported lesson files are exactly the tool's input surface.

Hash through a fixed-size stream from one no-follow regular-file descriptor.
That also removes the current check/use gap between the per-segment symlink
test, `is_file()`, and `read_bytes()`.

### L4 — Rollback state is not durably ordered before the manifest mutation (Low, confirmed)

The code calls the rollback copy “durable” and promises that a crash leaves
every already migrated bundle restorable, but writes the original bytes with
plain `Path.write_bytes()` and no `fsync()`
(`ec3c112:scripts/migrate_bundles.py:342-355`). The shared writer fsyncs a new
file before `os.replace()` but does not fsync the containing directory
(`app/services/bundle_schema.py:877-899`). A host crash or power loss can
therefore persist the new manifest without a durable original-byte copy and
ledger directory entry. Ordinary Python exceptions fail before the manifest
replacement and are safe; the finding is the narrower crash-consistency gap.

Write the copy through a mode-0600 descriptor, fsync it, durably publish and
fsync the rollback directory/ledger, and only then replace the lesson manifest;
fsync the bundle directory after replacement if power-loss durability is part
of the C4 rollback guarantee.

### L5 — A colliding member on a v1 object dropped by normalization is silently discarded (Low, confirmed in `ec3c112`; resolved in `40a7888`)

The §10 stop condition is a fact about every raw object-form `entry` or
`related[]` declaration: an `id`/`title` member has no lossless v2 home even if
the v1 read model later drops that item. In `ec3c112`, collision checks occur
only after path cleaning and duplicate removal, so an invalid or duplicate
object is skipped before its colliding member is examined
(`ec3c112:scripts/migrate_bundles.py:148-195`). Migration then reports success
and permanently removes that raw declaration, contrary to the stop-before-write
and unknown-field preservation contract.

An exact-commit probe put an invented `id` on an object whose `../` path the v1
model drops; the plan remained `migrate` with no stop reason. `40a7888` moves
collision detection ahead of cleaning/deduplication for every raw object
(`40a7888:scripts/migrate_bundles.py:156-199`). The exact follow-up probe
returned `stop` and named the collision, resolving L5.

## Confirmed protections and regression checks

- **The current §10 mapping is conforming after L5's fix.** Structural paths
  come from the normalized v1 read model; unknown top-level and surviving
  object-form page members are preserved in canonical order; every raw
  `id`/`title` collision now stops before write; deterministic IDs include UID
  and normalized path; a valid absent DB selection is folded in without
  writing the DB; and null legacy copies are omitted. Generated text is re-read
  as clean v2 before a plan becomes applicable
  (`40a7888:scripts/migrate_bundles.py:156-337`).
- **Manifest final-component handling is safe.** The plan reader uses
  nonblocking `O_NOFOLLOW`, validates the opened descriptor as regular, and
  enforces the 256 KiB manifest limit. Publication uses the previously reviewed
  mode-0600 temporary-file, file-`fsync`, and atomic-replace writer, so a planted
  link/FIFO at the final `lesson.json` name is replaced rather than followed.
  L1 is specifically about an unvalidated/traversable or symlinked parent
  bundle directory.
- **C3 parser and live-route fixes remain intact.** Total JSON parsing,
  non-finite-number rejection, exact v2 selection, edge-whitespace rejection,
  identity mismatch degradation, declared-page/asset file allowlisting,
  artifact-root exclusions, finding aggregation, and the title-free event
  payload are unchanged. The migration pre-validation consumes that hardened
  reader; no earlier confirmed C3 finding regressed.
- **Network posture is unchanged.** The commit registers no HTTP/WebSocket
  surface and does not change Host/Origin policy. It does not make wider
  deployment safe: the application remains unauthenticated and supported only
  under the documented local posture.

## Verification

- `git diff --check ec3c112^ ec3c112 -- scripts/migrate_bundles.py verify.py`
  — passed.
- Exact-`ec3c112` invented temporary-tree probes — confirmed M1, both L1 paths,
  L2, and L5: the intervening edit was lost and absent from rollback; a
  traversal DB slug rewrote an outside-root manifest; rollback followed a
  symlinked bundle directory; an absolute ledger copy path was accepted; and a
  collision on a dropped raw object did not stop migration.
- Exact-`a7acb6` stale-plan probe — passed: apply refused, preserved the edit,
  and created no rollback copy.
- Exact-`40a7888` probes — the dropped raw-object collision stopped as required;
  the derived rollback-copy name still followed a symlink to an outside source,
  confirming L2's residual.
- `PYTHONPATH=. python verify.py` — inconclusive in this environment. It emitted
  no output for two minutes in the previously documented TestClient startup
  phase and was interrupted (exit 130); no failing assertion was observed, so
  this review does not independently claim the commit message's 495 checks.

## Deploy verdict

**C4 migration/rollback clearance: NO.** `a7acb6` closes M1 and `40a7888`
closes L5, but L1-L4 remain in the reviewed committed implementation. The
unchanged application runtime may continue under direct loopback without
invoking this tool; do not run the migration on the private instance or treat
its output as deploy-cleared until the remaining findings are fixed and
reviewed. **Wider deployment: NO** — v0 remains unauthenticated, and this
offline tool does not change that boundary.

## Addendum — exact-`3310e2b` L1–L4 re-probe

**Scope and method:** re-diffed `3310e2b` from `40a7888`, traced the complete
new containment, read, hashing, and durability paths, and reran the original
hostile cases against `3310e2b` in an invented temporary private-instance
tree. In addition to the original traversal and symlink cases, the re-probe
swapped a planned bundle to a symlink before apply, planted a FIFO at the
derived rollback-copy name, hashed a greater-than-5-MiB page while recording
every read size, supplied a malformed ledger entry, and recorded every
`fsync()` and `replace()` in a real migration run.

### Per-finding disposition

- **L1 — RESOLVED.** `_valid_slug()` now rejects a DB slug before `run()` joins
  it to `LESSONS_DIR`, and `_bundle_dir_safe()` is checked during planning,
  immediately before apply, and before rollback writes
  (`3310e2b:scripts/migrate_bundles.py:176-193,349-355,421-424,498-508,563-573`).
  The synthetic v11 `../../escape` row stopped with exit 1, left the
  outside-root manifest byte-identical, and created no rollback directory. A
  symlinked bundle stopped at plan time; replacing a planned bundle with a
  symlink made apply refuse before creating a copy; and rollback through a
  symlinked bundle refused without changing the outside target. This closes
  both previously confirmed L1 paths under the repository's documented
  same-user TOCTOU posture.

- **L2 — RESOLVED.** Rollback validates the ledger container and required
  per-entry string fields, validates the slug, ignores the ledger's `file`
  member as path authority, derives the copy name from the slug, and reads it
  through the nonblocking no-follow regular-file/size boundary
  (`3310e2b:scripts/migrate_bundles.py:479-520`). The old absolute `file`
  payload was ignored; with no derived copy rollback refused. A symlink and a
  FIFO at the derived name both refused without changing the live manifest
  (the FIFO case returned in under 1 ms), and a malformed entry was reported
  and refused. The earlier arbitrary-path, link-following, and special-file
  forms are no longer reachable.

- **L3 — RESOLVED.** `_hash_file_no_follow()` opens one nonblocking,
  `O_NOFOLLOW` descriptor, requires it to be regular, and hashes it in fixed
  64-KiB reads; `_hash_pages()` no longer calls `read_bytes()`
  (`3310e2b:scripts/migrate_bundles.py:135-173`). The greater-than-5-MiB probe
  produced the exact expected digest through 82 read calls whose largest
  request was 65,536 bytes. Symlink and FIFO page probes returned no digest,
  with the FIFO refusing immediately rather than blocking. The whole-file
  allocation in L3 is gone.

- **L4 — NOT RESOLVED.** The inner ordering is now correct: the rollback copy
  is mode 0600 and file-fsynced, its atomic publication is followed by a
  rollback-directory fsync, the rewritten ledger's file and rollback
  directory are fsynced, and only then is `lesson.json` replaced; the bundle
  directory is fsynced afterward
  (`3310e2b:scripts/migrate_bundles.py:196-220,438-456`). The exact-run event
  trace confirmed all of those calls before/after the manifest replacement.
  However, `run()` creates the new `data/migrations/v1v2-*` directory and never
  fsyncs its parent `data/migrations/` before replacing the live manifest
  (`3310e2b:scripts/migrate_bundles.py:581-597`). Fsyncing files and the new
  directory makes entries *inside* that directory durable; it does not make
  the new directory entry in its parent crash-durable. A power loss can
  therefore still leave the migrated manifest durable while the only path to
  its rollback directory is absent. The event trace specifically contained no
  fsync of `data/migrations/`. Fsync the parent after publishing the new
  rollback directory (and any newly created ancestor edge) before the first
  bundle mutation; add an ordering check that fails if manifest replacement is
  reached first.

### Addendum verification

- `git diff --check 3310e2b^ 3310e2b -- scripts/migrate_bundles.py verify.py`
  — passed.
- Exact-`3310e2b` invented temporary-tree probes — L1, L2, and L3 passed as
  described above. L4's copy/ledger/bundle ordering passed, while the trace
  confirmed the missing migration-parent fsync.
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. timeout 150s python verify.py` —
  inconclusive in this environment: it emitted no output before timeout (exit
  124), matching the previously documented TestClient startup hang. No failing
  assertion was observed, so this addendum does not independently claim the
  commit message's 502 checks.

### Updated deploy verdict

**Run the C4 migration on the private instance: NO, not yet.** L1–L3 are
cleared, and L4 is substantially improved, but the stated power-loss rollback
guarantee still has one missing parent-directory durability edge. The normal
direct-loopback application can continue without invoking the tool. Once the
new rollback directory is durably linked from `data/migrations/` before the
first manifest replacement and that exact order is re-probed, no finding from
this report would remain as a C4 migration blocker. **Wider deployment remains
NO** because v0 is unauthenticated; this offline migration does not alter that
boundary.

## Closing note — exact-`41c5134` L4 re-probe

**L4 — RESOLVED.** `run()` now fsyncs the new rollback directory,
`data/migrations/`, and `data/` after publishing the initial ledger and before
entering the apply loop (`41c5134:scripts/migrate_bundles.py:581-603`). In an
invented temporary private-instance run, the event trace showed the initial
ledger file fsync and replace, then directory fsyncs for `v1v2-*`,
`data/migrations/`, and `data/` at events 3–5; the first live `lesson.json`
replace was event 13. The new rollback-directory entry and its newly created
parent edge were therefore durably ordered before the first bundle mutation,
closing the sole remaining L4 gap.

**Final private-instance verdict: YES.** No finding in this report remains a
C4 migration blocker. Run the dry-run first and perform the migration with the
service and lesson agents stopped, under the documented private/local posture.
This does not change the wider-deployment verdict: v0 remains unauthenticated.

## Second closing note — exact-`fbd315b` / exact-`f487b30` re-probe

- **`fbd315b` — no new finding.** Exact-commit probes changed `uid` and
  `current_entry` separately after planning: both applies refused, preserved
  the live manifests, and created neither rollback copies nor ledger entries.
  A restore trace also confirmed the bundle-directory fsync after the atomic
  manifest replacement.
- **`f487b30` — two new Low findings.** The intended cases passed: missing or
  invalid v1 `slug`/`title` copies were filled from usable DB values, and no
  usable title on either side stopped the plan. However, DB fallback makes
  `title` plan input while the `fbd315b` pre-apply re-read still compares only
  `uid` and `current_entry`. An exact-head probe changed only the DB title after
  planning; the run returned 0 and wrote the planned old title, which read as
  `stale-metadata` against the fresh row. Separately, the new title predicate
  bounds `len(value.strip())` but emits `value` unchanged: an invented
  242-character title containing 240 non-whitespace characters was accepted
  and written instead of falling back or stopping, contrary to §4's 240-character
  emitted-field limit.

**Final private-instance verdict at `f487b30`: NO, not yet.** The earlier
findings remain closed and `fbd315b` does not regress them, but the YES verdict
at `41c5134` does not carry to head. Cover every DB value used by the plan in
the immediate pre-apply comparison (including a DB-sourced title), validate
the emitted title's actual length, and re-probe both cases. Wider deployment
remains NO because v0 is unauthenticated.
