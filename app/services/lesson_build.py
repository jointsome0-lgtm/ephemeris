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
import time
from pathlib import Path, PurePosixPath

from .. import sandbox
from . import lessons, render_check

_log = logging.getLogger(__name__)

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
    if field == "out" and PurePosixPath(ref).suffix.lower() != ".js":
        raise BuildError("invalid-request", 400, "`out` must end in .js")
    return ref


def _seed_workspace(workspace: Path, slug: str) -> None:
    """Give bun a project root to install into, once.

    Written here rather than in `_ensure_build_workspace` because this is the
    only step that needs it, and deliberately WITHOUT a `bunfig.toml`: every
    setting that matters is on the argv, and a config file beside cwd is one
    more place a future reader would have to check before believing the
    quarantine holds. With no file here and a blank `$HOME` in the view, bun
    finds no configuration at all — which is the state the argv assumes.
    """
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


async def _run_step(
    step: sandbox.BuildStep,
    *,
    workspace: Path,
    bundle_dir: Path | None,
    command: list[str],
    timeout: float,
) -> tuple[int, str]:
    """Run one step in its mount view; return its exit status and output tail."""
    sandbox.require_sandbox_runtime()
    try:
        argv = sandbox.build_step_argv(
            step,
            build_workspace=str(workspace),
            bundle_root=str(lessons.LESSONS_DIR),
            private_root=str(lessons.LESSONS_DIR.parent),
            bundle_dir=str(bundle_dir) if bundle_dir is not None else None,
            command=command,
        )
    except ValueError as exc:
        raise BuildError("build-unavailable", 500, f"could not build the step view: {exc}") from exc
    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        raise BuildError("build-unavailable", 503, f"could not start {step}: {exc}") from exc
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise BuildError(
            f"{step}-timeout", 504,
            f"the {step} step ran longer than {timeout:.0f}s and was killed",
        ) from None
    elapsed = time.monotonic() - started
    tail = output[-OUTPUT_TAIL_BYTES:].decode("utf-8", "replace").strip()
    _log.info("lesson build: %s finished rc=%s in %.2fs", step, process.returncode, elapsed)
    return process.returncode, tail


