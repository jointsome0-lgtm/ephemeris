# Chain U queue batch — adversarial security review

## Review parameters

**Scope:** every Pending entry in `docs/reviews/QUEUE.md` at the clean starting
tree:

- issue #133 tier 2, the `welcome.record` snapshot landed as `d6e15bc`;
- issue #23 limits/retention plus its manifest-reader repair, landed as
  `898bbed` and `3d92e37`;
- issue #135, the `runs.jsonl` bundle projection landed as `64c2fb0`.

The listed paths and their direct callers were reviewed as one batch. No
Pending entry was skipped.

**Starting HEAD:** `9613c180cd7b586131ef3f548bedfeafe7732205` on `main`,
equal to `origin/main`. `git status --short --branch` showed only
`## main...origin/main`; the starting worktree was clean.

**Reviewed tree:** the canonical landed commits `898bbed`, `3d92e37`,
`64c2fb0`, and `d6e15bc` are ancestors of the starting HEAD. The issue #23
application range is reachable directly. The issue #23 repair branch tip
`c983b1d`, issue #135 final branch tip `dfb4a0d`, and issue #133 tier-2 final
branch tip `5d452328` are not ancestors, but each is byte-identical to its
reachable canonical landed commit: respectively trees `5684085`, `ad217ed`,
and `8288526`. The verdict is about the reachable current `main` tree; no
GitHub preview hash is used and no entry is out of scope.

**Report file:** `docs/reviews/2026-08-06-chain-u-batch-review.md`, derived
from today's date and the chain-U batch subject.

**Prior reports to reconcile:** every existing `docs/reviews/*-review.md`
closing verdict was scanned. The reports that bind these surfaces are the
August 4 limits/retention diagnosis; the lesson record-panel resolution; the
lesson bridge runtime, CSP-profile, check-activation, bridge-conventions, and
editor/run frontend reviews; the runner-core and run-API reviews; and the
attempt/assessment projection reviews. Their named conditions are reconciled
below.

**Validation baseline:** host `uv run pytest` passed **263 tests** and host
`uv run python verify_restore.py` passed **34 tests, 0 failed** at the clean
starting HEAD. Initial nested-sandbox runs stalled at the established
TestClient/PTY boundary and were interrupted without being counted.

## Context and method

The deployment decision assumes the documented direct-loopback
`127.0.0.1:8765`, single-worker, unauthenticated single-user app. No service
was restarted or signalled, no live-data path was read or written, and every
probe used invented throwaway data.

The canonical landing diffs, current changed files, direct callers, generated
TypeScript, tests, contracts, and prior reports were read. The pass covered
body admission and manifest totality; projection identity, publication,
growth, durability, and consumption; bridge grant, document identity,
question scoping, escaping, navigation, and outbound channels. Queue prose,
tests, commits, and prior verdicts were treated as claims and checked against
the current source.

## Findings

### M1 — Ungated `welcome.record` can leave the private instance through same-frame navigation (Medium, confirmed)

The parent attaches the current page's answer excerpts and tutor notes to the
welcome whenever child-controlled capability negotiation includes `attempts`
(`app/static/src/learn-bridge.ts:1364-1429`). There is no owner interaction.
The ABI makes that deliberate and names the exact risk: the data is ungated
even though the receiving document retains the same-frame-navigation residual
(`docs/lesson-bridge-abi.md:175-187`).

That residual is an outbound channel, not only a lifecycle wrinkle. The
interactive profile explicitly permits scripts and cannot stop the iframe
from assigning `location.href`; the destination request is outside the
response CSP (`app/routers/learn.py:629-659`,
`docs/learn-bundle-spec.md:361-374`). A lesson page can therefore request
`attempts`, receive the learner's answer excerpts and the tutor's notes, and
navigate itself to an external URL carrying those bytes. Loopback binding does
not mitigate a browser making an outbound request.

