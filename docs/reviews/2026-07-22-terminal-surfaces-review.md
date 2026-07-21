# E4 terminal surfaces — adversarial security review

**Scope:** exactly one Pending entry was present at review start: the 2026-07-21
entry for `9c85795..HEAD` on `fix/42-e4-terminal-surfaces`, covering
`app/static/src/terminal.ts`, emitted `app/static/terminal.js`,
`app/templates/base.html`, `app/templates/learn.html`, `app/static/style.css`,
`.gitattributes`, and `verify.py`, plus their direct callers. The scoped range
is `9c85795^..862d3b3`, three commits from `9c85795` through `862d3b3`; those
commits landed on `main` through merge commit `d355af1` before this drain.

**Starting HEAD:** `d355af15b65a19466af55bc5ac137c2cc39d0391` on `main`.
After refreshing `origin`, local `main` fast-forwarded to that exact remote head
and `git status --porcelain=v1 --untracked-files=all` was empty. `d355af1` is a
two-parent merge of prior `main` and `862d3b3`; its tree `e103fb6` is
byte-identical to the E4 branch head's tree. The starting worktree was clean.
The adversarial pass was first executed on the clean, tree-identical branch
head before live PR reconciliation exposed the fix-target error; the full
baseline was rerun on `d355af1` before the canonical fix replay, so no review
evidence depends on a different source tree.

**Fix target:** `git merge-base --is-ancestor 862d3b3 d355af1` returned zero,
and the merge commit names `862d3b3` as its second parent. The change was
already merged, so repository tradition requires drain fixes directly on
`main`; no new PR or merge is needed. An initial branch-only closeout was
pushed after an incorrect reachability reading. Its reviewed fix tree was
replayed byte-identically on `main` as `6af3af1`, and the report/queue hashes
below name only the canonical main fix.

**Report file:**
`docs/reviews/2026-07-22-terminal-surfaces-review.md`, derived from the entry's
E4 “terminal surfaces” subject.

**Prior reports to reconcile:** every existing `docs/reviews/*-review.md`
closing verdict was scanned. The most recent binding same-phase report is
`2026-07-21-lesson-learner-sandbox-review.md`: E3 resolved learner isolation
for the server terminal path but left the two-surface client as later work;
runner support remained later scope. It also requires terminal-tab L1 to stay
resolved, terminal-opt-in T1 to remain resolved for the two sandboxed lesson
roles but accepted for the deliberately plain owner shell, and the agent's
host-network and credential posture to remain confined to the trusted agent
role. The direct/no-forwarded-header condition remains mitigated only by the
documented direct-loopback deployment. Because E4 changes `learn.html`, D5 L1
remains mitigated, D5 L2/L3 remain resolved, and D4 A1/A2 remain accepted Low
follow-ups; E4 must not claim to resolve or regress them. Earlier terminal
F1–F4, workspace fail-closed behavior, relative-path display, atomic brief,
and bundle-reader protections remain resolved and bind this rewrite. Their
current-head dispositions are stated explicitly in the closing verdict.

**Validation baseline:** at starting HEAD, the required host runs passed:
`python verify.py` — **636 passed, 0 failed**; `python verify_restore.py` —
**28 passed, 0 failed**. The initial runs inside the reviewer's restricted
sandbox reproduced the known silent TestClient startup stall and were
interrupted without assertion output; the recorded counts are from approved
host reruns of the exact commands at the same head.

## Context and method

The deployment decision assumes an unauthenticated, single-user, single-worker
app bound directly to `127.0.0.1:8765`. No service was restarted or signalled,
and no live database, lesson bundle, export, browser profile, screenshot, or
authenticated state was read or written.

The complete scoped diff and queue-listed files are reviewed with their direct
template-loading, storage, lesson-selection, WebSocket/session, and server-role
call paths. Verifier checks and queue prose are treated as claims to verify,
not as review authority. Static and throwaway-runtime review cover server-owned
role acceptance, deliberate-connect behavior, cross-page and cross-lesson
storage separation, malformed persisted state, DOM and URL sinks, reconnect
and close/delete/recovery transitions, concurrent drawers, generated-runtime
parity, terminal trust/lifecycle invariants, and narrow-deployment boundaries.

