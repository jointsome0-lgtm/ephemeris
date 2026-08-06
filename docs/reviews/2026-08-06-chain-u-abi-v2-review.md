# Chain U ABI v2 queue batch — adversarial security review

## Review parameters

**Scope:** every Pending entry in `docs/reviews/QUEUE.md` at the clean starting
tree:

- the ABI v2 document-bound handshake repair, landed as `2e636c2`;
- issue #133 tier 2, the `welcome.record` snapshot landed as `d6e15bc`;
- issue #23 limits/retention and its manifest-reader repair, landed as
  `898bbed` and `3d92e37`;
- issue #135, the `runs.jsonl` projection landed as `64c2fb0`;
- the chain-U consent/retention repair, landed as `6b99ab2`.

The listed paths and their direct callers were reviewed as one batch. No
Pending entry was skipped.

**Starting HEAD:** `32d79b5a2d5d13897406c558ec61eada408521de` on `main`.
The worktree was clean, local `HEAD` equalled `origin/main`, and the live
GitHub `main` ref was independently read as the same SHA.

**Reviewed tree:** the canonical landed commits `898bbed`, `3d92e37`,
`64c2fb0`, `d6e15bc`, `6b99ab2`, and `2e636c2` are ancestors of the starting
HEAD. The queue retains pre-squash branch commits for traceability. The issue
#135 tip `dfb4a0d`, issue #133 tier-2 tip `5d452328`, chain-U repair tip
`eb160737`, and ABI v2 tip `7d0af676` are not ancestors, but their trees are
byte-identical to the reachable landed commits: respectively `ad217ed`,
`8288526`, `4295e30`, and `83efd50`. The verdict is about reachable current
`main`; no GitHub preview hash is used and no entry is out of scope.

**Report file:** `docs/reviews/2026-08-06-chain-u-abi-v2-review.md`, derived
from today's date and the current ABI v2 repair batch subject.

**Prior reports to reconcile:** every existing
`docs/reviews/*-review.md` closing verdict was scanned. The immediately
binding report is `2026-08-06-chain-u-repair-review.md`; its remaining Low
finding and the conditions it inherited from the chain-U batch, August 4
limits/retention, record-panel, bridge runtime/CSP/check-activation/
conventions, runner, run-API, attempt, and assessment-projection reports are
reconciled below.

**Validation baseline:** host `uv run pytest` passed **283 tests** and host
`uv run python verify_restore.py` passed **34 tests, 0 failed** at the clean
starting HEAD. The initial restricted `pytest` run stalled at the established
TestClient/subprocess boundary and was interrupted without being counted.
Any later repair must preserve or grow both counts.

## Context and method

The deployment decision assumes the documented direct-loopback
`127.0.0.1:8765`, single-worker, unauthenticated single-user app. No service
was restarted or signalled, no live-data path was read or written, and every
probe used invented throwaway data.

The canonical landing diffs, current changed files, direct callers, generated
JavaScript, browser fixtures, tests, contracts, and prior reports were read.
The pass covered handshake source/shape admission, reply- and bridge-port
lifecycle, consent and snapshot scoping, navigation timing and persistent
execution contexts, capability revalidation, generated child guidance, run-
history retention/publication, and the unchanged limits/manifest repairs.
Queue prose, tests, commits, and prior verdicts were treated as claims and
checked against the current source.

## Findings

No Critical, High, Medium, Low, Info, or other finding survived review.

## Confirmed protections and non-findings

- ABI v2 closes the delayed-successor disclosure at the delivery primitive.
  The global listener still admits only the current frame source and a bounded,
  valid `ready`, then requires exactly one transferred reply port
  (`app/static/src/learn-bridge.ts:1593-1625`). `finishReady` sends the whole
  result, including the approved `record` and the separate bridge port, only on
  that reply port; it never posts into the navigation-stable `WindowProxy`
  (`app/static/src/learn-bridge.ts:1403-1509`). Teardown closes both retained
  parent endpoints (`app/static/src/learn-bridge.ts:370-393`).
- The announcing document is the only supported recipient. Under the deployed
  `sandbox allow-scripts` interactive profile, it cannot open a popup or nested
  frame, use a same-origin window handle, or create a worker that survives the
  document; a same-frame successor gets a fresh opaque origin and does not
  inherit the reply port (`app/routers/learn.py:629-672`,
  `docs/lesson-bridge-abi.md:24-50`). A focused headless-Chrome regression
  started navigation before the announcement, let the successor run while its
  `load` stayed blocked for four seconds, approved the prompt, and observed no
  private bytes, handshake result, bridge port, or egress request in the
  successor.
