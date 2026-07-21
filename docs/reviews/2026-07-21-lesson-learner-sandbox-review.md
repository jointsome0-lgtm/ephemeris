# E3 lesson-learner sandbox integration — adversarial security review

**Scope:** exactly one Pending entry was present at review start: the 2026-07-21
entry for `d6e3563..HEAD` on `fix/36-e3-learner-role`, covering
`app/terminal.py`, `app/sandbox.py`, `app/services/lessons.py`,
`scripts/verify_e3_sessions.py`, and `verify.py`, plus their direct callers. The
scoped implementation range is `d6e3563..1a4f640`, eight commits from
`5479d1f` through `1a4f640`; merge commit `4fc89e9` has the same tree as the
reviewed branch head.

**Starting HEAD:** `4fc89e9993f8dccb45511f1c57f7a6de96f91a22` on `main`.
`git status --short --branch` showed only `## main...origin/main` before review
work, so the worktree was clean.

**Fix target:** after refreshing `origin`, git showed that reviewed branch head
`1a4f640983853587c5379dfcfbb34dfe8fbb19be` is an ancestor of `main` through
ordinary two-parent merge commit `4fc89e9`; both commits have tree
`381110f6ef8d91b9db62f407d34b4abea7e13822`. The change is merged, so repository
tradition puts any drain fixes directly on `main`, not on the feature branch.

**Report file:**
`docs/reviews/2026-07-21-lesson-learner-sandbox-review.md`, derived from the
entry's “lesson-learner sandbox” subject.

**Prior reports to reconcile:** every existing `docs/reviews/*-review.md`
closing verdict was scanned. The most recent binding report for this phase is
`2026-07-21-lesson-agent-sandbox-review.md`: E1 S1 and the lesson-agent
terminal-opt-in T1 route were resolved, the deliberately plain owner shell's
same-UID residual and the agent's host-network/credential posture remained
accepted, terminal F1–F4 and terminal-tab L1 remained resolved, and learner
isolation remained explicitly open for E3. The underlying
`2026-07-21-sandbox-launcher-review.md` requires an app-owned validated bundle
authority and fail-closed launcher behavior. The terminal-opt-in,
lesson-terminal, brief-writer, and bundle-runtime reports continue to bind the
exact peer/Host/Origin gate, fail-closed slug and workspace validation,
no-symlink containment, relative-path disclosure limit, atomic generated-brief
publication, and closed bundle-reader protections. The D5 report's L1
document-generation residual remains mitigated, L2/L3 remain resolved, and the
D4 A1/A2 availability items remain accepted follow-ups; E3 must not regress
them. Their current-head dispositions are stated explicitly in the closing
verdict.

**Validation baseline:** at starting HEAD, the required host runs passed:
`python verify.py` — **627 passed, 0 failed**; `python verify_restore.py` —
**28 passed, 0 failed**. The first `verify.py` run inside the reviewer's
restricted sandbox reproduced the known silent TestClient/bubblewrap startup
stall and was interrupted without assertion output; the recorded count is from
the approved host rerun of the exact command.

## Context and method

The deployment decision assumes an unauthenticated, single-user, single-worker
app bound directly to `127.0.0.1:8765`. No service was restarted or signalled,
and no live database, lesson bundle, export, browser profile, screenshot, or
authenticated state was read or written.

The complete scoped diff and queue-listed files were read with their direct
workspace-resolution, sandbox-launch, WebSocket create/attach, PTY lifecycle,
and application-lifespan callers. Tests and queue prose were treated as claims
to verify, not as review authority. Static review covered server-owned role
selection, malformed and conflicting selectors, reconnect behavior, lexical
and resolved private-root discovery, mount ordering and late binding, runtime
socket and database masking, network and environment isolation, lesson-bundle
write authority, generated-brief preservation, process/fd cleanup, session
identity, and the existing terminal trust and lifecycle invariants.

## Findings (severity-ranked)

### M1 — A DB override inside the writable bundle crosses the learner boundary (Medium, confirmed)

