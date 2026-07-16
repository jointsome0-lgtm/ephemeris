# Terminal opt-in and fail-closed workspace — adversarial security review

**Scope:** commit `61b6d65` — terminal registration changed from opt-out to
explicit opt-in; lesson-scoped session creation now refuses an unavailable
workspace instead of falling back to the repository root; child shells start
from an environment allowlist; proxy-banner userinfo is redacted; lesson paths
shown by the Learn page and preview-meta endpoint became bundle-relative; and
the example systemd unit gained restrictive defaults. The commit's full diff,
the complete terminal and lesson-service modules, relevant application,
middleware, template, client, and shutdown call paths, and both verifier changes
were read. `README.md` and `docs/security-model.md` were included because they
are touched by the commit even though the queue entry omits them from its path
list.

**Context:** v0 has no authentication. Per `AGENTS.md`, the live instance is
assumed to bind directly to loopback only; a terminal-enabled Uvicorn must not
use proxy-header rewriting. The embedded terminal grants a shell with the
service user's permissions, so every finding would have greater impact if the
port or terminal trust boundary were widened.

**Method:** diffed `61b6d65^..61b6d65`, read the touched surfaces and callees,
and re-checked earlier reports for terminal registration, Host/Origin trust,
PTY ownership/lifecycle, lesson workspace containment, generated briefs, and
the central security middleware. Fresh-process probes exercised every accepted
opt-in spelling plus unset, false, and typo states. Invented temporary data
confirmed the missing-preview path disclosure and a child process's access to
an omitted parent variable through `/proc`.

**Verdict:** no Critical, High, or Medium finding. The terminal is genuinely
off by default, invalid lesson workspaces fail closed before PTY creation, proxy
userinfo is removed from the banner, and the earlier terminal and lesson
protections have not regressed. Two Low findings limit the confidentiality
claims of this slice, and one informational contract mismatch should be
corrected before it can steer a later caller back toward fallback behavior.

## Findings (severity-ranked)

### T1 — The child-env allowlist does not keep the service environment secret from the spawned shell (Low, confirmed)

`_child_env()` omits variables not present in its allowlist
(`app/terminal.py:228-247`), and `_create_session()` passes that mapping to the
shell (`app/terminal.py:486-505`). This prevents accidental inheritance and
keeps ordinary `env` output cleaner, but it is not a confidentiality boundary:
the shell is a same-UID child of the Uvicorn service and can read the still-live
parent environment from `/proc/<ppid>/environ` on the supported Linux
deployment. The example unit does not isolate the terminal into another user or
process boundary (`deploy/ephemeris.service.example:23-43`).

An invented probe launched a child with an allowlisted-style environment that
omitted `EPHEMERIS_INVENTED_SECRET`; the child read its parent's
`/proc/<ppid>/environ` and recovered the omitted canary. An agent launched from
the terminal can do the same. This contradicts the stronger documentation claim
that service-side configuration and secret-shaped environment values stay out
of the shell and agents (`docs/security-model.md:66-72`).

Under the documented direct-loopback, single-user posture this is Low: the
terminal user already owns the same account, and the change still reduces
accidental propagation. It becomes material if the terminal is ever treated as
a restricted shell or exposed to a less-trusted principal, because omitted
credentials remain readable from the parent process.

*Fix direction:* describe the allowlist as accidental-inheritance reduction,
not secret isolation. If secret isolation is required, move the terminal broker
to a separate unprivileged service/account without access to the application's
secret-bearing process, or establish and verify an OS boundary that prevents
the child from inspecting that parent.

### T2 — The missing-file preview still discloses the absolute runtime path (Low, confirmed)

The new `rel_path` value is correctly used by the Learn header
(`app/templates/learn.html:100-106`) and preview-meta response
(`app/main.py:1171-1189`). However, when the selected lesson HTML file does not
exist, `preview_html()` still escapes `info["path"]` — the absolute filesystem
path — and embeds it in the generated placeholder document
(`app/services/lessons.py:641-679`). That document is returned by the
unauthenticated preview route (`app/main.py:1148-1168`) and displayed in the
Learn iframe.

