# Pending queue batch — adversarial security review

**Date:** 2026-08-16

## Review parameters

- **Scope:** every Pending entry present at review start: PR #197's lesson
  reference companions; merge `0b4f2a2` from PR #189; squashes `5096a50` and
  `514b363` from PRs #177/#178; and squash `c83c093` from PR #181.
- **Landed review tree:** clean `main` at
  `50feb273dc99e938461a5f11f5dbfcf884d71352`, equal to `origin/main`.
  Live GitHub state confirmed that all five named PRs are merged. PR #197's
  landed squash is `50feb27`; queued `d987ec1` was a preview commit, so the
  effective landed diff is `50feb27^..50feb27` and the review-fix delta is
  `d987ec1..50feb27`.
- **Report file:**
  `docs/reviews/2026-08-16-pending-queue-batch-review.md`.
- **Prior reports reconciled:** the lesson brief-writer and teaching-contract
  reviews, learning-output-style review, bundle-schema reviews, persistent
  agent-home review, lesson-source/shared-browser reviews, Claude-token review,
  terminal/sandbox reviews, and lesson-build/bridge reviews were re-read for
  their instruction/data, path, writer, role, environment, network, listener,
  and deployment findings.

## Context and method

The deployment decision assumes the documented unauthenticated, single-worker
app bound directly to `127.0.0.1:8765` with no forwarded-header rewriting.
Lesson-agent source material is explicitly untrusted, so a secret or host
capability in the model-controlled process tree must remain safe when source
text attempts to drive commands or tool calls. The owner starting the session
does not make fetched content trusted.

The listed diffs and the current changed functions were read with their direct
bundle-schema, preview-routing, workspace-preparation, terminal-role,
child-environment, sandbox-mount, CSP, and spawn callers. The complete generated
core and four companions were compared with the prior monolithic contract and
the current bundle specification. Host checks were metadata-only: listener and
unit metadata, selected MCP/permission configuration keys, profile-directory
metadata, and MCP `initialize`/`tools/list`. No browser tool was invoked; no
page, tab, profile content, token value, live lesson, ledger row, screenshot,
or service state was changed. No service was restarted or signalled.

## Findings

### H1 — Lesson roles still reach an RCE-equivalent host browser and unrestricted authenticated actions (High, confirmed)

The earlier shared-browser High remains open. The always-loaded lesson brief
still names the complete `mcp__playwright__browser_*` family and relies on prose
to limit it to reading (`app/services/lessons.py:1818-1824`). The lesson-agent
profile still mounts the user's Claude MCP configuration read-only and both
interactive lesson profiles still use the host network namespace
(`app/sandbox.py:340-367,656-688`). PR #197 changes neither boundary.

The live surface reproduces the consequence. `playwright-mcp.service` is active
on `127.0.0.1:9223`, registered as `playwright` at
`http://localhost:9223/mcp`, using the persistent `chrome-profile`. Its current
24-tool schema includes state-changing navigation, click, type, form-fill,
upload and evaluation operations plus `browser_run_code_unsafe`, described by
the server as arbitrary JavaScript in the Playwright server process and
RCE-equivalent, with `readOnlyHint: false` and `destructiveHint: true`. Claude's
current default mode is `auto` and no mechanical deny for this server is
configured. The exact-IP Host spelling is rejected, but the configured
`localhost` spelling succeeds from the shared namespace.

An invented hostile source can tell the tutor that reading the lesson requires
the unsafe tool or an authenticated account action. If the prompt-injected
model follows it, code runs in the host-side server outside bubblewrap or the
shared signed-in browser changes account state. The owner must open a lesson
session and the model must take the injected action, so this remains High
rather than Critical. Loopback and the Host check do not constrain the client
intentionally placed in that namespace.

The repair remains mechanical isolation: stop lesson roles reaching the
general Playwright server. Expose an app-owned per-lesson capability or an
equivalently restricted separate server that refuses every state-changing and
arbitrary-code operation. Prompt wording, `auto` classification, an origin
allowlist, or the current Host check are defense in depth, not that boundary.

### H2 — Untrusted source can still drive commands that inherit the reusable Claude OAuth token (High, confirmed)

`_read_claude_token()` still reads the private instance's `claude-token`, and
every lesson-agent open still copies its value into
`CLAUDE_CODE_OAUTH_TOKEN` (`app/terminal.py:373-389,900-907`). That environment
is passed unchanged to the interactive sandbox process
(`app/terminal.py:950-1004`). The current private-instance file exists as a
mode-0600 regular file. It was not read.

The token therefore sits in the same shell process tree that the generated
brief directs to fetch and interpret explicitly untrusted source material and
that has outbound network access. An invented source can instruct the tutor to
run a diagnostic command which transmits its environment. If the model
complies, the reusable credential leaves the sandbox and can be used outside
the lesson. Direct-loopback app binding does not mitigate outbound disclosure,
so the earlier High remains live.

The required repair is unchanged: keep reusable credential bytes outside every
command environment the model can inspect or spawn, using a broker or
equivalent OS/process boundary that performs authentication without revealing
the token.

### I1 — The retired `lesson-profile` browser state still exists (Info, confirmed)

The unit and MCP registration refer only to `chrome-profile`, but the retired
`lesson-profile` directory remains present and non-empty. No contents were
inspected. No active configuration reference was found, so this remains
private host cleanup rather than an application access path.

## PR #197 attack-surface analysis and non-findings

