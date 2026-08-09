"""The lesson build step and its acceptance gates (#161).

Three layers, gated separately so each runs wherever it can:

- the command lines and the mount views, which are pure functions and always run;
- the render gate, which needs a browser but nothing else, and so runs in CI;
- the real install-and-bundle, which needs this host's bubblewrap and bun and
  therefore skips anywhere else — the same posture the other sandbox tests take.

What the first layer is really guarding is a pair of measured facts, both from
2026-08-09 on bun 1.3.11. A `bunfig.toml` with `minimumReleaseAge = 0` beside
cwd installed katex 0.18.2, published the day before; the same install with
`--minimum-release-age=2592000` on the argv took 0.17.0 instead. And with the
default backend a lesson's `node_modules/katex/package.json` and the shared
cache entry were one inode (19167021), so an edit through the lesson reached
every later install; `--backend=copyfile` gave distinct inodes. Neither rule
survives in a config file the agent can write, so both are asserted here on the
argv, where the app is the only author.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app import sandbox
from app.services import lesson_build, render_check

HAVE_SANDBOX = Path(sandbox.BWRAP).exists() and Path(sandbox.BUN_BINARY).exists()
needs_sandbox = pytest.mark.skipif(
    not HAVE_SANDBOX, reason="the build step needs this host's bubblewrap and bun"
)
CHROME = render_check.chrome_binary()
needs_browser = pytest.mark.skipif(
    CHROME is None, reason="the render gate needs a real browser"
)


# --- the command lines -------------------------------------------------------

def test_the_install_argv_carries_both_rules_the_agent_could_otherwise_undo():
    argv = lesson_build._install_argv(["d3"])
    assert f"--minimum-release-age={lesson_build.MINIMUM_RELEASE_AGE_SECONDS}" in argv
    assert lesson_build.MINIMUM_RELEASE_AGE_SECONDS == 2592000, "30 days, in bun's seconds"
    assert "--backend=copyfile" in argv
    assert "--ignore-scripts" in argv
    # `--frozen-lockfile` replays a lockfile without consulting release age. It
    # is the right flag for CI and the wrong one here: every install this app
    # runs may resolve a version nobody has reviewed, so every one is filtered.
    assert "--frozen-lockfile" not in argv
    # The packages come last, behind `--`, so a spec can never be read as a flag.
    assert argv[-2:] == ["--", "d3"]


def test_an_install_with_nothing_to_add_still_carries_the_rules():
    argv = lesson_build._install_argv([])
    assert argv[1] == "install" and "--" not in argv
    assert "--backend=copyfile" in argv
    assert f"--minimum-release-age={lesson_build.MINIMUM_RELEASE_AGE_SECONDS}" in argv


def test_the_bundle_argv_asks_for_one_classic_script():
    argv = lesson_build._bundle_argv(Path("/b/src/page.ts"), Path("/w/out"))
    # The opaque origin a lesson renders on blocks an external module script,
    # an import map and a dynamic import; a classic `<script src>` it allows.
    assert "--format=iife" in argv
    assert "--target=browser" in argv
    # A directory, because a stylesheet import gives bun a second output and
    # `--outfile` refuses to hold two — with a naming rule, so the app knows
    # what to read without parsing bun's report.
    assert "--outdir=/w/out" in argv
    assert f"--entry-naming={lesson_build._ARTIFACT_STEM}.[ext]" in argv
    assert not [word for word in argv if word.startswith("--outfile")]
    # The module graph, beside the outputs: the request names one entry and the
    # bundler follows its imports, so this is the app's only way to know which
    # files the artifact must not be written over.
    assert f"--metafile=/w/out/{lesson_build._GRAPH_NAME}" in argv


@pytest.mark.parametrize("spec", [
    "--backend=hardlink", "-x", "--minimum-release-age=0", "d3 && rm -rf /",
    "../escape", "", "d3@--force", "UPPER", "a" * 300,
])
def test_a_package_spec_cannot_smuggle_a_flag_or_a_path(spec):
    with pytest.raises(lesson_build.BuildError) as caught:
        lesson_build.clean_packages([spec])
    assert caught.value.code == "invalid-request"


@pytest.mark.parametrize("spec", [
    "d3", "katex@0.17.0", "@scope/thing@^1.2.3", "mermaid@>=11 <12", "left-pad@*",
])
def test_an_ordinary_package_spec_is_accepted(spec):
    assert lesson_build.clean_packages([spec]) == [spec]


def test_the_output_must_be_a_script_inside_the_bundle():
    assert lesson_build.clean_source_ref("assets/page.js", "out") == "assets/page.js"
    for bad in ("../outside.js", "/etc/passwd", "assets/page.html", ""):
        with pytest.raises(lesson_build.BuildError):
            lesson_build.clean_source_ref(bad, "out")


def test_the_output_must_land_where_the_lesson_route_will_serve_it():
    """A v2 bundle serves declared pages and `assets/`, and nothing else.

    An artifact anywhere else 404s for the page that references it, so every
    build of that lesson would be refused by the render gate — truthfully, and
    for a reason that explains nothing. Say it at the door instead.
    """
    for outside in ("page.js", "dist/page.js", "src/built.js"):
        with pytest.raises(lesson_build.BuildError) as caught:
            lesson_build.clean_source_ref(outside, "out")
        assert "assets/" in caught.value.detail
    assert lesson_build.clean_source_ref("assets/deep/page.js", "out")
    for bad in ("../outside.ts", "page.html", "page.css"):
        with pytest.raises(lesson_build.BuildError):
            lesson_build.clean_source_ref(bad, "entry")


def test_the_output_may_not_land_on_the_shelf_the_app_reseeds():
    """`assets/libs/` is written by the app, not by the lesson.

    It is restored on every terminal open, so an artifact placed there is
    replaced by the vendored copy minutes after the build reported success —
    the page changes with nothing in the response to say why.
    """
    from app.services import lessons

    for ref in (f"{lessons.LESSON_LIBS_BUNDLE_DIR}/d3/d3.min.js",
                f"{lessons.LESSON_LIBS_BUNDLE_DIR}/anything.js"):
        with pytest.raises(lesson_build.BuildError) as caught:
            lesson_build.clean_source_ref(ref, "out")
        assert caught.value.status == 400
    # The neighbours are fine; only the managed subtree is out of bounds.
    assert lesson_build.clean_source_ref("assets/libraries.js", "out")
    assert lesson_build.clean_source_ref("assets/lib/page.js", "out")


def test_a_third_output_is_refused_rather_than_quietly_dropped(tmp_path):
    """Two names are read; anything else would be a file the page cannot load.

    Measured on bun 1.3.11 this does not arise — the stock KaTeX stylesheet
    and a CSS `url()` to a PNG both come back inlined as data URLs. If some
    input ever does split the output, the page would reference a file the
    bundle does not hold, and a refusal says that where a 404 later would not.
    """
    (tmp_path / f"{lesson_build._ARTIFACT_STEM}.js").write_bytes(b"(()=>{})();")
    (tmp_path / "KaTeX_Main-Regular.woff2").write_bytes(b"font")
    with pytest.raises(lesson_build.BuildError) as caught:
        lesson_build._compose_artifact(tmp_path, [])
    assert caught.value.code == "split-artifact" and caught.value.status == 422
    assert "KaTeX_Main-Regular.woff2" in caught.value.detail

    (tmp_path / "KaTeX_Main-Regular.woff2").unlink()
    (tmp_path / f"{lesson_build._ARTIFACT_STEM}.css").write_bytes(b".a{color:red}")
    combined = lesson_build._compose_artifact(tmp_path, [])
    assert b".a{color:red}" in combined and combined.endswith(b"(()=>{})();")
    assert b"document.createElement('style')" in combined


def test_a_directory_left_in_the_scratch_does_not_wedge_the_lesson(tmp_path):
    """A macro can make one, and nothing else can ever remove it.

    The scratch is inside the private workspace, so no session reaches it; if
    it were only emptied of files, `_compose_artifact` would report the leftover
    directory as a stray output and refuse every future build of that lesson —
    including after the macro that made it is gone.
    """
    outdir = tmp_path / "out"
    (outdir / "junk" / "deeper").mkdir(parents=True)
    (outdir / "junk" / "deeper" / "f.txt").write_text("x", encoding="utf-8")
    (outdir / "artifact.js").write_text("old", encoding="utf-8")
    # A link out of the scratch: removed as a link, never followed.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("authored", encoding="utf-8")
    (outdir / "junk" / "escape").symlink_to(outside)

    lesson_build._empty_outdir(outdir, [])
    assert outdir.is_dir() and list(outdir.iterdir()) == []
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "authored"
    assert oct(outdir.stat().st_mode)[-3:] == "700"


def test_a_cancelled_step_does_not_leave_a_package_manager_running(monkeypatch):
    """`wait_for` cancels the wait, not the process.

    The lesson lock is released as the request unwinds, so a surviving bun
    would still be writing `package.json`, the lockfile and `node_modules`
    while the next build starts a second one on the same files.
    """
    import asyncio
    import subprocess as _sp

    marker = f"ephemeris-cancel-probe-{os.getpid()}"
    monkeypatch.setattr(lesson_build, "_require_build_runtime", lambda: None)
    monkeypatch.setattr(
        sandbox, "build_step_argv",
        lambda *a, **k: ["/bin/sh", "-c", f"# {marker}\nsleep 30"],
    )
    # And the scope around it: this test is about what `_run_step` does with a
    # process it started, and CI has no systemd user session — `systemd-run
    # --user` there exits with "Failed to connect to bus" before the probe can
    # run, and the failure would read as a lifecycle bug that is not there.
    monkeypatch.setattr(sandbox, "build_scope_prefix", lambda *a, **k: ())

    async def scenario():
        task = asyncio.ensure_future(lesson_build._run_step(
            "install", workspace=Path("/nonexistent"), bundle_dir=None,
            command=["/bin/true"], timeout=30,
        ))
        # Long enough for the shell to be up and running under the marker.
        await asyncio.sleep(0.5)
        alive = _sp.run(["pgrep", "-f", marker], capture_output=True, text=True)
        assert alive.returncode == 0, "the probe never started"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.2)
        return _sp.run(["pgrep", "-f", marker], capture_output=True, text=True)

    left = asyncio.run(scenario())
    assert left.returncode != 0, f"the step outlived the request: {left.stdout}"


def test_a_build_may_not_write_its_artifact_over_its_own_source():
    """`{"entry": "x.js", "out": "x.js"}` would destroy the authored source.

    The bundler reads the entry, the artifact is written over `out`, and on a
    passing render the copy set aside is deleted — so the request would come
    back `ok: true` having thrown away the file it was given.
    """
    with pytest.raises(lesson_build.BuildError) as caught:
        lesson_build.clean_build_refs(
            {"entry": "assets/page.js", "out": "assets/page.js"}
        )
    assert caught.value.status == 400
    assert "same file" in caught.value.detail
    # A different name for the same kind of file is ordinary and stays allowed.
    assert lesson_build.clean_build_refs(
        {"entry": "src/page.js", "out": "assets/page.js"}
    ) == ("src/page.js", "assets/page.js")


def test_a_noisy_step_costs_a_constant_not_its_own_noise():
    """Only the tail is ever reported, so only the tail is ever held.

    The bundle step runs agent-authored code, which may print in a loop for the
    whole 120-second timeout; buffering all of it to show 8 KiB would make what
    a build costs the app a function of how loud it is.
    """
    import asyncio

    async def tail_of(chunks, limit):
        reader = asyncio.StreamReader()
        for chunk in chunks:
            reader.feed_data(chunk)
        reader.feed_eof()
        return await lesson_build._tail_of(reader, limit)

    loud = [f"line {i}\n".encode() for i in range(20000)]
    whole = b"".join(loud)
    tail = asyncio.run(tail_of(loud, 512))
    assert tail == whole[-512:]
    assert len(tail) == 512
    # Short output survives intact rather than being padded or clipped.
    assert asyncio.run(tail_of([b"brief"], 512)) == b"brief"


# --- the mount views ---------------------------------------------------------

ROOTS = {
    "build_workspace": "/data/lesson-builds/demo",
    "bundle_root": "/data/lessons",
    "private_root": "/data",
}


def _pairs(argv: list[str], flag: str) -> list[tuple[str, str]]:
    return [
        (argv[i + 1], argv[i + 2])
        for i, word in enumerate(argv) if word == flag and i + 2 < len(argv)
    ]


def _mount_at(argv: list[str], flag: str, source: str, target: str) -> int:
    """Where one mount sits in the argv. Order decides which of two overlapping
    binds wins, so it is part of the meaning, not of the formatting."""
    for i, word in enumerate(argv):
        if word == flag and argv[i + 1 : i + 3] == [source, target]:
            return i
    raise AssertionError(f"no {flag} {source} {target} in {argv}")


def test_only_the_install_step_can_reach_the_network_or_the_package_cache():
    install = sandbox.build_step_argv("install", command=["/bin/true"], **ROOTS)
    bundle = sandbox.build_step_argv(
        "bundle", bundle_dir="/data/lessons/demo", command=["/bin/true"], **ROOTS
    )
    assert "--share-net" in install and "--share-net" not in bundle
    assert (sandbox.BUN_CACHE_DIR, sandbox.BUN_CACHE_DIR) in _pairs(install, "--bind")
    assert sandbox.BUN_CACHE_DIR not in bundle, (
        "the bundler reads node_modules, never the cache; a writable shared "
        "cache in that view would be reach it does not need"
    )


def test_the_bundle_step_sees_the_packages_where_the_agent_sees_them():
    argv = sandbox.build_step_argv(
        "bundle", bundle_dir="/data/lessons/demo", command=["/bin/true"], **ROOTS
    )
    # bun resolves a bare specifier by walking up from the importer file, so
    # the packages have to appear at the bundle path or nothing resolves.
    assert ("/data/lesson-builds/demo/node_modules",
            "/data/lessons/demo/node_modules") in _pairs(argv, "--ro-bind")
    assert ("/data/lessons/demo", "/data/lessons/demo") in _pairs(argv, "--ro-bind"), (
        "the bundle is an input; the artifact is placed by the app once the "
        "size and render gates have passed"
    )


def test_a_build_time_macro_cannot_leave_config_for_the_next_install():
    """`bun build` runs agent-authored code, in a view that must not persist.

    A `with {type: "macro"}` import executes during the bundle. If it could
    write the workspace it would leave a `bunfig.toml`, a `package.json` or a
    lockfile for the NEXT install to read — the one step with the network and a
    writable shared cache. A dependency rewritten to a tarball URL has no
    release age, which is the 30-day quarantine gone.
    """
    workspace = ROOTS["build_workspace"]
    install = sandbox.build_step_argv("install", command=["/bin/true"], **ROOTS)
    bundle = sandbox.build_step_argv(
        "bundle", bundle_dir="/data/lessons/demo", command=["/bin/true"], **ROOTS
    )
    # The step that fills the workspace has it writable; the step that runs
    # somebody else's code does not.
    assert (workspace, workspace) in _pairs(install, "--bind")
    assert (workspace, workspace) not in _pairs(bundle, "--bind")
    assert (workspace, workspace) in _pairs(bundle, "--ro-bind")
    # One writable hole, holding the artifact and nothing bun reads config from.
    out = f"{workspace}/{sandbox.BUILD_OUTPUT_DIR}"
    assert (out, out) in _pairs(bundle, "--bind")
    # …and it is bound AFTER the read-only cover, or it would not be writable.
    assert _mount_at(bundle, "--ro-bind", workspace, workspace) < _mount_at(
        bundle, "--bind", out, out
    )


def test_cwd_is_the_app_owned_workspace_and_never_the_bundle():
    for step, extra in (("install", {}), ("bundle", {"bundle_dir": "/data/lessons/demo"})):
        argv = sandbox.build_step_argv(step, command=["/bin/true"], **ROOTS, **extra)
        assert argv[argv.index("--chdir") + 1] == ROOTS["build_workspace"], (
            "bunfig.toml carries `preload`, which executes a script, and bun "
            "reads it from cwd; the bundle is writable from inside its own "
            "session, so it must never be cwd"
        )


def test_both_views_blank_the_home_the_agent_cannot_read_either():
    for step, extra in (("install", {}), ("bundle", {"bundle_dir": "/data/lessons/demo"})):
        argv = sandbox.build_step_argv(step, command=["/bin/true"], **ROOTS, **extra)
        tmpfs = [argv[i + 1] for i, word in enumerate(argv) if word == "--tmpfs"]
        assert sandbox.USER_HOME in tmpfs, (
            "the entry is agent-authored: against the real home, a text import "
            "of an absolute path would inline an owner-readable file into a "
            "served artifact"
        )
        assert "--clearenv" in argv


def test_neither_step_gets_unbounded_memory_or_an_unbounded_scratch():
    """A timeout bounds wall time and nothing else.

    `bun build` executes an agent-authored macro. Allocating or forking in a
    loop takes the host down well inside a two-minute budget, and this app is
    one worker on that host — so the limits have to be on the cgroup, not on
    the clock.
    """
    for step, extra in (("install", {}), ("bundle", {"bundle_dir": "/data/lessons/demo"})):
        argv = sandbox.build_step_argv(step, command=["/bin/true"], **ROOTS, **extra)
        sized = [(argv[i - 1], argv[i + 1]) for i, word in enumerate(argv)
                 if word == "--tmpfs" and i and argv[i - 2] == "--size"]
        assert (str(sandbox.BUILD_SCRATCH_BYTES), "/tmp") in sized
        assert (str(sandbox.BUILD_HOME_BYTES), sandbox.USER_HOME) in sized

    for prefix, memory, tasks, grace, ceiling, make in (
        (sandbox.build_scope_prefix(120), sandbox.BUILD_MEMORY_MAX,
         sandbox.BUILD_TASKS_MAX, sandbox.BUILD_SCOPE_GRACE_SECONDS,
         sandbox.BUILD_MAX_WALL_SECONDS, sandbox.build_scope_prefix),
        # The gate's browser runs the artifact that build just produced, from
        # the same author, and a browser is a process tree — so it needs the
        # same bounds, torn down as a group.
        (sandbox.render_scope_prefix(60), sandbox.RENDER_MEMORY_MAX,
         sandbox.RENDER_TASKS_MAX, sandbox.RENDER_SCOPE_GRACE_SECONDS,
         sandbox.RENDER_MAX_WALL_SECONDS, sandbox.render_scope_prefix),
    ):
        wall = 120 if make is sandbox.build_scope_prefix else 60
        assert prefix[0] == sandbox.SYSTEMD_RUN and prefix[-1] == "--"
        assert f"--property=MemoryMax={memory}" in prefix
        assert f"--property=TasksMax={tasks}" in prefix
        assert "--property=MemorySwapMax=0" in prefix
        assert "--property=KillMode=control-group" in prefix
        # The scope outlives the caller's own timeout, or work that took its
        # full budget would be killed by the backstop and read as a failure.
        assert f"--property=RuntimeMaxSec={wall + grace}s" in prefix
        for bad in (0, -1, ceiling + 1):
            with pytest.raises(ValueError):
                make(bad)


def test_a_build_view_refuses_a_workspace_the_bundle_could_choose():
    for workspace in ("/data/lessons/demo/node_modules", "/elsewhere/demo", "relative"):
        with pytest.raises(ValueError):
            sandbox.build_step_argv(
                "install", command=["/bin/true"],
                build_workspace=workspace, bundle_root="/data/lessons",
                private_root="/data",
            )
    with pytest.raises(ValueError):
        sandbox.build_step_argv("bundle", command=["/bin/true"], **ROOTS)
    with pytest.raises(ValueError):
        sandbox.build_step_argv(
            "install", bundle_dir="/data/lessons/demo", command=["/bin/true"], **ROOTS
        )
    with pytest.raises(ValueError):
        sandbox.build_step_argv("polish", command=["/bin/true"], **ROOTS)


# --- the render gate ---------------------------------------------------------

_PAGES = {
    "/clean.html": b"<!doctype html><meta charset=utf-8><script src='ok.js'></script><p>hi",
    "/throws.html": b"<!doctype html><meta charset=utf-8><script src='boom.js'></script>",
    "/module.html": b"<!doctype html><meta charset=utf-8><script type=module src='ok.js'></script>",
    "/logged.html": b"<!doctype html><meta charset=utf-8><script src='cerr.js'></script>",
    # Nothing on it references the artifact the gate is asked about.
    "/bare.html": b"<!doctype html><meta charset=utf-8><p>no script here",
    # Both ways to start a worker, on the origin and policy a lesson gets.
    "/w-file.html": b"<!doctype html><meta charset=utf-8><script src='w-file.js'></script>",
    "/w-file.js": (
        b"try{new Worker('worker.js')}"
        b"catch(e){console.error('CTOR '+e.name+': '+e.message)}"
    ),
    "/worker.js": b"null.f();",
    "/w-blob.html": b"<!doctype html><meta charset=utf-8><script src='w-blob.js'></script>",
    "/w-blob.js": (
        b"try{var b=new Blob(['null.f();'],{type:'text/javascript'});"
        b"new Worker(URL.createObjectURL(b))}"
        b"catch(e){console.error('CTOR '+e.name+': '+e.message)}"
    ),
    # A worker that starts, says something is wrong, and is never asked.
    "/w-live.html": b"<!doctype html><meta charset=utf-8><script src='w-live.js'></script>",
    "/w-live.js": (
        b"var b=new Blob([\"console.error('FROM WORKER');postMessage('up')\"],"
        b"{type:'text/javascript'});"
        b"var w=new Worker(URL.createObjectURL(b));"
        b"w.onmessage=function(){document.title='worker up'};"
    ),
    # …and one that throws instead, with nothing on the page listening.
    "/w-throw.html": b"<!doctype html><meta charset=utf-8><script src='w-throw.js'></script>",
    "/w-throw.js": (
        b"var b=new Blob([\"null.f();\"],{type:'text/javascript'});"
        b"new Worker(URL.createObjectURL(b));"
    ),
    # Says something is wrong without saying what.
    "/wordless.html": b"<!doctype html><meta charset=utf-8><script src='wordless.js'></script>",
    "/wordless.js": b"console.error('');",
    # Fetches the file and never runs it: a download is not a run.
    "/preload.html": (
        b"<!doctype html><meta charset=utf-8>"
        b"<link rel=preload as=script href='ok.js'><p>preloaded only"
    ),
    # `slow.js` is answered late, so the document is still loading well past the
    # settle interval; the throw only happens once it arrives.
    "/slow.html": b"<!doctype html><meta charset=utf-8><script src='slow.js'></script>",
    "/ok.js": b"document.title = 'ok';",
    "/boom.js": b"null.f();",
    "/cerr.js": b"console.error('invented lesson failure');",
    "/slow.js": b"null.f();",
    # Errors as fast as the socket carries them, for as long as it is allowed to.
    "/flood.html": b"<!doctype html><meta charset=utf-8><script src='flood.js'></script>",
    "/flood.js": (
        b"for (let i = 0; i < 200000; i++) console.error('flood ' + i);"
    ),
}
# How long `/slow.js` is held back. Longer than the settle interval the test
# passes in, so a check that started settling at navigation would miss it.
_SLOW_SECONDS = 2.0


class _Site:
    """A throwaway server answering with the real lesson response headers."""

    def __init__(self, csp: str | None) -> None:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
                body = _PAGES.get(self.path)
                if self.path == "/slow.js":
                    time.sleep(_SLOW_SECONDS)
                if body is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    return self.end_headers()
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "text/javascript" if self.path.endswith(".js")
                    else "text/html; charset=utf-8",
                )
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                if csp is not None:
                    self.send_header("Content-Security-Policy", csp)
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture(scope="module")
def lesson_csp() -> str:
    from app.routers.learn import _LESSON_PREVIEW_CSP_INTERACTIVE

    return _LESSON_PREVIEW_CSP_INTERACTIVE


@pytest.fixture(scope="module")
def legacy_csp() -> str:
    """The historical permissive policy a v1 bundle still renders under, and
    the build route still accepts v1 bundles."""
    from app.routers.learn import _LESSON_PREVIEW_CSP_LEGACY

    return _LESSON_PREVIEW_CSP_LEGACY


@needs_browser
def test_the_render_gate_reads_a_page_under_the_real_lesson_policy(lesson_csp):
    """One browser, four pages: what passes, and the three ways to fail."""
    site = _Site(lesson_csp)
    try:
        clean = render_check.console_errors(f"{site.base}/clean.html")
        assert clean == [], f"a working page must be silent, got {clean}"

        thrown = render_check.console_errors(f"{site.base}/throws.html")
        assert any(e["source"] == "exception" and "TypeError" in e["text"] for e in thrown)

        logged = render_check.console_errors(f"{site.base}/logged.html")
        assert any(
            e["source"] == "console" and "invented lesson failure" in e["text"]
            for e in logged
        )

        # The regression the IIFE build exists to prevent: an external MODULE
        # script is fetched in cors mode and refused from an opaque origin,
        # and it says so on the browser log, never on the console.
        module = render_check.console_errors(f"{site.base}/module.html")
        assert any(e["source"] == "browser" for e in module), module
        assert any("ok.js" in e["text"] for e in module), module
    finally:
        site.close()


@needs_browser
def test_an_error_with_nothing_to_say_is_still_an_error(lesson_csp):
    """`console.error("")` has no text and every bit as much meaning: the page
    told the browser something went wrong.

    Measured while writing this: a bare `console.error()` with no arguments at
    all never reaches the wire — Chrome emits no `consoleAPICalled` for it — but
    an empty string does, and dropping it for having nothing to read would let
    the zero-console-error gate accept the page that raised it.
    """
    site = _Site(lesson_csp)
    try:
        errors = render_check.console_errors(f"{site.base}/wordless.html")
        assert errors, "the zero-console-error gate cannot pass this page"
        assert any(e["source"] == "console" and e["text"] for e in errors), errors
    finally:
        site.close()


@needs_browser
def test_a_worker_cannot_start_here_and_says_so_where_the_gate_listens(lesson_csp):
    """Why the check does not attach to worker targets: there are none.

    Measured on this host, under the real lesson response. A classic worker is
    a `SecurityError` — the script cannot be reached from origin `null` — and a
    blob worker is refused by `script-src`, which `worker-src` falls back to.
    Both refusals land on channels the gate already reads, so a worker's own
    console is unreachable rather than unwatched.

    Where a worker DOES run — the legacy policy, below — its diagnostics are
    forwarded to the page target the check is attached to, so nothing is
    missed there either.
    """
    site = _Site(lesson_csp)
    try:
        classic = render_check.console_errors(f"{site.base}/w-file.html")
        assert any("SecurityError" in e["text"] for e in classic), classic
        assert any("origin 'null'" in e["text"] for e in classic), classic

        blob = render_check.console_errors(f"{site.base}/w-blob.html")
        assert any(e["source"] == "browser" for e in blob), blob
        assert any("worker" in e["text"].lower() for e in blob), blob
    finally:
        site.close()


@needs_browser
def test_a_worker_that_does_run_is_still_heard(legacy_csp):
    """A v1 bundle renders under the permissive policy, which allows `blob:`
    scripts — so there a worker really does start, and the check is attached to
    the page target only.

    Measured on this host rather than assumed: Chrome forwards a worker's
    console errors and its uncaught exceptions to the page's `Log` domain, with
    the worker's own `blob:` URL in the text. Both fail the gate without the
    check attaching to worker sessions at all.
    """
    site = _Site(legacy_csp)
    try:
        live = render_check.console_errors(f"{site.base}/w-live.html")
        assert any("FROM WORKER" in e["text"] for e in live), live
        assert any("blob:" in e["text"] for e in live), (
            "the worker, not the page, is what said it", live
        )

        threw = render_check.console_errors(f"{site.base}/w-throw.html")
        assert any("TypeError" in e["text"] for e in threw), threw
        assert any("blob:" in e["text"] for e in threw), threw
    finally:
        site.close()


@needs_browser
def test_the_gate_refuses_a_page_that_did_not_come_back_opaque():
    """No sandbox CSP means the run would not see an opaque-origin failure."""
    site = _Site(None)
    try:
        with pytest.raises(render_check.RenderCheckUnavailable) as caught:
            render_check.console_errors(f"{site.base}/clean.html")
        assert "opaque" in str(caught.value)
    finally:
        site.close()


@needs_browser
def test_a_page_that_never_loads_the_artifact_is_no_evidence_about_it(lesson_csp):
    """The gate is about the built script, not about whatever page loads next.

    A page that does not reference the artifact renders perfectly well, so
    without this the acceptance gate would pass on a source that throws on its
    first line — and the learner would meet it the day the `<script>` tag
    arrived.
    """
    site = _Site(lesson_csp)
    try:
        missed = render_check.console_errors(
            f"{site.base}/bare.html", expect_url=f"{site.base}/ok.js"
        )
        assert any(e["source"] == "artifact" for e in missed), missed

        # Fetched and never executed is the same emptiness with a nicer
        # network log; a preload proves nothing about the code.
        preloaded = render_check.console_errors(
            f"{site.base}/preload.html", expect_url=f"{site.base}/ok.js"
        )
        assert any(e["source"] == "artifact" for e in preloaded), preloaded
        assert any("never ran it" in e["text"] for e in preloaded), preloaded

        loaded = render_check.console_errors(
            f"{site.base}/clean.html", expect_url=f"{site.base}/ok.js"
        )
        assert loaded == [], loaded
        # The page's own relative reference and the URL the app composes need
        # not be byte-identical; the file they name is what matters.
        encoded = render_check.console_errors(
            f"{site.base}/clean.html", expect_url=f"{site.base}/%6fk.js"
        )
        assert encoded == [], encoded
    finally:
        site.close()


@needs_browser
def test_the_gate_waits_for_the_page_to_finish_before_it_settles(lesson_csp):
    """`Page.navigate` returns on commit, which is long before done.

    Settling from that moment would judge a page that is still fetching: here
    the throwing script is answered after the settle interval has expired.
    """
    site = _Site(lesson_csp)
    try:
        started = time.monotonic()
        late = render_check.console_errors(
            f"{site.base}/slow.html", settle=_SLOW_SECONDS / 4
        )
        elapsed = time.monotonic() - started
    finally:
        site.close()
    # The property, asserted on time rather than on which channel the failure
    # came through: a gate that settled from `Page.navigate` returns in about a
    # quarter of `_SLOW_SECONDS`, long before the script it is judging exists.
    assert elapsed >= _SLOW_SECONDS, (
        f"the gate stopped listening after {elapsed:.2f}s, before a subresource "
        f"that takes {_SLOW_SECONDS}s had arrived"
    )
    assert late, "the late script throws, and a page that throws is not clean"


@needs_browser
def test_running_out_of_time_is_a_refusal_and_never_a_pass(lesson_csp):
    """A budget too small to finish must not read as "checked, and clean"."""
    site = _Site(lesson_csp)
    try:
        with pytest.raises(render_check.RenderCheckUnavailable):
            render_check.console_errors(f"{site.base}/slow.html", timeout=1.0)
    finally:
        site.close()


@needs_browser
def test_a_page_erroring_in_a_loop_is_bounded_not_absorbed(lesson_csp):
    """A broken page must cost a refusal, not the app's memory.

    Every notification was retained until the deadline and only trimmed to
    `MAX_ERRORS` at the end, so a page looping over `console.error` could grow
    the process for a minute before being told no.
    """
    site = _Site(lesson_csp)
    try:
        errors = render_check.console_errors(f"{site.base}/flood.html", settle=1.0)
        assert errors, "a page shouting errors is not a clean page"
        assert len(errors) <= render_check.MAX_ERRORS + 1
    finally:
        site.close()


def test_the_settle_window_will_not_start_until_the_document_is_done():
    """The same property as the browser test, without a browser in it."""
    browser = render_check._Browser.__new__(render_check._Browser)
    browser.events, browser.dropped = [], 0
    browser.fetched, browser.executed, browser.loaded = set(), set(), False
    # Nothing to read and no load: the wait must end as a refusal, never as a
    # quiet return that lets the caller settle on a half-loaded page.
    browser._read = lambda deadline: (_ for _ in ()).throw(TimeoutError())
    with pytest.raises(render_check.RenderCheckUnavailable):
        browser.wait_for_load(time.monotonic() + 0.05)
    browser.loaded = True
    browser.wait_for_load(time.monotonic() + 0.05)


def test_the_blank_page_the_browser_started_on_does_not_count_as_loaded():
    """The browser opens `about:blank`, in the frame the lesson then uses.

    Its `Page.frameStoppedLoading` arrives after `Page.enable` and carries the
    same frame id, so without a reset at navigation the gate treats the blank
    page's completion as the lesson's and settles on a document that has not
    fetched anything yet.
    """
    browser = render_check._Browser.__new__(render_check._Browser)
    browser.events, browser.dropped = [], 0
    browser.fetched, browser.executed, browser.loaded = set(), set(), False
    browser._main_frame = None
    browser._record({"method": "Page.frameStoppedLoading",
                     "params": {"frameId": "F1"}})
    assert browser.loaded, "about:blank did finish; the flag is not wrong yet"

    browser.begin_navigation("F1")
    assert not browser.loaded, "…but it is not the page under test"
    browser._record({"method": "Page.frameStoppedLoading",
                     "params": {"frameId": "OTHER"}})
    assert not browser.loaded, "another frame finishing is not this one finishing"
    browser._record({"method": "Page.frameStoppedLoading",
                     "params": {"frameId": "F1"}})
    assert browser.loaded


def test_the_events_kept_for_a_report_are_capped(monkeypatch):
    """The cap is on collection, and the signals the gate needs bypass it."""
    browser = render_check._Browser.__new__(render_check._Browser)
    browser.events, browser.dropped = [], 0
    browser.fetched, browser.executed, browser.loaded = set(), set(), False
    for i in range(render_check.MAX_EVENTS * 3):
        browser._record({"method": "Runtime.consoleAPICalled", "params": {
            "type": "error", "args": [{"value": f"flood {i}"}],
        }})
    assert len(browser.events) == render_check.MAX_EVENTS
    assert browser.dropped == render_check.MAX_EVENTS * 2
    # A flood cannot displace the load, fetch and execution signals, because
    # none of them is kept in the channel that has the cap.
    browser._record({"method": "Page.loadEventFired", "params": {}})
    browser._record({"method": "Debugger.scriptParsed",
                     "params": {"url": "http://x/a.js"}})
    browser._record({"method": "Network.responseReceived", "params": {
        "response": {"url": "http://x/a.js", "status": 200}}})
    assert browser.loaded
    assert browser.executed == {"http://x/a.js"} and browser.fetched == {"http://x/a.js"}


def test_a_page_with_many_assets_is_not_charged_with_errors_it_never_had():
    """The cap must count diagnostics, not traffic.

    Every subresource produces several `Network.*` notifications besides the
    one the gate reads. Letting those into the capped channel would make a
    lesson with a hundred honest images report "further errors were not
    collected" — and be refused on every build, for rendering correctly.
    """
    browser = render_check._Browser.__new__(render_check._Browser)
    browser.events, browser.dropped = [], 0
    browser.fetched, browser.executed, browser.loaded = set(), set(), False
    for i in range(render_check.MAX_EVENTS * 3):
        for method in ("Network.requestWillBeSent", "Network.loadingFinished",
                       "Network.dataReceived", "Runtime.executionContextCreated"):
            browser._record({"method": method, "params": {"requestId": str(i)}})
        # A chatty page is not a broken one either. The method is one the gate
        # reads; the subtype is not, and `_errors_from` would throw both away.
        browser._record({"method": "Runtime.consoleAPICalled", "params": {
            "type": "log", "args": [{"value": f"step {i}"}]}})
        browser._record({"method": "Log.entryAdded", "params": {
            "entry": {"level": "info", "text": f"note {i}"}}})
    assert browser.events == [] and browser.dropped == 0


def test_no_browser_is_a_refusal_and_never_a_pass(monkeypatch):
    monkeypatch.setattr(render_check, "chrome_binary", lambda: None)
    with pytest.raises(render_check.RenderCheckUnavailable):
        render_check.console_errors("http://127.0.0.1:1/never.html")


def test_the_apps_own_csp_complaint_is_not_charged_to_the_lesson():
    """`webrtc 'block'` is deliberate and Chrome does not know it; the
    complaint is about the response, and appears identically under every page."""
    noise = [{"method": "Log.entryAdded", "params": {"entry": {
        "level": "error", "url": "http://x/page.html",
        "text": "Unrecognized Content-Security-Policy directive 'webrtc'.",
    }}}, {"method": "Log.entryAdded", "params": {"entry": {
        "level": "error", "url": "http://x/favicon.ico",
        "text": "Failed to load resource: the server responded with a status of 404",
    }}}]
    assert render_check._errors_from(noise) == []
    # A page that says the words itself is still reported.
    mine = [{"method": "Runtime.consoleAPICalled", "params": {
        "type": "error",
        "args": [{"value": "my Unrecognized Content-Security-Policy directive note"}],
    }}]
    assert len(render_check._errors_from(mine)) == 1
    # And so is a policy the PAGE got wrong. `webrtc` is the only directive of
    # `interactive-local-v1` Chrome does not know, so any other unrecognized
    # one came from a `<meta>` tag the lesson wrote — a real defect that a
    # prefix match would have swallowed along with the noise.
    misspelled = [{"method": "Log.entryAdded", "params": {"entry": {
        "level": "error", "url": "http://x/page.html",
        "text": "Unrecognized Content-Security-Policy directive 'scrpit-src'.",
    }}}]
    assert len(render_check._errors_from(misspelled)) == 1


# --- the whole step, on this host --------------------------------------------

@pytest.fixture(scope="module")
def built_lesson(client):
    """A real lesson with a real bundle, on the suite's own data directory.

    Takes `client` for its side effect: the app's startup is what creates the
    schema this writes into."""
    from app.db import get_conn
    from app.services import lessons

    conn = get_conn()
    try:
        lesson_id = lessons.create_lesson(conn, "Invented Build Target")
        lesson = lessons.get_lesson(conn, lesson_id)
    finally:
        conn.close()
    bundle = lessons.lesson_bundle_dir(lesson["slug"])
    (bundle / "src").mkdir(exist_ok=True)
    # The ordinary source every test here builds unless it brings its own.
    (bundle / "src" / "page.ts").write_text(
        'import { select } from "d3";\n'
        'select(document.body).append("p").text("built");\n',
        encoding="utf-8",
    )
    (bundle / lessons.DEFAULT_ENTRY).write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>t</title></head>"
        "<body><script src='assets/page.js'></script></body></html>",
        encoding="utf-8",
    )
    yield lesson, bundle
    shutil.rmtree(lessons.ensure_build_workspace(lesson["slug"]), ignore_errors=True)


def _no_render_errors(monkeypatch, errors=()):
    monkeypatch.setattr(
        render_check, "console_errors", lambda *a, **k: list(errors)
    )


@needs_sandbox
def test_a_real_build_places_one_artifact_and_leaves_the_bundle_clean(
    built_lesson, monkeypatch
):
    import asyncio

    lesson, bundle = built_lesson
    (bundle / "src" / "page.ts").write_text(
        'import { select } from "d3";\n'
        'select(document.body).append("p").text("built");\n',
        encoding="utf-8",
    )
    _no_render_errors(monkeypatch)
    result = asyncio.run(lesson_build.build_lesson(
        lesson, add=["d3"], entry="src/page.ts", out="assets/page.js",
        page=None, page_url="http://127.0.0.1:1/unused",
            artifact_url="http://127.0.0.1:1/unused.js",
    ))
    assert result["ok"] and result["artifact"]["path"] == "assets/page.js"
    assert result["packages"].get("d3"), result["packages"]

    artifact = (bundle / "assets" / "page.js").read_bytes()
    assert artifact.startswith(b"(()"), "an IIFE, not a module"
    assert b"import" not in artifact[:64]
    # The packages are build-time input. They are bound over this name inside
    # the agent's session and must never reach the directory the app serves.
    assert list((bundle / "node_modules").iterdir()) == []

    from app.services import lessons
    workspace = lessons.ensure_build_workspace(lesson["slug"])
    assert (workspace / "node_modules" / "d3").is_dir()
    assert not (workspace / "bunfig.toml").exists(), (
        "no config file beside cwd: every rule that matters is on the argv"
    )


@needs_sandbox
def test_a_stylesheet_import_still_produces_one_file(built_lesson, monkeypatch):
    """`--outfile` cannot hold two outputs, and a lesson cannot fetch the second.

    Measured on bun 1.3.11: an entry importing a `.css` file fails the whole
    build with "Multiple files share the same output path". `node_modules` is
    not served and the page has no way to reach a side file, so the stylesheet
    rides inside the script the page already loads.
    """
    import asyncio

    lesson, bundle = built_lesson
    (bundle / "src" / "styled.css").write_text(
        ".invented-marker{color:rgb(1,2,3)}\n", encoding="utf-8"
    )
    (bundle / "src" / "styled.ts").write_text(
        'import "./styled.css";\ndocument.title = "styled";\n', encoding="utf-8"
    )
    _no_render_errors(monkeypatch)
    result = asyncio.run(lesson_build.build_lesson(
        lesson, add=[], entry="src/styled.ts", out="assets/styled.js",
        page=None, page_url="http://127.0.0.1:1/unused",
        artifact_url="http://127.0.0.1:1/unused.js",
    ))
    assert result["ok"]
    artifact = (bundle / "assets" / "styled.js").read_text(encoding="utf-8")
    assert ".invented-marker" in artifact, "the styles came with the script"
    assert 'document.title = "styled"' in artifact or "styled" in artifact
    assert not (bundle / "assets" / "styled.css").exists(), (
        "one artifact and one classic tag; a side file has no URL the page "
        "can reach"
    )


@needs_sandbox
def test_a_stylesheet_that_is_gone_does_not_haunt_the_next_build(
    built_lesson, monkeypatch
):
    """The bundler writes into a directory now, so stale outputs can survive."""
    import asyncio

    from app.services import lessons

    lesson, bundle = built_lesson
    (bundle / "src" / "plain.ts").write_text(
        'document.title = "plain";\n', encoding="utf-8"
    )
    stale = (lessons.ensure_build_workspace(lesson["slug"])
             / sandbox.BUILD_OUTPUT_DIR / f"{lesson_build._ARTIFACT_STEM}.css")
    stale.write_text(".invented-marker{color:red}\n", encoding="utf-8")
    _no_render_errors(monkeypatch)
    asyncio.run(lesson_build.build_lesson(
        lesson, add=[], entry="src/plain.ts", out="assets/plain.js",
        page=None, page_url="http://127.0.0.1:1/unused",
        artifact_url="http://127.0.0.1:1/unused.js",
    ))
    assert ".invented-marker" not in (
        bundle / "assets" / "plain.js"
    ).read_text(encoding="utf-8")


@needs_sandbox
def test_a_page_too_heavy_for_a_learner_is_refused(built_lesson, monkeypatch):
    import asyncio

    lesson, bundle = built_lesson
    (bundle / "src" / "heavy.ts").write_text(
        'import mermaid from "mermaid";\nconsole.log(mermaid);\n', encoding="utf-8"
    )
    _no_render_errors(monkeypatch)
    with pytest.raises(lesson_build.BuildError) as caught:
        asyncio.run(lesson_build.build_lesson(
            lesson, add=["mermaid"], entry="src/heavy.ts", out="assets/heavy.js",
            page=None, page_url="http://127.0.0.1:1/unused",
            artifact_url="http://127.0.0.1:1/unused.js",
        ))
    assert caught.value.code == "artifact-too-large"
    assert caught.value.fields["bytes"] > lesson_build.ARTIFACT_MAX_BYTES
    assert not (bundle / "assets" / "heavy.js").exists(), (
        "a refused artifact never reaches the bundle"
    )


@needs_sandbox
def test_a_page_that_fails_to_render_puts_the_last_good_artifact_back(
    built_lesson, monkeypatch
):
    import asyncio

    lesson, bundle = built_lesson
    good = (bundle / "assets" / "page.js").read_bytes()
    (bundle / "src" / "broken.ts").write_text("null.f();\n", encoding="utf-8")
    _no_render_errors(monkeypatch, [{"source": "exception", "text": "Uncaught TypeError"}])
    with pytest.raises(lesson_build.BuildError) as caught:
        asyncio.run(lesson_build.build_lesson(
            lesson, add=[], entry="src/broken.ts", out="assets/page.js",
            page=None, page_url="http://127.0.0.1:1/unused",
            artifact_url="http://127.0.0.1:1/unused.js",
        ))
    assert caught.value.code == "render-errors"
    assert (bundle / "assets" / "page.js").read_bytes() == good, (
        "a lesson is never left carrying an artifact the gate rejected"
    )


@needs_sandbox
def test_the_bundler_reads_no_more_of_the_home_than_the_agent_does(built_lesson):
    """An agent-authored entry cannot inline an owner-readable file."""
    import asyncio

    lesson, bundle = built_lesson
    secret = Path(sandbox.USER_HOME) / ".bashrc"
    if not secret.exists():
        pytest.skip("no file in the real home to try reading")
    (bundle / "src" / "leak.ts").write_text(
        f'import s from "{secret}" with {{ type: "text" }};\ndocument.title = s;\n',
        encoding="utf-8",
    )
    with pytest.raises(lesson_build.BuildError) as caught:
        asyncio.run(lesson_build.build_lesson(
            lesson, add=[], entry="src/leak.ts", out="assets/leak.js",
            page=None, page_url="http://127.0.0.1:1/unused",
            artifact_url="http://127.0.0.1:1/unused.js",
        ))
    assert caught.value.code == "bundle-failed"
    assert "could not resolve" in json.dumps(caught.value.fields).lower()


@needs_sandbox
def test_a_bunfig_the_agent_writes_into_the_bundle_is_not_read(
    built_lesson, monkeypatch
):
    """`preload` executes a script, and bun reads bunfig.toml from cwd."""
    import asyncio

    lesson, bundle = built_lesson
    (bundle / "evil.js").write_text(
        "require('fs').writeFileSync('/tmp/ephemeris-preload-ran', 'x');\n",
        encoding="utf-8",
    )
    (bundle / "bunfig.toml").write_text(
        '[install]\nminimumReleaseAge = 0\npreload = ["./evil.js"]\n', encoding="utf-8"
    )
    marker = Path("/tmp/ephemeris-preload-ran")
    marker.unlink(missing_ok=True)
    try:
        _no_render_errors(monkeypatch)
        result = asyncio.run(lesson_build.build_lesson(
            lesson, add=[], entry="src/page.ts", out="assets/page.js",
            page=None, page_url="http://127.0.0.1:1/unused",
            artifact_url="http://127.0.0.1:1/unused.js",
        ))
        assert result["ok"]
        assert not marker.exists(), "cwd is the workspace, so that bunfig is not bun's"
    finally:
        marker.unlink(missing_ok=True)
        (bundle / "bunfig.toml").unlink(missing_ok=True)
        (bundle / "evil.js").unlink(missing_ok=True)


@needs_sandbox
def test_an_authored_file_at_the_backup_name_survives_a_build(
    built_lesson, monkeypatch
):
    """`os.rename` replaces its target, so the set-aside name cannot be guessable.

    A bundle holding its own `assets/.page.js.previous` would otherwise lose it
    to every build of `assets/page.js` — including the ones that succeed, where
    the aside copy is deleted at the end.
    """
    import asyncio

    lesson, bundle = built_lesson
    (bundle / "assets").mkdir(exist_ok=True)
    decoys = {
        # The set-aside name and the write-through name: both are renamed over,
        # and `os.rename` replaces its target without asking.
        bundle / "assets" / ".page.js.previous": "authored, not a backup\n",
        bundle / "assets" / ".page.js.new": "authored, not a scratch file\n",
    }
    for path, text in decoys.items():
        path.write_text(text, encoding="utf-8")
    _no_render_errors(monkeypatch)
    result = asyncio.run(lesson_build.build_lesson(
        lesson, add=[], entry="src/page.ts", out="assets/page.js",
        page=None, page_url="http://127.0.0.1:1/unused",
        artifact_url="http://127.0.0.1:1/unused.js",
    ))
    assert result["ok"]
    for path, text in decoys.items():
        assert path.read_text(encoding="utf-8") == text, f"{path.name} was eaten"
    assert sorted(p.name for p in (bundle / "assets").iterdir()
                  if "page.js" in p.name) == [
        ".page.js.new", ".page.js.previous", "page.js"
    ], "no leftovers from the set-aside dance"


def test_the_walk_to_the_output_never_leaves_the_bundle(tmp_path):
    """The last line of defence for the output path.

    `_linked_paths` refuses a bundle holding a link before the build starts, so
    end to end this never runs — but the two checks are seconds apart and the
    lesson's own session keeps writing throughout. Whatever appears in between,
    the descriptor chain still refuses to follow it.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    bundle = tmp_path / "bundle"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "assets" / "elsewhere").symlink_to(outside)

    with pytest.raises(OSError):
        lesson_build._bundle_fd(bundle, "assets/elsewhere/page.js")
    assert list(outside.iterdir()) == []

    fd, name = lesson_build._bundle_fd(bundle, "assets/here/page.js")
    try:
        assert name == "page.js"
        assert os.path.samestat(os.fstat(fd), os.stat(bundle / "assets" / "here"))
    finally:
        os.close(fd)


