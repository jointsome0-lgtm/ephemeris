# Terminal child job control and learner toolchain — adversarial security review

**Date:** 2026-08-02

## Review parameters

- **Scope:** the one Pending entry present at review start: commits `06f6df5`
  and `63e7a1e` on `fix/terminal-job-control`, landed through merge commit
  `ddbdddb`. The executable diff is `06f6df5^..63e7a1e`, covering
  `app/terminal.py` and the changed portions of
  `tests/test_040_core_surfaces.py`, `tests/test_050_sandbox_learning.py`, and
  `tests/test_060_role_runner.py`; the queue-only branch/head and merge
  bookkeeping were also checked.
- **Starting head:** `81c403e278e6d7191e6e0a1ec80e918605d1d207` on `main`,
  equal to `origin/main`. The tracked worktree was clean. Unrelated untracked
  local state was left untouched and was not used as review evidence.
- **Landed-tree proof:** merge `ddbdddb` has branch head `17394e5` as its
  second parent and both have tree
  `d54151344eaef2c9d72f77d7a5fcb123be537e5a`. From implementation head
  `63e7a1e` to the starting head, the only changed queue-listed path is
  `docs/reviews/QUEUE.md`; the reviewed application and test bytes are the
  landed bytes.
- **Report file:**
  `docs/reviews/2026-08-02-terminal-job-control-review.md`.
- **Prior reports reconciled:** the terminal multi-session, lesson-terminal,
  terminal opt-in, E1 sandbox launcher, E2 lesson-agent, E3 lesson-learner,
  E4 terminal-surfaces, platform-support, and assessment-capability reports
  were re-read for the same PTY/spawn/environment surface. Their current
  dispositions are stated below.

## Context and method

The deployment decision assumes the documented unauthenticated,
single-worker application bound directly to loopback with no forwarded-header
rewriting. The terminal remains an explicitly enabled full-shell surface. No
service was restarted, stopped, signalled, or otherwise touched, and no live
database, lesson bundle, export, credential, browser state, or screenshot was
read or changed.

The complete `app/terminal.py` module and all three changed test files were
read, along with the complete sandbox launcher, the runner toolchain
environment, application assembly, both spawn branches, role selection,
capability ownership, session lifecycle, WebSocket trust/attach paths, and
earlier reports. Static review traced parent/fork boundaries, PTY name and fd
ownership, controlling-terminal acquisition, exception translation, fd and
capability cleanup, logger inputs, sandbox namespace/mount ordering, learner
environment normalization, executable precedence, and the effect of a wider
listener. Tests and commit messages were treated as claims to verify against
the landed source.

## Findings

No Critical, High, Medium, Low, Info, or other finding was found.

## Attack-surface analysis

### Controlling-terminal setup and failure behavior

- `_child_setup_for()` resolves the cached `(fcntl, pty, termios)` tuple and
  `os.ttyname(slave_fd)` in the parent (`app/terminal.py:196-229`). The original
  slave descriptor remains open through spawn, so its `/dev/pts/<n>` allocation
  cannot be recycled before the child opens that name. Neither client input nor
  lesson data contributes to the path.
- The post-fork closure performs only the captured syscalls: `setsid`, open the
  slave, `TIOCSCTTY`, and a `finally` close. It imports nothing, follows no
  attacker-controlled repository path, leaks no additional descriptor on an
  ioctl failure, and propagates failure instead of returning a degraded shell.
- `_spawn_on_pty()` constructs the closure before either spawn and closes both
  PTY ends if parent-side name resolution fails (`app/terminal.py:889-918`). A
  plain pre-exec failure is covered by `subprocess.SubprocessError`, closes both
  ends, logs a bounded server-minted SID plus the exception, and returns no
  session (`app/terminal.py:938-955`). The sandbox wrapper catches the same
  subprocess class and translates it to `SandboxSpawnError`
  (`app/sandbox.py:678-693`); the terminal then closes both ends and raises the
  existing visible fail-closed lesson refusal (`app/terminal.py:919-937`). No
  branch retries without the sandbox.
- On success the parent closes the slave once, the session owns only the
  nonblocking master, and the existing pump, detach, reap, kill, and shutdown
  paths retain ownership of that master. A failed lesson-agent spawn also still
  reaches `_create_session()`'s `finally`, revoking any capability published
  before spawn (`app/terminal.py:857-886`).
- A uvloop probe with `/bin/sleep` observes the child as the PTY foreground
  process group without relying on Bash to repair job control
  (`tests/test_040_core_surfaces.py:2055-2102`). An additional invented-data
  host probe composed `_child_setup_for()` through the real
  `lesson-learner` bubblewrap launcher and likewise observed a positive
  foreground process group equal to the returned wrapper PID.

