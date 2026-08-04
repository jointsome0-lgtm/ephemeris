# Review: request limits, export retention, and storage status (#23)

**Date:** 2026-08-04

## Self-parameterization

- **Scope:** the one Pending entry present in `docs/reviews/QUEUE.md`:
  issue #23, commits `0b25126` through `113b1dd`, merged to `main` as
  `898bbed`. The review covers the listed paths and their direct callers:
  the central unsafe-request perimeter, service-layer field limits, JSONL
  export creation and retention, the `/export` storage read model, backup-path
  ownership, the daily-note error branch, and the related public documentation
  and regression suite.
- **Starting HEAD:** `5b7194b700fed8ecad72dadc35d5cf1be319f7b1` on clean `main`.
  The worktree had no tracked or untracked changes at the start.
- **Reviewed tree:** every listed commit (`0b25126`, `113b1dd`) and the landed
  merge (`898bbed`) is reachable from the starting HEAD. The scoped application
  tree is byte-identical from `898bbed` through the starting HEAD; the verdict
  is therefore about the landed code actually read, not an ephemeral preview
  merge or squash hash.
- **Report file:** `docs/reviews/2026-08-04-limits-retention-review.md`.
- **Prior reports reconciled:** the central write-guard report,
  `2026-07-20-attempt-backend-review.md`,
  `2026-07-23-attempt-body-stream-cap-review.md`, and the latest recovery
  posture statements in the assessment projection and lesson record-panel
  reports. The dispositions are recorded below.
- **Validation baseline:** host `uv run pytest` — **175 passed, 0 failed**;
  host `uv run python verify_restore.py` — **34 passed, 0 failed**. The
  restricted runner stalled at the known TestClient/subprocess boundary; the
  same mandated commands were rerun on the host. The repair PR must preserve
  or grow both counts.

## Context and method

The complete issue range and all 21 changed files were read, together with the
security middleware, request-body reader, changed service writers, export
router, backup writer/reader, database/settings path owners, storage template,
and direct route callers. The review treated the queue description and prior
review prose as evidence, not as instructions.

The threat model is the documented direct-loopback `127.0.0.1:8765`,
single-worker, unauthenticated single-user deployment. The public-data boundary
was preserved: all probes used invented throwaway data under temporary paths;
no service, live database, private instance, export, screenshot, credential,
or browser state was read or changed.

## Findings

### L1 — A malformed backup manifest can turn every `/export` GET into a 500 (Low, confirmed)

`app/services/storage.py:143-164` accepts any non-negative JSON integer in a
manifest's `files.*.bytes` field and passes the resulting total to
`app/services/export.py:301-312`, where `human_size()` converts the integer to
`float`. Python raises `OverflowError` for an invented integer such as
`10**400`.

This is reachable through the real route, not only a helper: an invented
version-1 manifest placed in a throwaway `BACKUPS_DIR` caused the current
`GET /export` TestClient probe to return **500 Internal Server Error**. The
storage reader explicitly claims to survive damaged or hand-written manifests
and fall back to an older readable set (`app/services/storage.py:76-120`), so a
single malformed numeric value can instead take down the status page and hide
the no-backup/space warnings. Under the documented single-user loopback
posture this is a local availability and operational-diagnostic failure, hence
Low; it is not an authentication or data-exfiltration finding.

**Fix direction:** make manifest numeric fields total before formatting: reject
non-finite or unreasonable claimed sizes as an unreadable candidate, or make
the size formatter integer-safe and separately bound the value used by the
status model. Add an invented malformed-manifest route regression proving that
the newest bad candidate is skipped and an older valid candidate, or the
no-backup warning, still renders. The repair belongs in a separate reviewed PR.

### L2 — An accepted offset-less manifest timestamp is dated in the host zone, not the ledger zone (Low, confirmed)

`app/services/storage.py:76-103` promises that a manifest timestamp is
normalized into the ledger's zone, but it calls
`datetime.fromisoformat(value).astimezone(app_tz())` directly. For an
offset-less datetime, Python first interprets the naive value in the host's
local zone and only then converts it. With an invented
`2040-01-01T00:30:00`, a host in Europe/Moscow, and a configured ledger zone
of America/New_York, the current code produces
`2039-12-31T16:30:00-05:00`; interpreting the accepted value in the ledger
zone produces `2040-01-01T00:30:00-05:00`.

The current backup writer emits offset-aware timestamps
(`scripts/backup_db.py:130-137`), so this is not a failure for freshly written
sets. It is nevertheless an active accepted-input path: `load_manifest()` does
not require an offset, and the storage reader deliberately keeps naive values
instead of skipping them. Around midnight, the displayed newest-backup date,
age, and ordering can therefore be one calendar day wrong when
`APP_TIMEZONE` differs from the host zone. The impact is an incorrect recovery
warning in the documented deployment, so Low.

