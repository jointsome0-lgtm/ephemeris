# Attempts projection cost review

**Date:** 2026-07-23

## Self-parameterization

- **Scope:** queue entry #58, landed via merge commit `f004546`, whose tree is
  byte-identical to reviewed branch head `4256a2c`. The review covered the
  exact four-commit `13cab1b..4256a2c` range in
  `app/services/attempts.py`, `app/db.py`, `verify.py`,
  `docs/lesson-attempts-api.md`, and queue bookkeeping, plus the attempt HTTP
  handler, lesson/bundle readers, SQLite connection/migration path, generated
  reader guidance, and sandbox mount posture as direct context.
- **Starting HEAD:** `29197efe9244613015c92136fc69d75ca1ce380b`
  on clean `main`, equal to `origin/main` after entry #59's closing docs commit
  and descending from the caller's required `62f8afa` start.
- **Report file:**
  `docs/reviews/2026-07-23-attempts-projection-cost-review.md`.
- **Prior reports reconciled:** the 2026-07-20 attempt-backend report, the D5
  check-activation report, and the latest F4/F5 reports touching attempts.
  This entry is the attempt-backend report's accepted Low follow-up A2. Entry
  #59's immediately preceding drain resolved A1. D5 L1 remains mitigated and
  D5 L2/L3 remain resolved.
- **Validation baseline:** host `python verify.py` — **770 passed, 0 failed**;
  host `python verify_restore.py` — **28 passed, 0 failed**. The intervening
  #59 closeout changed documentation only.

## Context and method

The complete projection service and schema migration were read at the landed
tree. The four commits were also reviewed separately so later hardening was
not allowed to obscure the initial design. Tests and PR-round descriptions
were treated as claims rather than evidence.

Static tracing covered authority commit boundaries, same-process and
cross-process ordering, flock lifecycle, private path derivation, malformed
and recursively nested state, fixed read/render bounds, SQLite query plans
implied by the new indexes, cursor and sort-tail authority anchors, database
restore/reuse cases, append ordering, short/failed writes, delayed close
errors, inode/link/name substitution, same-inode mutation, rebuild snapshots,
directory collisions, fsync/replace crash windows, rate/idempotency behavior,
and WAL writer concurrency. The committed invented-data growth/race harness
was read as executable evidence and the full verifier was run independently.

One additional invented throwaway-data probe placed a same-inode prefix write
inside the append helper's write-to-immediate-fstat window. It confirmed the
boundary calibration recorded below. No live service, private instance,
runtime bundle, browser state, or real learner data was read or changed.

## Findings

No Critical, High, Medium, Low, Info, or other finding was found.

The nominal append path now performs fixed work independent of historical
answer volume: one at-most-4-KiB state read, two indexed anchor lookups, an
at-most-two-row cursor lookup, one at-most-64-KiB render/write/readback, and
fixed metadata/state publication. A repair still necessarily walks authority,
but it streams one row at a time into a temporary file instead of retaining
the history in memory. Neither path opens a SQLite writer transaction.

## Rebutted candidate — same-UID projection mutation is not a new authority

The throwaway probe rewrote the first projection byte from another descriptor
inside `_write_all` after the append write but before its returned `fstat`.
The immediate seal therefore described the already-mutated inode; a later
fast append accepted that seal. This is reproducible, but it is not counted as
a finding under the repository's explicit ownership and trust boundary:

- `learn-bundle-spec.md` designates `attempts.jsonl` app-owned and read-only
  for the agent and learner. The generated brief repeats that it must never be
  written or rewritten. Reaching the probe requires a same-UID bundle writer
  to violate that boundary concurrently; no unauthenticated HTTP client,
  lesson iframe, or bridge capability can perform such a filesystem write.
- The projection is explicitly non-authoritative and best-effort. SQLite plus
  the paired ledger event remain untouched; readers skip malformed/unknown
  lines, cap reads, treat all answers as untrusted data, and never infer
  non-attempt from absence. An explicit reconcile always reconstructs the
  authority bytes.
- A same-UID actor that disregards the read-only contract can rewrite the
  derived file immediately after any possible validation as well. Continuous
  tamper resistance against that actor is not a property this plain bundle
  file can provide. The reviewed seals instead prevent stale or planted state
  from being trusted at operation boundaries and preserve authority when
  ordinary corruption, crashes, or competing app projectors occur.

This rebuttal does not weaken a test or validator and does not treat the
private sidecar as authority. Enforcing the read-only ownership rule against a
compromised same-UID lesson shell would require a different sandbox/mount
contract, outside this entry and the current same-trust deployment model.

## Confirmed protections

- The immutable, DB-minted lesson UID names a private state and lock pair
  outside the agent-writable bundle. UID grammar prevents path selection.
  State reads use no-follow bounded descriptors, require a singly linked
  regular file, stop at 4 KiB, and treat malformed, oversized, recursive,
  stale, or missing state as rebuild input.