An invented headless-Chrome probe used the production-relevant
`sandbox allow-scripts`, `connect-src 'none'`, and `form-action 'none'`
directives. Its child assigned `location.href` after load; the throwaway
server received `/captured?value=invented-answer`. The existing artifact-read
path already treats this same channel as a private-data boundary: it asks in a
parent-owned, sticky confirmation and explicitly warns that the page can
navigate and send saved code away (`app/static/src/learn-bridge.ts:677-705`,
`docs/lesson-bridge-abi.md:348-355`). Read-back bypasses that protection for
answers and notes.

The exposure is bounded to recorded questions declared on the current page,
and the answer body is the panel excerpt rather than the full attempt. It is
still private runtime content disclosed to untrusted page script without a
decision, and a malicious or compromised bundle can transmit it on ordinary
load. That makes the failure Medium under the supported single-user
deployment.

**Fix direction:** the owner must choose the trust/consent contract. The
smallest established direction is a parent-owned, per-document consent gate
before attaching `record`, with denial omitting the field and approval warning
about navigation egress as the artifact-read gate does. The repair needs an
exact-browser regression proving that denial sends no answer/note bytes and
that approval remains document-scoped. A different trusted-bundle or
non-navigable isolation design is an owner decision, not this diagnosis run's
work.

### L1 — The append-only run projection has no storage ceiling (Low, confirmed)

Every finished run can append an 8 KiB UTF-8 output tail to `runs.jsonl` at the
existing limit of ten starts per lesson per minute
(`app/services/runs.py:23-38`, `201-223`, `553-560`). Publication is constant
work because it appends in place (`app/services/runs.py:461-484`), but neither
the writer nor any maintenance path rotates, compacts, or removes old lines.
The generated consumer contract states both “no compaction” and “no ceiling”
(`app/services/lessons.py:1396-1406`, `1572-1576`). The child's run operation
requires the capability and normal structural checks but no user activation
(`app/static/src/learn-bridge.ts:1282-1358`).

The raw 8 KiB bound is not the file-growth bound because JSON escaping expands
control bytes. An invented legal NUL-heavy tail produced a **49,622-byte**
projection line through `_projection_record()` and the real serializer. Ten
such completions per minute sustain about **682 MiB/day for one lesson**.
Ordinary output grows more slowly, but still forever. The projection lives in
the private bundle and is included in instance storage/backup work, so a long-
running or hostile interactive page can consume the data volume until normal
writes or backups fail.

The runner's active-job, output, and per-minute limits make this gradual rather
than an immediate resource-exhaustion path, and the documented deployment is
single-user. That keeps the severity Low; it does not turn an unbounded durable
write into a safe storage contract.

**Fix direction:** choose and document a durable per-lesson byte or record
retention bound, preserve the newest complete records the consumer already
prioritizes, and keep the seal/collision and crash-durability guarantees. Add
regressions at and beyond the bound, including maximally escaped output and a
publication interruption. The current explicit no-compaction contract makes
the retention choice owner-level; this run does not make it.

No Critical, High, Info, or other finding survived review.

## Confirmed protections and non-findings

- The August 4 malformed-manifest findings are repaired. Claimed byte counts
  outside `0..2**63-1` reject the manifest before formatting, and offset-less
  timestamps are attached to `app_tz()` before conversion. The full issue
  suite covers impossible sizes, fallback to an older set, no-backup warning,
  and different host/configured zones; there is no later application delta on
  that surface.
- `runs.jsonl` omits output from the authoritative ledger/export event, caps
  the raw tail at 8 KiB, uses an app-private seal and lock, refuses symlink or
  multi-link publication nodes, fsyncs staged data/state/directories, preserves
  foreign collisions, and checks current bundle identity before publication.
  Terminal-open verification and the generated data-boundary wording reduce
  forged-history risk. The accepted same-user post-validation window remains
  a non-authoritative projection tradeoff; it is not widened into a separate
  finding here.
- `welcome.record` is projected from the same database/manifest reading as
  the Record panel, carries only the current page's declared recorded ids,
  revalidates those ids from fresh metadata at grant time, binds the snapshot
  to lesson/page identity, and remains absent without the `attempts` grant.
  Learner/tutor strings stay inert in the parent attribute and the child
  contract requires text insertion. These controls prevent cross-page mixups
  and markup injection, but do not close M1's outbound navigation channel.
