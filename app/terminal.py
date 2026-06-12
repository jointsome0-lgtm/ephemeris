"""Desktop / localhost-only terminal tab — a PTY bridged to xterm.js over a WebSocket.

Goal 2 of the agent feature (see memory `agent-feature-plan`): a simple terminal
for running general agents (Claude Code, codex, aider) and shell commands.

SECURITY: this grants full shell access. The app itself runs on the LAN (0.0.0.0)
with NO AUTH, so the terminal MUST never be reachable from another device. The socket
rejects any non-loopback peer AND validates the Host/Origin headers — so a browser the
local user visits cannot be used as a confused deputy (cross-site WebSocket hijacking),
and DNS-rebinding is blocked; the drawer UI (in base.html) is only rendered for local
clients. Access it from the machine running the server, via
http://localhost:<port> / http://127.0.0.1:<port> — NOT the LAN IP.
NOTE: do NOT run uvicorn with --proxy-headers or behind a forwarded-headers proxy, or
`scope["client"]` could become attacker-influenced and weaken the loopback peer check.

The UI is a GCP-style bottom drawer docked over any page (toggled from the rail icon
or Ctrl+`); there is no dedicated page route, only this websocket.
"""
from __future__ import annotations

import asyncio
import fcntl
import ipaddress
import json
import os
import pty
import signal
import socket
import struct
import termios
import time
from collections import deque
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates

# Repo root: a sensible cwd so agents/commands run against the project by default.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOOPBACK_CLOSE = 1008  # WebSocket "policy violation"


def is_local_host(host: str | None) -> bool:
    """True only for loopback peers (127.0.0.0/8, ::1, IPv4-mapped loopback)."""
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_loopback


def _client_is_local(request: Request) -> bool:
    """Template helper: is this request from the local machine? (gates the rail link)"""
    return bool(request.client) and is_local_host(request.client.host)


def _is_loopback_hostname(hostname: str | None) -> bool:
    """True for a loopback hostname — the name 'localhost' or any loopback IP."""
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    return is_local_host(hostname)


def _ws_is_trusted(ws: WebSocket) -> bool:
    """A loopback peer is necessary but NOT sufficient: a browser can be a confused
    deputy. Also require the Host header to be a loopback name (blocks DNS-rebinding)
    and every Origin value, if any is present, to be a loopback origin (blocks cross-site
    WebSocket hijacking — a malicious page the local user visits opening ws://127.0.0.1/...).
    Origin is absent for non-browser local clients, which the peer check already trusts;
    a browser always sends exactly one browser-controlled Origin and cannot suppress it."""
    if not is_local_host(ws.client.host if ws.client else None):
        return False
    if not _is_loopback_hostname(urlsplit("//" + (ws.headers.get("host") or "")).hostname):
        return False
    # getlist (not get) so a smuggled duplicate "Origin: <loopback>" + "Origin: <evil>"
    # can't slip through on the first value alone — reject if ANY value is non-loopback.
    for origin in ws.headers.getlist("origin"):
        if not _is_loopback_hostname(urlsplit(origin).hostname):
            return False
    return True


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


def _child_setup() -> None:
    """Run in the forked child before exec: own a new session and make the pty
    slave (fd 0) our controlling terminal, so job control + isatty work."""
    os.setsid()
    try:
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    except OSError:
        pass


