#!/usr/bin/env python3
"""On-host proof for the E1 bubblewrap profile invariants.

The script uses only throwaway bundles under /tmp.  By default it also makes
one minimal, non-persistent Codex API call through the local xray proxy; pass
``--skip-agent-api`` for the filesystem/network-only proof.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.sandbox import build_sandbox_argv, require_sandbox_runtime  # noqa: E402


PROXY_HTTP = "http://127.0.0.1:10809"
PROXY_SOCKS = "socks5h://127.0.0.1:10808"
SENTINEL = "E1_AGENT_API_OK"

_INSIDE_PROBE = r"""
import json, os, socket, sys
from pathlib import Path

profile, bundle, repo = sys.argv[1:]
expected_home = {
    "lesson-agent": {".cache", ".claude", ".claude.json", ".codex", ".local", ".nvm", "go"},
    "lesson-learner": {".cache", ".local", "go"},
    "lesson-runner": {".local"},
}[profile]
home_entries = {entry.name for entry in Path("/home/aina").iterdir()}
probe_file = Path(bundle) / ".e1-write-probe"
try:
    probe_file.write_text("scratch", encoding="utf-8")
    bundle_writable = True
    probe_file.unlink()
except OSError:
    bundle_writable = False
sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(("127.0.0.1", 10809))
    proxy_reachable = True
except OSError:
    proxy_reachable = False
finally:
    sock.close()
print(json.dumps({
    "profile": profile,
    "repo_absent": not Path(repo).exists(),
    "home_blanked": home_entries == expected_home,
    "home_entries": sorted(home_entries),
    "bundle_access": "rw" if bundle_writable else "ro",
    "cwd": os.getcwd(),
    "network": "host" if proxy_reachable else "none",
    "proxy_reachable": proxy_reachable,
}, sort_keys=True))
"""


def clean_env(*, proxy: bool) -> dict[str, str]:
    env = {
        "HOME": "/home/aina",
        "USER": "aina",
        "LOGNAME": "aina",
        "SHELL": "/bin/bash",
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "PATH": (
            "/home/aina/.local/bin:"
            "/home/aina/.nvm/versions/node/v24.14.0/bin:"
            "/usr/local/bin:/usr/bin:/bin"
        ),
    }
    if proxy:
        env.update({
            "HTTP_PROXY": PROXY_HTTP, "http_proxy": PROXY_HTTP,
            "HTTPS_PROXY": PROXY_HTTP, "https_proxy": PROXY_HTTP,
            "ALL_PROXY": PROXY_SOCKS, "all_proxy": PROXY_SOCKS,
        })
    return env


def run_profile(profile: str, bundle: Path) -> dict[str, object]:
    command = [
        "/usr/bin/python3", "-c", _INSIDE_PROBE,
        profile, str(bundle), str(ROOT),
    ]
    result = subprocess.run(
        build_sandbox_argv(profile, bundle) + ["--", *command],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=clean_env(proxy=profile == "lesson-agent"),
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"{profile}: probe failed: {result.stderr.strip()}")
    payload = json.loads(result.stdout)
    expected_access = "ro" if profile == "lesson-runner" else "rw"
    expected_network = "host" if profile == "lesson-agent" else "none"
    expected_cwd = str(bundle) if profile != "lesson-runner" else "/run/ephemeris-runner"
    if not (
        payload["repo_absent"]
        and payload["home_blanked"]
        and payload["bundle_access"] == expected_access
        and payload["network"] == expected_network
        and payload["cwd"] == expected_cwd
    ):
        raise SystemExit(f"{profile}: invariant mismatch: {json.dumps(payload, sort_keys=True)}")
    return payload


def run_agent_api(bundle: Path) -> None:
    command = [
        "codex", "exec", "--ephemeral", "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox", "--color", "never",
        "-m", "gpt-5.6-sol",
        f"Reply with exactly {SENTINEL} and nothing else.",
    ]
    result = subprocess.run(
        build_sandbox_argv("lesson-agent", bundle) + ["--", *command],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=clean_env(proxy=True),
        check=False,
        timeout=180,
    )
    if result.returncode != 0 or SENTINEL not in result.stdout:
        detail = " ".join(result.stderr.split())[-500:]
        raise SystemExit(f"lesson-agent: Codex API probe failed: {detail}")
    print("agent_api codex=ok via=http://127.0.0.1:10809")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-agent-api", action="store_true")
    args = parser.parse_args()

    require_sandbox_runtime()
    with tempfile.TemporaryDirectory(prefix="ephemeris-e1-probe-", dir="/tmp") as raw:
        bundle = Path(raw) / "invented-demo-bundle"
        bundle.mkdir()
        for profile in ("lesson-agent", "lesson-learner", "lesson-runner"):
            print(json.dumps(run_profile(profile, bundle), sort_keys=True))
        if not args.skip_agent_api:
            run_agent_api(bundle)
    print("E1 sandbox profile probe: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
