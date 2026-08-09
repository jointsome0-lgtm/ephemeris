# Terminal prompt and lesson build batch — adversarial security review

**Date:** 2026-08-09

## Review parameters

- **Scope:** every Pending entry present at review start: squash `2552323`
  of `term-quiet-prompt`, PR #172, and squash `8a4e3ee` of
  `feat/161-build-step`, PR #165. Their executable diffs are
  `2552323^..2552323` and `8a4e3ee^..8a4e3ee`, covering the paths and direct
  callers named by both queue entries. Later squash `cfa04b8` changed the
  current lesson-build implementation and tests on the same listed feature
  surface; those current bytes were reviewed so this verdict describes the
  tree that would run, not a historical copy of `8a4e3ee`.
- **Starting HEAD:** clean `main` at
  `9cee2e57b9613e8891a9d9e0e9475184a9aa4c32`. After a fresh fetch,
  `origin/main` was `9899114132f26ed76bbd09e5064c12cef999efb6`, the direct
  parent of the starting HEAD; the one local-only commit merely records the
  landed #172 squash hash in this queue.
- **Reviewed tree:** both queue hashes resolve as repository commits and are
  ancestors of the starting HEAD. `2552323` resolves to
  `255232393628d64298dbc77e80596908d8606a86` with parent `66acf04`; `8a4e3ee`
  resolves to `8a4e3ee1bb28e19082eefe338879823f82255547` with parent `3b0936f`.
  Each is a one-parent squash commit, as the entries state. No Pending entry
  was out of scope.
- **Report file:**
  `docs/reviews/2026-08-09-terminal-prompt-and-lesson-build-review.md`.
- **Prior reports reconciled:** the 2026-08-09 lesson-build-workspace,
  2026-08-08 persistent-agent-home, 2026-08-02 terminal-job-control,
  E1/E2/E3 sandbox, terminal-opt-in, and CSP-profile reports were re-read for
  the same package workspace, backup, build/render, child environment,
  banner, role, mount, browser-policy, and deployment surfaces. Their binding
  conditions are stated below.
- **Validation baseline at the starting HEAD:** `uv run pytest` on the host —
  **384 passed**, one existing TestClient deprecation warning;
  `uv run python verify_restore.py` on the host — **34 passed, 0 failed**. The
  repair must preserve or grow both counts. The first pytest run inside the
  nested reviewer sandbox stalled at the known TestClient/PTY boundary and
  was interrupted without assertion output; it is not counted.

## Context and method

The deployment decision assumes the documented unauthenticated, single-worker
application bound directly to `127.0.0.1:8765` with no forwarded-header
rewriting. The lesson-agent remains a trusted owner tool with host networking
and deliberately re-exposed CLI credentials; this report does not turn it into
a boundary for a less-trusted principal.

Both squash diffs and the current changed functions were read in full, together
with the complete new build and render-check modules, their route, workspace,
sandbox, terminal, bundle-reader, backup/archive, CSP, and test callers. Static
review traced child-environment construction and banner bytes; package-spec and
path admission; install and bundle mount views; cache, macro, and configuration
authority; subprocess cancellation and resource scopes; output placement,
render acceptance, and rollback; browser diagnostics; backup inclusion; and
the wider-listener case. Commit and queue prose were treated as claims to check
against the landed source.

Focused probes used only invented files under throwaway directories. No live
database, lesson bundle, package tree, browser profile, credential, screenshot,
or service was read, changed, restarted, or signalled.

## Findings

### L1 — A concurrent output replacement is deleted on refusal or accepted without being rendered (Low, confirmed)

The per-slug lock serialises build requests, but it does not and cannot stop the
live lesson-agent session from writing its own bundle while a build is in
progress. The build renames the old output aside, places the candidate under the
final served name, and releases the event loop while the browser checks it
(`app/services/lesson_build.py:947-973`). On failure it unconditionally unlinks
whatever the final name holds before restoring the aside copy; on success it
unconditionally deletes the aside copy without confirming that the final name
still identifies the bytes the browser checked (`app/services/lesson_build.py:974-990`).