## Findings (severity-ranked)

### M1 — Clean CI does not verify the generated full-shell client (Medium, confirmed)

E4 makes TypeScript the review surface and marks the committed executable
`app/static/terminal.js` as generated (`.gitattributes:1-4`), while the
zero-build deployment serves that JavaScript file directly. The intended
source/served-artifact byte comparison in `verify.py` runs only when the
ignored, untracked `node_modules/.bin/tsc` exists; otherwise the verifier prints
an informational skip and succeeds (`verify.py:1161-1180`). The clean GitHub
workflow installs only Python/uv dependencies before invoking `verify.py` and
never runs `npm ci` (`.github/workflows/ci.yml:15-30`). Its repository-local
compiler check therefore always skips.

A stale or deliberately divergent generated client can consequently pass the
normal clean CI path while source review sees the correct TypeScript. On this
surface the served file constructs terminal WebSocket selectors and validates
the returned role, so divergence can erase the learner/agent UI boundary and
request the credentialed, host-network agent role. This is Medium rather than
High because exploitation requires a repository change to land and then be
deployed, and the supported listener remains direct-loopback/single-user. It is
not Low because the unchecked artifact is the executable client for a full
shell and GitHub is explicitly told to de-emphasize that artifact's diff.

The starting head is not itself divergent: a fresh emit with the lockfile's
TypeScript 5.8.3 was byte-identical to both committed generated runtimes.
Nevertheless, the queue cannot close with the clean-branch integrity gate
silently absent.

### L1 — Learner persistence drops the newest lesson at its global cap (Low, confirmed)

`readStoredTabs()` keeps the *first* 64 learner records before it filters for
the current lesson (`app/static/src/terminal.ts:144-164`). `persistTabs()` does
the opposite ordering: it preserves other lessons and appends the current
lesson's tabs at the end (`app/static/src/terminal.ts:195-203`). With 64 older
invented lesson-tab records, opening `lesson-new` stores its first tab and SID
as record 65. The next page load slices that current record away and immediately
rewrites only the older 64. A subsequent deliberate open creates a new
`role=lesson-learner` session rather than reattaching with the lost SID.

A throwaway actual-runtime harness reproduced the transition against the
committed JavaScript: the current lesson's SID disappeared after reload and the
next WebSocket URL carried the creation selector instead of `sid=`. The old PTY
remains detached until the server reaps it. Repeating the normal sequence can
consume the global eight-session allowance and cause visible creation refusals
(`app/terminal.py:361-366`, `app/terminal.py:627-630`). Impact is bounded local
availability and session continuity, not role escape or remote access, so the
finding is Low. The current lesson must remain inside the bounded persisted set
while older other-lesson records are evicted.

No Critical, High, or Info finding.

## Confirmed protections and rebutted candidates at starting HEAD

- A learner page load never opens the drawer or a WebSocket, even if its
  learner-specific open key contains `1`. The first deliberate toggle connects
  only the active current-lesson tab and uses `lesson=<server-rendered slug>`
  plus creation-only `role=lesson-learner`; A-to-B-to-A navigation preserves
  separate mappings and reattaches only after a fresh click.
- Agent and learner storage keys are disjoint. Server session roles and
  workspaces remain immutable; a stale server-minted `learner.` SID fails
  closed instead of healing into a lesson-agent session, and the client closes
  a cross-surface role mismatch. The server sends the role message before PTY
  replay bytes.
- A stored live SID paired out-of-band with the wrong same-surface lesson is
  accepted because live SID identity intentionally wins and the handshake does
  not echo workspace. No product transition writes such a pair: tab lesson is
  immutable, current-lesson filtering preserves it, and the opaque preview
  cannot write parent-origin storage. A same-origin actor able to corrupt these
  SIDs can call the same WebSocket directly. This is therefore not a new
  authority bypass, and adding workspace identity to the server handshake would
  expand the E3 contract outside this drain.