An invented lesson with no generated HTML produced a placeholder containing a
path shaped like `/tmp/.../data/lessons/invented-preview-probe/index.html`, while
its new display value was `invented-preview-probe/index.html`. Thus the slice
removes the path from the outer page and polling JSON but not from all client
surfaces. A LAN deployment can reveal the service account/home and private data
layout to any client that can open the lesson preview; the trusted-LAN posture
and low sensitivity of that metadata keep this Low.

*Fix direction:* build the placeholder from `info["rel_path"]`, and add a route-
level check for a lesson whose current entry is absent so future tests inspect
the rendered preview body, not only the template source and metadata helper.

### T3 — `prepare_terminal_workspace()` still documents the removed insecure fallback (Info, confirmed)

The new caller correctly treats `None` as refusal and raises before PTY creation
(`app/terminal.py:464-505`). The callee's own contract still says that `None`
means “spawn at the repo root instead” and that a broken lesson directory must
not block a plain terminal (`app/services/lessons.py:430-442`). That is the exact
behavior this commit removes for lesson-scoped requests. Current execution is
safe, but the stale API contract can mislead a later caller or refactor into
reintroducing the fallback.

*Fix direction:* update the docstring to say `None` means the lesson-scoped
request must be refused; a plain terminal should bypass this function entirely.

## Confirmed protections and regression checks

- **Opt-in wiring is fail-closed.** `_TERMINAL_ENABLED` accepts only `1`,
  `true`, `yes`, and `on` after trimming/case-folding
  (`app/terminal.py:47-52`). The same frozen value gates both template rendering
  (`app/terminal.py:75-81`) and WebSocket route registration
  (`app/terminal.py:697-706`). Fresh processes confirmed route absent for unset,
  `0`, `false`, and an invented typo, and present for all four documented truthy
  spellings. The old disable variables being ignored cannot fail open because
  the new default is off.
- **Lesson workspace failure is closed before PTY allocation.** Workspace
  preparation now precedes proxy probing and `pty.openpty()`; a `None` result
  raises `_LessonWorkspaceError` (`app/terminal.py:476-496`). `_serve_ws()` sends
  a fixed refusal and closes instead of spawning elsewhere
  (`app/terminal.py:624-646`). Slug validation, DB lookup, direct-child directory
  containment, atomic brief replacement, and total error handling remain intact
  (`app/services/lessons.py:386-462`).
- **Proxy banner redaction works for the supported URL shapes.** Userinfo is
  removed before printable-character filtering and scrollback storage
  (`app/terminal.py:250-257`, `app/terminal.py:512-520`). Proxy credentials still
  intentionally enter the child proxy variables; T1 concerns other supposedly
  omitted parent values and the stronger isolation claim.
- **Prior F1–F4 terminal findings remain fixed.** The loopback peer plus exact
  Host/Origin gate remains pre-accept (`app/terminal.py:93-120`,
  `app/terminal.py:611-616`); stale writers and control frames retain ownership
  checks (`app/terminal.py:310-317`, `app/terminal.py:530-608`); and the reaper
  still excludes attach-locked sessions while attach stays serialized
  (`app/terminal.py:419-438`, `app/terminal.py:648-680`). These bodies were not
  weakened by the reviewed diff.
- **Earlier lesson findings remain fixed.** Generated agent instructions remain
  constant rather than interpolating lesson metadata
  (`app/services/lessons.py:335-383`), and same-directory temporary files plus
  `os.replace()` avoid following or opening symlink, hard-link, and FIFO
  destinations (`app/services/lessons.py:403-427`).
- **Central middleware remains an independent outer gate.** Every WebSocket
  handshake first receives the trusted-Host check; the terminal then applies
  its stricter peer and exact-Origin policy (`app/security.py:127-145`). The new
  opt-in does not bypass or replace either layer.

## Verification

- `git diff 61b6d65^ 61b6d65 --check` — passed.
- `.venv/bin/python -m compileall -q app verify.py verify_restore.py` — passed.
- Fresh-process opt-in matrix — passed: unset/`0`/`false`/typo kept both flag and
  route false; `1`/`true`/`yes`/`on` made both true.
- Invented missing-file preview probe — confirmed T2: the generated HTML
  contained `info["path"]` while `info["rel_path"]` was bundle-relative.
- Invented parent/child environment probe — confirmed T1: the child recovered an
  omitted canary from `/proc/<ppid>/environ`.