@pytest.mark.parametrize("target", ["/etc/hostname", "page.ts"])
def test_a_symlinked_entry_is_missing_rather_than_a_fault(
    built_lesson, monkeypatch, target
):
    """§2: a symlinked path is missing, and the check comes BEFORE the resolve.

    Both directions matter. A link out of the bundle is caught by the resolver;
    a link to a sibling INSIDE it resolves perfectly well, and would be
    compiled by a check that only watched for an escape.
    """
    import asyncio

    lesson, bundle = built_lesson
    link = bundle / "src" / "linked.ts"
    link.symlink_to(target)
    try:
        with pytest.raises(lesson_build.BuildError) as caught:
            asyncio.run(lesson_build.build_lesson(
                lesson, add=[], entry="src/linked.ts", out="assets/linked.js",
                page=None, page_url="http://127.0.0.1:1/unused",
                artifact_url="http://127.0.0.1:1/unused.js",
            ))
        assert caught.value.code == "no-entry" and caught.value.status == 404
    finally:
        link.unlink()


@needs_sandbox
def test_a_link_the_entry_could_import_stops_the_build(built_lesson, monkeypatch):
    """§2 covers the whole bundle, and the bundler honours none of it.

    The entry itself is checked, but the entry is only the first file read: an
    `import "./helper.ts"` is resolved by the bundler exactly like any other
    path, link and all. So the target of a link nobody serves would end up
    compiled into a page this app does serve.
    """
    import asyncio

    lesson, bundle = built_lesson
    link = bundle / "src" / "helper.ts"
    link.symlink_to("/etc/hostname")
    try:
        _no_render_errors(monkeypatch)
        with pytest.raises(lesson_build.BuildError) as caught:
            asyncio.run(lesson_build.build_lesson(
                lesson, add=[], entry="src/page.ts", out="assets/page.js",
                page=None, page_url="http://127.0.0.1:1/unused",
                artifact_url="http://127.0.0.1:1/unused.js",
            ))
        assert caught.value.code == "linked-source" and caught.value.status == 409
        assert "src/helper.ts" in caught.value.detail
    finally:
        link.unlink()