- Closing an active learner tab can select/connect a same-lesson learner
  successor. A headless-browser harness confirmed it, but it stays inside an
  already deliberately opened drawer and preserves the exact lesson, role, and
  no-network profile. Unlike the earlier terminal-tab L1, it crosses neither
  the off-Learn context boundary nor a privilege boundary, so it is ordinary
  terminal continuity rather than a security finding.
- A manually corrupted stored array containing a non-object can abort client
  initialization. No application writer emits that shape, storage writes are
  atomic, and same-origin authority can already clear or replace the record;
  this is recoverable local-state hardening, not a security finding.
- The earlier off-Learn terminal-tab L1 remains fixed: implicit close fallback
  prefers a plain tab, and an all-lesson fallback is selected without a
  connection. Persisted titles reach only text/title properties, query fields
  are percent-encoded, and the terminal-link addon recognizes web links rather
  than introducing a new markup sink.

## Initial verification

- Starting-head `python verify.py` — **636 passed, 0 failed**.
- Starting-head `python verify_restore.py` — **28 passed, 0 failed**.
- `./node_modules/.bin/tsc -p . --noEmit`,
  `node --check app/static/terminal.js`, and
  `git diff --check 9c85795^..862d3b3` — passed.
- Fresh pinned TypeScript emit — both committed generated runtimes were
  byte-identical.
- Invented-state headless/runtime probes — reproduced L1; confirmed explicit
  initial learner connection, A/B lesson separation, stale-session behavior,
  cross-surface role refusal, close/reload/clear transitions, and the earlier
  off-Learn L1 repair.

## Initial deploy verdict

**Not yet. The starting E4 tree matches its TypeScript source and does not
currently cross a terminal role boundary, but M1 leaves the normal clean CI
path unable to prove that fact and L1 can orphan learner sessions through
normal bounded storage use. The queue remains Pending until both findings are
closed directly on `main` and the exact fixed tree is re-verified. Wider,
proxy-adjacent, multi-user, or runner deployment remains NO independently.**

## CLOSING ADDENDUM — fix commit `6af3af1` (cycle 1 of 10)

### M1 — resolved

The clean CI workflow now runs `npm ci` from the committed lockfile before
`verify.py` (`.github/workflows/ci.yml:23-30`). The verifier proves that step
precedes verification and, when `CI` is set, treats a missing repository-local
TypeScript compiler as a failure instead of an informational skip
(`verify.py:1167-1197`). With the toolchain present it still compiles to a
scratch directory and byte-compares both committed generated runtimes. Thus a
clean branch can no longer obtain a green verifier result while silently
omitting the source-to-served-artifact integrity gate.

`npm ci` succeeded from the lockfile, and the fixed head's fresh TypeScript
emit is byte-identical to both `app/static/learn-bridge.js` and
`app/static/terminal.js`. Review remains centered on the TypeScript without
leaving the executable zero-build artifact unproved.

### L1 — resolved

Learner persistence now has one named 64-record global cap. Reads retain the
newest bounded window, while writes append the current lesson's tabs and apply
the same newest-record bound (`app/static/src/terminal.ts:91-92`,
`app/static/src/terminal.ts:145-179`, `app/static/src/terminal.ts:203-217`).
The historical agent first-eight behavior is unchanged. The verifier locks the
cap mechanics into both source and emitted client (`verify.py:284-289`).

Two independent executable probes supplied 64 invented older records plus a
current lesson as record 65. The starting runtime kept 64 but dropped the
current record, then requested `role=lesson-learner`. The fixed emitted runtime
kept 64 by evicting an older record, retained `learner.current-sid` and its
active key, opened no socket before the deliberate click, and then reattached
with `sid=learner.current-sid&lesson=lesson-current`. It did not use a creation
role selector. The normal cap can therefore no longer orphan the newest
learner PTY on navigation.

Fresh review of `6af3af1` found no new Critical, High, Medium, Low, or Info
finding. Both findings from this drain are closed.

## Prior-condition reconciliation at closing head