### Learner `PATH`

- The new `/usr/local/go/bin` entry is a fixed application literal, not a
  service-environment or lesson-derived value (`app/terminal.py:437-468`). The
  learner profile already exposed the host root read-only, blanked home before
  re-exposing only declared mounts, removed host network/runtime discovery,
  and gave the selected bundle the one intended writable bind
  (`app/sandbox.py:309-375`). Naming the already-readable Go binary on `PATH`
  therefore grants no new filesystem, network, credential, or write authority.
- The existing user-command-shim precedence at `{USER_HOME}/.local/bin` is
  unchanged. The new fixed directory precedes only the ordinary system
  prefixes and matches the Go location in `runner.RUNNER_ENV`
  (`app/runner.py:53-64`). The learner still receives no proxy, SSH-agent,
  runtime, or XDG socket variables, and its no-network sandbox profile is
  unchanged (`tests/test_060_role_runner.py:95-126`).

### Listener and logging posture

- No route, listener, opt-in flag, Host/Origin check, ASGI peer check,
  forwarded-header rule, WebSocket selector, session identity, or sandbox
  profile changed. The two new warnings run only on spawn failures. Their SID
  is minted by the server and their exception text comes from the local PTY or
  subprocess path; no lesson title, ledger row, child environment, or proxy
  credential is logged.
- A wider or proxy-adjacent listener remains unsupported independently. The
  change neither weakens the current pre-accept trust gate nor makes the full
  shell safe for a remote or less-trusted principal.

## Prior-condition reconciliation

- **Terminal F1-F4, terminal-tab L1, opt-in wiring, exact Host/Origin trust,
  stale-writer/control ownership, attach/reaper serialization, and PTY/fd
  lifecycle — REMAIN RESOLVED.** The reviewed diff changes only child setup and
  its spawn-failure handling inside that established lifecycle.
- **Platform-support parent-warmed import contract — REMAINS RESOLVED.** Both
  `_pty_stack()` and `profile_preexec_fn()` resolve their modules in the parent;
  the composed child hook performs no first import between fork and exec.
- **E1 S1 bundle authority and E2/E3 sandbox role separation — REMAIN
  RESOLVED.** Both lesson roles still pass the app-owned lesson root, retain
  fail-closed no-fallback behavior, and keep immutable role/workspace/profile
  identity. The learner remains no-network with private runtime/data masks.
- **Terminal-opt-in T1 — REMAINS RESOLVED for `lesson-agent` and
  `lesson-learner`; REMAINS OPEN/ACCEPTED only for the deliberately plain owner
  shell.** The child-setup repair changes no PID namespace or parent `/proc`
  exposure rule.
- **Trusted lesson-agent host network, CLI login material, `SSH_AUTH_SOCK`, and
  proxy posture — UNCHANGED/ACCEPTED for that role only.** The learner `PATH`
  addition does not inherit any of those capabilities.
- **Assessment capability/session provenance — REMAINS RESOLVED.** Publishing
  before spawn, revoking on every failure, and session-owned revocation after a
  successful spawn are unchanged.
- **Direct-loopback/no-forwarded-header condition — REMAINS the deployment
  boundary.** Wider, proxy-adjacent, or multi-user deployment remains NO.

## Verification

- `git diff --check 06f6df5^ 63e7a1e` — passed.
- Merge/tree and current-path comparisons — passed as recorded above.
- `uv run pytest` — **10 passed**, with one existing TestClient deprecation
  warning. The first nested-sandbox run stalled at the known TestClient/
  bubblewrap boundary and was interrupted; the completed count is from the
  same command rerun on the host.
- `uv run python verify_restore.py` — **28 passed, 0 failed** using throwaway
  data.
- Invented `/tmp` learner-bubblewrap PTY probe — passed: foreground process
  group was positive and equal to the returned wrapper PID; no live database
  was opened.
- `python scripts/check_public_hygiene.py`, `git diff --check`, and ignored-
  status inspection — passed; no denied path or unmarked fixture was added.

## Verdict

**SAFE TO MAKE LIVE for the documented direct-loopback, single-worker,
unauthenticated single-user deployment.** This entry has **0 Critical, 0 High,
0 Medium, 0 Low, and 0 Info** findings and no open finding. The controlling
terminal is now acquired independently of event-loop stdio-redirection order,
both failure branches remain fail-closed with complete PTY/capability cleanup,
and the learner `PATH` addition grants no authority that the sandbox did not
already expose. Wider, proxy-adjacent, or multi-user deployment remains
**NO**. The queue entry may move to Done. No live service action was performed.
