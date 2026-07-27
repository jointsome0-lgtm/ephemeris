# Terminal copy affordance — adversarial security review

**Date:** 2026-07-27

## Self-parameterization

- **Scope:** exactly one Pending entry existed at review start: the 2026-07-27
  terminal copy-affordance entry for commits `d1d487d` and `4b398e6` on
  `fix/terminal-copy-affordance`. It covers
  `app/static/src/terminal.ts`, emitted `app/static/terminal.js`,
  `app/templates/base.html`, `app/templates/learn.html`,
  `app/templates/_icons.html`, `app/static/style.css`, and `verify.py`, plus
  their direct xterm key-handler, clipboard-addon, localStorage,
  cross-document storage-event, drawer-template, icon-rendering, and static
  loading callers. The later branch commit `bf27cbb` changes only
  `docs/reviews/QUEUE.md`; it keeps the entry current and does not expand the
  application-code scope.
- **Starting HEAD:** `bf27cbbdbd925ea710e9f59a2242a29419ba1513`
  on `fix/terminal-copy-affordance`, equal to
  `origin/fix/terminal-copy-affordance`. `git status --short --branch` showed
  only the clean tracking line and no worktree change.
- **Fix target:** `git merge-base --is-ancestor d1d487d main`,
  `git merge-base --is-ancestor 4b398e6 main`, and
  `git merge-base --is-ancestor bf27cbb main` each returned nonzero. The
  queued change is not merged, so any drain fix belongs on
  `fix/terminal-copy-affordance`.
- **Report file:**
  `docs/reviews/2026-07-27-terminal-copy-affordance-review.md`, derived from
  the entry's terminal copy-affordance subject.
- **Prior reports to reconcile:** every existing
  `docs/reviews/*-review.md` closing verdict was scanned. The most recent
  binding report for the same static/template surface is
  `2026-07-22-terminal-surfaces-review.md`: generated source/served-runtime
  parity, the two independently namespaced terminal surfaces, deliberate
  learner connection, server-accepted role identity, bounded learner
  persistence, and terminal-tab L1 are resolved. Terminal-opt-in T1 remains
  resolved for `lesson-agent` and `lesson-learner` but open/accepted for the
  deliberately plain owner shell. Trusted-agent host network, CLI login
  material, `SSH_AUTH_SOCK`, and proxy credentials remain accepted only for
  that role. The direct/no-forwarded-header condition remains mitigated by
  the documented loopback deployment; wider and proxy-adjacent deployment
  remain excluded. `2026-07-27-platform-support-review.md` most recently
  confirms the server-side terminal, sandbox, runner, attempt, and bridge
  protections retain those dispositions. The closing verdict below states
  explicitly whether each condition is resolved, mitigated, open/accepted,
  or unchanged for this change.
- **Validation baseline:** at the starting HEAD, approved host
  `python verify.py` — **767 passed, 0 failed**; approved host
  `python verify_restore.py` — **28 passed, 0 failed**. The initial restricted
  run reproduced the known silent TestClient/bubblewrap boundary stall after
  its first six passing startup checks and was interrupted without an
  assertion failure; only the completed host runs supply the baseline counts.

## Context and method

The deployment decision assumes the documented unauthenticated,
single-worker application bound directly to `127.0.0.1:8765`, with no
forwarded-header rewriting. The live service was not restarted or signalled,
and no live database, bundle, export, browser profile, screenshot, credential,
or private runtime path was read or changed.

The exact implementation range `d1d487d^..4b398e6` and every listed source,
emitted runtime, template, icon, style, and verifier change were read with the
complete terminal client and its template-loading and WebSocket server callers.
Static review traced keyboard-event return semantics, clipboard read/write
direction, selection deduplication, missing and failed browser APIs, storage
failure and cross-document synchronization, per-surface key derivation,
same-document agent/learner initialization, DOM and SVG sinks, minimized and
find-bar layout, generated-runtime parity, local template gating, server role
acceptance, and the loopback Host/Origin gate.

