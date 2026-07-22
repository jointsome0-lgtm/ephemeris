"""Bubblewrap launcher primitives for isolated lesson roles.

E1 defines the profiles and fail-closed spawn seam. E2 routes lesson-agent
terminal sessions through that seam; later phases reuse it for other roles.
"""
from __future__ import annotations

import asyncio
import fcntl
import os
import resource
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Callable, Literal, Mapping, Sequence


SandboxProfile = Literal["lesson-agent", "lesson-learner", "lesson-runner"]

BWRAP = "/home/aina/.local/bin/bwrap"
USER_HOME = "/home/aina"
RUNNER_WORKDIR = "/tmp/ephemeris-runner"
RUNTIME_DIR = "/run"
SYSTEMD_RUN = "/usr/bin/systemd-run"

RUNNER_SCRATCH_BYTES = 64 * 1024 * 1024
RUNNER_HOME_BYTES = 256 * 1024 * 1024
RUNNER_ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024
RUNNER_FILE_BYTES = 32 * 1024 * 1024
RUNNER_MAX_WALL_SECONDS = 120
RUNNER_NPROC = 4096

RUNNER_SCOPE_PREFIX = (
    SYSTEMD_RUN,
    "--user",
    "--scope",
    "--collect",
    "--quiet",
    "--property=TasksMax=256",
    "--property=MemoryMax=1G",
    "--property=MemorySwapMax=0",
    "--",
)


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

_RUNNER_HOME_MOUNTS = (
    _HomeMount("--ro-bind-try", f"{USER_HOME}/go",
               "warm read-only Go module cache for offline single-file runs"),
)

_PROFILES: tuple[SandboxProfile, ...] = (
    "lesson-agent", "lesson-learner", "lesson-runner",
)


def _pure_bundle_path(
    bundle_dir: str | os.PathLike[str],
    bundle_root: str | os.PathLike[str],
) -> str:
    """Validate a strict descendant of a trusted root without filesystem I/O."""
    path = Path(bundle_dir)
    root = Path(bundle_root)
    if (
        not path.is_absolute()
        or not root.is_absolute()
        or ".." in path.parts
        or ".." in root.parts
    ):
        raise ValueError("bundle_dir and bundle_root must be absolute without '..'")
    if root == Path(root.anchor):
        raise ValueError("bundle_root must not be the filesystem root")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("bundle_dir must be inside bundle_root") from exc
    if relative == Path("."):
        raise ValueError("bundle_dir must be a strict descendant of bundle_root")
    return str(path)


def _pure_private_root(
    private_root: str | os.PathLike[str],
    bundle_root: str | os.PathLike[str],
) -> str:
    """Validate the private instance root that owns the lesson authority."""
    private = Path(private_root)
    authority = Path(bundle_root)
    if (
        not private.is_absolute()
        or ".." in private.parts
        or private == Path(private.anchor)
    ):
        raise ValueError("private_root must be absolute, non-root, and without '..'")
    try:
        relative = authority.relative_to(private)
    except ValueError as exc:
        raise ValueError("bundle_root must be inside private_root") from exc
    if relative == Path("."):
        raise ValueError("bundle_root must be a strict descendant of private_root")
    return str(private)


def _pure_mask_root(root: str | os.PathLike[str]) -> str:
    path = Path(root)
    if not path.is_absolute() or ".." in path.parts or path == Path(path.anchor):
        raise ValueError("private mask roots must be absolute, non-root, and without '..'")
    return str(path)


def _paths_overlap(left: Path, right: Path) -> bool:
    for child, parent in ((left, right), (right, left)):
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            pass
    return False


def _needs_private_mask(path: str, rebound: Sequence[_HomeMount]) -> bool:
    candidate = Path(path)
    for masked in (Path("/tmp"), Path(RUNTIME_DIR)):
        try:
            candidate.relative_to(masked)
            return False
        except ValueError:
            pass
    try:
        candidate.relative_to(USER_HOME)
    except ValueError:
        return True
    # Blank home is enough unless one of the learner's later cache/tool binds
    # overlaps the private root. Overlaps must be re-masked after those binds.
    return any(
        mount.flag != "--tmpfs"
        and _paths_overlap(candidate, Path(mount.target))
        for mount in rebound
    )


