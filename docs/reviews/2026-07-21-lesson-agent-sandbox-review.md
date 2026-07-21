# E2 lesson-agent sandbox integration — adversarial security review

**Scope:** exactly one Pending entry was present at review start: the 2026-07-21
entry for `530556f..HEAD` on `fix/16-e2-lesson-agent-sandbox`, covering
`app/terminal.py`, `app/sandbox.py`, and `verify.py`, plus their direct callers.
The range contains one scoped implementation commit, `de51c00`.

**Starting HEAD:** `de51c00f0ee92b90fc786b6a74c8b37638e810fe` on
`fix/16-e2-lesson-agent-sandbox`. `git status --porcelain=v1
--untracked-files=all` was empty before review work, so the tracked worktree was
clean. The ignored local tooling, dependency, cache, screenshot-reference, and
review-work directories shown by `git status --short --ignored` were not
tracked or used as review inputs.

**Fix target:** `main` and `origin/main` both pointed to `530556f`; `main` was
an ancestor of starting HEAD, while starting HEAD was not an ancestor of
`main`. The entry is therefore a not-yet-merged branch drain. Any drain fixes
would belong on `fix/16-e2-lesson-agent-sandbox`, not `main`. That conclusion
was correct at review start; the PR merged while the drain was in progress, and
the resulting artifact-landing race is reconciled below.

**Report file:**
`docs/reviews/2026-07-21-lesson-agent-sandbox-review.md`, derived from the
entry's “lesson-agent sandbox” subject.

**Prior reports to reconcile:** every existing `docs/reviews/*-review.md`
closing verdict was scanned. The most recent binding report for this phase is
`2026-07-21-sandbox-launcher-review.md`: E1 S1 was resolved; E2 had to supply
the app-owned, filesystem-validated bundle root; terminal-opt-in T1 remained
open for the live lesson shell until E2 applied the private PID namespace; the
agent's host network and read-only CLI credentials remained deliberate narrow-
deployment posture. `2026-07-16-terminal-optin-review.md` additionally binds
the fail-closed workspace and child-environment claims, the accepted plain-
shell same-UID residual, and deliberate `SSH_AUTH_SOCK`/proxy access.
`2026-07-16-terminal-tab-scoping-review.md` named an off-Learn implicit lesson-
tab connection Low, and the earlier terminal reports bind F1–F4 plus the
direct/no-forwarded-header launch condition. The lesson-terminal and brief-
writer reports bind slug containment, symlink refusal, and atomic generated-
brief publication. Their current-head dispositions are stated explicitly
below.

**Validation baseline:** at starting HEAD, the required host runs passed:
`python verify.py` — **614 passed, 0 failed**; `python verify_restore.py` —
**28 passed, 0 failed**. Initial runs inside the reviewer's nested sandbox
reproduced the preceding E1 report's silent TestClient/bubblewrap stall and
were interrupted without assertion output; the recorded counts are from the
approved host reruns of the exact commands.

## Context and method

The deployment decision assumes an unauthenticated, single-user, single-worker
app bound directly to `127.0.0.1:8765`. No service was restarted or signalled,
and no live database, bundle, export, browser profile, screenshot, or
authenticated state was read or written.

The complete scoped diff and files were read with the workspace-preparation,
WebSocket attach, PTY lifecycle, application lifespan, client tab-selection,
and E1 launcher call paths. The live owner statement on issue #16 was checked
read-only: real lesson-agent isolation is the remaining issue scope after the
opt-in/workspace/env slice. Static review covered server-owned role selection,
duplicate/empty/junk lesson parameters, lexical and filesystem bundle
authority, mount ordering, PID/network namespaces, environment and credential
exposure, PTY fd/process cleanup, sandbox-init failure, capacity and attach
races, session identity, handshake behavior, and plain-session preservation.

Two host checks used only invented throwaway directories. The existing E1
three-profile probe confirmed repository masking, blank-home mounts, bundle
mode/cwd, and network policy. A dedicated real E2 PTY probe created an invented
lesson in a temporary private data directory, launched it through
`_create_session()`, and confirmed the `lesson-agent` role/profile, bundle cwd,
hidden repository, private `/proc` with no parent-environment canary, and clean
session shutdown.

## Findings (severity-ranked)

No Critical, High, Medium, Low, or Info finding.

## Confirmed protections

- A `lesson` parameter is classified server-side before PTY allocation; every
  present value, including empty or junk input, must first pass the total,
  fail-closed workspace preparation path (`app/terminal.py:514-541`,
  `app/services/lessons.py:1080-1113`). Client input cannot request `plain` or
  another profile for that creation.
- The lesson path goes only through `spawn_sandboxed("lesson-agent", ...)`, with
  `LESSONS_DIR` as the required root authority. Runtime-probe, argv-validation,
  and spawn exceptions close both PTY fds and become a visible refusal; there
  is no direct-shell retry (`app/terminal.py:553-584`).
- E1's late-bind repair remains intact. The pure launcher requires an absolute,
  traversal-free strict descendant of a non-root authority before adding the
  final bundle bind (`app/sandbox.py:92-164`). Workspace preparation separately
  enforces a real direct child of the resolved lessons root before writing or
  spawning (`app/services/lessons.py:1034-1050`,
  `app/services/lessons.py:1094-1113`).