def _install_argv(packages: list[str]) -> list[str]:
    """The install command line, with both rules on it and neither negotiable."""
    argv = [
        sandbox.BUN_BINARY,
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


def _bundle_argv(entry: Path, outfile: Path) -> list[str]:
    return [
        sandbox.BUN_BINARY, "build", str(entry),
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
        f"--outfile={outfile}",
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


def _set_aside(fd: int, name: str) -> str | None:
    """Move whatever is at `name` out of the way; return where it went.

    A rename rather than a read-and-remember. The thing being displaced is
    whatever the agent last put at this path, which is not necessarily a small
    file this module wrote — reading it into memory to put it back would need a
    cap, and a cap would silently truncate what it was supposed to preserve.
    """
    aside = f".{name}.previous"
    try:
        os.rename(name, aside, src_dir_fd=fd, dst_dir_fd=fd)
    except OSError:
        return None
    return aside


def _write_through(fd: int, name: str, data: bytes) -> None:
    temporary = f".{name}.new"
    handle = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o644, dir_fd=fd,
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        os.unlink(temporary, dir_fd=fd)
        raise
    os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)


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
) -> dict:
    """Install, bundle, size-gate and render-gate one lesson page.

    The artifact reaches the bundle only if every gate passes. It is written
    before the render gate — the page loads it by relative path, so there is no
    way to render the built page without the built file being in place — and
    put back the way it was if the gate fails. A lesson is never left carrying
    an artifact this function knows to be broken.
    """
    async with _lesson_lock(lesson["slug"]):
        return await _build_locked(
            lesson, add=add, entry=entry, out=out, page=page, page_url=page_url,
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
) -> dict:
    slug = lesson["slug"]
    lesson_dir = lessons.lesson_bundle_dir(slug)
    if not lesson_dir.is_dir():
        raise BuildError("no-bundle", 409, "this lesson has no bundle directory yet")
    try:
        workspace = lessons.ensure_build_workspace(slug)
    except (OSError, lessons.LessonError) as exc:
        raise BuildError("workspace-unavailable", 503, str(exc)) from exc
    entry_path = lessons.bundle_ref_path(slug, entry)
    if not entry_path.is_file():
        raise BuildError("no-entry", 404, f"no such source in the bundle: {entry}")

    steps: list[dict] = []
    _seed_workspace(workspace, slug)

    started = time.monotonic()
    code, output = await _run_step(
        "install", workspace=workspace, bundle_dir=None,
        command=_install_argv(add), timeout=INSTALL_TIMEOUT_SECONDS,
    )
    steps.append({"step": "install", "ok": code == 0,
                  "seconds": round(time.monotonic() - started, 2), "output": output})
    if code != 0:
        raise BuildError("install-failed", 422, "the install step failed",
                         steps=steps)

    # Straight into the workspace: the artifact is judged before the bundle is
    # allowed to see it, and `<workspace>` is the one directory in the view
    # that is writable and outside anything this app serves.
    outfile = workspace / "artifact.js"
    # Cleared first, so a bundler that exits 0 without writing cannot get the
    # previous run's bytes accepted as this run's result.
    outfile.unlink(missing_ok=True)
    started = time.monotonic()
    code, output = await _run_step(
        "bundle", workspace=workspace, bundle_dir=lesson_dir,
        command=_bundle_argv(entry_path, outfile), timeout=BUNDLE_TIMEOUT_SECONDS,
    )
    steps.append({"step": "bundle", "ok": code == 0,
                  "seconds": round(time.monotonic() - started, 2), "output": output})
    if code != 0:
        raise BuildError("bundle-failed", 422, "the bundle step failed", steps=steps)
    try:
        artifact = outfile.read_bytes()
    except OSError as exc:
        raise BuildError("bundle-failed", 422, f"the bundler wrote nothing: {exc}",
                         steps=steps) from exc
    if len(artifact) > ARTIFACT_MAX_BYTES:
        raise BuildError(
            "artifact-too-large", 422,
            f"the built page is {len(artifact)} bytes, over the "
            f"{ARTIFACT_MAX_BYTES}-byte ceiling; import the names you use "
            "instead of the package default, or split the page",
            steps=steps, bytes=len(artifact), limit=ARTIFACT_MAX_BYTES,
        )

    fd, name = _bundle_fd(lesson_dir, out)
    aside = None
    placed = False
    try:
        aside = _set_aside(fd, name)
        _write_through(fd, name, artifact)
        placed = True
        try:
            errors = await asyncio.to_thread(render_check.console_errors, page_url)
        except render_check.RenderCheckUnavailable as exc:
            raise BuildError("render-check-unavailable", 503, str(exc), steps=steps) from exc
        except OSError as exc:  # pragma: no cover - browser died mid-check
            raise BuildError("render-check-unavailable", 503, str(exc), steps=steps) from exc
        if errors:
            raise BuildError(
                "render-errors", 422,
                f"the built page reported {len(errors)} console error(s); "
                "the previous artifact is back in place",
                steps=steps, errors=errors, page=page, page_url=page_url,
            )
    except BaseException:
        # Every path out of here that is not a pass puts the bundle back the
        # way it was found. A lesson never keeps an artifact this function
        # knows to be broken, and never loses the one that worked.
        if placed:
            _remove_through(fd, name)
        if aside is not None:
            try:
                os.rename(aside, name, src_dir_fd=fd, dst_dir_fd=fd)
            except OSError:  # pragma: no cover - nothing better to try
                _log.warning("lesson build: could not restore %s in %s", name, lesson_dir)
        raise
    else:
        if aside is not None:
            _remove_through(fd, aside)
    finally:
        os.close(fd)

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