- `env -u ACTIVITY_DATA_DIR PYTHONPATH=. .venv/bin/python verify.py` and the
  corresponding `verify_restore.py` command both emitted only the known
  TestClient deprecation warning and then made no progress for approximately
  three minutes. They were interrupted (exit 130), with no failing assertion
  observed. This report therefore does not independently claim the queue
  entry's `366+28` full-suite result.

## Deploy verdict

**Direct-loopback deployment: YES, with two Low follow-ups.** The opt-in and
fail-closed lesson-workspace controls themselves work, and no prior terminal
trust or lifecycle finding regressed. Do not treat the environment allowlist as
secret isolation, and do not treat the relative-path change as complete until
the missing-file placeholder is corrected. Wider or proxy-adjacent terminal
deployment remains unsupported.

## Addendum — 2026-07-16 (`5d7c226`, `ad11d31`)

**Scope and method:** re-applied the standing brief to `git show 5d7c226` and
`git show ad11d31`, then read the complete changed functions and their route,
query-parameter, workspace-preparation, preview, and verifier call paths. The
review also checked the two commits together for regressions against the
protections confirmed above.

**Prior findings:** T1 is accepted posture for this slice. The underlying
same-user `/proc` access remains, but `docs/security-model.md` now accurately
describes the allowlist as accidental-inheritance reduction rather than secret
isolation and points real isolation to the remaining issue #16 scope. T2 is
resolved: the missing-file placeholder now renders `info["rel_path"]`, and the
new route-level checks cover both the rendered placeholder and preview-meta
JSON. T3 is resolved: `prepare_terminal_workspace()` now documents `None` as a
mandatory refusal for lesson-scoped callers and says that plain terminals do
not call it.

**PR-bot follow-up:** resolved. Starlette distinguishes an absent `lesson`
parameter (`None`) from a present-but-empty one (`""`). `_create_session()` now
tests `lesson is not None`, so unknown, empty, and junk values all reach
`prepare_terminal_workspace()` and raise `_LessonWorkspaceError` before proxy
probing, PTY allocation, or shell spawn when preparation returns `None`. An
absent parameter alone retains the intended plain-terminal repository-root
behavior. The verifier exercises all three refusal classes.

**New findings:** none (no Critical, High, Medium, Low, or Info findings).

**Verification:** `git diff 5d7c226^ ad11d31 --check` passed;
`.venv/bin/python -m compileall -q app verify.py verify_restore.py` passed; a
direct Starlette `QueryParams` probe confirmed absent → `None` and
`lesson=` → `""`; an invented missing-entry probe confirmed that the generated
HTML contains the bundle-relative path and neither the absolute file path nor
the data-root path; and direct workspace-preparation probes returned `None` for
empty, junk, and unknown slugs. A bounded full `verify.py` run again emitted
only the known TestClient deprecation warning and timed out after 55 seconds
without assertion output, matching the environment limitation recorded in the
original report; this addendum therefore does not independently claim the full
`368`-check result.

**Updated deploy verdict (`61b6d65..ad11d31`): direct-loopback deployment: YES,
with T1 accepted posture and no open review finding.** T2, T3, and the empty
`?lesson=` fail-open are resolved without weakening the previously confirmed
terminal trust, lifecycle, opt-in, or lesson-containment controls. Wider or
proxy-adjacent terminal deployment remains unsupported.

## Second-pass convergence (2026-07-16, independent Opus review of 61b6d65)

An independent second pass (Opus subagent, same standing brief) returned
**APPROVE — no High/Medium findings, no regressions**, independently ran both
verifier suites green (366+28 at that commit), and confirmed: complete opt-in
gating on both consumers with a clean cutover from the old opt-out variable;
the lesson refusal costing no fd/process (workspace prep precedes
`pty.openpty()`, `_CREATE_LOCK` released on the exception path); the totality
of `prepare_terminal_workspace` against a hostile bundle (defensive
`_normalise_manifest`, all raisers covered by the outer except); redaction
correctness on hand-checked edge shapes; and the absolute-path leak closed on
every client surface (the remaining `info["path"]` use is a server-side
`FileResponse` only).

Two informational notes, converged into `docs/security-model.md` (no code
change): `SSH_AUTH_SOCK` on the allowlist deliberately forwards the user's
live SSH identity to shells and agents (acceptable only under the single-user
loopback posture); and proxy credentials necessarily remain in the child
environment — `_redact_userinfo` scopes to what is displayed, aligning with T1.
