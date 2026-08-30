"""Central request-security perimeter for the main app (issue #15, minimal slice).

One ASGI middleware, installed once in main.py, owns three jobs that used to be
per-route (or absent):

1. Trusted-host allowlist. Every HTTP request and WebSocket handshake must
   carry a Host whose hostname is one of the loopback names (TRUSTED_HOSTS).
   Blocks DNS rebinding for the whole app, GET routes included. Starlette's
   TrustedHostMiddleware is not used because it splits Host on ":" and so
   mangles bracketed IPv6 ("[::1]:8765" -> "["); we parse like app/terminal.py
   does, with urlsplit.

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

3. Request-body ceiling on unsafe methods (issue #23). Routes that read JSON
   bound their own bodies through app/request_body.py; ordinary form POSTs —
   tasks, habits, calendar — bounded nothing, so a multi-megabyte body was
   parsed and stored by whichever route received it. The ceiling is one number
   (limits.MAX_BODY_BYTES) applied here, ABOVE the per-route caps and never
   instead of them: a Learn endpoint whose own cap is 512 KiB still answers
   with its own 413 and its own message, because the route's smaller counter
   trips first.

   Same two-part shape as read_capped: Content-Length is an early refusal, the
   streaming count is the authority, so a chunked or dishonest request cannot
   buy a larger body than an honest one. Nothing is buffered here — the count
   rides on the chunks the app is already pulling. Once the count is over, the
   app is handed `http.disconnect` instead of more body: Starlette turns that
   into ClientDisconnect, so no route can ever act on a body that was silently
   truncated, and whatever answer the app produces afterwards (its own 400, or
   the exception reaching this middleware) is replaced with the 413.

   Not this middleware's, and left alone: Starlette's form parser already
   refuses a single field over 1 MB. That is a narrower question — one field
   cannot be a megabyte — and it sits under this ceiling rather than beside it,
   so a body that is one enormous field is answered by the parser (400) before
   the whole-body count gets near 2 MiB. Either way nothing oversized is read
   into a route; only the wording of the refusal differs.

   Not covered, deliberately: a route that never reads its body at all. Nothing
   is buffered in that case either, and the Content-Length refusal still
   catches every honest client; the alternative is draining bodies the app
   declared no interest in. GET/HEAD and the WebSocket handshake are untouched,
   so SSE responses and the terminal's WS upgrade never meet this counter.

4. Response headers on every HTTP response:
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

from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import ClientDisconnect
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import limits

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

TRUSTED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_TOO_LARGE = "request body too large"

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


def browser_origin_rejection(headers: Headers, scheme: str) -> str | None:
    """Apply the browser same-origin policy to a route that guards safe reads.

    The global middleware deliberately leaves GET/HEAD alone. Streaming
    routes may call this before reserving scarce server-side reader state.
    """
    own = _host_parts(headers.get("host"))
    if own is None or own[0] not in TRUSTED_HOSTS:
        return "untrusted Host"
    return _write_rejection(headers, own, scheme)


def embed_frame_csp(embed_url: str) -> str:
    """CSP value for a page that embeds `embed_url` in an iframe (#128).

    The middleware below stamps `frame-ancestors 'none'` only on responses
    that carry no CSP of their own, so a route that embeds sets this instead:
    the same frame-ancestors statement plus a deliberate `frame-src` limited
    to the embedded URL's exact origin. On every other page frame-src stays
    absent (framing unrestricted by CSP, as before); on the embedding pages
    this is strictly tighter, never wider. Callers pass a URL that already
    survived app.settings' loopback check, so the origin here is loopback.
    """
    parts = urlsplit(embed_url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    origin = f"{parts.scheme}://{host}"
    if parts.port is not None:
        origin += f":{parts.port}"
    return f"frame-ancestors 'none'; frame-src {origin}"


def _declared_length(headers: Headers) -> int | None:
    """A Content-Length this middleware is willing to act on, else None.

    Only a header that parses to a non-negative int is an early refusal. A
    missing, malformed or negative one is left entirely alone: the routes that
    care already answer it in their own vocabulary (the Learn endpoints return
    a typed JSON `invalid-request`), and the streaming count below is the
    authority in every case anyway.
    """
    raw = headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


class _CappedBody:
    """Counts the request body as the app pulls it, and cuts it off past `limit`.

    The count rides on chunks the app asked for, so nothing is buffered and no
    memory is held beyond what the app itself holds. Past the limit the app is
    handed `http.disconnect` instead of the next chunk, which Starlette raises
    as ClientDisconnect: a route can be denied its body, but never handed a
    short one it would mistake for the whole request.
    """

    def __init__(self, receive: Receive, limit: int) -> None:
        self._receive = receive
        self._limit = limit
        self._seen = 0
        self.tripped = False

    async def __call__(self) -> Message:
        if self.tripped:
            # Sticky: once the decision is made, the rest of an oversized body
            # stays on the socket rather than being read in to be discarded.
            return {"type": "http.disconnect"}
        message = await self._receive()
        if message["type"] == "http.request":
            self._seen += len(message.get("body", b""))
            if self._seen > self._limit:
                self.tripped = True
                return {"type": "http.disconnect"}
        return message


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

        cap: _CappedBody | None = None
        if scope["method"] in _UNSAFE_METHODS:
            reason = _write_rejection(headers, own, scope.get("scheme", "http"))
            if reason is not None:
                await self._refuse(scope, receive, send, 403, reason)
                return
            declared = _declared_length(headers)
            if declared is not None and declared > limits.MAX_BODY_BYTES:
                await self._refuse(scope, receive, send, 413, _TOO_LARGE)
                return
            cap = _CappedBody(receive, limits.MAX_BODY_BYTES)

        answered = False

        async def send_with_headers(message: Message) -> None:
            nonlocal answered
            if cap is not None and cap.tripped:
                # Whatever the app is about to say describes a request it was
                # not allowed to finish reading — commonly FastAPI's own 400
                # for a body it could not parse. Replace it, once, and drop the
                # rest of its messages.
                if not answered:
                    answered = True
                    await self._refuse(scope, receive, send, 413, _TOO_LARGE)
                return
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                for key, value in _RESPONSE_HEADERS:
                    if key not in out:
                        out[key] = value
                if "Content-Security-Policy" not in out:
                    out["Content-Security-Policy"] = "frame-ancestors 'none'"
            await send(message)

        try:
            await self.app(scope, receive if cap is None else cap, send_with_headers)
        except ClientDisconnect:
            # A route reading its body directly (app/request_body.py) lets this
            # propagate instead of turning it into a response. If it is the
            # disconnect this middleware manufactured, the client is owed the
            # 413; a genuine one is re-raised exactly as before.
            if cap is None or not cap.tripped or answered:
                raise
            answered = True
            await self._refuse(scope, receive, send, 413, _TOO_LARGE)

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
