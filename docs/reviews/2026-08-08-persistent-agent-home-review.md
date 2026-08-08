# Persistent lesson-agent home — adversarial security review

**Date:** 2026-08-08

## Review parameters

- **Scope:** the sole Pending entry present at review start: squash `8483d68`
  of `agent-home-persist`, PR #158. The executable diff is
  `8483d68^..8483d68`, covering `app/sandbox.py`,
  `app/services/lessons.py`, `app/terminal.py`, `docs/backup-restore.md`, and
  the changed tests and queue bookkeeping named by the entry.
- **Starting tree:** `main` at `5fc2795`, equal to `origin/main`; `8483d68` is
  its first parent. The tracked worktree was clean before review work.
- **Report file:**
  `docs/reviews/2026-08-08-persistent-agent-home-review.md`.
- **Prior reports reconciled:** the E1 sandbox-launcher, E2 lesson-agent, E3
  lesson-learner, lesson brief-writer, terminal opt-in, and terminal job-control
  reports were re-read for the same path, mount, workspace, credential,
  environment, PTY, and deployment surfaces. Their current dispositions are
  stated below.

## Context and method

The deployment decision assumes the documented unauthenticated, single-worker
application bound directly to loopback with no forwarded-header rewriting. The
lesson-agent remains a trusted owner tool with host networking and deliberately
re-exposed CLI credentials; this review does not treat it as a boundary for a
less-trusted principal.

The complete squash diff and the full current `app/sandbox.py`,
`app/services/lessons.py`, and `app/terminal.py` modules were read, together
with the changed tests, backup documentation, backup/archive implementation,
workspace and spawn callers, and earlier reports. Static review traced slug and
lesson identity, directory creation and foreign-node handling, lexical mount
authority, mount ordering, role selection, fail-closed spawn behavior,
cross-lesson visibility, credential/config overlays, PTY and capability
lifecycle, backup inclusion, restore extraction, and the wider-listener case.
Tests and commit prose were treated as claims to verify against the landed
source.

The first full-suite and restore-verifier runs inside the nested reviewer
sandbox stalled at the known TestClient/bubblewrap boundary and were
interrupted without assertion output. The exact commands were rerun on the
host; only the completed host runs count below. No live database, lesson
bundle, agent history, credential, browser state, screenshot, or service was
read, changed, restarted, or signalled.

## Findings

No Critical, High, Medium, Low, Info, or other finding was found.

## Attack-surface analysis

### Per-lesson path and mount authority

- The agent-home path is server-derived from a DB-resolved slug that must match
  the closed slug grammar. It is created as
  `<private-root>/agent-homes/<slug>/{claude,codex}`, outside the writable
  bundle tree; a symlink or non-directory at the lesson home or either state
  name is moved aside, and any filesystem failure refuses workspace creation
  (`app/services/lessons.py:2042-2077`, `app/services/lessons.py:2441-2486`).
  A client supplies only the lesson selector and cannot name an agent-home
  path.
- The sandbox builder accepts the argument only for `lesson-agent`, requires a
  non-root private authority, rejects relative/traversal paths, requires a
  strict descendant of that authority, and rejects every path under the bundle
  root (`app/sandbox.py:214-265`, `app/sandbox.py:299-333`). The learner and
  runner profiles reject the argument before argv construction.
- A persistent home replaces exactly the two agent-state tmpfs mounts. The
  blank `$HOME` and `/tmp`, bundle bind, tool/cache mounts, namespace policy,
  and no-home argv remain unchanged. The later read-only binds for Codex and
  Claude credentials/configuration still win over the writable state roots
  (`app/sandbox.py:120-174`, `app/sandbox.py:370-408`). No sibling lesson home
  is mounted or made discoverable through the blank home.

### Terminal integration and failure behavior

- Only the server-selected `lesson-agent` path calls the mutating workspace
  preparation function; `lesson-learner` uses the read-only resolver and gets
  `agent_home=None`. A failed home preparation is the existing visible
  workspace refusal before PTY allocation, and a validation/probe/spawn failure
  remains a sandbox refusal with no bare-shell fallback
  (`app/terminal.py:801-859`, `app/terminal.py:909-960`).
