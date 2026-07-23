# Phase F blocks activation review

**Date:** 2026-07-23

## Self-parameterization

- **Scope:** the sole Pending entry in `docs/reviews/QUEUE.md`: commits after
  `1c04bd2` on `fix/35-f5-blocks-activation`, limited to the generated lesson
  brief's editor/run-block activation in `app/services/lessons.py`, its direct
  verifier anchors in `verify.py`, and the queue bookkeeping.
- **Starting HEAD:** `a29f95435d725acdc5d449eb4de3b18ea6a6470c` on a clean
  `fix/35-f5-blocks-activation` worktree.
- **Fix target:** the same PR branch. `git merge-base --is-ancestor HEAD main`
  returned false at the starting tree, while `1c04bd2` is its base; this is the
  required pre-merge template drain.
- **Report file:** `docs/reviews/2026-07-23-blocks-activation-review.md`.
- **Prior reports reconciled:** the generated pedagogy/template-E report, the
  lesson editor/run frontend report, the artifact-editor backend report, the
  runner-core and run-API reports, and the bridge runtime/conventions reports.
  Their closing conditions bind this activation: the brief writer and teaching
  boundary must not regress; the activated guidance must preserve independent
  capability degradation, authenticated port conventions, parent/server
  authority checks, artifact write ownership, text-only output, and the
  sandboxed runner contract; D5 L1 and D4 A1/A2 retain their stated
  dispositions.
- **Validation baseline:** `python verify.py` — **756 passed, 0 failed**;
  `python verify_restore.py` — **28 passed, 0 failed**.

## Context and method

The exact `1c04bd2..a29f954` history and the replaced template section were
read in full. The template writer and generated-workspace call path, the frozen
bundle block/profile/write-authority rules, the artifact/run endpoint contract,
the editor/run bridge ABI and parent capability negotiation, the lesson-agent
sandbox mounts, the F5 design memo, and the verifier anchors were reviewed as
direct context. Earlier PR-bot findings were treated as closed claims to
verify, not re-counted as this drain's findings.

The threat model is the documented direct-loopback, single-worker,
unauthenticated single-user deployment. Generated lesson pages remain untrusted
opaque-origin content and learner artifact bytes remain private runtime data.
No live service, private instance, runtime bundle, authenticated browser state,
or real learner data was read or changed.

## Starting-head verdict

No Critical, High, or Medium finding was found. Two Low instruction-contract
findings are confirmed. Both fail closed in the reviewed parent membrane and
backend, so they do not grant file or execution authority; they can instead
make generated editor/run pages unnecessarily inert or encourage guessed,
invalid protocol envelopes. The queue remains Pending until the guidance is
self-contained, independently capability-gated, and re-verified.

## Findings (severity-ranked)

### A1 — Partial capability grants can disable the working editor (Low, confirmed)

The starting text requests `attempts`, `editor`, and `run` together, then says
to keep the textarea and controls disabled unless the requested capabilities
are granted. The parent deliberately negotiates those capabilities
independently: an editor endpoint plus a declared block can grant `editor`
while `run` is absent because the backend is old, the runner is unhealthy, or
the selected runner is absent/incompatible. The bundle and ABI contracts
explicitly retain the save-only editor in those states.

Reading the starting sentence as an all-requested-capabilities gate therefore
turns a valid editor grant into an inert textarea whenever Run degrades. This
regresses the reviewed graceful-degradation matrix and can block learner saves,
but it cannot bypass the parent or backend authority checks; severity is Low.
The brief must gate textarea/Load/Save on `editor` and Run/Cancel on `run`
separately, with an editor-only page requesting no unused Run authority.

### A2 — The activated operations are not implementable from the mounted brief (Low, confirmed)

The starting section names `artifact.get`, `artifact.save`,
`artifact.save_run`, and `run.cancel`, but supplies none of their request
envelopes or the minimum revision/run state rules. Its relative references to
`docs/lesson-bridge-abi.md` and `docs/lesson-artifacts-api.md` are useful source
citations but not locally readable contracts in the lesson shell: the same
brief says the app repository is a different project that may not exist in the
session, and the lesson-agent sandbox exposes the writable bundle under a
blanked home rather than the Ephemeris checkout.