An invented-data probe paused at that exact seam and atomically replaced
`assets/page.js`, as the still-running lesson session may. In the refusal case,
the gate observed `built artifact`; the replacement wrote `concurrent authored
bytes`; the response took the `render-errors` path; and the final file was the
older `old artifact`, with the concurrent authored bytes gone. In the success
case the gate again observed `built artifact`, the replacement wrote
`concurrent unrendered bytes`, and the build returned `ok: true` while the final
file differed from the bytes accepted by the gate.

This is bounded to one caller-selected output in its own lesson and requires a
same-user concurrent write, so it is Low rather than Medium. It is nevertheless
concrete data loss and a broken acceptance claim in the supported deployment.
A repair should render a staged immutable snapshot and publish it atomically
only after acceptance, or otherwise bind both rollback and success to the exact
inode/version placed by this build; it must preserve a concurrent replacement
rather than deleting it or reporting it as rendered.

### L2 — Full backups still silently omit package-tree symlinks (Low, confirmed prior condition)

The current install path populates the persistent private workspace at
`lesson-builds/<slug>/node_modules`, and package trees normally include internal
links such as executable shims. The full-backup walker includes ordinary files
by exclusion but prunes directory symlinks and skips file symlinks without
listing them as archived, vanished, or excluded
(`scripts/backup_db.py:360-415`). Verification therefore proves only the
already-incomplete manifest, not faithful recovery of the package tree.

A fresh invented workspace probe against the current walker listed
`lesson-builds/invented-lesson/node_modules/demo-tool/index.js` and omitted
`.bin/demo-tool -> ../demo-tool/index.js`. This re-establishes the Low first
reported by `2026-08-09-lesson-build-workspace-review.md`; the build-step squash
does not resolve or mitigate it, and now supplies the package-manager path that
creates such trees in ordinary use.

A dedicated repair should either preserve validated in-tree links safely, or
declare `lesson-builds` reconstructible and exclude it explicitly from backup
and restore with matching documentation and regression coverage. Silent partial
recovery must not remain the contract.

## Confirmed protections and non-findings

- The prompt change adds only one of two fixed `PROMPT_COMMAND` values, only to
  server-selected lesson roles whose selected shell basename is `bash`.
  `plain` and non-Bash shells receive no new variable, the base environment
  remains allowlisted, and no lesson, workspace, proxy, or client value is
  interpolated (`app/terminal.py:429-494,825-903`).
- The combined banner is assembled from the closed role enum, fixed text, and
  the proxy URL after both userinfo redaction and printable-character filtering.
  The workspace path is no longer emitted. Session registration, PTY ownership,
  capability cleanup, scrollback bounds, and start order are unchanged
  (`app/terminal.py:923-1033`).
- Package names are bounded and cannot become flags; the app-owned install argv
  carries both `--minimum-release-age=2592000` and `--backend=copyfile`, with
  lifecycle scripts disabled. The install view alone gets network, cache, and
  writable workspace; the bundle view gets no network or cache, a read-only
  workspace and bundle, and only `out/` writable. Both process trees retain
  aggregate task, memory, swap, and wall-clock bounds.
- Source, output, and page references retain the bundle grammar and exact v2
  page selection. Symlinked sources are refused, module-graph inputs are checked
  before output placement, generated bytes and diagnostics are bounded, and a
  missing or incomplete render check fails closed. The route body is capped and
  the browser URL is derived from the accepted socket rather than Host.
- The tokenless build route does not create a new remote-authentication claim.
  Origin-less owner and trusted-agent requests remain admitted by the existing
  v0 perimeter, while cross-origin and opaque-origin lesson-page requests are
  refused. That posture would be unacceptable for a wider or less-trusted
  deployment, which remains unsupported independently.
- The later removal of the vendored library shelf leaves `assets/` author-owned
  and does not weaken the build sandbox, output-path grammar, quarantine, or
  copying-cache boundary. It does not address either finding above.