def test_the_mount_this_app_makes_is_not_read_as_bundle_content(built_lesson):
    """`node_modules` is a bind of the workspace, and a package manager fills
    it with `.bin` links by design — refusing those would refuse every build."""
    from app.services import lessons

    lesson, bundle = built_lesson
    mount = bundle / sandbox.BUILD_WORKSPACE_MOUNT
    mount.mkdir(exist_ok=True)
    (mount / ".bin").mkdir(exist_ok=True)
    shim = mount / ".bin" / "invented-shim"
    shim.symlink_to("../invented/cli.js")
    try:
        assert lesson_build._linked_paths(lessons.lesson_bundle_dir(lesson["slug"])) == []
    finally:
        shim.unlink()


def test_an_artifact_the_bundler_did_not_write_is_never_read(tmp_path):
    """A build-time macro can put a link where the bundle is about to appear.

    Bun follows its own `--outfile` link and still exits 0. Pointed at
    `/dev/zero` the file reports size zero and then reads forever, on the
    thread the app answers requests with — so the size check has to happen on
    a descriptor that followed no link, not on the name.
    """
    outfile = tmp_path / "artifact.js"
    outfile.symlink_to("/dev/zero")
    with pytest.raises(lesson_build.BuildError) as caught:
        lesson_build._read_artifact(outfile, [])
    assert caught.value.code == "bundle-failed" and caught.value.status == 422

    outfile.unlink()
    outfile.symlink_to(tmp_path)
    with pytest.raises(lesson_build.BuildError) as caught:
        lesson_build._read_artifact(outfile, [])
    assert caught.value.code == "bundle-failed"

    outfile.unlink()
    outfile.write_bytes(b"ok()")
    assert lesson_build._read_artifact(outfile, []) == b"ok()"