An agent following only its generated workspace instructions must therefore
guess fields such as `v`, `block_id`, `content`, `base_rev`, `after`, and
`run_id`, or avoid the feature. Guessed envelopes fail closed in the parent,
so this is bounded functional loss rather than an authority escalation; a
page that guesses response state can nevertheless ship dead controls. Severity
is Low. The active section must repeat the minimum frozen request shapes and
state transitions while keeping the full documents authoritative.

## Confirmed protections at the starting head

- The replacement remains one `_AGENTS_TEMPLATE` hunk. Every other generated
  brief section and the mode-0600 atomic writer path are unchanged; no lesson,
  title, URL, attempt, or artifact content is interpolated into the constant.
- The first PR-bot round's fixes correctly keep learner-owned artifacts
  read-only to the agent, place starter code in page state, require an
  unrejected v2 manifest with `interactive-local-v1`, and use one ready message
  that requests editor/run for a runner-backed page.
- Stable block/page identity, manifest-derived file paths, the two fixed runner
  ids and suffixes, single-file dependency-free execution, and the ban on
  manifest commands remain accurate. No schema, registry, route, sandbox, CSP,
  static, terminal, or listener code changes.
- The general bridge section still authenticates the exact parent/origin and
  envelope, consumes one handshake result, treats identity as parent-owned,
  mints lesson-wide request ids, and degrades to a useful read-only page.
- Private artifact reads still require parent-owned per-document consent.
  Editor/run writes and starts retain fresh parent and server validation;
  execution uses the reviewed immutable sandbox snapshot, and output is data,
  never authority.

## Starting-head verification

- `git diff --check` — passed.
- `python -m py_compile app/services/lessons.py verify.py` — passed.
- `python verify.py` — **756 passed, 0 failed**.
- `python verify_restore.py` — **28 passed, 0 failed**.
- `python scripts/check_public_hygiene.py` — passed; ignored-status inspection
  showed only established local tool, environment, cache, screenshot-reference,
  and review-work paths.

## CLOSING ADDENDUM — fix commit `d2f97f4` (cycle 1 of 10)

The exact-head review of `a29f954..d2f97f4` confirmed A1 and A2, then found
one additional Low instruction-contract issue in the new minimum response
guidance. The commit changes only the active editor/run-block guidance and its
verifier anchors; no runtime, endpoint, schema, ABI document, static, sandbox,
CSP, terminal, or listener path changed.

### A1 — resolved

The brief now makes capability gates explicit and independent. An `editor`
grant alone makes the textarea writable and enables Load/Save; `run` alone
enables Run/Cancel, and its absence cannot revoke an already granted editor.
An editor-only page asks for `editor`, adds `attempts` only when it also records
declared questions, and omits `run`. The save-only editor therefore survives
old-backend, runner-health, unknown-runner, and incompatible-runner degradation
as the bundle and ABI contracts require.

### A2 — resolved

The active section now repeats the four exact minimum v1 request envelopes,
labels every ellipsis as a placeholder rather than a literal id/value, and
states the required `base_rev` progression, request/run ownership matching,
monotonic output sequence handling, retry cursor, and text-only rendering.
The canonical ABI and endpoint docs remain named as authorities, while an agent
whose sandbox contains only the bundle no longer has to invent the protocol.
Parent/backend validation remains unchanged and fail closed.

### A3 — Asynchronous run errors leave generated controls active (Low, confirmed)

The cycle-1 guidance lists `run.output` and `run.exit` as the asynchronous
owned-run messages to accept, but omits the ABI's `run.error`. The parent emits
that terminal error when an SSE relay is malformed, oversized, or closes
prematurely. A generated page following the list literally can leave its Run
UI stuck active and fail to show the bounded relay failure. The error remains
scoped to a parent-owned `run_id` and grants no new authority, so severity is
Low. Handle `run.error` under the same owned-run check and clear active state
on either exit or error.