# --- egress proxy for agent CLIs -------------------------------------------------
# Common loopback ports for popular local proxy clients:
#   xray / v2ray 10808 (socks) + 10809 (http), Clash / mihomo 7890 / 7891,
#   sing-box 2080 (socks/mixed), plus generic 1080 / 8080 / 8118.
_HTTP_PROXY_PORTS = (10809, 7890, 8118, 8080, 8889)
_SOCKS_PROXY_PORTS = (10808, 7891, 1080, 2080)
# Loopback literals are honored by every client and cover this app's own calls; the
# CIDR LAN ranges are best-effort (only some clients parse CIDR in NO_PROXY).
_NO_PROXY = "localhost,127.0.0.1,::1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12"
# Presence of any of these => "already configured"; the full set is what we clear/re-emit.
_PROXY_SET_VARS = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
_PROXY_ENV_VARS = _PROXY_SET_VARS + ("NO_PROXY", "no_proxy", "FTP_PROXY", "ftp_proxy")


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.15) -> bool:
    """Cheap liveness probe for a local proxy listener."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _socks5h(url: str) -> str:
    """Upgrade socks5:// -> socks5h:// so the proxy resolves DNS remotely. Local DNS
    can be poisoned/blocked on a censored network, which would defeat the bypass."""
    return "socks5h://" + url[len("socks5://"):] if url.startswith("socks5://") else url


def _detect_proxy_env() -> dict[str, str]:
    """Pick an egress so agent CLIs (codex, claude) work from a geo-blocked network.

    The systemd service runs with NO proxy, so by default the agents dial
    OpenAI/Anthropic on the raw public IP — which a country-level block (e.g. RU)
    answers with HTTP 403. We instead route them through the user's existing local
    proxy. Precedence:

      1. ``TICKLIKE_TERM_PROXY=off``   -> force a direct connection (no proxy);
      2. ``TICKLIKE_TERM_PROXY=<url>`` -> use exactly this (``http://…`` / ``socks5h://…``);
      3. a proxy already in the service env -> inherit it verbatim;
      4. else auto-detect a proxy on a common loopback port.

    Contract: the return value is the COMPLETE set of proxy vars the child shell
    should have. The caller clears any ambient proxy vars first, then applies this,
    so an empty dict reliably means "connect directly".
    """
    override = os.environ.get("TICKLIKE_TERM_PROXY", "").strip()
    if override.lower() in {"off", "none", "0", "false"}:
        return {}

    http_url = socks_url = ""
    if override:
        if override.startswith("socks"):
            socks_url = _socks5h(override)
        else:
            http_url = override
    elif any(os.environ.get(v) for v in _PROXY_SET_VARS):
        # already configured upstream — preserve it verbatim (incl. NO_PROXY)
        return {k: os.environ[k] for k in _PROXY_ENV_VARS if k in os.environ}
    else:
        for p in _HTTP_PROXY_PORTS:
            if _port_open(p):
                http_url = f"http://127.0.0.1:{p}"
                break
        for p in _SOCKS_PROXY_PORTS:
            if _port_open(p):
                socks_url = f"socks5h://127.0.0.1:{p}"
                break

    if not (http_url or socks_url):
        return {}
    http_url = http_url or socks_url  # let HTTP(S) ride the socks proxy if that's all we found

    env = {
        "NO_PROXY": _NO_PROXY, "no_proxy": _NO_PROXY,
        "HTTP_PROXY": http_url, "http_proxy": http_url,
        "HTTPS_PROXY": http_url, "https_proxy": http_url,
    }
    if socks_url:
        env["ALL_PROXY"] = env["all_proxy"] = socks_url
    return env


# --- persistent terminal sessions -----------------------------------------------
# The PTY/shell outlives any single WebSocket so the terminal survives page
# navigation (a full reload in this MPA): the browser keeps a session id and each
# page reattaches, replaying the scrollback. Output is always drained into a ring
# buffer — even while detached — so nothing from a long-running agent is lost.
_SESSION_TTL = 60 * 60          # reap a detached session after 1h idle
_FORCE_GRACE = 5.0              # never force-evict a session detached < this (protects in-flight ones)
_MAX_SESSIONS = 8               # bound the number of live shells
_RING_BYTES = 256 * 1024        # scrollback bytes replayed on reattach
_REAP_INTERVAL = 5 * 60         # background idle sweep, so a lone session is reaped without new traffic
_SESSIONS: dict[str, "_TermSession"] = {}
_CREATE_LOCK = asyncio.Lock()  # serialize creation so the cap check is atomic (loop-lazy since 3.10)
_REAPER_TASK: "asyncio.Task | None" = None  # periodic _reap_idle sweep (lazy-started on first connect)


class _TermSession:
    """A shell on a PTY, plus a ring buffer of recent output. At most one WebSocket
    is 'attached' at a time; a background pump drains the PTY regardless."""

    def __init__(self, sid: str, proc, master_fd: int) -> None:
        self.sid = sid
        self.proc = proc
        self.master_fd = master_fd
        self.ws: WebSocket | None = None
        self.rows = 24
        self.cols = 80
        self.closed = False
        self.detached_at = time.monotonic()
        self._chunks: deque[bytes] = deque()
        self._buf_len = 0
        self._pump: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()  # serialize replay vs pump sends on one socket
        self._attach_lock = asyncio.Lock()  # serialize boot-old + attach so one PTY has one reader

    def remember(self, data: bytes) -> None:
        self._chunks.append(data)
        self._buf_len += len(data)
        while self._buf_len > _RING_BYTES and len(self._chunks) > 1:
            self._buf_len -= len(self._chunks.popleft())

    def snapshot(self) -> bytes:
        return b"".join(self._chunks)

    def attach(self, ws: WebSocket) -> None:
        self.ws = ws
        self.detached_at = 0.0

    def detach(self, ws: WebSocket) -> None:
        if self.ws is ws:
            self.ws = None
            self.detached_at = time.monotonic()

    def start(self) -> None:
        self._pump = asyncio.create_task(self._run())

    async def _run(self) -> None:
        """Drain the PTY into the ring buffer (and the attached ws) until EOF."""
        loop = asyncio.get_running_loop()
        try:
            while True:
                data = await loop.run_in_executor(None, os.read, self.master_fd, 65536)
                if not data:  # shell exited → EOF
                    break
                self.remember(data)
                ws = self.ws
                if ws is not None:
                    try:
                        async with self._send_lock:      # never overlaps the replay send
                            if self.ws is ws:
                                await ws.send_bytes(data)
                    except (RuntimeError, WebSocketDisconnect, OSError):
                        self.detach(ws)  # only detaches the socket that actually failed
        except (OSError, RuntimeError):
            pass
        finally:
            await self.close()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        _SESSIONS.pop(self.sid, None)
        if self.proc.returncode is None:
            try:
                self.proc.send_signal(signal.SIGHUP)
                await asyncio.wait_for(self.proc.wait(), timeout=2)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self.proc.kill()
                    await self.proc.wait()  # reap the child so kill-on-timeout leaves no zombie
                except ProcessLookupError:
                    pass
        try:
            os.close(self.master_fd)
        except OSError:
            pass
        if self._pump is not None and not self._pump.done() and self._pump is not asyncio.current_task():
            self._pump.cancel()
        ws, self.ws = self.ws, None
        if ws is not None:
            try:
                await ws.close()
            except RuntimeError:
                pass


def _reap_idle(force_oldest: bool = False) -> None:
    """Close sessions detached longer than the TTL (lazy, on each new connection).
    With force_oldest, also evict the oldest detached session to free a slot."""
    now = time.monotonic()
    stale = [s for s in _SESSIONS.values()
             if s.ws is None and s.detached_at and now - s.detached_at > _SESSION_TTL]
    if force_oldest and not stale:
        evictable = [s for s in _SESSIONS.values()
                     if s.ws is None and s.detached_at and now - s.detached_at > _FORCE_GRACE]
        if evictable:
            stale = [min(evictable, key=lambda s: s.detached_at)]
    for s in stale:
        _SESSIONS.pop(s.sid, None)      # free the slot immediately; close() is async
        asyncio.create_task(s.close())


async def _reaper_loop() -> None:
    """Periodic idle sweep so a lone detached session is reaped at its TTL even when no
    new connection arrives (the lazy on-connect _reap_idle would otherwise never fire)."""
    while True:
        await asyncio.sleep(_REAP_INTERVAL)
        try:
            _reap_idle()
        except Exception:
            pass  # a transient reap error must not kill the periodic sweep


def _ensure_reaper() -> None:
    """Lazily (idempotently) start the background reaper. Called on connect — which runs
    under the event loop — and cancelled in shutdown_terminal()."""
    global _REAPER_TASK
    if _REAPER_TASK is None or _REAPER_TASK.done():
        _REAPER_TASK = asyncio.create_task(_reaper_loop())


async def _create_session() -> "_TermSession | None":
    """Spawn a fresh shell on a PTY and register it. Returns None at capacity or on a
    spawn failure. Serialized via _CREATE_LOCK so the capacity check is atomic."""
    async with _CREATE_LOCK:
        if len(_SESSIONS) >= _MAX_SESSIONS:
            _reap_idle(force_oldest=True)
            if len(_SESSIONS) >= _MAX_SESSIONS:
                return None

        shell = os.environ.get("SHELL") or "/bin/bash"
        env = {**os.environ, "TERM": "xterm-256color"}
        # Help find user-installed agent CLIs even under a minimal service PATH.
        home = os.path.expanduser("~")
        env["PATH"] = f"{home}/.local/bin:/usr/local/bin:" + env.get("PATH", "/usr/bin:/bin")
        # Route agent CLIs around country-level blocks via the user's local proxy (if any).
        # Clear the ambient proxy slate first so TICKLIKE_TERM_PROXY=off truly means direct.
        # Detection runs in a worker thread: its socket probes block (up to ~0.15s/port),
        # which must not stall the event loop (and every other PTY pump) mid-detect.
        for _k in _PROXY_ENV_VARS:
            env.pop(_k, None)
        proxy = await asyncio.to_thread(_detect_proxy_env)
        env.update(proxy)

        master_fd, slave_fd = pty.openpty()
        try:
            proc = await asyncio.create_subprocess_exec(
                shell, "-i",
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                preexec_fn=_child_setup,
                cwd=str(_REPO_ROOT),
                env=env,
            )
        except (OSError, ValueError):
            os.close(master_fd)  # no proc took ownership of the master end — don't leak it
            os.close(slave_fd)
            return None
        os.close(slave_fd)  # success: parent keeps only the master end

        sess = _TermSession(token_urlsafe(18), proc, master_fd)
        _SESSIONS[sess.sid] = sess
        if proxy.get("HTTP_PROXY"):  # informational banner, replayed with the scrollback
            shown = "".join(c for c in proxy["HTTP_PROXY"] if c.isprintable())  # defang control bytes
            sess.remember(
                (f"\x1b[2m· terminal egress via proxy {shown} — agents bypass geo-blocks; "
                 f"localhost direct (TICKLIKE_TERM_PROXY=off to disable).\x1b[0m\r\n").encode()
            )
        sess.start()
        return sess


async def _write_all(fd: int, data: bytes) -> None:
    """Write all of `data` to `fd` off the event loop (a PTY master can briefly block
    if the program at the slave end has stopped draining stdin)."""
    loop = asyncio.get_running_loop()
    mv = memoryview(data)
    while mv:
        n = await loop.run_in_executor(None, os.write, fd, mv)
        mv = mv[n:]


async def _read_input(ws: WebSocket, sess: "_TermSession") -> None:
    """Pump client → PTY: binary frames are keystrokes; TEXT JSON is control."""
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if data is not None:
                if sess.ws is not ws:  # booted by a newer attach to this sid — stop writing
                    break
                await _write_all(sess.master_fd, data)
                continue
            text = msg.get("text")
            if not text:
                continue
            try:
                ctrl = json.loads(text)
            except ValueError:
                continue
            if not isinstance(ctrl, dict):
                continue
            kind = ctrl.get("type")
            if kind == "resize":
                try:
                    rows = max(1, min(65535, int(ctrl.get("rows", 24))))
                    cols = max(1, min(65535, int(ctrl.get("cols", 80))))
                except (TypeError, ValueError):
                    continue
                sess.rows, sess.cols = rows, cols
                _set_winsize(sess.master_fd, rows, cols)
            elif kind == "kill":
                await sess.close()
                break
    except (OSError, RuntimeError, WebSocketDisconnect):
        pass


async def _serve_ws(ws: WebSocket) -> None:
    """Accept a loopback-only WS and attach it to a new or existing session."""
    if not _ws_is_trusted(ws):
        await ws.close(code=_LOOPBACK_CLOSE)
        return
    await ws.accept()
    _reap_idle()
    _ensure_reaper()

    sid = ws.query_params.get("sid")
    sess = _SESSIONS.get(sid) if sid else None
    if sess is not None and sess.closed:
        sess = None
    if sess is None:
        sess = await _create_session()
        if sess is None:
            try:
                await ws.send_bytes(b"\r\n\x1b[31m[terminal: too many sessions]\x1b[0m\r\n")
            except (RuntimeError, WebSocketDisconnect):
                pass
            await ws.close()
            return

    # Hold the per-session attach lock across the WHOLE boot-old + attach sequence, so two
    # pages racing the same sid can't both end up in _read_input on one PTY: the second
    # waits here, then boots the first (whose _read_input ends when its socket closes).
    # Inside, snapshot()+attach() run with no await between them and the pump takes the
    # send lock too — so every PTY chunk is either in this snapshot or sent right after,
    # with no concurrent socket send, no duplicate, no dropped gap during replay.
    try:
        async with sess._attach_lock:
            old = sess.ws  # single-attach: boot a stale socket before taking over
            if old is not None and old is not ws:
                sess.ws = None
                try:
                    await old.close()
                except RuntimeError:
                    pass
            await ws.send_text(json.dumps({"type": "session", "sid": sess.sid}))
            async with sess._send_lock:
                snap = sess.snapshot()
                sess.attach(ws)
                if snap:
                    await ws.send_bytes(snap)
    except (RuntimeError, WebSocketDisconnect):
        sess.detach(ws)
        return

    if sess.rows and sess.cols:
        _set_winsize(sess.master_fd, sess.rows, sess.cols)
    try:
        await _read_input(ws, sess)
    finally:
        sess.detach(ws)


async def shutdown_terminal() -> None:
    """Kill every live shell — called from the app lifespan teardown so persistent
    sessions don't outlive the server (also covered by systemd's cgroup kill)."""
    global _REAPER_TASK
    if _REAPER_TASK is not None:
        _REAPER_TASK.cancel()
        _REAPER_TASK = None
    # Concurrently: each close() can wait up to 2s for a shell that ignores SIGHUP,
    # so a serial loop would add up to _MAX_SESSIONS × 2s to every service restart.
    if _SESSIONS:
        await asyncio.gather(*(s.close() for s in list(_SESSIONS.values())),
                             return_exceptions=True)


def setup_terminal(app: FastAPI, templates: Jinja2Templates) -> None:
    """Register the localhost-only terminal websocket + the `client_is_local`
    template helper (which gates the drawer UI rendered in base.html)."""
    templates.env.globals.setdefault("client_is_local", _client_is_local)

    @app.websocket("/terminal/ws")
    async def terminal_ws(ws: WebSocket):
        await _serve_ws(ws)