## Prior-condition reconciliation

- **Lesson-build workspace mount, authority, collision, and role conditions —
  REMAIN RESOLVED.** The workspace is still server-derived outside the bundle;
  only the trusted lesson-agent receives its `node_modules` bind; learner and
  runner reject it; preparation and sandbox failures remain fail-closed.
- **The prior package-tree backup Low — STILL OPEN as L2.** Regular files remain
  included, but internal symlinks are silently absent. The separate
  file-by-file, non-point-in-time filesystem snapshot limitation also remains
  **OPEN/ACCEPTED** as documented; neither condition is hidden by the 34/34
  restore verifier.
- **Persistent-agent-home and E1/E2/E3 mount, home-overlay, role-separation,
  and fail-closed conditions — REMAIN RESOLVED.** Read-only credential and
  configuration overlays retain precedence. The trusted lesson-agent's host
  network, CLI login material, SSH agent, proxy, and persistent state remain
  **OPEN/ACCEPTED only for that single-user role**; no less-trusted boundary is
  claimed.
- **Terminal F1-F4, tab scoping, exact Host/Origin trust, child job control,
  attach/reaper serialization, fd cleanup, assessment-capability lifetime, and
  environment allowlisting — REMAIN RESOLVED.** The prompt and banner diff does
  not alter those seams. Terminal-opt-in T1 remains **RESOLVED for both
  sandboxed lesson roles** and **OPEN/ACCEPTED for the deliberately plain owner
  shell**.
- **CSP profile C1 and exact loaded-revision/bridge binding — REMAIN RESOLVED.**
  The render gate consumes the same effective-profile response. Chromium's
  documented WebRTC and same-frame-navigation residual remains
  **OPEN/ACCEPTED for direct loopback** rather than being represented as closed;
  this batch does not widen it.
- **Direct loopback with no forwarded-header rewriting — REMAINS A MITIGATION,
  not a general boundary.** Wider, proxy-adjacent, multi-user, remote, or
  less-trusted deployment remains **NO**.

## Verification

- Commit resolution, parentage, ancestry, and current-tree checks — passed as
  recorded in the review parameters.
- `git diff --check 2552323^ 2552323` and
  `git diff --check 8a4e3ee^ 8a4e3ee` — passed.
- Starting-head `uv run pytest` on the host — **384 passed**, one existing
  TestClient deprecation warning.
- Starting-head `uv run python verify_restore.py` on the host — **34 passed,
  0 failed**.
- Invented output/render race probes — confirmed both failure modes: rollback
  discarded a concurrent replacement, and success accepted final bytes the gate
  had not rendered.
- Invented current-backup enumeration probe — regular package file included,
  sibling `.bin` symlink omitted without a manifest record.
- `git diff --check` and `python scripts/check_public_hygiene.py` — passed after
  this report and both queue pointers; ignored-status inspection found no new
  tracked private/runtime material.

## Closing verdict

**NOT SAFE TO MAKE LIVE for the current Pending batch.** The reviewed tree has
**0 Critical, 0 High, 0 Medium, 2 Low, and 0 Info** findings. The terminal
prompt/banner change introduces no finding, and the build sandbox, route,
resource, and diagnostic bounds remain intact for the documented
direct-loopback, single-worker, unauthenticated single-user posture. L1 still
permits silent loss or false acceptance when the live lesson session replaces
the output during its render gate, and L2 still makes a verified full backup
silently incomplete for ordinary package trees.

Both queue entries remain **Pending** and point to this report. A separate,
ordinarily reviewed repair must close L1 and L2, add regressions for the exact
failure modes, preserve or grow the **384** pytest and **34/34** restore
baselines, and receive a fresh drain before the entries can move to Done.
Wider, proxy-adjacent, multi-user, remote, or less-trusted deployment remains
**NO** independently. No live service action was performed; restart remains
the owner's action after the Pending gate is empty.
