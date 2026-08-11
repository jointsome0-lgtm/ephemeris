# Lesson source material and shared browser — adversarial security review

**Date:** 2026-08-11

## Review parameters

- **Scope:** the sole Pending entry present at review start: squash
  `5096a50` of `feat/study-browser`, PR #177, and squash `514b363` of
  `docs/playwright-browser-brief`, PR #178. The executable diffs are
  `5096a50^..5096a50` and `514b363^..514b363`; the current review tree is
  `514b363ff511ecdf53231a1d6d3ab6d78855cb92` on `drain-source`.
- **Starting worktree:** the only tracked change was the owner's existing
  amendment to `docs/reviews/QUEUE.md`. It was treated as the current factual
  scope and was not modified.
- **Report file:**
  `docs/reviews/2026-08-11-lesson-source-material-review.md`.
- **Prior reports reconciled:** the lesson brief teaching-contract,
  bundle-schema runtime, lesson-agent sandbox, persistent-agent-home, lesson
  build workspace, and 2026-08-09 combined terminal/build reports were
  re-read for the prompt-injection, reserved-path, symlink, host-network,
  credential/configuration, backup, and wider-listener surfaces. Their current
  dispositions are stated below.

## Context and method

The deployment decision assumes the documented unauthenticated, single-worker
application bound directly to loopback with no forwarded-header rewriting.
The lesson-agent remains an owner-started tool, but the source it is now told
to fetch and read is explicitly untrusted. A control made reachable from that
agent therefore has to remain safe when source text attempts to drive it; the
agent's general trust does not turn page content into trusted instructions.

Both squash diffs and the complete current `app/services/lessons.py` and
`app/services/bundle_schema.py` modules were read, together with the terminal
and sandbox call chain, build-time symlink scan, bundle specification,
security model, changed tests, and prior reports. Static review traced source
directory creation and collision behavior; manifest and preview exclusion;
brief generation; lesson role, mount and network selection; user-scope MCP
configuration; and the browser server's effective tool surface.

Host verification was deliberately metadata-only. It inspected the listener,
process command line, the two relevant MCP configuration keys, profile
directory metadata, and an MCP initialize/tools-list handshake. It did not
open a browser page, list tabs, take a snapshot, invoke any browser tool, read
profile contents, or read any live lesson, ledger, credential, or screenshot.
No service was restarted or signalled.

## Findings

### H1 — The lesson sandbox can reach an RCE-equivalent host tool and unrestricted authenticated browser actions (High, confirmed)

The generated brief names the whole `mcp__playwright__browser_*` family, then
relies on prose to restrict it to navigation and snapshots and to forbid state
changes (`app/services/lessons.py:1345-1374`). The repository's own security
model already says this is agent compliance rather than enforcement and that
an app-owned capability which refuses state-changing tools does not exist
(`docs/security-model.md:92-120`). The live surface is materially wider than
the brief's examples:

- The `lesson-agent` profile deliberately shares the host network namespace
  and bind-mounts the user Claude configuration, including the MCP registration
  (`app/sandbox.py:196-218,507-523`).
- The current server listens on `127.0.0.1:9223` and accepts an unauthenticated
  MCP initialization at `localhost:9223`; the exact-IP Host header is refused,
  but the configured hostname works from the shared namespace.
- Its advertised core tool set contains click, form fill, typing, file upload,
  arbitrary page evaluation, and `browser_run_code_unsafe`. The latter is
  annotated `readOnlyHint: false`, `destructiveHint: true`, and described by
  the installed `@playwright/mcp` 0.0.79 server itself as executing arbitrary
  JavaScript in the Playwright server process and being “RCE-equivalent.” The
  bundled implementation places it in the always-on `core` capability and
  evaluates caller-supplied code with Node's `vm.runInContext`
  (`playwright-core/lib/coreBundle.js:67013-67070,67777-67807`). The current
  command has no origin allowlist or per-tool restriction and uses one shared
  persistent browser context.