- Consent remains fail-closed and per document. A refusal or unavailable
  dialog omits `record`; an approval is cleared by teardown; the snapshot is
  still bound to lesson/page identity and freshly declared question ids; and
  empty disclosure remains unprompted. The 250 ms settle and generation check
  remain defence in depth rather than the recipient boundary
  (`app/static/src/learn-bridge.ts:699-728`, `1446-1503`).
- Port possession remains routing rather than authority. Attempt, artifact,
  run, relay, and cancel operations continue to re-check the current
  generation, fresh metadata identity, declared question/block membership,
  and server-side record-time authority. ABI v2 changes the handshake
  transport, not those operation contracts.
- The breaking v1-to-v2 compatibility cost is explicit rather than hidden. A
  port-less v1 announcement gets silence before consent; generated lesson
  guidance, the ABI document, and all shipped bridge fixtures use fresh
  per-retry channels. Owner-approved PR #153 rejected a `WindowProxy` fallback
  because it would recreate the finding; an older authored child therefore
  stays usable read-only until updated rather than receiving an unsafe grant.
- The original unbounded-projection finding remains resolved. `runs.jsonl`
  retains its 20 MiB ceiling, newest-whole-record compaction, bounded legacy
  read, app-private lock/seal, conditional staged publication, and terminal-
  open foreign-file retirement. No application delta after `6b99ab2` changed
  that surface.
- The August 4 malformed-manifest findings remain resolved. Impossible claimed
  byte counts reject the manifest before formatting and offset-less timestamps
  use the configured ledger zone. No later application delta touched
  `app/security.py`, `app/limits.py`, or the repaired storage reader.

## Prior-condition reconciliation

- **Chain-U successor L1 — RESOLVED by `2e636c2`.** The welcome and bridge port
  are no longer addressed to the navigation-stable `WindowProxy`; the exact
  delayed-`load` successor receives neither. The owner-approved document-
  scoped channel and required browser regression are both present.
- **Chain-U automatic-read M1 — RESOLVED.** The consent repair closed automatic
  disclosure, and ABI v2 now makes an approval reach only the announcing
  document. Refusal, identity/question filtering, private-data wording, and
  same-frame-egress warning remain intact.
- **Chain-U projection L1 — REMAINS RESOLVED by `6b99ab2`.** Durable history
  keeps the byte ceiling, newest-complete-record retention, bounded migration,
  and seal-conditioned compaction established by the prior re-drain.
- **Limits/retention L1 and L2 — REMAIN RESOLVED.** Manifest numeric totality,
  ledger-zone timestamp handling, central write admission, service bounds,
  export retention, backup consistency, and public-data posture are unchanged.
- **Attempt A1/A2, assessment/attempt authority, record-panel retirement and
  page selection — REMAIN RESOLVED.** The shared-reader explicit-null
  diagnostic remains open outside this batch and is neither used nor masked
  here.
- **Bridge D5 L1/L2/L3, bridge conventions, artifact reads, runner core/run
  API, relay/cancel ownership, and artifact authority — REMAIN RESOLVED.** ABI
  v2 removes the navigation-stable port-delivery residual while retaining
  fresh parent/server authority checks and served-byte/hash bounds.
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
- Host `uv run pytest` — **283 passed, 0 failed**.
- Host `uv run python verify_restore.py` — **34 passed, 0 failed**.
- Focused host `tests/test_210_bridge_browser.py` — **6 passed**, including the
  delayed-successor, transferred-welcome/bridge-port, and port-less-v1 cases.
- `npm run build` reproduced the committed JavaScript with no tracked diff;
  `git diff --check` passed.
- No application, test, specification, service, or live-data file was edited.
  The only intended changes are this report and the five Pending-entry
  pointers.

## Closing verdict

**SAFE TO MAKE LIVE for the reviewed current tree in the documented direct-
loopback `127.0.0.1:8765`, single-worker, unauthenticated single-user
deployment.** This re-drain found **0 Critical, 0 High, 0 Medium, 0 Low, and 0
Info** findings. ABI v2 resolves the remaining document-scoping failure, and
the consent, run-projection, and issue #23 repairs retain their approved
controls. Wider, proxy-adjacent, or multi-user deployment remains **NO**.

All five entries stay **Pending** and point to this report, as required for a
diagnosis-only drain. No further repair PR is required by this review; the
owner may close the entries after accepting the clean verdict. No live service
action was performed; any restart remains the owner's action after the Pending
gate is empty.
