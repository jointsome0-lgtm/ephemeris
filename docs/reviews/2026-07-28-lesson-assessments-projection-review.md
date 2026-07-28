# Lesson assessments projection — adversarial security review

**Scope:** two Pending entries were present at review start. The owner
explicitly selected them as one combined scope because the second is the
first's same-module follow-up and the reviewed state is their sum on `main`:

- `a05d5fc`, `f03ae5d`, `49ae440`, and `d18f755` on
  `fix/4-s2-assessments-projection`, landed by ordinary merge commit `876b879`
  (PR #88);
- `7161e08`, `5ca48f2`, and `95b78d9` on
  `fix/4-s2-projection-replay-budget`, landed by ordinary merge commit
  `5ebdc78` (PR #89).

The combined listed surface is `app/services/assessments.py`,
`app/services/lessons.py`, `app/services/bundle_schema.py`, `app/db.py`,
`docs/learn-bundle-spec.md`, `docs/lesson-assessments-api.md`, `verify.py`,
`docs/reviews/QUEUE.md`, and this report, plus the assessment authority/fold,
lesson workspace and terminal-create caller, manifest reader, bundle
file-serving, sandbox mount, SQLite migration, and projection-lock direct
callers. Both queue entries move together only on a clean closing verdict.

**Starting HEAD:** `5260d3d075673a97ec12c5fd06bddbd8ff7a3a3f` on
`main`, matching `origin/main`. `git status --porcelain=v1 --branch` showed
only `## main...origin/main`; the starting worktree was clean.

**Fix target:** merge commits `876b879` and `5ebdc78` are both ancestors of
`main`, as are every listed branch commit. `876b879^{tree}` equals
`d18f755^{tree}` at `43a9fd9d5cff58c3ec96b1cab6d35535abaf02d8`;
`5ebdc78^{tree}` equals `95b78d9^{tree}` at
`20649b3321783a868ccb28e60bcd4d010eba6ac1`. Both entries are merged, so repo
tradition and the drain brief put every fix directly on `main`.

**Report file:**
`docs/reviews/2026-07-28-lesson-assessments-projection-review.md`, derived
from the entry's lesson-assessments-projection subject.

**Prior reports to reconcile:** every existing
`docs/reviews/*-review.md` closing verdict was scanned. The binding conditions
for this surface are:

- `2026-07-28-lesson-assessment-authority-review.md`: central write-guard W1,
  assessment request admission, same-lesson references, fingerprint
  idempotency, archive ordering, row/event atomicity, the active-state fold,
  and UTF-8-safe refusals are resolved. Origin-less non-browser access remains
  accepted only inside the direct-loopback posture; assessment recovery still
  requires a consistent private SQLite backup.
- `2026-07-23-attempts-projection-cost-review.md`: attempt A1/A2 and the
  attempts projection's bounded nominal append, streaming repair, cursor/seal
  checks, and private cross-process exclusion are resolved. This entry must not
  regress or copy that machinery into the separate assessments projection.
- `2026-07-17-bundle-schema-runtime-review.md` and later bundle/runner reports:
  bounded/total manifest parsing, v2 positive file allowlisting, reserved-name
  exclusion, identity/profile fail-closed behavior, and sanitized findings are
  resolved. The same-user bundle-publication window remains accepted by
  design.
- Generated-brief, lesson-role, terminal, workspace, and sandbox reports:
  metadata remains data, brief publication remains atomic, learner isolation
  and server-owned role selection remain resolved, and terminal-opt-in T1
  remains open/accepted only for the deliberately plain owner shell. The
  trusted lesson-agent's intentional host-network and credential posture is
  unchanged.
- `2026-07-21-check-activation-review` and later Phase-F reports:
  bridge-runtime D5 L1 remains mitigated; D5 L2/L3 and artifact, runner,
  run-API, and generated-pedagogy protections remain resolved.
- `2026-07-27-platform-support-review.md`: portable import/startup, missing
  `fcntl` projection-pending fallback, and public-data-boundary dispositions
  remain binding. Wider, proxy-adjacent, or multi-user deployment remains
  unsupported.

A closing verdict must state whether each condition remains resolved,
mitigated, open/accepted, or unchanged for the closing tree.

**Validation baseline:** approved host runs at the clean combined starting
HEAD passed: `python verify.py` — **824 passed, 0 failed**;
`python verify_restore.py` — **28 passed, 0 failed**. Restricted-sandbox runs
of both commands emitted no assertion result and were interrupted after the
repository's known TestClient/startup stall; only the complete host counts are
baselines.

## Context and method

The deployment decision assumes the documented unauthenticated,
direct-loopback `127.0.0.1:8765`, single-user, single-worker app. No service
was restarted or signalled, and no live database, lesson bundle, export,
browser profile, screenshot, or authenticated state was read or written.
Executable probes used an invented lesson identity and an in-memory SQLite
database.

The complete combined landed diff, assessment service, projection
implementation and process-local publication cache, workspace reconcile hook,
terminal-create caller, reserved-name grammar, bundle serving path, API and
bundle contracts, schema-v15 migration, verifier section, queue descriptions,
and direct sandbox and SQLite callers were read. Static tracing covered
authority commit boundaries, active-fold cardinality, rate and idempotency
behavior, read-snapshot consistency, watermark query plans, flock lifecycle,
private lock naming, cached file-identity claims, manifest identity gating,
bundle-root descriptor use, temporary-file publication, collision handling,
link/name substitution, fsync ordering, every reconcile trigger, response
honesty, and reader exposure. Tests, commit messages, and earlier review
summaries were treated as claims to verify, not as authority.

## PR #88 starting-tree finding (historical, preserved)

### L1 — Unbounded active keys restore lifetime-linear full rewrites and an unmetered replay amplifier (Low, confirmed; owner decision required)

The repository contract says `assessments.jsonl` is small by construction and
that its size is bounded by current state rather than lifetime writes
(`docs/learn-bundle-spec.md`, section 6.5). The implemented keyspace does not
provide that bound:

- each evidence write may introduce 1–8 new opaque concept refs, and the
  endpoint deliberately does not require those refs to be among the manifest's
  at-most-64 declared concepts (`app/services/assessments.py:_clean_concepts`;
  `docs/lesson-assessments-api.md`, request table);
- each new concept retains that evidence row in `evidence_by_concept`, and each
  newly reviewed attempt retains one review in `reviews_by_attempt`
  (`app/services/assessments.py:active_state`);
- attempts and distinct ad-hoc concept refs have no lifetime cardinality or
  retention bound.

A caller can therefore make every accepted assessment remain in the active
fold simply by using a fresh valid concept ref. `_fold_records()` materializes
the complete fold, `_render()` materializes the complete JSONL byte string,
and `_publish()` writes and fsyncs it synchronously. That work runs after every
successful write, on a lesson-agent terminal open, and on a matching
idempotent replay. The replay path remains outside the assessment rate budget:
the nearby claim that replay work is “one indexed SELECT” became false when
`_replay_or_conflict()` began calling `_row_response()`, which performs the
full reconcile. A local caller can create one known assessment and then issue
unlimited matching duplicates, forcing repeated full rewrites without
consuming the 30-per-60-second window.

A focused in-memory probe inserted valid-shaped active evidence with distinct
invented concepts and maximum-size 8-KiB notes. Current code produced:

| active rows | rendered bytes | fold CPU | render CPU |
|---:|---:|---:|---:|
| 300 | 2,591,045 | 0.0111 s | 0.0093 s |
| 1,000 | 8,636,948 | 0.0302 s | 0.0319 s |
| 5,000 | 43,192,948 | 0.1779 s | 0.1680 s |

Those timings exclude temporary-file writes, file and directory `fsync`, JSON
response latency, and contention. Five thousand active rows are reachable in
about 2 hours 47 minutes at the documented rate cap—the same arithmetic that
made a full-history rewrite unacceptable—because unique active keys prevent
the promised compaction. From there every accepted write or duplicate replay
can synchronously rewrite about 43 MiB; continued growth is unbounded.

This is Low, not Medium, for the supported direct-loopback single-user
deployment: the caller is a same-machine process, accepted writes remain
durable, projection failure degrades honestly to `pending`, and there is no
authority bypass, confidentiality breach, or data loss. It is nevertheless a
concrete persistent service/terminal-availability defect in the deployed
posture, not hypothetical wider-deployment hardening. On a wider
unauthenticated listener it would become a direct remote resource-exhaustion
primitive, independently reinforcing the existing wider-deployment
prohibition.

Resolving L1 requires a product/spec choice. A fixed projection byte/active-key
ceiling needs defined overflow semantics (which current judgments may be
omitted, or whether the projection becomes permanently pending). Restricting
evidence to declared concepts still leaves reviews-per-attempt unbounded and
changes the accepted ad-hoc-concept contract. Changing the fold to retain only
recent attempts/concepts changes what “active state” means. A different
incremental or sharded medium changes the frozen single-file/full-rewrite
contract. The reviewer cannot choose among those behaviors without owner
authority.

No Critical, High, Medium, or Info finding was found.

## Confirmed protections and rebutted candidates at the starting tree

- The assessment row and paired event still commit before any projection
  work. `reconcile_projection()` refuses an active caller transaction, and a
  projection exception cannot turn a durable write into a 500.
- Fold rows and `as_of_seq` come from one read snapshot. A sibling commit
  cannot be advertised by the watermark while omitted from the rendered
  state.
- The app-private lesson-UID lock is separate from the attempts lock and
  outside the agent-writable bundle. Busy or unavailable locking returns
  projection-pending without holding SQLite's writer lock.
- A non-rejected manifest with a contradictory lesson UID blocks publication;
  missing, legacy, and rejected manifests retain the explicit publish
  behavior. Projection reads do not create bundle state.
- Publication opens one no-follow directory descriptor, stages a mode-0600
  exclusive file, fsyncs it, atomically replaces the reserved name, and fsyncs
  the directory. Directories, symlinks, and multi-link collisions are not
  opened or adopted.
- Same-UID mutation or name substitution inside the writable bundle is
  reproducible but is not a new finding. The file is explicitly derived and
  non-authoritative; the same actor can rewrite it immediately after any
  validation. SQLite plus the paired event remain the authority, and the
  existing attempts-projection report already records this trust-boundary
  calibration.
- `assessments.jsonl` and anything nested under that name are rejected by the
  v2 path grammar. Both the v2 positive allowlist and the v1 reserved-name
  denylist keep it off the generic `/files/` preview surface.
- Neither lesson terminal role receives a filesystem-enforced read-only bundle
  mount, but that is unchanged established same-UID posture rather than a new
  authority claim by this entry. The learner iframe and HTTP bridge receive no
  assessment-write capability.

## PR #88 initial verification and stop verdict (historical)

- Reachability/tree proof — all listed commits and merge commit `876b879` are
  ancestors of starting `main`; `876b879^{tree}` equals `d18f755^{tree}`.
- `git diff --check a05d5fc^..d18f755` — passed.
- Starting-head approved host `python verify.py` — **819 passed, 0 failed**.
- Starting-head approved host `python verify_restore.py` — **28 passed,
  0 failed**.
- Focused active-key growth probe — confirmed L1 and the figures above without
  touching a runtime bundle or private instance.

**NOT YET SAFE TO MAKE LIVE for the new assessments projection.** L1 leaves a
documented bounded-current-state design with no actual bound and restores the
lifetime-linear synchronous rewrite class that the projection design claims
to avoid. The queue entry remains Pending. Per the drain brief's owner-level
product/specification stop rule, no fix was selected, no code or queue
bookkeeping was changed, no commit was created or pushed, and **0 of 10**
cycles have been consumed. Wider, proxy-adjacent, or multi-user deployment
remains **NO** independently. A live restart remains owner-only and was not
performed.

## Combined-scope review at `5260d3d`

PR #89 makes three valid corrections to the PR #88 starting tree:

- the contract now says the active fold is a compaction rather than a fixed
  cap. New concepts and attempts may grow it without a ceiling; truncating
  current tutor state is explicitly rejected as a worse lie than a large
  resume artifact;
- an intact projection that this process published at the current insert-only
  `MAX(id)` watermark skips fold materialization, rendering, replacement, and
  fsync on an idempotent replay;
- schema v15 adds `(lesson_id, id)`, and the starting-head query plan uses it
  for the unmetered watermark lookup. A foreign manifest is refused before
  the fold on the skip path.

Those changes close the false fixed-bound claim and the duplicate-request
amplifier described in the original L1. They do not by themselves establish a
clean combined verdict: the fresh review below checked every trigger and the
cache's claimed file identity independently.

## Combined findings (severity-ranked)

### L1 — Lesson-agent terminal creation still forces an unmetered full rewrite (Low, confirmed)

`prepare_terminal_workspace()` is called for every newly created
`lesson-agent` terminal session. Its assessment hook passes `force=True`, so
`_rewrite_locked()` ignores the PR #89 publication stamp even when the
watermark and file are unchanged. It then materializes the whole active fold,
renders the whole JSONL byte string, replaces the file, and fsyncs it. Closing
and creating sessions can repeat without consuming the assessment endpoint's
30-per-60-second budget.

This preserves one unmetered form of the original L1. The follow-up's stated
reason — terminal creation fires because the file *may* be gone — does not
require bypassing the cache: `_projection_unchanged()` already returns false
for a missing, replaced, resized, linked, or non-regular file, which falls
through to a repair. A focused throwaway-data probe opened the same unchanged
lesson workspace twice and observed two `_publish()` calls, each rendering
624 bytes. At the original report's larger active folds, the same trigger
redrives the lifetime-linear synchronous work that PR #89 correctly removed
from replays.

This is Low for the supported direct-loopback single-user deployment: only a
same-machine client admitted to the terminal surface can drive it, terminal
session capacity remains bounded, the work runs off the event loop, and the
projection is derived. It is still a concrete avoidable availability cost in
the deployed posture. Wider unauthenticated exposure would raise its impact,
but that posture remains prohibited independently.

### L2 — Inode, size, and mtime do not prove the cached projection is unchanged (Low, confirmed)

PR #89 describes `_published` as the identity of the exact file the process
left behind, but its stamp omits device and change time. A writer with the
documented bundle access can edit the regular file in place without changing
its inode, preserve its byte length, and restore the old mtime with
`utimensat`/`os.utime`. The kernel change time moves, but
`_projection_unchanged()` does not compare it. An unchanged watermark then
skips the fold and publication and `_row_response()` reports
`projection: projected` although the bytes no longer reflect SQLite.

A focused invented-data probe preserved inode, size, and mtime while changing
one same-width byte sequence. Change time moved as expected. A matching
idempotent replay returned `duplicate` and `projected`, performed zero
publishes, and left the altered bytes in place. This is not an authority or
confidentiality breach — SQLite plus its paired event remain authoritative,
and the same-UID lesson agent can alter the derived file again immediately
after any check — but it is a concrete false success response and defeats the
promised replay-heal boundary.

The attempts projection's already-reviewed seal includes device, inode, size,
mtime, and ctime. Reusing that minimal metadata shape here closes the false
identity claim without reading bundle bytes or copying the attempts
projection's cursor/seal machinery. With ctime in the stamp, the terminal
hook can use the ordinary skip safely: deletion and every detected mutation
still repair, while an intact file does no fold or filesystem work.

No Critical, High, Medium, or Info finding was found in the combined scope.

## Combined starting-head verification and interim verdict

- Reachability/tree proof — both merge commits and every listed branch commit
  are ancestors of starting `main`; each merge tree equals its reviewed branch
  head tree.
- `git diff --check 876b879^1..5ebdc78` — passed.
- Starting-head approved host `python verify.py` — **824 passed, 0 failed**.
- Starting-head approved host `python verify_restore.py` — **28 passed,
  0 failed**.
- Focused throwaway-data cache/terminal probe — confirmed L1 and L2 without
  touching a runtime bundle or private instance.

**NOT YET SAFE TO MAKE LIVE for the combined assessments projection.** PR #89
resolves the replay-driven part of the historical L1 and makes the no-fixed-cap
product contract explicit, but terminal creation still bypasses that same
cost guard and the cache can falsely accept a changed file. Both are
implementation defects with a coherent in-scope repair; no product or
specification decision is required. Both queue entries remain Pending. The
initial combined report and self-parameterization consume **0 of 10** cycles.
Wider, proxy-adjacent, or multi-user deployment remains **NO** independently.
A live restart remains owner-only and was not performed.

## Cycle 1 resolution — `39fc478`

One ordinary fix commit resolves both combined findings:

- **L1 — RESOLVED.** The lesson-agent workspace hook now uses the ordinary
  reconcile path. A missing, replaced, resized, hard-linked, in-place-mutated,
  or otherwise seal-mismatched projection still falls through to the complete
  fold and atomic repair; an intact projection at the current watermark does
  not materialize the fold or publish merely because another terminal was
  created. Regression coverage opens the same unchanged lesson workspace
  twice and requires zero `active_state()` and `_publish()` calls, then deletes
  the file and confirms terminal creation still heals it.
- **L2 — RESOLVED.** The process-local stamp is now
  `(watermark, device, inode, size, mtime, ctime)`. The same-inode,
  same-size, restored-mtime probe now fails the ctime seal, runs one repair,
  returns `projection: projected`, and replaces the altered bytes. The check
  remains metadata-only and the cache remains process-local; no projection
  bytes become authority.

Cycle 1 validation preserved and grew the baseline:

- focused throwaway-data probe — repeated terminal opens changed from two
  publishes to zero; the restored-mtime mutation changed from zero publishes
  and stale bytes to one publish and healed bytes;
- `git diff --check` and `git show --check 39fc478` — passed;
- approved host `python verify.py` — **826 passed, 0 failed**;
- approved host `python verify_restore.py` — **28 passed, 0 failed**.

No new finding was introduced by the fix. No production caller passes
`force=True`; its two remaining uses are focused verifier probes that
deliberately demand a fresh render.

## Prior-condition reconciliation at the closing application tree

- **Historical projection L1 — RESOLVED under the explicit contract.** PR #89
  corrected “bounded” to the deliberate compaction-without-a-fixed-cap
  contract, made matching replays one indexed watermark check, indexed that
  check, and refused identity contradiction before folding. Cycle 1 removes
  the remaining unmetered terminal-open rewrite. Accepted new assessments
  still rewrite the active fold under the documented 30-per-60-second
  single-worker damper; that explicit product behavior is unchanged rather
  than silently truncated or weakened.
- **Combined L2 and projection response honesty — RESOLVED.** The cached
  metadata claim now uses the same device/inode/size/mtime/ctime shape already
  approved for the attempts projection. Missing, changed, foreign, busy, or
  otherwise unpublishable state still answers `pending`; a durable assessment
  is never rolled back or turned into a 500 by projection work.
- **Central write-guard W1 and assessment authority — REMAIN RESOLVED.** Both
  aliases remain behind the same Host/Origin middleware; strict body
  admission, UTF-8-safe refusals, same-lesson references, fingerprint
  idempotency, archive ordering, row/event atomicity, and the active-state
  fold are unchanged. Origin-less non-browser access and the direct/no-
  forwarded-header condition remain **OPEN/ACCEPTED only for the documented
  direct-loopback deployment**. Typed recovery still requires a consistent
  private SQLite backup.
- **Attempt A1/A2 and attempt authority — REMAIN RESOLVED.** The attempts
  endpoint, projection, cursor, private seal/state, lock, streaming repair,
  and history-independent append are untouched. This change reuses only the
  already-reviewed metadata tuple; it does not copy or weaken that machinery.
- **Bundle and publication protections — REMAIN RESOLVED.** Bounded/total
  manifest parsing, v2 positive file allowlisting, reserved-name exclusion,
  identity/profile fail-closed behavior, sanitized findings, one-descriptor
  no-follow publication, exclusive staging, link-count checks, collision
  handling, and file/directory fsync remain intact. The same-user
  post-validation publication window remains **OPEN/ACCEPTED by design**:
  `assessments.jsonl` is derived and non-authoritative.
- **Generated brief, lesson-role, terminal, workspace, and sandbox conditions
  — RETAIN THEIR PRIOR DISPOSITIONS.** Metadata remains data, brief publication
  remains atomic, learner isolation and server-owned role selection remain
  resolved, and a projection failure still cannot refuse the agent workspace.
  Terminal-opt-in T1 remains **OPEN/ACCEPTED only for the deliberately plain
  owner shell**. The trusted lesson-agent's intentional host-network,
  credential, and writable-bundle posture is unchanged. No PTY, WebSocket,
  fd-lifecycle, sandbox profile, mount, or listener code changed.
- **Bridge, artifact, runner, and generated-pedagogy conditions — RETAIN THEIR
  PRIOR DISPOSITIONS.** Bridge-runtime D5 L1 remains **MITIGATED** and D5
  L2/L3 remain **RESOLVED**. Artifact, runner, run-API, frontend relay, and
  generated-pedagogy protections remain resolved; the assessment projection
  adds no iframe or bridge capability.
- **Platform, public-data, and recovery conditions — REMAIN
  RESOLVED/UNCHANGED.** Missing `fcntl` still degrades the projection to
  `pending` without breaking app import/startup. No real lesson, assessment,
  database row, export, screenshot, browser state, credential, or private
  instance path entered the repository; every probe used invented throwaway
  data. Wider, proxy-adjacent, or multi-user deployment remains unsupported.

## Closing verification and verdict

The starting baseline was **824 passed, 0 failed** and **28 passed, 0 failed**.
Cycle 1 grew it to **826 passed, 0 failed** and preserved **28 passed,
0 failed**. The final documentation-only head carries the same application
tree and is rechecked before push together with public hygiene and ignored-file
inspection.

**SAFE TO MAKE LIVE for the documented direct-loopback `127.0.0.1:8765`,
single-worker, unauthenticated single-user deployment.** The combined review
found **2 Low, 0 Critical, 0 High, 0 Medium, and 0 Info** findings; L1 and L2
are resolved in **1 of 10** cycles, and no open review finding remains for
either queue entry. The explicit unbounded-active-fold contract remains, but
unchanged work is no longer redriven by matching replays or terminal opens,
and a cached success requires the full metadata seal. Both entries may move
to Done referencing this report and fix commit `39fc478`. Wider,
proxy-adjacent, or multi-user deployment remains **NO**. A live restart
remains owner-only and was not performed.
