# Terminal tab scoping — adversarial security review

**Scope:** commit `9747fc9` on `fix/37-terminal-tab-scoping`, reviewed against
base `ab8fd29`. The executable change is confined to
`app/static/terminal.js`: the active-tab pointer is split into durable and
in-memory values, lesson tabs are not auto-active or auto-connected outside
Learn, and explicit tab switches can still connect them.

**Context:** v0 has no authentication. Per `AGENTS.md`, the live instance is
assumed to bind directly to loopback only. A terminal WebSocket grants a shell
with the service user's permissions; the client-side Learn distinction is an
interaction boundary, not a substitute for the server's peer and same-origin
trust gate.

**Method:** diffed `ab8fd29..9747fc9`, read `app/static/terminal.js` in full and
read its template and WebSocket/server call paths. Earlier reports for the
multi-session client, lesson-scoped sessions, terminal disable switch, and
environment rename were re-checked against current source. An invented-state
headless-browser harness exercised off-Learn boot and tab closure without using
runtime data or a real lesson name.

**Verdict:** one Low client-side policy gap is confirmed. The commit does not
regress the server trust gate or the earlier PTY ownership/lifecycle fixes, and
there is no blocker for the assumed direct-loopback deployment.

## Findings

### L1 — Closing a plain tab off Learn can implicitly connect a lesson tab (Low, confirmed)

The new startup policy is correctly enforced in the bulk-connect path:
off Learn, a stored lesson-active tab is replaced in memory by the first plain
tab (`app/static/terminal.js:90-113`), and `connectAllTabs()` skips lesson tabs
(`app/static/terminal.js:417-424`). The skip does not cover every internal path
into `switchTab()`, however.

`closeActiveTab()` removes the current tab, selects the adjacent remaining tab,
and then calls `switchTab(activeId)` (`app/static/terminal.js:582-607`). With the
drawer open, `switchTab()` unconditionally creates that tab's runtime and calls
`connectTab()` (`app/static/terminal.js:519-535`). `connectTab()` includes the
lesson slug and stored session id in the WebSocket URL
(`app/static/terminal.js:380-389`). Therefore closing the active plain tab can
select and connect a neighboring lesson tab off Learn even though the user did
not explicitly activate that lesson tab.

The browser harness seeded two invented tabs, with a lesson tab durably active
and a plain tab available. Off-Learn boot made the plain tab effective and
opened only its WebSocket, as intended. Clicking **Close active terminal** then
opened a second WebSocket carrying the invented lesson slug and persisted the
lesson tab as active. This re-verifies the interrupted pass's report from
executed behavior rather than relying on it.

Impact is limited: a local user must deliberately close the active terminal,
the server still independently enforces loopback and same-origin access, and no
cross-user or remote boundary is crossed. The result can nevertheless
reattach—or, after reaping, create—a lesson-scoped shell outside the Learn
context, contrary to the new "explicit switch still connects" boundary. That
can resume lesson-directory activity unexpectedly and makes the UI's context
separation unreliable.

*Fix direction:* make connection intent explicit. An internal fallback after
close should either select another plain tab, leave a selected lesson tab
disconnected off Learn, or call a switching primitive that connects lesson tabs
only for direct user activation. Keep the fix in its own terminal-client change.

## Verified behavior and non-findings

- **Durable/effective active split:** the off-Learn fallback changes only
  `activeId`, while `persistTabs()` writes only `storedActiveId`
  (`app/static/terminal.js:36-50`, `app/static/terminal.js:116-121`). Incidental
  title or session-id persistence therefore does not overwrite the durable
  Learn selection.
- **Normal off-Learn boot:** `connectAllTabs()` excludes every lesson tab before
  runtime or WebSocket creation (`app/static/terminal.js:417-424`). The browser
  harness confirmed that only the plain tab connected before the close action.
- **No new input-to-HTML sink:** tab titles and ids loaded from storage continue
  through `cleanTitle()`, and titles are rendered with `textContent`, not HTML
  (`app/static/terminal.js:68-85`, `app/static/terminal.js:140-158`). The commit
  adds no script-execution surface.
- **No client-side trust assumption reaches the shell directly:** a client
  connection still goes through `_ws_is_trusted()` before acceptance; it
  requires a loopback peer, loopback Host, and exact Host/Origin tuple match
  (`app/terminal.py:81-108`). A lesson value affects only creation of a new
  session after that gate (`app/terminal.py:557-579`).
- **Earlier F1–F4 fixes remain present:** detach wakes stale writers
  (`app/terminal.py:258-265`); PTY writes re-check socket ownership around waits
  (`app/terminal.py:476-513`); binary and control frames share the ownership
  check (`app/terminal.py:516-552`); and the reaper excludes attach-locked
  sessions while attach stays serialized (`app/terminal.py:367-386`,
  `app/terminal.py:582-604`). `app/terminal.py` is identical at base and head.
- **Earlier lesson symlink fix remains present:** workspace preparation checks
  the bundle directory before writes and opens the generated brief with
  `O_NOFOLLOW` (`app/services/lessons.py:390-412`,
  `app/services/lessons.py:417-443`). That server/service code is also identical
  at base and head.
- **Previously documented operational findings:** the launch/proxy-header and
  environment-rename migration findings in the earlier reports are neither
  worsened nor fixed by this client-only commit.

## Verification

- `node --check app/static/terminal.js` — passed.
- Headless Chrome harness with invented local-storage state — reproduced L1 and
  confirmed the normal off-Learn bulk-connect skip.
- `git diff --check ab8fd29 9747fc9 -- app/static/terminal.js` — passed.
- Public-hygiene checks are recorded in the review handoff; no runtime data,
  exports, screenshots, or authenticated artifacts were used.

## Deploy verdict

**Direct-loopback deployment: YES, with one Low follow-up.** L1 does not bypass
the terminal's security gate, but it should be fixed before off-Learn lesson-tab
non-connection is treated as a complete interaction invariant. A wider bind is
still unsupported regardless of this commit.
