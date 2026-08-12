# Lesson source and Claude token — adversarial security review

**Date:** 2026-08-12

## Review parameters

- **Scope:** the two Pending entries named for this batch: squashes `5096a50`
  and `514b363` from 2026-08-09, and merge `0b4f2a2` from 2026-08-12. The
  executable diffs are `5096a50^..5096a50`, `514b363^..514b363`, and
  `0b4f2a2^1..0b4f2a2`.
- **Review tree:** `main` at
  `bf47e46b5b33c3ff95d3f938186f17aa65b7f56c`, equal to `origin/main`. The
  tracked worktree was clean before review work, and all three commits are
  ancestors of the review tree.
- **Context, not a third drained entry:** the Pending 2026-08-11 learner-network
  entry was read because it gives the learner the same host network namespace
  and records its dependency on the 2026-08-09 browser finding. Its queue state
  is unchanged by this report.
- **Prior reports reconciled:** the lesson-agent sandbox, persistent agent
  home, lesson source/shared browser, terminal prompt/build, and terminal
  lifecycle reports were re-read for the same role, mount, environment,
  prompt-injection, browser, backup, and deployment surfaces.

## Context and method

The deployment decision assumes the documented unauthenticated, single-worker
app bound directly to loopback with no forwarded-header rewriting. The
lesson-agent is owner-started, but the current generated brief explicitly
directs it to ingest untrusted source material. A reusable secret or host
capability placed in that agent's model-controlled process tree therefore has
to remain safe when source text attempts to drive tool calls; calling the role
trusted does not make fetched text trusted.

The complete commit diffs, current changed functions, and direct workspace,
sandbox, terminal-spawn, child-environment, backup, bundle-schema, and browser
call paths were read. Static review traced source-directory collision and
serving behavior; generated brief contents; role selection; mount and network
selection; token-file reads; environment construction and subprocess
inheritance; terminal failure and cleanup; and backup inclusion and modes.

Host checks were metadata-only. They inspected the listener socket, Playwright
unit command, relevant MCP configuration and permission keys, profile-directory
metadata, and MCP initialize/tools-list responses. They did not invoke a
browser tool, navigate, list tabs, take a snapshot, read profile contents, read
the Claude token, or read any live lesson or ledger data. No service was
restarted or signalled.

## Findings

### H1 — The lesson sandbox still reaches an RCE-equivalent host browser and unrestricted authenticated actions (High, confirmed)

The 2026-08-11 report's High remains open. The generated brief names the
complete `mcp__playwright__browser_*` family and relies on prose to restrict it
to reading (`app/services/lessons.py:1349-1381`). The lesson-agent profile
still shares the host network and mounts the user's Claude MCP configuration
(`app/sandbox.py:338-365,653-685`). No listed commit adds the app-owned
per-lesson capability or restricted separate server the prior report requires.

The live listener confirms the consequence. It is bound to
`127.0.0.1:9223`, accepts the configured `http://localhost:9223/mcp`, and
rejects an exact-IP Host header. Its 24 advertised tools still include
state-changing click, type, form-fill, upload, and evaluation tools plus
`browser_run_code_unsafe`, which the server marks destructive and describes as
arbitrary JavaScript in the Playwright server process and RCE-equivalent. User
settings remain in `auto` mode without a mechanical deny for those tools. The
loopback and Host checks do not constrain the intended lesson client already
sharing that network namespace.

An invented source page can instruct the tutor that extracting the lesson
requires the unsafe code tool or an authenticated account action. If the
prompt-injected model complies, code executes in the host-side server outside
bubblewrap or the shared signed-in profile changes account state. The owner
must start the tutor and the model must follow the injected instruction, so
this remains High rather than Critical.

The dedicated repair remains the one recorded in the queue: stop lesson roles
reaching the general Playwright server. Expose an app-owned, per-lesson
capability or equivalently restricted separate server that mechanically
refuses every state-changing and arbitrary-code tool. Prompt wording, the
`auto` classifier, a Host check, and origin limits are not that boundary.

### H2 — Untrusted source can drive commands that inherit the reusable Claude OAuth token (High, confirmed)

Merge `0b4f2a2` removes the read-only rotating credentials file but replaces
it with a long-lived, non-rotating secret in the tutor shell's environment.
`_read_claude_token()` reads `DATA_DIR/claude-token`; `_create_session()` adds
the value as `CLAUDE_CODE_OAUTH_TOKEN` for every lesson-agent open
(`app/terminal.py:373-389,843-907`). That environment reaches
`spawn_sandboxed()`, which uses it as the terminal process environment and does
not clear it for interactive profiles (`app/terminal.py:950-1004`;
`app/sandbox.py:1087-1187`). The interactive shell and Claude process therefore
hold the token, and ordinary commands in that process tree inherit it. The
sandbox isolates unrelated host paths; it does not hide a process's own
environment from commands that process starts.