- Role, workspace, and profile are recorded at creation and exposed without
  public setters. Attach-by-SID uses that live session wholesale and reports
  its stored role; conflicting `lesson` or `role` query data cannot reclassify
  it (`app/terminal.py:296-345`, `app/terminal.py:703-767`).
- Host proxy variables are derived only for server-owned host-network roles.
  The lesson-agent deliberately retains host networking; a future learner role
  receives no unreachable host-loopback proxy variables
  (`app/terminal.py:70-71`, `app/terminal.py:183-235`).
- The existing PTY ownership, reaper, fd-removal, and shutdown paths still
  operate on the wrapper process returned by the launcher. The real host probe
  exercised interactive PTY output and close/reap behavior, not only mocks.
- A plain session still bypasses lesson workspace preparation and the sandbox,
  starts directly in the repository, and records `plain`/no-profile. This is an
  explicit preservation of the owner shell, not a sandbox fallback from a
  failed lesson request (`app/terminal.py:531-594`).

## Prior-condition reconciliation at closing head

- **E1 S1 bundle authority — RESOLVED and not regressed.** E2 supplies the
  app-owned `LESSONS_DIR` authority, while the lesson service retains the
  filesystem/no-symlink direct-child validation that E1 required.
- **Terminal-opt-in T1 parent `/proc` visibility — RESOLVED for live lesson-
  agent sessions; STILL OPEN/ACCEPTED for plain sessions.** The real E2 child
  saw only its private PID namespace and could not find the invented parent
  canary. The deliberately unsandboxed plain repository shell remains a same-
  UID owner shell and is not represented as isolated.
- **Agent host network, CLI login material, `SSH_AUTH_SOCK`, and proxy
  credentials — UNCHANGED/ACCEPTED.** E2 does not make the agent a boundary for
  a less-trusted principal. These capabilities are compatible only with the
  documented direct-loopback, trusted-single-user deployment and continue to
  prohibit wider exposure.
- **Terminal-tab scoping L1 — RESOLVED.** The current client prefers a plain
  successor after close and leaves a lesson-only successor disconnected off
  Learn (`app/static/terminal.js:602-622`). E2 does not reopen that implicit
  connection path.
- **Terminal F1–F4, opt-in, fail-closed workspace, environment allowlist,
  relative path display, and atomic lesson brief conditions — REMAIN
  RESOLVED.** The exact peer/Host/Origin gate precedes all query handling; stale
  writer/control and attach/reaper protections remain; workspace failure still
  precedes PTY allocation; generated briefs retain same-directory atomic
  replacement.
- **Direct/no-forwarded-header launch condition — MITIGATED by the documented
  deployment, not removed from the broader threat model.** This verdict assumes
  the direct loopback listener stated above. Proxy-adjacent or wider deployment
  remains unsupported because the terminal trust gate must receive the real
  peer address.
- **Issue #16 real-isolation scope — RESOLVED for the E2 lesson-agent terminal
  path reviewed here; STILL OPEN for later planned roles/phases.** This drain
  does not claim the later learner-terminal/backend and two-surface client
  work, the F-phase runner integration, or a general multi-user sandbox
  boundary.

## Verification

- `git diff --check 530556f..de51c00` — passed.
- `python verify.py` at starting/closing code head — **614 passed, 0 failed**.
- `python verify_restore.py` at starting/closing code head — **28 passed,
  0 failed**.
- `python scripts/probe_sandbox_profiles.py --skip-agent-api` on the host — all
  three invented profiles passed their filesystem, bundle, cwd, and network
  invariants.
- Real E2 PTY integration probe on the host — passed: `lesson-agent` role and
  profile, bundle cwd, repository hidden, parent environment absent from the
  private `/proc`, and child/session shutdown complete.
- No drain fix cycle was required; the validation baselines were therefore not
  consumed by an implementation commit.

## Post-review landing reconciliation

PR #62 merged while this drain was in progress, before the report/queue commit
was pushed. GitHub records reviewed branch head
`de51c00f0ee92b90fc786b6a74c8b37638e810fe` and merge commit
`def1b11116057166c19a0974b05b459414629f6c`. Local git evidence confirms that
`def1b111` is an ordinary two-parent merge of `530556f` and `de51c00`, and that
its tree `8720790` is byte-identical to the reviewed `de51c00` tree. The later
branch-only docs commit was therefore not part of the merge; this docs-only
follow-up lands the report and Done bookkeeping on `main` without changing the
reviewed application tree. The verdict and counts above apply unchanged to the
landed code.

## Closing verdict

**SAFE TO MAKE LIVE for the documented direct-loopback
(`127.0.0.1:8765`), single-worker, unauthenticated single-user deployment. No
Critical, High, Medium, Low, or Info finding was raised, so zero fix cycles were
consumed and no finding remains open for this queue entry. E1 S1 remains
resolved; terminal-opt-in T1 is resolved for the live lesson-agent path and
remains accepted only for the deliberately plain owner shell; the intentional
agent network/credential posture is unchanged; terminal-tab L1 and the earlier
terminal, workspace, and lesson-writer protections remain resolved. Wider,
proxy-adjacent, multi-user, learner, or runner deployment remains NO. The queue
entry may move to Done. Restarting the live service remains the owner's action
and was not performed by this review.**
