"""Bubblewrap launcher primitives for isolated lesson roles.

E1 defines the profiles and fail-closed spawn seam only.  Live terminal
sessions are intentionally not routed through this module until E2.
"""
from __future__ import annotations

import asyncio
import os
import resource
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Callable, Literal, Mapping, Sequence


SandboxProfile = Literal["lesson-agent", "lesson-learner", "lesson-runner"]

BWRAP = "/home/aina/.local/bin/bwrap"
USER_HOME = "/home/aina"
RUNNER_WORKDIR = "/tmp/ephemeris-runner"


class SandboxError(RuntimeError):
    """A sandboxed role could not be started; callers must refuse visibly."""


class SandboxUnavailableError(SandboxError):
    """The cached bubblewrap runtime probe failed."""


class SandboxSpawnError(SandboxError):
    """Bubblewrap could not be spawned for a sandboxed role."""


@dataclass(frozen=True)
class _HomeMount:
    flag: str
    target: str
    reason: str
    source: str | None = None

    def argv(self) -> list[str]:
        if self.flag == "--tmpfs":
            return [self.flag, self.target]
        return [self.flag, self.source or self.target, self.target]


# Every path re-exposed below the blank home is listed here with its reason.
_COMMON_HOME_MOUNTS = (
    _HomeMount("--ro-bind", f"{USER_HOME}/.local/bin",
               "user-installed command shims used by every lesson role"),
)

_AGENT_HOME_MOUNTS = (
    _HomeMount("--ro-bind-try", f"{USER_HOME}/.nvm/versions",
               "the installed Codex Node runtime and package"),
    _HomeMount("--ro-bind-try", f"{USER_HOME}/.local/share/claude/versions",
               "the installed Claude native binary targeted by its shim"),
    _HomeMount("--tmpfs", f"{USER_HOME}/.codex",
               "ephemeral writable Codex session and app-server state"),
    _HomeMount("--ro-bind-try", f"{USER_HOME}/.codex/auth.json",
               "Codex login material, deliberately read-only"),
    _HomeMount("--ro-bind-try", f"{USER_HOME}/.codex/config.toml",
               "Codex configuration, deliberately read-only"),
    _HomeMount("--tmpfs", f"{USER_HOME}/.claude",
               "ephemeral writable Claude session and cache state"),
    _HomeMount("--ro-bind-try", f"{USER_HOME}/.claude/.credentials.json",
               "Claude login material, deliberately read-only"),
    _HomeMount("--ro-bind-try", f"{USER_HOME}/.claude/settings.json",
               "Claude configuration, deliberately read-only"),
    _HomeMount("--ro-bind-try", f"{USER_HOME}/.claude.json",
               "Claude installation/account metadata, deliberately read-only"),
    _HomeMount("--bind-try", f"{USER_HOME}/go",
               "writable Go module cache for agent-driven dependency work"),
    _HomeMount("--bind-try", f"{USER_HOME}/.cache/go-build",
               "writable Go build cache for agent-driven builds"),
)

_LEARNER_HOME_MOUNTS = (
    _HomeMount("--ro-bind-try", f"{USER_HOME}/go",
               "warm Go module cache for offline learner builds"),
    _HomeMount("--ro-bind-try", f"{USER_HOME}/.cache/go-build",
               "warm Go build cache for offline learner builds"),
)

_PROFILES: tuple[SandboxProfile, ...] = (
    "lesson-agent", "lesson-learner", "lesson-runner",
)


def _pure_bundle_path(bundle_dir: str | os.PathLike[str]) -> str:
    """Validate the mount target lexically; never inspect the filesystem."""
    path = Path(bundle_dir)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("bundle_dir must be an absolute path without '..'")
    return str(path)