def build_sandbox_argv(
    profile: SandboxProfile,
    bundle_dir: str | os.PathLike[str],
    *,
    bundle_root: str | os.PathLike[str],
    private_root: str | os.PathLike[str] | None = None,
    private_masks: Sequence[str | os.PathLike[str]] = (),
    snapshot_fd: int | None = None,
    snapshot_name: str | None = None,
) -> list[str]:
    """Purely build the bubblewrap prefix for ``profile`` and ``bundle_dir``.

    The returned argv ends at the profile's ``--chdir``.  The caller appends
    ``--`` and the command to execute.  No path is resolved or probed here.
    ``bundle_root`` is the caller's trusted bundle authority.  Requiring the
    mounted directory to be below it prevents a late bundle bind from replacing
    the root, home, or temporary-filesystem masks.
    """
    if profile not in _PROFILES:
        raise ValueError(f"unknown sandbox profile: {profile}")
    bundle = _pure_bundle_path(bundle_dir, bundle_root)
    private = (
        _pure_private_root(private_root, bundle_root)
        if private_root is not None else None
    )
    mask_roots = list(dict.fromkeys(
        [*([private] if private is not None else []),
         *(_pure_mask_root(root) for root in private_masks)]
    ))
    if profile == "lesson-runner":
        for root in mask_roots[1 if private is not None else 0:]:
            try:
                Path(root).relative_to(bundle)
            except ValueError:
                pass
            else:
                raise ValueError("a private mask must not be inside the runner bundle")
    if profile != "lesson-runner" and (snapshot_fd is not None or snapshot_name is not None):
        raise ValueError("snapshot injection is valid only for lesson-runner")
    if (snapshot_fd is None) != (snapshot_name is None):
        raise ValueError("snapshot fd and name must be supplied together")
    snapshot_target = None
    if snapshot_name is not None:
        pure_name = PurePosixPath(snapshot_name)
        if (
            pure_name.name != snapshot_name
            or snapshot_name in (".", "..")
            or not snapshot_name
            or len(snapshot_name) > 200
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in snapshot_name)
        ):
            raise ValueError("snapshot name must be one safe basename")
        if snapshot_fd is None or snapshot_fd < 0:
            raise ValueError("snapshot fd must be open and non-negative")
        snapshot_target = f"{RUNNER_WORKDIR}/{snapshot_name}"

    argv = [BWRAP, "--unshare-all"]
    if profile == "lesson-agent":
        argv.append("--share-net")
    argv.extend([
        "--die-with-parent",
        "--ro-bind", "/", "/",
        "--proc", "/proc",
        "--dev", "/dev",
    ])
    if profile == "lesson-runner":
        argv.extend([
            "--size", str(RUNNER_SCRATCH_BYTES), "--tmpfs", "/tmp",
            "--size", str(RUNNER_HOME_BYTES), "--tmpfs", USER_HOME,
        ])
    else:
        # Preserve the E1 terminal profile argv byte-for-byte.
        argv.extend(["--tmpfs", "/tmp", "--tmpfs", USER_HOME])
    if profile in ("lesson-learner", "lesson-runner"):
        # AF_UNIX sockets survive network namespace isolation. Replace the
        # whole runtime tree; /var/run resolves into this mount as well.
        argv.extend(["--tmpfs", RUNTIME_DIR])

    mounts = list(_COMMON_HOME_MOUNTS)
    if profile == "lesson-agent":
        mounts.extend(_AGENT_HOME_MOUNTS)
    elif profile == "lesson-learner":
        mounts.extend(_LEARNER_HOME_MOUNTS)
    elif profile == "lesson-runner":
        mounts.extend(_RUNNER_HOME_MOUNTS)
    for mount in mounts:
        argv.extend(mount.argv())

    if profile in ("lesson-learner", "lesson-runner"):
        # Apply private masks after learner cache/tool re-binds so a private
        # instance nested below one of those paths cannot be reopened by them.
        rebound = (
            (*_COMMON_HOME_MOUNTS, *_LEARNER_HOME_MOUNTS)
            if profile == "lesson-learner"
            else (*_COMMON_HOME_MOUNTS, *_RUNNER_HOME_MOUNTS)
        )
        for root in mask_roots:
            if _needs_private_mask(root, rebound):
                argv.extend(["--tmpfs", root])

    if profile == "lesson-runner":
        argv.extend([
            "--ro-bind", bundle, bundle,
            "--dir", RUNNER_WORKDIR,
        ])
        if snapshot_target is not None:
            argv.extend([
                "--perms", "0444",
                "--ro-bind-data", str(snapshot_fd), snapshot_target,
            ])
        argv.extend(["--chdir", RUNNER_WORKDIR])
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


def _set_bounded_rlimit(limit: int, cap: int) -> None:
    _soft, hard = resource.getrlimit(limit)
    bounded = cap if hard == resource.RLIM_INFINITY else min(cap, hard)
    resource.setrlimit(limit, (bounded, bounded))


def apply_profile_rlimits(
    profile: SandboxProfile,
    *,
    runner_wall_seconds: int | None = None,
) -> None:
    """Apply the unchanged terminal caps or F3's strict runner backstops."""
    if profile in ("lesson-agent", "lesson-learner"):
        for limit, cap in _GENEROUS_LIMITS.items():
            _set_bounded_rlimit(limit, cap)
        return
    if profile != "lesson-runner":
        return
    if (
        runner_wall_seconds is None
        or not 1 <= runner_wall_seconds <= RUNNER_MAX_WALL_SECONDS
    ):
        raise ValueError("lesson-runner requires a bounded wall limit")
    for limit, cap in (
        (resource.RLIMIT_CPU, runner_wall_seconds),
        (resource.RLIMIT_AS, RUNNER_ADDRESS_SPACE_BYTES),
        (resource.RLIMIT_NOFILE, 256),
        # RLIMIT_NPROC is kernel-wide for the owner uid. The implementation
        # host already has >1000 threads, so the memo's 1024 false-trips a
        # cold Go toolchain before useful work. TasksMax remains the job bound.
        (resource.RLIMIT_NPROC, RUNNER_NPROC),
        (resource.RLIMIT_FSIZE, RUNNER_FILE_BYTES),
    ):
        _set_bounded_rlimit(limit, cap)