`ACTIVITY_DB` is a documented direct path override, and the starting tree tried
to protect it by masking both its lexical and resolved parent directories. That
ordering cannot protect a database stored inside the selected lesson bundle:
the learner profile must bind that bundle back read-write after all parent
masks (`app/sandbox.py:232-246`). A host bubblewrap probe using an invented
private root, lesson, and sentinel database confirmed the result at starting
HEAD: the learner command read the sentinel successfully (`db_readable: true`,
exit 0).

This is an isolation-boundary failure rather than a normal consequence of the
writable-bundle contract. Under the supported override, the learner shell could
read or modify the ledger database and its neighboring sidecars when the DB was
placed in the selected bundle. The unusual owner-controlled layout and
direct-loopback single-user posture reduce reachability, but the impact is the
private authority store, so this is Medium. The queue could not close while it
remained.

### L1 — An invalid role selector can evict a detached live session at capacity (Low, confirmed)

At starting HEAD, `_create_session()` checked the eight-session cap and called
`_reap_idle(force_oldest=True)` before `_select_create_role()`. An invented
async probe filled the sole configured slot with a detached session older than
the force grace, then submitted an unknown role. The request was rejected, but
only after that valid session had been removed and closed. A same-origin local
client could therefore turn malformed E3 creation requests into terminal
availability loss. The loopback/Host/Origin gate limits the actor and no PTY
authority crosses roles, so this is Low.

No Critical, High, or Info finding.

## Confirmed protections at starting HEAD

- The server owns a closed `plain` / `lesson-agent` / `lesson-learner` role
  enum. Omitting `role` preserves E2 semantics; explicit `plain` with a lesson,
  a role without a lesson, an unknown role, and `sid` plus `role` all refuse.
  Creation-time role, workspace, and profile remain immutable on attach.
- Learner workspace resolution reuses the DB/slug and real-direct-child checks
  without calling the manifest/brief writer. Missing, junk, unknown, symlinked,
  or non-directory bundles refuse before PTY allocation; opening a learner
  session does not regenerate `AGENTS.md` or `CLAUDE.md`.
- A live learner session receives a server-minted `learner.` SID. A stale SID
  with that marker refuses instead of being reinterpreted as an implicit E2
  lesson-agent creation. Agent and learner sessions can remain live
  concurrently without replacing one another.
- The learner profile keeps the E1 private user/PID/network namespaces, fresh
  `/proc`, blank `/tmp`, blank home, read-only tool caches, writable selected
  bundle, and no host network. It additionally blanks `/run`, strips proxy,
  `SSH_AUTH_SOCK`, runtime, and XDG discovery variables, and normalizes
  HOME/SHELL/PATH.
- Private data, lesson-store, database-parent, and repository masks include
  lexical and resolved spellings. They are applied after learner cache binds
  and before the one intentional selected-bundle bind, so an external private
  root, cache-nested private instance, symlinked store, DB override outside the
  selected bundle, and checkout remain hidden.
- Bubblewrap probe or spawn failure closes both PTY fds and becomes a visible
  sandbox refusal. There is no bare-shell retry. The plain owner shell and
  lesson-agent paths retain their pre-E3 process, environment, proxy, and
  launcher behavior.

## CLOSING ADDENDUM — fix commit `20f4c57` (cycle 1 of 10)

### M1 — resolved

`_learner_workspace_contains_db()` now compares both absolute lexical and
resolved spellings of the configured DB and selected workspace. If any DB
spelling is equal to or below a workspace spelling, learner creation raises the
existing visible sandbox refusal before environment construction, PTY
allocation, or bubblewrap spawn (`app/terminal.py:321-344`,
`app/terminal.py:625-648`). This preserves the intentionally writable bundle
for valid layouts while failing closed for the one layout parent masks cannot
secure.

The regression supplies an invented inside-bundle override and proves refusal
before `openpty()` or `spawn_sandboxed()`; a sibling override remains accepted
by the containment predicate (`verify.py:4231-4257`).

### L1 — resolved

The pure closed-enum selection now runs before entering `_CREATE_LOCK` and
before the capacity/reaper path (`app/terminal.py:593-631`). The regression
sets capacity to zero and proves an unknown role still raises the selector
refusal without calling `_reap_idle()` (`verify.py:4159-4171`). Valid requests
retain the serialized cap check and oldest-detached eviction behavior.

