# Security model

Ephemeris v0 is a local-first, single-user application. Its security boundary is
the machine on which it runs, not an application login.

## Supported deployments

| Binding | v0 support |
| --- | --- |
| `127.0.0.1` | The only supported binding. Only clients on the server machine can reach the app. |
| Public internet | Unsupported. Do not expose v0 directly or through a public reverse proxy; it has no authentication. |

This is acceptable for v0 only because the intended deployment is one user on a
machine they control. It is not a general multi-user security model.
The repository can be public because it contains application code, while the
running service and its private runtime data stay inside that local boundary.

## Embedded terminal

The terminal is a higher-risk surface than the main app: a successful connection
grants a shell with the server process's operating-system permissions. It is
therefore **off by default** and exists only when the process was started with
`EPHEMERIS_ENABLE_TERMINAL=1` (see below). The terminal is supported only from
the server machine through `localhost`, `127.0.0.1`, or another loopback
address.

The WebSocket at `/terminal/ws` applies several independent checks:

- The network peer in ASGI `scope["client"]` must be loopback. The drawer,
  terminal toggle, script, and lesson terminal button are also rendered only for
  a loopback client.
- `Host` must name `localhost` or a loopback IP. This prevents a hostile domain
  that resolves to loopback from using DNS rebinding to reach the shell.
- Every browser `Origin`, when present, must have exactly the same host and port
  as `Host`. This blocks cross-site WebSocket hijacking, including from a page on
  another local port. Multiple `Origin` headers are all checked. An absent
  `Origin` is allowed after the peer and Host checks for non-browser clients;
  browsers normally supply it.

The peer address is a trust input. Run terminal-enabled deployments with
Uvicorn's `--no-proxy-headers`; do not put the terminal behind a proxy that
rewrites the client address from forwarded headers. Otherwise
`scope["client"]` can become attacker-influenced and weaken the loopback check.

The flag is not a no-op: the pinned Uvicorn version honors forwarded headers by
default, so every launch command in this repository — README, the systemd
template, `docs/system-design.md`, and the example above — passes
`--no-proxy-headers`, and it must stay there. Keep it even when the terminal is
off, so enabling `EPHEMERIS_ENABLE_TERMINAL` later cannot silently start a
process whose loopback peer check reads an attacker-influenced client address.

### Enabling the terminal (opt-in)

The terminal is disabled unless `EPHEMERIS_ENABLE_TERMINAL` is set to a truthy
value (`1`, `true`, `yes`, `on`) before starting the process:

```bash
EPHEMERIS_ENABLE_TERMINAL=1 uv run uvicorn app.main:app \
  --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Any other value — unset, empty, `0`, a typo — leaves it off. The variable is
read when the app is imported, so restart the process after setting or
unsetting it. While disabled, `/terminal/ws` is not registered and the terminal
UI is not rendered. The committed systemd example ships with the variable
commented out; enable it only on a single-user desktop deployment.

### Shell environment and lesson scoping

The spawned shell does not inherit the full service environment. It starts from
a small allowlist (identity, locale, session paths; `TERM`/`PATH` normalized).
This reduces *accidental* propagation of service-side configuration into the
shell and agents launched from it; it is **not** secret isolation. The shell is
a same-user child of the service process and can still read the parent's live
environment (for example via `/proc/<pid>/environ`), so anything that must stay
hidden from the terminal user needs a real OS boundary (a separate account),
not this list. Two
deliberate pass-throughs to be aware of: `SSH_AUTH_SOCK` is on the allowlist,
so shells and agents launched from them can use the user's live SSH identity
(git-over-SSH) — acceptable only under the single-user loopback posture. The
service's own `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` (either case) reach
the child unchanged, credentials included; the app adds, probes for and
rewrites nothing, and network egress for the shell is the host's business.

### The study browser (host configuration, not app code)

The tutor's brief describes a `playwright` MCP server: one shared real
browser, running under a logged-in study profile whose material a lesson is
built from, used when a plain fetch returns a login wall. Nothing in this
repository starts it, configures it, or grants access to it. It exists when
the operator runs a `@playwright/mcp` server on loopback and registers it in
their own Claude configuration; a lesson-agent shell reaches it because it
runs on the host network, and sees it because the shell uses the real
`~/.claude.json`.

What that means for the trust boundary, stated plainly rather than implied
by the brief:

- The agent gets whatever the browser's profile is signed into. The profile
  is therefore the boundary, and it should be a study profile signed into
  the lesson sources only — not the operator's everyday browser profile.
- The brief tells the tutor to read the lesson's material and change no
  account state. That is agent compliance, not enforcement: source material
  is untrusted text, and a prompt-injected tutor is exactly the case
  compliance does not cover. `--allowed-origins` narrows which origins the
  browser will request and is worth setting to the lesson's hosts, but its
  own documentation says it is not a security boundary.
- Consequently: run the server only while lessons are being built, and keep
  its profile signed out of anything whose loss would matter. An
  app-owned capability that scopes origins per lesson and refuses
  state-changing tools would replace compliance with enforcement; it is not
  built, and is the open question this section exists to name.

A lesson-scoped terminal (`/terminal/ws?lesson=<slug>`) fails closed: if the
lesson workspace cannot be prepared — unknown slug, symlinked bundle directory,
filesystem or database error — the connection is refused with a visible message
instead of opening a shell anywhere else (in particular, never the repo root).

## Private data

Everything under `data/` is private runtime state and stays out of Git. This
includes `activity.sqlite`, its WAL/SHM sidecars, backups, and `data/exports/`.
Exports can contain task titles, habit names, notes, dates, and behavioral
history. Public docs, tests, and fixtures use invented examples rather than
copies of real data.

Keeping these files out of Git is not access control: local clients can use the
routes the unauthenticated app exposes.

## Main-app request perimeter

`app/security.py` installs one middleware in front of every route (issue #15,
first slice; issue #23 added the body ceiling). It owns four things:

- **Trusted-host allowlist.** Every HTTP request and WebSocket handshake must
  carry a `Host` whose hostname is `localhost`, `127.0.0.1` or `::1`. This
  blocks DNS rebinding for the whole app, `GET` routes included.
- **Central write guard.** Every unsafe-method request (`POST`/`PUT`/`PATCH`/
  `DELETE`) passes one origin policy in middleware — a newly added route
  cannot forget it. Each case is deliberate: any present `Origin` (all values,
  so duplicates can't smuggle) must be a serialized http(s) origin equal to
  the request's own scheme, hostname, and port — scheme from the ASGI scope,
  default ports normalized, so an https page cannot write to the http app
  even on the same host; `Origin: null` (an opaque origin, e.g. a sandboxed lesson iframe
  posting directly) is rejected — the sanctioned lesson write path is the
  postMessage bridge; an absent `Origin` with no fetch metadata is allowed
  (non-browser loopback clients such as curl or an agent CLI; browsers always
  send `Origin` on cross-origin unsafe requests); an absent `Origin` with
  `Sec-Fetch-Site` other than `same-origin`/`none` is rejected, including
  `same-site` — a page on another local port must not write here, the same
  stance as the terminal gate.
- **Request-body ceiling.** Every unsafe-method request is bounded at
  2 MiB. `Content-Length` is an early refusal and the streaming byte count
  is the authority, so a chunked or dishonest request buys nothing; nothing is
  buffered by the middleware itself. A body past the ceiling is not truncated
  into the route — the app is handed a disconnect, so no handler can act on a
  partial request — and the answer is `413`. This is a ceiling **over** the
  per-route caps, never a replacement: the Learn JSON endpoints keep their own
  smaller caps (artifact 512 KiB, attempt 256 KiB, assessment 64 KiB, run
  16 KiB) and their own typed JSON refusals, which the lesson agent reads. A
  regression fails if the ceiling is ever set below the largest route cap.
  Separately and upstream, Starlette's form parser refuses a single form field
  over 1 MB.
- **Security headers on every response.** `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: same-origin`, and `Content-Security-Policy:
  frame-ancestors 'none'` when the route sets no CSP of its own — the
  lesson-preview responses keep their sandbox CSP with its narrow
  `frame-ancestors 'self'` exception.

The terminal gate in `app/terminal.py` remains the stricter authority for
`/terminal/ws` (loopback peer + loopback Host + exact origin); the middleware
only vets the handshake's `Host` before it.

## Known v0 limitations

These are documented limitations, not fixes made in this pass:

- The main app has no authentication and no CSRF tokens.
  The intended fixes are single-user session authentication and CSRF protection
  for state-changing requests. The origin-policy middleware is defense in
  depth, not a substitute for either.
- The lesson-preview CSPs permit external network connections through
  `connect-src ... https:`. For `interactive-local-v1` this is a deliberate
  owner decision (2026-08-11): non-script resources and fetch are open, while
  script sources stay bundle-local, keeping the build step's 30-day release
  quarantine the sanctioned road for code. It is not scheduled for
  tightening. Two consequences ride along, accepted with the decision: a
  page script can fetch remote text and inject it as inline script (the
  quarantine bounds what a build ships, not what a shipped loader pulls —
  see the spec §5 residual), and a page can read the app's unauthenticated
  GET routes via `connect-src 'self'` and relay the response out (writes
  still die on the origin guard's `Origin: null` refusal).
- Both lesson terminals (`lesson-agent` and `lesson-learner`) run on the
  host network, so commands in them can reach the unauthenticated loopback
  app — read routes and, as an origin-less non-browser client, unsafe ones —
  the same authority every other local process already has. The durable fix
  for the HTTP path is the single-user authentication already listed above,
  not a network carve-out. The host network also reaches any other loopback
  listener — concretely the Playwright MCP server on `localhost:9223`, whose
  tool set includes arbitrary host-side code execution. The owner accepted
  that reach (2026-08-17, see Accepted risks below): the brief's read-only
  rule stays prose, and no restricted server or per-lesson capability is
  planned.

Until those fixes exist, keep the documented deployment boundary on loopback
and never expose it to the public internet.

## Accepted risks

Owner decisions, dated. They stand until the owner revisits them.

- 2026-08-14 (#191): the diary cloud-context boundary (AGENTS.md) is
  convention, not enforcement. Lesson-agent shells share the host network and
  can read `/diary` and the export over loopback; no egress block or route
  gate is planned.
- 2026-08-17: lesson shells reach the shared Playwright MCP listener on
  `localhost:9223`, whose core tool set includes arbitrary host-side code
  execution. The brief's read-only rule stays prose; no per-lesson capability
  or separate restricted server is planned. The shared browser is deliberate
  architecture on a single-owner host (queue entries 2026-08-09/#177+#178 and
  2026-08-11/#181; reviews of 2026-08-11/-12/-16).
- 2026-08-17: the reusable year-long Claude token (`DATA_DIR/claude-token`)
  rides in the lesson-agent shell environment while that shell reads untrusted
  source material and has outbound network. No broker or process boundary is
  planned: with the shared-browser risk above accepted, host-side code
  execution would reach the token file past any broker anyway (queue entry
  2026-08-12/#189; reviews of 2026-08-12/-16).
