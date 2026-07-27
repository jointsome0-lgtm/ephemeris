# Platform support review

**Date:** 2026-07-27

## Self-parameterization

- **Scope:** exactly one Pending entry existed at review start: the 2026-07-26
  platform-support entry covering the five commits in
  `4dea680..95593ba`. The change landed through merge commit `a2aa560`, whose
  tree `787c430c74627b1217736e16e13822a753da947b` is byte-identical to
  reviewed branch head `95593ba`. The review covered `app/terminal.py`,
  `app/sandbox.py`, `app/services/attempts.py`, `app/db.py`, `app/main.py`,
  `app/services/focus.py`, `app/services/stats.py`, `verify.py`, `README.md`,
  and queue bookkeeping, plus the terminal registration/WebSocket/session
  paths, sandbox and runner spawn paths, attempt HTTP/projection callers, and
  date-rendering consumers as direct context.
- **Starting HEAD:** `08bffd3e05570b486ff0ca13d9ae22a9c8c530ac`
  on clean `main`, equal to `origin/main`. The only differences from reviewed
  application head `95593ba` were later `AGENTS.md` and queue-documentation
  edits; the application and verifier trees remained the reviewed bytes.
- **Fix target:** `a2aa560` is an ancestor of `main`, so the entry is merged
  and any drain fix would go directly on `main`. No drain fix was required.
- **Report file:**
  `docs/reviews/2026-07-27-platform-support-review.md`.
- **Prior reports reconciled:** every existing closing verdict was scanned.
  The binding reports for this surface were the terminal opt-in, lesson-agent
  and lesson-learner sandbox, terminal-surfaces, runner-core, lesson-run API,
  editor/run frontend, blocks-activation, attempt-backend, attempt projection,
  and check-activation reports. Their current dispositions are stated below.
- **Validation baseline:** approved host `python verify.py` —
  **762 passed, 0 failed**; approved host `python verify_restore.py` —
  **28 passed, 0 failed**. Initial nested-sandbox runs stalled at the known
  TestClient/bubblewrap boundary and were interrupted without assertion
  output; only the completed host runs supply these counts.

## Context and method

The deployment decision assumes the documented unauthenticated,
single-worker application bound directly to `127.0.0.1:8765`, with no
forwarded-header rewriting. No service was restarted or signalled, and no
live database, bundle, export, browser state, credential, or private runtime
path was read or changed.

The five commits and complete changed production modules were read at the
landed tree, along with the application import graph and direct callers.
Static review traced default-off and opted-in startup, partial and failed
imports, cache publication, concurrent calls, every fork/pre-exec path, PTY
allocation and fd cleanup, sandbox runtime refusal, rlimit application,
snapshot sealing, projection-lock acquisition and cleanup, post-commit
projection failures, retry semantics, locale/date fidelity, terminal trust
gates, and the public platform statement. Tests and PR commentary were treated
as claims until reproduced or established from current source.

PR #79's earlier P1 finding was not re-litigated as a fresh drain finding. It
was checked at final head: `95593ba` converts missing `fcntl` into `OSError`
inside the projection lock, so both existing narrow callers return
projection-pending after the authority commit. The full verifier independently
exercised the unavailable-lock context manager and both callers.

Issue #25 selected the lazy-import option so the default no-terminal
application can import without Unix PTY modules. This review's portability
verdict is limited to that startup/import contract, the explicit terminal
refusal, and the portable date output. It does not reinterpret the existing
Linux-specific bubblewrap/systemd runner as cross-platform execution support.

## Findings

No Critical, High, Medium, Low, Info, or other finding was found.

## Confirmed protections

- `app.main` can import with `fcntl`, `pty`, `termios`, and `resource`
  unavailable while the terminal is off. No module-level import of those
  modules remains under `app/`, and the public terminal helper names remain
  callable.
- Opting the terminal in resolves the PTY trio during startup and raises the
  dedicated unsupported-platform error before route registration when it is
  absent. The message names both the missing capability and
  `EPHEMERIS_ENABLE_TERMINAL`; the default-off path pays none of that cost.
- `_pty_stack()` publishes only a complete `(fcntl, pty, termios)` tuple.
  `_create_session()` resolves it before `pty.openpty()` and either subprocess
  fork, while `setup_terminal()` also warms it at startup. `_child_setup()`
  therefore reads the parent-populated tuple between fork and exec rather than
  performing a first import there.
- `profile_preexec_fn()` resolves `resource` in the parent before returning
  its child callback. Rlimit names remain resolved only at the point of use,
  and the agent, learner, and runner limits, profile selection, mount argv,
  runtime probes, and fail-closed spawn behavior are unchanged.
- Runner snapshot sealing imports `fcntl` only after a Linux memfd with
  sealing support exists. This changes neither the descriptor authority nor
  the close-on-failure path.