## SECOND CLOSING ADDENDUM — fix commit `c148e6b` (cycle 2 of 10)

Fresh review of the exact `d2f97f4..c148e6b` diff found no new Critical,
High, Medium, Low, Info, or other finding. The change remains confined to the
active generated guidance and its verifier anchors.

### A3 — resolved

The brief now accepts `run.error` only under the same page-owned `run_id` check
as output and exit, and explicitly treats either `run.exit` or `run.error` as
terminal for the active UI state. Sequence progression remains limited to the
output/exit messages that carry a cursor. A malformed, oversized, or prematurely
closed relay therefore becomes visible text and releases the local Run controls
without inventing output, a terminal cause, or cross-run ownership.

## Prior-condition reconciliation at the closing tree

- **Generated brief writer and teaching/data-boundary protections — REMAIN
  RESOLVED.** The constant writer, atomic publication, symlink/special-file
  replacement, immutable manifest identity, unknown-field preservation,
  untrusted-data boundary, learner-artifact ownership, and no-symlink rules are
  unchanged. The activated guidance reinforces rather than weakens text-only
  treatment of artifact and run data.
- **F4 generated-pedagogy activation condition — RESOLVED.** The prior
  frontend report left the shipped brief intentionally inactive pending this
  separate F5 slice. The reviewed text now activates only the already-landed
  manifest/editor/run contract, with exact capability and envelope guidance;
  no other pedagogy section changes.
- **Bridge-conventions B1/B2/N1 — REMAIN RESOLVED.** Exact parent/origin and
  envelope authentication, one-result consumption, fresh lesson-wide request
  ids, and correct reject handling remain in the unchanged general section.
  The new operation examples use only that authenticated port and explicitly
  reject literal placeholder ids.
- **Bridge-runtime D5 L1 — REMAINS MITIGATED; D5 L2/L3 — REMAIN RESOLVED.**
  The same-`WindowProxy` document-generation residual remains documented.
  Content-bound serving, fresh parent/server operation checks, private-read
  consent, and bounded identity/cache rules are unchanged.
- **Artifact A1 and the artifact write boundary — REMAIN RESOLVED.** The brief
  preserves app-owned save authority and agent read-only ownership. Descriptor
  stability, no-follow traversal, bounded UTF-8 reads/writes, conflict checks,
  and rate/event behavior are unchanged. The same-user final-publication window
  remains **OPEN/ACCEPTED by design** and is not widened.
- **Runner-core, run-API, and F4 frontend findings — REMAIN RESOLVED.** Fixed
  registry templates, immutable snapshots, namespace/private-root isolation,
  resource bounds, health single-flight, admission/retention, SSE replay,
  owned cancellation, private-read consent, and text-only relay are unchanged.
  The earlier runner/client activation conditions are now **RESOLVED for the
  generated brief**.
- **Lesson-role, terminal, and deployment protections — REMAIN RESOLVED at
  their prior dispositions.** Agent/learner/runner sandboxes and private masks
  are unchanged. Terminal-opt-in remains resolved for lesson roles and
  **OPEN/ACCEPTED** only for the deliberately plain owner shell. The
  direct/no-forwarded-header condition remains **MITIGATED** by the documented
  direct-loopback deployment.
- **D4 A1/A2 — REMAIN OPEN/ACCEPTED Low availability follow-ups.** This text
  slice changes neither attempt buffering nor projection reconciliation and
  does not claim to resolve them.

## Closing verification

- Both fix cycles' `git diff --check` and
  `python -m py_compile app/services/lessons.py verify.py` — passed.
- Cycle-1 and cycle-2 `python verify.py` — **756 passed, 0 failed** each,
  including the independent-grant, minimum-envelope, revision, run-ownership,
  relay-error, terminal-state, and text-only guidance anchors.
- Cycle-1 and cycle-2 `python verify_restore.py` — **28 passed, 0 failed** each.
- PR #72's exact application head `c148e6b` had both CI checks green and
  received the review bot's clean `+1` at 2026-07-23 00:26:04 UTC before this
  report and Done bookkeeping were committed.