- The lock file is opened no-follow/nonblocking, checked as a singly linked
  regular file, and held with `LOCK_EX|LOCK_NB`. Contention returns
  `projection: pending` after the authority commit. Unlock and close remain in
  nested `finally` paths; a failed close is not retried on a reused fd number.
- Public reconcile and post-commit append refuse an active caller transaction.
  Projection work therefore cannot publish caller-local uncommitted rows.
- The durable cursor binds both the maximum row id to its `attempt_id` and the
  projection sort tail to its (`created_at`, `attempt_id`) authority row.
  A restored database whose old sidecar is ahead or points at different rows
  rebuilds instead of blessing an empty unseen-row query.
- The fast path trusts a file only when its full device/inode/size/mtime/ctime
  seal and single-link regular-file guard match state. It selects at most two
  rows beyond the cursor, appends only exactly one strict sort successor, uses
  a complete write loop, fsyncs, rechecks the descriptor, reads back only the
  appended line, closes once, checks the published name, and then atomically
  publishes private state.
- Missing, forged, truncated, reordered, special, multi-link, and multi-row
  lag states rebuild. Rebuild iterates the ordered SQLite cursor, keeps its
  mode-0600 temporary descriptor open across replace, fsyncs file and
  directory, compares stable render fields and the post-replace descriptor/
  name seal, closes once, and only then publishes the cursor. Empty directory
  collisions are removed; non-empty collisions are moved aside without
  destroying their content.
- Cross-process projector B cannot overwrite projector A's snapshot while A
  holds the UID lock. B commits authority without waiting on projection,
  returns pending on contention, and a later holder observes B beyond the
  cursor. The stale-rebuild exclusion therefore survives without the former
  database-wide `BEGIN IMMEDIATE`.
- Schema v13 adds the `(lesson_id, id)` cursor index and
  `(lesson_id, created_at, attempt_id)` sort/anchor index through an idempotent
  migration. No authority row, event, idempotency key, HTTP response, refusal
  ordering, rate policy, or projection line changes.
- Merge commit `f004546` and branch head `4256a2c` resolve to the same tree
  `370e3d5e37752b379801c979565de2e1de2c67e9`; no ephemeral preview hash is
  needed or treated as repository evidence.

## Prior-condition reconciliation

- **Attempt-backend A2 — RESOLVED.** A successful nominal append no longer
  renders or rereads lifetime history and no projection section holds
  SQLite's database-wide writer lock. Repair memory is bounded by one record;
  repair time remains proportional to authority by the frozen full-reconcile
  contract, without blocking unrelated WAL writers.
- **Attempt-backend A1 — RESOLVED by entry #59.** Both aliases enforce the
  256-KiB delivered-byte limit while consuming the ASGI stream and reject
  negative declared lengths before a body read.
- **Bridge-runtime D5 L1 — REMAINS MITIGATED; D5 L2/L3 — REMAIN RESOLVED.**
  The navigation-stable `WindowProxy` residual and direct-loopback same-trust
  condition are unchanged. Versioned byte-bound serving, bounded identity,
  parent settle/fresh checks, and D4 record-time authority remain intact.
- **Attempt authority, D3 guidance, and data-boundary protections — REMAIN
  RESOLVED.** Row/event atomicity, replay/conflict semantics, question and
  page validation, stale derivation, origin/Host middleware, no-store
  responses, private-answer routing, bounded projection readers, and
  untrusted-data treatment are unchanged.
- **Deployment and terminal conditions — retain their prior dispositions.**
  The direct/no-forwarded-header condition remains mitigated by direct
  loopback. Terminal-opt-in remains open/accepted only for the deliberately
  plain owner shell. No listener, authentication, terminal, sandbox, or live
  runtime behavior changed in this entry.

## Closing verification

- `git diff --check 13cab1b..4256a2c` — passed.
- Merge/tree identity — `f004546^{tree}` equals `4256a2c^{tree}`.
- Host `python verify.py` at the reviewed application tree — **770 passed,
  0 failed**, including streaming reconcile, history-independent append,
  unrelated-writer concurrency, busy-lock recovery, cursor-restore anchors,
  rebuild/append mutation seals, and stale-rebuild exclusion.
- Host `python verify_restore.py` — **28 passed, 0 failed**.
- No fix commit was required; this drain consumed **0 of 10** cycles.

## Closing verdict

**SAFE TO MAKE LIVE for the documented direct-loopback `127.0.0.1:8765`,
single-worker, unauthenticated single-user deployment.** This entry has **0
Critical, 0 High, 0 Medium, 0 Low, and 0 Info** findings and no open finding.
Attempt-backend follow-up **A2 is resolved**; together with entry #59, **A1 is
also resolved**. D5 L1 remains mitigated and continues to prohibit wider trust
postures; D5 L2/L3 remain resolved. Wider, proxy-adjacent, or multi-user
deployment remains **NO**. Queue entry #58 may move to Done, making Pending
empty. A live restart remains owner-only and was not performed.
