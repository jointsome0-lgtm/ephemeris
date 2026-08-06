# Chain U repair queue batch — adversarial security review

## Review parameters

**Scope:** every Pending entry in `docs/reviews/QUEUE.md` at the clean starting
tree:

- issue #133 tier 2, the `welcome.record` snapshot landed as `d6e15bc`;
- issue #23 limits/retention and its manifest-reader repair, landed as
  `898bbed` and `3d92e37`;
- issue #135, the `runs.jsonl` projection landed as `64c2fb0`;
- the chain-U M1/L1 repair, landed as `6b99ab2`.

The listed paths and their direct callers were reviewed as one batch. No
Pending entry was skipped.

**Starting HEAD:** `86629cea663e6a0549c46d445f61a58fddb3d1cb` on `main`.
The worktree was clean, local `HEAD` equalled `origin/main`, and the live
GitHub `main` ref was independently read as the same SHA after an SSH fetch
could not resolve GitHub.

**Reviewed tree:** the canonical landed commits `898bbed`, `3d92e37`,
`64c2fb0`, `d6e15bc`, and `6b99ab2` are ancestors of the starting HEAD. The
queue also retains pre-squash branch commits for traceability: the issue #135
tip `dfb4a0d`, issue #133 tier-2 tip `5d452328`, and repair tip `eb160737` are
not ancestors, but their trees are byte-identical to the reachable landed
commits: respectively `ad217ed`, `8288526`, and `4295e30`. The verdict is
about reachable current `main`; no GitHub preview hash is used and no entry is
out of scope.

**Report file:** `docs/reviews/2026-08-06-chain-u-repair-review.md`, derived
from today's date and the repair batch subject.

**Prior reports to reconcile:** every existing
`docs/reviews/*-review.md` closing verdict was scanned. The immediately
binding report is `2026-08-06-chain-u-batch-review.md`; its two findings and
all conditions it inherited from the August 4 limits/retention, record-panel,
bridge runtime/CSP/check-activation/conventions, runner, run-API, attempt, and
assessment-projection reports are reconciled below.

**Validation baseline:** host `uv run pytest` passed **280 tests** and host
`uv run python verify_restore.py` passed **34 tests, 0 failed** at the clean
starting HEAD. The repair PR must preserve or grow both counts.

## Context and method

The deployment decision assumes the documented direct-loopback
`127.0.0.1:8765`, single-worker, unauthenticated single-user app. No service
was restarted or signalled, no live-data path was read or written, and every
probe used invented throwaway data.

The canonical landing diffs, current changed files, direct callers, generated
JavaScript, tests, contracts, and prior reports were read. The pass covered
record-snapshot consent, document identity and navigation timing; run-history
retention, whole-record cutting, bounded legacy reads, seal-conditioned
publication and crash behavior; and the unchanged limits/manifest repairs.
Queue prose, tests, commits, and prior verdicts were treated as claims and
checked against the current source.

## Finding

### L1 — Consent can be delivered to a successor document before its delayed `load` (Low, confirmed)

The new gate asks before attaching private answers and tutor notes, then waits
250 ms and checks `generation` before posting the welcome
(`app/static/src/learn-bridge.ts:1387-1481`). That generation changes only in
the iframe's `load` handler (`app/static/src/learn-bridge.ts:492-518`). An
iframe's `WindowProxy` remains the same across navigation, and a successor can
run script while one of its subresources still delays `load`. During that
interval every post-consent check at lines 1474-1477 still passes, and line
1481 delivers the old document's welcome, transferred port, and `record` to
the successor.

The ABI states both the intended per-document promise and this exact residual
(`docs/lesson-bridge-abi.md:178-208`). The shipped browser regression named
"consent is scoped to the document" changes the version only after the first
welcome has already arrived (`tests/test_210_bridge_browser.py:418-442`); it
does not exercise a successor that becomes active during the approval-to-
delivery interval.

A focused headless-Chrome probe ran the committed `learn-bridge.js` under the
production-relevant `sandbox="allow-scripts"`. An invented settled page began
a same-frame navigation and announced `ready`; the invented successor
installed a message handler and held its `load` event open with a four-second
image. After one approval, the successor received the welcome containing
`invented-successor-answer-orbit-9` and
`invented-successor-note-orbit-9`, then carried both values into an invented
`/captured` navigation request. The later `load` did trigger teardown and
parent re-assertion, but only after the private bytes had crossed.

The owner does make an explicit decision, and the prompt warns that the page
can navigate and send what it reads elsewhere. That materially reduces the
automatic disclosure established by the first chain-U report. It does not
satisfy the documented statement that approval covers the document that was
asked about: the recipient can be a successor the owner was not shown. The
remaining failure therefore earns Low rather than the original Medium, but it
is a real private-data boundary failure in the supported deployment.

**Fix direction:** make the announcing document prove possession of a channel
that a navigation cannot transfer, or use a non-navigable isolation profile;
the ABI already identifies those as owner decisions. The repair needs an
exact-browser regression where page A starts navigation before announcing,
page B delays `load`, and approval for A sends no `record` bytes or port to B.
This diagnosis run does not choose or implement the ABI change.

