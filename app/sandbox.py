"""Bubblewrap launcher primitives for isolated lesson roles.

E1 defines the profiles and fail-closed spawn seam. E2 routes lesson-agent
terminal sessions through that seam; later phases reuse it for other roles.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Callable, Literal, Mapping, Sequence

try:
    import pwd
except ImportError:  # pragma: no cover - POSIX-only; see _resolve_user_home
    pwd = None  # type: ignore[assignment]


SandboxProfile = Literal["lesson-agent", "lesson-learner", "lesson-runner"]

# A path that exists nowhere, for the one case where the process owner's home
# cannot be looked up. Such a host has no bubblewrap either, so the runtime
# probe refuses before any mount built from it is used.
_UNRESOLVED_HOME = "/nonexistent"


def _resolve_user_home() -> str:
    """The process owner's home from passwd, deliberately not from ``$HOME``.

    ``$HOME`` is whatever the caller that launched this service exported, and
    this path decides what the sandbox masks — `~/.claude`, `~/.codex`, the
    credential files bound read-only below. A caller-settable value would move
    that boundary, so the boundary is read from the passwd entry of the uid
    this process actually runs as.

    ``app.main`` imports this module unconditionally, so a host that cannot
    answer the question at all — no ``pwd``, or a uid with no passwd entry, as
    a container running under an arbitrary numeric uid has — must not take the
    whole tracker down with it. It gets a home that exists nowhere instead, and
    Learn fails closed on the runtime probe like any other missing requirement.
    """
    if pwd is None:  # pragma: no cover - non-POSIX import path only
        return _UNRESOLVED_HOME
    try:
        return pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:  # pragma: no cover - uid without a passwd entry
        return _UNRESOLVED_HOME


USER_HOME = _resolve_user_home()

# Where bubblewrap is looked for, in order, and never through ``$PATH``: the
# search path is caller-settable for the same reason ``$HOME`` is, and the
# binary resolved here is the one thing standing between a lesson session and
# the host. A user-installed build wins over the distribution one so a host can
# carry a newer bubblewrap than its release ships.
_BWRAP_CANDIDATES = (f"{USER_HOME}/.local/bin/bwrap", "/usr/bin/bwrap")

# Every long option this module puts on a bubblewrap command line. This is the
# whole minimum-version requirement, stated as vocabulary rather than as a
# number: upstream records neither which release introduced which option nor a
# `--version` contract to compare against, the set below is already accepted by
# the bubblewrap 0.9.0 Ubuntu 24.04 ships (measured 2026-08-12), and the one
# recent advisory — CVE-2026-41163, fixed in 0.11.2 — affects only setuid
# installations, which neither candidate is. A numeric floor would therefore be
# a guess; asking each candidate what it accepts is not, and it turns "too old"
# into a startup refusal instead of an "unknown option" at the first spawn.
_REQUIRED_BWRAP_OPTIONS = (
    "--bind", "--bind-try", "--chdir", "--clearenv", "--dev",
    "--die-with-parent", "--dir", "--perms", "--proc", "--ro-bind",
    "--ro-bind-data", "--ro-bind-fd", "--ro-bind-try", "--setenv",
    "--share-net", "--size", "--tmpfs", "--unshare-all", "--unshare-user",
)


def _bwrap_rejection(path: str) -> str:
    """Why ``path`` cannot serve as this module's bubblewrap, or ``""``.

    Every answer other than "yes" is a rejection, including a ``--help`` that
    cannot be run or does not finish: a candidate this module cannot even ask
    is a candidate it cannot vouch for, and rejecting it is what lets the next
    one — a working distribution install behind a stale user build — be tried.
    """
    if not (os.path.isfile(path) and os.access(path, os.X_OK)):
        return "not an executable file"
    try:
        result = subprocess.run(
            [path, "--help"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"cannot be asked for its options: {exc}"
    listed = set(result.stdout.split())
    missing = [
        option for option in _REQUIRED_BWRAP_OPTIONS if option not in listed
    ]
    if missing:
        return "does not accept " + " ".join(missing)
    return ""


def _resolve_bwrap() -> tuple[str, str]:
    """The first candidate that speaks this module's whole option set.

    Returns the chosen path and an empty reason. When no candidate qualifies it
    returns the preferred path — so the pure argv builders stay total — and why
    each was rejected, which the runtime probe below refuses with instead of
    spawning anything.
    """
    reasons = []
    for candidate in _BWRAP_CANDIDATES:
        rejection = _bwrap_rejection(candidate)
        if not rejection:
            return candidate, ""
        reasons.append(f"{candidate}: {rejection}")
    return _BWRAP_CANDIDATES[0], "no usable bubblewrap — " + "; ".join(reasons)


# Resolved once, at import: a bubblewrap installed or upgraded while the
# service is running is not picked up until `systemctl --user restart
# ephemeris`, the same restart the cached runtime probe already needs.
BWRAP, _BWRAP_UNUSABLE = _resolve_bwrap()

RUNNER_WORKDIR = "/tmp/ephemeris-runner"
RUNTIME_DIR = "/run"
SYSTEMD_RUN = "/usr/bin/systemd-run"
SYSTEMCTL = "/usr/bin/systemctl"
EPHEMERIS_CHECKOUT_ROOT = str(Path(__file__).resolve().parents[1])
GO_MODULE_CACHE_ROOT = f"{USER_HOME}/go/pkg/mod"

RUNNER_SCRATCH_BYTES = 64 * 1024 * 1024
RUNNER_HOME_BYTES = 256 * 1024 * 1024
RUNNER_ADDRESS_SPACE_BYTES = 1024 * 1024 * 1024
RUNNER_FILE_BYTES = 32 * 1024 * 1024
RUNNER_MAX_WALL_SECONDS = 120
RUNNER_SCOPE_GRACE_SECONDS = 5
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
    "--property=KillMode=control-group",
)

# A build step is not user code from a stranger, but `bun build` executes an
# agent-authored macro, and a wall-clock timeout is no help against a macro
# that allocates or forks in a loop: the host is out of memory long before the
# timer fires, and the one worker serving this app dies with it. Same scope
# mechanism as the runner, sized for a toolchain rather than for one exercise —
# a real `bun install` of a large tree peaks well under this.
BUILD_MEMORY_MAX = "2G"
BUILD_TASKS_MAX = 512
BUILD_MAX_WALL_SECONDS = 600
BUILD_SCOPE_GRACE_SECONDS = 15
# Bounded for the same reason the runner's are: an unbounded tmpfs is host
# memory that a loop can claim without ever allocating it in a process.
BUILD_SCRATCH_BYTES = 512 * 1024 * 1024
BUILD_HOME_BYTES = 64 * 1024 * 1024

# The render gate loads the artifact the build just produced, so the page runs
# code from the same author as the macro — in a browser, which is a process
# tree rather than a process. Roomier than the build's: headless Chrome is a
# handful of processes and a few hundred threads before a page has done
# anything, and a cap that only a real browser trips would refuse every build.
RENDER_MEMORY_MAX = "2G"
RENDER_TASKS_MAX = 1024
RENDER_MAX_WALL_SECONDS = 600
RENDER_SCOPE_GRACE_SECONDS = 30


def _resource_scope_prefix(
    *, tasks_max: int, memory_max: str, wall_seconds: int, grace: int, ceiling: int
) -> tuple[str, ...]:
    """A transient user scope with the limits a whole process tree shares.

    `RuntimeMaxSec` is a backstop under the caller's own timeout, not a
    replacement for it: the caller kills the process it started, and this kills
    the cgroup if that process is gone or wedged. `KillMode=control-group` is
    what makes that reach the children — a browser's renderers, a macro's
    forks — rather than only the leader.
    """
    if not 1 <= wall_seconds <= ceiling:
        raise ValueError("a resource scope requires a bounded wall limit")
    return (
        SYSTEMD_RUN, "--user", "--scope", "--collect", "--quiet",
        f"--property=TasksMax={tasks_max}",
        f"--property=MemoryMax={memory_max}",
        "--property=MemorySwapMax=0",
        "--property=KillMode=control-group",
        *_systemd_no_expand_option(),
        f"--property=RuntimeMaxSec={wall_seconds + grace}s",
        "--",
    )


@cache
def _systemd_no_expand_option() -> tuple[str, ...]:
    """Use the explicit no-expansion switch when this host systemd has it."""
    try:
        result = subprocess.run(
            [SYSTEMD_RUN, "--help"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if "--expand-environment" in result.stdout:
        return ("--expand-environment=no",)
    return ()


def runner_scope_prefix(
    wall_seconds: int,
    *,
    unit_name: str | None = None,
) -> tuple[str, ...]:
    """Build the aggregate runner scope wrapper with an orphan backstop."""
    if not 1 <= wall_seconds <= RUNNER_MAX_WALL_SECONDS:
        raise ValueError("runner scope requires a bounded wall limit")
    if unit_name is not None and (
        not unit_name.startswith("ephemeris-runner-")
        or not unit_name.removeprefix("ephemeris-runner-").replace("-", "").isalnum()
    ):
        raise ValueError("runner scope unit name is invalid")
    argv = [*RUNNER_SCOPE_PREFIX, *_systemd_no_expand_option()]
    if unit_name is not None:
        argv.append(f"--unit={unit_name}")
    argv.extend([
        f"--property=RuntimeMaxSec={wall_seconds + RUNNER_SCOPE_GRACE_SECONDS}s",
        "--",
    ])
    return tuple(argv)


def build_scope_prefix(wall_seconds: int) -> tuple[str, ...]:
    """The resource scope one build step runs inside.

    The grace is wider than the runner's because a build step's own timeout is
    minutes, not seconds, and a scope that expired first would turn a slow but
    honest install into a build failure.
    """
    return _resource_scope_prefix(
        tasks_max=BUILD_TASKS_MAX, memory_max=BUILD_MEMORY_MAX,
        wall_seconds=wall_seconds, grace=BUILD_SCOPE_GRACE_SECONDS,
        ceiling=BUILD_MAX_WALL_SECONDS,
    )


def render_scope_prefix(wall_seconds: int) -> tuple[str, ...]:
    """The resource scope the render gate's browser runs inside."""
    return _resource_scope_prefix(
        tasks_max=RENDER_TASKS_MAX, memory_max=RENDER_MEMORY_MAX,
        wall_seconds=wall_seconds, grace=RENDER_SCOPE_GRACE_SECONDS,
        ceiling=RENDER_MAX_WALL_SECONDS,
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

# The two directories under the blank home that hold the agents' OWN memory:
# Claude's transcripts under `.claude/projects/`, Codex's sessions and
# `history.jsonl`. Blank above, they die with the PTY — so `claude --continue`
# and `codex resume` find nothing the next time a lesson terminal opens. A
# caller that supplies a persistent agent home swaps each tmpfs for a bind of
# that home's matching subdirectory; the login and configuration files bound
# read-only AFTER them in the same tuple stay read-only either way.
AGENT_STATE_SUBDIRS = {
    f"{USER_HOME}/.claude": "claude",
    f"{USER_HOME}/.codex": "codex",
}

# The one name a build workspace occupies, on both sides of its bind: the
# subdirectory it keeps and the path it appears at inside the bundle. Node
# resolution only looks for this name, so naming it once keeps the mount and
# the bundle spec's reserved entry from drifting apart.
BUILD_WORKSPACE_MOUNT = "node_modules"

# The package manager the build step drives, addressed by absolute path because
# the view below hands it no PATH worth searching. `~/.bun` is deliberately NOT
# on the terminal profiles' mount list: an agent that could run `bun` itself
# would own the install command line, and with it the 30-day quarantine and the
# copying backend that keeps one lesson out of another's cache entry. The app
# runs the package manager; the agent asks it to (`app/services/lesson_build.py`).
# The only writable place in the `bundle` step's view. A build-time macro runs
# agent-authored code, and everything bun reads configuration from — the
# workspace `bunfig.toml`, `package.json` and lockfile — has to stay out of its
# reach, or the next install inherits whatever it wrote.
BUILD_OUTPUT_DIR = "out"
BUN_BINARY = f"{USER_HOME}/.bun/bin/bun"
BUN_CACHE_DIR = f"{USER_HOME}/.bun/install/cache"

BuildStep = Literal["install", "bundle"]
_BUILD_STEPS: tuple[BuildStep, ...] = ("install", "bundle")

# What the two build steps get for an environment. `--clearenv` first, so the
# app process's own environment — proxy variables, tokens, whatever systemd
# handed it — is not what resolves a package name.
_BUILD_STEP_ENV: Mapping[str, str] = {
    "PATH": "/usr/bin:/bin",
    "HOME": USER_HOME,
    "TMPDIR": "/tmp",
}


def _agent_home_mounts(agent_home: str | None) -> tuple[_HomeMount, ...]:
    """The agent profile's home mounts, ephemeral or backed by `agent_home`."""
    if agent_home is None:
        return _AGENT_HOME_MOUNTS
    return tuple(
        _HomeMount(
            "--bind", mount.target,
            "persistent per-lesson agent memory (transcripts and sessions)",
            f"{agent_home}/{AGENT_STATE_SUBDIRS[mount.target]}",
        )
        if mount.flag == "--tmpfs" and mount.target in AGENT_STATE_SUBDIRS
        else mount
        for mount in _AGENT_HOME_MOUNTS
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


def _pure_agent_home(
    agent_home: str | os.PathLike[str],
    private_root: str | None,
    bundle_root: str | os.PathLike[str],
) -> str:
    """Validate the persistent agent home without resolving or probing paths.

    It must live under the private instance root — the same authority the
    bundles answer to — and OUTSIDE the bundle root. A bundle is writable from
    inside its own session, so an agent home reached through one would let a
    lesson's files choose what gets bound over `$HOME` on the next open.
    """
    if private_root is None:
        raise ValueError("a persistent agent home requires the private instance root")
    path = Path(agent_home)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("agent home must be absolute and without '..'")
    private = Path(private_root)
    if path == private or not path.is_relative_to(private):
        raise ValueError("agent home must be a strict descendant of private_root")
    if path.is_relative_to(Path(bundle_root)):
        raise ValueError("agent home must be outside the bundle root")
    return str(path)


def _pure_build_workspace(
    build_workspace: str | os.PathLike[str],
    private_root: str | None,
    bundle_root: str | os.PathLike[str],
) -> str:
    """Validate the persistent build workspace on the agent home's terms.

    Same authority rules as :func:`_pure_agent_home`, for the same reason: this
    directory is bound over a path *inside* the writable bundle, so a workspace
    reached through a bundle would let one lesson's files choose what appears
    under another's `node_modules` on the next open.
    """
    if private_root is None:
        raise ValueError("a build workspace requires the private instance root")
    path = Path(build_workspace)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("build workspace must be absolute and without '..'")
    private = Path(private_root)
    if path == private or not path.is_relative_to(private):
        raise ValueError("build workspace must be a strict descendant of private_root")
    if path.is_relative_to(Path(bundle_root)):
        raise ValueError("build workspace must be outside the bundle root")
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
    agent_home: str | os.PathLike[str] | None = None,
    build_workspace: str | os.PathLike[str] | None = None,
    snapshot_fd: int | None = None,
    snapshot_name: str | None = None,
    module_cache_fd: int | None = None,
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
    if profile == "lesson-runner" and private is None:
        raise ValueError("lesson-runner requires the private instance root")
    if agent_home is not None and profile != "lesson-agent":
        raise ValueError("a persistent agent home is valid only for lesson-agent")
    home = (
        _pure_agent_home(agent_home, private, bundle_root)
        if agent_home is not None else None
    )
    if build_workspace is not None and profile != "lesson-agent":
        raise ValueError("a build workspace is valid only for lesson-agent")
    workspace = (
        _pure_build_workspace(build_workspace, private, bundle_root)
        if build_workspace is not None else None
    )
    if profile == "lesson-runner" and (module_cache_fd is None or module_cache_fd < 0):
        raise ValueError("lesson-runner requires an open Go module-cache fd")
    if profile != "lesson-runner" and module_cache_fd is not None:
        raise ValueError("module-cache fd is valid only for lesson-runner")
    mask_roots = list(dict.fromkeys(
        [*([private] if private is not None else []),
         *([EPHEMERIS_CHECKOUT_ROOT] if profile == "lesson-runner" else []),
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
    if profile in ("lesson-agent", "lesson-learner"):
        # Both interactive shells share the host network (owner decision
        # 2026-08-11): network lessons need real experiments from the
        # learner's shell. The offline profile is now lesson-runner only.
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
        # Keep the host's AF_UNIX sockets (session bus, agent sockets) out of
        # these profiles by replacing the whole runtime tree; /var/run
        # resolves into this mount as well. For lesson-runner this backs up
        # its network isolation; for lesson-learner it stands on its own now
        # that the shell shares the host network.
        argv.extend(["--tmpfs", RUNTIME_DIR])

    mounts = [] if profile == "lesson-runner" else list(_COMMON_HOME_MOUNTS)
    if profile == "lesson-agent":
        mounts.extend(_agent_home_mounts(home))
    elif profile == "lesson-learner":
        mounts.extend(_LEARNER_HOME_MOUNTS)
    elif profile == "lesson-runner":
        argv.extend([
            "--dir", f"{USER_HOME}/go",
            "--dir", f"{USER_HOME}/go/pkg",
        ])
        mounts.append(_HomeMount(
            "--ro-bind-fd", GO_MODULE_CACHE_ROOT,
            "descriptor-bound read-only Go module cache for offline single-file runs",
            str(module_cache_fd),
        ))
    for mount in mounts:
        argv.extend(mount.argv())

    if profile in ("lesson-learner", "lesson-runner"):
        # Apply private masks after learner cache/tool re-binds so a private
        # instance nested below one of those paths cannot be reopened by them.
        rebound = tuple(mounts)
        for index, root in enumerate(mask_roots):
            if any(
                Path(root).is_relative_to(Path(parent))
                for parent in mask_roots[:index]
            ):
                continue
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
        argv.extend(["--bind", bundle, bundle])
        if workspace is not None:
            # After the bundle bind, so it is not shadowed by it. The packages
            # an agent installs belong to the lesson but not IN it: the bundle
            # is served, walked by the manifest reader, and writable from
            # inside its own session. A bwrap bind lives only in this mount
            # namespace, so the bundle on disk never grows the directory and
            # the read path cannot see it.
            #
            # This does NOT isolate lessons from each other. A package manager
            # that hardlinks out of one shared cache — bun's default — hands
            # every lesson the same inode, so an edit through any of them
            # reaches all of them wherever `node_modules` happens to live
            # (measured on katex: `>>` into one copy, no chmod, and the next
            # install elsewhere receives the tampered file). Whatever writable
            # package cache the build step needs arrives with that step, which
            # can force a copying backend on its own command line; nothing here
            # gives a session one.
            argv.extend([
                "--bind", f"{workspace}/{BUILD_WORKSPACE_MOUNT}",
                f"{bundle}/{BUILD_WORKSPACE_MOUNT}",
            ])
        argv.extend(["--chdir", bundle])
    return argv


def build_step_argv(
    step: BuildStep,
    *,
    build_workspace: str | os.PathLike[str],
    bundle_root: str | os.PathLike[str],
    private_root: str | os.PathLike[str] | None = None,
    bundle_dir: str | os.PathLike[str] | None = None,
    command: Sequence[str],
) -> list[str]:
    """Purely build the bubblewrap argv for one app-owned build step.

    Deliberately a sibling of :func:`build_sandbox_argv` rather than a fourth
    profile inside it: the terminal and runner profiles exist to confine a
    shell somebody else drives, and their argv is reviewed as one shape. These
    two steps run a command *this app wrote*, with no interactive tenant, and
    they need a different view — no bundle for ``install``, no network for
    ``bundle``. Folding that into the profile builder would put two unrelated
    contracts behind one set of arguments.

    What the view is for, in order of how easy each is to get wrong:

    1. **Module resolution.** bun resolves a bare specifier by walking up from
       the importer file, not from cwd, and the packages live outside the
       bundle (#164). So the ``bundle`` step re-creates the one bind the
       agent's terminal already has — ``<workspace>/node_modules`` at
       ``<bundle>/node_modules`` — and resolution is then the ordinary
       filesystem walk, byte-identical to building inside the workspace.
       ``NODE_PATH`` resolves too, but silently skips the package ``exports``
       map: measured, katex fell back from its ESM entry to its legacy CJS
       ``main``, and a package that declares only ``exports`` does not resolve
       at all. That is not a base to hand an agent free package choice on.
    2. **Reading no more than the agent can.** The entry is an agent-authored
       file. A plain subprocess of the app would compile it against the real
       ``$HOME``, where ``import s from "../../../secret" with {type:"text"}``
       or a tsconfig ``paths`` mapping inlines an owner-readable file into an
       artifact this app then serves. Keeping the terminal's blank ``$HOME``
       means the build reads what the agent already reads and nothing more.
    3. **cwd.** ``bunfig.toml`` carries ``preload``, which executes a script,
       and bun reads it from cwd. cwd is the app-owned workspace for both
       steps; the entry travels as an absolute path. The bundle — writable
       from inside the agent's own session — is never cwd, and for ``bundle``
       it is bound read-only: the artifact is placed by the app afterwards,
       once the size and render gates have passed.

    4. **What each step may write.** ``install`` fills the workspace, so it has
       it writable. ``bundle`` does not: ``bun build`` executes an
       ``with {type: "macro"}`` import at build time, so agent-authored code
       *does* run in that view, and a writable workspace would let it leave a
       ``bunfig.toml``, a ``package.json`` or a lockfile behind for the next
       install to read — in the one view that has the network and a writable
       shared package cache. Rewriting a dependency to a tarball URL, which has
       no release age at all, would walk straight through the 30-day quarantine
       this whole mechanism exists to enforce. So ``bundle`` gets the workspace
       read-only with exactly one writable hole, ``<workspace>/out``, which
       holds the artifact and nothing bun ever reads configuration from.

    It is not a confinement boundary for package code, which neither step
    executes: bun runs no dependency lifecycle script without an explicit
    ``trustedDependencies`` entry, and ``package.json`` lives in the workspace,
    which no session can reach and the bundle step cannot write.
    ``--ignore-scripts`` on the install argv says so a second time.
    """
    if step not in _BUILD_STEPS:
        raise ValueError(f"unknown build step: {step}")
    if not command:
        raise ValueError("build step command must not be empty")
    private = (
        _pure_private_root(private_root, bundle_root)
        if private_root is not None else None
    )
    workspace = _pure_build_workspace(build_workspace, private, bundle_root)
    if (bundle_dir is None) != (step == "install"):
        raise ValueError("the bundle step needs a bundle dir and install must not have one")
    bundle = (
        _pure_bundle_path(bundle_dir, bundle_root)
        if bundle_dir is not None else None
    )

    argv = [BWRAP, "--unshare-all"]
    if step == "install":
        # The only step that talks to a registry.
        argv.append("--share-net")
    argv.extend([
        "--die-with-parent",
        "--ro-bind", "/", "/",
        "--proc", "/proc",
        "--dev", "/dev",
        "--size", str(BUILD_SCRATCH_BYTES), "--tmpfs", "/tmp",
        "--size", str(BUILD_HOME_BYTES), "--tmpfs", USER_HOME,
        "--ro-bind", BUN_BINARY, BUN_BINARY,
    ])
    if step == "install":
        # Writable because this is the step that fills it. Shared between
        # lessons on purpose — it is why the second lesson to want d3 costs
        # nothing — which is exactly why the install argv forces a copying
        # backend: a hardlinked node_modules entry IS the cache entry, and one
        # lesson's session could edit what every later install receives.
        argv.extend(["--bind", BUN_CACHE_DIR, BUN_CACHE_DIR])
    if bundle is None:
        # `install` is the step that fills the workspace.
        argv.extend(["--bind", workspace, workspace])
    else:
        # `bundle` runs agent-authored code (a build-time macro) and must not
        # be able to leave install configuration behind for the next install to
        # read. Read-only everywhere, with one writable hole for the artifact —
        # a later bind wins over the earlier one covering it.
        argv.extend([
            "--ro-bind", workspace, workspace,
            "--bind", f"{workspace}/{BUILD_OUTPUT_DIR}",
            f"{workspace}/{BUILD_OUTPUT_DIR}",
            "--ro-bind", bundle, bundle,
            "--ro-bind", f"{workspace}/{BUILD_WORKSPACE_MOUNT}",
            f"{bundle}/{BUILD_WORKSPACE_MOUNT}",
        ])
    argv.extend(["--chdir", workspace, "--clearenv"])
    for name, value in _BUILD_STEP_ENV.items():
        argv.extend(["--setenv", name, value])
    argv.extend(["--", *command])
    return argv


@dataclass(frozen=True)
class _ProbeResult:
    available: bool
    detail: str = ""


@cache
def _cached_user_scope_probe() -> _ProbeResult:
    """Verify limits and literal argv delivery through the user scope."""
    literal = "$EPHEMERIS_SCOPE_LITERAL"
    prefix = list(runner_scope_prefix(5))
    prefix[-1:-1] = ["--setenv=EPHEMERIS_SCOPE_LITERAL=expanded"]
    try:
        result = subprocess.run(
            [*prefix, "/usr/bin/printf", "%s", literal],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _ProbeResult(False, str(exc))
    if result.returncode == 0 and result.stdout == literal:
        return _ProbeResult(True)
    detail = " ".join((result.stderr or result.stdout or "").split())
    return _ProbeResult(False, detail[:500] or f"exit {result.returncode}")


def require_user_scope_runtime() -> None:
    result = _cached_user_scope_probe()
    if not result.available:
        raise SandboxUnavailableError(
            f"systemd user scope probe failed: {result.detail}"
        )


@cache
def _cached_runtime_probe() -> _ProbeResult:
    """Run bubblewrap's process-lifetime probe once, caching failures too."""
    if _BWRAP_UNUSABLE:
        return _ProbeResult(False, _BWRAP_UNUSABLE)
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


# Keyed by rlimit NAME, not by resource.RLIMIT_* value: `resource` is Unix-only
# and this module sits on main.py's unconditional import chain (main -> terminal
# -> sandbox), so the constant must not need it at module import time. Resolved
# against the module at the point of use, where the platform is known to have it.
_GENEROUS_LIMITS: Mapping[str, int] = {
    "RLIMIT_NOFILE": 4096,
    "RLIMIT_NPROC": 4096,
}


def _set_bounded_rlimit(limit: int, cap: int) -> None:
    import resource

    _soft, hard = resource.getrlimit(limit)
    bounded = cap if hard == resource.RLIM_INFINITY else min(cap, hard)
    resource.setrlimit(limit, (bounded, bounded))


def apply_profile_rlimits(
    profile: SandboxProfile,
    *,
    runner_wall_seconds: int | None = None,
) -> None:
    """Apply the unchanged terminal caps or F3's strict runner backstops."""
    import resource

    if profile in ("lesson-agent", "lesson-learner"):
        for name, cap in _GENEROUS_LIMITS.items():
            _set_bounded_rlimit(getattr(resource, name), cap)
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
    # setup() runs between fork and exec, where a module's FIRST import could
    # deadlock on the import lock. Pay for it here, in the parent, so the child's
    # `import resource` is only a sys.modules hit.
    import resource  # noqa: F401

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
    if len(snapshot) > RUNNER_FILE_BYTES:
        raise ValueError(
            f"runner snapshot exceeds the {RUNNER_FILE_BYTES}-byte file ceiling"
        )
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
            import fcntl

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


def open_runner_module_cache_fd() -> int:
    """Open the exact cache directory without following any path component."""
    path = Path(GO_MODULE_CACHE_ROOT)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Go module-cache path must be absolute without '..'")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in path.parts[1:]:
            child_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=fd,
            )
            os.close(fd)
            fd = child_fd
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
    agent_home: str | os.PathLike[str] | None = None,
    build_workspace: str | os.PathLike[str] | None = None,
    stdin: int | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
    env: Mapping[str, str],
    preexec_fn: Callable[[], None] | None = None,
    snapshot: bytes | None = None,
    snapshot_name: str | None = None,
    runner_wall_seconds: int | None = None,
    runner_scope_unit: str | None = None,
) -> asyncio.subprocess.Process:
    """Spawn inside ``profile`` or raise; ``env`` must be explicitly allowlisted."""
    if not command:
        raise ValueError("sandbox command must not be empty")
    if profile == "lesson-runner" and (
        snapshot is None or snapshot_name is None or runner_wall_seconds is None
        or runner_scope_unit is None
    ):
        raise ValueError(
            "lesson-runner requires snapshot bytes, name, wall limit, and scope unit"
        )
    if profile != "lesson-runner" and (
        snapshot is not None or snapshot_name is not None
        or runner_wall_seconds is not None or runner_scope_unit is not None
    ):
        raise ValueError("runner-only spawn arguments used for a terminal profile")
    require_sandbox_runtime()
    if profile == "lesson-runner":
        require_user_scope_runtime()
    snapshot_fd: int | None = None
    module_cache_fd: int | None = None
    try:
        if profile == "lesson-runner":
            module_cache_fd = open_runner_module_cache_fd()
        if snapshot is not None:
            snapshot_fd = _snapshot_memfd(snapshot)
        bwrap_argv = build_sandbox_argv(
            profile, bundle_dir, bundle_root=bundle_root,
            private_root=private_root,
            private_masks=private_masks,
            agent_home=agent_home,
            build_workspace=build_workspace,
            snapshot_fd=snapshot_fd,
            snapshot_name=snapshot_name,
            module_cache_fd=module_cache_fd,
        )
        if profile == "lesson-runner":
            for authority in (bundle_dir, bundle_root, private_root):
                path = Path(authority)  # type: ignore[arg-type]
                if path.absolute() != path.resolve(strict=False):
                    raise SandboxSpawnError(
                        "lesson-runner refuses symlinked bundle/private authorities"
                    )
            bwrap_argv.append("--clearenv")
            for name, value in env.items():
                bwrap_argv.extend(["--setenv", name, value])
        bwrap_argv.extend(["--", *command])
        argv = (
            [
                *runner_scope_prefix(
                    runner_wall_seconds, unit_name=runner_scope_unit
                ),
                *bwrap_argv,
            ]
            if profile == "lesson-runner" else bwrap_argv
        )
        kwargs = {}
        inherited_fds = tuple(
            fd for fd in (snapshot_fd, module_cache_fd) if fd is not None
        )
        if inherited_fds:
            kwargs["pass_fds"] = inherited_fds
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
        if module_cache_fd is not None:
            os.close(module_cache_fd)