An executable Node harness loaded the committed `terminal.js` with isolated
browser primitives and exercised both surface buttons, sibling-document
storage events, storage clearing, every selection/no-selection
Ctrl+C/Ctrl+Shift+C branch, copy-on-select deduplication and live flag
re-reading, and the agent WebSocket selector. A throwaway Uvicorn child bound
to `127.0.0.1:8877` with only an invented `/tmp` data directory confirmed the
actual rendered button, icon, shortcut text, cache-busted script reference,
and served JavaScript. That child was stopped after the checks; it was not the
systemd service.

## Findings

No Critical, High, Medium, Low, Info, or other finding was found.

## Confirmed protections and rebutted candidates

- **Selection-only copy preserves terminal input semantics.** Both Ctrl+C and
  Ctrl+Shift+C enter the copy branch only with Ctrl held and Alt/Meta absent.
  A selection is sent only to `navigator.clipboard.writeText()` and the event
  returns `false`; without a selection the handler returns `true`, preserving
  xterm's existing input path and SIGINT behavior
  (`app/static/src/terminal.ts:359-380`). The executable harness confirmed all
  four combinations and exact selected-text writes.
- **The existing clipboard boundary remains write-only for OSC 52.**
  `writeOnlyClipboardProvider()` still returns no clipboard content to the
  PTY and accepts only the `c` selection for terminal-originated writes
  (`app/static/src/terminal.ts:402-409`). The change does not grant terminal
  output a clipboard-read capability. Paste remains the pre-existing explicit
  Ctrl+Shift+V browser read.
- **Copy on select stays explicit, off by default, and surface-scoped.** The
  keys remain `al-term-copyselect` and
  `al-term-learner-copyselect`; the button toggles only its surface key and
  reflects the value read back from storage
  (`app/static/src/terminal.ts:847-869`). If storage access throws, reads fail
  closed to off and a failed write cannot produce a false on presentation.
  Selection handling re-reads the flag at each change and suppresses a
  duplicate unchanged selection (`app/static/src/terminal.ts:383-398`).
- **Cross-document synchronization grants no new authority.** A storage event
  for another key returns without changing the button; the matching surface
  re-reads its own key, and a `clear()` event re-reads both. Storage contents
  never select a session, lesson, role, URL, or DOM markup. A same-origin
  document able to write these keys could already invoke the loopback terminal
  UI and WebSocket subject to the independent server gates; this is state
  coherence, not a privilege boundary.
- **The new markup is local-only and inert.** Both buttons have static IDs,
  shortcut text, `aria-pressed`, and the constant `copy` icon
  (`app/templates/base.html:103-136`,
  `app/templates/learn.html:176-200`). The icon macro selects a constant SVG
  fragment by a literal name; no runtime or stored value enters the safe SVG
  sink. The new style only presents the active state and hides the button in
  the minimized right dock (`app/static/style.css:381-412`).
- **Missing clipboard support or a rejected clipboard promise is a bounded UX
  failure, not a shell or data-boundary bypass.** The wrappers catch absent,
  throwing, and rejected browser APIs without falling back to DOM insertion,
  PTY output, or another storage location
  (`app/static/src/terminal.ts:330-356`). No error feedback is added, but under
  the documented single-user loopback posture that is not a security finding.
- **The served generated client is the reviewed client.** Strict TypeScript
  compilation, `--noEmit`, JavaScript syntax checking, and a fresh scratch
  emit passed. The fresh `terminal.js` and unchanged `learn-bridge.js` were
  byte-identical to their committed served files. CI still installs the pinned
  compiler before the verifier's byte comparison (`verify.py:1419-1443`).
- **Server authority is unchanged and remains independent of client
  presentation.** Terminal markup still requires the explicit opt-in plus a
  loopback request (`app/templates/base.html:103-137`,
  `app/terminal.py:136-142`). Every WebSocket still requires a loopback peer,
  loopback Host, and exact Host/Origin tuple before accept
  (`app/terminal.py:154-181`); session role/workspace/profile remain
  server-owned, and the handshake still reports the accepted role before the
  client trusts it (`app/terminal.py:875-973`). No PTY, fd, session, sandbox,
  listener, or application-lifecycle code changed.

