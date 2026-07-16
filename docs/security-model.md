# Security model

Tick-like v0 is a local-first, single-user application. Its security boundary is
the machine and network on which it runs, not an application login.

## Supported deployments

| Binding | v0 support |
| --- | --- |
| `127.0.0.1` | Default and recommended. Only clients on the server machine can reach the app. |
| `0.0.0.0` | Supported only on a trusted LAN, for example to use the app from another personal device. Every LAN client can reach the unauthenticated main app. |
| Public internet | Unsupported. Do not expose v0 directly or through a public reverse proxy; it has no authentication. |

This is acceptable for v0 only because the intended deployment is one user on a
machine and network they control. It is not a general multi-user security model.
The repository can be public because it contains application code, while the
running service and its private runtime data stay inside that local boundary.

## Embedded terminal

The terminal is a higher-risk surface than the main app: a successful connection
grants a shell with the server process's operating-system permissions. It is
therefore **off by default** and exists only when the process was started with
`EPHEMERIS_ENABLE_TERMINAL=1` (see below). Even when the main app listens on a
trusted LAN, the terminal is supported only from the server machine through
`localhost`, `127.0.0.1`, or another loopback address.

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
(The previous opt-out switch, `EPHEMERIS_DISABLE_TERMINAL`, is no longer
honored.)

### Shell environment and lesson scoping

The spawned shell does not inherit the full service environment. It starts from
a small allowlist (identity, locale, session paths; `TERM`/`PATH` normalized),
so service-side configuration and anything secret-shaped in the service
environment stays out of the shell and out of agents launched from it. Egress
proxy variables are then re-derived deliberately (`EPHEMERIS_TERM_PROXY`
override, service-configured proxy, or local auto-detection); when a proxy URL
is shown in the terminal banner, any `user:password@` credentials are redacted.

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

Keeping these files out of Git is not access control: in LAN mode, any client
that can reach the unauthenticated app can use the routes the app exposes.

## Main-app request perimeter

`app/security.py` installs one middleware in front of every route (issue #15,
first slice). It owns three things:

- **Trusted-host allowlist.** Every HTTP request and WebSocket handshake must
  carry a `Host` whose hostname is in `EPHEMERIS_TRUSTED_HOSTS`
  (comma-separated hostnames; default `localhost,127.0.0.1,::1`; read at
  import, so restart to change). This blocks DNS rebinding for the whole app,
  `GET` routes included. LAN deployments must list the names or addresses
  clients will actually use.
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

- The main app has no authentication, including in LAN mode, and no CSRF tokens.
  The intended fixes are single-user session authentication and CSRF protection
  for state-changing requests. The origin-policy middleware is defense in
  depth, not a substitute for either.
- The lesson-preview CSP permits external network connections through
  `connect-src ... https:`. The intended fix is a tighter `connect-src` policy
  or an explicit minimal allowlist for lesson content that genuinely needs it.

Until those fixes exist, keep the documented deployment boundary: loopback by
default, a trusted LAN only when needed, and never the public internet.
