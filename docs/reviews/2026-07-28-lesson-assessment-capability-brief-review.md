# Lesson assessment capability and tutor brief — adversarial security review

**Scope:** exactly one Pending entry was present at review start: the
2026-07-28 issue-#4 phase-S s3 entry for `f40bc2f`, `76b2021`, `3706562`,
`419ccbc`, and `2cef3b4` on `fix/4-s3-capability-brief`, landed by ordinary
merge commit `42eabf4` (PR #90). The listed surface is `app/terminal.py`,
`app/services/assessments.py`, `app/services/lessons.py`, `app/main.py`,
`docs/lesson-assessments-api.md`, `verify.py`, and
`docs/reviews/QUEUE.md`, plus the terminal trust/session/sandbox, proxy,
lesson-workspace, assessment-authority/projection, central request-perimeter,
SQLite, generated-brief, and application-lifespan direct callers.

**Starting HEAD:** `f2286561dfd1c3525d36a4b0df271742167b05e1` on
`main`, matching refreshed `origin/main`. `git status --short --branch`
showed only `## main...origin/main`; ignored-status inspection showed only
established local tool, virtual-environment, dependency, cache,
screenshot-reference, and review-work paths. The starting worktree was clean.

**Reviewed tree:** every listed branch commit and merge commit `42eabf4` is
an ancestor of the starting HEAD. `42eabf4^{tree}` and the reviewed branch
head `2cef3b4^{tree}` are byte-identical at
`b953ef19bbc09fb3e9d091761fc6358cd24767e2`. The only change from
`2cef3b4` to the starting HEAD is `docs/reviews/QUEUE.md`, which pins the
merge and tree identity; the application tree under review is therefore the
landed application tree. No listed entry is out of scope.

**Report file:**
`docs/reviews/2026-07-28-lesson-assessment-capability-brief-review.md`,
derived from the entry's assessment-capability and matching-brief subject.

**Prior reports to reconcile:** every existing
`docs/reviews/*-review.md` closing verdict was scanned. The binding conditions
for this surface are:

- `2026-07-28-lesson-assessment-authority-review.md`: central write-guard W1,
  strict bounded admission, UTF-8-safe refusals, same-lesson references,
  fingerprint idempotency, archive ordering, row/event atomicity, and the
  active-state fold are resolved. Origin-less non-browser writes remain
  accepted only inside the documented direct-loopback posture.
- `2026-07-28-lesson-assessments-projection-review.md`: the projection has an
  explicit compaction-without-a-fixed-cap contract. Unchanged replays and
  terminal opens do not refold it, cached success uses the complete metadata
  seal, and projection/publication protections are resolved. New concepts and
  reviewed attempts may still grow the active fold without a ceiling.
- `2026-07-16-lesson-brief-teaching-contract-review.md` and its addenda:
  learner/source/record content remains data rather than instructions;
  bundle-path, no-symlink, bounded-discovery, unknown-field, and current
  brief/spec consistency findings are resolved. Atomic generated-brief
  publication remains resolved.
- The terminal, workspace, sandbox, and platform reports: terminal F1-F4,
  terminal-tab L1, opt-in wiring, server-owned role/workspace selection,
  PTY/fd ownership, attach/reaper serialization, fail-closed sandboxing, and
  portable default-off startup are resolved. Terminal-opt-in T1 remains
  open/accepted only for the deliberately plain owner shell. The trusted
  lesson-agent's intentional host network, CLI-login, `SSH_AUTH_SOCK`, proxy,
  and writable-bundle posture remains accepted only inside the documented
  single-user deployment.
- Attempt A1/A2 remain resolved. Bridge-runtime D5 L1 remains mitigated and
  D5 L2/L3 remain resolved; artifact, runner, run-API, editor/run membrane,
  generated-pedagogy, bundle, lesson-role, public-data, and recovery
  conditions retain their prior dispositions.

The closing verdict states explicitly whether these conditions remain
resolved, mitigated, open/accepted, or unchanged for the reviewed code.

**Validation baseline:** approved host runs at the clean starting HEAD passed:
`python verify.py` — **849 passed, 0 failed**; `python verify_restore.py` —
**28 passed, 0 failed**.

## Context and method

The deployment decision assumes the documented unauthenticated,
direct-loopback `127.0.0.1:8765`, single-user, single-worker app with no
forwarded-header rewriting. No service was restarted or signalled, and no live
database, lesson bundle, export, browser profile, screenshot, credential, or
private runtime path was read or changed. The focused executable probe used
invented in-memory records and an unused `/tmp` configuration path.

The complete landed diff and the changed production functions, generated
brief, API contract, and verifier sections were read with their direct callers.
Static review traced the pre-accept peer/Host/Origin gate, ASGI server-address
derivation, token entropy and registry ownership, child-environment
allowlisting, proxy/no-proxy composition, lesson identity resolution, PTY
allocation and spawn failures, session close/reap/shutdown, deferred
capability lookup, body admission, lesson mismatch, replay/conflict ordering,
rate accounting, transaction boundaries, one-summary-per-sitting enforcement,
active-state and projection behavior, prompt-data boundaries, and degradation
instructions. Queue prose, tests, commit messages, and earlier review summaries
were treated as claims to verify against the current source.

## Findings (severity-ranked)

### L1 — The generated tutor brief requires an unbounded read of a projection with no fixed ceiling (Low, confirmed; owner decision required)

The new first-read instruction tells every lesson agent to read
`assessments.jsonl` “whole” because current-state compaction means “it stays
small” (`app/services/lessons.py:962-965`). The exact verifier makes that
unbounded instruction a regression requirement by pinning
`Read assessments.jsonl next, whole` (`verify.py:528-537`).

That claim contradicts the immediately preceding projection drain and the
current repository contract. The implementation says explicitly that the file
is a compaction, not a cap: each new concept or reviewed attempt may remain in
the active fold, so the file keeps growing and every complete render is linear
in that fold (`app/services/assessments.py:846-858`). The bundle spec likewise
states that the projection “has no fixed ceiling” and deliberately rejects
truncating current tutor state (`docs/learn-bundle-spec.md:440-449`).

A focused current-code probe rendered invented active evidence records carrying
the allowed 8-KiB note size. It produced:

| active records | rendered bytes |
|---:|---:|
| 300 | 2,567,045 |
| 1,000 | 8,556,948 |
| 5,000 | 42,792,948 |

Those are valid current-state records rather than discarded history. The
30-per-60-second write damper can reach 5,000 distinct active records in about
2 hours 47 minutes, as the preceding projection report already established.
A tutor following the new brief can therefore push tens of MiB into one tool
result/model context, exhaust its usable context or process memory, or receive
a silently truncated tool result while believing it read the complete resume
state. This is a concrete lesson-agent availability and correctness failure in
the supported deployment, not wider-network hardening.

The finding is Low rather than Medium: only the trusted same-machine
lesson-agent/owner path can grow the file, SQLite remains authoritative, the
app's projection work retains its resolved guards, and no privilege,
confidentiality, or durable-data boundary is bypassed. Wider unauthenticated
deployment remains prohibited independently.

**Fix direction:** the separate repair PR must remove the false small-file and
whole-read requirement and define an explicitly bounded consumption behavior
for the generated tutor. It must state honestly when current judgments were
omitted and preserve the data-not-instructions rule. Because the projection's
no-truncation behavior was an explicit owner decision, the repair may not
silently choose which current records to discard; if bounded chunking cannot
retain the required resume semantics, the owner must choose a bounded query or
summary contract. Update the exact verifier so it pins the chosen byte/context
bound and omission behavior instead of pinning the unbounded read.

No Critical, High, Medium, Info, or other finding was found.

## Confirmed protections and rebutted candidates

- The assessment token is generated with `secrets.token_urlsafe(32)`, registered
  only for a server-selected `lesson-agent`, and injected with exactly one
  complete assessment URL into that session's allowlisted child environment.
  Plain and learner shells receive neither value, and service-level
  `EPHEMERIS_` values cannot enter through a broad prefix.
- The URL comes from the accepted socket's ASGI `server` address, never the
  client-supplied Host header. The documented wildcard bind falls back to
  `127.0.0.1`; absent/unspellable server data mints nothing. Inherited and
  explicit proxy branches retain their configured values while adding both
  `NO_PROXY` spellings for `localhost`, `127.0.0.1`, and `::1`.
- The token is published before spawn so a shell startup process cannot observe
  a false dead capability. Failed or refused spawn paths revoke it, a created
  `_TermSession` owns it, and close, idle reap, kill, and application shutdown
  all remove it. The accessor returns a copy rather than mutable registry
  state.
- The endpoint reads only `X-Ephemeris-Assess-Token`; the body cannot claim a
  lesson, sitting, question, sequence, or event identity. A live same-lesson
  token stamps the session SID; a foreign lesson is a distinct 409 and an
  unknown, blank, revoked, or prior-process token is a distinct 403. An absent
  token remains the documented owner/manual path and is not represented as
  authenticated.
- Capability resolution precedes projection, replay, rate, and write work, so
  a dead token cannot degrade silently into an anonymous write or receive a
  quiet duplicate. The capability is provenance rather than authentication;
  the direct-loopback write perimeter remains the actual deployment boundary.
- The one-active-summary rule is checked under `BEGIN IMMEDIATE` and the
  process-local per-lesson lock. A second active summary in one sitting must
  supersede the current row, while another sitting and the tokenless owner path
  retain their documented behavior. Same-lesson `supersedes` validation and
  row/event atomicity remain intact.
- Splitting `_spawn_on_pty()` out of `_create_session()` preserves the existing
  PTY fd cleanup, mandatory sandbox/no-fallback behavior, role/workspace
  creation-time identity, capacity serialization, output pump, attach
  ownership, reaper, and shutdown paths. No listener, WebSocket protocol, or
  sandbox profile changed.
- The generated brief keeps the token out of pages, artifacts, and lesson
  files; teaches one fresh idempotency key per verdict and same-key retry;
  distinguishes refused from unanswered writes; keeps assessment, learner,
  and earlier-note content as data rather than instructions; and leaves
  `studied` as the owner's manual decision. L1 is the bounded-read exception,
  not a broader prompt-authority regression.

## Verification

- Refreshed remote proof — starting `main` equals `origin/main` at
  `f2286561dfd1c3525d36a4b0df271742167b05e1`.
- Reachability/tree proof — all listed commits and merge commit `42eabf4` are
  ancestors of starting `main`; `42eabf4^{tree}` equals `2cef3b4^{tree}` at
  `b953ef19bbc09fb3e9d091761fc6358cd24767e2`.
- Current-tree proof — `git diff --name-status 2cef3b4..f228656` lists only
  `docs/reviews/QUEUE.md`.
- `git diff --check 71852d3..2cef3b4` — passed.
- Starting-head approved host `python verify.py` — **849 passed, 0 failed**.
- Starting-head approved host `python verify_restore.py` — **28 passed,
  0 failed**.
- Focused invented-record render probe — confirmed L1 at 300, 1,000, and
  5,000 active records without reading or writing runtime lesson data.
- No repair was attempted and no application, test, or specification file was
  changed by this diagnosis-only drain.

## Prior-condition reconciliation at the reviewed application tree

- **Assessment authority and central write-guard W1 — REMAIN RESOLVED.**
  Both aliases retain the bounded streaming reader, UTF-8-safe refusal
  boundary, same-lesson authority, fingerprint idempotency, archive ordering,
  row/event transaction, and active-state fold. Origin-less non-browser access
  and the direct/no-forwarded-header condition remain **OPEN/ACCEPTED only for
  the documented direct-loopback deployment**.
- **Assessment projection publication, cache honesty, and replay/terminal-open
  cost guards — REMAIN RESOLVED.** The explicit
  compaction-without-a-fixed-cap contract remains unchanged. L1 is a new open
  consumer-guidance defect: it does not reopen the fixed projection writer,
  cache, lock, descriptor, collision, or response-honesty findings.
- **Generated-brief data authority, path/no-symlink rules, bounded artifact
  discovery, unknown-field preservation, and atomic publication — REMAIN
  RESOLVED.** The new assessment and earlier-note paragraphs retain the
  data-not-instructions boundary. L1 is the one open generated-brief condition:
  the assessment projection itself is newly assigned no read bound.
- **Terminal F1-F4, terminal-tab L1, opt-in wiring, workspace refusal,
  PTY/fd ownership, attach/reaper serialization, and platform startup —
  REMAIN RESOLVED.** Terminal-opt-in T1 remains **OPEN/ACCEPTED only for the
  deliberately plain owner shell**. The trusted lesson-agent's intentional
  host-network, credential, proxy, and writable-bundle posture remains
  **UNCHANGED/ACCEPTED for that role only**.
- **E1/E2/E3 sandbox authority and server-owned role separation — REMAIN
  RESOLVED.** Agent and learner profiles, namespace/mount order, learner
  no-network posture, private masks, and fail-closed no-fallback behavior are
  unchanged. The new capability is injected only after the server has selected
  the trusted agent role and resolved the app-owned lesson identity.
- **Attempt A1/A2 — REMAIN RESOLVED. Bridge-runtime D5 L1 — REMAINS MITIGATED;
  D5 L2/L3 — REMAIN RESOLVED.** The attempt endpoint/projection, iframe,
  page-byte identity, bridge handshake, artifact/editor/run membrane, runner,
  and operation-admission paths are unchanged.
- **Bundle, public-data, and recovery posture — REMAIN RESOLVED/UNCHANGED.**
  No real lesson, assessment, database row, export, screenshot, browser state,
  credential, or private instance path entered the repository. Assessment
  authority remains private SQLite plus its event; typed recovery still
  requires a consistent SQLite backup. Wider, proxy-adjacent, or multi-user
  deployment remains unsupported.

## Closing verdict

**NOT YET SAFE TO MAKE LIVE for the phase-S s3 assessment-capability and tutor-
brief batch.** The review found **1 Low, 0 Critical, 0 High, 0 Medium, and
0 Info** findings. The capability, terminal lifecycle, request authority,
sitting provenance, summary enforcement, and degradation paths are sound for
the documented direct-loopback `127.0.0.1:8765`, single-worker,
unauthenticated single-user deployment, but L1 directs every new tutor to make
an unbounded whole-file read of a projection the repository explicitly permits
to grow without a ceiling.

The queue entry remains **Pending** and points to this report. A separate
ordinary reviewed repair PR must define and test bounded, honest consumption
of `assessments.jsonl` without silently reversing the owner-approved
no-truncation projection contract; it must preserve or grow the **849 / 28**
validation baselines. Wider, proxy-adjacent, or multi-user deployment remains
**NO** independently. A live restart remains owner-only and was not performed.
