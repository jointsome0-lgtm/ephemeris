# Security Policy

## Supported versions

Ephemeris is a single-maintainer project in v0. Only the current `main` branch
receives fixes; there are no maintained release branches and no backports to
older commits or tags.

## Reporting a vulnerability

Report privately through GitHub security advisories on this repository:
[Report a vulnerability](https://github.com/jointsome0-lgtm/ephemeris/security/advisories/new).
That channel is private until an advisory is published; please use it instead of
a public issue for anything exploitable.

Useful in a report: the affected file or route, the deployment shape you were
running (binding, whether `EPHEMERIS_ENABLE_TERMINAL` was set), and the steps
that reproduce the behaviour. Please leave real personal data out of the report —
invented examples are enough.

## What to expect

This is a solo project maintained in spare time, so there is no response-time
guarantee, no bug bounty, and no paid support. In practice: reports are read and
acknowledged when the maintainer next works on the project, fixes land on `main`
through the normal PR and review process, and the advisory is published once a
fix exists. If a report turns out to be a documented limitation rather than a
defect, it is answered as such and closed.

## Threat model

Read [`docs/security-model.md`](docs/security-model.md) first — it is
authoritative for this repository and defines what counts as a vulnerability
here. In short:

- Single-user, local-first app with **no authentication** and no CSRF tokens.
- The supported binding is loopback (`127.0.0.1`); a trusted LAN (`0.0.0.0`) is
  supported only as an explicit choice on a network the user controls.
- Public-internet deployment is **unsupported** in v0, directly or behind a
  reverse proxy. Findings that only apply to such a deployment are out of scope.
- The embedded terminal is off by default, requires `EPHEMERIS_ENABLE_TERMINAL`,
  and is loopback-only when enabled; it grants a shell with the server process's
  permissions by design.
- Documented launch commands pass `--no-proxy-headers` so the client address the
  app checks is the real peer. Reports assuming forwarded headers are trusted do
  not describe a supported configuration.

In scope, for example: a way to reach the terminal websocket from another
machine in a documented deployment, a path that writes or exposes data outside
the configured private data directory, or private runtime data leaking into the
public repository.

The ecosystem-wide policy lives in
[selfos `SECURITY.md`](https://github.com/jointsome0-lgtm/selfos/blob/main/SECURITY.md);
this file stays authoritative for Ephemeris itself.