**Fix direction:** choose and test one manifest contract. Either reject
offset-less timestamps as malformed and fall back to the next candidate, or
attach `app_tz()` to a naive value before converting it. Add a regression with
different host and configured ledger zones, including a near-midnight value,
and keep the existing aware-timestamp and DST cases. The repair belongs in a
separate reviewed PR.

## Confirmed protections

- The unsafe-method body ceiling is streaming and sticky. `Content-Length` is
  only the early refusal; `_CappedBody` is the delivered-byte authority, hands
  an over-limit route `http.disconnect`, and replaces the resulting response
  with 413. The route-specific Learn caps retain their typed refusals below the
  2 MiB perimeter ceiling.
- The five newly bounded note fields and the four moved title bounds are
  enforced in their domain services, and the daily-note route maps the new
  domain error to its existing Mode A/Mode B responses. Invented boundary and
  over-bound writes passed the issue suite.
- Export writes are staged, fsynced, atomically named, and pruned best-effort.
  The current export is protected, recent overlapping exports are protected by
  the grace window, and retention does not follow temporary files.
- The storage panel is read-only: it does not create directories, write the
  ledger, trigger a backup, or prune exports. The backup writer still uses the
  SQLite Online Backup API plus a full integrity check before publishing a
  manifest, and restore verification passes 34/34.
- The new settings-owned `BACKUPS_DIR` is shared by the app status reader and
  `scripts/backup_db.py`; no second path computation remains in the changed
  path.

## Prior-condition reconciliation

- **Central write-guard W1 — RESOLVED and not regressed.** The earlier report's
  scheme/authority comparison remains in the unchanged origin policy in
  `app/security.py:165-200`; issue #23 adds only the body counter after that
  guard. Duplicate origins, opaque origins, trusted hosts, and safe-method
  posture remain as previously dispositioned.
- **Attempt-backend A1 — RESOLVED and not regressed.** The prior body-buffering
  finding was closed by the parser-independent stream-cap drain: the current
  middleware also counts delivered chunks without buffering a second copy. The
  attempt route's own cap remains authoritative below the perimeter.
- **Attempt-backend A2 — STILL OPEN OUTSIDE THIS SCOPE.** The projection-cost
  follow-up concerns `app/services/attempts.py` and its reconciliation lock;
  neither that path nor its behavior is changed by issue #23. This report does
  not claim to resolve it.
- **Recovery/public-data condition — RESOLVED/UNCHANGED for this code.** The
  prior reports continue to require a consistent private SQLite backup for
  full-fidelity recovery. This change preserves that boundary, keeps JSONL
  labeled as an audit stream rather than a full backup, and retains the
  consistent snapshot/integrity-check path. The two status-reader findings
  above do not make the recovery contract safer than that; they remain open.
- **Deployment boundary — UNCHANGED.** No authentication was added, and no
  listener or service action was taken. The code remains unsuitable for public,
  proxy-adjacent, or multi-user exposure independently of these findings.

## Closing verification

- `git diff --check 0b25126^..113b1dd` — passed.
- All listed commits and merge `898bbed` are reachable from the starting HEAD;
  the scoped tree has no later application delta.
- Host `uv run pytest` — **175 passed, 0 failed**.
- Host `uv run python verify_restore.py` — **34 passed, 0 failed**.
- Invented malformed-manifest probe — confirmed L1: `storage.newest_backup()`
  raised `OverflowError`, and the real `/export` route returned 500.
- Invented configured-zone probe — confirmed L2: an accepted naive timestamp
  shifted from 2040-01-01 to 2039-12-31 when converted from the host zone to a
  different configured ledger zone.
- No application, test, specification, service, or live-data file was edited
  by this diagnosis. The only intended changes from this drain are this report
  and the Pending-entry pointer.

## Closing verdict

**NOT SAFE TO MAKE LIVE for the issue #23 limits/retention tree, including the
documented direct-loopback `127.0.0.1:8765`, single-worker, unauthenticated
single-user deployment.** This drain found **2 Low, 0 Critical, 0 High, 0
Medium, and 0 Info** findings. The request-body stream admission, service-layer
field caps, export atomicity/retention, and consistent SQLite backup path are
otherwise approved as stated above, but the `/export` status reader still has a
confirmed 500 path for a malformed manifest and a confirmed timezone error for
an accepted offset-less manifest. Wider, proxy-adjacent, or multi-user
deployment remains **NO**. The entry stays **Pending** until a separate,
ordinarily reviewed repair PR makes manifest parsing total and timezone-correct,
preserves or grows the **175 / 34** baselines, and receives a fresh drain. No
live service action was performed; restart remains the owner's action.
