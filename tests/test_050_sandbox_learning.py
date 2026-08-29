"""Sandbox, lesson-agent, capability, and record-panel verification."""
from __future__ import annotations

import hashlib
import importlib.abc as _importlib_abc
import json
import os
import sqlite3
import stat as stat_module
import subprocess
import sys
import tempfile
import threading
from datetime import date as _vdate
from pathlib import Path

from conftest import ROOT, events_of, item_row



def test_sandbox_learning(client, suite_state):
    from app.db import SCHEMA_VERSION, get_conn, pretty_date, today_str
    from app.main import app

    c = client
    _asyncio = suite_state["_asyncio"]
    _lt = suite_state["_lt"]
    _lt_id = suite_state["_lt_id"]
    _pty = suite_state["_pty"]
    _terminal = suite_state["_terminal"]
    _types = suite_state["_types"]
    _uuid4 = suite_state["_uuid4"]
    assess_svc = suite_state["assess_svc"]
    attempts_svc = suite_state["attempts_svc"]
    bschema = suite_state["bschema"]
    lessons_svc = suite_state["lessons_svc"]
    ws_info = suite_state["ws_info"]
    # --- E1: pure sandbox profiles + cached probe + no-fallback spawn seam ----
    from app import sandbox as _sandbox
    from app.services import bundle_schema as _bundle_schema
    from unittest import mock as _sandbox_mock

    # sandbox.py imports `resource` at the point of use (issue #25), so the rlimit
    # checks below patch the stdlib module itself — the same object the function's
    # local import resolves to.
    import resource as _resource_mod

    # Issue #182: the mounts below are built from the resolved home.
    _sb_userhome = _sandbox.USER_HOME
    _sb_root = "/tmp/ephemeris-e1-verify"
    _sb_bundle = f"{_sb_root}/invented-bundle"
    _sb_agent = _sandbox.build_sandbox_argv(
        "lesson-agent", _sb_bundle, bundle_root=_sb_root)
    _sb_learner = _sandbox.build_sandbox_argv(
        "lesson-learner", _sb_bundle, bundle_root=_sb_root)
    _sb_runner = _sandbox.build_sandbox_argv(
        "lesson-runner", _sb_bundle, bundle_root=_sb_root,
        private_root="/tmp", module_cache_fd=7)

    def _sb_mounts(argv, flag):
        return [(argv[i + 1], argv[i + 2]) for i, arg in enumerate(argv)
                if arg == flag]

    assert (
        all(argv[0] == _sandbox.BWRAP
          and "--unshare-all" in argv
          and "--die-with-parent" in argv
          and ("/", "/") in _sb_mounts(argv, "--ro-bind")
          and ["--proc", "/proc"] == argv[argv.index("--proc"):argv.index("--proc") + 2]
          and ["--dev", "/dev"] == argv[argv.index("--dev"):argv.index("--dev") + 2]
          and argv.count("--tmpfs") >= 2
          and "/tmp" in [argv[i + 1] for i, x in enumerate(argv) if x == "--tmpfs"]
          and _sb_userhome in [argv[i + 1] for i, x in enumerate(argv) if x == "--tmpfs"]
          for argv in (_sb_agent, _sb_learner, _sb_runner))
    ), (
        "E1 argv: every profile has the namespace/base-fs/die-with-parent contract"
    )
    assert (
        "--share-net" in _sb_agent
        and "--share-net" in _sb_learner
        and "--share-net" not in _sb_runner
    ), (
        "E1 argv: both interactive shells share the host network; the runner does not"
    )

    _sb_agent_try_ro = {
        (f"{_sb_userhome}/.nvm/versions", f"{_sb_userhome}/.nvm/versions"),
        (f"{_sb_userhome}/.local/share/claude/versions", f"{_sb_userhome}/.local/share/claude/versions"),
        (f"{_sb_userhome}/.codex/auth.json", f"{_sb_userhome}/.codex/auth.json"),
        (f"{_sb_userhome}/.codex/config.toml", f"{_sb_userhome}/.codex/config.toml"),
        (f"{_sb_userhome}/.claude/settings.json", f"{_sb_userhome}/.claude/settings.json"),
        (f"{_sb_userhome}/.claude.json", f"{_sb_userhome}/.claude.json"),
    }
    _sb_common_try_ro = {
        (f"{_sb_userhome}/.local/bin", f"{_sb_userhome}/.local/bin"),
    }
    assert (
        set(_sb_mounts(_sb_agent, "--ro-bind")) == {("/", "/")}
        and set(_sb_mounts(_sb_agent, "--ro-bind-try"))
        == _sb_agent_try_ro | _sb_common_try_ro
        and set(_sb_mounts(_sb_agent, "--bind-try")) == {
          (f"{_sb_userhome}/go", f"{_sb_userhome}/go"),
          (f"{_sb_userhome}/.cache/go-build", f"{_sb_userhome}/.cache/go-build"),
        }
        and _sb_mounts(_sb_agent, "--bind") == [(_sb_bundle, _sb_bundle)]
        and {f"{_sb_userhome}/.codex", f"{_sb_userhome}/.claude"}.issubset(
          {_sb_agent[i + 1] for i, x in enumerate(_sb_agent) if x == "--tmpfs"})
    ), (
        "E1 argv: lesson-agent exact home binds and ephemeral CLI state"
    )

    # A persistent agent home swaps exactly the two ephemeral state directories
    # for binds of its own subdirectories, so `claude --continue` / `codex
    # resume` still find the last conversation after the PTY is gone. Every
    # other mount except the Codex config is untouched, and the blank $HOME
    # above them stays blank. The persistent config must remain writable so
    # Codex can save this lesson's workspace-trust decision.
    _sb_home = "/tmp/agent-homes/some-lesson"
    _sb_agent_persist = _sandbox.build_sandbox_argv(
        "lesson-agent", _sb_bundle, bundle_root=_sb_root,
        private_root="/tmp", agent_home=_sb_home)
    _sb_agent_persist_try_ro = _sb_agent_try_ro - {
        (f"{_sb_userhome}/.codex/config.toml", f"{_sb_userhome}/.codex/config.toml")
    }
    assert (
        set(_sb_mounts(_sb_agent_persist, "--bind")) == {
          (_sb_bundle, _sb_bundle),
          (f"{_sb_home}/claude", f"{_sb_userhome}/.claude"),
          (f"{_sb_home}/codex", f"{_sb_userhome}/.codex"),
        }
        and set(_sb_mounts(_sb_agent_persist, "--ro-bind-try"))
        == _sb_agent_persist_try_ro | _sb_common_try_ro
        and {_sb_agent_persist[i + 1]
             for i, x in enumerate(_sb_agent_persist) if x == "--tmpfs"} == {
          "/tmp", _sb_userhome,
        }
        # The persistent binds land where the tmpfs entries were. Login material
        # stays read-only; the per-lesson config remains writable for trust.
        and all(
          _sb_agent_persist.index(f"{_sb_home}/{name}")
          < _sb_agent_persist.index(f"{_sb_userhome}/.{cli}/{leaf}")
          for name, cli, leaf in (
            ("codex", "codex", "auth.json"),
          )
        )
        # The host's rotating Claude login material never enters the profile;
        # the tutor authenticates via CLAUDE_CODE_OAUTH_TOKEN instead (#188).
        and not any(
          ".credentials.json" in arg for arg in _sb_agent + _sb_agent_persist
        )
    ), "E1 argv: a persistent agent home replaces only the ephemeral state dirs"
    assert (
        set(_AGENT_TMPFS_TARGETS := {
          m.target for m in _sandbox._AGENT_HOME_MOUNTS if m.flag == "--tmpfs"
        }) == set(_sandbox.AGENT_STATE_SUBDIRS)
    ), (
        "E1 argv: every ephemeral agent state dir has a persistent counterpart "
        "— a new tmpfs added without one would silently stay amnesiac"
    )
    for _sb_bad_home, _sb_bad_private, _sb_bad_why in (
        (_sb_home, None, "no private root to answer to"),
        ("relative/home", "/tmp", "not absolute"),
        ("/tmp/../etc", "/tmp", "escapes via '..'"),
        ("/tmp", "/tmp", "is the private root itself"),
        ("/var/elsewhere", "/tmp", "outside the private root"),
        (f"{_sb_bundle}/home", "/tmp", "inside the writable bundle root"),
    ):
        try:
            _sandbox.build_sandbox_argv(
                "lesson-agent", _sb_bundle, bundle_root=_sb_root,
                private_root=_sb_bad_private, agent_home=_sb_bad_home)
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"E1 argv: agent home {_sb_bad_why} must be refused")
    for _sb_other in ("lesson-learner", "lesson-runner"):
        try:
            _sandbox.build_sandbox_argv(
                _sb_other, _sb_bundle, bundle_root=_sb_root,
                private_root="/tmp", agent_home=_sb_home,
                **({"module_cache_fd": 7} if _sb_other == "lesson-runner" else {}))
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"E1 argv: {_sb_other} must refuse a persistent agent home")

    # The build workspace: packages the agent installs, bound over a name
    # INSIDE the bundle so the agent works in an ordinary project layout while
    # the bundle on disk stays empty of them (#161).
    _sb_build = "/tmp/lesson-builds/some-lesson"
    _sb_agent_build = _sandbox.build_sandbox_argv(
        "lesson-agent", _sb_bundle, bundle_root=_sb_root,
        private_root="/tmp", build_workspace=_sb_build)
    _sb_mount = _sandbox.BUILD_WORKSPACE_MOUNT
    assert (
        set(_sb_mounts(_sb_agent_build, "--bind")) == {
          (_sb_bundle, _sb_bundle),
          (f"{_sb_build}/{_sb_mount}", f"{_sb_bundle}/{_sb_mount}"),
        }
        # After the bundle bind, or the bundle would shadow it; before the
        # --chdir that ends the prefix.
        and _sb_agent_build.index(f"{_sb_build}/{_sb_mount}")
        > _sb_agent_build.index(_sb_bundle)
        and _sb_agent_build[-2:] == ["--chdir", _sb_bundle]
        # A place for packages is not a package cache. Nothing here makes one
        # writable: a shared cache with a hardlinking backend would let an edit
        # in one lesson reach every other, which the mount cannot prevent and
        # must not be read as preventing. That arrives with the build step.
        and f"{_sandbox.USER_HOME}/.bun" not in _sb_agent_build
    ), "E1 argv: the build workspace lands under the bundle, after it"
    assert not any(_sb_mount in arg for arg in _sb_agent), (
        "E1 argv: no build workspace, no mount — the bundle keeps its own name"
    )
    assert _sb_mount in _bundle_schema.RESERVED_NAMES, (
        "E1 argv: the mount point must be reserved, or a page could claim it"
    )
    for _sb_bad_build, _sb_bad_private, _sb_bad_why in (
        (_sb_build, None, "no private root to answer to"),
        ("relative/build", "/tmp", "not absolute"),
        ("/tmp/../etc", "/tmp", "escapes via '..'"),
        ("/tmp", "/tmp", "is the private root itself"),
        ("/var/elsewhere", "/tmp", "outside the private root"),
        (f"{_sb_bundle}/build", "/tmp", "inside the writable bundle root"),
    ):
        try:
            _sandbox.build_sandbox_argv(
                "lesson-agent", _sb_bundle, bundle_root=_sb_root,
                private_root=_sb_bad_private, build_workspace=_sb_bad_build)
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"E1 argv: build workspace {_sb_bad_why} must be refused")
    for _sb_other in ("lesson-learner", "lesson-runner"):
        try:
            _sandbox.build_sandbox_argv(
                _sb_other, _sb_bundle, bundle_root=_sb_root,
                private_root="/tmp", build_workspace=_sb_build,
                **({"module_cache_fd": 7} if _sb_other == "lesson-runner" else {}))
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"E1 argv: {_sb_other} must refuse a build workspace")
    assert (
        set(_sb_mounts(_sb_learner, "--ro-bind")) == {("/", "/")}
        and set(_sb_mounts(_sb_learner, "--ro-bind-try")) == _sb_common_try_ro | {
          (f"{_sb_userhome}/go", f"{_sb_userhome}/go"),
          (f"{_sb_userhome}/.cache/go-build", f"{_sb_userhome}/.cache/go-build"),
        }
        and _sb_mounts(_sb_learner, "--bind") == [(_sb_bundle, _sb_bundle)]
        and _sb_learner[-2:] == ["--chdir", _sb_bundle]
    ), (
        "E1 argv: lesson-learner exact ro caches + rw bundle"
    )
    assert (
        set(_sb_mounts(_sb_runner, "--ro-bind")) == {
          ("/", "/"),
          (_sb_bundle, _sb_bundle),
        }
        and _sb_mounts(_sb_runner, "--ro-bind-fd") == [
          ("7", f"{_sb_userhome}/go/pkg/mod")
        ]
        and not _sb_mounts(_sb_runner, "--bind")
        and _sb_runner[-4:] == ["--dir", _sandbox.RUNNER_WORKDIR,
                              "--chdir", _sandbox.RUNNER_WORKDIR]
    ), (
        "E1 argv: lesson-runner ro bundle + isolated tmpfs cwd"
    )
    try:
        _sandbox.build_sandbox_argv("plain", _sb_bundle, bundle_root=_sb_root)
        _sb_bad_profile = False
    except ValueError:
        _sb_bad_profile = True
    try:
        _sandbox.build_sandbox_argv(
            "lesson-agent", "relative/bundle", bundle_root=_sb_root)
        _sb_bad_path = False
    except ValueError:
        _sb_bad_path = True
    _sb_boundary_rejections = []
    for _bad_bundle, _bad_root in (
        ("/", _sb_root),
        (_sb_root, _sb_root),
        ("/tmp/invented-outside", _sb_root),
        (_sb_bundle, "/"),
    ):
        try:
            _sandbox.build_sandbox_argv(
                "lesson-agent", _bad_bundle, bundle_root=_bad_root)
            _sb_boundary_rejections.append(False)
        except ValueError:
            _sb_boundary_rejections.append(True)
    assert (
        _sb_bad_profile and _sb_bad_path and all(_sb_boundary_rejections)
    ), (
        "E1 argv builder rejects unknown profiles and unsafe bundle authorities"
    )

    # Discovery short-circuits the probe, so these blocks stub it as resolved:
    # a mocked probe must not need a host bubblewrap to run.
    _sb_resolved = lambda: _sandbox_mock.patch.object(  # noqa: E731
        _sandbox, "_BWRAP_UNUSABLE", "")

    _sandbox._cached_runtime_probe.cache_clear()
    _sb_probe_ok = _types.SimpleNamespace(returncode=0, stderr="")
    with _sb_resolved(), \
            _sandbox_mock.patch.object(_sandbox.subprocess, "run", return_value=_sb_probe_ok) as _run:
        _sandbox.require_sandbox_runtime()
        _sandbox.require_sandbox_runtime()
    assert (
        _run.call_count == 1
        and _run.call_args.args[0] == [
          _sandbox.BWRAP, "--unshare-user", "--die-with-parent",
          "--ro-bind", "/", "/", "true",
        ]
    ), (
        "E1 runtime probe: exact command succeeds once and is process-cached"
    )

    # Discovery (issue #182), written to run on a host with no bubblewrap.
    _sb_rejected_missing = _sandbox._bwrap_rejection("/invented/nowhere/bwrap")
    _sb_rejected_wrong = _sandbox._bwrap_rejection(sys.executable)
    with _sandbox_mock.patch.object(
            _sandbox, "_bwrap_rejection",
            side_effect=lambda path: "" if path == "/second/bwrap"
            else "invented rejection"), \
            _sandbox_mock.patch.object(
                _sandbox, "_BWRAP_CANDIDATES",
                ("/first/bwrap", "/second/bwrap")):
        _sb_fell_through = _sandbox._resolve_bwrap()
    with _sandbox_mock.patch.object(
            _sandbox, "_BWRAP_CANDIDATES",
            ("/invented/nowhere/bwrap", sys.executable)):
        _sb_chosen, _sb_reason = _sandbox._resolve_bwrap()
    assert (
        _sb_rejected_missing == "not an executable file"
        and _sb_rejected_wrong.startswith("does not accept ")
        # An unusable first candidate must not hide a usable second one.
        and _sb_fell_through == ("/second/bwrap", "")
        # Nothing usable: the preferred path is kept so the pure argv builders
        # stay total, and every candidate's reason reaches the refusal.
        and _sb_chosen == "/invented/nowhere/bwrap"
        and _sb_reason.startswith("no usable bubblewrap")
        and "/invented/nowhere/bwrap: not an executable file" in _sb_reason
        and f"{sys.executable}: does not accept " in _sb_reason
    ), (
        "E1 bwrap discovery: ordered candidates, no $PATH, reasoned refusal"
    )

    # The setuid gate: CVE-2026-41163 is a defect of setuid mode only, so the
    # version floor applies only there. Driven with a stub that answers both
    # queries — the kernel ignores the setuid bit on a script, but the bit is
    # what is read here, so this needs no real bubblewrap and no privilege.
    def _sb_fake_bwrap(directory, version, setuid):
        path = Path(directory) / "bwrap"
        path.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            f'  --help) echo "{" ".join(_sandbox._REQUIRED_BWRAP_OPTIONS)}" ;;\n'
            f'  --version) echo "bubblewrap {version}" ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        path.chmod(0o4755 if setuid else 0o755)
        return str(path)

    _sb_gate = {}
    with tempfile.TemporaryDirectory(prefix="ephemeris-bwrap-gate-") as _sb_gate_dir:
        for _sb_label, _sb_version, _sb_setuid in (
            ("plain-old", "0.9.0", False),
            ("setuid-old", "0.9.0", True),
            ("setuid-exact", "0.11.2", True),
            ("setuid-newer", "0.12.0", True),
            ("setuid-unknown", "unreleased", True),
        ):
            _sb_case_dir = Path(_sb_gate_dir) / _sb_label
            _sb_case_dir.mkdir()
            _sb_gate[_sb_label] = _sandbox._bwrap_rejection(
                _sb_fake_bwrap(_sb_case_dir, _sb_version, _sb_setuid))
    assert (
        # Not setuid: the option vocabulary is the whole requirement.
        _sb_gate["plain-old"] == ""
        # Setuid below the fix: refused, and the reason names the version.
        and "0.9.0" in _sb_gate["setuid-old"]
        and "CVE-2026-41163" in _sb_gate["setuid-old"]
        # The floor itself and above it pass; an unreadable version does not.
        and _sb_gate["setuid-exact"] == ""
        and _sb_gate["setuid-newer"] == ""
        and "does not report a version" in _sb_gate["setuid-unknown"]
    ), (
        "E1 bwrap discovery: the version floor applies to setuid installs only"
    )

    # That refusal is what the probe reports, without spawning anything.
    _sandbox._cached_runtime_probe.cache_clear()
    with _sandbox_mock.patch.object(
            _sandbox, "_BWRAP_UNUSABLE", "invented discovery failure"), \
            _sandbox_mock.patch.object(_sandbox.subprocess, "run") as _no_run:
        try:
            _sandbox.require_sandbox_runtime()
            _sb_unusable_refused = False
        except _sandbox.SandboxUnavailableError as exc:
            _sb_unusable_refused = "invented discovery failure" in str(exc)
    _sandbox._cached_runtime_probe.cache_clear()
    assert _sb_unusable_refused and _no_run.call_count == 0, (
        "E1 runtime probe: an unresolvable bwrap refuses before any spawn"
    )

    async def _sb_no_fallback_contract():
        results = {}
        _sandbox._cached_runtime_probe.cache_clear()
        failed = _types.SimpleNamespace(returncode=1, stderr="userns denied")
        with _sb_resolved(), \
                _sandbox_mock.patch.object(_sandbox.subprocess, "run", return_value=failed), \
                _sandbox_mock.patch.object(_sandbox.asyncio, "create_subprocess_exec") as spawn:
            for _ in range(2):
                try:
                    await _sandbox.spawn_sandboxed(
                        "lesson-agent", _sb_bundle, ["/bin/bash", "-i"],
                        bundle_root=_sb_root, env={})
                except _sandbox.SandboxUnavailableError as exc:
                    results["probe_visible"] = "userns denied" in str(exc)
            results["probe_cached"] = _sandbox.subprocess.run.call_count == 1
            results["probe_never_spawned"] = spawn.call_count == 0

        _sandbox._cached_runtime_probe.cache_clear()
        with _sb_resolved(), \
                _sandbox_mock.patch.object(
                    _sandbox.subprocess, "run", return_value=_sb_probe_ok), \
                _sandbox_mock.patch.object(
                    _sandbox.asyncio, "create_subprocess_exec",
                    side_effect=OSError("exec refused")) as spawn:
            try:
                await _sandbox.spawn_sandboxed(
                    "lesson-agent", _sb_bundle, ["/bin/bash", "-i"],
                    bundle_root=_sb_root, env={})
            except _sandbox.SandboxSpawnError as exc:
                results["spawn_visible"] = "exec refused" in str(exc)
            results["only_bwrap_attempted"] = (
                spawn.call_count == 1 and spawn.call_args.args[0] == _sandbox.BWRAP
            )
        _sandbox._cached_runtime_probe.cache_clear()
        return results

    _sb_fail = _asyncio.run(_sb_no_fallback_contract())
    assert (
        _sb_fail.get("probe_visible") and _sb_fail.get("probe_cached")
        and _sb_fail.get("probe_never_spawned")
    ), (
        "E1 no-fallback: failed cached probe visibly refuses before spawn"
    )
    assert (
        _sb_fail.get("spawn_visible") and _sb_fail.get("only_bwrap_attempted")
    ), (
        "E1 no-fallback: bwrap spawn failure is visible, never a bare command retry"
    )
    try:
        _sandbox.spawn_sandboxed(
            "lesson-agent", _sb_bundle, ["/bin/true"], bundle_root=_sb_root)
        _sb_env_required = False
    except TypeError:
        _sb_env_required = True
    assert (
        set(_sandbox._GENEROUS_LIMITS) == {"RLIMIT_NOFILE", "RLIMIT_NPROC"}
        and _sandbox.profile_preexec_fn("lesson-agent") is not None
        and _sandbox.profile_preexec_fn("lesson-runner") is not None
        and _sb_env_required
    ), (
        "E1 rlimits and env: PTY caps hooked, explicit child env required"
    )

    # --- E2: lesson-agent is server-owned, sandboxed, immutable, fail-closed ---
    class _E2Sock:
        def __init__(self, query, scope=None):
            self.query_params = query
            # A real WebSocket always carries the ASGI scope; `server` is the
            # local end of the accepted socket, which is where the s3 capability
            # URL comes from (never the client's Host header).
            self.scope = {"server": ("127.0.0.1", 8765), "scheme": "ws"}
            if scope is not None:
                self.scope = scope
            self.sent_text = []
            self.sent_bytes = []
            self.accepted = False
            self.closed = False

        async def accept(self):
            self.accepted = True

        async def close(self, code=None):
            self.closed = True

        async def send_text(self, data):
            self.sent_text.append(data)

        async def send_bytes(self, data):
            self.sent_bytes.append(data)

        async def receive(self):
            return {"type": "websocket.disconnect"}

    async def _e2_contract():
        results = {}
        workspace = {"dir": ws_info["dir"], "slug": _lt["slug"], "title": "demo"}
        proc = _types.SimpleNamespace(returncode=0)

        # A lesson parameter is classified server-side and reaches only E1's
        # lesson-agent launcher with the lesson root as the bind authority.
        with _sandbox_mock.patch.object(
                _terminal, "prepare_terminal_workspace", return_value=workspace), \
                _sandbox_mock.patch.object(
                    _terminal, "_detect_proxy_env", return_value={
                        "HTTP_PROXY": "http://127.0.0.1:10809",
                    }) as proxy_detect, \
                _sandbox_mock.patch.object(
                    _terminal, "spawn_sandboxed",
                    new=_sandbox_mock.AsyncMock(return_value=proc)) as sandbox_spawn, \
                _sandbox_mock.patch.object(
                    _terminal.asyncio, "create_subprocess_exec",
                    new=_sandbox_mock.AsyncMock()) as bare_spawn, \
                _sandbox_mock.patch.object(_terminal._TermSession, "start"):
            lesson_sess = await _terminal._create_session(_lt["slug"])
        spawn_args = sandbox_spawn.call_args
        results["lesson_launcher"] = (
            lesson_sess is not None
            and lesson_sess.role == "lesson-agent"
            and lesson_sess.workspace == workspace["dir"]
            and lesson_sess.sandbox_profile == "lesson-agent"
            and proxy_detect.call_args.args == ("lesson-agent",)
            and bare_spawn.call_count == 0
            and spawn_args.args[:3] == (
                "lesson-agent", workspace["dir"],
                [os.environ.get("SHELL") or "/bin/bash", "-i"],
            )
            and spawn_args.kwargs["bundle_root"] == str(lessons_svc.LESSONS_DIR)
            and spawn_args.kwargs["private_root"]
                == str(lessons_svc.LESSONS_DIR.parent)
            and spawn_args.kwargs["private_masks"] == ()
            and spawn_args.kwargs["preexec_fn"].__qualname__.startswith(
                "_child_setup_for")
            and spawn_args.kwargs["env"]["HTTP_PROXY"]
                == "http://127.0.0.1:10809"
        )
        _terminal._SESSIONS.pop(lesson_sess.sid, None)
        os.close(lesson_sess.master_fd)

        # Both E1 failure classes become the terminal's visible lesson refusal,
        # and the direct subprocess path is never attempted as fallback.
        refusal_kinds = []
        fallback_calls = 0
        for failure in (
            _sandbox.SandboxUnavailableError("probe denied"),
            _sandbox.SandboxSpawnError("bwrap exec denied"),
        ):
            with _sandbox_mock.patch.object(
                    _terminal, "prepare_terminal_workspace", return_value=workspace), \
                    _sandbox_mock.patch.object(
                        _terminal, "_detect_proxy_env", return_value={}), \
                    _sandbox_mock.patch.object(
                        _terminal, "spawn_sandboxed",
                        new=_sandbox_mock.AsyncMock(side_effect=failure)), \
                    _sandbox_mock.patch.object(
                        _terminal.asyncio, "create_subprocess_exec",
                        new=_sandbox_mock.AsyncMock()) as direct:
                try:
                    await _terminal._create_session(_lt["slug"])
                except _terminal._LessonSandboxError:
                    refusal_kinds.append(type(failure))
                fallback_calls += direct.call_count
        refusal_ws = _E2Sock({"lesson": _lt["slug"]})
        with _sandbox_mock.patch.object(_terminal, "_ws_is_trusted", return_value=True), \
                _sandbox_mock.patch.object(_terminal, "_reap_idle"), \
                _sandbox_mock.patch.object(_terminal, "_ensure_reaper"), \
                _sandbox_mock.patch.object(
                    _terminal, "_create_session",
                    new=_sandbox_mock.AsyncMock(
                        side_effect=_terminal._LessonSandboxError(_lt["slug"]))):
            await _terminal._serve_ws(refusal_ws)
        results["sandbox_refusal"] = (
            refusal_kinds == [
                _sandbox.SandboxUnavailableError, _sandbox.SandboxSpawnError,
            ]
            and fallback_calls == 0
            and refusal_ws.accepted and refusal_ws.closed
            and b"refusing to open an unsandboxed shell" in b"".join(
                refusal_ws.sent_bytes)
        )

        # No lesson parameter keeps the owner's existing bare repo shell.
        with _sandbox_mock.patch.object(
                _terminal, "_detect_proxy_env", return_value={}) as proxy_detect, \
                _sandbox_mock.patch.object(
                    _terminal, "spawn_sandboxed",
                    new=_sandbox_mock.AsyncMock()) as sandbox_spawn, \
                _sandbox_mock.patch.object(
                    _terminal.asyncio, "create_subprocess_exec",
                    new=_sandbox_mock.AsyncMock(return_value=proc)) as bare_spawn, \
                _sandbox_mock.patch.object(_terminal._TermSession, "start"):
            plain_sess = await _terminal._create_session()
        plain_call = bare_spawn.call_args
        results["plain_unchanged"] = (
            plain_sess is not None
            and plain_sess.role == "plain"
            and plain_sess.workspace == str(_terminal._REPO_ROOT)
            and plain_sess.sandbox_profile is None
            and proxy_detect.call_args.args == ("plain",)
            and sandbox_spawn.call_count == 0
            and plain_call.args == (os.environ.get("SHELL") or "/bin/bash", "-i")
            and plain_call.kwargs["cwd"] == str(_terminal._REPO_ROOT)
            and plain_call.kwargs["preexec_fn"].__qualname__.startswith(
                "_child_setup_for")
        )
        _terminal._SESSIONS.pop(plain_sess.sid, None)
        os.close(plain_sess.master_fd)

        # Attach with conflicting query data uses the live SID wholesale and
        # reports its stored role; the creation-time properties have no setters.
        attach_master, attach_slave = _pty.openpty()
        attach_sess = _terminal._TermSession(
            "verify-e2-attach", proc, attach_master,
            role="lesson-agent", workspace=workspace["dir"],
            sandbox_profile="lesson-agent")
        _terminal._SESSIONS[attach_sess.sid] = attach_sess
        attach_ws = _E2Sock({
            "sid": attach_sess.sid,
            "lesson": "conflicting-lesson",
        })
        immutable = True
        for attr, value in (
            ("role", "plain"), ("workspace", str(ROOT)),
            ("sandbox_profile", None),
        ):
            try:
                setattr(attach_sess, attr, value)
                immutable = False
            except AttributeError:
                pass
        before = (
            attach_sess.role, attach_sess.workspace, attach_sess.sandbox_profile,
        )
        with _sandbox_mock.patch.object(_terminal, "_ws_is_trusted", return_value=True), \
                _sandbox_mock.patch.object(_terminal, "_reap_idle"), \
                _sandbox_mock.patch.object(_terminal, "_ensure_reaper"), \
                _sandbox_mock.patch.object(_terminal, "_set_winsize"), \
                _sandbox_mock.patch.object(
                    _terminal, "_create_session",
                    new=_sandbox_mock.AsyncMock()) as create_again:
            await _terminal._serve_ws(attach_ws)
        handshake = json.loads(attach_ws.sent_text[0])
        results["attach_immutable"] = (
            immutable and create_again.call_count == 0
            and before == (
                attach_sess.role, attach_sess.workspace,
                attach_sess.sandbox_profile,
            )
            and handshake == {
                "type": "session", "sid": attach_sess.sid,
                "role": "lesson-agent",
            }
        )
        _terminal._SESSIONS.pop(attach_sess.sid, None)
        os.close(attach_master)
        os.close(attach_slave)
        return results

    _e2 = _asyncio.run(_e2_contract())
    assert (
        _e2.get("lesson_launcher")
    ), (
        "E2 lesson create uses only the lesson-agent sandbox launcher"
    )
    assert (
        _e2.get("sandbox_refusal")
    ), (
        "E2 probe/bwrap failures visibly refuse with no bare-shell fallback"
    )
    assert (
        _e2.get("plain_unchanged")
    ), (
        "E2 plain create stays unsandboxed in the repository"
    )
    assert (
        _e2.get("attach_immutable")
    ), (
        "E2 attach preserves immutable role/workspace/profile and reports role"
    )

    with _sandbox_mock.patch.dict(
            os.environ,
            {"EPHEMERIS_TERM_PROXY": "http://127.0.0.1:19091"}):
        _proxy_plain = _terminal._detect_proxy_env("plain")
        _proxy_agent = _terminal._detect_proxy_env("lesson-agent")
        _proxy_learner = _terminal._detect_proxy_env("lesson-learner")
    with _sandbox_mock.patch.dict(
            os.environ, {"EPHEMERIS_TERM_PROXY": "off"}):
        _proxy_off = (
            _terminal._detect_proxy_env("plain"),
            _terminal._detect_proxy_env("lesson-agent"),
        )
    assert (
        _proxy_plain.get("HTTP_PROXY") == "http://127.0.0.1:19091"
        and _proxy_agent.get("HTTPS_PROXY") == "http://127.0.0.1:19091"
        and _proxy_learner == _proxy_agent
        and _proxy_off == ({}, {})
    ), (
        "E2 proxy env reaches every host-network role (learner matches agent) "
        "and honors override-off"
    )
    # A proxied child must still reach this app directly: the s3 capability URL
    # is a loopback address, and an inherited proxy can arrive with no NO_PROXY
    # at all (or one that never mentions loopback).
    with _sandbox_mock.patch.dict(
            os.environ, {"HTTP_PROXY": "http://proxy.invalid:3128"}, clear=True):
        _proxy_inherited = _terminal._detect_proxy_env("lesson-agent")
    with _sandbox_mock.patch.dict(
            os.environ,
            {"HTTP_PROXY": "http://proxy.invalid:3128",
             # both spellings, deliberately different: neither list may be lost
             "NO_PROXY": "example.invalid", "no_proxy": "lower.invalid"},
            clear=True):
        _proxy_kept = _terminal._detect_proxy_env("lesson-agent")
    _proxy_none = _terminal._with_loopback_direct({})
    assert (
        _proxy_inherited["HTTP_PROXY"] == "http://proxy.invalid:3128"
        and set(_proxy_inherited["NO_PROXY"].split(","))
          == {"localhost", "127.0.0.1", "::1"}
        and _proxy_inherited["no_proxy"] == _proxy_inherited["NO_PROXY"]
        # an existing exclusion list is extended, never replaced
        and _proxy_kept["NO_PROXY"].split(",")
          == ["example.invalid", "lower.invalid",
              "localhost", "127.0.0.1", "::1"]
        and _proxy_kept["HTTP_PROXY"] == "http://proxy.invalid:3128"
        # the composed sets already spell it out, and a proxy-less child is
        # left exactly as it was
        and _proxy_plain["NO_PROXY"] == _terminal._NO_PROXY
        and _proxy_none == {}
    ), (
        "an inherited proxy never swallows this app's own loopback address"
    )

    # --- S3: the assessment write capability (S-DESIGN D-S1-3 / D-S2-2) ------
    _S3_VARS = {"EPHEMERIS_ASSESS_URL", "EPHEMERIS_ASSESS_TOKEN"}

    async def _s3_capability_contract():
        results = {}
        workspace = {
            "dir": ws_info["dir"], "slug": _lt["slug"], "title": "demo",
            "id": _lt_id, "uid": _lt["uid"],
        }
        proc = _types.SimpleNamespace(returncode=0)

        async def _sandboxed(role_selector, base_url, prepare=True):
            resolver = ("prepare_terminal_workspace" if prepare
                        else "resolve_terminal_workspace")
            with _sandbox_mock.patch.object(
                    _terminal, resolver, return_value=workspace), \
                    _sandbox_mock.patch.object(
                        _terminal, "_detect_proxy_env", return_value={}), \
                    _sandbox_mock.patch.object(
                        _terminal, "spawn_sandboxed",
                        new=_sandbox_mock.AsyncMock(return_value=proc)) as spawn, \
                    _sandbox_mock.patch.object(_terminal._TermSession, "start"):
                sess = await _terminal._create_session(
                    _lt["slug"], role_selector, base_url=base_url)
            return sess, spawn.call_args.kwargs["env"]

        agent, agent_env = await _sandboxed(None, "http://127.0.0.1:8765")
        token = agent_env.get("EPHEMERIS_ASSESS_TOKEN", "")
        capability = _terminal.resolve_assessment_capability(token)
        results["agent_capability"] = (
            agent_env.get("EPHEMERIS_ASSESS_URL")
            == f"http://127.0.0.1:8765/learn/lessons/{_lt_id}/assessments"
            and len(token) >= 32
            and capability is not None
            and capability["lesson_id"] == _lt_id
            and capability["lesson_uid"] == _lt["uid"]
            and capability["sitting_id"] == agent.sid
        )
        # The accessor hands out a copy: a consumer cannot edit the registry.
        capability["lesson_id"] = -1
        results["registry_copy"] = (
            _terminal.resolve_assessment_capability(token)["lesson_id"] == _lt_id
        )
        await agent.close()
        results["revoked_with_session"] = (
            _terminal.resolve_assessment_capability(token) is None
            and token not in _terminal._ASSESS_CAPABILITIES
        )

        # A second agent session on the same lesson is a second sitting.
        first, first_env = await _sandboxed(None, "http://127.0.0.1:8765")
        second, second_env = await _sandboxed(None, "http://127.0.0.1:8765")
        results["distinct_sittings"] = (
            first_env["EPHEMERIS_ASSESS_TOKEN"]
            != second_env["EPHEMERIS_ASSESS_TOKEN"]
            and first_env["EPHEMERIS_ASSESS_URL"]
            == second_env["EPHEMERIS_ASSESS_URL"]
            and _terminal.resolve_assessment_capability(
                first_env["EPHEMERIS_ASSESS_TOKEN"])["sitting_id"] == first.sid
            and _terminal.resolve_assessment_capability(
                second_env["EPHEMERIS_ASSESS_TOKEN"])["sitting_id"] == second.sid
        )
        # Closing one sitting leaves the other's capability alive.
        await first.close()
        results["revocation_is_per_session"] = (
            _terminal.resolve_assessment_capability(
                first_env["EPHEMERIS_ASSESS_TOKEN"]) is None
            and _terminal.resolve_assessment_capability(
                second_env["EPHEMERIS_ASSESS_TOKEN"]) is not None
        )
        await second.close()

        learner, learner_env = await _sandboxed(
            "lesson-learner", "http://127.0.0.1:8765", prepare=False)
        results["learner_gets_nothing"] = not (_S3_VARS & set(learner_env))
        await learner.close()

        # --- #188: Claude auth is a long-lived token variable, agent-only ----
        results["no_token_file_no_var"] = (
            "CLAUDE_CODE_OAUTH_TOKEN" not in agent_env
            and "CLAUDE_CODE_OAUTH_TOKEN" not in learner_env
        )
        with tempfile.TemporaryDirectory() as _tok_dir:
            _tok_file = Path(_tok_dir) / "claude-token"
            _tok_file.write_text("demo-long-lived-token\n", encoding="utf-8")
            with _sandbox_mock.patch.object(
                    _terminal, "_CLAUDE_TOKEN_FILE", _tok_file):
                tok_agent, tok_agent_env = await _sandboxed(
                    None, "http://127.0.0.1:8765")
                tok_learner, tok_learner_env = await _sandboxed(
                    "lesson-learner", "http://127.0.0.1:8765", prepare=False)
        results["token_env_agent_only"] = (
            tok_agent_env.get("CLAUDE_CODE_OAUTH_TOKEN")
            == "demo-long-lived-token"
            and "CLAUDE_CODE_OAUTH_TOKEN" not in tok_learner_env
        )
        await tok_agent.close()
        await tok_learner.close()

        with _sandbox_mock.patch.object(
                _terminal, "_detect_proxy_env", return_value={}), \
                _sandbox_mock.patch.object(
                    _terminal.asyncio, "create_subprocess_exec",
                    new=_sandbox_mock.AsyncMock(return_value=proc)) as bare, \
                _sandbox_mock.patch.object(_terminal._TermSession, "start"):
            plain = await _terminal._create_session(
                base_url="http://127.0.0.1:8765")
        results["plain_gets_nothing"] = not (
            _S3_VARS & set(bare.call_args.kwargs["env"]))
        await plain.close()

        # No spellable app address: neither variable, rather than a token the
        # agent has no URL to use.
        blind, blind_env = await _sandboxed(None, None)
        results["no_url_no_token"] = (
            not (_S3_VARS & set(blind_env))
            and blind._assess_token is None
            and _terminal._ASSESS_CAPABILITIES == {}
        )
        await blind.close()

        # The child can reach the endpoint from a startup file, before this
        # coroutine resumes: the capability must already resolve while the spawn
        # is still in flight, or a young capability would read as a dead one.
        _during = {}

        async def _spawn_and_probe(*a, **kw):
            _during["resolved"] = _terminal.resolve_assessment_capability(
                kw["env"]["EPHEMERIS_ASSESS_TOKEN"])
            return proc

        with _sandbox_mock.patch.object(
                _terminal, "prepare_terminal_workspace", return_value=workspace), \
                _sandbox_mock.patch.object(
                    _terminal, "_detect_proxy_env", return_value={}), \
                _sandbox_mock.patch.object(
                    _terminal, "spawn_sandboxed", new=_spawn_and_probe), \
                _sandbox_mock.patch.object(_terminal._TermSession, "start"):
            early = await _terminal._create_session(
                _lt["slug"], None, base_url="http://127.0.0.1:8765")
        results["live_during_spawn"] = (
            _during.get("resolved") is not None
            and _during["resolved"]["sitting_id"] == early.sid
            and _during["resolved"]["lesson_id"] == _lt_id
        )
        await early.close()
        results["live_during_spawn_revoked"] = (
            _terminal._ASSESS_CAPABILITIES == {})

        # Every other way a session ends revokes it too: the idle reaper's
        # forced eviction and the lifespan shutdown both run close().
        reaped, reaped_env = await _sandboxed(None, "http://127.0.0.1:8765")
        reaped.detached_at = (
            _terminal.time.monotonic() - (_terminal._SESSION_TTL + 60))
        _terminal._reap_idle()
        await _asyncio.sleep(0)  # close() is scheduled as a task
        killed, killed_env = await _sandboxed(None, "http://127.0.0.1:8765")
        await _terminal.shutdown_terminal()
        results["revoked_on_reap_and_shutdown"] = (
            _terminal.resolve_assessment_capability(
                reaped_env["EPHEMERIS_ASSESS_TOKEN"]) is None
            and _terminal.resolve_assessment_capability(
                killed_env["EPHEMERIS_ASSESS_TOKEN"]) is None
            and _terminal._ASSESS_CAPABILITIES == {}
        )

        # A failed spawn registers nothing: no session, no live token.
        with _sandbox_mock.patch.object(
                _terminal, "prepare_terminal_workspace", return_value=workspace), \
                _sandbox_mock.patch.object(
                    _terminal, "_detect_proxy_env", return_value={}), \
                _sandbox_mock.patch.object(
                    _terminal, "spawn_sandboxed",
                    new=_sandbox_mock.AsyncMock(
                        side_effect=_sandbox.SandboxSpawnError("denied"))):
            try:
                await _terminal._create_session(
                    _lt["slug"], None, base_url="http://127.0.0.1:8765")
            except _terminal._LessonSandboxError:
                pass
        results["failed_spawn_leaves_no_token"] = (
            _terminal._ASSESS_CAPABILITIES == {}
        )
        return results

    _s3 = _asyncio.run(_s3_capability_contract())
    assert (
        _s3.get("agent_capability") and _s3.get("registry_copy")
    ), (
        "S3 a lesson-agent session carries the complete URL and a bound token"
    )
    assert (
        _s3.get("revoked_with_session")
        and _s3.get("revocation_is_per_session")
    ), (
        "S3 the capability dies with its own terminal session"
    )
    assert (
        _s3.get("distinct_sittings")
    ), (
        "S3 concurrent agent sessions on one lesson are distinct sittings"
    )
    assert (
        _s3.get("learner_gets_nothing") and _s3.get("plain_gets_nothing")
    ), (
        "S3 learner and plain shells receive neither capability variable"
    )
    assert (
        _s3.get("no_token_file_no_var") and _s3.get("token_env_agent_only")
    ), (
        "#188 the Claude token variable exists iff the instance file does, "
        "and only the lesson-agent shell receives it"
    )
    assert (
        _s3.get("no_url_no_token") and _s3.get("failed_spawn_leaves_no_token")
    ), (
        "S3 no injection without a spellable app address, none on a failed spawn"
    )
    assert (
        _s3.get("live_during_spawn") and _s3.get("live_during_spawn_revoked")
    ), (
        "S3 the capability is live while the child is still being spawned"
    )
    assert (
        _s3.get("revoked_on_reap_and_shutdown")
    ), (
        "S3 the idle reaper and the lifespan shutdown revoke it too"
    )
    _s3_urls = [
        _terminal._app_base_url(_E2Sock({}, {
            "server": ("127.0.0.1", 8765), "scheme": "ws"})),
        _terminal._app_base_url(_E2Sock({}, {
            "server": ("::1", 8765), "scheme": "wss"})),
        _terminal._app_base_url(_E2Sock({}, {"server": None})),
        _terminal._app_base_url(_E2Sock({}, {"server": ("127.0.0.1", None)})),
        _terminal._app_base_url(_E2Sock({}, {})),
    ]
    assert (
        _s3_urls == [
          "http://127.0.0.1:8765", "https://[::1]:8765",
          None, None, None]
        # the derivation reads the ASGI scope only — the fake carries no
        # headers at all, so a spoofed Host has no channel into the URL
        and not hasattr(_E2Sock({}), "headers")
    ), (
        "S3 the capability URL is the app's own bound address, never Host"
    )
    _s3_role_bound = False
    _s3_master, _s3_slave = _pty.openpty()
    try:
        _terminal._TermSession(
            "verify-s3-role", _types.SimpleNamespace(returncode=0), _s3_master,
            role="lesson-learner", workspace=ws_info["dir"],
            sandbox_profile="lesson-learner", assess_token="never")
    except ValueError:
        _s3_role_bound = True
    finally:
        os.close(_s3_master)
        os.close(_s3_slave)
    with _sandbox_mock.patch.dict(os.environ, {
            "EPHEMERIS_ASSESS_TOKEN": "leaked-from-the-service",
            "EPHEMERIS_ASSESS_URL": "http://leaked.invalid/"}):
        _s3_inherited = _terminal._child_env("lesson-agent")
    assert (
        _s3_role_bound and not (_S3_VARS & set(_s3_inherited))
    ), (
        "S3 the two names are minted per session, never inherited or role-shared"
    )

    # --- S4: the Learn record panel (S-DESIGN D-S3-1) ------------------------
    # The panel is a pure read over the s1 authority rows: the active fold, the
    # retired-question split, and escaped agent/learner text. Everything below
    # asserts what /learn RENDERS, plus the context builder directly where the
    # page cannot show it (the pure-manifest rule, the stale-process guard).
    import shutil as _s4_shutil

    from starlette.requests import Request as _s4_Request

    from app.routers.learn import _record_panel as _s4_panel
    from app.templating import templates as _s4_templates
    from app.services import focus as _s4_focus

    def _s4_rows(lesson_id):
        conn_ = get_conn()
        try:
            return [dict(r) for r in conn_.execute(
                "SELECT * FROM lesson_assessments WHERE lesson_id = ? ORDER BY id",
                (lesson_id,)).fetchall()]
        finally:
            conn_.close()

    _s4_conn = get_conn()
    try:
        _s4_id = lessons_svc.create_lesson(_s4_conn, "Record Panel Fixture")
        _s4 = lessons_svc.get_lesson(_s4_conn, _s4_id)
        _s4_focus.record_session(_s4_conn, "countdown", 25 * 60, lesson_id=_s4_id)
    finally:
        _s4_conn.close()
    _s4_dir = Path(lessons_svc.LESSONS_DIR) / _s4["slug"]
    _s4_manifest = json.loads((_s4_dir / "lesson.json").read_text(encoding="utf-8"))
    _s4_page = _s4_manifest["pages"][0]["id"]
    _s4_qs = ["q_s4alpha001", "q_s4beta0001", "q_s4gamma001", "q_s4retire01"]
    _s4_manifest["questions"] = [
        {"id": qid, "page": _s4_page, "kind": "prediction",
         "label": None if qid == "q_s4beta0001" else f"Label <{qid}> & more"}
        for qid in _s4_qs
    ]
    bschema.write_manifest(_s4_dir / "lesson.json", _s4_manifest)
    (_s4_dir / "index.html").write_text(
        "<html>Vera Example record page</html>", encoding="utf-8")
    _s4_rev = "sha256:" + hashlib.sha256(
        (_s4_dir / "index.html").read_bytes()).hexdigest()
    _s4_url = f"/learn/lessons/{_s4_id}/assessments"
    attempts_svc._reset_rate_limit()
    assess_svc._reset_rate_limit()

    def _s4_attempt(question_id, answer, key):
        return c.post(f"/learn/lessons/{_s4_id}/attempts", json={
            "question_id": question_id, "page_id": _s4_page,
            "page_rev": _s4_rev, "answer": answer,
            "idempotency_key": key}).json()["attempt_id"]

    def _s4_assess(body):
        return c.post(_s4_url, json=body).json()

    # alpha is answered twice: only the LATEST answer and the verdict on THAT
    # answer may render — a verdict must never migrate onto a newer attempt.
    _s4_a_old = _s4_attempt("q_s4alpha001", "s4-superseded-answer", "s4-a0")
    _s4_a_new = _s4_attempt(
        "q_s4alpha001", "Vera Example: <b>closes</b> the channel & waits.", "s4-a1")
    _s4_b = _s4_attempt("q_s4beta0001", "s4-beta-answer", "s4-b1")
    _s4_ret = _s4_attempt("q_s4retire01", "s4-retired-answer", "s4-r1")

    _s4_assess({"kind": "review", "level": "correct", "attempt_id": _s4_a_old,
                "note": "s4-verdict-on-the-old-answer", "idempotency_key": "s4-1"})
    _s4_rev1 = _s4_assess({
        "kind": "review", "level": "unclear", "attempt_id": _s4_a_new,
        "note": "s4-first-reading-of-the-answer", "idempotency_key": "s4-2"})
    _s4_assess({"kind": "review", "level": "partial", "attempt_id": _s4_a_new,
                "note": "Counts the edges, not the <iterations> & stops.",
                "supersedes": _s4_rev1["assessment_id"], "idempotency_key": "s4-3"})
    _s4_rev_beta = _s4_assess({
        "kind": "review", "level": "incorrect", "attempt_id": _s4_b,
        "note": "s4-review-that-gets-retracted", "idempotency_key": "s4-4"})
    _s4_assess({"kind": "retraction", "supersedes": _s4_rev_beta["assessment_id"],
                "note": "s4-wrong-attempt-judged", "idempotency_key": "s4-5"})
    _s4_assess({"kind": "review", "level": "correct", "attempt_id": _s4_ret,
                "note": "s4-verdict-kept-after-retirement", "idempotency_key": "s4-6"})
    _s4_ev1 = _s4_assess({
        "kind": "evidence", "level": "passed", "basis": "attempts",
        "concepts": ["channels & <select>"], "note": "s4-evidence-superseded",
        "idempotency_key": "s4-7"})
    # the note lands in a title="" attribute as well as in body text, so it
    # carries a quote and angle brackets: an escaping regression on either
    # surface has to show up somewhere in the assertions below.
    _s4_ev_note = 'Says "a mutex" where <a channel> fits & stalls.'
    _s4_assess({"kind": "evidence", "level": "weak", "basis": "live",
                "concepts": ["channels & <select>"], "note": _s4_ev_note,
                "supersedes": _s4_ev1["assessment_id"], "idempotency_key": "s4-8"})
    _s4_assess({"kind": "evidence", "level": "passed", "basis": "runs",
                "concepts": ["goroutines"], "mode": "exam",
                "note": "s4-exam-evidence", "idempotency_key": "s4-9"})
    _s4_sum1 = _s4_assess({"kind": "summary", "note": "s4-summary-superseded",
                           "idempotency_key": "s4-10"})
    _s4_assess({"kind": "summary", "note": "Covered fan-in & <select>.",
                "next_action": "Read the select page, then retry q_s4beta0001.",
                "supersedes": _s4_sum1["assessment_id"], "idempotency_key": "s4-11"})

    # q_s4retire01 leaves the manifest: its durable id is retired forever, and
    # the attempt and verdict behind it must not vanish with it (S-M7).
    _s4_manifest["questions"] = [
        q for q in _s4_manifest["questions"] if q["id"] != "q_s4retire01"]
    bschema.write_manifest(_s4_dir / "lesson.json", _s4_manifest)

    _s4_conn = get_conn()
    try:
        _s4_ctx = _s4_panel(_s4_conn, lessons_svc.get_lesson(_s4_conn, _s4_id))
    finally:
        _s4_conn.close()
    _s4_html = c.get(f"/learn?lesson={_s4_id}").text
    _s4_body = _s4_html.split('<details class="lesson-record"', 1)[-1]

    assert (
        '<details class="lesson-record"' in _s4_html
        and _s4_ctx["counts"] == {"attempts": 4, "questions": 0,
                                "assessments": 6,
                                "focus": "25m", "focus_seconds": 1500}
        and 'data-record-count="attempts">4</span> attempts' in _s4_body
        and 'data-record-count="assessments">6</span> active' in _s4_body
        and 'data-record-count="verdicts">2</span> verdicts' in _s4_body
        and 'data-record-count="focus">25m</span> focused' in _s4_body
    ), (
        "S4 panel renders on the selected lesson with its counts line" + "  -- " + (str(_s4_ctx["counts"]))
    )
    assert (
        [(chip["concept"], chip["level"], chip["exam"])
        for chip in _s4_ctx["evidence"]]
        == [("channels & <select>", "weak", False), ("goroutines", "passed", True)]
        and "s4-evidence-superseded" not in _s4_html
        and 'class="rec-chip rec-chip-fold rec-lv-weak"' in _s4_body
    ), (
        "S4 evidence chips fold to the latest ACTIVE row per concept, weak first" + "  -- " + (str(_s4_ctx["evidence"]))
    )
    assert (
        _s4_ctx["summary"]["note"] == "Covered fan-in & <select>."
        and "Read the select page, then retry q_s4beta0001." in _s4_body
        and 'class="rec-next"' in _s4_body
        and "s4-summary-superseded" not in _s4_html
    ), (
        "S4 latest active summary renders with its next_action line"
    )
    _s4_by_q = {q["question_id"]: q for q in _s4_ctx["questions"]}
    assert (
        [q["question_id"] for q in _s4_ctx["questions"]] == _s4_qs[:3]
        and _s4_by_q["q_s4alpha001"]["attempt"]["attempt_id"] == _s4_a_new
        and "s4-superseded-answer" not in _s4_html
        and _s4_by_q["q_s4gamma001"]["attempt"] is None
        and "Not attempted." in _s4_body
    ), (
        "S4 each question carries its LATEST attempt only" + "  -- " + (str([q["question_id"] for q in _s4_ctx["questions"]]))
    )
    assert (
        _s4_by_q["q_s4alpha001"]["review"]["level"] == "partial"
        and _s4_by_q["q_s4alpha001"]["earlier_reviews"] == 1
        and "(1 earlier)" in _s4_body
        and "s4-first-reading-of-the-answer" not in _s4_html
        # the verdict on the OLD attempt never migrates to the new one
        and "s4-verdict-on-the-old-answer" not in _s4_html
    ), (
        "S4 verdict is the latest ACTIVE review of that attempt, earlier ones a count"
    )
    assert (
        _s4_by_q["q_s4beta0001"]["review"] is None
        and "s4-review-that-gets-retracted" not in _s4_html
        and "s4-wrong-attempt-judged" not in _s4_html
        and "No verdict yet." in _s4_body
    ), (
        "S4 a retracted review leaves the question unreviewed, not mis-reviewed"
    )
    assert (
        [q["question_id"] for q in _s4_ctx["retired"]] == ["q_s4retire01"]
        and _s4_ctx["retired"][0]["review"]["level"] == "correct"
        and _s4_ctx["retired"][0]["retired"] is True
        and "s4-verdict-kept-after-retirement" in _s4_body
        and 'class="rec-tag rec-retired"' in _s4_body
        and 'rec-block rec-block-retired' in _s4_body
    ), (
        "S4 an undeclared question's attempt and verdict move to the retired block" + "  -- " + (str([q["question_id"] for q in _s4_ctx["retired"]]))
    )
    # Agent- and learner-authored text is the panel's whole content, so nothing
    # it carries may reach the parent document as markup (no markdown either).
    assert (
        "Counts the edges, not the &lt;iterations&gt; &amp; stops." in _s4_body
        and "Vera Example: &lt;b&gt;closes&lt;/b&gt; the channel &amp; waits."
          in _s4_body
        and "channels &amp; &lt;select&gt;" in _s4_body
        and "Covered fan-in &amp; &lt;select&gt;." in _s4_body
        and "Label &lt;q_s4alpha001&gt; &amp; more" in _s4_body
        and "<b>closes</b>" not in _s4_body and "<select>" not in _s4_body
        and "<iterations>" not in _s4_body
        # the same note inside the chip's title="" attribute: a dropped
        # escape there would close the attribute on the quote
        and 'Says &#34;a mutex&#34; where &lt;a channel&gt; fits &amp; stalls.'
          in _s4_body
        and _s4_ev_note not in _s4_body
    ), (
        "S4 agent/learner text renders as escaped text, never markup"
    )
    assert (
        _s4_by_q["q_s4beta0001"]["label"] == "q_s4beta0001"
    ), (
        "S4 a question label falls back to its durable id"
    )

    # PR round 1. A review written AFTER the displayed one and then retracted
    # is not an earlier reading of the answer: the fold falls back to the
    # older active review, so counting every review minus one would report a
    # later retracted verdict among the ones it replaced.
    _s4_late = _s4_assess({
        "kind": "review", "level": "incorrect", "attempt_id": _s4_a_new,
        "note": "s4-late-review-then-retracted", "idempotency_key": "s4-12"})
    _s4_assess({"kind": "retraction", "supersedes": _s4_late["assessment_id"],
                "note": "s4-retracting-the-late-review", "idempotency_key": "s4-13"})
    _s4_conn = get_conn()
    try:
        _s4_late_ctx = _s4_panel(_s4_conn, lessons_svc.get_lesson(_s4_conn, _s4_id))
    finally:
        _s4_conn.close()
    _s4_late_q = {q["question_id"]: q for q in _s4_late_ctx["questions"]}
    assert (
        _s4_late_q["q_s4alpha001"]["review"]["level"] == "partial"
        and _s4_late_q["q_s4alpha001"]["earlier_reviews"] == 1
        and "(2 earlier)" not in c.get(f"/learn?lesson={_s4_id}").text
    ), (
        "S4 only reviews written BEFORE the displayed verdict count as earlier" + "  -- " + (str(_s4_late_q["q_s4alpha001"]["earlier_reviews"]))
    )

    # PR round 1. `type-mismatch`, `dangling-ref` and `invalid-id` are DEGRADED,
    # not rejecting: a manifest can drop a question from the typed list while
    # still declaring it. Validation failure is not retirement.
    _s4_degraded = json.loads((_s4_dir / "lesson.json").read_text(encoding="utf-8"))
    _s4_degraded["questions"] = [
        dict(q, page="pg_no_such_page0") if q["id"] == "q_s4alpha001" else q
        for q in _s4_degraded["questions"]
    ]
    bschema.write_manifest(_s4_dir / "lesson.json", _s4_degraded)
    _s4_conn = get_conn()
    try:
        _s4_deg_lesson = lessons_svc.get_lesson(_s4_conn, _s4_id)
        _s4_deg_read = lessons_svc.read_bundle_readonly(_s4_deg_lesson)
        _s4_deg_ctx = _s4_panel(_s4_conn, _s4_deg_lesson)
    finally:
        _s4_conn.close()
    _s4_deg_html = c.get(f"/learn?lesson={_s4_id}").text.split(
        '<details class="lesson-record"', 1)[-1]
    _s4_deg_q = {q["question_id"]: q for q in _s4_deg_ctx["questions"]}
    assert (
        "dangling-ref" in _s4_deg_read.codes() and not _s4_deg_read.rejected
        and "q_s4alpha001" not in {q["id"] for q in _s4_deg_read.questions}
        and [q["question_id"] for q in _s4_deg_ctx["retired"]] == ["q_s4retire01"]
        and _s4_deg_q["q_s4alpha001"]["unvalidated"] is True
        and _s4_deg_q["q_s4alpha001"]["retired"] is False
        # its verdict survives the degraded read intact
        and _s4_deg_q["q_s4alpha001"]["review"]["level"] == "partial"
        and 'class="rec-tag rec-unvalidated"' in _s4_deg_html
    ), (
        "S4 a question dropped by validation is shown as unread, never retired" + "  -- " + (str([q["question_id"] for q in _s4_deg_ctx["retired"]]))
    )
    bschema.write_manifest(_s4_dir / "lesson.json", _s4_manifest)

    # PR round 3. A `questions` value of the wrong type is DEGRADED too, and it
    # leaves the typed list empty — so reading absence from it would retire
    # every attempted question at once. Nothing can be observed absent from a
    # list that is not there, so the whole declaration reads as unknown.
    (_s4_dir / "lesson.json").write_text(
        json.dumps(dict(_s4_manifest, questions={"q_s4alpha001": "moved?"})),
        encoding="utf-8")
    _s4_conn = get_conn()
    try:
        _s4_bad_lesson = lessons_svc.get_lesson(_s4_conn, _s4_id)
        _s4_bad_read = lessons_svc.read_bundle_readonly(_s4_bad_lesson)
        _s4_bad_ctx = _s4_panel(_s4_conn, _s4_bad_lesson)
    finally:
        _s4_conn.close()
    assert (
        "type-mismatch" in _s4_bad_read.codes() and not _s4_bad_read.rejected
        and _s4_bad_read.questions == []
        and _s4_bad_ctx["declared_known"] is False
        and _s4_bad_ctx["retired"] == []
        and {q["question_id"] for q in _s4_bad_ctx["questions"]}
        >= {"q_s4alpha001", "q_s4retire01"}
        and "no longer declared" not in c.get(f"/learn?lesson={_s4_id}").text
    ), (
        "S4 a question list of the wrong type retires nothing" + "  -- " + (str([q["question_id"] for q in _s4_bad_ctx["retired"]]))
    )

    # Drain L1. An explicit null is the same document as `{}` for this purpose
    # — a value is present and it is not a list — but `raw.get` cannot tell it
    # from a MISSING key, which does mean the author declares none. The bundle
    # reader passes null through as absent and says nothing about it, so the
    # panel has to read the key itself.
    (_s4_dir / "lesson.json").write_text(
        json.dumps(dict(_s4_manifest, questions=None)), encoding="utf-8")
    _s4_conn = get_conn()
    try:
        _s4_null_lesson = lessons_svc.get_lesson(_s4_conn, _s4_id)
        _s4_null_read = lessons_svc.read_bundle_readonly(_s4_null_lesson)
        _s4_null_ctx = _s4_panel(_s4_conn, _s4_null_lesson)
    finally:
        _s4_conn.close()
    _s4_dropped = json.loads(json.dumps(_s4_manifest))
    _s4_dropped.pop("questions")
    (_s4_dir / "lesson.json").write_text(json.dumps(_s4_dropped), encoding="utf-8")
    _s4_conn = get_conn()
    try:
        _s4_gone_ctx = _s4_panel(_s4_conn, lessons_svc.get_lesson(_s4_conn, _s4_id))
    finally:
        _s4_conn.close()
    assert (
        not _s4_null_read.rejected and _s4_null_read.questions == []
        and _s4_null_ctx["declared_known"] is False
        and _s4_null_ctx["retired"] == []
        and {q["question_id"] for q in _s4_null_ctx["questions"]}
        >= {"q_s4alpha001", "q_s4retire01"}
        # the key genuinely absent IS an answer: everything attempted has left
        and _s4_gone_ctx["declared_known"] is True
        and _s4_gone_ctx["questions"] == []
        and {q["question_id"] for q in _s4_gone_ctx["retired"]}
        >= {"q_s4alpha001", "q_s4retire01"}
    ), (
        "S4 an explicit null question list retires nothing; a missing key does" + "  -- " + (f'{_s4_null_ctx["declared_known"]} / {_s4_gone_ctx["declared_known"]}')
    )
    bschema.write_manifest(_s4_dir / "lesson.json", _s4_manifest)

    # PR round 6. `identity-mismatch` is DEGRADED so the copied bundle can
    # still render, but its declarations belong to another lesson. They
    # observe nothing about this lesson's question retirement.
    _s4_foreign_q = "q_s4foreign01"
    bschema.write_manifest(
        _s4_dir / "lesson.json",
        dict(_s4_manifest, lesson_uid=str(_uuid4()), questions=[{
            "id": _s4_foreign_q, "page": _s4_page,
            "kind": "prediction", "label": "Foreign lesson question",
        }]),
    )
    _s4_conn = get_conn()
    try:
        _s4_foreign_lesson = lessons_svc.get_lesson(_s4_conn, _s4_id)
        _s4_foreign_read = lessons_svc.read_bundle_readonly(_s4_foreign_lesson)
        _s4_foreign_ctx = _s4_panel(_s4_conn, _s4_foreign_lesson)
    finally:
        _s4_conn.close()
    _s4_foreign_html = c.get(f"/learn?lesson={_s4_id}").text.split(
        '<details class="lesson-record"', 1)[-1]
    assert (
        "identity-mismatch" in _s4_foreign_read.codes()
        and not _s4_foreign_read.rejected
        and _s4_foreign_ctx["declared_known"] is False
        and _s4_foreign_ctx["retired"] == []
        and _s4_foreign_q not in {
          q["question_id"] for q in _s4_foreign_ctx["questions"]
        }
        and {q["question_id"] for q in _s4_foreign_ctx["questions"]}
        >= {"q_s4alpha001", "q_s4retire01"}
        and "Foreign lesson question" not in _s4_foreign_html
        and "question declarations unavailable" in _s4_foreign_html
        and "manifest unreadable" not in _s4_foreign_html
        and "no longer declared" not in _s4_foreign_html
    ), (
        "S4 a foreign-identity declaration retires and declares nothing" + "  -- " + (str([q["question_id"] for q in _s4_foreign_ctx["questions"]]))
    )
    bschema.write_manifest(_s4_dir / "lesson.json", _s4_manifest)

    # PR round 7. v1 has no question declaration, so its missing `questions`
    # key says nothing about the recorded v2 questions still existing.
    (_s4_dir / "lesson.json").write_text(
        json.dumps({"entry": "index.html"}), encoding="utf-8")
    _s4_conn = get_conn()
    try:
        _s4_v1_lesson = lessons_svc.get_lesson(_s4_conn, _s4_id)
        _s4_v1_read = lessons_svc.read_bundle_readonly(_s4_v1_lesson)
        _s4_v1_ctx = _s4_panel(_s4_conn, _s4_v1_lesson)
    finally:
        _s4_conn.close()
    _s4_v1_html = c.get(f"/learn?lesson={_s4_id}").text.split(
        '<details class="lesson-record"', 1)[-1]
    assert (
        _s4_v1_read.version == bschema.SCHEMA_V1
        and not _s4_v1_read.rejected
        and _s4_v1_ctx["declared_known"] is False
        and _s4_v1_ctx["retired"] == []
        and {q["question_id"] for q in _s4_v1_ctx["questions"]}
        >= {"q_s4alpha001", "q_s4retire01"}
        and "question declarations unavailable" in _s4_v1_html
        and "manifest unreadable" not in _s4_v1_html
        and "no longer declared" not in _s4_v1_html
    ), (
        "S4 a v1 manifest asserts no question retirement" + "  -- " + (str([q["question_id"] for q in _s4_v1_ctx["questions"]]))
    )
    bschema.write_manifest(_s4_dir / "lesson.json", _s4_manifest)

    # PR round 3. A question may move pages, and `stale` was decided when the
    # answer was recorded — a move afterwards leaves no mark on it. So the row
    # shows the page the answer was WRITTEN on and names the new binding
    # beside it; adopting the manifest's current page would attribute an old
    # answer to a page it never came from, silently.
    _s4_moved_page = "pg_s4moved0001"
    _s4_moved = dict(
        _s4_manifest,
        pages=_s4_manifest["pages"] + [dict(_s4_manifest["pages"][0],
                                            id=_s4_moved_page,
                                            path="s4-moved.html")],
        questions=[dict(q, page=_s4_moved_page) if q["id"] == "q_s4alpha001" else q
                   for q in _s4_manifest["questions"]],
    )
    (_s4_dir / "s4-moved.html").write_text("<html>moved</html>", encoding="utf-8")
    bschema.write_manifest(_s4_dir / "lesson.json", _s4_moved)
    _s4_conn = get_conn()
    try:
        _s4_mv_ctx = _s4_panel(_s4_conn, lessons_svc.get_lesson(_s4_conn, _s4_id))
    finally:
        _s4_conn.close()
    _s4_mv = {q["question_id"]: q for q in _s4_mv_ctx["questions"]}
    _s4_mv_html = c.get(f"/learn?lesson={_s4_id}").text.split(
        '<details class="lesson-record"', 1)[-1]
    assert (
        _s4_mv["q_s4alpha001"]["attempt"]["attempt_id"] == _s4_a_new
        and _s4_mv["q_s4alpha001"]["page_id"] == _s4_page
        and _s4_mv["q_s4alpha001"]["moved_to"] == _s4_moved_page
        and _s4_mv["q_s4beta0001"]["moved_to"] is None
        and 'class="rec-tag rec-moved"' in _s4_mv_html
        and f">{_s4_page}<" in _s4_mv_html
    ), (
        "S4 an answer keeps the page it was written on when its question moves" + "  -- " + (f'{_s4_mv["q_s4alpha001"]["page_id"]} / '
        f'{_s4_mv["q_s4alpha001"]["moved_to"]}')
    )
    bschema.write_manifest(_s4_dir / "lesson.json", _s4_manifest)
    (_s4_dir / "s4-moved.html").unlink()

    # Re-check finding. The mirror of the round-1 case, and the one it missed:
    # a review RETRACTED before the displayed one is not an earlier reading of
    # the answer either — the retraction says it should not stand at all. A
    # review a later review CORRECTED still counts, which is what the marker is
    # for, so the alpha case above must keep its one.
    _s4_g_att = _s4_attempt("q_s4gamma001", "s4-gamma-answer", "s4-g1")
    _s4_g1 = _s4_assess({
        "kind": "review", "level": "unclear", "attempt_id": _s4_g_att,
        "note": "s4-gamma-review-retracted", "idempotency_key": "s4-14"})
    _s4_assess({"kind": "retraction", "supersedes": _s4_g1["assessment_id"],
                "note": "s4-gamma-retraction", "idempotency_key": "s4-15"})
    _s4_g2 = _s4_assess({
        "kind": "review", "level": "correct", "attempt_id": _s4_g_att,
        "note": "s4-gamma-standing-verdict", "idempotency_key": "s4-16"})
    _s4_conn = get_conn()
    try:
        _s4_ret_ctx = _s4_panel(_s4_conn, lessons_svc.get_lesson(_s4_conn, _s4_id))
    finally:
        _s4_conn.close()
    _s4_ret_q = {q["question_id"]: q for q in _s4_ret_ctx["questions"]}
    assert (
        _s4_ret_q["q_s4gamma001"]["review"]["assessment_id"]
        == _s4_g2["assessment_id"]
        and _s4_ret_q["q_s4gamma001"]["earlier_reviews"] == 0
        and "s4-gamma-review-retracted" not in c.get(f"/learn?lesson={_s4_id}").text
        # the corrected-by-a-review case is untouched
        and _s4_ret_q["q_s4alpha001"]["earlier_reviews"] == 1
    ), (
        "S4 a retracted review is not counted as an earlier reading either" + "  -- " + (str(_s4_ret_q["q_s4gamma001"]["earlier_reviews"]))
    )

    # PR round 5. The fold has to VISIT every active row, but it keeps one per
    # concept, one per attempt and one summary — and this runs on every /learn
    # render. So the walk carries no `note` (8 KiB a row, no cardinality
    # ceiling) and only the rows the fold keeps are read whole.
    _s4_bulk_att = _s4_attempt("q_s4beta0001", "s4-bulk-answer", "s4-bulk-a")
    for _s4_n in range(4):
        _s4_assess({"kind": "review", "level": "partial", "attempt_id": _s4_bulk_att,
                    "note": f"s4-bulk-note-{_s4_n} " * 400,
                    "idempotency_key": f"s4-bulk-{_s4_n}"})

    class _S4Spy:
        """Only what panel_state uses, so every read it makes is recorded."""

        def __init__(self, conn):
            self._conn, self.calls = conn, []

        @property
        def in_transaction(self):
            return self._conn.in_transaction

        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            return self._conn.execute(sql, params)

        def rollback(self):
            return self._conn.rollback()

    _s4_conn = get_conn()
    try:
        _s4_spy = _S4Spy(_s4_conn)
        _s4_latest_attempt_ids = {
            attempt["attempt_id"]
            for attempt in attempts_svc.lesson_attempt_summary(
                _s4_conn, _s4_id
            )["latest_by_question"].values()
        }
        _s4_bounded = assess_svc.panel_state(
            _s4_spy, _s4_id, review_attempt_ids=_s4_latest_attempt_ids
        )
        _s4_wide_fold = assess_svc.active_state(_s4_conn, _s4_id)
        _s4_active_n = len(assess_svc.active_rows(_s4_conn, _s4_id))
    finally:
        _s4_conn.close()
    _s4_wide_calls = [call for call in _s4_spy.calls if "SELECT *" in call[0]]
    _s4_winners = (len(_s4_bounded["evidence_by_concept"])
                   + len(_s4_bounded["reviews_by_attempt"])
                   + (1 if _s4_bounded["summary"] else 0))
    assert (
        _s4_active_n >= _s4_winners + 4
        and "note" not in assess_svc.ACTIVE_FOLD_KEYS_SQL
        and "next_action" not in assess_svc.ACTIVE_FOLD_KEYS_SQL
        and len(_s4_wide_calls) == 1
        # lesson_id + exactly one id per displayed record
        and len(_s4_wide_calls[0][1]) == 1 + _s4_winners
        # and the narrow path folds to precisely what the wide one does
        and _s4_bounded["evidence_by_concept"] == _s4_wide_fold["evidence_by_concept"]
        and _s4_bounded["reviews_by_attempt"] == {
          attempt_id: row
          for attempt_id, row in _s4_wide_fold["reviews_by_attempt"].items()
          if attempt_id in _s4_latest_attempt_ids
        }
        and _s4_bounded["summary"] == _s4_wide_fold["summary"]
    ), (
        "S4 the panel's fold walks narrow rows and reads only its winners whole" + "  -- " + (f"{_s4_active_n} active, {_s4_winners} read whole")
    )
    assert (
        set(_s4_bounded["reviews_by_attempt"]) <= _s4_latest_attempt_ids
        and set(_s4_wide_fold["reviews_by_attempt"])
        - set(_s4_bounded["reviews_by_attempt"])
    ), (
        "S4 hydrates no review winner for a discarded historical attempt" + "  -- " + (str(len(_s4_wide_fold["reviews_by_attempt"])
        - len(_s4_bounded["reviews_by_attempt"])))
    )
    _s4_count_calls = [
        call for call in _s4_spy.calls
        if "earlier_count" in call[0]
    ]
    assert (
        len(_s4_count_calls) == 1
        and "WITH winners(attempt_id, winner_id) AS (VALUES" in
          _s4_count_calls[0][0]
        and "COUNT(r.id) AS earlier_count" in _s4_count_calls[0][0]
        and set(_s4_count_calls[0][1][:-1:2])
          == set(_s4_bounded["reviews_by_attempt"])
        and "review_seqs" not in _s4_bounded
        and len(_s4_bounded["earlier_review_counts"])
        <= len(_s4_bounded["reviews_by_attempt"])
    ), (
        "S4 earlier-review markers aggregate only displayed winners" + "  -- " + (str(_s4_bounded["earlier_review_counts"]))
    )

    # PR round 9. Two variables per displayed winner are bounded independently
    # of both lifetime review history and the number of active historical
    # attempts that are not shown.
    class _S4CountCursor:
        def fetchall(self):
            return []

    class _S4CountConn:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            return _S4CountCursor()

    _s4_count_n = assess_svc._REVIEW_COUNTS_PER_QUERY + 1
    _s4_count_winners = {
        f"displayed-attempt-{n:04d}": {"seq": 2_000_000 + n}
        for n in range(_s4_count_n)
    }
    _s4_count_conn = _S4CountConn()
    assess_svc._earlier_review_counts(
        _s4_count_conn, _s4_id, _s4_count_winners
    )
    _s4_bound_attempts = {
        attempt_id
        for _sql, params in _s4_count_conn.calls
        for attempt_id in params[:-1:2]
    }
    assert (
        len(_s4_count_conn.calls) == 2
        and max(len(params) for _, params in _s4_count_conn.calls)
          == assess_svc._REVIEW_COUNTS_PER_QUERY * 2 + 1
        and _s4_bound_attempts == set(_s4_count_winners)
    ), (
        "S4 displayed-review aggregates use bounded SQL-variable batches" + "  -- " + (str([len(params) for _, params in _s4_count_conn.calls]))
    )

    # PR round 6. Hydration has no winner-count ceiling, but each SQL statement
    # has to stay below a fixed variable budget.
    class _S4HydrateCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _S4HydrateConn:
        def __init__(self, rows):
            self._rows, self.calls = rows, []

        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            return _S4HydrateCursor([
                self._rows[row_id] for row_id in params[1:]
            ])

    _s4_conn = get_conn()
    try:
        _s4_hydrate_template = dict(_s4_conn.execute(
            "SELECT * FROM lesson_assessments "
            "WHERE lesson_id = ? AND kind = 'evidence' ORDER BY id LIMIT 1",
            (_s4_id,),
        ).fetchone())
    finally:
        _s4_conn.close()
    _s4_hydrate_n = assess_svc._HYDRATE_IDS_PER_QUERY + 1
    _s4_hydrate_rows = {}
    _s4_hydrate_narrow = {}
    for _s4_n in range(_s4_hydrate_n):
        _s4_seq = 1_000_000 + _s4_n
        _s4_concept = f"bulk-concept-{_s4_n}"
        _s4_full = dict(
            _s4_hydrate_template,
            id=_s4_seq,
            assessment_id=f"00000000-0000-4000-8000-{_s4_seq:012d}",
            concepts_json=json.dumps([_s4_concept]),
        )
        _s4_hydrate_rows[_s4_seq] = _s4_full
        _s4_hydrate_narrow[_s4_concept] = assess_svc._fold_keys(_s4_full)
    _s4_hydrate_conn = _S4HydrateConn(_s4_hydrate_rows)
    _s4_hydrated = assess_svc._hydrate(_s4_hydrate_conn, _s4_id, {
        "evidence_by_concept": _s4_hydrate_narrow,
        "reviews_by_attempt": {},
        "summary": None,
    })
    assert (
        len(_s4_hydrate_conn.calls) == 2
        and max(len(params) for _, params in _s4_hydrate_conn.calls)
        == assess_svc._HYDRATE_IDS_PER_QUERY + 1
        and len(_s4_hydrated["evidence_by_concept"]) == _s4_hydrate_n
        and all(
          row["seq"] == 1_000_000 + n
          for n, row in enumerate(
              _s4_hydrated["evidence_by_concept"].values()
          )
        )
    ), (
        "S4 winner hydration uses bounded SQL-variable batches" + "  -- " + (str([len(params) for _, params in _s4_hydrate_conn.calls]))
    )

    # PR round 8. The attempt read establishes the panel snapshot. A review
    # committed by a sibling connection immediately afterwards must not leak
    # into the assessment fold/count queries from a newer database version.
    _s4_conn = get_conn()
    try:
        _s4_snapshot_before = _s4_panel(
            _s4_conn, lessons_svc.get_lesson(_s4_conn, _s4_id)
        )
    finally:
        _s4_conn.close()
    _s4_snapshot_alpha = {
        q["question_id"]: q for q in _s4_snapshot_before["questions"]
    }["q_s4alpha001"]
    _s4_panel_state_real = assess_svc.panel_state
    _s4_snapshot_write = {}

    def _s4_panel_state_racing(conn, lesson_id, **kwargs):
        if not _s4_snapshot_write:
            _s4_snapshot_write.update(_s4_assess({
                "kind": "review", "level": "correct",
                "attempt_id": _s4_snapshot_alpha["attempt"]["attempt_id"],
                "note": "s4-snapshot-later-review",
                "idempotency_key": "s4-snapshot-1",
            }))
        return _s4_panel_state_real(conn, lesson_id, **kwargs)

    assess_svc.panel_state = _s4_panel_state_racing
    _s4_conn = get_conn()
    try:
        _s4_snapshot_ctx = _s4_panel(
            _s4_conn, lessons_svc.get_lesson(_s4_conn, _s4_id)
        )
    finally:
        assess_svc.panel_state = _s4_panel_state_real
        _s4_conn.close()
    _s4_conn = get_conn()
    try:
        _s4_after_ctx = _s4_panel(
            _s4_conn, lessons_svc.get_lesson(_s4_conn, _s4_id)
        )
    finally:
        _s4_conn.close()
    _s4_snapshot_q = {
        q["question_id"]: q for q in _s4_snapshot_ctx["questions"]
    }["q_s4alpha001"]
    _s4_after_q = {
        q["question_id"]: q for q in _s4_after_ctx["questions"]
    }["q_s4alpha001"]
    assert (
        _s4_snapshot_write
        and _s4_snapshot_q["review"] == _s4_snapshot_alpha["review"]
        and _s4_snapshot_q["earlier_reviews"]
        == _s4_snapshot_alpha["earlier_reviews"]
        and _s4_after_q["review"]["assessment_id"]
        == _s4_snapshot_write["assessment_id"]
    ), (
        "S4 concurrent assessment writes cannot mix panel DB versions" + "  -- " + (f'{_s4_snapshot_q["review"]} / {_s4_after_q["review"]}')
    )

    # PR round 1 + re-check. The answer excerpt is bounded by SQLite, so a long
    # answer is never materialized whole to render 400 characters of it — and
    # the bound is over BLOB bytes, because SQLite's TEXT `substr`/`length` stop
    # at the first NUL and an attempt answer is not rejected for holding one.
    _s4_long = "".join(str(n % 10) for n in range(4000))
    _s4_long_att = _s4_attempt("q_s4gamma001", _s4_long, "s4-long-1")
    _s4_nul = "abc\x00def" + "é" * 3000
    _s4_nul_att = _s4_attempt("q_s4beta0001", _s4_nul, "s4-nul-1")
    _s4_wide = "é" * (attempts_svc.PANEL_ANSWER_CHARS - 1)
    _s4_wide_att = _s4_attempt("q_s4alpha001", _s4_wide, "s4-wide-1")
    _s4_conn = get_conn()
    try:
        _s4_views = attempts_svc.lesson_attempt_summary(
            _s4_conn, _s4_id)["latest_by_question"]
    finally:
        _s4_conn.close()
    _s4_long_view = _s4_views["q_s4gamma001"]
    _s4_nul_view = _s4_views["q_s4beta0001"]
    _s4_wide_view = _s4_views["q_s4alpha001"]
    assert (
        _s4_long_view["attempt_id"] == _s4_long_att
        and _s4_long_view["answer"] == _s4_long[:attempts_svc.PANEL_ANSWER_CHARS]
        and _s4_long_view["answer_truncated"] is True
        # a whole answer shorter than the bound is neither cut nor flagged,
        # multi-byte characters included
        and _s4_wide_view["attempt_id"] == _s4_wide_att
        and _s4_wide_view["answer"] == _s4_wide
        and _s4_wide_view["answer_truncated"] is False
    ), (
        "S4 the panel reads a bounded excerpt of the answer, flagged when cut" + "  -- " + (str(len(_s4_long_view["answer"])))
    )
    assert (
        _s4_nul_view["attempt_id"] == _s4_nul_att
        and _s4_nul_view["answer"] == _s4_nul[:attempts_svc.PANEL_ANSWER_CHARS]
        and len(_s4_nul_view["answer"]) == attempts_svc.PANEL_ANSWER_CHARS
        and _s4_nul_view["answer_truncated"] is True
        and "CAST(answer AS BLOB)" in attempts_svc._LATEST_PER_QUESTION_SQL
    ), (
        "S4 an embedded NUL neither swallows the answer nor hides the cut" + "  -- " + (repr(_s4_nul_view["answer"][:20]))
    )

    # The panel reads the manifest through the PURE reader (D-F1-2 binds phase
    # S too): a render may never create bundle state, and what it cannot read
    # it does not call retired.
    _s4_conn = get_conn()
    try:
        _s4_ghost_id = lessons_svc.create_lesson(_s4_conn, "Record Panel Unread")
        _s4_ghost = lessons_svc.get_lesson(_s4_conn, _s4_ghost_id)
        _s4_ghost_dir = Path(lessons_svc.LESSONS_DIR) / _s4_ghost["slug"]
        _s4_shutil.rmtree(_s4_ghost_dir)
        _s4_ghost_ctx = _s4_panel(_s4_conn, _s4_ghost)
    finally:
        _s4_conn.close()
    assert (
        not _s4_ghost_dir.exists()
        and _s4_ghost_ctx["declared_known"] is False
        and _s4_ghost_ctx["questions"] == [] and _s4_ghost_ctx["retired"] == []
        and _s4_ghost_ctx["empty"] is True
    ), (
        "S4 the panel's manifest read creates nothing and asserts no retirement"
    )
    assert (
        "Nothing recorded yet" in c.get(
          f"/learn?lesson={_s4_ghost_id}").text.split(
              '<details class="lesson-record"', 1)[-1]
    ), (
        "S4 an empty record still renders the panel with its invitation"
    )

    # Drain follow-up. Start the request with a valid selected page, then
    # atomically remove it after the DB state is captured but before the one
    # final manifest read. That read must be the single authority for bundle
    # metadata, selection persistence, and the record: the response falls
    # back visibly and the removed page is never stored as current_entry.
    _s4_learn = sys.modules["app.routers.learn"]
    _s4_ensure_real = lessons_svc._ensure_bundle_manifest
    _s4_readonly_real = lessons_svc.read_bundle_readonly
    _s4_mark_opened_real = lessons_svc.mark_opened
    _s4_db_state_real = _s4_learn._record_panel_db_state
    _s4_panel_real = _s4_learn._record_panel
    _s4_read_order = []
    _s4_ensured_reads = []
    _s4_readonly_reads = []
    _s4_panel_reads = []
    _s4_mark_opened_calls = []
    _s4_swap_path = "s4-swap-removed.html"
    _s4_swap_manifest = dict(
        _s4_manifest,
        pages=_s4_manifest["pages"] + [
            dict(
                _s4_manifest["pages"][0],
                id="pg_s4swap00001",
                path=_s4_swap_path,
                title="Invented swap page",
            )
        ],
    )
    (_s4_dir / _s4_swap_path).write_text(
        "<html>Invented swap page</html>", encoding="utf-8"
    )
    bschema.write_manifest(_s4_dir / "lesson.json", _s4_swap_manifest)
    _s4_conn = get_conn()
    try:
        lessons_svc.mark_opened(_s4_conn, _s4_id, "index.html")
    finally:
        _s4_conn.close()

    def _s4_ensure_once(lesson):
        read = _s4_ensure_real(lesson)
        _s4_read_order.append("manifest")
        _s4_ensured_reads.append(read)
        return read

    def _s4_readonly_unexpected(lesson):
        _s4_readonly_reads.append(lesson["id"])
        return _s4_readonly_real(lesson)

    def _s4_mark_opened_spy(conn, lesson_id, entry):
        _s4_mark_opened_calls.append((lesson_id, entry))
        return _s4_mark_opened_real(conn, lesson_id, entry)

    def _s4_db_before_manifest(conn, lesson_id):
        _s4_read_order.append("db")
        state = _s4_db_state_real(conn, lesson_id)
        bschema.write_manifest(_s4_dir / "lesson.json", _s4_manifest)
        _s4_read_order.append("manifest-swap")
        return state

    def _s4_panel_same_read(conn, lesson, **kwargs):
        _s4_read_order.append("panel")
        _s4_panel_reads.append(kwargs.get("manifest_read"))
        return _s4_panel_real(conn, lesson, **kwargs)

    lessons_svc._ensure_bundle_manifest = _s4_ensure_once
    lessons_svc.read_bundle_readonly = _s4_readonly_unexpected
    lessons_svc.mark_opened = _s4_mark_opened_spy
    _s4_learn._record_panel_db_state = _s4_db_before_manifest
    _s4_learn._record_panel = _s4_panel_same_read
    try:
        _s4_same_manifest = c.get(
            f"/learn?lesson={_s4_id}&entry={_s4_swap_path}"
        )
    finally:
        bschema.write_manifest(_s4_dir / "lesson.json", _s4_manifest)
        lessons_svc._ensure_bundle_manifest = _s4_ensure_real
        lessons_svc.read_bundle_readonly = _s4_readonly_real
        lessons_svc.mark_opened = _s4_mark_opened_real
        _s4_learn._record_panel_db_state = _s4_db_state_real
        _s4_learn._record_panel = _s4_panel_real
    _s4_conn = get_conn()
    try:
        _s4_entry_after_swap = lessons_svc.get_lesson(
            _s4_conn, _s4_id
        )["current_entry"]
    finally:
        _s4_conn.close()
    _s4_swap_html = _s4_same_manifest.text
    assert (
        _s4_same_manifest.status_code == 200
        and _s4_read_order
          == ["db", "manifest-swap", "manifest", "panel"]
        and len(_s4_ensured_reads) == 1
        and _s4_panel_reads == [_s4_ensured_reads[-1]]
        # #81 reads other lessons' manifests through the readonly reader to
        # build the track strip. The invariant here is about THIS lesson: its
        # single ensured read stays the only authority, so the track pass takes
        # the read it was handed rather than re-reading the file.
        and _s4_id not in _s4_readonly_reads
        and not _s4_mark_opened_calls
        and _s4_entry_after_swap == "index.html"
        and "/files/index.html" in _s4_swap_html
        and f"preview-meta?entry={_s4_swap_path}" in _s4_swap_html
    ), (
        "S4 a page removed before the final manifest read is not persisted" + "  -- " + (f"order={_s4_read_order}, current_entry={_s4_entry_after_swap}")
    )

    # The live process runs the OLD context until the owner's restart while
    # serving this template from the working tree: the guard must omit the
    # panel rather than half-draw it.
    _s4_stale_selected = {
        k: v for k, v in dict(
            lessons_svc.with_bundle_info(_s4),
            file_url="", preview_url="", preview_meta_url="",
            sandbox="allow-scripts", record=None,
        ).items() if k != "record"
    }
    _s4_stale = _s4_templates.get_template("learn.html").render(
        request=_s4_Request({"type": "http", "client": ("127.0.0.1", 50000),
                             "headers": [], "method": "GET", "path": "/learn",
                             "query_string": b"", "scheme": "http",
                             "server": ("testserver", 80)}),
        rows=[], counts={"all": 0, "archived": 0,
                         **{k: 0 for k in lessons_svc.STATUSES}},
        status_tabs=[], status_filter=None, show_archived=False, flash=None,
        self_url="/learn", selected=_s4_stale_selected)
    assert (
        "record" not in _s4_stale_selected
        and "lesson-record" not in _s4_stale and "lesson-frame" in _s4_stale
    ), (
        "S4 the template guard omits the panel when the context lacks it"
    )
    _s4_all = _s4_rows(_s4_id)
    _s4_superseded = {r["supersedes"] for r in _s4_all if r["supersedes"]}
    _s4_conn = get_conn()
    try:
        _s4_final = _s4_panel(_s4_conn, lessons_svc.get_lesson(_s4_conn, _s4_id))
    finally:
        _s4_conn.close()
    assert (
        assess_svc.fold_rows([]) == {"evidence_by_concept": {},
                                   "reviews_by_attempt": {}, "summary": None}
        and _s4_final["counts"]["assessments"] == len(
          [r for r in _s4_all if r["kind"] != "retraction"
           and r["assessment_id"] not in _s4_superseded])
        # the whole lesson's writes are in this count, not just the first fold
        and _s4_final["counts"]["assessments"] > _s4_ctx["counts"]["assessments"]
    ), (
        "S4 the fold helpers are shared, and retractions are not counted as state"
    )

    suite_state.update({
        name: value for name, value in locals().items()
        if name not in {"client", "suite_state"}
    })