@needs_sandbox
def test_a_directory_standing_where_the_artifact_goes_is_a_refusal(
    built_lesson, monkeypatch
):
    """`os.rename` would happily move an authored `assets/page.js/` tree aside.

    The build would then report success while the tree sat under a random
    hidden name and every URL under it 404'd — a silent loss, which is the one
    outcome the set-aside dance exists to prevent.
    """
    import asyncio

    lesson, bundle = built_lesson
    # Its own source, importing nothing: this test is about the placement, and
    # a bundle step that needs a package would only fail earlier and elsewhere.
    (bundle / "src" / "plain.ts").write_text(
        'document.title = "placed";\n', encoding="utf-8"
    )
    tree = bundle / "assets" / "tree.js"
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "kept.txt").write_text("authored", encoding="utf-8")
    try:
        _no_render_errors(monkeypatch)
        with pytest.raises(lesson_build.BuildError) as caught:
            asyncio.run(lesson_build.build_lesson(
                lesson, add=[], entry="src/plain.ts", out="assets/tree.js",
                page=None, page_url="http://127.0.0.1:1/unused",
                artifact_url="http://127.0.0.1:1/unused.js",
            ))
        assert caught.value.code == "invalid-out" and caught.value.status == 409
        assert (tree / "kept.txt").read_text(encoding="utf-8") == "authored"
        assert [p.name for p in tree.parent.iterdir()
                if "tree.js" in p.name] == ["tree.js"], "nothing was moved aside"
    finally:
        shutil.rmtree(tree, ignore_errors=True)


