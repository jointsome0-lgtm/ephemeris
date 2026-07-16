"""Central request-security perimeter for the main app (issue #15, minimal slice).

One ASGI middleware, installed once in main.py, owns three jobs that used to be
per-route (or absent):

1. Trusted-host allowlist. Every HTTP request and WebSocket handshake must
   carry a Host whose hostname is in EPHEMERIS_TRUSTED_HOSTS (comma-separated;
   default: the loopback names). Blocks DNS rebinding for the whole app, GET
   routes included. Starlette's TrustedHostMiddleware is not used because it
   splits Host on ":" and so mangles bracketed IPv6 ("[::1]:8765" -> "[");
   we parse like app/terminal.py does, with urlsplit.

2. Write guard. Every unsafe-method request (POST/PUT/PATCH/DELETE — anything
   a new route could add) passes one origin policy; a route cannot opt out by
   forgetting a call. Load-bearing invariant: safe methods are NOT guarded,
   so GET/HEAD routes must stay side-effect-free — a mutating GET would sit
   outside this policy. The policy, each case deliberate:
   - Origin present: every value (getlist — duplicates can't smuggle) must be
     a serialized http(s) origin (no userinfo/path/query/fragment) equal to
     the request's own (scheme, hostname, port) — scheme from the ASGI scope,
     hostname/port from Host, default ports normalized so "http://host" and
     "http://host:80" are the same origin. Cross-anything, including a scheme
     mismatch (https page writing to the http app), -> 403.
   - "Origin: null" -> 403. An opaque origin is what the sandboxed lesson
     iframe would send on a direct form POST; the sanctioned write path for
     lesson content is the postMessage bridge (issue #36), never a direct POST.
   - Origin absent, no Sec-Fetch-Site: allowed. This is the non-browser
     loopback client (curl, agent CLI, TestClient); browsers always send
     Origin on cross-origin unsafe requests, so CSRF stays covered.
   - Origin absent but Sec-Fetch-Site present and neither "same-origin" nor
     "none": 403. Defense in depth against a browser path that omits Origin;
     "same-site" is deliberately rejected — a page on another local port is
     same-site but must not write here (same stance as the terminal gate F1).

3. Response headers on every HTTP response:
   - X-Content-Type-Options: nosniff
   - Referrer-Policy: same-origin
   - Content-Security-Policy: frame-ancestors 'none' — only when the route
     set no CSP of its own, so the lesson-preview responses keep their full
     sandbox CSP with the narrow frame-ancestors 'self' exception.

The middleware never *accepts* a WebSocket — a bad handshake Host is refused
pre-accept (close code 1008; HTTP requests get a 400) before the app sees
it; everything else passes through to the terminal gate in app/terminal.py,
which stays the stricter authority (loopback peer + loopback Host +
exact-origin).
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_DEFAULT_TRUSTED_HOSTS = "localhost,127.0.0.1,::1"

# Import-time read, like the terminal's kill switch: restart to change.
TRUSTED_HOSTS = frozenset(
    h.strip().lower()
    for h in os.environ.get("EPHEMERIS_TRUSTED_HOSTS", _DEFAULT_TRUSTED_HOSTS).split(",")
    if h.strip()
)

_RESPONSE_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "same-origin"),
)


def _host_parts(host_header: str | None) -> tuple[str, int | None] | None:
    """(hostname, port) from a Host header, or None if it doesn't parse.
    urlsplit handles bracketed IPv6 and lowercases the hostname."""
    try:
        parts = urlsplit("//" + (host_header or ""))
        if not parts.hostname:
            return None
        return (parts.hostname, parts.port)  # .port raises ValueError on junk
    except ValueError:
        return None


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _write_rejection(
    headers: Headers, own: tuple[str, int | None], scheme: str
) -> str | None:
    """Why this unsafe-method request must be refused, or None to allow."""
    origins = headers.getlist("origin")
    if origins:
        expected = (scheme, own[0], own[1] or _DEFAULT_PORTS.get(scheme))
        for origin in origins:
            if origin == "null":
                return "opaque-origin (null) write rejected"
            try:
                parts = urlsplit(origin)
                # A browser-serialized origin is scheme://host[:port] and
                # nothing else; anything richer is not an origin — reject.
                if (
                    parts.scheme not in _DEFAULT_PORTS
                    or parts.path
                    or parts.query
                    or parts.fragment
                    or "@" in parts.netloc
                ):
                    return "cross-origin write rejected"
                got = (
                    parts.scheme,
                    parts.hostname,
                    parts.port or _DEFAULT_PORTS[parts.scheme],
                )
            except ValueError:
                return "cross-origin write rejected"
            if got != expected:
                return "cross-origin write rejected"
        return None
    site = headers.get("sec-fetch-site")
    if site and site.lower() not in ("same-origin", "none"):
        return "cross-site write rejected"
    return None


class SecurityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        own = _host_parts(headers.get("host"))
        if own is None or own[0] not in TRUSTED_HOSTS:
            await self._refuse(scope, receive, send, 400, "untrusted Host")
            return

        if scope["type"] == "websocket":
            # Host vetted; peer/origin enforcement stays with the terminal gate.
            await self.app(scope, receive, send)
            return

        if scope["method"] in _UNSAFE_METHODS:
            reason = _write_rejection(headers, own, scope.get("scheme", "http"))
            if reason is not None:
                await self._refuse(scope, receive, send, 403, reason)
                return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                for key, value in _RESPONSE_HEADERS:
                    if key not in out:
                        out[key] = value
                if "Content-Security-Policy" not in out:
                    out["Content-Security-Policy"] = "frame-ancestors 'none'"
            await send(message)

        await self.app(scope, receive, send_with_headers)

    async def _refuse(
        self, scope: Scope, receive: Receive, send: Send, status: int, detail: str
    ) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = PlainTextResponse(detail, status_code=status)
        for key, value in _RESPONSE_HEADERS:
            response.headers.setdefault(key, value)
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        await response(scope, receive, send)


def install_security(app) -> None:
    """Register the perimeter middleware (outermost, so it sees every request)."""
    app.add_middleware(SecurityMiddleware)
