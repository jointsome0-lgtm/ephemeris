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
import shutil
import threading
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
    argv = lesson_build._bundle_argv(Path("/b/src/page.ts"), Path("/w/artifact.js"))
    # The opaque origin a lesson renders on blocks an external module script,
    # an import map and a dynamic import; a classic `<script src>` it allows.
    assert "--format=iife" in argv
    assert "--target=browser" in argv
    assert "--outfile=/w/artifact.js" in argv


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
    for bad in ("../outside.ts", "page.html", "page.css"):
        with pytest.raises(lesson_build.BuildError):
            lesson_build.clean_source_ref(bad, "entry")


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
            "/data/lessons/demo/node_modules") in _pairs(argv, "--bind")
    assert ("/data/lessons/demo", "/data/lessons/demo") in _pairs(argv, "--ro-bind"), (
        "the bundle is an input; the artifact is placed by the app once the "
        "size and render gates have passed"
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
    "/ok.js": b"document.title = 'ok';",
    "/boom.js": b"null.f();",
    "/cerr.js": b"console.error('invented lesson failure');",
}


class _Site:
    """A throwaway server answering with the real lesson response headers."""

    def __init__(self, csp: str | None) -> None:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
                body = _PAGES.get(self.path)
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
def test_the_gate_refuses_a_page_that_did_not_come_back_opaque():
    """No sandbox CSP means the run would not see an opaque-origin failure."""
    site = _Site(None)
    try:
        with pytest.raises(render_check.RenderCheckUnavailable) as caught:
            render_check.console_errors(f"{site.base}/clean.html")
        assert "opaque" in str(caught.value)
    finally:
        site.close()


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
        ))
        assert result["ok"]
        assert not marker.exists(), "cwd is the workspace, so that bunfig is not bun's"
    finally:
        marker.unlink(missing_ok=True)
        (bundle / "bunfig.toml").unlink(missing_ok=True)
        (bundle / "evil.js").unlink(missing_ok=True)


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