def profile_preexec_fn(
    profile: SandboxProfile,
    existing: Callable[[], None] | None = None,
    *,
    runner_wall_seconds: int | None = None,
) -> Callable[[], None]:
    """Compose terminal.py's existing PTY setup with the profile limit hook."""
    if profile not in _PROFILES:
        raise ValueError(f"unknown sandbox profile: {profile}")

    def setup() -> None:
        if existing is not None:
            existing()
        apply_profile_rlimits(
            profile, runner_wall_seconds=runner_wall_seconds
        )

    return setup


def _snapshot_memfd(snapshot: bytes) -> int:
    """Create a sealed, rewinded memfd and verify its readable byte length."""
    if not isinstance(snapshot, bytes):
        raise TypeError("runner snapshot must be bytes")
    flags = os.MFD_CLOEXEC | getattr(os, "MFD_ALLOW_SEALING", 0)
    fd = os.memfd_create("ephemeris-runner-snapshot", flags)
    try:
        view = memoryview(snapshot)
        written = 0
        while written < len(view):
            count = os.write(fd, view[written:])
            if count <= 0:
                raise OSError("short write while creating runner snapshot")
            written += count
        if os.fstat(fd).st_size != len(snapshot):
            raise OSError("runner snapshot length changed during creation")
        os.lseek(fd, 0, os.SEEK_SET)
        readable = 0
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            readable += len(chunk)
        if readable != len(snapshot):
            raise OSError("runner snapshot readable length mismatch")
        os.fchmod(fd, 0o444)
        if getattr(os, "MFD_ALLOW_SEALING", 0):
            seals = (
                fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK
                | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
            )
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except BaseException:
        os.close(fd)
        raise


async def spawn_sandboxed(
    profile: SandboxProfile,
    bundle_dir: str | os.PathLike[str],
    command: Sequence[str],
    *,
    bundle_root: str | os.PathLike[str],
    private_root: str | os.PathLike[str] | None = None,
    private_masks: Sequence[str | os.PathLike[str]] = (),
    stdin: int | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
    env: Mapping[str, str],
    preexec_fn: Callable[[], None] | None = None,
    snapshot: bytes | None = None,
    snapshot_name: str | None = None,
    runner_wall_seconds: int | None = None,
) -> asyncio.subprocess.Process:
    """Spawn inside ``profile`` or raise; ``env`` must be explicitly allowlisted."""
    if not command:
        raise ValueError("sandbox command must not be empty")
    if profile == "lesson-runner" and (
        snapshot is None or snapshot_name is None or runner_wall_seconds is None
    ):
        raise ValueError("lesson-runner requires snapshot bytes, name, and wall limit")
    if profile != "lesson-runner" and (
        snapshot is not None or snapshot_name is not None or runner_wall_seconds is not None
    ):
        raise ValueError("runner-only spawn arguments used for a terminal profile")
    require_sandbox_runtime()
    snapshot_fd: int | None = None
    try:
        if snapshot is not None:
            snapshot_fd = _snapshot_memfd(snapshot)
        bwrap_argv = build_sandbox_argv(
            profile, bundle_dir, bundle_root=bundle_root,
            private_root=private_root,
            private_masks=private_masks,
            snapshot_fd=snapshot_fd,
            snapshot_name=snapshot_name,
        )
        if profile == "lesson-runner":
            bwrap_argv.append("--clearenv")
            for name, value in env.items():
                bwrap_argv.extend(["--setenv", name, value])
        bwrap_argv.extend(["--", *command])
        argv = (
            [*RUNNER_SCOPE_PREFIX, *bwrap_argv]
            if profile == "lesson-runner" else bwrap_argv
        )
        kwargs = {}
        if snapshot_fd is not None:
            kwargs["pass_fds"] = (snapshot_fd,)
            kwargs["start_new_session"] = True
        spawn_env = dict(env)
        if profile == "lesson-runner":
            # systemd-run needs the user-bus locator. bwrap clears these
            # wrapper-only values before the untrusted command starts.
            for name in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
                value = os.environ.get(name)
                if value:
                    spawn_env[name] = value
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            env=spawn_env,
            preexec_fn=profile_preexec_fn(
                profile, preexec_fn,
                runner_wall_seconds=runner_wall_seconds,
            ),
            **kwargs,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise SandboxSpawnError(
            f"sandbox spawn failed for {profile}: {exc}"
        ) from exc
    finally:
        if snapshot_fd is not None:
            os.close(snapshot_fd)