@needs_sandbox
def test_an_output_the_build_itself_read_is_refused(built_lesson, monkeypatch):
    """`entry == out` is the obvious way to ask for this, and not the only one.

    The entry may import `../assets/helper.js` while the request names that same
    file as `out`. Everything downstream succeeds — bun reads both, the page
    renders — and the placement then writes the whole bundle over the helper and
    deletes the copy it set aside, because a passing render is what tells it the
    aside copy is no longer needed. Authored work gone, response `ok`.
    """
    import asyncio

    lesson, bundle = built_lesson
    (bundle / "assets").mkdir(exist_ok=True)
    helper = bundle / "assets" / "helper.js"
    helper.write_text('export const h = () => "authored";\n', encoding="utf-8")
    (bundle / "src" / "uses-helper.ts").write_text(
        'import { h } from "../assets/helper.js";\ndocument.title = h();\n',
        encoding="utf-8",
    )
    try:
        _no_render_errors(monkeypatch)
        with pytest.raises(lesson_build.BuildError) as caught:
            asyncio.run(lesson_build.build_lesson(
                lesson, add=[], entry="src/uses-helper.ts", out="assets/helper.js",
                page=None, page_url="http://127.0.0.1:1/unused",
                artifact_url="http://127.0.0.1:1/unused.js",
            ))
        assert caught.value.code == "out-is-source", caught.value.code
        assert caught.value.status == 409
        assert helper.read_text(encoding="utf-8") == 'export const h = () => "authored";\n'
        assert [p.name for p in helper.parent.iterdir()
                if "helper.js" in p.name] == ["helper.js"], "nothing was set aside"
    finally:
        helper.unlink(missing_ok=True)