- Missing `fcntl.flock` is classified as an unavailable projection lock.
  `_project_attempt()` and `reconcile_projection()` retain their
  `(OSError, sqlite3.Error)` boundary and return `False`; a committed attempt
  therefore answers successfully with `projection: "pending"` instead of
  producing a 500 over durable authority. Lock fd validation, nonblocking
  exclusive acquisition, unlock, and close ordering are unchanged.
- `pretty_date()` leaves month, weekday, and year rendering with `strftime`
  and renders only the unpadded numeric day directly. All seven former
  `%-d` consumers use the helper, the literal single-digit forms pass, and the
  current glibc host confirms byte identity with every replaced spelling.
- The terminal's opt-in predicate, loopback peer gate, exact Host/Origin
  checks, pre-accept rejection, no-forwarded-header requirement, session
  ownership, attach/reaper serialization, and shutdown paths are unchanged.
- Merge commit `a2aa560` and reviewed head `95593ba` have the same tree.
  Current `main` changes only `AGENTS.md` and `docs/reviews/QUEUE.md` relative
  to that head, so the validated application bytes are the landed bytes.

## Prior-condition reconciliation

- **Terminal F1-F4, terminal-tab L1, opt-in wiring, workspace refusal,
  relative-path display, PTY/fd ownership, and atomic brief publication —
  REMAIN RESOLVED.** The lazy imports move availability checks without moving
  or weakening any WebSocket, workspace, session, or filesystem authority
  gate.
- **Terminal-opt-in T1 — REMAINS RESOLVED for `lesson-agent` and
  `lesson-learner`; REMAINS OPEN/ACCEPTED for the deliberately plain owner
  shell.** Both lesson roles retain private PID namespaces and their existing
  profiles. The plain same-UID shell is not represented as isolated.
- **Direct/no-forwarded-header condition — REMAINS MITIGATED by the documented
  direct-loopback deployment.** The peer, Host, and Origin checks are
  unchanged; proxy-adjacent deployment remains excluded.
- **E1 S1, lesson-agent/learner sandbox authority, and role separation —
  REMAIN RESOLVED.** The app-owned lesson root, strict-child validation,
  namespace/mount order, no-network learner, and fail-closed no-fallback
  behavior are unchanged. Agent host networking, CLI login material,
  `SSH_AUTH_SOCK`, and proxy credentials remain
  **UNCHANGED/ACCEPTED for the trusted agent role only**.
- **Runner core, run API/lifecycle, editor/run membrane, and generated
  activation protections — REMAIN RESOLVED for the documented deployment.**
  Localizing `resource` and snapshot `fcntl` imports changes no registry,
  command, bound, route, ownership, output, or cancellation contract.
- **Attempt-backend A1/A2 and attempt authority protections — REMAIN
  RESOLVED.** Request streaming caps, row/event atomicity, replay/conflict
  behavior, record-time validation, projection bounds, cursor/seal checks, and
  cross-process exclusion are unchanged. The missing-lock fallback now
  preserves the already-documented projection-pending contract.
- **Bridge-runtime D5 L1 — REMAINS MITIGATED; D5 L2/L3 — REMAIN RESOLVED.**
  This entry changes no iframe, bridge, page-byte, identity, or operation
  admission path.

## Closing verification

- `git diff --check 4dea680..95593ba` — passed.
- Reachability/tree proof — `a2aa560` is an ancestor of starting `main`, and
  `a2aa560^{tree}` equals `95593ba^{tree}` at
  `787c430c74627b1217736e16e13822a753da947b`.
- Current-tree proof — `git diff --name-status 95593ba..08bffd3` lists only
  `AGENTS.md` and `docs/reviews/QUEUE.md`.
- Approved host `python verify.py` — **762 passed, 0 failed**, including the
  no-PTY import/startup probes, portable-date checks, missing-`fcntl`
  projection fallback, terminal trust/lifecycle checks, sandbox profiles, and
  real runner isolation matrix.
- Approved host `python verify_restore.py` — **28 passed, 0 failed**.
- No drain fix commit was required; this drain consumed **0 of 10** cycles.

## Closing verdict

**SAFE TO MAKE LIVE for the documented direct-loopback `127.0.0.1:8765`,
single-worker, unauthenticated single-user deployment.** This entry has **0
Critical, 0 High, 0 Medium, 0 Low, and 0 Info** findings and no open finding.
The earlier PR P1 is resolved by `95593ba`; the terminal, sandbox, runner,
attempt, bridge, and data-boundary conditions retain the explicit dispositions
above. Wider, proxy-adjacent, or multi-user deployment remains **NO**. The
platform-support queue entry may move to Done, making Pending empty. A live
restart remains owner-only and was not performed.
