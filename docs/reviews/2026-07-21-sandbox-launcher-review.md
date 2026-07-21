# E1 sandbox launcher — adversarial security review

**Scope:** exactly one Pending entry was present at review start: the 2026-07-21
entry for `40dfa69..HEAD` on `fix/16-e1-sandbox-launcher`, covering
`app/sandbox.py`, `scripts/probe_sandbox_profiles.py`, and `verify.py`, plus
their direct callers. The scoped commits are `6aa80ca`, `53b8481`, and
`4161f76`. There is no live terminal caller in E1; the only current callers are
the throwaway on-host probe and verifier.

**Starting HEAD:** `4161f766d1e133a22618f115a41b120943a80977` on
`fix/16-e1-sandbox-launcher`. `git status --porcelain=v1` was empty before any
review work, so the worktree was clean.

**Fix target:** `40dfa69` is reachable from `main`, but starting HEAD is not;
`main` is an ancestor of starting HEAD. This is therefore a not-yet-merged
branch drain, and fixes belong on `fix/16-e1-sandbox-launcher`, not `main`.

**Report file:** `docs/reviews/2026-07-21-sandbox-launcher-review.md`, derived
from the queue subject “sandbox launcher”.

**Prior reports to reconcile:** the most recent binding report for this feature
area is `2026-07-16-terminal-optin-review.md`. Its T1 same-UID `/proc`
environment visibility is accepted posture pending real isolation; its closing
verdict also records deliberate `SSH_AUTH_SOCK` and proxy-credential exposure
inside the single-user lesson shell. The older terminal trust/lifecycle and
lesson-workspace findings are recorded there as resolved. No earlier report
reviews this E1 launcher surface itself. The closing verdict below must state
the status of T1 and those resolved protections for the code under review.

**Validation baseline:** the starting tree passed `python verify.py` with
**609 passed, 0 failed** and `python verify_restore.py` with **28 passed,
0 failed**. Both commands first reproduced the known silent TestClient stall in
the nested reviewer sandbox and were interrupted without assertion output;
the recorded counts are from the required host reruns.

## Context and method

v0 has no authentication. The deployment decision assumes a direct
`127.0.0.1:8765` bind, one worker, and one trusted local user. E1 defines the
bubblewrap profiles and fail-closed spawn seam but deliberately does not route
the live terminal through them; E2 owns that integration.

The complete scoped diff and files were read, along with every current caller,
the issue #16 real-isolation contract, the terminal opt-in report, security
model, and bundle-spec reference. Static tracing covered mount ordering,
namespace and network selection, home/repository masking, bundle authority,
credential/config and cache re-exposure, child environment handling, runtime
probe caching, spawn failure behavior, pre-exec rlimits, and the probe's
assertions. Host bubblewrap probes used only invented throwaway paths and did
not inspect live data.

## Initial summary

No Critical or High finding. One Medium sandbox-boundary finding is confirmed:
the path validator accepts any absolute path without `..`, including `/`, and
the agent profile's late writable bundle bind can therefore replace the masks
that are supposed to hide the repository and home. This is not reachable from
the live service in E1, but it is a defect in the security primitive being
prepared for E2 and must be fixed before this entry can move to Done.

## Findings (severity-ranked)

### S1 — the bundle argument can remount an arbitrary host subtree over the sandbox masks (Medium, confirmed)

At starting HEAD, `_pure_bundle_path()` requires only an absolute path with no
`..` component (`app/sandbox.py:93-98`). `build_sandbox_argv()` installs the
read-only root, `/tmp` tmpfs, and blank-home tmpfs first, then appends the
caller-supplied bundle bind (`app/sandbox.py:114-142`). For `lesson-agent` and
`lesson-learner` that final bind is writable. Nothing ties the path to a
trusted lesson-bundle root or prevents it from naming a mask ancestor.

A read-only host probe passed the invented invalid input `bundle_dir="/"` to
the agent profile and ran a child that only checked path visibility. Bubblewrap
exited 0; `/home/aina/projects/ephemeris` was visible and the home directory
contained the full host view instead of the seven allowlisted top-level
entries. The late `--bind / /` had replaced the earlier read-only-root and
blank-home mounts. Other accepted system subtrees can likewise be made
writable inside the sandbox.

There is no current live exploit path because E1 has no terminal/runner caller,
and the checked-in probe supplies its own freshly created `/tmp` child. The
impact is nevertheless a complete isolation bypass if a later caller passes an
incorrect or insufficiently trusted path, while the helper's validation and
name imply that such paths have been rejected. Make the trusted bundle root an
explicit required input, reject a filesystem-root authority and the authority
path itself, require the bundle to be a strict lexical descendant, and add
regressions for `/`, the root itself, and an outside sibling. The builder may
remain pure; filesystem/no-symlink validation remains the integrating caller's
separate responsibility.

## Confirmed protections and prior-condition reconciliation at starting HEAD

- The intended profiles work on the host for a freshly created bundle:
  `lesson-agent` has host networking and a writable bundle; `lesson-learner`
  has no network and a writable bundle; `lesson-runner` has no network, a
  read-only bundle, and tmpfs scratch. All three hide the repository and expose
  only their declared home entries for that valid input.
- Runtime probe and spawn failures are visible and do not retry the command
  unsandboxed. The probe result is cached for the process lifetime, and child
  environments are explicit rather than inherited.
- The agent and learner rlimit hook bounds `RLIMIT_NOFILE` and `RLIMIT_NPROC`
  without attempting to raise the inherited hard limit. Strict runner limits
  remain explicitly deferred to F3 and are not claimed by E1.