@needs_sandbox
def test_a_build_that_read_nothing_of_the_kind_still_places_its_artifact(
    built_lesson, monkeypatch
):
    """The graph check is a refusal, not a tax: an ordinary build still lands,
    and the bundler's report does not survive as a stray output."""
    import asyncio

    from app.services import lessons

    lesson, bundle = built_lesson
    (bundle / "src" / "graph-ok.ts").write_text(
        'document.title = "graph ok";\n', encoding="utf-8"
    )
    _no_render_errors(monkeypatch)
    result = asyncio.run(lesson_build.build_lesson(
        lesson, add=[], entry="src/graph-ok.ts", out="assets/graph-ok.js",
        page=None, page_url="http://127.0.0.1:1/unused",
        artifact_url="http://127.0.0.1:1/unused.js",
    ))
    assert result["ok"]
    assert (bundle / "assets" / "graph-ok.js").is_file()
    outdir = lessons.ensure_build_workspace(lesson["slug"]) / "out"
    assert lesson_build._GRAPH_NAME not in os.listdir(outdir)


def test_a_graph_that_cannot_be_read_stops_the_build(tmp_path):
    """Fail closed. Without the graph there is no way to tell whether the
    output path is one of the files the artifact was compiled from, and the
    check exists precisely because that case ends in lost work."""
    outdir = tmp_path / "out"
    outdir.mkdir()
    with pytest.raises(lesson_build.BuildError) as missing:
        lesson_build._graph_sources(outdir, tmp_path, [])
    assert missing.value.code == "bundle-failed"

    (outdir / lesson_build._GRAPH_NAME).write_text("not json", encoding="utf-8")
    with pytest.raises(lesson_build.BuildError) as malformed:
        lesson_build._graph_sources(outdir, tmp_path, [])
    assert malformed.value.code == "bundle-failed"

    # Read, then removed: it is not one of the outputs the page is composed of.
    (outdir / lesson_build._GRAPH_NAME).write_text(
        json.dumps({"inputs": {
            "../bundle/src/page.ts": {"imports": [
                {"path": "/data/lessons/x/assets/helper.js"},
            ]},
            "node_modules/d3/index.js": {},
        }}),
        encoding="utf-8",
    )
    sources = lesson_build._graph_sources(outdir, tmp_path, [])
    assert str(tmp_path.parent / "bundle" / "src" / "page.ts") in sources
    assert "/data/lessons/x/assets/helper.js" in sources
    assert str(tmp_path / "node_modules" / "d3" / "index.js") in sources
    assert not (outdir / lesson_build._GRAPH_NAME).exists()