- **E3 two-surface client condition — RESOLVED for the terminal client; runner
  support remains STILL OPEN/out of scope.** E4 provides separate agent and
  learner surfaces, keeps their storage disjoint, creates learner sessions only
  on deliberate action, and accepts role from the server handshake. It does
  not claim a runner surface.
- **Terminal-tab L1 — REMAINS RESOLVED.** Off-Learn fallback still prefers a
  plain tab and never implicitly connects a lesson tab. The same-lesson
  successor connection after an explicit learner-drawer open is confined to
  that already authorized lesson/role/profile and is not the old regression.
- **Terminal-opt-in T1 — RESOLVED for `lesson-agent` and `lesson-learner`;
  STILL OPEN/ACCEPTED for the deliberately plain owner shell.** E4 changes
  client selection and presentation, not the two sandboxed roles' private PID
  namespaces or the owner shell's stated contract.
- **Agent host network, CLI login material, `SSH_AUTH_SOCK`, and proxy
  credentials — UNCHANGED/ACCEPTED for the trusted agent role only.** The
  learner client requests only `lesson-learner`; its E3 no-network profile and
  sanitized environment are unchanged.
- **Direct/no-forwarded-header launch condition — MITIGATED by the documented
  deployment, not removed from the broader threat model.** The verdict assumes
  a direct loopback listener receiving the real peer address; proxy-adjacent
  deployment remains excluded.
- **E1 S1, the E3 learner-isolation fixes, terminal F1–F4, workspace
  fail-closed behavior, relative-path display, atomic briefs, and bundle-reader
  protections — REMAIN RESOLVED.** The E4 client neither changes server-owned
  session authority nor weakens workspace, PTY/fd, writer, bundle, or generated
  publication paths. The generated-client integrity gap found here is closed
  by M1.
- **D5 L1 — MITIGATED; D5 L2/L3 — RESOLVED; D4 A1/A2 — STILL OPEN/ACCEPTED.**
  E4 does not change the iframe document-generation residual, served-byte
  binding, bounded identity reads, request-body buffering, or projection-lock
  duration. None is regressed or newly exposed by the terminal drawers.

## Closing verification

- Starting HEAD `d355af1`: `python verify.py` — **636 passed, 0 failed**;
  `python verify_restore.py` — **28 passed, 0 failed**.
- `d355af1` has `862d3b3` as its second parent and the two commits have the
  same tree; main fix `6af3af1` likewise has the same tree as the independently
  reviewed branch replay.
- `npm ci`, `npm run build`, `./node_modules/.bin/tsc -p . --noEmit`, and
  `node --check app/static/terminal.js` — passed. Fresh emitted bytes match the
  two committed JavaScript runtimes.
- Fix-cycle `python verify.py` — **638 passed, 0 failed**, including the learner
  cap and clean-CI toolchain gates.
- Fix-cycle `python verify_restore.py` — **28 passed, 0 failed**.
- The fixed-runtime 65-record learner probes passed the bounded-persistence,
  no-auto-connect, active-key, exact-SID reattach, and no-create-selector
  assertions described under L1.
- `git diff --check`, the public-hygiene checker, and the public/ignored status
  inspection — passed; no denied path, private runtime artifact, or unmarked
  fixture is tracked.

## Closing verdict

**SAFE TO MAKE LIVE for the documented direct-loopback
(`127.0.0.1:8765`), single-worker, unauthenticated single-user deployment. One
Medium and one Low finding were raised and resolved in cycle 1; no Critical,
High, Medium, Low, Info, or other finding remains open for this queue entry.
E4 resolves E3's two-surface client condition while runner support remains
outside this surface; terminal-tab L1 and the server/workspace/sandbox
protections remain resolved; terminal-opt-in T1 remains accepted only for the
deliberately plain owner shell; the trusted agent's intentional network and
credential posture is unchanged; D5 and D4 conditions retain the explicit
dispositions above. Wider, proxy-adjacent, multi-user, or runner deployment
remains NO. The queue entry may move to Done. Restarting the live service
remains the owner's action and was not performed by this review.**
