# Terminal disable switch — adversarial security review

**Scope:** commit `4855e8e` — the import-time `TICKLIKE_DISABLE_TERMINAL`
switch in `app/terminal.py`, its fresh-process checks in `verify.py`, and the
deployment guidance added to `README.md` and `docs/security-model.md`. The full
terminal module and its registration, template, and shutdown call paths were
re-read; PTY/session behavior was checked for regressions but was not changed by
this commit.

**Context:** v0 has no authentication. Per the repository posture, the live
instance is assumed to bind directly to loopback only. The terminal grants a
shell with the service user's permissions, so a wider bind or proxy deployment
would materially increase the impact of a trust-gate mistake.

**Method:** diffed `4855e8e` from its parent, read the touched code and relevant
callers in full, replayed both switch states in fresh Python processes, and
re-checked the confirmed F1-F4 findings in
`terminal-multisession-review.md` against the current source.

**Verdict:** the kill switch itself is effective: when the environment variable
is present at import, neither `/terminal/ws` nor any terminal UI is registered or
rendered. One Low deployment-documentation mismatch remains; it is not a bypass
of the assumed direct-loopback deployment.

## Findings

### T1 — Normal launch instructions leave proxy-header rewriting enabled (Low, confirmed)

The terminal's first trust check consumes `ws.client.host`
(`app/terminal.py:81-108`), while both the module warning
(`app/terminal.py:13-16`) and the new security model
(`docs/security-model.md:39-42`) require Uvicorn to run with
`--no-proxy-headers`. The security-model example includes that flag
(`docs/security-model.md:46-51`), but the normal localhost, pip, and LAN commands
omit it (`README.md:18-23`, `README.md:30-34`, `README.md:41-46`), as does the
committed service template (`deploy/tick-like.service.example:27-29`) and the
deployment design's two canonical commands (`docs/system-design.md:441-446`).
The locked runtime is Uvicorn 0.49.0 (`uv.lock:563-573`); its locally verified
CLI default enables proxy headers and trusts forwarded addresses from loopback.

This does not let a direct remote peer through the current loopback deployment,
and a direct LAN peer is not in Uvicorn's default forwarded-address trust set.
It becomes dangerous if a loopback reverse proxy is introduced, forwarded
trust is widened, or forwarding headers are handled unsafely: the ASGI client
address can then stop representing the TCP peer even though the terminal gate
uses it as a security boundary. The published primary launch paths therefore do
not enforce an invariant on which the code and earlier review rely.

*Fix direction:* in a dedicated follow-up, put `--no-proxy-headers` on every
terminal-enabled launch command and the service template, or enforce the
equivalent in the supported launcher instead of relying on prose.

## Kill-switch behavior verified

- Presence is sampled once at import (`app/terminal.py:43-46`), matching the
  documented restart requirement; values such as `0` still disable it.
- The same frozen flag makes `client_is_local()` false
  (`app/terminal.py:63-69`) and makes `setup_terminal()` return before adding the
  WebSocket route (`app/terminal.py:631-640`). `app/main.py:167-179` wires only
  those two entry points.
- The shared template helper removes the rail toggle, drawer, and script from
  `base.html` (`app/templates/base.html:67`, `app/templates/base.html:105-136`)
  and the lesson terminal button from `learn.html`
  (`app/templates/learn.html:120-125`). Static terminal assets remain readable,
  but there is no UI bootstrap and no terminal WebSocket route behind them.
- `verify.py:29-42` and `verify.py:75-103` import the app in separate processes
  and check both the enabled and disabled route/UI shapes. Both focused checks
  passed in this review.

## Earlier terminal findings — regression check

- **F1, exact same-origin Host/Origin:** still fixed. The peer must be loopback,
  Host must be a loopback name, and every Origin host/port tuple must exactly
  equal Host (`app/terminal.py:81-108`). T1 above records the remaining launch
  invariant around how that peer address is populated.
- **F2, stale blocked writer:** still fixed. Detach wakes the old writer
  (`app/terminal.py:258-265`), and `_write_all()` re-checks socket ownership on
  every loop turn and after waits (`app/terminal.py:476-513`).
- **F3, stale control frame:** still fixed. Ownership is checked before binary
  or text/control handling, with no intervening await before resize/kill
  dispatch (`app/terminal.py:516-552`).
- **F4, reaper during attach:** still fixed. The reaper excludes locked
  sessions (`app/terminal.py:367-386`), and the boot-old/replay/attach sequence
  remains under the per-session attach lock (`app/terminal.py:582-604`).

## Verification limitation

`python -u verify.py` passed both new wiring checks, then stalled while entering
`TestClient`; a blank FastAPI app reproduced the same `TestClient.__enter__()`
stall. A 60-second full run therefore timed out before the rest of the suite and
is not claimed as full-suite evidence. The fresh-process switch probes and
source-level regression checks above are independent of that harness failure.

## Deploy verdict

The switch change has no blocking terminal-code finding for the assumed direct
loopback deployment. T1 should be corrected before the repository's normal
commands are relied on in any proxy-adjacent deployment.