def test_a_page_over_the_ceiling_is_measured_whole(tmp_path):
    """The refusal names the size of the page that was asked for.

    The stylesheet is read against what the script has already spent, so a
    900 KiB script and a 200 KiB stylesheet must not be refused as a
    "204800-byte" page that is somehow over a 1 MiB ceiling — a number the
    agent cannot act on, attached to the wrong file.
    """
    outdir = tmp_path / "out"
    outdir.mkdir()
    script = 900 * 1024
    styles = 200 * 1024
    (outdir / f"{lesson_build._ARTIFACT_STEM}.js").write_bytes(b"x" * script)
    (outdir / f"{lesson_build._ARTIFACT_STEM}.css").write_bytes(b"y" * styles)
    with pytest.raises(lesson_build.BuildError) as caught:
        lesson_build._compose_artifact(outdir, [])
    assert caught.value.code == "artifact-too-large"
    assert caught.value.fields["bytes"] == script + styles
    assert str(script + styles) in caught.value.detail


def test_a_name_no_filesystem_can_carry_is_a_bad_request(client, built_lesson):
    """A JSON body may hold a lone surrogate: `"assets/\\ud800.js"` is a
    perfectly ordinary Python `str` that neither `os.open` nor
    `urllib.parse.quote` can encode. Refused here, or it becomes an
    unstructured 500 several layers away."""
    lesson, _bundle = built_lesson
    url = f"/learn/lessons/{lesson['id']}/build"
    for field, body in (
        ("out", {"entry": "src/page.ts", "out": "assets/\ud800.js"}),
        ("entry", {"entry": "src/\ud800.ts", "out": "assets/page.js"}),
    ):
        response = client.post(url, content=json.dumps(body).encode("utf-8"),
                               headers={"Content-Type": "application/json"})
        assert response.status_code == 400, (field, response.status_code)
        assert response.json()["error"] == "invalid-request", (field, response.json())


@needs_sandbox
def test_an_output_directory_that_refuses_the_write_is_a_refusal(
    built_lesson, monkeypatch
):
    """`chmod 0555 assets` passes the descriptor walk and fails the write."""
    import asyncio

    lesson, bundle = built_lesson
    assets = bundle / "assets"
    assets.mkdir(exist_ok=True)
    before = assets.stat().st_mode
    assets.chmod(0o555)
    try:
        _no_render_errors(monkeypatch)
        with pytest.raises(lesson_build.BuildError) as caught:
            asyncio.run(lesson_build.build_lesson(
                lesson, add=[], entry="src/page.ts", out="assets/page.js",
                page=None, page_url="http://127.0.0.1:1/unused",
                artifact_url="http://127.0.0.1:1/unused.js",
            ))
        assert caught.value.code == "invalid-out" and caught.value.status == 409
    finally:
        assets.chmod(before)


@needs_sandbox
def test_a_rebuild_moves_the_page_the_learner_is_watching(built_lesson, monkeypatch):
    """The preview token is the PAGE's mtime, and only the script changed.

    Every consumer — the route's `?v` check, the metadata, the bridge poll —
    derives from `lessons.lesson_file_info`'s `version`. Replacing the artifact
    changes none of its inputs, so an open iframe would keep running the code
    this build just replaced until somebody reloaded by hand.
    """
    import asyncio

    from app.services import lessons

    lesson, bundle = built_lesson
    page = lessons.DEFAULT_ENTRY
    before = lessons.lesson_file_info(lesson, page)["version"]
    _no_render_errors(monkeypatch)
    result = asyncio.run(lesson_build.build_lesson(
        lesson, add=[], entry="src/page.ts", out="assets/page.js",
        page=page, page_url="http://127.0.0.1:1/unused",
        artifact_url="http://127.0.0.1:1/unused.js",
    ))
    assert result["ok"]
    assert lessons.lesson_file_info(lesson, page)["version"] != before, (
        "a rebuild the learner cannot see is a rebuild that did not happen"
    )