- The instruction/data boundary stays in the always-loaded core and precedes
  generated STATE. It classifies source, manifest metadata, learner text,
  artifacts, run output, prior pages, and STATE excerpts as data rather than
  directives, while retaining the no-symlink and text-only insertion rules
  (`app/services/lessons.py:1722-1758`). Moving exact API and manifest mechanics
  to companions does not move this trust boundary out of the core.
- The four companions are compile-time constants with no lesson-data slots
  (`app/services/lessons.py:1882-2447`). Their core pointers and writer map are
  pinned to the same four names. `reference` is a shared reserved first segment,
  so v2 manifest paths reject it and v1 preview routing withholds it
  (`app/services/bundle_schema.py:33-37,237-259`;
  `app/services/lessons.py:1151-1175`). `/reference/` is also excluded from the
  bundle's local git history (`app/services/lessons.py:85-97`).
- Workspace preparation writes the companions only on the server-selected
  lesson-agent path. A pre-existing link or non-directory on `reference` is
  moved aside before a real directory is made; foreign destination nodes are
  preserved, and each replacement is a mode-0600 same-directory temporary
  file which is flushed, fsynced, and renamed without opening the destination
  (`app/services/lessons.py:2497-2639,3070-3119`). This retains the previously
  accepted same-user post-validation window; it does not add a new principal
  or weaken the writer's stated best-effort boundary.
- The landed review fixes keep the complete rendered brief under Codex's
  32 KiB project-document cap: learner-controlled JSON excerpts are byte
  bounded, STATE is capped at 16 KiB on whole-line boundaries, and template
  substitution occurs before STATE insertion so learner text cannot create a
  second template slot (`app/services/lessons.py:1557-1575,2680-2717`). The
  data boundary and first open questions remain ahead of any truncation.
- The change adds no route, listener, terminal role, mount, child environment,
  browser capability, or network policy. It neither repairs nor worsens H1 or
  H2. No Critical, High, Medium, Low, or Info finding is attributable to PR
  #197 itself.

## Prior-condition reconciliation

- **Lesson brief stored-instruction and symlink findings — REMAIN RESOLVED.**
  Generated instruction files remain constant, final nodes are replaced rather
  than followed, and all runtime/learner content remains below the core data
  boundary.
- **Teaching-contract L1/L2/N1/N2/N3 and unknown-field drift — REMAIN
  RESOLVED.** The same rules are present across the core and companions, and
  the manifest companion matches the current reserved-name, path, artifact-root,
  unknown-field, identity, and version contracts.
- **Bundle-schema reserved-path, preview allowlist, manifest bounds, parser
  totality, and source collision behavior — REMAIN RESOLVED.** `source` and
  `reference` share the runtime reservation, and neither creates a served path.
- **Persistent-home, build-workspace, sandbox authority, server-owned role,
  fail-closed workspace/spawn, PTY lifecycle, Host/Origin, capability lifetime,
  and no-bare-shell fallback findings — REMAIN RESOLVED.** The companion change
  uses the existing agent workspace preparation seam and changes none of these
  controls.
- **Shared-browser High — STILL OPEN as H1.** It now covers both
  `lesson-agent` and `lesson-learner` because both share host networking.
- **Reusable-token High — STILL OPEN as H2.** The token remains restricted to
  the lesson-agent role, but that is the model-controlled role which ingests
  untrusted source.
- **Direct loopback/no forwarded headers — remains a mitigation, not a general
  boundary.** Wider, proxy-adjacent, multi-user, remote, or public exposure
  remains unsupported independently.

## Verification

- Live PR state — #197, #189, #177, #178, and #181 all confirmed merged at the
  hashes named above.
- `git diff --check 50feb27^ 50feb27`, `git diff --check d987ec1 50feb27`,
  `git diff --check 0b4f2a2^1 0b4f2a2`, `git diff --check 5096a50^ 5096a50`,
  `git diff --check 514b363^ 514b363`, and
  `git diff --check c83c093^ c83c093` — passed.
- `uv run ruff check` on the reviewed current Python surfaces and companion
  tests — passed.
- The first nested `uv run pytest` stalled at the known
  TestClient/bubblewrap boundary and was stopped without assertion output; it
  is excluded from evidence. The unchanged host rerun — **429 passed**, one
  existing TestClient deprecation warning.
- `uv run python verify_restore.py` on the host — **34 passed, 0 failed**.
- Metadata-only host/MCP checks — active loopback listener; one registered
  `playwright` server; Playwright `1.63.0-alpha-2026-08-05`; 24 tools including
  the destructive RCE-equivalent schema; exact-IP Host rejected; retired
  profile present and non-empty. No browser tool or private content was read.
- Final `git diff --check` and `python scripts/check_public_hygiene.py` —
  passed. Ignored-status inspection found only established local/runtime paths;
  the tracked/untracked review diff is this report plus `QUEUE.md`.

## Closing verdict

**NOT SAFE TO MAKE LIVE for the complete reviewed batch.** The current combined
surface has **0 Critical, 2 High, 0 Medium, 0 Low, and 1 Info** finding. PR #197
is clean for the documented direct-loopback, single-worker, unauthenticated
single-user deployment and its queue entry may move to Done. The 2026-08-09,
2026-08-11, and 2026-08-12 entries remain Pending under the queue's repair-and-
fresh-review rule because H1 and H2 are still confirmed; each receives this
report pointer. The deploy gate therefore remains closed. No application code,
tests, specification, private runtime state, browser state, or live service was
changed, restarted, or signalled.