- The path passed to `spawn_sandboxed()` comes from the prepared workspace,
  alongside the app-owned lesson and private roots. Session role, workspace,
  sandbox profile, attach identity, PTY ownership, and assessment-capability
  lifetime are unchanged. Persistence outlives the PTY by design; the
  per-session assessment token still dies with that PTY session.

### Persistent state and recovery boundary

- The writable mounts are the agents' complete `.claude` and `.codex` state
  roots, not a parser or copier of individual transcript files. That means
  caches and other agent-owned state may persist with sessions and history.
  This is not a new privilege boundary: the same trusted lesson-agent shell
  can already write its persistent lesson bundle and has the accepted host
  network, CLI credential, SSH-agent, and proxy posture. The state is isolated
  per lesson and remains private runtime data; it must never enter public Git.
- Full backups enumerate instance files by exclusion, skip links and special
  files, and therefore include regular files below `agent-homes/` without a
  new allowlist. Restore verifies the archive before extraction, uses the
  standard safe extraction filter, and restores under the private instance
  root (`scripts/backup_db.py:360-415`, `scripts/backup_db.py:500-545`). The
  existing documented limitation remains: filesystem files are copied
  file-by-file rather than as one atomic snapshot, so a running agent can
  change its history while a backup is being taken.

## Prior-condition reconciliation

- **E1 S1 bundle authority — REMAINS RESOLVED.** Both bundle and agent-home
  mounts are bounded by separate app-owned roots, and the new home cannot be
  placed inside the writable bundle.
- **E2/E3 role separation and fail-closed launch — REMAIN RESOLVED.** The new
  writable state exists only in the lesson-agent profile; learner and runner
  profiles reject it, and no failure retries outside bubblewrap.
- **Terminal F1-F4, terminal-tab L1, opt-in, exact Host/Origin trust,
  attach/reaper serialization, job control, fd cleanup, and capability
  revocation — REMAIN RESOLVED.** The change passes one prepared path through
  the established spawn seam and changes none of those controls.
- **Brief-writer prompt and filesystem protections — REMAIN RESOLVED.** The
  generated root brief is still constant and atomically replaced; lesson data
  remains data, and foreign link/special-file names are not followed.
- **Terminal-opt-in T1 — REMAINS RESOLVED for both sandboxed lesson roles and
  OPEN/ACCEPTED for the deliberately plain owner shell.** The lesson-agent
  retains a private PID namespace; this change does not alter the plain shell.
- **Lesson-agent host network, CLI login material, `SSH_AUTH_SOCK`, proxy, and
  complete agent-state persistence — ACCEPTED only for the documented trusted
  single-user role.** None makes a remote, proxy-adjacent, multi-user, or
  less-trusted deployment safe.

## Verification

- `git diff --check 8483d68^ 8483d68` — passed.
- `uv run pytest` on the host — **294 passed**, one existing TestClient
  deprecation warning.
- `uv run python verify_restore.py` on the host — **34 passed, 0 failed**.
- `git diff --check` — passed after the report and queue edit.
- `python scripts/check_public_hygiene.py` — passed after the report and queue
  edit; final ignored-status inspection showed only these two intended docs
  changes plus pre-existing ignored local state.

## Verdict

**SAFE TO MAKE LIVE for the documented direct-loopback, single-worker,
unauthenticated single-user deployment.** This entry has **0 Critical, 0 High,
0 Medium, 0 Low, and 0 Info** findings and no open finding. Per-lesson agent
state remains separated from the writable bundle and from sibling lessons,
read-only host credential/config overlays retain precedence, non-agent roles
cannot request the mount, and workspace or sandbox failures remain fail-closed.
The earlier terminal, sandbox, workspace, brief-writer, and recovery protections
retain the dispositions above. Wider, proxy-adjacent, multi-user, or
less-trusted deployment remains **NO**. The queue entry may move to Done. No
live service action was performed.