@needs_sandbox
def test_build_time_code_cannot_write_the_config_the_next_install_reads(
    built_lesson, monkeypatch
):
    """The P1, end to end: a macro runs during `bun build`, and must not persist.

    Whether this particular macro compiles is bun's business; what is asserted
    is the property underneath it — after any bundle step, the files the next
    install reads are exactly the ones the app wrote.
    """
    import asyncio

    lesson, bundle = built_lesson
    from app.services import lessons

    workspace = lessons.ensure_build_workspace(lesson["slug"])
    (bundle / "src" / "macro.ts").write_text(
        'import { plant } from "./plant.ts" with { type: "macro" };\n'
        'export const x = plant();\n',
        encoding="utf-8",
    )
    (bundle / "src" / "plant.ts").write_text(
        "import { writeFileSync } from 'node:fs';\n"
        "export function plant() {\n"
        "  try {\n"
        "    writeFileSync('bunfig.toml', '[install]\\nminimumReleaseAge = 0\\n');\n"
        "  } catch (e) { return 'blocked'; }\n"
        "  return 'planted';\n"
        "}\n",
        encoding="utf-8",
    )
    _no_render_errors(monkeypatch)
    try:
        asyncio.run(lesson_build.build_lesson(
            lesson, add=[], entry="src/macro.ts", out="assets/macro.js",
            page=None, page_url="http://127.0.0.1:1/unused",
            artifact_url="http://127.0.0.1:1/unused.js",
        ))
    except lesson_build.BuildError:
        pass  # refusing to compile it at all is also a pass
    assert not (workspace / "bunfig.toml").exists(), (
        "a build-time macro reached the config the next install reads, in the "
        "one view that has the network and a writable shared package cache"
    )
    assert json.loads((workspace / "package.json").read_text())["name"] == (
        f"lesson-{lesson['slug']}"
    ), "the workspace manifest is the app's, and stays the app's"


def test_a_host_without_the_package_manager_says_so(built_lesson, monkeypatch):
    """bwrap fails at mount setup for a missing bind source, and a nonzero exit
    during setup is indistinguishable from a package that would not resolve."""
    import asyncio

    lesson, _bundle = built_lesson
    monkeypatch.setattr(sandbox, "BUN_BINARY", "/nonexistent/bin/bun")
    with pytest.raises(lesson_build.BuildError) as caught:
        asyncio.run(lesson_build.build_lesson(
            lesson, add=[], entry="src/page.ts", out="assets/page.js",
            page=None, page_url="http://127.0.0.1:1/unused",
            artifact_url="http://127.0.0.1:1/unused.js",
        ))
    assert caught.value.code == "build-unavailable" and caught.value.status == 503


def test_a_host_that_cannot_sandbox_refuses_in_the_documented_shape(
    built_lesson, monkeypatch
):
    """No bubblewrap is a 503 the agent can read, not a 500 it cannot."""
    import asyncio

    lesson, _bundle = built_lesson

    def no_runtime() -> None:
        raise sandbox.SandboxUnavailableError("invented: no user namespaces here")

    monkeypatch.setattr(sandbox, "require_sandbox_runtime", no_runtime)
    with pytest.raises(lesson_build.BuildError) as caught:
        asyncio.run(lesson_build.build_lesson(
            lesson, add=[], entry="src/page.ts", out="assets/page.js",
            page=None, page_url="http://127.0.0.1:1/unused",
            artifact_url="http://127.0.0.1:1/unused.js",
        ))
    assert caught.value.code == "build-unavailable" and caught.value.status == 503


# --- the route ---------------------------------------------------------------

def test_the_build_route_admits_only_a_bounded_json_object(client, built_lesson):
    lesson, _bundle = built_lesson
    url = f"/learn/lessons/{lesson['id']}/build"
    body = {"entry": "src/page.ts", "out": "assets/page.js"}

    assert client.post(url, content=b"{}", headers={"Content-Type": "text/plain"}
                       ).status_code == 415
    assert client.post(url, content=b"not json",
                       headers={"Content-Type": "application/json"}).status_code == 400
    assert client.post(url, content=b'"a string"',
                       headers={"Content-Type": "application/json"}).status_code == 400
    from app.routers.learn import _BUILD_MAX_BODY
    assert client.post(url, content=b"x" * (_BUILD_MAX_BODY + 1),
                       headers={"Content-Type": "application/json"}).status_code == 413
    assert client.post("/learn/lessons/999999/build", json=body).status_code == 404
    assert client.post(url, json={"out": "assets/page.js"}).status_code == 400
    assert client.post(url, json={**body, "add": "d3"}).status_code == 400
    assert client.post(url, json={**body, "page": "no/such.html"}).status_code == 404


def test_the_page_to_render_is_the_page_asked_for_and_not_a_near_spelling(
    client, built_lesson
):
    """`./index.html` is a fallback, not a synonym.

    A v2 bundle matches declared pages exactly (§4.1), so the resolver refuses
    that spelling and quietly hands back the manifest entry instead. When the
    entry happens to be `index.html` the two end up equal after normalization,
    and comparing normalized names would let the build report a render of a
    page the bundle declined to serve under the name in the request.
    """
    lesson, _bundle = built_lesson
    from app.services import lessons

    assert lessons.lesson_file_info(lesson, "./index.html")["entry"] == (
        lessons.DEFAULT_ENTRY
    ), "the fallback is what makes this worth guarding"
    response = client.post(
        f"/learn/lessons/{lesson['id']}/build",
        json={"entry": "src/page.ts", "out": "assets/page.js",
              "page": "./index.html"},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "no-page"


def test_a_page_that_is_not_a_name_is_refused_rather_than_ignored(
    client, built_lesson
):
    """Asking for a page and getting a different one is the failure here.

    `page` is the evidence the build is accepted on. A number or a list is not a
    page name, and treating it like an absent field would render the bundle's
    default page and report that render as proof for the request — so the
    request is refused instead.
    """
    lesson, _bundle = built_lesson
    url = f"/learn/lessons/{lesson['id']}/build"
    body = {"entry": "src/page.ts", "out": "assets/page.js"}
    for page in (7, ["index.html"], {"ref": "index.html"}, True):
        response = client.post(url, json={**body, "page": page})
        assert response.status_code == 400, (page, response.json())
        assert response.json()["error"] == "invalid-request", (page, response.json())


def test_a_v1_bundle_keeps_the_spelling_it_always_accepted(client, built_lesson):
    """The exactness above is v2's rule, and v1 predates it.

    `_resolve_entry` normalizes for v1 on purpose — historical tolerance the
    preview still relies on. Holding a v1 bundle to v2's comparison would
    refuse a page the same app renders happily everywhere else.
    """
    from app.db import get_conn
    from app.services import bundle_schema, lessons

    lesson, _bundle = built_lesson
    assert lessons.read_bundle_readonly(lesson).version == bundle_schema.SCHEMA_V2
    assert lessons.selected_page_ref(lesson, "./index.html") == "./index.html"

    conn = get_conn()
    try:
        legacy_id = lessons.create_lesson(conn, "Invented Legacy Bundle")
        legacy = lessons.get_lesson(conn, legacy_id)
    finally:
        conn.close()
    legacy_dir = lessons.lesson_bundle_dir(legacy["slug"])
    (legacy_dir / lessons.DEFAULT_ENTRY).write_text(
        "<!doctype html><title>t</title>", encoding="utf-8"
    )
    (legacy_dir / "lesson.json").write_text(
        json.dumps({"entry": lessons.DEFAULT_ENTRY}), encoding="utf-8"
    )
    assert lessons.read_bundle_readonly(legacy).version == bundle_schema.SCHEMA_V1
    assert lessons.selected_page_ref(legacy, "./index.html") == "index.html"
    with pytest.raises(lessons.LessonError):
        lessons.selected_page_ref(legacy, "../outside.html")
    # …and the route agrees: the same selection that is a fallback under v2 is
    # this bundle's own page under v1, so the build gets past the gate.
    response = client.post(
        f"/learn/lessons/{legacy_id}/build",
        json={"entry": "src/page.ts", "out": "assets/page.js",
              "page": "./index.html"},
    )
    assert response.json().get("error") != "no-page", response.json()


def test_a_sandboxed_lesson_page_cannot_ask_for_its_own_rebuild(client, built_lesson):
    """The perimeter policy every unsafe method passes: an opaque origin is
    refused, so a lesson page cannot drive the package manager."""
    lesson, _bundle = built_lesson
    response = client.post(
        f"/learn/lessons/{lesson['id']}/build",
        json={"entry": "src/page.ts", "out": "assets/page.js"},
        headers={"Origin": "null"},
    )
    assert response.status_code == 403