- `python scripts/check_public_hygiene.py` — passed. Ignored-status inspection
  showed only established local tool, environment, cache, screenshot-reference,
  and review-work paths.

## Closing verdict

**SAFE TO MAKE LIVE for the documented direct-loopback `127.0.0.1:8765`,
single-worker, unauthenticated single-user deployment.** The review found
**3 Low, 0 Critical, 0 High, 0 Medium, and 0 Info** findings; all were resolved
in **2 of 10** cycles, and no open finding remains for this queue entry. The
activated generated guidance is self-contained at its mounted boundary,
preserves independent editor/run degradation, learner artifact ownership,
authenticated bridge operations, fixed runner authority, and text-only data
handling. Wider, proxy-adjacent, or multi-user deployment remains **NO**. The
queue entry may move to Done. A live restart remains owner-only and was not
performed.

## REOPENING ADDENDUM — exact-head reviews `fa13d5c` and `a0e694f`

The closing verdict immediately above is **SUPERSEDED for the current tree**.
The first Done-bookkeeping head `fa13d5c` received two fresh instruction
findings, both fixed in cycle 3 as `a0e694f`; exact-head review of
that fix found three further instruction details plus the stale report verdict
itself. The queue is Pending again. No version of this report may be used as a
restart approval until a later superseding closing verdict explicitly closes
this addendum on a reviewed exact head.

### A4 — Request-scoped bridge errors were not given UI/state handling (Low, confirmed; resolved in cycle 3)

The cycle-2 text matched success replies and asynchronous run messages but did
not say that Load, Save, Save/Run, and Cancel can instead answer with
`op: "error"` and the same `request_id`. Generated pages could leave an
operation pending or silently hide conflict, consent denial, admission, and
degradation states. The parent/backend still failed closed, so no authority
was gained.

Cycle 3 adds the frozen error envelope, request-id matching, text-only code
display, and per-request pending-state release. Its first revision rule was
deliberately conservative but incomplete; A8 below tightens mutating failures.

### A5 — Runner pages requested unused `attempts` authority (Low, confirmed; resolved in cycle 3)

The runner-backed concrete ready message always requested `attempts`, even on
a page that did not record declared answers. Because capability negotiation is
independent, that unnecessarily granted the page an attempt channel for other
declared questions on the armed page. Server-side question identity still
bounded writes, but the grant violated least privilege and could let unrelated
page script produce misleading records.

Cycle 3 makes the concrete runner list `editor` + `run` and adds `attempts` to
that one announcement only when the page actually records declared answers.
The editor-only instruction uses the same condition.

### A6 — `job-missing` Cancel cannot wait for a later relay event (Low, confirmed; resolved in cycle 4)

Cycle 3 initially told every failed Cancel to retain active state until
`run.exit` or `run.error`. For `job-missing`, however, the parent removes the
owned run before replying, and the relay's ownership gate then prevents any
later asynchronous message. The generated controls would stay stuck forever.

Cycle 4 distinguishes that terminal code: `job-missing` clears local active-run
state, while other Cancel errors release only the request-pending state and
retain the owned run until its actual asynchronous terminal message.

### A7 — The report claimed closure while the queue was Pending (Low, confirmed; resolved for the interim state)

The first closeout commit reopened the queue but left the earlier three-finding
SAFE verdict as the report's last word. That contradicted the repository's
deployment gate and made the document reusable as stale approval. This
reopening addendum explicitly supersedes that verdict and states the only
current posture: Pending and not restart-approved. A final SAFE verdict remains
for a later exact-head-reviewed closeout commit.

### A8 — Mutating errors can leave the child revision unknown (Low, confirmed; resolved in cycle 4)

Cycle 3 told every request error to preserve the last `base_rev`. A Save/Run can
publish the artifact successfully and then fail its fresh Run check or start;
an ordinary Save can likewise have a durable file before a later visible
failure. Reusing the old revision guarantees a conflict despite matching
textarea bytes.

Cycle 4 preserves the known revision only after a Load error. Any Save or
Save/Run error keeps the learner's textarea but marks the revision unknown and
requires a successful Load before the next mutation. This is safe for both
pre-write refusals and post-write failures and never guesses server state.

