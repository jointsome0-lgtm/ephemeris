"""Install a lesson's packages and bundle its page — the app's hand, not the agent's (#161).

The agent picks the libraries; this module runs the package manager. That split
is the whole point, and it is not stylistic:

- **The 30-day quarantine.** `minimumReleaseAge` is read from `bunfig.toml`
  beside cwd and from `$HOME/.bunfig.toml`, and inside its terminal the agent
  can write both. Measured 2026-08-09 on bun 1.3.11: a `bunfig.toml` with
  `minimumReleaseAge = 0` next to cwd installed katex 0.18.2, published the
  previous day. The same install with `--minimum-release-age=2592000` on the
  argv took 0.17.0 instead, skipping every 0.18.x inside the window. So the
  rule travels on the command line, where only this app writes.
- **The shared package cache.** bun's default backend hardlinks out of
  `~/.bun/install/cache`, which makes a lesson's `node_modules` entry *the same
  inode* as the cache entry (measured: inode 19167021 on both sides). Editing
  through any one lesson would hand the tampered bytes to every later install.
  `--backend=copyfile` breaks that, and it, too, has to be on an argv the agent
  cannot rewrite. Moving `node_modules` out of the bundle (#164) did not help
  with this; nothing but the backend does.

Package code is never executed here. bun runs no dependency lifecycle script
without a `trustedDependencies` entry, that entry would live in the workspace
`package.json` no session can reach, and `--ignore-scripts` says it a second
time. Installing copies files; bundling reads and concatenates them. The one
place a lesson's dependencies run is the learner's browser, inside the CSP
sandbox — which is what the render gate at the end of this module checks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import stat
import time
from pathlib import Path, PurePosixPath

from . import bundle_schema, lessons, render_check

_log = logging.getLogger(__name__)

# The package manager the build steps drive. Addressed by absolute path: the
# steps get a PATH of their own, not the service's.
BUN_BINARY = os.path.expanduser("~/.bun/bin/bun")
BUN_CACHE_DIR = os.path.expanduser("~/.bun/install/cache")
# Where the bundle step writes its outputs, inside the workspace: nothing bun
# reads configuration from lives under it, and the artifact is judged there
# before the bundle is allowed to see it.
BUILD_OUTPUT_DIR = "out"
# Both steps run from an environment of the app's choosing rather than the
# service's — proxy variables, tokens, whatever systemd handed it — so nothing
# in it decides how a package name resolves.
_STEP_ENV = {"PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~"), "TMPDIR": "/tmp"}

# 30 days, in seconds — bun's unit, unlike npm's `min-release-age`, which
# counted days. Long enough that a compromised release has been found by
# somebody else before it can reach this machine.
MINIMUM_RELEASE_AGE_SECONDS = 30 * 24 * 60 * 60

# The ceiling a built page may not cross. Measured 2026-08-09, minified IIFE:
# `import mermaid from "mermaid"` is 3.33 MB, while named imports of d3 and
# katex come to 0.30 MB. 1 MiB is the geometric mean of the two — 3.4x of room
# above the honest build, 3.3x of margin below the accident. The accident is
# specific and common: a default import of a library that publishes its whole
# world behind one entry point, shipped to a learner on a page that will not
# say why it took ten seconds to appear.
ARTIFACT_MAX_BYTES = 1024 * 1024

# Cold install of mermaid + d3 + katex measured 6.18 s, a warm one 124 ms, and
# the heaviest bundle 0.34 s. These are the ceilings for a step that has gone
# wrong, not a budget anybody should get near.
INSTALL_TIMEOUT_SECONDS = 300.0
BUNDLE_TIMEOUT_SECONDS = 120.0

# Enough of a failed step's output for the agent to fix it, capped so a package
# manager in a retry loop cannot make the response the problem.
OUTPUT_TAIL_BYTES = 8192
MAX_PACKAGES = 32

# npm's name grammar, with an optional version range. Anchored, and a leading
# "-" is unrepresentable: these strings become argv words next to the flags
# that carry the quarantine and the copying backend, and a package named
# `--backend=hardlink` must not be a way to turn either of them off.
_PACKAGE_RE = re.compile(
    r"^(?:@[a-z0-9][a-z0-9._-]{0,127}/)?[a-z0-9][a-z0-9._-]{0,127}"
    r"(?:@[A-Za-z0-9^~*<>=][A-Za-z0-9.^~*+|<>= -]{0,63})?$"
)
# What the bundler will read as an entry point.
_ENTRY_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".mts")
# The one directory a v2 bundle serves besides its declared pages
# (`lessons.bundle_resource_info`), and therefore the only place an artifact
# a page can load is allowed to go.
_ARTIFACT_DIR = "assets"
# What the bundler is told to call its outputs, so the app can find them
# without reading bun's report: `<stem>.js`, plus `<stem>.css` when the graph
# imported a stylesheet.
_ARTIFACT_STEM = "artifact"
# bun's report of the module graph, written beside the outputs and consumed —
# then removed — before they are composed, so it never counts as an output.
_GRAPH_NAME = "graph.json"
# Generous: the report holds a line per module with no source text in it, so
# even a graph of a few thousand modules stays in the low megabytes. Big enough
# never to refuse an honest build, small enough that a macro cannot make the
# app read an arbitrary amount into memory.
_GRAPH_MAX_BYTES = 32 * 1024 * 1024


class BuildError(Exception):
    """A build step was refused; the router turns this into the response."""

    def __init__(self, code: str, status: int, detail: str = "", **fields) -> None:
        super().__init__(detail or code)
        self.code = code
        self.status = status
        self.detail = detail
        self.fields = fields


def clean_packages(raw) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise BuildError("invalid-request", 400, "`add` must be a list of package specs")
    if len(raw) > MAX_PACKAGES:
        raise BuildError(
            "invalid-request", 400, f"at most {MAX_PACKAGES} packages per build"
        )
    packages: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not _PACKAGE_RE.match(item.strip()):
            raise BuildError(
                "invalid-request", 400,
                f"not a package spec: {item if isinstance(item, str) else type(item).__name__}",
            )
        packages.append(item.strip())
    return packages


def clean_source_ref(raw, field: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise BuildError("invalid-request", 400, f"`{field}` is required")
    try:
        ref = lessons.clean_bundle_ref(raw)
    except lessons.LessonError as exc:
        raise BuildError("invalid-request", 400, f"`{field}`: {exc}") from exc
    if field == "entry" and PurePosixPath(ref).suffix.lower() not in _ENTRY_SUFFIXES:
        raise BuildError(
            "invalid-request", 400,
            "`entry` must be a script source " + "/".join(_ENTRY_SUFFIXES),
        )
    if field == "out":
        if PurePosixPath(ref).suffix.lower() != ".js":
            raise BuildError("invalid-request", 400, "`out` must end in .js")
        # Where the artifact may land is decided by what the files route will
        # hand back, not by taste. A v2 bundle's preview surface is a positive
        # allowlist — declared pages plus `assets/` — so an artifact at
        # `dist/page.js` would 404 for the page that references it, and every
        # build of that lesson would be refused by the render gate for a
        # reason that says nothing about the reason. Better to say it here.
        if not ref.startswith(f"{_ARTIFACT_DIR}/"):
            raise BuildError(
                "invalid-request", 400,
                f"`out` must be under `{_ARTIFACT_DIR}/`, the only place in a "
                "bundle besides its declared pages that the lesson route serves",
            )
        # No carve-out inside `assets/` any more: the app used to reseed a
        # vendored library shelf at `assets/libs/` on every terminal open and
        # had to refuse an artifact there, because the copy would come back
        # over it minutes after the build reported success. The shelf is gone
        # (#161) — a lesson builds every library it needs — so the whole of
        # `assets/` is the agent's to place an artifact in.
    return ref


def clean_build_refs(payload: dict) -> tuple[str, str]:
    """The entry and the output name, checked together as well as apart.

    They may not be the same file. The bundler reads the entry into the
    workspace and the artifact is then written over `out`, so a request naming
    one path twice would replace the agent's source with the generated IIFE and
    then, on a passing render, delete the copy that was set aside — destroying
    authored work to produce a page that still renders and reports `ok`.
    """
    entry = clean_source_ref(payload.get("entry"), "entry")
    out = clean_source_ref(payload.get("out"), "out")
    if entry == out:
        raise BuildError(
            "invalid-request", 400,
            "`entry` and `out` name the same file; the build would overwrite "
            "the source it just read",
        )
    return entry, out


def _seed_workspace(workspace: Path, slug: str) -> None:
    """Give bun a project root to install into, once.

    Written here rather than in `_ensure_build_workspace` because this is the
    only step that needs it, and deliberately WITHOUT a `bunfig.toml`: every
    setting that matters is on the argv, and a config file beside cwd is one
    more place a future reader would have to check before believing the
    quarantine holds. With no file here, the argv is where the rules are.
    """
    (workspace / BUILD_OUTPUT_DIR).mkdir(mode=0o700, exist_ok=True)
    manifest = workspace / "package.json"
    if manifest.exists():
        return
    body = json.dumps(
        {"name": f"lesson-{slug}", "private": True, "version": "0.0.0"},
        indent=2,
    ) + "\n"
    _atomic_write(manifest, body.encode("utf-8"), mode=0o600)


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        os.unlink(temporary)
        raise
    os.replace(temporary, path)


def _require_build_runtime() -> None:
    """A host with no bun answers with a 503 that says so, not an install failure."""
    if not os.access(BUN_BINARY, os.X_OK):
        raise BuildError(
            "build-unavailable", 503,
            f"no executable package manager at {BUN_BINARY}",
        )
    # Created rather than demanded: bun makes it on first use anyway, and on a
    # host that has never installed anything its absence is not a fault.
    try:
        os.makedirs(BUN_CACHE_DIR, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise BuildError(
            "build-unavailable", 503,
            f"no usable package cache at {BUN_CACHE_DIR}: {exc}",
        ) from exc


async def _run_step(
    step: str,
    *,
    workspace: Path,
    command: list[str],
    timeout: float,
) -> tuple[int, str]:
    """Run one step from the workspace; return its exit status and output tail.

    cwd is the app-owned workspace for both steps and the entry travels as an
    absolute path: `bunfig.toml` carries `preload`, which executes a script,
    and bun reads it from cwd — the bundle, writable from inside the agent's
    own session, is never cwd.
    """
    _require_build_runtime()
    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=workspace,
            env=_STEP_ENV,
            start_new_session=True,
        )
    except OSError as exc:
        raise BuildError("build-unavailable", 503, f"could not start {step}: {exc}") from exc
    async def run() -> bytes:
        assert process.stdout is not None
        tail = await _tail_of(process.stdout, OUTPUT_TAIL_BYTES)
        await process.wait()
        return tail

    try:
        output = await asyncio.wait_for(run(), timeout)
    except asyncio.TimeoutError:
        await _kill(process)
        raise BuildError(
            f"{step}-timeout", 504,
            f"the {step} step ran longer than {timeout:.0f}s and was killed",
        ) from None
    except BaseException:
        # Cancellation, most likely a shutdown. `wait_for` cancels the wait and
        # nothing else, so without this the step keeps running while the lesson
        # lock is released under it — and the next build starts a second bun on
        # the same `package.json`, lockfile and `node_modules`.
        await _kill(process)
        raise
    # A leader that exits normally may still leave a child behind in its
    # group, and once the lesson lock is released nothing else stops it from
    # writing the workspace. The group id is the leader's pid; nobody left in
    # it is the ordinary outcome.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    elapsed = time.monotonic() - started
    tail = output.decode("utf-8", "replace").strip()
    _log.info("lesson build: %s finished rc=%s in %.2fs", step, process.returncode, elapsed)
    return process.returncode, tail


async def _kill(process: asyncio.subprocess.Process) -> None:
    """End the step and wait for it where waiting is still possible.

    The guarantee owed to the caller is the *kill*, and it is taken before the
    first `await`: no bun keeps writing the workspace after the lock protecting
    it is released, whatever happens to this coroutine next. The step was
    started as a session leader, so the kill reaches its children too — and it
    is sent whether or not the leader itself is still there: a child that
    inherited the output pipe is exactly what keeps the tail read waiting
    after the leader has gone.

    On the cancellation path the `await` raises again straight away, which is
    fine and deliberately not shielded — asyncio's child watcher reaps the
    process on SIGCHLD whether or not anyone is waiting, while a shielded task
    left running past the loop's own shutdown is a real leak.
    """
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    # The output pipe outlives its reader. Measured on CPython 3.10: cancelling
    # the task inside `StreamReader.read` leaves the read transport open, so the
    # subprocess transport never finishes and its fd waits for the collector.
    # `Process` exposes no way to say this, and the alternative — owning the
    # pipe here instead of letting asyncio make it — is a great deal of code for
    # two file descriptors.
    transport = getattr(process, "_transport", None)
    if transport is not None:
        transport.close()
    await process.wait()


async def _tail_of(stream: asyncio.StreamReader, limit: int) -> bytes:
    """Read a pipe to the end, keeping only its last `limit` bytes.

    `communicate()` would keep all of it. Only the tail is ever reported, but
    the whole stream would sit in the app's memory until the step finished —
    and the bundle step runs agent-authored code, which is free to print in a
    loop for the full timeout. Trimming as it arrives makes what a noisy build
    costs the app a constant rather than a function of how noisy it is.
    """
    chunks: list[bytes] = []
    held = 0
    while True:
        chunk = await stream.read(64 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
        held += len(chunk)
        while len(chunks) > 1 and held - len(chunks[0]) >= limit:
            held -= len(chunks.pop(0))
    return b"".join(chunks)[-limit:]


def _install_argv(packages: list[str]) -> list[str]:
    """The install command line, with both rules on it and neither negotiable."""
    argv = [
        BUN_BINARY,
        "add" if packages else "install",
        # Isolate this lesson's files from the shared cache entry they were
        # copied out of; see the module docstring for the measurement.
        "--backend=copyfile",
        # The 30-day quarantine, on the argv rather than in a file the agent
        # can reach. `--frozen-lockfile` would bypass it, which is why no step
        # here uses one: every install this app runs may resolve a new version,
        # so every install must be filtered.
        f"--minimum-release-age={MINIMUM_RELEASE_AGE_SECONDS}",
        # Belt to the braces: bun already refuses dependency lifecycle scripts
        # without `trustedDependencies`, and that key would have to appear in a
        # `package.json` no lesson session can write.
        "--ignore-scripts",
        "--no-progress",
        "--no-summary",
    ]
    if packages:
        argv.append("--")
        argv.extend(packages)
    return argv


def _bundle_argv(entry: Path, outdir: Path) -> list[str]:
    return [
        BUN_BINARY, "build", str(entry),
        "--target=browser",
        # One classic script, and the reason is the opaque origin: a lesson
        # renders inside `sandbox allow-scripts`, where an external MODULE
        # script, an import map, a dynamic `import()` and a web font are all
        # fetched in cors mode and blocked, while `<script src>` is not. An
        # IIFE needs no `Access-Control-Allow-Origin` on the files route, so
        # the page keeps working without widening what that route hands out.
        "--format=iife",
        "--production",
        # `--production` minifies identifiers too, which turns every stack
        # trace the render gate reports into noise. Names are worth the bytes.
        "--keep-names",
        # A directory rather than `--outfile`, because a stylesheet import
        # anywhere in the graph gives bun a second output and `--outfile`
        # cannot hold two: measured on bun 1.3.11, an entry with
        # `import "./style.css"` fails with "Multiple files share the same
        # output path" and emits nothing. `katex` is one such package, and
        # `node_modules` is not served, so that is a dead end for the lesson
        # rather than an inconvenience. The naming is fixed so the app knows
        # what to look for without parsing bun's report.
        f"--outdir={outdir}",
        f"--entry-naming={_ARTIFACT_STEM}.[ext]",
        # Every file the bundler read, so the app can refuse to write the
        # artifact over one of them. Nothing else here knows the graph: the
        # request names one entry, and bun follows its imports wherever they
        # lead. Read and removed before the outputs are composed.
        f"--metafile={outdir / _GRAPH_NAME}",
    ]


def _bundle_fd(bundle_dir: Path, ref: str) -> tuple[int, str]:
    """Open the directory that will hold `ref`, refusing to follow a link.

    The bundle is writable from inside the lesson's own session, so every
    component of an agent-supplied output path is untrusted: an `assets`
    symlink pointing at `~/.ssh` would otherwise turn "place the artifact" into
    "write into the owner's home". Each component is opened with `O_NOFOLLOW`
    and missing directories are created rather than resolved, so the final
    `os.replace` lands through a descriptor chain that never left the bundle.
    """
    parts = PurePosixPath(ref).parts
    fd = os.open(bundle_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for component in parts[:-1]:
            try:
                os.mkdir(component, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=fd,
            )
            os.close(fd)
            fd = child
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _linked_paths(lesson_dir: Path) -> list[str]:
    """Every symlink in the bundle, as bundle-relative paths.

    Checking the entry alone is not enough. The entry may `import "./util.js"`
    where `util.js` is a link, and the bundler resolves that import the way any
    other reader would — it has no notion of §2, which says a symlinked path is
    missing, full stop. So the target gets compiled into the artifact, and a
    link pointing outside the bundle gets content the bundle never held.

    `node_modules` is skipped: it is not bundle content but this app's own
    link to the build workspace, and a package manager fills that with links
    (`.bin` shims) by design.
    """
    found: list[str] = []
    for parent, dirs, files in os.walk(lesson_dir, followlinks=False):
        here = Path(parent)
        if here == lesson_dir:
            dirs[:] = [d for d in dirs if d != lessons.BUILD_WORKSPACE_LINK]
        for name in list(dirs) + files:
            path = here / name
            if path.is_symlink():
                found.append(str(path.relative_to(lesson_dir)))
                if len(found) >= 10:
                    return found
    return found


def _set_aside(fd: int, name: str) -> str | None:
    """Move whatever is at `name` out of the way; return where it went.

    A rename rather than a read-and-remember. The thing being displaced is
    whatever the agent last put at this path, which is not necessarily a small
    file this module wrote — reading it into memory to put it back would need a
    cap, and a cap would silently truncate what it was supposed to preserve.

    The destination carries random bytes because `os.rename` replaces its
    target without asking: a predictable `.page.js.previous` would destroy an
    authored file of that name, and destroy it on the *successful* path too,
    where this function's whole job is that nothing is lost.
    """
    aside = f".{name}.{os.urandom(6).hex()}.previous"
    try:
        os.rename(name, aside, src_dir_fd=fd, dst_dir_fd=fd)
    except OSError:
        return None
    return aside


def _reject_irregular_output(fd: int, name: str, out: str, steps: list[dict]) -> None:
    """Refuse an output name that is anything but a file, or absent.

    A directory renames aside as happily as a file does, and then cannot be
    unlinked at the end — the build would report success while an authored
    `assets/page.js/` tree sat stranded under a random hidden name with every
    URL under it broken. Nothing here is a repair the app should be inventing.
    """
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise BuildError(
            "invalid-out", 409,
            f"the bundle would not let the artifact be placed at {out}: "
            f"{exc.strerror or exc}", steps=steps,
        ) from exc
    if not stat.S_ISREG(info.st_mode):
        raise BuildError(
            "invalid-out", 409,
            f"{out} already exists and is not a regular file; the build will "
            "not replace it",
            steps=steps,
        )


def _write_through(fd: int, name: str, data: bytes) -> tuple:
    """Place `data` at `name`; return the identity of the file placed there.

    The identity comes from `fstat` on the descriptor this function wrote, held
    open across the rename, and never from a second look at the path. The agent
    session can replace `name` the instant the rename returns, and a path-based
    stat would then record ITS inode as this build's own — and vouch for those
    bytes at the end, which is the failure this whole check exists to stop.

    Read after the rename, because the rename moves the inode's ctime.
    """
    # Random, for the reason `_set_aside` is: this name is opened `O_TRUNC` and
    # then renamed over `name`, so a predictable one would destroy an authored
    # `.page.js.new` and then present it as the build's output.
    temporary = f".{name}.{os.urandom(6).hex()}.new"
    handle = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o644, dir_fd=fd,
    )
    try:
        # `closefd=False`: the descriptor outlives the stream, because the
        # identity below is read through it after the rename.
        with os.fdopen(handle, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        return _identity_of(os.fstat(handle))
    except BaseException:
        _remove_through(fd, temporary)
        raise
    finally:
        os.close(handle)


def _identity_of(info: os.stat_result) -> tuple:
    """A file's identity, closely enough to notice it being replaced.

    Inode is not enough on its own. It catches an atomic replacement — the
    rename an editor or a script's `>` into a temporary makes — but a plain
    `echo … > assets/page.js` truncates the existing inode in place and keeps
    the number. Size and both timestamps move for that one, so all of it is
    compared.
    """
    return (
        info.st_dev, info.st_ino, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def _identity(fd: int, name: str) -> tuple | None:
    """What is at `name` right now, or `None` if nothing is."""
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:  # pragma: no cover - the descriptor walk already proved it
        return None
    return _identity_of(info)


def _read_artifact(
    outfile: Path, steps: list[dict], *, committed: int = 0,
    optional: bool = False,
) -> bytes | None:
    """Read what the bundler produced, on one descriptor that followed no link.

    `committed` is what the artifact already weighs — the script, when this is
    reading the stylesheet that will travel inside it. It shrinks the room left
    and is added back into the refusal, so the number the agent is given is the
    size of the page it asked for rather than of one half of it.

    `out/` is the bundle step's one writable directory, and that step runs
    agent-authored code: a build-time macro can put a symlink at this name and
    bun will write through it and still exit 0. Following it would be enough on
    its own — a link to `/dev/zero` stats as a regular file of size 0, sails
    past the ceiling, and then reads forever into the app's memory.

    So: opened `O_NOFOLLOW`, required to be a regular file, measured through
    the same descriptor it is read from, and read one byte past the ceiling so
    a file that grows between the two calls is refused rather than trusted.
    """
    limit = max(ARTIFACT_MAX_BYTES - committed, 0)
    try:
        fd = os.open(outfile, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        if optional:
            return None
        raise BuildError(
            "bundle-failed", 422,
            "the bundler left no readable artifact", steps=steps,
        ) from None
    except OSError as exc:
        raise BuildError(
            "bundle-failed", 422,
            f"the bundler left no readable artifact: {exc.strerror or exc}",
            steps=steps,
        ) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise BuildError(
                "bundle-failed", 422,
                "the bundler's output is not a regular file", steps=steps,
            )
        if info.st_size > limit:
            raise _too_large(committed + info.st_size, steps)
        with os.fdopen(os.dup(fd), "rb") as handle:
            artifact = handle.read(limit + 1)
    finally:
        os.close(fd)
    if len(artifact) > limit:
        raise _too_large(committed + len(artifact), steps)
    return artifact


def _too_large(size: int, steps: list[dict]) -> BuildError:
    return BuildError(
        "artifact-too-large", 422,
        f"the built page is {size} bytes, over the {ARTIFACT_MAX_BYTES}-byte "
        "ceiling; import the names you use instead of the package default, or "
        "split the page",
        steps=steps, bytes=size, limit=ARTIFACT_MAX_BYTES,
    )


def _outdir_names(outdir: Path) -> list[str]:
    try:
        return os.listdir(outdir)
    except OSError:
        return []


def _empty_outdir(outdir: Path, steps: list[dict]) -> None:
    """Replace the scratch directory with an empty one of the same name.

    The whole tree, not just the files: the bundle step runs agent-authored
    code with this as its one writable place, and a macro that leaves a
    directory behind would otherwise stay forever. Nothing in the app can reach
    into the private workspace to remove it, `_compose_artifact` reports it as a
    stray output, and the lesson is unbuildable from then on — including after
    the macro is fixed.

    Removed rather than emptied in place because `shutil.rmtree` refuses to
    descend through a symlink, and recreating the name afterwards costs one
    syscall.
    """
    shutil.rmtree(outdir, ignore_errors=True)
    try:
        outdir.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise BuildError(
            "workspace-unavailable", 503,
            f"the build has nowhere to write: {exc.strerror or exc}", steps=steps,
        ) from exc


def _graph_sources(outdir: Path, workspace: Path, steps: list[dict]) -> set[str]:
    """Every file the bundler read, absolute, from bun's own report.

    Read on a descriptor that follows no link and removed straight away, so the
    report is gone before `_compose_artifact` counts what is left in `out/`.

    Fails closed. A build whose graph cannot be read is a build whose output
    path cannot be checked against it, and the check exists to stop a
    successful build from destroying an authored file.

    Paths in the report are relative to the bundler's cwd, which is the
    workspace, and are compared lexically: every symlink in the bundle has
    already been refused by `_linked_paths`, so two spellings of one file
    cannot get past this by disagreeing.
    """
    report = outdir / _GRAPH_NAME
    try:
        fd = os.open(report, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise BuildError(
            "bundle-failed", 422,
            f"the bundler reported no module graph, so the artifact cannot be "
            f"checked against what it was built from: {exc.strerror or exc}",
            steps=steps,
        ) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _GRAPH_MAX_BYTES:
            raise BuildError(
                "bundle-failed", 422,
                "the bundler's module graph is not a file this app will read",
                steps=steps,
            )
        with os.fdopen(os.dup(fd), "rb") as handle:
            raw = handle.read(_GRAPH_MAX_BYTES + 1)
    finally:
        os.close(fd)
    # `unlink` never follows a link, so this removes whatever name is there and
    # nothing it points at.
    try:
        os.unlink(report)
    except OSError:  # pragma: no cover - the next run empties the directory
        pass
    try:
        graph = json.loads(raw.decode("utf-8"))
        inputs = graph["inputs"]
        if not isinstance(inputs, dict):
            raise TypeError("inputs is not an object")
    except (UnicodeDecodeError, ValueError, TypeError, KeyError) as exc:
        raise BuildError(
            "bundle-failed", 422,
            f"the bundler's module graph could not be read: {exc}", steps=steps,
        ) from exc
    sources: set[str] = set()
    for key, value in inputs.items():
        if isinstance(key, str):
            sources.add(os.path.normpath(os.path.join(workspace, key)))
        for entry in (value or {}).get("imports", []) if isinstance(value, dict) else []:
            path = entry.get("path") if isinstance(entry, dict) else None
            if isinstance(path, str):
                sources.add(os.path.normpath(os.path.join(workspace, path)))
    return sources


def _compose_artifact(outdir: Path, steps: list[dict]) -> bytes:
    """One file out of what the bundler may have written as two.

    A stylesheet import gives bun a second output. Serving it as a second file
    would mean a second placement, a second URL and a `<link>` the page has to
    carry — and the page cannot fetch it as a module, cannot reach
    `node_modules`, and would need the whole `<script src>` story told again
    for CSS. Carrying the stylesheet inside the script instead keeps the
    promise the page was written against: one artifact, one classic tag.

    `style-src` in `interactive-local-v1` allows inline styles, so the injected
    element is not the thing the render gate is watching for.

    A third output is refused rather than dropped. Measured on bun 1.3.11, the
    stock KaTeX stylesheet and a CSS `url()` to a local PNG both come back
    inlined as `data:` URLs, so nothing is left beside the two names — and if a
    future input does leave something, it would be a file the page references
    and the bundle does not hold. On an opaque origin a web font cannot be
    fetched cross-scheme anyway, so placing such a file would not save the
    page; saying so is the only useful answer.
    """
    strays = sorted(
        name for name in _outdir_names(outdir)
        if name not in (f"{_ARTIFACT_STEM}.js", f"{_ARTIFACT_STEM}.css")
    )
    if strays:
        raise BuildError(
            "split-artifact", 422,
            "the bundler produced files the page has no way to load: "
            + ", ".join(strays[:10])
            + "; a lesson is served one script, so import what you need as "
            "code or as a data URL rather than as a side file",
            steps=steps,
        )
    script = _read_artifact(outdir / f"{_ARTIFACT_STEM}.js", steps)
    assert script is not None
    styles = _read_artifact(
        outdir / f"{_ARTIFACT_STEM}.css", steps,
        committed=len(script), optional=True,
    )
    if not styles:
        return script
    injector = (
        "(()=>{var s=document.createElement('style');s.textContent="
        + json.dumps(styles.decode("utf-8", "replace"))
        + ";(document.head||document.documentElement).appendChild(s)})();\n"
    ).encode("utf-8")
    combined = injector + script
    if len(combined) > ARTIFACT_MAX_BYTES:
        raise _too_large(len(combined), steps)
    return combined


def _touch_page(bundle_dir: Path, page: str | None) -> None:
    """Move the page's mtime forward so the preview notices the new artifact.

    Never fatal: the artifact has already passed every gate and belongs in the
    bundle. The worst case without this is the stale preview it exists to
    prevent, which a reload fixes.
    """
    if not page:
        return
    try:
        fd, name = _bundle_fd(bundle_dir, page)
    except OSError:
        return
    try:
        os.utime(name, dir_fd=fd, follow_symlinks=False)
    except OSError:  # pragma: no cover - a page we just rendered
        _log.warning("lesson build: could not touch %s in %s", page, bundle_dir)
    finally:
        os.close(fd)


def _remove_through(fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=fd)
    except OSError:  # pragma: no cover - already gone is the wanted state
        pass


async def build_lesson(
    lesson: dict,
    *,
    add: list[str],
    entry: str,
    out: str,
    page: str | None,
    page_url: str,
    artifact_url: str,
) -> dict:
    """Install, bundle, size-gate and render-gate one lesson page.

    The artifact reaches the bundle only if every gate passes. It is written
    before the render gate — the page loads it by relative path, so there is no
    way to render the built page without the built file being in place — and
    put back the way it was if the gate fails. A lesson is never left carrying
    an artifact this function knows to be broken.

    The lock below serialises builds, and cannot serialise the agent session
    that owns the same bundle by hand. So the output is checked, after the
    gate, for still being the file this build placed: a page the agent replaced
    mid-check is neither deleted by the undo nor reported as rendered.
    """
    async with _lesson_lock(lesson["slug"]):
        return await _build_locked(
            lesson, add=add, entry=entry, out=out, page=page,
            page_url=page_url, artifact_url=artifact_url,
        )


# One build at a time per lesson. Two `bun add` runs sharing a workspace race
# on `package.json` and the lockfile, and two placements race on the same
# bundle name — and the agent has every reason to fire a rebuild while the
# last one is still going.
_LOCKS: dict[str, asyncio.Lock] = {}


def _lesson_lock(slug: str) -> asyncio.Lock:
    lock = _LOCKS.get(slug)
    if lock is None:
        lock = _LOCKS[slug] = asyncio.Lock()
    return lock


async def _build_locked(
    lesson: dict,
    *,
    add: list[str],
    entry: str,
    out: str,
    page: str | None,
    page_url: str,
    artifact_url: str,
) -> dict:
    slug = lesson["slug"]
    lesson_dir = lessons.lesson_bundle_dir(slug)
    if not lesson_dir.is_dir():
        raise BuildError("no-bundle", 409, "this lesson has no bundle directory yet")
    try:
        workspace = lessons.ensure_build_workspace(slug)
    except (OSError, lessons.LessonError) as exc:
        raise BuildError("workspace-unavailable", 503, str(exc)) from exc
    # §2 of the bundle spec: a symlinked path is missing, full stop, and every
    # other reader here checks that BEFORE resolving so the link is never
    # followed. `bundle_ref_path` only raises when the target escapes the
    # bundle, so a link to a sibling inside it would otherwise be compiled —
    # and an agent can make either kind. Both read as "not in this bundle".
    try:
        symlinked = bundle_schema.path_has_symlink(lesson_dir, entry)
        entry_path = lessons.bundle_ref_path(slug, entry)
        readable = not symlinked and entry_path.is_file()
    except lessons.LessonError:
        readable = False
    if not readable:
        raise BuildError("no-entry", 404, f"no such source in the bundle: {entry}")

    steps: list[dict] = []
    _seed_workspace(workspace, slug)

    started = time.monotonic()
    code, output = await _run_step(
        "install", workspace=workspace,
        command=_install_argv(add), timeout=INSTALL_TIMEOUT_SECONDS,
    )
    steps.append({"step": "install", "ok": code == 0,
                  "seconds": round(time.monotonic() - started, 2), "output": output})
    if code != 0:
        raise BuildError("install-failed", 422, "the install step failed",
                         steps=steps)

    # After the install, because that is when the bundle stops changing and the
    # walk still costs nothing: the bundler is about to follow every local
    # import, and a link anywhere along that graph would smuggle in content §2
    # says is not here.
    linked = _linked_paths(lesson_dir)
    if linked:
        raise BuildError(
            "linked-source", 409,
            "the bundler follows a symlink where the rest of this app treats "
            "it as a missing file, so a bundle holding one cannot be built: "
            + ", ".join(sorted(linked)),
            steps=steps,
        )

    # Straight into the workspace: the artifact is judged before the bundle is
    # allowed to see it, and the workspace is outside anything this app serves.
    outdir = workspace / BUILD_OUTPUT_DIR
    # Cleared first, so a bundler that exits 0 without writing cannot get the
    # previous run's bytes accepted as this run's result — and so a stylesheet
    # from a graph that no longer imports one cannot be carried forward.
    _empty_outdir(outdir, steps)
    started = time.monotonic()
    code, output = await _run_step(
        "bundle", workspace=workspace,
        command=_bundle_argv(entry_path, outdir), timeout=BUNDLE_TIMEOUT_SECONDS,
    )
    steps.append({"step": "bundle", "ok": code == 0,
                  "seconds": round(time.monotonic() - started, 2), "output": output})
    if code != 0:
        raise BuildError("bundle-failed", 422, "the bundle step failed", steps=steps)

    # `entry == out` is the obvious way to ask the build to eat its own source,
    # and not the only one: the entry may import `../assets/helper.js` while the
    # request names that same file as `out`. bun reads both happily, the page
    # renders, and the placement then writes the whole bundle over the helper
    # and — on a passing render — deletes the copy it set aside. The authored
    # file is gone, and the response says `ok`. So the output is checked against
    # every file the build actually read, not just against the entry.
    destination = os.path.normpath(lesson_dir / out)
    if destination in _graph_sources(outdir, workspace, steps):
        raise BuildError(
            "out-is-source", 409,
            f"`out` names {out}, which this build read as source; writing the "
            "artifact there would replace it with the bundle it was compiled "
            "into and leave no copy behind",
            steps=steps,
        )
    artifact = _compose_artifact(outdir, steps)

    # The descriptor walk refuses a symlinked or non-directory parent, which is
    # a thing the agent can create in its own bundle and therefore a request to
    # refuse in the response, not a fault to raise through it.
    try:
        fd, name = _bundle_fd(lesson_dir, out)
    except OSError as exc:
        raise BuildError(
            "invalid-out", 409,
            f"the bundle cannot hold an artifact at {out}: {exc.strerror or exc}; "
            "a directory on that path is a symlink or not a directory",
            steps=steps,
        ) from exc
    async def place() -> None:
        aside = None
        placed: tuple | None = None
        try:
            _reject_irregular_output(fd, name, out, steps)
            aside = _set_aside(fd, name)
            placed = _write_through(fd, name, artifact)
            try:
                errors = await asyncio.to_thread(
                    render_check.console_errors, page_url, expect_url=artifact_url,
                )
            except render_check.RenderCheckUnavailable as exc:
                raise BuildError(
                    "render-check-unavailable", 503, str(exc), steps=steps
                ) from exc
            except OSError as exc:  # pragma: no cover - browser died mid-check
                raise BuildError(
                    "render-check-unavailable", 503, str(exc), steps=steps
                ) from exc
            # Before the verdict is used for anything: is it a verdict about a
            # file that is still there? The gate read the bundle over seconds of
            # released event loop, and the lesson-agent session writes into this
            # same bundle. If the path changed hands in that window, the render
            # says nothing about what a reader would now get, so neither
            # outcome — pass or fail — is one this build may act on.
            if _identity(fd, name) != placed:
                raise BuildError(
                    "output-replaced", 409,
                    f"{out} was written or removed by something else while the "
                    "built page was being checked, so what the check saw is not "
                    "what is there now; that change is left alone and this build "
                    "was not applied",
                    steps=steps, page=page, page_url=page_url,
                )
            if errors:
                raise BuildError(
                    "render-errors", 422,
                    f"the built page did not come back clean ({len(errors)} "
                    "problem(s)); the previous artifact is back in place",
                    steps=steps, errors=errors, page=page, page_url=page_url,
                )
        except BaseException:
            # Every path out of here that is not a pass puts the bundle back the
            # way it was found: a lesson never keeps an artifact this function
            # knows to be broken, and never loses the one that worked. What the
            # undo may NOT do is reach over a change this build did not make —
            # the agent session owns these files too.
            #
            # So once the artifact was placed, the path stops being this
            # function's to undo the moment it stops being the file it placed.
            # Replaced and removed are the same answer: the agent said something
            # about that path, and neither an unlink nor a restore of the aside
            # may argue with it. Resurrecting a page the agent deleted is as
            # wrong as burying one it wrote.
            restore = aside
            if placed is not None and _identity(fd, name) != placed:
                if aside is not None:
                    _remove_through(fd, aside)
                restore = None
            elif placed is not None and aside is None:
                # Ours, and nothing to put back: removing it IS the undo. Only
                # reached when the output name was free before this build.
                _remove_through(fd, name)
            # What is left restores the aside — over our own artifact, or onto a
            # path the write never reached. A rename replaces its target, so
            # neither needs an unlink first: one destructive syscall, with
            # nothing observable in between.
            #
            # That rename is still by name. POSIX has no "replace this path only
            # while it is still inode N", so a write landing between the check
            # above and the rename is overwritten. The window this repair is
            # about — the render gate, seconds of released event loop with a
            # browser in it — is closed; what is left is two adjacent syscalls,
            # and closing that needs a guarantee the filesystem interface does
            # not offer.
            if restore is not None:
                try:
                    os.rename(restore, name, src_dir_fd=fd, dst_dir_fd=fd)
                except OSError:  # pragma: no cover - nothing better to try
                    _log.warning(
                        "lesson build: could not restore %s in %s", name, lesson_dir
                    )
            raise
        else:
            if aside is not None:
                _remove_through(fd, aside)

    try:
        await place()
    except OSError as exc:
        # The walk proved the path, but the directory it ends in can still
        # refuse the write — `chmod 0555 assets` is an ordinary thing for an
        # agent to have done, and it deserves the same typed answer.
        raise BuildError(
            "invalid-out", 409,
            f"the bundle would not accept the artifact at {out}: "
            f"{exc.strerror or exc}",
            steps=steps,
        ) from exc
    finally:
        os.close(fd)

    # The learner may have this page open. Every consumer of the preview
    # token — the route's `?v` check, the metadata and the poll — derives it
    # from the PAGE's stat, and replacing a script the page loads changes none
    # of that, so an open iframe would keep running the artifact this build
    # just replaced. Touching the page is the smallest true statement: what it
    # renders did change.
    _touch_page(lesson_dir, page)
    steps.append({"step": "render", "ok": True, "page": page, "url": page_url})
    return {
        "ok": True,
        "artifact": {"path": out, "bytes": len(artifact)},
        "entry": entry,
        "packages": _installed_packages(workspace),
        "steps": steps,
    }


def _installed_packages(workspace: Path) -> dict:
    """What the workspace `package.json` now pins, for the agent's report."""
    try:
        data = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    dependencies = data.get("dependencies")
    return dependencies if isinstance(dependencies, dict) else {}