- The emitted `learn-bridge.js` remains reproducible from the TypeScript; a
  fresh `npm run build` produced no tracked diff.

## Prior-condition reconciliation

- **Limits/retention L1 and L2 — RESOLVED by `3d92e37`.** The status reader is
  total for the confirmed impossible-size case and ledger-zone correct for
  accepted naive timestamps. Central write-guard W1, streaming body admission,
  service bounds, export retention, consistent backup, and public-data
  posture remain resolved/unchanged.
- **Attempt A1/A2 and assessment/attempt authority — REMAIN RESOLVED.** Later
  drains superseded the older “A2 open” wording. Request streaming,
  record-time authority, projection locks/seals, replay behavior, and bounded
  tutor consumption are unchanged.
- **Record-panel false-retirement and page-selection findings — REMAIN
  RESOLVED.** One final manifest read still owns response metadata, record
  classification, and selection persistence. The shared-reader explicit-null
  diagnostic remains **STILL OPEN outside this batch** and is neither used nor
  masked by the bridge's validated `bridge_page.questions` list.
- **Bridge D5 L2/L3 and bridge conventions — REMAIN RESOLVED.** Served-byte
  identity, bounded hashing/cache behavior, source/origin/envelope validation,
  one-result handling, and fresh request ids are unchanged. **D5 L1 was
  previously MITIGATED for capability-bearing writes, but its accepted
  same-frame-navigation residual is no longer an acceptable private-read
  posture after M1.** Artifact reads remain resolved because their consent
  gate covers that residual; `welcome.record` does not.
- **Runner core, run API, relay/cancel ownership, and artifact authority —
  REMAIN RESOLVED.** Snapshot isolation, fd authority, job/output/capacity
  limits, SSE parsing, and compare-and-publish behavior are unchanged. L1 is a
  new durable-projection retention failure, not a reopening of those controls.
- **Projection publication and generated-role conditions — RETAIN THEIR PRIOR
  DISPOSITIONS.** The trusted lesson-agent's host-network, credential, and
  writable-bundle posture and the derived-file same-user window remain
  open/accepted by design. Lesson-agent/learner private masks and server-owned
  role selection remain resolved. Terminal-opt-in T1 remains open/accepted
  only for the deliberately plain owner shell.
- **Direct/no-forwarded-header and deployment boundary — REMAIN MITIGATED only
  by direct loopback.** No listener or authentication behavior changed. Wider,
  proxy-adjacent, or multi-user deployment remains unsupported independently
  of M1 and L1.

## Closing verification

- Clean starting branch and canonical commit/tree placement — passed.
- Host `uv run pytest` — **263 passed, 0 failed**.
- Host `uv run python verify_restore.py` — **34 passed, 0 failed**.
- `npm run build`, emitted-JavaScript parity, and `git diff --check` — passed.
- Invented headless-Chrome navigation probe — confirmed M1's outbound request
  under the relevant production sandbox/CSP directives.
- Invented serializer/rate probe — confirmed a 49,622-byte legal line and the
  ten-per-60-second rate used for L1's bound.
- No application, test, specification, service, or live-data file was edited.
  The only intended changes are this report and the three Pending-entry
  pointers.

## Closing verdict

**NOT SAFE TO MAKE LIVE for the current Pending batch, including the documented
direct-loopback `127.0.0.1:8765`, single-worker, unauthenticated single-user
deployment.** This drain found **1 Medium, 1 Low, 0 Critical, 0 High, and 0
Info** findings. The issue #23 manifest-reader repair is approved, and the run
projection and record snapshot retain the confirmed controls above. However,
private answer/note text can leave through an ungated page-navigation channel,
and the new durable run history has no retention bound. Wider,
proxy-adjacent, or multi-user deployment remains **NO**.

All three entries stay **Pending** and point to this report. Separate,
ordinarily reviewed repair work must carry the owner decisions and regressions
described in M1 and L1, preserve or grow the **263 / 34** baselines, retain
TypeScript/generated-JavaScript parity, and receive a fresh drain. No live
service action was performed; restart remains the owner's action after the
Pending gate is empty.