def build_sandbox_argv(
    profile: SandboxProfile,
    bundle_dir: str | os.PathLike[str],
) -> list[str]:
    """Purely build the bubblewrap prefix for ``profile`` and ``bundle_dir``.

    The returned argv ends at the profile's ``--chdir``.  The caller appends
    ``--`` and the command to execute.  No path is resolved or probed here.
    """
    if profile not in _PROFILES:
        raise ValueError(f"unknown sandbox profile: {profile}")
    bundle = _pure_bundle_path(bundle_dir)

    argv = [BWRAP, "--unshare-all"]
    if profile == "lesson-agent":
        argv.append("--share-net")
    argv.extend([
        "--die-with-parent",
        "--ro-bind", "/", "/",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        "--tmpfs", USER_HOME,
    ])

    mounts = list(_COMMON_HOME_MOUNTS)
    if profile == "lesson-agent":
        mounts.extend(_AGENT_HOME_MOUNTS)
    elif profile == "lesson-learner":
        mounts.extend(_LEARNER_HOME_MOUNTS)
    for mount in mounts:
        argv.extend(mount.argv())

    if profile == "lesson-runner":
        argv.extend([
            "--ro-bind", bundle, bundle,
            "--dir", RUNNER_WORKDIR,
            "--chdir", RUNNER_WORKDIR,
        ])
    else:
        argv.extend(["--bind", bundle, bundle, "--chdir", bundle])
    return argv


@dataclass(frozen=True)
class _ProbeResult:
    available: bool
    detail: str = ""


@cache
def _cached_runtime_probe() -> _ProbeResult:
    """Run bubblewrap's process-lifetime probe once, caching failures too."""
    argv = [
        BWRAP,
        "--unshare-user",
        "--die-with-parent",
        "--ro-bind", "/", "/",
        "true",
    ]
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as exc:
        return _ProbeResult(False, str(exc))
    if result.returncode == 0:
        return _ProbeResult(True)
    detail = " ".join((result.stderr or "").split())
    return _ProbeResult(False, detail[:500] or f"exit {result.returncode}")


def require_sandbox_runtime() -> None:
    """Raise a visible refusal when the cached runtime probe is not healthy."""
    result = _cached_runtime_probe()
    if not result.available:
        raise SandboxUnavailableError(
            f"sandbox runtime probe failed: {result.detail}"
        )


_GENEROUS_LIMITS: Mapping[int, int] = {
    resource.RLIMIT_NOFILE: 4096,
    resource.RLIMIT_NPROC: 4096,
}


def apply_profile_rlimits(profile: SandboxProfile) -> None:
    """Apply E1's generous PTY caps; strict runner limits belong to F3."""
    if profile not in ("lesson-agent", "lesson-learner"):
        return
    for limit, cap in _GENEROUS_LIMITS.items():
        _soft, hard = resource.getrlimit(limit)
        bounded = cap if hard == resource.RLIM_INFINITY else min(cap, hard)
        resource.setrlimit(limit, (bounded, bounded))


def profile_preexec_fn(
    profile: SandboxProfile,
    existing: Callable[[], None] | None = None,
) -> Callable[[], None]:
    """Compose terminal.py's existing PTY setup with the profile limit hook."""
    if profile not in _PROFILES:
        raise ValueError(f"unknown sandbox profile: {profile}")

    def setup() -> None:
        if existing is not None:
            existing()
        apply_profile_rlimits(profile)

    return setup


async def spawn_sandboxed(
    profile: SandboxProfile,
    bundle_dir: str | os.PathLike[str],
    command: Sequence[str],
    *,
    stdin: int | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
    env: Mapping[str, str],
    preexec_fn: Callable[[], None] | None = None,
) -> asyncio.subprocess.Process:
    """Spawn inside ``profile`` or raise; ``env`` must be explicitly allowlisted."""
    if not command:
        raise ValueError("sandbox command must not be empty")
    require_sandbox_runtime()
    argv = build_sandbox_argv(profile, bundle_dir) + ["--", *command]
    try:
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            env=dict(env),
            preexec_fn=profile_preexec_fn(profile, preexec_fn),
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise SandboxSpawnError(
            f"sandbox spawn failed for {profile}: {exc}"
        ) from exc
