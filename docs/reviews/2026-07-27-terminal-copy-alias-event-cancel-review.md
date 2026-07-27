# Terminal copy alias event cancellation — adversarial security review

**Date:** 2026-07-27

## Self-parameterization

- **Scope:** exactly one Pending entry existed at review start: the 2026-07-27
  terminal copy alias event-cancellation entry for `c159a2b` on
  `fix/terminal-copy-affordance`. It covers
  `app/static/src/terminal.ts`, emitted `app/static/terminal.js`, and
  `verify.py`, plus the direct xterm custom-key-handler, clipboard, generated
  static, template-loading, and terminal WebSocket callers. The later branch
  commit `0d32ad` changes only `docs/reviews/QUEUE.md`; it records this entry
  and does not expand the application-code scope.
- **Starting HEAD:** `0d32ad03582cca8fc27ff3ba665d1998dead80f2` on
  `fix/terminal-copy-affordance`, equal to
  `origin/fix/terminal-copy-affordance`. `git status --short --branch` showed
  only the clean tracking line and no worktree change.
- **Fix target:** `git merge-base --is-ancestor c159a2b main` and
  `git merge-base --is-ancestor 0d32ad main` both returned nonzero. The queued
  change is not merged, so any drain fix belongs on
  `fix/terminal-copy-affordance`.
- **Report file:**
  `docs/reviews/2026-07-27-terminal-copy-alias-event-cancel-review.md`, derived
  from the entry's copy-alias event-cancellation subject and kept distinct
  from the earlier same-day terminal copy-affordance report.
- **Prior reports to reconcile:** every existing
  `docs/reviews/*-review.md` closing verdict was scanned. The immediately
  preceding and most specific report is
  `2026-07-27-terminal-copy-affordance-review.md`; it binds this follow-up to
  the generated-runtime parity, two independently namespaced terminal
  surfaces, terminal-tab L1, server-owned role and workspace authority, and
  the terminal opt-in and deployment conditions stated there.
  `2026-07-22-terminal-surfaces-review.md` is the originating same-surface
  report, and `2026-07-27-platform-support-review.md` most recently confirms
  the server-side terminal, sandbox, runner, attempt, and bridge protections.
  The closing reconciliation below states explicitly whether each named
  condition remains resolved, mitigated, or open/accepted for this change.
- **Validation baseline:** at the starting HEAD, approved host
  `python verify.py` — **767 passed, 0 failed**; approved host
  `python verify_restore.py` — **28 passed, 0 failed**.

## Context and method

The deployment decision assumes the documented unauthenticated, single-user,
single-worker app bound directly to `127.0.0.1:8765`, with no forwarded-header
rewriting. The live service was not restarted or signalled, and no live
database, bundle, export, browser profile, screenshot, credential, or private
runtime path was read or changed.

The exact `c159a2b^..c159a2b` diff, the complete TypeScript terminal client,
the committed emitted client, the changed verifier block, and their direct
template, xterm, clipboard, and terminal-server callers were reviewed. The
vendored xterm 5.5.0 `_keyDown` implementation was checked directly: when its
custom key handler returns `false`, `_keyDown` returns before xterm's own
`cancel()` call, so browser-default cancellation must happen in the custom
handler.

An isolated executable Node harness loaded the committed `terminal.js` with
invented browser state and captured the real installed custom key handler. It
exercised selected and empty Ctrl+C/Ctrl+Shift+C events, clipboard writes,
return values, and each event's `defaultPrevented` state, alongside the
existing two-surface storage, copy-on-select, and WebSocket-selector checks.

## Findings

No Critical, High, Medium, Low, Info, or other finding was found.

## Confirmed protections and rebutted candidates

- **The alias now cancels the browser default at the necessary seam.** With a
  selection, Ctrl+Shift+C writes exactly the selected text, calls
  `preventDefault()`, and returns `false` to xterm
  (`app/static/src/terminal.ts:367-379`). The executable harness confirmed both
  cancellation signals independently. The vendored xterm implementation
  confirms that the explicit `preventDefault()` is necessary because its
  custom-handler early return precedes xterm's own cancellation routine.
- **Plain Ctrl+C behavior is unchanged.** With a selection it follows the same
  explicit copy path and returns `false`, but does not call
  `preventDefault()`; without a selection it returns `true` so xterm can still
  deliver SIGINT. The harness confirmed the exact return and
  `defaultPrevented` values for both cases.
- **The no-selection alias path is unchanged.** Ctrl+Shift+C with no selection
  returns `true` and does not cancel the event. The entry deliberately changes
  only the copy path and does not turn the alias into a terminal-input
  suppression rule.