No Critical, High, Medium, Info, or other finding survived review.

## Confirmed protections and non-findings

- The original automatic-read M1 is materially mitigated. A refusal or an
  unavailable dialog omits `record`, an approval is sticky only until
  teardown, empty disclosure is not prompted, the snapshot remains bound to
  lesson/page identity and freshly declared question ids, and ordinary
  completed reloads ask again. L1 is the narrower in-flight delivery gap.
- The original unbounded-projection L1 is resolved. `runs.jsonl` is capped at
  20 MiB in the production constants; an oversize append retains the newest
  whole records to a three-quarter watermark, reads at most that bounded tail
  from a legacy file, and keeps the newest line. Fast-path appends, app-private
  locking/seals, foreign-file preservation, staged fsync/rename publication,
  and failure-after-authority behavior remain intact. No new run-projection
  finding was established.
- The August 4 malformed-manifest findings remain resolved. Impossible claimed
  byte counts reject the manifest before formatting and offset-less timestamps
  use the configured ledger zone. No later application delta touched
  `app/security.py`, `app/limits.py`, or the repaired storage reader.
- `npm run build` reproduced the committed JavaScript with no tracked diff,
  and `git diff --check` passed for the reviewed application range.

## Prior-condition reconciliation

- **Chain-U M1 — MITIGATED, NOT RESOLVED.** Automatic disclosure is closed,
  refusal is effective, and ordinary reloads are document-scoped. L1 proves
  that approved bytes can still cross to an in-flight successor before
  `load`; the prior report's required document-scoped approval condition is
  therefore still open at lower severity.
- **Chain-U projection L1 — RESOLVED by `6b99ab2`.** Durable history now has a
  byte ceiling, newest-complete-record retention, bounded migration from a
  legacy oversized file, and seal-conditioned compaction. The 8 KiB raw tail,
  2 MiB consumer window, durability, and untrusted-output treatment remain
  consistent.
- **Limits/retention L1 and L2 — REMAIN RESOLVED.** Manifest numeric totality,
  ledger-zone timestamp handling, central write admission, service bounds,
  export retention, backup consistency, and public-data posture are unchanged.
- **Attempt A1/A2, assessment/attempt authority, record-panel retirement and
  page selection — REMAIN RESOLVED.** The shared-reader explicit-null
  diagnostic remains open outside this batch and is neither used nor masked
  here.
- **Bridge D5 L2/L3, bridge conventions, artifact reads, runner core/run API,
  relay/cancel ownership, and artifact authority — REMAIN RESOLVED.** D5 L1's
  navigation-stable `WindowProxy` ambiguity remains the root of this finding;
  the old write-only form stays mitigated by fresh server authority checks,
  but those checks cannot retract private bytes already placed in a welcome.
- **Projection publication and generated-role conditions — RETAIN THEIR PRIOR
  DISPOSITIONS.** The trusted lesson-agent's host-network, credential, and
  writable-bundle posture and the derived-file same-user window remain
  open/accepted by design. Lesson-agent/learner private masks and server-owned
  role selection remain resolved. Terminal-opt-in T1 remains open/accepted
  only for the deliberately plain owner shell.
- **Direct/no-forwarded-header and deployment boundary — REMAIN MITIGATED only
  by direct loopback.** No listener or authentication behavior changed.
  Wider, proxy-adjacent, or multi-user deployment remains unsupported.

## Closing verification

- Clean starting branch, live `main` SHA, and canonical commit/tree placement
  — passed.
- Host `uv run pytest` — **280 passed, 0 failed**.
- Host `uv run python verify_restore.py` — **34 passed, 0 failed**.
- `npm run build`, emitted-JavaScript parity, and `git diff --check` — passed.
- Invented delayed-load headless-Chrome probe — confirmed L1: after one
  approval, the successor received and navigated with both invented private
  strings before its `load` event.
- No application, test, specification, service, or live-data file was edited.
  The only intended changes are this report and the four Pending-entry
  pointers.

## Closing verdict

**NOT SAFE TO MAKE LIVE for the current Pending batch, including the documented
direct-loopback `127.0.0.1:8765`, single-worker, unauthenticated single-user
deployment.** This re-drain found **1 Low, 0 Critical, 0 High, 0 Medium, and 0
Info** findings. The projection ceiling and the issue #23 repairs are approved,
and the consent gate removes automatic read-back disclosure. However, its
document-scoping contract fails in the delayed-`load` successor case. Wider,
proxy-adjacent, or multi-user deployment remains **NO**.

All four entries stay **Pending** and point to this report. A separate,
ordinarily reviewed repair must carry the owner-approved ABI or isolation
decision and the delayed-successor browser regression, preserve or grow the
**280 / 34** baselines, retain TypeScript/generated-JavaScript parity, and
receive a fresh drain. No live service action was performed; restart remains
the owner's action after the Pending gate is empty.