### A9 — Runner examples omitted the closed-stdin contract (Low, confirmed; resolved in cycle 4)

The runner launches every program with `stdin=subprocess.DEVNULL`; a generated
Python `input()` or Go `os.Stdin` exercise therefore sees EOF or fails instead
of accepting learner input. The previous single-file/dependency-free wording
did not expose this execution boundary.

Cycle 4 now calls both runners non-interactive, forbids standard-input examples,
and directs fixed invented input into the program while retaining the terminal
for experiments that need learner interaction.

## Reopened verification and interim verdict

- Cycle-3 host `python verify.py` — **756 passed, 0 failed**.
- Cycle-3 host `python verify_restore.py` — **28 passed, 0 failed**.
- Cycle-3 `git diff --check`, one-template-hunk assertion, Python compile, and
  public hygiene — passed.
- Cycle-4 host `python verify.py` — **756 passed, 0 failed**.
- Cycle-4 host `python verify_restore.py` — **28 passed, 0 failed**.
- Cycle-4 `git diff --check`, one-template-hunk assertion, Python compile, and
  public hygiene — passed. These changes are not closed until their own
  exact-head review completes.

**NOT YET SAFE TO MAKE LIVE.** The current drain has raised **9 Low, 0
Critical, 0 High, 0 Medium, and 0 Info** findings across four cycles; the text
changes for all nine are present, but cycle 4 and its later closeout have not
yet passed the exact-head review gate. `docs/reviews/QUEUE.md` therefore remains
Pending. Wider, proxy-adjacent, or multi-user deployment remains **NO**, and a
live restart remains owner-only and was not performed.

## FINAL SUPERSEDING ADDENDUM — fix commit `418615f` (cycle 4 of 10)

Fresh review of the exact `a0e694f..418615f` diff found no new Critical, High,
Medium, Low, Info, or other finding. PR #72's exact application head
`418615f95fac14b4684296625828d04eb8f17ced` had its CI workflow green and
received the review bot's clean `+1` at 2026-07-23 01:08:22 UTC. The change
remains limited to the active generated guidance, its verifier anchors, and
the report/queue state that explicitly kept the drain Pending while open.

### A6–A9 — resolved and re-verified

The exact reviewed text now treats only Cancel `job-missing` as locally
terminal, requires a Load after any mutating request error before another
Save/Run, states the runners' closed-stdin contract, and leaves no stale SAFE
verdict after a Pending reopen. The parent/backend remain fail closed and
unchanged; these rules align generated UI state with the already-reviewed
runtime rather than adding authority.

## Final closing verification

- Cycle-4 `python verify.py` — **756 passed, 0 failed**.
- Cycle-4 `python verify_restore.py` — **28 passed, 0 failed**.
- Cycle-4 `git diff --check`, one-template-hunk assertion,
  `python -m py_compile app/services/lessons.py verify.py`, and public hygiene
  — passed.
- Exact application head `418615f`: GitHub CI — green; review bot — clean
  `+1`; no new inline finding.
- The final report/Done bookkeeping changes no application, schema, ABI,
  sandbox, CSP, route, static, terminal, or listener behavior.

## FINAL SUPERSEDING VERDICT

**SAFE TO MAKE LIVE for the documented direct-loopback `127.0.0.1:8765`,
single-worker, unauthenticated single-user deployment.** Across the complete
drain, **9 Low, 0 Critical, 0 High, 0 Medium, and 0 Info** findings were raised;
all nine were resolved in **4 of 10** cycles, and no open finding remains for
this queue entry. The generated editor/run guidance is self-contained,
least-privilege, revision-safe after ambiguous mutations, terminal-state-safe,
non-interactive where the runner is non-interactive, and still bound to the
reviewed parent/backend authorities. All prior-condition dispositions in this
report remain unchanged except that F4's generated-pedagogy activation
condition is resolved. Wider, proxy-adjacent, or multi-user deployment remains
**NO**. The queue entry may move to Done. A live restart remains owner-only and
was not performed.