- **Clipboard authority is not widened.** The new call is only event
  cancellation after the existing selection-only
  `navigator.clipboard.writeText()` request. It adds no clipboard read,
  terminal output, DOM, storage, session, URL, or WebSocket capability.
  The write-only OSC 52 provider remains unchanged.
- **Source and served runtime remain one reviewed implementation.** Strict
  TypeScript checking and JavaScript syntax checking passed. A fresh scratch
  TypeScript emit was byte-identical to the committed `terminal.js`; the
  unchanged `learn-bridge.js` was also byte-identical. The clean-CI toolchain
  and byte-comparison gate established by terminal-surfaces M1 remain active.
- **Server authority remains independent of the client shortcut.** Template
  rendering still requires terminal opt-in plus a local client, and each
  WebSocket still enforces loopback peer, Host, and Origin checks before
  server-owned role, workspace, profile, session, PTY, and fd handling. No
  Python runtime, listener, sandbox, persistence, or lifecycle path changed.

## Prior-condition reconciliation at closing tree

- **E4 two-surface terminal client and generated-runtime parity — REMAIN
  RESOLVED.** Both surfaces use the same corrected handler without merging
  their tab, role, lesson, session, or copy-on-select state. Fresh emission is
  byte-identical to the served client.
- **Terminal-tab L1 — REMAINS RESOLVED.** The change cannot open, select,
  create, attach, close, or reconnect a tab and does not touch off-Learn
  fallback or persisted active-tab state.
- **Terminal-opt-in T1 — REMAINS RESOLVED for `lesson-agent` and
  `lesson-learner`; REMAINS OPEN/ACCEPTED for the deliberately plain owner
  shell.** The lesson roles retain their sandbox profiles. Browser event
  cancellation does not represent the plain same-UID shell as isolated.
- **Trusted-agent host network, CLI login material, `SSH_AUTH_SOCK`, and proxy
  credentials — REMAIN UNCHANGED/ACCEPTED for that role only.** The learner
  surface still requests only `lesson-learner`; no role or environment path
  changed.
- **Direct/no-forwarded-header condition — REMAINS MITIGATED by the documented
  deployment.** The loopback peer and exact Host/Origin gates are unchanged.
  Proxy-adjacent deployment remains excluded.
- **Terminal F1-F4, opt-in wiring, workspace refusal, PTY/fd ownership,
  attach/reaper serialization, relative-path display, atomic briefs, sandbox
  authority, runner core/API, editor/run membrane, and generated activation
  protections — REMAIN RESOLVED for the documented deployment.** This entry
  changes only the client key event's browser-default cancellation and its
  verifier anchor.
- **Attempt-backend A1/A2 — REMAIN RESOLVED; bridge-runtime D5 L1 — REMAINS
  MITIGATED; D5 L2/L3 — REMAIN RESOLVED.** This entry changes no request
  admission, attempt authority/projection, iframe, page-byte, identity, or
  operation-admission path.

## Closing verification

- Starting HEAD `0d32ad`: approved host `python verify.py` —
  **767 passed, 0 failed**; approved host `python verify_restore.py` —
  **28 passed, 0 failed**.
- `git diff --check c159a2b^..0d32ad` — passed.
- `./node_modules/.bin/tsc -p . --noEmit` and
  `node --check app/static/terminal.js` — passed.
- Fresh scratch TypeScript emit — committed `terminal.js` and
  `learn-bridge.js` were byte-identical.
- Executable terminal-copy harness — **25 passed**, including the four
  selection/no-selection Ctrl+C/Ctrl+Shift+C branches and their exact
  `defaultPrevented` states.
- Final exact-head approved host `python verify.py` —
  **767 passed, 0 failed**; approved host `python verify_restore.py` —
  **28 passed, 0 failed**.
- `python scripts/check_public_hygiene.py` and ignored-status inspection —
  passed.
- No drain fix commit was required; this drain consumed **0 of 10** cycles.

## Closing verdict

**SAFE TO MAKE LIVE for the documented direct-loopback
`127.0.0.1:8765`, single-worker, unauthenticated single-user deployment.**
This entry has **0 Critical, 0 High, 0 Medium, 0 Low, and 0 Info** findings and
no open finding. The alias now cancels its browser default without changing
plain Ctrl+C, either no-selection path, clipboard authority, terminal input,
or server/session authority. The E4 two-surface client, generated-runtime,
terminal-tab, server/workspace/sandbox, runner, attempt, and bridge protections
retain the explicit dispositions above; terminal-opt-in T1 remains accepted
only for the deliberately plain owner shell, and the trusted agent's
intentional network and credential posture is unchanged. Wider,
proxy-adjacent, or multi-user deployment remains **NO**. The queue entry may
move to Done. Restarting the live service remains the owner's action and was
not performed by this review.
