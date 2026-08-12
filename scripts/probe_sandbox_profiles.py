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
import pwd
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.sandbox import (  # noqa: E402
    RUNNER_WORKDIR,
    USER_HOME,
    build_sandbox_argv,
    open_runner_module_cache_fd,
    require_sandbox_runtime,
)

# The same passwd entry the sandbox resolves its home from, so this probe
# describes the host it runs on rather than the one it was written on.
OWNER = pwd.getpwuid(os.getuid())


PROXY_HTTP = "http://127.0.0.1:10809"
PROXY_SOCKS = "socks5h://127.0.0.1:10808"
SENTINEL = "E1_AGENT_API_OK"

_INSIDE_PROBE = r"""
import json, os, socket, sys
from pathlib import Path

profile, bundle, repo, home, expected = sys.argv[1:]
expected_home = set(json.loads(expected))
home_entries = {entry.name for entry in Path(home).iterdir()}
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


# What the blanked home should contain, per profile. The unconditional entries
# come from mounts that CREATE their target (`--tmpfs`, `--dir`); the optional
# ones from `-try` mounts, which contribute nothing when their host source is
# absent — so a clean account using only /usr/bin/bwrap is healthy with fewer
# entries, not broken.
_ALWAYS_HOME_ENTRIES = {
    "lesson-agent": {".claude", ".codex"},
    "lesson-learner": set(),
    "lesson-runner": {"go"},
}
# Each name maps to EVERY `-try` source that would create it: one entry appears
# as soon as any of its sources does, which is why these are tuples. `.local`
# is the one that has more than one today.
_OPTIONAL_HOME_ENTRIES = {
    "lesson-agent": {
        ".cache": (f"{USER_HOME}/.cache/go-build",),
        ".claude.json": (f"{USER_HOME}/.claude.json",),
        ".local": (
            f"{USER_HOME}/.local/bin",
            f"{USER_HOME}/.local/share/claude/versions",
        ),
        ".nvm": (f"{USER_HOME}/.nvm/versions",),
        "go": (f"{USER_HOME}/go",),
    },
    "lesson-learner": {
        ".cache": (f"{USER_HOME}/.cache/go-build",),
        ".local": (f"{USER_HOME}/.local/bin",),
        "go": (f"{USER_HOME}/go",),
    },
    "lesson-runner": {},
}


def expected_home_entries(profile: str) -> set[str]:
    return _ALWAYS_HOME_ENTRIES[profile] | {
        name for name, sources in _OPTIONAL_HOME_ENTRIES[profile].items()
        if any(Path(source).exists() for source in sources)
    }


def clean_env(*, proxy: bool) -> dict[str, str]:
    env = {
        "HOME": USER_HOME,
        "USER": OWNER.pw_name,
        "LOGNAME": OWNER.pw_name,
        "SHELL": "/bin/bash",
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "PATH": (
            f"{USER_HOME}/.local/bin:"
            f"{USER_HOME}/.nvm/versions/node/v24.14.0/bin:"
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


def run_profile(
    profile: str,
    bundle: Path,
    bundle_root: Path,
) -> dict[str, object]:
    command = [
        "/usr/bin/python3", "-c", _INSIDE_PROBE,
        profile, str(bundle), str(ROOT), USER_HOME,
        json.dumps(sorted(expected_home_entries(profile))),
    ]
    module_cache_fd = (
        open_runner_module_cache_fd() if profile == "lesson-runner" else None
    )
    try:
        result = subprocess.run(
            build_sandbox_argv(
                profile, bundle, bundle_root=bundle_root,
                private_root=(
                    bundle_root.parent if profile == "lesson-runner" else None
                ),
                module_cache_fd=module_cache_fd,
            ) + ["--", *command],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=clean_env(proxy=profile in ("lesson-agent", "lesson-learner")),
            check=False,
            pass_fds=((module_cache_fd,) if module_cache_fd is not None else ()),
        )
    finally:
        if module_cache_fd is not None:
            os.close(module_cache_fd)
    if result.returncode != 0:
        raise SystemExit(f"{profile}: probe failed: {result.stderr.strip()}")
    payload = json.loads(result.stdout)
    expected_access = "ro" if profile == "lesson-runner" else "rw"
    expected_network = "none" if profile == "lesson-runner" else "host"
    expected_cwd = str(bundle) if profile != "lesson-runner" else RUNNER_WORKDIR
    if not (
        payload["repo_absent"]
        and payload["home_blanked"]
        and payload["bundle_access"] == expected_access
        and payload["network"] == expected_network
        and payload["cwd"] == expected_cwd
    ):
        raise SystemExit(f"{profile}: invariant mismatch: {json.dumps(payload, sort_keys=True)}")
    return payload


def run_agent_api(bundle: Path, bundle_root: Path) -> None:
    command = [
        "codex", "exec", "--ephemeral", "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox", "--color", "never",
        "-m", "gpt-5.6-sol",
        f"Reply with exactly {SENTINEL} and nothing else.",
    ]
    result = subprocess.run(
        build_sandbox_argv(
            "lesson-agent", bundle, bundle_root=bundle_root
        ) + ["--", *command],
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
        bundle_root = Path(raw)
        bundle = bundle_root / "invented-demo-bundle"
        bundle.mkdir()
        for profile in ("lesson-agent", "lesson-learner", "lesson-runner"):
            payload = run_profile(profile, bundle, bundle_root)
            print(json.dumps(payload, sort_keys=True))
        if not args.skip_agent_api:
            run_agent_api(bundle, bundle_root)
    print("E1 sandbox profile probe: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