This becomes a concrete credential-disclosure path when combined with the
2026-08-09 source surface. The generated brief tells the tutor to fetch and
analyze page content and correctly calls it untrusted data
(`app/services/lessons.py:1342-1384`), but that distinction is prose enforced
by the model. An invented malicious page can tell the tutor that a shell
command must send its environment to an invented diagnostic endpoint. With
the current host-network/proxy posture and `auto` tool mode, compliance
discloses a reusable Claude credential beyond the sandbox, enabling
unauthorized account or API use. Direct-loopback app binding does not mitigate
outbound disclosure. An owner-started session and successful prompt injection
are required, so this is High rather than Critical.

Moving the token file outside the bundle does not repair this: it stops a
lesson reading the file by path, but the app copies its contents into the
untrusted tool process. A dedicated repair must keep the reusable credential
outside every command environment the model can inspect or spawn, through a
credential broker or equivalent OS/process boundary which grants only the
authentication operation and never the token bytes.

### I1 — The retired `lesson-profile` browser state still exists (Info, confirmed)

The current Playwright unit and user MCP registration reference only
`chrome-profile`, but the unreferenced `lesson-profile` directory remains
present and non-empty. No contents were inspected. This creates no demonstrated
access path and remains private host cleanup rather than application repair.

## Confirmed protections and non-findings

- `source` remains in the shared reserved-name set. Manifest paths and preview
  routing exclude it, and workspace preparation creates it only when the name
  is free, accepts an existing real directory, and neither follows nor
  relocates a file or link occupying the name. No serving or symlink escape was
  found.
- The token file is not mounted into a lesson. Its value is added only for the
  server-selected `lesson-agent` role; learner, runner, and plain sessions do
  not receive it, and an absent or unreadable file leaves it unset. No token
  value is stored in the session object or written to an application log.
- Removing `~/.claude/.credentials.json` resolves issue #188's host-login
  invalidation: a sandbox can no longer consume a single-use refresh token
  without persisting its replacement. H2 is the different disclosure property
  of the replacement credential.
- Full backups deliberately enumerate regular private-instance files by
  exclusion, so `claude-token` is recoverable with the rest of the instance.
  Backup sets are published at mode `0600` under a directory forced to `0700`;
  this did not create a separate backup finding.
- Terminal role selection, fail-closed workspace and sandbox launch, private
  PID namespace, exact peer/Host/Origin gate, PTY ownership and cleanup,
  capability revocation, and direct/no-forwarded-header assumptions were not
  changed and remain in their prior dispositions.
- H2 is not hypothetical multi-user hardening. It is exploitable in the
  documented single-user deployment because untrusted input reaches a
  model-controlled command process with outbound access and the reusable
  secret.

## Prior-condition reconciliation

- **2026-08-11 shared-browser H1 — STILL OPEN.** The current listener and tool
  schemas reproduce the same unrestricted host capability; neither reviewed
  entry supplies its required mechanical restriction.
- **Lesson brief untrusted-data wording — REMAINS PRESENT but is not an
  enforcement boundary.** It is useful defense in depth and does not resolve
  H1 or H2 when the model follows injected text.
- **Bundle-schema reserved paths, preview exclusion, manifest validation, and
  source-name collision behavior — REMAIN RESOLVED.** The current tree retains
  the shared constant and fail-closed filesystem behavior.
- **Lesson-agent role, persistent-home/build-workspace authority, and
  fail-closed sandbox launch — REMAIN RESOLVED.** H2 uses the environment the
  app intentionally installs after role selection; it is not a mount escape.
- **Direct-loopback deployment — remains a mitigation, not a general security
  boundary.** Wider, proxy-adjacent, multi-user, or public deployment remains
  unsupported independently.

## Verification

- `git diff --check 5096a50^ 5096a50`, `git diff --check 514b363^ 514b363`,
  and `git diff --check 0b4f2a2^1 0b4f2a2` — passed.
- `uv run ruff check` on the reviewed Python files and focused tests — passed.
- A focused nested `test_050_sandbox_learning.py` run hit the documented
  TestClient/bubblewrap stall and was stopped after 55 seconds; it is excluded
  from product evidence.
- `uv run pytest` in the normal host context — **398 passed**, with one existing
  TestClient deprecation warning.
- `uv run python verify_restore.py` in the normal host context — **34 passed,
  0 failed**.
- Metadata-only MCP handshake — Playwright server
  `1.63.0-alpha-2026-08-05`, 24 tools, including the state-changing and
  RCE-equivalent schemas above. No browser tool was invoked.
- Host configuration metadata — one registered user MCP server named
  `playwright` at `http://localhost:9223/mcp`; `auto` permission mode; active
  loopback listener using `chrome-profile`; exact-IP Host rejected; retired
  `lesson-profile` still non-empty.

## Closing verdict

**NOT SAFE TO MAKE LIVE for this batch.** The combined surface has **0
Critical, 2 High, 0 Medium, 0 Low, and 1 Info** finding. H1 is the still-open
general Playwright host capability. H2 is the reusable Claude credential in a
process tree that consumes untrusted source and can issue outbound commands.
Both reviewed entries remain Pending until dedicated repairs land and receive
fresh review. No application code, test, specification, private runtime state,
browser state, or live service was changed.