- The mounted user settings currently select Claude Code's `auto` permission
  mode and contain no deny rule for the server or its unsafe/state-changing
  tools. Auto mode can execute tool calls without a user prompt; Claude Code's
  own documentation calls its classifier a research preview that does not
  guarantee safety
  ([permission modes](https://code.claude.com/docs/en/permission-modes)). It is
  useful defense in depth, not a deterministic deny.

An invented malicious source can tell the tutor that extracting the lesson
requires the unsafe code tool, a form submission, or a click on an
account-setting control. If the prompt-injected agent follows it, the unsafe
tool executes in the host-side MCP process outside bubblewrap, while the other
tools act through the already-signed-in persistent profile. The consequences
are host-code execution across the lesson sandbox boundary, disclosure of
host/private state, or authenticated account changes. The owner must still
start the lesson agent and the model must take the injected action, so this is
High rather than Critical; neither direct-loopback binding nor the Host check
mitigates a client intentionally placed in that same network namespace.

A dedicated repair must stop exposing the general Playwright MCP server to the
lesson agent. The boundary described in `docs/security-model.md` is the right
shape: an app-owned, per-lesson capability should expose only the necessary
read operations, constrain origins, refuse every state-changing and arbitrary-
code tool mechanically, and run against a least-privilege study profile.
Explicit Claude deny rules and a separately sandboxed MCP process are useful
additional layers, but prompt wording or the auto-mode classifier cannot be
the enforcement mechanism.

### I1 — The retired `lesson-profile` browser state still exists (Info, confirmed)

The amended queue entry says the `lesson-profile` profile no longer exists.
Current host metadata contradicts that claim: both `chrome-profile` and
`lesson-profile` are present, and the latter is a non-empty directory. The
registered `playwright` MCP server and the listener process use only
`chrome-profile`; no running process or configuration reference to
`lesson-profile` was found, and no profile contents were inspected.

This does not create a demonstrated access path, so it is informational rather
than Low. It is nevertheless retained private browser state and means the
queue's deletion claim is not true. A separate host cleanup should first
confirm that no session needs it, then remove it through the owner's normal
private-state deletion procedure; do not add any of it to Git.

## Confirmed protections and non-findings

- `source` is in the shared reserved-name set, and the v2 path grammar rejects
  it and every descendant for pages, block files, entries, and artifact roots
  (`app/services/bundle_schema.py:31-36,238-258`). The v1 preview denylist uses
  the same set, so `source/` is not a public lesson-file surface.
- Workspace preparation derives the bundle from the DB-resolved slug, refuses
  an unsafe bundle directory, creates `source/` at mode `0700` only when the
  name is free, accepts an existing real directory, and neither follows nor
  relocates a file or link on the name. The generated brief is selected from
  that result before publication, so a taken name is not advertised as a write
  target (`app/services/lessons.py:2170-2229,2504-2547`).
- Existing contents in a real source directory survive another terminal open.
  The source material remains private bundle data; it is neither declared in
  the manifest nor served by the preview. The build path retains its whole-
  bundle no-symlink refusal, so a source link cannot be smuggled into a built
  artifact (`app/services/lesson_build.py:463-487,903-943`).
- The brief still marks fetched material as untrusted data rather than
  instructions and keeps the whole-bundle no-symlink rule. This preserves the
  earlier prompt-injection wording repair. H1 is not a wording regression: it
  is the new fact that the reachable capability can cross the sandbox and
  change host/account state when wording fails.
- The live MCP listener is bound to `127.0.0.1:9223`, accepts `localhost:9223`,
  and rejects `127.0.0.1:9223` as Host. That prevents a direct wider listener
  and simple alternate-Host access; it does not constrain the intended
  lesson-agent client already sharing host networking.
- `514b363` changes only the generated brief, documentation, and its string
  assertion. It introduces no new Ephemeris route, listener, filesystem
  writer, or terminal code path.

## Prior-condition reconciliation

- **Lesson-brief untrusted-data and no-symlink findings — REMAIN RESOLVED as
  generated instructions.** The source, learner record, run output, pages, and
  artifacts are still classified as data and the generated brief retains
  precedence. H1 confirms the already-documented limit of that defense when a
  privileged external tool is exposed.
- **Bundle-schema reserved-path, preview allowlist, manifest, and per-segment
  symlink conditions — REMAIN RESOLVED.** The new reservation uses the shared
  runtime constant and does not create a second file-serving path.
- **Lesson-agent role, persistent-home/build-workspace authority, fail-closed
  launch, and read-only credential/configuration mounts — REMAIN RESOLVED.**
  H1 bypasses those filesystem mounts through a separate process on the shared
  network; it is not a regression in their argv construction.
- **Terminal Host/Origin, PTY lifecycle, environment allowlist, capability
  revocation, and direct/no-forwarded-header conditions — REMAIN RESOLVED.**
  The listed commits do not alter them.
- **Agent host network and CLI credentials — remain ACCEPTED only for the
  trusted single-user lesson-agent role.** That acceptance does not include
  granting untrusted source text an RCE-equivalent host tool or unrestricted
  authenticated-browser actions; H1 is open.
- **Direct-loopback deployment — remains a mitigation, not a general
  boundary.** Wider, proxy-adjacent, multi-user, remote, or less-trusted
  deployment remains **NO** independently.

## Verification

- `git diff --check 5096a50^ 5096a50` and
  `git diff --check 514b363^ 514b363` — passed.
- `uv run pytest` on the host — **392 passed**, with one existing
  TestClient deprecation warning.
- `uv run python verify_restore.py` on the host — **34 passed, 0 failed**.
- Live listener/configuration probe — confirmed one registered user-scope MCP
  server named `playwright` at `http://localhost:9223/mcp`, a listener bound to
  `127.0.0.1:9223`, and rejection of the exact-IP Host header.
- Metadata-only MCP handshake — confirmed Playwright server version
  `1.63.0-alpha-2026-08-05` and the state-changing/RCE-equivalent tool schemas.
  No browser tool was invoked.
- Current profile metadata probe — confirmed `chrome-profile` is active and
  the supposedly removed `lesson-profile` directory remains non-empty. No
  profile file or browser content was read.
- Final `git diff --check` and `python scripts/check_public_hygiene.py` —
  passed. Ignored-status inspection showed only the owner's pre-existing queue
  amendment and this new report; test-created caches were removed.

## Closing verdict

**NOT SAFE TO MAKE LIVE for the current Pending entry.** The reviewed surface
has **0 Critical, 1 High, 0 Medium, 0 Low, and 1 Info** finding. The repository
implementation safely reserves, creates, and withholds `source/`, but the
host-side browser integration exposes an RCE-equivalent, state-changing MCP
surface across the lesson sandbox and relies on prompt compliance plus a
non-guaranteed classifier. H1 needs a dedicated repair and fresh review before
the deploy gate can clear. I1 needs separate private host-state cleanup.

Per the user's explicit instruction for this run, only this report was written:
the pre-existing queue edit remains untouched and the entry remains Pending.
No application repair or live-service action was performed.