- **Terminal-opt-in T1 remains open as accepted posture for the live code.** E1
  does not yet route terminal children into the PID namespace, so this branch
  does not itself close the same-UID `/proc` visibility described by the prior
  report. The E1 design would mitigate that route once integrated, but S1 must
  be closed before relying on it.
- **Deliberate shell capabilities remain explicit, not regressed.** The agent
  profile intentionally shares host networking and re-exposes its CLI login
  material read-only; the existing live terminal still deliberately forwards
  `SSH_AUTH_SOCK` and proxy variables under the direct-loopback single-user
  posture. E1 does not make those safe for a less-trusted user or wider bind.
- **Earlier terminal trust/lifecycle and lesson-workspace findings remain
  resolved.** No live terminal, WebSocket, PTY, workspace-preparation, or
  listener path changes in this entry.

## Initial verification

- `git diff --check 40dfa69..4161f76` — passed.
- `python -m py_compile app/sandbox.py scripts/probe_sandbox_profiles.py` —
  passed.
- Host `python scripts/probe_sandbox_profiles.py --skip-agent-api` — passed all
  three intended profiles using a throwaway bundle.
- Read-only host adversarial probe with agent `bundle_dir="/"` — confirmed S1:
  child exit 0, repository visible, blank-home invariant lost.
- Starting `python verify.py` — 609 passed, 0 failed.
- Starting `python verify_restore.py` — 28 passed, 0 failed.

## Initial deploy verdict

**Current direct-loopback service: unchanged and safe at the previously
reviewed posture because E1 is not wired live. E1 launcher contract: NOT YET
SAFE TO INTEGRATE; S1 remains open. Wider deployment remains NO — v0 is
unauthenticated, the live terminal retains the accepted same-user credential
posture, and the agent profile intentionally has host networking. The queue
entry remains Pending.**

## CLOSING ADDENDUM — fix commit `f59e84a` (cycle 1 of 10)

### S1 — resolved

`build_sandbox_argv()` and `spawn_sandboxed()` now require an explicit
`bundle_root` authority (`app/sandbox.py:118-134`, `app/sandbox.py:241-259`).
The pure validator requires both paths to be absolute and traversal-free,
rejects a filesystem-root authority, rejects mounting the authority itself,
and accepts only a strict lexical descendant (`app/sandbox.py:93-115`). The
on-host probe passes its temporary directory as the authority and the invented
bundle as its child; no current caller can omit the trust boundary.

The regression replaces the earlier single relative-path assertion with a
boundary matrix: `/`, the authority itself, an outside sibling, and a valid
bundle under a `/` authority are all refused. A valid child still produces the
same profile argv. The demonstrated `--bind / /` sequence can no longer be
constructed through the API unless a future caller first lies about a separate
trusted authority; E2 must obtain that authority from app configuration and
retain the existing filesystem/no-symlink workspace validation rather than
accepting client input. That integration responsibility is explicit and is not
silently claimed by this pure E1 builder.

Fresh review of `f59e84a` found no new Critical, High, Medium, Low, or Info
finding. S1 is the only finding from this drain and is closed.

## Gate reconciliation at closing head

- **S1 — RESOLVED.** Unsafe root, authority-equal, and outside-authority paths
  fail before bubblewrap is invoked; valid throwaway bundles preserve every
  profile invariant.
- **Terminal-opt-in T1 — MITIGATED BY THE E1 PRIMITIVE, STILL OPEN FOR THE LIVE
  TERMINAL UNTIL E2.** A child actually launched through this profile receives
  a private PID namespace and fresh `/proc`, addressing the earlier same-UID
  parent-environment route. E1 does not change the live PTY spawn path, so the
  accepted current-service residual is not represented as resolved early.
- **Deliberate `SSH_AUTH_SOCK`, proxy, CLI-login, and agent-network posture —
  UNCHANGED/ACCEPTED for the documented single-user deployment.** The E1 agent
  profile deliberately retains host networking and read-only login material.
  This is not a boundary for less-trusted principals and does not support a
  wider listener.
- **Earlier terminal F1–F4, opt-in, fail-closed workspace, environment
  allowlist, path-display, and lesson-writer conditions — REMAIN RESOLVED.** No
  live terminal, WebSocket, PTY, lesson writer, or listener code changed.

## Closing verification

- `git show --check f59e84a` — passed.
- `python -m py_compile app/sandbox.py scripts/probe_sandbox_profiles.py
  verify.py` — passed before the fix commit.
- Direct negative authority matrix — passed; all four unsafe shapes refused.
- Host `python scripts/probe_sandbox_profiles.py --skip-agent-api` — passed all
  three intended profiles after the fix; repository/home masking, network
  policy, bundle access, and runner cwd matched expectations.
- `python verify.py` — **609 passed, 0 failed**, preserving the 609 baseline.
- `python verify_restore.py` — **28 passed, 0 failed**, preserving the 28
  baseline.
- `python scripts/check_public_hygiene.py` — passed before the fix commit; no
  denied path or unmarked fixture is tracked.

## Closing verdict

**SAFE TO MAKE LIVE for the documented direct-loopback, single-worker,
single-user deployment.** E1 still has no live spawn-path effect, and its
launcher contract is now safe for the later E2 integration provided E2 supplies
the app-owned, filesystem-validated bundle root and retains fail-closed
workspace validation. One Medium finding was resolved in cycle 1; no finding
remains open. Terminal-opt-in T1 remains accepted for the current live path
until E2 actually routes it through the private PID namespace; all earlier
resolved terminal and lesson protections remain resolved. Wider deployment
remains **NO** — v0 is unauthenticated and the agent role intentionally has host
networking and CLI credentials. The queue entry may move to Done. Restarting
the live service remains the owner's action and was not performed by this
review.
