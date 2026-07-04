# Multi-session terminal — pre-deploy review

**Scope:** `app/terminal.py` (server: detachable PTY over WebSocket at `/terminal/ws`,
session registry, trust gate, reaper, backpressure, proxy-env injection) and
`app/static/terminal.js` (client: multi-tab, re-attach across navigation).

**Context:** single-user, no-auth personal app. The live service binds
**`127.0.0.1:8765`** and runs **without `--proxy-headers`**, so `ws.client.host` is
the real peer — the loopback peer check in the trust gate is sound and the service
is not reachable off the local machine. Findings below are therefore
**same-machine** exposure, not network exposure.

**Method:** independent adversarial pass by Codex, converged against the source by
Claude (correctness half). Verdict: **not safe to bless as-is — fix F1–F3 first.**

## Findings

### F1 — Trust gate is host-only, ignores port (High, confirmed)
`_ws_is_trusted` (`app/terminal.py:73`) requires every `Origin` to be a *loopback
hostname* via `_is_loopback_hostname`, but never checks the port. A page served
from another local port (e.g. `http://localhost:3000`) has hostname `localhost`
→ passes. The docstring claims to block cross-site WebSocket hijacking, but only
blocks cross-*host*, not cross-*port*: any other local origin the user visits can
open a shell on `:8765`.
*Fix direction:* compare `Origin` against the app's own origin — reject unless the
Origin's host:port equals the `Host` header (i.e. same-origin, not merely
same-family). Keep the "no Origin → non-browser local client" allowance.

### F2 — Blocked write can resume into a re-attached session (High, confirmed)
`_read_input` (`app/terminal.py:473`) checks `sess.ws is not ws` once, *before*
`_write_all`. `_write_all` (`:434`) can `await` PTY writability; during that await a
newer socket can attach to the same `sid` (booting this one). When the old write
resumes it keeps writing the old socket's bytes to `sess.master_fd`, now owned by
the new tab. `sess._writer_waiter` is also shared across writers.
*Failure:* paste a large command while the foreground process isn't draining stdin,
reload the page → the tail of the old paste is injected into the new session's shell.
*Fix direction:* re-check ownership after each `await` in the write loop (bail if
`sess.ws is not ws`), and scope the writer-waiter to the attaching socket.

### F3 — Control frames skip the ownership check (Medium, confirmed)
Binary frames re-check `sess.ws is ws` (`:474`); the `resize` and `kill` handlers
(`:488`, `:496`) do not. A stale/booted socket can still `kill` or `resize` the PTY
a newer tab just took over.
*Fix direction:* guard the text-control branch with the same `sess.ws is ws` check.

### F4 — Reaper can evict a session mid-reconnect (Medium, plausible)
The attach sequence awaits (`send_text`, snapshot send) before `sess.attach(ws)`
(`app/terminal.py:533`), while the session still looks detached. `_reap_idle` /
`_reaper_loop` can close a near-TTL session in that window, tearing down the PTY as
the reconnect lands.
*Fix direction:* mark the session "attaching" (or hold a lock the reaper respects)
across the boot-old+attach sequence.

### F5 — Child shells inherit the full service env (Low, informational)
`_create_session` replaces only the proxy vars; the rest of the service environment
passes into every shell. For a single-user machine this is low impact (the user
already owns the shell), but any secret in the service env is readable via `env`,
and would matter more if F1 were reached.
*Fix direction:* optional — pass an allowlisted env to the child instead of inheriting.

## Not findings (checked, clear)
- Direct non-loopback browser origins are rejected (peer check, `:80`).
- PTY EOF/EIO + double-close look correct: reader removal precedes fd close and
  `close()` is idempotent (`app/terminal.py:270`–`342`).
- The `--proxy-headers` dependency the code relies on holds (service confirmed
  without it), so `ws.client.host` can't be spoofed via forwarded headers.

## Deploy verdict
Fix **F1, F2, F3** in a dedicated terminal-core change (not a ride-along) before the
terminal is treated as reviewed-OK. F4 worth folding into the same pass; F5 optional.
The terminal is already running live, so these are live same-machine issues, bounded
by the loopback-only bind and single-user context.