Fresh review of `20f4c57` found no new Critical, High, Medium, Low, or Info
finding. Both findings from this drain are closed.

## Prior-condition reconciliation at closing head

- **E1 S1 bundle authority — RESOLVED and not regressed.** Both sandboxed
  terminal roles still pass the app-owned `LESSONS_DIR` authority and the
  filesystem-validated strict-child workspace. M1 adds a pre-spawn refusal; it
  does not weaken the authority or the final bind rules.
- **E2's later learner-isolation condition — RESOLVED for the E3 terminal path.**
  The live learner role now has the E1 no-network/private-PID profile, sanitized
  environment, runtime/private-root masks, stale-reconnect refusal, and the M1
  DB-layout guard. Runner and two-surface client work remain later scope and
  are not claimed here.
- **Terminal-opt-in T1 parent `/proc` visibility — RESOLVED for lesson-agent and
  lesson-learner sessions; STILL OPEN/ACCEPTED for the deliberately plain owner
  shell.** Both lesson roles run with a private PID namespace and fresh
  `/proc`; E3 does not represent the unsandboxed repository shell as isolated.
- **Agent host network, CLI login material, `SSH_AUTH_SOCK`, and proxy
  credentials — UNCHANGED/ACCEPTED for the agent role only.** The learner gets
  none of them. The agent remains a trusted single-user tool, not a boundary
  for a less-trusted principal.
- **Terminal F1–F4, terminal-tab L1, opt-in, workspace fail-closed behavior,
  relative-path display, atomic lesson briefs, and bundle-reader findings —
  REMAIN RESOLVED.** The peer/Host/Origin gate still precedes query handling;
  attach ownership, writer wakeup, reaper locking, slug/symlink checks, and
  generated-file publication are unchanged. Learner resolution performs no
  brief write.
- **Direct/no-forwarded-header launch condition — MITIGATED by the documented
  deployment, not removed from the broader threat model.** This verdict
  assumes the direct loopback listener receives the real peer address.
- **D5 L1 — MITIGATED; D5 L2/L3 — RESOLVED; D4 A1/A2 — STILL OPEN/ACCEPTED.**
  E3 does not change the bridge document-generation residual, served-byte
  binding, bounded identity reads, request buffering, or projection-lock
  duration. None is regressed or newly reachable through the learner PTY.

## Closing verification

- Starting HEAD `4fc89e9`: `python verify.py` — **627 passed, 0 failed**;
  `python verify_restore.py` — **28 passed, 0 failed**.
- Host M1 proof at starting HEAD — the invented in-bundle DB sentinel was
  readable from the real learner bubblewrap process; this reproduced the
  finding without touching live data.
- Invented L1 async proof at starting HEAD — the invalid selector was rejected
  only after the detached session was evicted and closed.
- `git show --check 20f4c57` and `python -m py_compile app/terminal.py
  verify.py` — passed.
- Fix-cycle `python verify.py` — **629 passed, 0 failed**, including the real
  throwaway-loopback E3 agent/learner WebSocket probe and both new regressions.
- Fix-cycle `python verify_restore.py` — **28 passed, 0 failed**.
- Fix-cycle `python scripts/check_public_hygiene.py` — passed; no denied path or
  unmarked fixture is tracked.

## Closing verdict

**SAFE TO MAKE LIVE for the documented direct-loopback
(`127.0.0.1:8765`), single-worker, unauthenticated single-user deployment. One
Medium and one Low finding were raised and resolved in cycle 1; no Critical,
High, Medium, Low, Info, or other finding remains open for this queue entry. E1
S1 remains resolved; E3 closes the prior learner-isolation condition for the
lesson-learner terminal path; terminal-opt-in T1 is resolved for both sandboxed
lesson roles and remains accepted only for the deliberately plain owner shell;
the agent's intentional network/credential posture is unchanged; terminal,
workspace, lesson-writer, bundle, and D5 protections retain the dispositions
listed above. Wider, proxy-adjacent, multi-user, or runner deployment remains
NO. The queue entry may move to Done. Restarting the live service remains the
owner's action and was not performed by this review.**
