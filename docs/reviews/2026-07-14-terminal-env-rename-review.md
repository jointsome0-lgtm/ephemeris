# Terminal environment rename — adversarial security review

**Scope:** commit `d56b617` — the repository rename to Ephemeris, with the
security-sensitive changes being `TICKLIKE_DISABLE_TERMINAL` →
`EPHEMERIS_DISABLE_TERMINAL` and `TICKLIKE_TERM_PROXY` →
`EPHEMERIS_TERM_PROXY` in `app/terminal.py`, plus the corresponding verifier,
documentation, dependency metadata, and systemd-template changes.

**Context:** v0 has no authentication. Per the repository posture, the live
instance is assumed to bind directly to loopback only. The terminal grants a
shell with the service user's permissions, so silently changing whether it is
registered or how its child processes reach the network is security-relevant
even though the current loopback/single-user boundary limits the impact.

**Method:** diffed `d56b617^..d56b617`, read the touched executable code and the
terminal registration, template, client, lesson-workspace, and shutdown call
paths, searched tracked active files for both old and new environment names,
and re-read the earlier terminal reports. Fresh Python processes exercised the
old-only, new-only, both, and unset switch states. Focused probes exercised the
proxy override and the earlier F1–F4 terminal fixes. After substituting only the
two renamed environment identifiers, the parent and commit versions of
`app/terminal.py` have identical SHA-256 hashes; the same normalization check
passed for both verifier files.

**Verdict:** the current `EPHEMERIS_*` controls work, and the rename introduced
no new Host/Origin, PTY ownership, fd-lifecycle, or reaper defect. It is not safe
as an unattended in-place upgrade, however: one Medium and two Low migration
findings were confirmed. In particular, an installation that still carries the
old terminal kill switch silently gets the full-shell route and UI back.

## Findings

### E1 — Renaming the kill switch silently re-enables the terminal on upgrade (Medium, confirmed)

The import-time flag now checks only for `EPHEMERIS_DISABLE_TERMINAL`
(`app/terminal.py:43-46`). That flag is the sole guard used by the template
helper (`app/terminal.py:63-69`) and by WebSocket route registration
(`app/terminal.py:631-640`). `TICKLIKE_DISABLE_TERMINAL`, the documented name
before this commit, has no compatibility path or startup warning anywhere in
active code.

A fresh-process probe rendered the loopback template and inspected registered
routes under four invented configurations:

| Environment at import | `/terminal/ws` | Terminal UI/script |
| --- | --- | --- |
| neither name set | present | present |
| old name only | present | present |
| new name only | absent | absent |
| both names | absent | absent |

This is fail-open behavior for an existing security control: after updating and
restarting, an operator can retain the setting that previously disabled a full
OS shell yet unknowingly run with that shell enabled. The risk is not limited to
the drawer being visible. A loopback non-browser client may omit `Origin` after
passing the peer and loopback-Host checks (`app/terminal.py:81-108`). The current
direct-loopback and single-user assumptions keep this at Medium; a future wider
or proxy-adjacent deployment would increase the impact.

The verification change cannot detect this migration failure. Its subprocess
helper removes and sets only the new name and tests only new-name unset/set
states (`verify.py:75-103`). Thus a green 340-check run proves the new switch,
not the safety of upgrading an old configuration.

*Fix direction:* during a documented compatibility window, treat the presence
of either name as disabled and emit a deprecation warning when the old name is
seen. Add fresh-process assertions for old-only, new-only, both, empty values,
and neither before eventually removing the alias in a separately announced
migration.

### E2 — Renaming the proxy override silently changes terminal-child egress (Low, confirmed)

`_detect_proxy_env()` reads only `EPHEMERIS_TERM_PROXY`
(`app/terminal.py:158-187`). Session creation then clears every ambient proxy
variable from the child environment and applies that function's result
(`app/terminal.py:423-435`). An existing `TICKLIKE_TERM_PROXY=off` or explicit
old-name URL is therefore ignored rather than rejected or migrated.

In a focused invented probe, the environment contained
`TICKLIKE_TERM_PROXY=off` and `HTTP_PROXY=http://127.0.0.1:9999`. The detector
returned the HTTP proxy. Replacing the old override with
`EPHEMERIS_TERM_PROXY=off` returned an empty mapping as intended. Old-name
explicit URLs were likewise ignored, while new-name URLs were honored. A
repo-wide search found no proxy-override assertion in either verifier.

This can move agent CLI traffic away from the route the operator explicitly
selected: through an ambient or auto-detected proxy, or directly when an old
explicit proxy URL was expected. Under the current local single-user posture
this is Low, but it is still a confidentiality, policy, and availability change
rather than a cosmetic rename.

*Fix direction:* prefer the new name, fall back to the old name with a
deprecation warning, and handle conflicting values explicitly. Cover `off`, an
explicit HTTP URL, an explicit SOCKS URL, inherited proxy variables, and
auto-detection for old-only/new-only/both-name cases.

### E3 — The service rename has no safe in-place migration path (Low, confirmed)

The install instructions create and enable only `ephemeris.service`
(`README.md:58-72`), while the new template assumes both a renamed checkout and
virtual environment under `~/projects/ephemeris`
(`deploy/ephemeris.service.example:14`, `deploy/ephemeris.service.example:23-29`).
Neither file tells an existing installation to stop and disable
`tick-like.service`, update the checkout path, or migrate unit-local environment
overrides such as the terminal kill switch.