## Prior-condition reconciliation at closing tree

- **E4 two-surface terminal client and generated-runtime parity — REMAIN
  RESOLVED.** Agent and learner drawers retain independent state keys and
  server-accepted role identity. The new control is present for each surface
  without merging their tab, session, role, lesson, or copy-on-select state.
  Fresh TypeScript emission remains byte-identical to the served runtime.
- **Terminal-tab L1 — REMAINS RESOLVED.** This change does not touch tab
  selection, off-Learn fallback, close-successor behavior, connection intent,
  or session persistence. The copy button cannot open, select, create, attach,
  or reconnect a tab.
- **Terminal-opt-in T1 — REMAINS RESOLVED for `lesson-agent` and
  `lesson-learner`; REMAINS OPEN/ACCEPTED for the deliberately plain owner
  shell.** The two lesson roles retain their sandbox profiles. Clipboard
  presentation does not represent the plain same-UID shell as isolated.
- **Trusted-agent host network, CLI login material, `SSH_AUTH_SOCK`, and proxy
  credentials — REMAIN UNCHANGED/ACCEPTED for that role only.** The learner
  surface continues to request only `lesson-learner`; the no-network learner
  profile and environment stripping are unchanged.
- **Direct/no-forwarded-header condition — REMAINS MITIGATED by the documented
  deployment.** The loopback peer and exact Host/Origin gates are unchanged.
  Proxy-adjacent deployment remains excluded.
- **Terminal F1-F4, opt-in wiring, workspace refusal, PTY/fd ownership,
  attach/reaper serialization, relative-path display, atomic briefs, sandbox
  authority, runner core/API, editor/run membrane, and generated activation
  protections — REMAIN RESOLVED for the documented deployment.** No server,
  sandbox, runner, bridge, persistence, filesystem, or listener path changed.
- **Attempt-backend A1/A2 — REMAIN RESOLVED; bridge-runtime D5 L1 — REMAINS
  MITIGATED; D5 L2/L3 — REMAIN RESOLVED.** This entry changes no request
  admission, attempt authority/projection, iframe, page-byte, identity, or
  operation-admission path.

## Closing verification

- Starting HEAD `bf27cbb`: approved host `python verify.py` —
  **767 passed, 0 failed**; approved host `python verify_restore.py` —
  **28 passed, 0 failed**.
- `git diff --check d1d487d^..4b398e6` — passed.
- `./node_modules/.bin/tsc -p . --noEmit` and
  `node --check app/static/terminal.js` — passed.
- Fresh scratch TypeScript emit — committed `terminal.js` and
  `learn-bridge.js` were byte-identical.
- Executable terminal-copy harness — **21 passed**, covering surface defaults
  and isolation, matching and clear storage events, the four copy/SIGINT
  branches, exact clipboard writes, copy-on-select deduplication/live disable,
  and the unchanged non-learner agent selector.
- Throwaway loopback render/serve checks — passed; the full-shell template
  emitted the expected static button/icon/labels and the generated client was
  served with the existing security headers.
- No drain fix commit was required; this drain consumed **0 of 10** cycles.

## Closing verdict

**SAFE TO MAKE LIVE for the documented direct-loopback
`127.0.0.1:8765`, single-worker, unauthenticated single-user deployment.**
This entry has **0 Critical, 0 High, 0 Medium, 0 Low, and 0 Info** findings and
no open finding. The E4 two-surface client, generated-runtime, terminal-tab,
server/workspace/sandbox, runner, attempt, and bridge protections retain the
explicit dispositions above; terminal-opt-in T1 remains accepted only for the
deliberately plain owner shell, and the trusted agent's intentional network
and credential posture is unchanged. Wider, proxy-adjacent, or multi-user
deployment remains **NO**. The queue entry may move to Done. Restarting the
live service remains the owner's action and was not performed by this review.
