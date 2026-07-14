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
grants a shell with the server process's operating-system permissions. Even when
the main app listens on a trusted LAN, the terminal is supported only from the
server machine through `localhost`, `127.0.0.1`, or another loopback address.

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

### Disable the terminal

Set `EPHEMERIS_DISABLE_TERMINAL` before starting the process:

```bash
EPHEMERIS_DISABLE_TERMINAL=1 uv run uvicorn app.main:app \
  --host 127.0.0.1 --port 8000 --no-proxy-headers
```

The switch is presence-based: any value, including an empty value or `0`,
disables the terminal. It is read when the app is imported, so restart the
process after setting or unsetting it. When disabled, `/terminal/ws` is not
registered and the terminal UI is not rendered. With the variable unset, the
existing loopback-only behavior remains the default.

## Private data

Everything under `data/` is private runtime state and stays out of Git. This
includes `activity.sqlite`, its WAL/SHM sidecars, backups, and `data/exports/`.
Exports can contain task titles, habit names, notes, dates, and behavioral
history. Public docs, tests, and fixtures use invented examples rather than
copies of real data.

Keeping these files out of Git is not access control: in LAN mode, any client
that can reach the unauthenticated app can use the routes the app exposes.

## Known v0 limitations

These are documented limitations, not fixes made in this pass:

- The main app's Origin check is manually applied to state-changing `POST`
  handlers. It does not protect `GET` routes, accepts requests with no `Origin`,
  and only compares the supplied Origin authority with `Host`.
- Unlike the terminal gate, the main app has no `Host` allowlist. DNS rebinding
  can therefore reach `GET` routes and can present matching hostile `Host` and
  `Origin` values to the weaker POST check. The intended fix is a `TrustedHost`
  allowlist covering the configured local names and addresses.
- The main app has no authentication, including in LAN mode, and no CSRF tokens.
  The intended fixes are single-user session authentication and CSRF protection
  for state-changing requests. The current Origin check is defense in depth,
  not a substitute for either.
- The lesson-preview CSP permits external network connections through
  `connect-src ... https:`. The intended fix is a tighter `connect-src` policy
  or an explicit minimal allowlist for lesson content that genuinely needs it.

Until those fixes exist, keep the documented deployment boundary: loopback by
default, a trusted LAN only when needed, and never the public internet.