Pulling a Git rename commit does not rename an existing checkout or systemd
unit. Following the new instructions in place can therefore leave the old unit
running while the new one fails its working-directory or executable lookup; a
port conflict can have the same visible result. The operator may then believe
the renamed service and its reviewed configuration are live while the older
process remains the listener. This is Low under the loopback posture, but it is
security-relevant for a service containing the terminal surface.

*Fix direction:* publish an explicit upgrade sequence: stop/disable the old
unit, choose and verify the checkout path, migrate environment overrides using
the compatibility rules above, daemon-reload, enable the new unit, and verify
the exact running unit, command line, bind address, and absence of the old
listener.

## Earlier terminal findings — regression check

- **F1, exact same-origin Host/Origin:** still fixed. The peer must be loopback,
  Host must be a loopback name, and every Origin host/port tuple must match Host
  exactly (`app/terminal.py:81-108`). Same-origin loopback and no-Origin local
  probes passed; cross-port, different-loopback-name, duplicate-Origin,
  non-loopback peer/Host, and junk-port probes were rejected.
- **F2, stale blocked writer:** still fixed. Detach wakes the old writer
  (`app/terminal.py:258-265`), while `_write_all()` checks ownership before every
  write and after any writability wait (`app/terminal.py:476-513`). The direct
  blocked-writer handoff probe passed without stale tail bytes.
- **F3, stale control frame:** still fixed. Ownership is checked before every
  binary or text/control frame (`app/terminal.py:516-552`); stale resize/kill
  probes were ignored and owner controls applied.
- **F4, reaper during attach:** still fixed. Reaping excludes attach-locked
  sessions (`app/terminal.py:367-386`), and the boot/replay/attach sequence stays
  under the session lock (`app/terminal.py:582-604`). Both direct probes passed.
- **Prior lesson-workspace findings:** no regression was introduced. The lesson
  call and PTY creation bodies are unchanged after normalizing the environment
  identifiers. The open prompt-injection and hard-link/FIFO findings remain
  documented in `2026-07-11-lesson-claude-shim-review.md`; this rename neither
  worsens nor resolves them.
- **Prior launch finding T1 remains open (Low):** terminal code still warns that
  proxy-header rewriting invalidates its peer-address trust input
  (`app/terminal.py:13-14`), and the security model still requires
  `--no-proxy-headers` (`docs/security-model.md:39-42`). The normal README
  commands (`README.md:22-44`), systemd template
  (`deploy/ephemeris.service.example:29`), and deployment-design commands
  (`docs/system-design.md:439-446`) still omit the flag. The rename preserved,
  rather than regressed or fixed, this previously confirmed Low finding.

## Verification notes

- The new kill switch correctly removed the route, drawer, toggle, and script in
  a fresh process; the unset state correctly retained all four.
- The parent/commit normalization hashes matched for `app/terminal.py`,
  `verify.py`, and `verify_restore.py`, confirming that their other executable
  behavior did not ride along with the rename. Dependency versions and artifact
  hashes were unchanged; `uv.lock` only moved the local package record from
  `tick-like` to `ephemeris`.
- `.venv/bin/python -m compileall -q app verify.py verify_restore.py` passed.
- A 120-second local run of `verify.py` passed the two fresh-process terminal
  wiring checks, then timed out while entering `TestClient`. `verify_restore.py`
  timed out at the same harness boundary before printing checks. This matches
  the previously recorded local TestClient limitation, so this review does not
  independently claim the commit message's 340/340 full-suite result.
- The review used invented environment values only and introduced no runtime
  data, exports, screenshots, or authenticated artifacts.

## Deploy verdict

**In-place upgrade: NO** until E1 is fixed or the old environment and service
state are explicitly migrated and verified. **A fresh direct-loopback deployment
has no new terminal-core blocker** when the new environment names are used,
subject to the previously open Low `--no-proxy-headers` launch mismatch. E2 and
E3 should be corrected in the same migration-focused follow-up; no PTY/WS fix
belongs in that documentation/configuration change.

## Convergence (Claude, correctness pass)

A second independent pass (Opus subagent, same brief by file reference) reached
PASS with the same operational caveat as E1: the hazard exists only for an
installation that actually sets the old names. Disposition:

- **E1/E2 — accepted as real, resolved by explicit migration, no alias code.**
  The only live installation's unit sets neither `TICKLIKE_*` variable
  (verified by reading `~/.config/systemd/user/tick-like.service`: only
  `APP_TIMEZONE`), so no setting is silently lost; the terminal was and remains
  intentionally enabled. A permanent compatibility alias for a one-time,
  operator-verified migration is complexity we decline; the report's own
  verdict permits deploy once "the old environment and service state are
  explicitly migrated and verified", which the migration below performs.
- **E3 — accepted and fixed**: README now documents the upgrade sequence
  (stop/disable old unit, rename checkout, re-set any `TICKLIKE_*` overrides
  under the new names, enable the new unit, verify the listener).
- **T1 (`--proxy-headers`)** stays open-by-design: the loopback trust gate
  requires the flag to be absent, and the local unit never passes it.
