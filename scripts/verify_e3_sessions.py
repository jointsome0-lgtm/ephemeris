#!/usr/bin/env python3
"""Throwaway loopback integration proof for phase E session E3.

The script owns its temporary data directory and uvicorn child. It never reads
or writes the configured live Ephemeris instance.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ROLE_PARAM = "role"
PROXY_NAMES = (
    "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
    "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
    "FTP_PROXY", "ftp_proxy",
)
SOCKET_ENV_NAMES = ("SSH_AUTH_SOCK", "XDG_RUNTIME_DIR")


def _free_loopback_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_ready(port: int, proc: subprocess.Popen, log_path: Path) -> None:
    url = f"http://127.0.0.1:{port}/today"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"throwaway uvicorn exited {proc.returncode}: "
                f"{log_path.read_text(encoding='utf-8', errors='replace')}"
            )
        try:
            with urllib.request.urlopen(url, timeout=0.3) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("throwaway uvicorn did not become ready")


async def _handshake(ws) -> dict:
    for _ in range(20):
        message = await asyncio.wait_for(ws.recv(), timeout=3)
        if isinstance(message, str):
            parsed = json.loads(message)
            if parsed.get("type") == "session":
                return parsed
    raise RuntimeError("terminal session handshake not received")


async def _output_until(ws, marker: str) -> str:
    chunks: list[str] = []
    deadline = asyncio.get_running_loop().time() + 8
    while asyncio.get_running_loop().time() < deadline:
        message = await asyncio.wait_for(ws.recv(), timeout=3)
        text = message.decode("utf-8", "replace") if isinstance(message, bytes) else message
        chunks.append(text)
        joined = "".join(chunks)
        if marker in joined:
            return joined
    raise RuntimeError(f"terminal output marker not received: {marker}")


async def _refusal(uri: str, expected: bytes) -> bool:
    from websockets import connect

    async with connect(uri, open_timeout=5) as ws:
        message = await asyncio.wait_for(ws.recv(), timeout=3)
        return isinstance(message, bytes) and expected in message


async def _run_sessions(port: int, slug: str, bundle: Path) -> dict[str, bool | str]:
    from websockets import connect
    from websockets.exceptions import ConnectionClosed

    base = f"ws://127.0.0.1:{port}/terminal/ws"
    lesson = urllib.parse.quote(slug, safe="")
    invalid = b"invalid session request"
    refusals = await asyncio.gather(
        _refusal(f"{base}?{ROLE_PARAM}=lesson-learner", invalid),
        _refusal(f"{base}?lesson={lesson}&{ROLE_PARAM}=unknown", invalid),
        _refusal(f"{base}?sid=missing&{ROLE_PARAM}=lesson-learner", invalid),
    )

    agent_uri = f"{base}?lesson={lesson}&{ROLE_PARAM}=lesson-agent"
    learner_uri = f"{base}?lesson={lesson}&{ROLE_PARAM}=lesson-learner"
    async with connect(agent_uri, open_timeout=5) as agent:
        agent_handshake = await _handshake(agent)
        await agent.send((
            ("/usr/bin/python3 -c \"import socket; s=socket.socket(); "
             "s.settimeout(2); r=s.connect_ex(('127.0.0.1', %d)); s.close(); "
             "print('__E3_' + ('AGENT_NET_OK' if r == 0 else 'AGENT_NET_BLOCKED') + '__')\"; "
             "printf '__E3_%%s__\\n' 'AGENT_LIVE'\n") % port
        ).encode())
        agent_output = await _output_until(agent, "__E3_AGENT_LIVE__")

        briefs = [bundle / "AGENTS.md", bundle / "CLAUDE.md"]
        before = {
            path.name: (path.stat().st_mtime_ns, path.read_bytes())
            for path in briefs
        }

        async with connect(learner_uri, open_timeout=5) as learner:
            learner_handshake = await _handshake(learner)
            after_spawn = {
                path.name: (path.stat().st_mtime_ns, path.read_bytes())
                for path in briefs
            }
            proxy_expr = repr(PROXY_NAMES)
            socket_expr = repr(SOCKET_ENV_NAMES)
            await learner.send((
                ("/usr/bin/python3 -c \"import os,socket; "
                 "p=%s; s=socket.socket(); s.settimeout(2); "
                 "r=s.connect_ex(('127.0.0.1', %d)); s.close(); "
                 "print('__E3_' + ('LEARNER_NET_BLOCKED' if r != 0 else 'LEARNER_NET_OPEN') + '__'); "
                 "print('__E3_' + ('LEARNER_PROXY_NONE' if not any(k in os.environ for k in p) "
                 "else 'LEARNER_PROXY_PRESENT') + '__'); "
                 "q=%s; print('__E3_' + ('LEARNER_SOCKET_ENV_NONE' "
                 "if not any(k in os.environ for k in q) "
                 "else 'LEARNER_SOCKET_ENV_PRESENT') + '__')\"; "
                 "printf '__E3_%%s__\\n' 'LEARNER_LIVE'\n") % (proxy_expr, port, socket_expr)
            ).encode())
            learner_output = await _output_until(learner, "__E3_LEARNER_LIVE__")

            await agent.send(b"printf '__E3_%s__\\n' 'AGENT_STILL_LIVE'\n")
            agent_after = await _output_until(agent, "__E3_AGENT_STILL_LIVE__")

            await learner.send(json.dumps({"type": "kill"}))
            try:
                await asyncio.wait_for(learner.recv(), timeout=2)
            except (asyncio.TimeoutError, ConnectionClosed):
                pass
        stale_learner_refused = await _refusal(
            f"{base}?sid={urllib.parse.quote(learner_handshake['sid'], safe='')}"
            f"&lesson={lesson}",
            b"stale learner session",
        )
        await agent.send(json.dumps({"type": "kill"}))

    return {
        "wire_param": ROLE_PARAM,
        "selector_without_lesson_refused": refusals[0],
        "unknown_role_refused": refusals[1],
        "selector_with_sid_refused": refusals[2],
        "agent_role_echoed": agent_handshake.get("role") == "lesson-agent",
        "learner_role_echoed": learner_handshake.get("role") == "lesson-learner",
        "briefs_unchanged": before == after_spawn,
        "agent_network": "__E3_AGENT_NET_OK__" in agent_output,
        "learner_no_network": "__E3_LEARNER_NET_BLOCKED__" in learner_output,
        "learner_no_proxy_env": "__E3_LEARNER_PROXY_NONE__" in learner_output,
        "learner_no_socket_env": "__E3_LEARNER_SOCKET_ENV_NONE__" in learner_output,
        "stale_learner_sid_refused": stale_learner_refused,
        "both_shells_live": (
            "__E3_AGENT_STILL_LIVE__" in agent_after
            and "__E3_LEARNER_LIVE__" in learner_output
        ),
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ephemeris-e3-") as temp:
        temp_path = Path(temp)
        data_dir = temp_path / "data"
        env = os.environ.copy()
        env.pop("ACTIVITY_DB", None)
        os.environ.pop("ACTIVITY_DB", None)
        env.update({
            "ACTIVITY_DATA_DIR": str(data_dir),
            "EPHEMERIS_ENABLE_TERMINAL": "1",
            "EPHEMERIS_TERM_PROXY": "off",
            "EPHEMERIS_TRUSTED_HOSTS": "127.0.0.1,localhost,::1",
            "PYTHONUNBUFFERED": "1",
            "SHELL": "/bin/bash",
        })
        os.environ.update({key: env[key] for key in (
            "ACTIVITY_DATA_DIR", "EPHEMERIS_ENABLE_TERMINAL",
            "EPHEMERIS_TERM_PROXY", "EPHEMERIS_TRUSTED_HOSTS", "SHELL",
        )})

        from app.db import get_conn, init_db
        from app.services.lessons import create_lesson, get_lesson

        init_db()
        conn = get_conn()
        try:
            lesson_id = create_lesson(conn, "Invented E3 Integration Lesson")
            lesson = get_lesson(conn, lesson_id)
        finally:
            conn.close()
        slug = lesson["slug"]
        bundle = data_dir / "lessons" / slug

        port = _free_loopback_port()
        log_path = temp_path / "uvicorn.log"
        with log_path.open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app",
                 "--host", "127.0.0.1", "--port", str(port),
                 "--no-proxy-headers", "--log-level", "warning"],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                _wait_ready(port, proc, log_path)
                result = asyncio.run(_run_sessions(port, slug, bundle))
            finally:
                # This is only the throwaway child created above, never the service.
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

        print(json.dumps(result, sort_keys=True))
        return 0 if all(value for key, value in result.items() if key != "wire_param") else 1


if __name__ == "__main__":
    raise SystemExit(main())
