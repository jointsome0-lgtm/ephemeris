"""Role selection and fixed runner verification."""
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



def test_role_runner(client, suite_state):
    from app.db import SCHEMA_VERSION, get_conn, pretty_date, today_str
    from app.main import app

    c = client
    _E2Sock = suite_state["_E2Sock"]
    _asyncio = suite_state["_asyncio"]
    _lt = suite_state["_lt"]
    _resource_mod = suite_state["_resource_mod"]
    _sandbox = suite_state["_sandbox"]
    _sandbox_mock = suite_state["_sandbox_mock"]
    _sb_bundle = suite_state["_sb_bundle"]
    _sb_mounts = suite_state["_sb_mounts"]
    _sb_root = suite_state["_sb_root"]
    _terminal = suite_state["_terminal"]
    _terminal_pty = suite_state["_terminal_pty"]
    _time = suite_state["_time"]
    _types = suite_state["_types"]
    bschema = suite_state["bschema"]
    lessons_svc = suite_state["lessons_svc"]
    ws_info = suite_state["ws_info"]
    # --- E3: closed role selector + concurrent agent/learner integration -----
    assert (
        _terminal._TERMINAL_ROLES == (
            "plain", "lesson-agent", "lesson-learner",
        )
        and _terminal._select_create_role(None, None) == "plain"
        and _terminal._select_create_role(_lt["slug"], None) == "lesson-agent"
    ), "E3 role enum is closed and absent selector preserves E2 semantics"
    _plain_lesson_refused = False
    try:
        _terminal._select_create_role(_lt["slug"], "plain")
    except _terminal._SessionRequestError:
        _plain_lesson_refused = True
    assert _plain_lesson_refused, "E3 explicit plain cannot bypass the sandboxed lesson boundary"
    _selector_refusals = 0
    for _lesson_arg, _role_arg in (
        (None, "lesson-learner"),
        (_lt["slug"], "unknown"),
    ):
        try:
            _terminal._select_create_role(_lesson_arg, _role_arg)
        except _terminal._SessionRequestError:
            _selector_refusals += 1
    _sid_role_ws = _E2Sock({
        "sid": "invented-stale-sid",
        "lesson": _lt["slug"],
        "role": "lesson-learner",
    })
    with _sandbox_mock.patch.object(_terminal, "_ws_is_trusted", return_value=True), \
            _sandbox_mock.patch.object(_terminal, "_reap_idle"), \
            _sandbox_mock.patch.object(_terminal, "_ensure_reaper"), \
            _sandbox_mock.patch.object(
                _terminal, "_create_session",
                new=_sandbox_mock.AsyncMock()) as _sid_role_create:
        _asyncio.run(_terminal._serve_ws(_sid_role_ws))
    assert (
        _selector_refusals == 2
        and _sid_role_create.call_count == 0
        and _sid_role_ws.accepted and _sid_role_ws.closed
        and b"invalid session request" in b"".join(_sid_role_ws.sent_bytes)
    ), "E3 selector validation refuses no-lesson, unknown, and sid attach"

    async def _e3_invalid_selector_at_capacity():
        with _sandbox_mock.patch.object(_terminal, "_MAX_SESSIONS", 0), \
                _sandbox_mock.patch.object(_terminal, "_reap_idle") as reap:
            try:
                await _terminal._create_session(_lt["slug"], "unknown")
            except _terminal._SessionRequestError:
                refused = True
            else:
                refused = False
        return refused and reap.call_count == 0

    assert _asyncio.run(_e3_invalid_selector_at_capacity()), "E3 invalid selector cannot evict a detached session at capacity"
    with _sandbox_mock.patch.dict(os.environ, {
        "SSH_AUTH_SOCK": "/run/user/1000/agent.sock",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "HOME": "/root",
        "PATH": "/root/private-bin:/usr/bin",
        "SHELL": "/root/private-shell",
        "XDG_CONFIG_HOME": "/srv/private-config",
        "XDG_DATA_HOME": "/srv/private-data",
        "XDG_CACHE_HOME": "/srv/private-cache",
        "XDG_STATE_HOME": "/srv/private-state",
    }):
        _agent_socket_env = _terminal._child_env("lesson-agent")
        _learner_socket_env = _terminal._child_env("lesson-learner")
    from app import runner as _runner_toolchain
    assert (
        _agent_socket_env.get("SSH_AUTH_SOCK") == "/run/user/1000/agent.sock"
        and _agent_socket_env.get("XDG_RUNTIME_DIR") == "/run/user/1000"
        and "SSH_AUTH_SOCK" not in _learner_socket_env
        and "XDG_RUNTIME_DIR" not in _learner_socket_env
        and _learner_socket_env.get("HOME") == _sandbox.USER_HOME
        and _learner_socket_env.get("SHELL") == "/bin/bash"
        and _learner_socket_env.get("PATH")
            == f"{_sandbox.USER_HOME}/.local/bin:/usr/local/go/bin:"
               "/usr/local/bin:/usr/bin:/bin"
        # The learner shell resolves the same toolchain the runner compiles with.
        and "/usr/local/go/bin" in _runner_toolchain.RUNNER_ENV["PATH"]
        and not any(name in _learner_socket_env for name in (
            "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
        ))
    ), "E3 learner child env strips inherited host-socket discovery paths"
    with tempfile.TemporaryDirectory(prefix="ephemeris-e3-mask-") as _mask_tmp:
        _mask_base = Path(_mask_tmp)
        _mask_target = _mask_base / "resolved-private"
        _mask_target.mkdir()
        _mask_link = _mask_base / "private-link"
        _mask_link.symlink_to(_mask_target, target_is_directory=True)
        _mask_spellings = _terminal._private_mask_spellings(_mask_link)
        _lesson_store_target = _mask_base / "resolved-lesson-store"
        _lesson_store_target.mkdir()
        _lesson_store_link = _mask_base / "lessons-link"
        _lesson_store_link.symlink_to(
            _lesson_store_target, target_is_directory=True)
        _db_target_dir = _mask_base / "resolved-db"
        _db_target_dir.mkdir()
        _db_target = _db_target_dir / "activity.sqlite"
        _db_target.touch()
        _db_link_dir = _mask_base / "db-link-dir"
        _db_link_dir.mkdir()
        _db_link = _db_link_dir / "activity.sqlite"
        _db_link.symlink_to(_db_target)
        _db_mask_spellings = _terminal._learner_private_mask_spellings(
            data_root=_mask_link,
            lesson_root=_lesson_store_link,
            db_path=_db_link,
            repo_root=_terminal._REPO_ROOT,
        )
    assert (
        _mask_spellings == (str(_mask_link), str(_mask_target))
        and str(_lesson_store_link) in _db_mask_spellings
        and str(_lesson_store_target) in _db_mask_spellings
        and str(_db_link_dir) in _db_mask_spellings
        and str(_db_target_dir) in _db_mask_spellings
    ), "E3 private masks include lexical symlinks and resolved targets"

    async def _e3_db_in_bundle_refusal():
        workspace = {"dir": ws_info["dir"], "slug": _lt["slug"], "title": "demo"}
        bundle_db = Path(workspace["dir"]) / "invented-private.sqlite"
        outside_db = Path(workspace["dir"]).parent / "invented-private.sqlite"
        with _sandbox_mock.patch.object(
                _terminal, "resolve_terminal_workspace", return_value=workspace), \
                _sandbox_mock.patch.object(_terminal, "DB_PATH", bundle_db), \
                _sandbox_mock.patch.object(_terminal_pty, "openpty") as openpty, \
                _sandbox_mock.patch.object(
                    _terminal, "spawn_sandboxed",
                    new=_sandbox_mock.AsyncMock()) as spawn:
            try:
                await _terminal._create_session(_lt["slug"], "lesson-learner")
            except _terminal._LessonSandboxError:
                refused = True
            else:
                refused = False
        return (
            refused and openpty.call_count == 0 and spawn.call_count == 0
            and _terminal._learner_workspace_contains_db(
                workspace["dir"], bundle_db)
            and not _terminal._learner_workspace_contains_db(
                workspace["dir"], outside_db)
        )

    assert _asyncio.run(_e3_db_in_bundle_refusal()), "E3 learner refuses a DB override inside the writable bundle"
    _external_private = "/srv/invented-ephemeris-private"
    _external_lessons = f"{_external_private}/lessons"
    _external_bundle = f"{_external_lessons}/invented-bundle"
    _external_learner_argv = _sandbox.build_sandbox_argv(
        "lesson-learner", _external_bundle,
        bundle_root=_external_lessons,
        private_root=_external_private,
    )
    _external_tmpfs = [
        _external_learner_argv[i + 1]
        for i, value in enumerate(_external_learner_argv)
        if value == "--tmpfs"
    ]
    assert (
        _sandbox.RUNTIME_DIR in _external_tmpfs
        and _external_private in _external_tmpfs
        and _external_learner_argv.index(_external_private)
            < _external_learner_argv.index("--bind")
        and _sb_mounts(_external_learner_argv, "--bind")
            == [(_external_bundle, _external_bundle)]
    ), "E3 learner masks runtime sockets and external private instance root"
    _nested_private = "/home/aina/go/invented-ephemeris-private"
    _nested_lessons = f"{_nested_private}/lessons"
    _nested_bundle = f"{_nested_lessons}/invented-bundle"
    _db_override_root = "/opt/invented-ephemeris-db"
    _checkout_root = "/workspace/invented-ephemeris-checkout"
    _nested_learner_argv = _sandbox.build_sandbox_argv(
        "lesson-learner", _nested_bundle,
        bundle_root=_nested_lessons,
        private_root=_nested_private,
        private_masks=(_db_override_root, _checkout_root),
    )
    _nested_tmpfs = [
        _nested_learner_argv[i + 1]
        for i, value in enumerate(_nested_learner_argv)
        if value == "--tmpfs"
    ]
    assert (
        _nested_private in _nested_tmpfs
        and _db_override_root in _nested_tmpfs
        and _checkout_root in _nested_tmpfs
        and _nested_learner_argv.index("/home/aina/go")
            < _nested_learner_argv.index(_nested_private)
        and _nested_learner_argv.index(_db_override_root)
            < _nested_learner_argv.index("--bind")
    ), "E3 learner masks cache-nested data, DB override, and external checkout"

    async def _e3_learner_plumbing():
        workspace = {"dir": ws_info["dir"], "slug": _lt["slug"], "title": "demo"}
        proc = _types.SimpleNamespace(returncode=0)
        with _sandbox_mock.patch.object(
                _terminal, "resolve_terminal_workspace", return_value=workspace) as resolve, \
                _sandbox_mock.patch.object(
                    _terminal, "prepare_terminal_workspace") as prepare, \
                _sandbox_mock.patch.object(
                    _terminal, "_detect_proxy_env", return_value={}) as proxy, \
                _sandbox_mock.patch.object(
                    _terminal, "spawn_sandboxed",
                    new=_sandbox_mock.AsyncMock(return_value=proc)) as spawn, \
                _sandbox_mock.patch.object(_terminal._TermSession, "start"):
            session = await _terminal._create_session(
                _lt["slug"], "lesson-learner")
        call = spawn.call_args
        result = (
            resolve.call_count == 1 and prepare.call_count == 0
            and proxy.call_args.args == ("lesson-learner",)
            and call.args[:3] == (
                "lesson-learner", workspace["dir"], ["/bin/bash", "-i"],
            )
            and call.kwargs["private_root"] == str(lessons_svc.LESSONS_DIR.parent)
            and set(call.kwargs["private_masks"]) == set(
                _terminal._learner_private_mask_spellings()
            )
            and not any(name in call.kwargs["env"] for name in (
                *_terminal._PROXY_ENV_VARS, "SSH_AUTH_SOCK", "XDG_RUNTIME_DIR",
            ))
        )
        _terminal._SESSIONS.pop(session.sid, None)
        os.close(session.master_fd)
        return result

    assert _asyncio.run(_e3_learner_plumbing()), "E3 learner spawn plumbs only its private masks and no socket/proxy env"

    try:
        _sandbox.require_sandbox_runtime()
        _e3_host_runtime = True
        _e3_runtime_detail = ""
    except _sandbox.SandboxError as exc:
        _e3_host_runtime = False
        _e3_runtime_detail = str(exc)
    if _e3_host_runtime:
        _e3_override_sentinel = (
            Path(os.environ["ACTIVITY_DATA_DIR"])
            / "invented-e3-inherited-override.sqlite"
        )
        _e3_probe_env = os.environ.copy()
        _e3_probe_env["ACTIVITY_DB"] = str(_e3_override_sentinel)
        _e3_probe_run = subprocess.run(
            [sys.executable, "scripts/verify_e3_sessions.py"],
            cwd=ROOT,
            env=_e3_probe_env,
            text=True,
            capture_output=True,
        )
        try:
            _e3_probe = json.loads(_e3_probe_run.stdout)
        except (TypeError, ValueError):
            _e3_probe = {}
        _e3_extra = _e3_probe_run.stderr.strip() or _e3_probe_run.stdout.strip()
        assert (
            _e3_probe_run.returncode == 0
            and _e3_probe.get("wire_param") == "role"
            and _e3_probe.get("selector_without_lesson_refused") is True
            and _e3_probe.get("unknown_role_refused") is True
            and _e3_probe.get("selector_with_sid_refused") is True
            and not _e3_override_sentinel.exists()
        ), f"E3 host probe: ?role= wire has all three required refusals: {_e3_extra}"
        assert (
            _e3_probe.get("agent_role_echoed") is True
            and _e3_probe.get("learner_role_echoed") is True
            and _e3_probe.get("both_shells_live") is True
            and _e3_probe.get("stale_learner_sid_refused") is True
        ), f"E3 host probe: concurrent WS sessions echo both roles: {_e3_extra}"
        assert _e3_probe.get("briefs_unchanged") is True, f"E3 host probe: learner leaves both briefs untouched: {_e3_extra}"
        assert (
            _e3_probe.get("agent_network") is True
            and _e3_probe.get("learner_no_network") is True
            and _e3_probe.get("learner_no_proxy_env") is True
            and _e3_probe.get("learner_no_socket_env") is True
        ), f"E3 host probe: agent network; learner no network/proxy/socket env: {_e3_extra}"
    else:
        assert True, f"E3 host probe skipped when sandbox runtime is unavailable: {_e3_runtime_detail}"

    # --- F3: fixed runner registry, sandbox limits, job owner, host matrix ---
    from app import runner as _runner
    from app.services import runner_registry as _runner_registry

    _registry_source = (ROOT / "app/services/runner_registry.py").read_text(
        encoding="utf-8"
    )
    assert (
        "from app" not in _registry_source
        and set(_runner_registry.RUNNER_REGISTRY) == {
            "python-script-v1", "go-run-v1",
        }
        and _runner_registry.RUNNER_REGISTRY["python-script-v1"].argv == (
            "/usr/bin/python3", _runner_registry.SNAPSHOT_PATH,
        )
        and _runner_registry.RUNNER_REGISTRY["go-run-v1"].argv == (
            "/usr/local/go/bin/go", "run", _runner_registry.SNAPSHOT_PATH,
        )
    ), "F3 registry is a pure leaf with the two frozen v1 runners"
    _f3_specs_valid = all(
        spec.argv.count(_runner_registry.SNAPSHOT_PATH) == 1
        and 1 <= spec.wall_seconds <= _runner_registry.MAX_WALL_SECONDS
        for spec in _runner_registry.RUNNER_REGISTRY.values()
    )
    try:
        _runner_registry.RunnerSpec(("/usr/bin/python3",), (".py",))
        _f3_bad_spec_refused = False
    except ValueError:
        _f3_bad_spec_refused = True
    assert (
        _f3_specs_valid and _f3_bad_spec_refused
        and _runner_registry.RUNNER_REGISTRY["python-script-v1"].accepts("demo.py")
        and not _runner_registry.RUNNER_REGISTRY["python-script-v1"].accepts("demo.go")
    ), "F3 registry argv has one placeholder and bounded pure data only"

    _f3_manifest = {
        "schema_version": 2,
        "lesson_uid": "1b7e9c9e-4a5d-4f5e-9c6f-2a8b7d3e1f04",
        "runtime": {"profile": "interactive-local-v1"},
        "entry": "index.html",
        "pages": [{"id": "pg_demo", "path": "index.html"}],
        "artifact_roots": ["attempts"],
        "blocks": [{
            "id": "blk_demo", "page": "pg_demo", "kind": "editor",
            "file": "attempts/blk_demo/main.py", "runner_id": "python-script-v1",
        }],
    }
    _f3_compatible = bschema.read_manifest_text(
        json.dumps(_f3_manifest), runner_registry=_runner_registry.RUNNER_REGISTRY
    )
    _f3_manifest["blocks"][0]["file"] = "attempts/blk_demo/main.go"
    _f3_incompatible = bschema.read_manifest_text(
        json.dumps(_f3_manifest), runner_registry=_runner_registry.RUNNER_REGISTRY
    )
    assert (
        _f3_compatible.blocks[0]["run_enabled"] is True
        and _f3_incompatible.blocks[0]["run_enabled"] is False
        and "incompatible-runner" in _f3_incompatible.codes()
    ), "F3 manifest runner suffix gates Run but retains the editor"
    _f3_manifest["blocks"][0]["file"] = "attempts/blk_demo/main.py"
    _f3_legacy_manifest = json.loads(json.dumps(_f3_manifest))
    _f3_legacy_manifest.pop("runtime")
    _f3_legacy = bschema.read_manifest_text(
        json.dumps(_f3_legacy_manifest),
        runner_registry=_runner_registry.RUNNER_REGISTRY,
    )
    _f3_rejected_manifest = json.loads(json.dumps(_f3_manifest))
    _f3_rejected_manifest.pop("lesson_uid")
    _f3_rejected = bschema.read_manifest_text(
        json.dumps(_f3_rejected_manifest),
        runner_registry=_runner_registry.RUNNER_REGISTRY,
    )
    assert (
        _f3_legacy.effective_profile == bschema.PROFILE_LEGACY
        and _f3_legacy.blocks[0]["run_enabled"] is False
        and _f3_rejected.rejected
        and _f3_rejected.blocks[0]["run_enabled"] is False
    ), "F3 fail-closed manifests never grant the Run affordance"
    import inspect as _inspect
    _ensure_source = _inspect.getsource(lessons_svc._ensure_bundle_manifest)
    assert _ensure_source.count("runner_registry=RUNNER_REGISTRY") == 2, "F3 lesson manifest reads use the real registry at both call sites"

    def _f3_argv_digest(argv):
        return hashlib.sha256(
            json.dumps(argv, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    assert (
        _f3_argv_digest(_sandbox.build_sandbox_argv(
            "lesson-agent", _sb_bundle, bundle_root=_sb_root
        )) == "a0a6b85c4d66389748fd17572dc7f5f2bbfb69c92414d9fb21732dde5a0acf5a"
        and _f3_argv_digest(_sandbox.build_sandbox_argv(
            "lesson-learner", _sb_bundle, bundle_root=_sb_root
        )) == "a77d4eeef5689810b8a10cd123fe5600dbe8332b994072c1d09fdd605ce8301f"
    ), "F3 sandbox amendments keep agent/learner argv byte-identical"
    _f3_private = "/srv/invented-private"
    _f3_root = f"{_f3_private}/lessons"
    _f3_bundle = f"{_f3_root}/invented-bundle"
    _f3_runner_argv = _sandbox.build_sandbox_argv(
        "lesson-runner", _f3_bundle,
        bundle_root=_f3_root,
        private_root=_f3_private,
        private_masks=("/opt/invented-private-db",),
        snapshot_fd=7,
        snapshot_name="main.py",
        module_cache_fd=8,
    )
    _f3_tmpfs = [
        _f3_runner_argv[i + 1] for i, arg in enumerate(_f3_runner_argv)
        if arg == "--tmpfs"
    ]
    assert (
        ["--size", str(_sandbox.RUNNER_SCRATCH_BYTES), "--tmpfs", "/tmp"]
            == _f3_runner_argv[_f3_runner_argv.index("--size"):
                               _f3_runner_argv.index("--size") + 4]
        and ["--size", str(_sandbox.RUNNER_HOME_BYTES), "--tmpfs", _sandbox.USER_HOME]
            in [_f3_runner_argv[i:i + 4] for i in range(len(_f3_runner_argv) - 3)]
        and _sandbox.RUNTIME_DIR in _f3_tmpfs
        and _f3_private in _f3_tmpfs
        and "/opt/invented-private-db" in _f3_tmpfs
        and _f3_runner_argv.index(_f3_private) < _f3_runner_argv.index(_f3_bundle)
    ), "F3 runner argv has sized scratch/home, /run, and late private masks"
    assert (
        ["--perms", "0444", "--ro-bind-data", "7",
         f"{_sandbox.RUNNER_WORKDIR}/main.py"]
            in [_f3_runner_argv[i:i + 5] for i in range(len(_f3_runner_argv) - 4)]
        and ("8", "/home/aina/go/pkg/mod")
            in _sb_mounts(_f3_runner_argv, "--ro-bind-fd")
        and "/home/aina/.cache/go-build" not in _f3_runner_argv
        and _sb_mounts(_f3_runner_argv, "--ro-bind")[-1]
            == (_f3_bundle, _f3_bundle)
        and _f3_runner_argv[-2:] == ["--chdir", _sandbox.RUNNER_WORKDIR]
    ), "F3 runner argv injects one 0444 fd snapshot and only the ro Go module cache"
    try:
        _sandbox.build_sandbox_argv(
            "lesson-runner", _f3_bundle, bundle_root=_f3_root,
            private_root=_f3_private,
            private_masks=(f"{_f3_bundle}/invented-secret",),
            module_cache_fd=8,
        )
        _f3_overlap_refused = False
    except ValueError:
        _f3_overlap_refused = True
    assert _f3_overlap_refused, "F3 runner fails closed when a private mask is inside the mounted bundle"
    try:
        _sandbox.build_sandbox_argv(
            "lesson-runner", _f3_bundle, bundle_root=_f3_root,
        )
        _f3_missing_private_refused = False
    except ValueError:
        _f3_missing_private_refused = True
    with _sandbox_mock.patch.object(
        _sandbox, "EPHEMERIS_CHECKOUT_ROOT", "/workspace/invented-checkout"
    ):
        _f3_external_checkout_argv = _sandbox.build_sandbox_argv(
            "lesson-runner", _f3_bundle, bundle_root=_f3_root,
            private_root=_f3_private,
            module_cache_fd=8,
        )
    assert (
        _f3_missing_private_refused
        and "/workspace/invented-checkout" in [
            _f3_external_checkout_argv[i + 1]
            for i, arg in enumerate(_f3_external_checkout_argv)
            if arg == "--tmpfs"
        ]
    ), "F3 runner requires private authority and masks an external checkout"
    with tempfile.TemporaryDirectory(
        prefix="ephemeris-f3-cache-link-", dir="/tmp"
    ) as _f3_cache_raw:
        _f3_cache_root = Path(_f3_cache_raw)
        _f3_cache_target = _f3_cache_root / "invented-private"
        _f3_cache_target.mkdir()
        (_f3_cache_target / "mod").mkdir()
        _f3_cache_link = _f3_cache_root / "module-cache"
        _f3_cache_link.symlink_to(_f3_cache_target, target_is_directory=True)
        with _sandbox_mock.patch.object(
            _sandbox, "GO_MODULE_CACHE_ROOT", str(_f3_cache_link)
        ):
            try:
                _f3_cache_fd = _sandbox.open_runner_module_cache_fd()
                os.close(_f3_cache_fd)
                _f3_cache_link_refused = False
            except OSError:
                _f3_cache_link_refused = True
        _f3_cache_parent_link = _f3_cache_root / "cache-parent"
        _f3_cache_parent_link.symlink_to(
            _f3_cache_target, target_is_directory=True
        )
        with _sandbox_mock.patch.object(
            _sandbox, "GO_MODULE_CACHE_ROOT", str(_f3_cache_parent_link / "mod")
        ):
            try:
                _f3_cache_fd = _sandbox.open_runner_module_cache_fd()
                os.close(_f3_cache_fd)
                _f3_cache_parent_link_refused = False
            except OSError:
                _f3_cache_parent_link_refused = True
    assert _f3_cache_link_refused and _f3_cache_parent_link_refused, "F3 runner refuses symlinks in the Go module-cache authority path"
    with _sandbox_mock.patch.object(_sandbox, "RUNNER_FILE_BYTES", 8):
        try:
            _sandbox._snapshot_memfd(b"123456789")
            _f3_oversized_memfd_refused = False
        except ValueError:
            _f3_oversized_memfd_refused = True
    assert _f3_oversized_memfd_refused, "F3 snapshot creation enforces the file-size ceiling"

    async def _f3_snapshot_spawn_contract():
        observed = {}

        def invented_module_cache_fd():
            return os.open(
                "/tmp", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
            )

        async def successful_spawn(*args, **kwargs):
            fd = kwargs["pass_fds"][0]
            observed["fd"] = fd
            observed["module_cache_fd"] = kwargs["pass_fds"][1]
            observed["mode"] = os.fstat(fd).st_mode & 0o777
            observed["argv"] = list(args)
            observed["new_session"] = kwargs.get("start_new_session")
            observed["env"] = kwargs["env"]
            return _types.SimpleNamespace(pid=999, stdout=None, stderr=None)

        with _sandbox_mock.patch.object(_sandbox, "require_sandbox_runtime"), \
                _sandbox_mock.patch.object(_sandbox, "require_user_scope_runtime"), \
                _sandbox_mock.patch.object(
                    _sandbox, "open_runner_module_cache_fd",
                    side_effect=invented_module_cache_fd,
                ), \
                _sandbox_mock.patch.object(
                    _sandbox, "_systemd_no_expand_option", return_value=()
                ), \
                _sandbox_mock.patch.object(
                    _sandbox.asyncio, "create_subprocess_exec",
                    side_effect=successful_spawn,
                ):
            await _sandbox.spawn_sandboxed(
                "lesson-runner", _f3_bundle, ["python3", f"{_sandbox.RUNNER_WORKDIR}/main.py"],
                bundle_root=_f3_root, private_root=_f3_private,
                stdin=subprocess.DEVNULL, stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE, env=_runner.RUNNER_ENV,
                snapshot=b"print('invented')\n", snapshot_name="main.py",
                runner_wall_seconds=30, runner_scope_unit="ephemeris-runner-test",
            )
        try:
            os.fstat(observed["fd"])
            observed["closed_success"] = False
        except OSError:
            observed["closed_success"] = True
        try:
            os.fstat(observed["module_cache_fd"])
            observed["cache_closed_success"] = False
        except OSError:
            observed["cache_closed_success"] = True

        failed_fd = {}

        async def failed_spawn(*args, **kwargs):
            failed_fd["fd"] = kwargs["pass_fds"][0]
            raise OSError("invented spawn refusal")

        with _sandbox_mock.patch.object(_sandbox, "require_sandbox_runtime"), \
                _sandbox_mock.patch.object(_sandbox, "require_user_scope_runtime"), \
                _sandbox_mock.patch.object(
                    _sandbox, "open_runner_module_cache_fd",
                    side_effect=invented_module_cache_fd,
                ), \
                _sandbox_mock.patch.object(
                    _sandbox, "_systemd_no_expand_option", return_value=()
                ), \
                _sandbox_mock.patch.object(
                    _sandbox.asyncio, "create_subprocess_exec",
                    side_effect=failed_spawn,
                ):
            try:
                await _sandbox.spawn_sandboxed(
                    "lesson-runner", _f3_bundle, ["python3", f"{_sandbox.RUNNER_WORKDIR}/main.py"],
                    bundle_root=_f3_root, private_root=_f3_private,
                    env=_runner.RUNNER_ENV, snapshot=b"invented",
                    snapshot_name="main.py", runner_wall_seconds=30,
                    runner_scope_unit="ephemeris-runner-test",
                )
            except _sandbox.SandboxSpawnError:
                pass
        try:
            os.fstat(failed_fd["fd"])
            observed["closed_failure"] = False
        except OSError:
            observed["closed_failure"] = True
        return observed

    _f3_snapshot_spawn = _asyncio.run(_f3_snapshot_spawn_contract())

    async def _f3_symlink_authority_contract():
        with tempfile.TemporaryDirectory(
            prefix="ephemeris-f3-symlink-", dir="/tmp"
        ) as raw:
            physical = Path(raw) / "physical"
            bundle = physical / "lessons" / "invented-bundle"
            bundle.mkdir(parents=True)
            lexical = Path(raw) / "lexical"
            lexical.symlink_to(physical, target_is_directory=True)
            with _sandbox_mock.patch.object(_sandbox, "require_sandbox_runtime"), \
                    _sandbox_mock.patch.object(
                        _sandbox, "require_user_scope_runtime"
                    ), _sandbox_mock.patch.object(
                        _sandbox.asyncio, "create_subprocess_exec"
                    ) as spawn:
                try:
                    await _sandbox.spawn_sandboxed(
                        "lesson-runner",
                        lexical / "lessons" / "invented-bundle",
                        ["/usr/bin/python3", f"{_sandbox.RUNNER_WORKDIR}/main.py"],
                        bundle_root=lexical / "lessons",
                        private_root=lexical,
                        env=_runner.RUNNER_ENV,
                        snapshot=b"print('invented')\n",
                        snapshot_name="main.py",
                        runner_wall_seconds=30,
                        runner_scope_unit="ephemeris-runner-symlink-test",
                    )
                    return False
                except _sandbox.SandboxSpawnError:
                    return spawn.call_count == 0

    _f3_symlink_authority_refused = _asyncio.run(
        _f3_symlink_authority_contract()
    )
    _f3_kill_job = _types.SimpleNamespace(
        scope_unit="ephemeris-runner-invented", process=_types.SimpleNamespace(pid=778899)
    )
    with _sandbox_mock.patch.object(_runner.subprocess, "run") as _systemctl_kill, \
            _sandbox_mock.patch.object(_runner.os, "killpg") as _killpg:
        _runner.RunnerService._kill_tree(_f3_kill_job)
    _f3_scope_kill = (
        _systemctl_kill.call_args.args[0] == [
            _sandbox.SYSTEMCTL, "--user", "kill", "--kill-whom=all",
            "--signal=SIGKILL", "ephemeris-runner-invented.scope",
        ]
        and _killpg.call_args.args == (778899, _runner.signal.SIGKILL)
    )
    assert (
        _f3_snapshot_spawn["mode"] == 0o444
        and _f3_snapshot_spawn["closed_success"]
        and _f3_snapshot_spawn["cache_closed_success"]
        and _f3_snapshot_spawn["closed_failure"]
        and _f3_snapshot_spawn["new_session"] is True
    ), "F3 snapshot fd is 0444, passed once, and closed on success/failure"
    assert _f3_symlink_authority_refused, "F3 runner refuses symlinked bundle/private authorities before spawn"
    assert (
        _f3_snapshot_spawn["argv"][:len(_sandbox.RUNNER_SCOPE_PREFIX)]
            == list(_sandbox.RUNNER_SCOPE_PREFIX)
        and "--unit=ephemeris-runner-test" in _f3_snapshot_spawn["argv"]
        and "--property=RuntimeMaxSec=35s" in _f3_snapshot_spawn["argv"]
        and "--property=KillMode=control-group" in _f3_snapshot_spawn["argv"]
        and _f3_scope_kill
        and "--clearenv" in _f3_snapshot_spawn["argv"]
        and ["--setenv", "PWD", _sandbox.RUNNER_WORKDIR]
            in [_f3_snapshot_spawn["argv"][i:i + 3]
                for i in range(len(_f3_snapshot_spawn["argv"]) - 2)]
        and set(_f3_snapshot_spawn["env"]) <= {
            *set(_runner.RUNNER_ENV), "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
        }
    ), "F3 spawn is scope-wrapped and clears wrapper-only environment in bwrap"

    with _sandbox_mock.patch.object(
            _resource_mod, "getrlimit",
            return_value=(0, _resource_mod.RLIM_INFINITY)), \
            _sandbox_mock.patch.object(_resource_mod, "setrlimit") as _setlimit:
        _sandbox.apply_profile_rlimits(
            "lesson-runner", runner_wall_seconds=60
        )
    _f3_limit_calls = dict(call.args for call in _setlimit.call_args_list)
    assert (
        _f3_limit_calls == {
            _resource_mod.RLIMIT_CPU: (60, 60),
            _resource_mod.RLIMIT_AS: (
                _sandbox.RUNNER_ADDRESS_SPACE_BYTES,
                _sandbox.RUNNER_ADDRESS_SPACE_BYTES,
            ),
            _resource_mod.RLIMIT_NOFILE: (256, 256),
            _resource_mod.RLIMIT_NPROC: (
                _sandbox.RUNNER_NPROC, _sandbox.RUNNER_NPROC,
            ),
            _resource_mod.RLIMIT_FSIZE: (
                _sandbox.RUNNER_FILE_BYTES, _sandbox.RUNNER_FILE_BYTES,
            ),
        }
    ), "F3 runner preexec applies CPU/AS/NOFILE/NPROC/FSIZE backstops"

    _runner._cached_runner_health.cache_clear()
    with _sandbox_mock.patch.object(_runner.sandbox, "require_sandbox_runtime"), \
            _sandbox_mock.patch.object(_runner.sandbox, "require_user_scope_runtime") as _scopeprobe, \
            _sandbox_mock.patch.object(_runner, "_probe_ro_bind_data", return_value="") as _roprobe, \
            _sandbox_mock.patch.object(_runner, "_probe_go_module_cache", return_value="") as _cacheprobe, \
            _sandbox_mock.patch.object(_runner, "_probe_result", return_value="") as _allprobe:
        _f3_health_a = _runner.runner_health()
        _f3_health_b = _runner.runner_health()
    assert (
        _f3_health_a.available and _f3_health_b.available
        and _scopeprobe.call_count == 1 and _roprobe.call_count == 1
        and _cacheprobe.call_count == 1 and _allprobe.call_count == 2
    ), "F3 health probes bwrap/ro-bind-data/scope/tools once per process"

    _runner._cached_runner_health.cache_clear()
    _f3_health_workers = 4
    _f3_health_gate = threading.Barrier(_f3_health_workers)
    _f3_health_started = threading.Event()
    _f3_health_release = threading.Event()
    _f3_health_entries = []
    _f3_health_results = []

    def _f3_blocking_ro_probe():
        _f3_health_entries.append(threading.get_ident())
        _f3_health_started.set()
        _f3_health_release.wait(timeout=2)
        return ""

    def _f3_health_worker():
        _f3_health_gate.wait(timeout=2)
        _f3_health_results.append(_runner.runner_health())

    with _sandbox_mock.patch.object(_runner.sandbox, "require_sandbox_runtime"), \
            _sandbox_mock.patch.object(_runner.sandbox, "require_user_scope_runtime"), \
            _sandbox_mock.patch.object(
                _runner, "_probe_ro_bind_data", side_effect=_f3_blocking_ro_probe
            ), _sandbox_mock.patch.object(
                _runner, "_probe_go_module_cache", return_value=""
            ), _sandbox_mock.patch.object(_runner, "_probe_result", return_value=""):
        _f3_health_threads = [
            threading.Thread(target=_f3_health_worker)
            for _ in range(_f3_health_workers)
        ]
        for _thread in _f3_health_threads:
            _thread.start()
        _f3_health_started.wait(timeout=1)
        _time.sleep(0.05)
        _f3_health_release.set()
        for _thread in _f3_health_threads:
            _thread.join(timeout=2)
    assert (
        len(_f3_health_entries) == 1
        and len(_f3_health_results) == _f3_health_workers
        and all(result.available for result in _f3_health_results)
    ), "F3 concurrent cold health callers share one process-lifetime probe"
    _runner._cached_runner_health.cache_clear()
    with _sandbox_mock.patch.object(_runner.sandbox, "require_sandbox_runtime"), \
            _sandbox_mock.patch.object(_runner, "_probe_ro_bind_data", return_value="unsupported"):
        try:
            _runner.require_runner_health()
            _f3_health_refusal = False
        except _runner.RunnerUnavailableError as exc:
            _f3_health_refusal = "unsupported" in str(exc)
    _runner._cached_runner_health.cache_clear()
    with _sandbox_mock.patch.object(_runner.sandbox, "require_sandbox_runtime"), \
            _sandbox_mock.patch.object(_runner, "_probe_ro_bind_data", return_value=""), \
            _sandbox_mock.patch.object(_runner.sandbox, "require_user_scope_runtime"), \
            _sandbox_mock.patch.object(_runner, "_probe_result", return_value=""), \
            _sandbox_mock.patch.object(
                _runner, "_probe_go_module_cache", return_value="module cache absent"
            ):
        try:
            _runner.require_runner_health()
            _f3_cache_refusal = False
        except _runner.RunnerUnavailableError as exc:
            _f3_cache_refusal = "module cache absent" in str(exc)
    assert _f3_health_refusal and _f3_cache_refusal, "F3 unhealthy runner refuses visibly with no degraded spawn"
    _runner._cached_runner_health.cache_clear()

    class _F3Process:
        _next_pid = 900000

        def __init__(self):
            type(self)._next_pid += 1
            self.pid = type(self)._next_pid
            self.stdout = _asyncio.StreamReader()
            self.stderr = _asyncio.StreamReader()
            self.returncode = None
            self._result = _asyncio.get_running_loop().create_future()

        async def wait(self):
            self.returncode = await self._result
            return self.returncode

        def finish(self, returncode=0):
            self.stdout.feed_eof()
            self.stderr.feed_eof()
            if not self._result.done():
                self._result.set_result(returncode)

    async def _f3_service_contracts():
        def req(
            lesson="lesson-a", key="key-a", block="blk_demo",
            private_root="/tmp/private",
            snapshot=b"print('invented')\n",
        ):
            return _runner.RunnerRequest(
                lesson, block, "sha256:invented", key,
                "python-script-v1", "attempts/blk_demo/main.py",
                snapshot, "/tmp/private/lessons/demo",
                "/tmp/private/lessons", private_root,
            )

        result = {}
        processes = []

        async def spawn(_job):
            process = _F3Process()
            processes.append(process)
            return process

        service = _runner.RunnerService(spawn_hook=spawn, health_hook=lambda: None)
        with _sandbox_mock.patch.object(_runner.sandbox, "RUNNER_FILE_BYTES", 8):
            try:
                await service.admit(req(key="oversized", snapshot=b"123456789"))
                result["oversized_snapshot"] = False
            except _runner.SnapshotTooLargeError:
                result["oversized_snapshot"] = (
                    not processes and service.active_total == 0
                )
        try:
            await service.admit(req(key="missing-private", private_root=None))
            result["missing_private"] = False
        except _runner.RunnerUnavailableError:
            result["missing_private"] = not processes and service.active_total == 0
        admission = await service.admit(req())
        result["starting"] = admission.job.state == _runner.STARTING
        await _asyncio.sleep(0)
        await _asyncio.sleep(0)
        result["running"] = admission.job.state == _runner.RUNNING
        processes[0].stdout.feed_data(b"split:\xe2")
        await _asyncio.sleep(0)
        processes[0].stdout.feed_data(b"\x82\xac\n")
        processes[0].finish(0)
        finished = await service.wait(admission.job.job_id)
        result["normal"] = (
            finished.state == _runner.FINISHED
            and finished.cause == "exit" and finished.exit_code == 0
            and "split:€\n" == "".join(
                event["text"] for event in finished.events
                if event["event"] == "output"
            )
            and sum(event["event"] == "exit" for event in finished.events) == 1
        )

        health_started = threading.Event()
        health_release = threading.Event()
        health_threads = []
        health_processes = []

        def blocking_health():
            health_threads.append(threading.get_ident())
            health_started.set()
            health_release.wait(timeout=2)

        async def health_spawn(_job):
            process = _F3Process()
            health_processes.append(process)
            return process

        health_service = _runner.RunnerService(
            spawn_hook=health_spawn, health_hook=blocking_health
        )
        loop_thread = threading.get_ident()
        health_admit_task = _asyncio.create_task(
            health_service.admit(req("health", "health-key"))
        )
        for _ in range(100):
            if health_started.is_set():
                break
            await _asyncio.sleep(0.01)
        try:
            health_lookup_responsive = await _asyncio.wait_for(
                health_service.get("invented-missing"), timeout=0.2
            ) is None
        except _asyncio.TimeoutError:
            health_lookup_responsive = False
        finally:
            health_release.set()
        health_admission = await health_admit_task
        for _ in range(100):
            if health_processes:
                break
            await _asyncio.sleep(0.01)
        health_processes[0].finish(0)
        await health_service.wait(health_admission.job.job_id)
        result["health_off_loop"] = (
            health_started.is_set()
            and health_threads == [health_threads[0]]
            and health_threads[0] != loop_thread
            and health_lookup_responsive
        )

        natural_processes = []

        async def natural_spawn(_job):
            process = _F3Process()
            natural_processes.append(process)
            return process

        natural_service = _runner.RunnerService(
            spawn_hook=natural_spawn, health_hook=lambda: None
        )
        natural = (
            await natural_service.admit(req("natural", "natural-key"))
        ).job
        for _ in range(100):
            if natural_processes:
                break
            await _asyncio.sleep(0.01)
        natural_processes[0].returncode = 0
        with _sandbox_mock.patch.object(
                _runner.RunnerService, "_kill_tree") as natural_kill:
            natural_cancelled = await natural_service.cancel(natural.job_id)
        natural_processes[0].finish(0)
        natural = await natural_service.wait(natural.job_id)
        result["natural_exit_beats_cancel"] = (
            not natural_cancelled and natural_kill.call_count == 0
            and natural.cause == "exit" and natural.exit_code == 0
        )

        cancel_processes = []

        async def cancel_spawn(_job):
            process = _F3Process()
            cancel_processes.append(process)
            return process

        cancel_service = _runner.RunnerService(
            spawn_hook=cancel_spawn, health_hook=lambda: None
        )
        cancelled = (await cancel_service.admit(req("lesson-c", "key-c"))).job
        await _asyncio.sleep(0)
        await _asyncio.sleep(0)
        kill_started = threading.Event()
        kill_release = threading.Event()
        kill_threads = []

        def blocking_kill(_job):
            kill_threads.append(threading.get_ident())
            kill_started.set()
            kill_release.wait(timeout=2)

        with _sandbox_mock.patch.object(
                _runner.RunnerService, "_kill_tree",
                side_effect=blocking_kill) as kill:
            cancel_task = _asyncio.create_task(
                cancel_service.cancel(cancelled.job_id)
            )
            for _ in range(100):
                if kill_started.is_set():
                    break
                await _asyncio.sleep(0.01)
            try:
                cancel_lookup_responsive = await _asyncio.wait_for(
                    cancel_service.get(cancelled.job_id), timeout=0.2
                ) is cancelled
            except _asyncio.TimeoutError:
                cancel_lookup_responsive = False
            finally:
                kill_release.set()
            first = await cancel_task
            second = await cancel_service.cancel(cancelled.job_id)
        cancel_processes[0].finish(-9)
        cancelled = await cancel_service.wait(cancelled.job_id)
        result["first_cause_release"] = (
            first and not second and cancelled.cause == "cancelled"
            and cancelled.reservation_released
            and cancel_service.active_total == 0 and kill.call_count == 1
            and sum(event["event"] == "exit" for event in cancelled.events) == 1
        )
        result["cancel_off_loop"] = (
            kill_started.is_set() and kill_threads == [kill_threads[0]]
            and kill_threads[0] != loop_thread and cancel_lookup_responsive
        )

        async def broken_spawn(_job):
            raise OSError("invented spawn failure")

        broken_service = _runner.RunnerService(
            spawn_hook=broken_spawn, health_hook=lambda: None
        )
        broken = (await broken_service.admit(req("lesson-d", "key-d"))).job
        broken = await broken_service.wait(broken.job_id)
        result["spawn_failure"] = (
            broken.cause == "spawn-failed" and broken.state == _runner.FINISHED
            and broken.reservation_released and broken_service.active_total == 0
        )

        race_processes = []

        async def race_spawn(_job):
            process = _F3Process()
            race_processes.append(process)
            return process

        race_charges = []
        race_refunds = []

        def race_rate(lesson):
            token = (lesson, len(race_charges))
            race_charges.append(token)
            return token

        race_service = _runner.RunnerService(
            spawn_hook=race_spawn, health_hook=lambda: None,
            rate_hook=race_rate,
            rate_refund_hook=lambda lesson, token: race_refunds.append((lesson, token)),
        )
        per_lesson = await _asyncio.gather(
            race_service.admit(req("same", "key-1")),
            race_service.admit(req("same", "key-2")),
            return_exceptions=True,
        )
        result["per_lesson_race"] = (
            sum(isinstance(item, _runner.Admission) for item in per_lesson) == 1
            and sum(isinstance(item, _runner.LessonCapacityError) for item in per_lesson) == 1
            and len(race_charges) == 2 and len(race_refunds) == 1
        )
        await _asyncio.sleep(0)
        for process in race_processes:
            process.finish(0)
        for item in per_lesson:
            if isinstance(item, _runner.Admission):
                await race_service.wait(item.job.job_id)

        global_processes = []

        async def global_spawn(_job):
            process = _F3Process()
            global_processes.append(process)
            return process

        global_service = _runner.RunnerService(
            spawn_hook=global_spawn, health_hook=lambda: None
        )
        global_results = await _asyncio.gather(
            *(global_service.admit(req(f"lesson-{i}", f"key-{i}")) for i in range(3)),
            return_exceptions=True,
        )
        result["global_race"] = (
            sum(isinstance(item, _runner.Admission) for item in global_results) == 2
            and sum(isinstance(item, _runner.GlobalCapacityError) for item in global_results) == 1
        )
        await _asyncio.sleep(0)
        for process in global_processes:
            process.finish(0)
        for item in global_results:
            if isinstance(item, _runner.Admission):
                await global_service.wait(item.job.job_id)

        rate_calls = []
        replay_processes = []

        async def replay_spawn(_job):
            process = _F3Process()
            replay_processes.append(process)
            return process

        replay_service = _runner.RunnerService(
            spawn_hook=replay_spawn, health_hook=lambda: None,
            rate_hook=lambda lesson: rate_calls.append(lesson) or True,
        )
        same_request = req("replay", "same-key")
        replay_results = await _asyncio.gather(
            replay_service.admit(same_request), replay_service.admit(same_request)
        )
        result["idempotency_first"] = (
            replay_results[0].job is replay_results[1].job
            and {item.replayed for item in replay_results} == {False, True}
            and len(rate_calls) == 1 and replay_service.active_total == 1
        )
        await _asyncio.sleep(0)
        replay_processes[0].finish(0)
        await replay_service.wait(replay_results[0].job.job_id)

        retention_service = _runner.RunnerService(
            spawn_hook=broken_spawn, health_hook=lambda: None,
            max_terminal_jobs=1,
        )
        old = (await retention_service.admit(req("old", "old-key"))).job
        await retention_service.wait(old.job_id)
        new = (await retention_service.admit(req("new", "new-key"))).job
        await retention_service.wait(new.job_id)
        try:
            await retention_service.preflight(
                "old", "old-key", "blk_demo", "sha256:invented"
            )
            old_replay_missing = False
        except _runner.JobMissingError:
            old_replay_missing = True
        result["retention"] = (
            await retention_service.get(old.job_id) is None
            and await retention_service.get(new.job_id) is not None
            and old_replay_missing
        )
        first_reader = await retention_service.attach_reader(new.job_id)
        second_reader = await retention_service.attach_reader(new.job_id)
        try:
            await retention_service.attach_reader(new.job_id)
            third_reader_refused = False
        except _runner.ReaderCapacityError:
            third_reader_refused = True
        new.finished_monotonic = (
            _time.monotonic() - _runner.TERMINAL_RETENTION_SECONDS - 1
        )
        replay_key = ("new", "new-key")
        saved_replay = retention_service._idempotency[replay_key]
        retention_service._idempotency[replay_key] = (
            saved_replay[0], saved_replay[1], saved_replay[2],
            _time.monotonic() - 1,
        )
        attached_replay = await retention_service.preflight(
            "new", "new-key", "blk_demo", "sha256:invented"
        )
        later = (
            await retention_service.admit(req("later", "later-key"))
        ).job
        await retention_service.wait(later.job_id)
        attached_survived_pruning = (
            await retention_service.get(new.job_id) is new
        )
        await retention_service.detach_reader(first_reader)
        await retention_service.detach_reader(second_reader)
        detached_expired = await retention_service.get(new.job_id) is None
        detached_replay = await retention_service.preflight(
            "new", "new-key", "blk_demo", "sha256:invented"
        )
        result["reader_cap"] = (
            third_reader_refused and new.reader_count == 0
        )
        result["reader_retention"] = (
            isinstance(attached_replay, _runner.Admission)
            and attached_replay.job is new and attached_replay.replayed
            and attached_survived_pruning and detached_expired
            and isinstance(detached_replay, _runner.AdmissionPermit)
        )

        reader_bound_service = _runner.RunnerService(
            health_hook=lambda: None, max_terminal_jobs=1
        )
        reader_job_a = _runner.RunnerJob(
            "reader-job-a", req("reader-a", "reader-key-a"),
            _runner_registry.RUNNER_REGISTRY["python-script-v1"],
            state=_runner.RUNNING,
        )
        reader_job_b = _runner.RunnerJob(
            "reader-job-b", req("reader-b", "reader-key-b"),
            _runner_registry.RUNNER_REGISTRY["python-script-v1"],
            state=_runner.RUNNING,
        )
        reader_bound_service._jobs.update({
            reader_job_a.job_id: reader_job_a,
            reader_job_b.job_id: reader_job_b,
        })
        reader_lease_a = await reader_bound_service.attach_reader(
            reader_job_a.job_id
        )
        reader_lease_a_second = await reader_bound_service.attach_reader(
            reader_job_a.job_id
        )
        try:
            await reader_bound_service.attach_reader(reader_job_b.job_id)
            distinct_reader_refused = False
        except _runner.ReaderCapacityError:
            distinct_reader_refused = True
        await reader_bound_service.detach_reader(reader_lease_a)
        await reader_bound_service.detach_reader(reader_lease_a)
        count_after_double_detach = reader_job_a.reader_count
        await reader_bound_service.detach_reader(reader_lease_a_second)
        result["reader_global_bound"] = (
            distinct_reader_refused and count_after_double_detach == 1
            and reader_job_a.reader_count == 0
        )

        notify_processes = []

        async def notify_spawn(_job):
            process = _F3Process()
            notify_processes.append(process)
            return process

        notify_service = _runner.RunnerService(
            spawn_hook=notify_spawn, health_hook=lambda: None
        )
        notify_job = (
            await notify_service.admit(req("notify", "notify-key"))
        ).job
        for _ in range(100):
            if notify_processes and notify_job.state == _runner.RUNNING:
                break
            await _asyncio.sleep(0.01)
        notify_reader_a = await notify_service.attach_reader(notify_job.job_id)
        notify_reader_b = await notify_service.attach_reader(notify_job.job_id)
        waiter_a = _asyncio.create_task(
            notify_service.wait_for_update(notify_job.job_id, 0)
        )
        waiter_b = _asyncio.create_task(
            notify_service.wait_for_update(notify_job.job_id, 0)
        )
        for _ in range(100):
            if len(notify_job._waiters) == 2:
                break
            await _asyncio.sleep(0.01)
        notify_processes[0].stdout.feed_data(b"invented wakeup\n")
        await _asyncio.wait_for(
            _asyncio.gather(waiter_a, waiter_b), timeout=1
        )
        _seen_job, output_batch, output_state = (
            await notify_service.events_after(notify_job.job_id, 0)
        )
        output_cursor = max(int(event["seq"]) for event in output_batch)
        _seen_job, empty_batch, pre_finish_state = (
            await notify_service.events_after(notify_job.job_id, output_cursor)
        )
        notify_processes[0].finish(0)
        await notify_service.wait(notify_job.job_id)
        await _asyncio.wait_for(
            notify_service.wait_for_update(notify_job.job_id, output_cursor),
            timeout=0.2,
        )
        _seen_job, terminal_batch, terminal_state = (
            await notify_service.events_after(notify_job.job_id, output_cursor)
        )
        await notify_service.detach_reader(notify_reader_a)
        await notify_service.detach_reader(notify_reader_b)
        result["reader_notifications"] = (
            len(notify_job._waiters) == 0
            and output_state == _runner.RUNNING
            and any(event["event"] == "output" for event in output_batch)
            and empty_batch == () and pre_finish_state == _runner.RUNNING
            and terminal_state == _runner.FINISHED
            and [event["event"] for event in terminal_batch] == ["exit"]
        )

        shutdown_processes = []

        async def shutdown_spawn(_job):
            process = _F3Process()
            shutdown_processes.append(process)
            return process

        shutdown_service = _runner.RunnerService(
            spawn_hook=shutdown_spawn, health_hook=lambda: None
        )
        shutdown_job = (await shutdown_service.admit(req("shutdown", "shutdown-key"))).job
        await _asyncio.sleep(0)
        await _asyncio.sleep(0)
        with _sandbox_mock.patch.object(_runner.RunnerService, "_kill_tree"):
            shutdown_task = _asyncio.create_task(shutdown_service.shutdown())
            await _asyncio.sleep(0)
            shutdown_processes[0].finish(-9)
            await shutdown_task
        result["shutdown"] = (
            shutdown_job.cause == "shutdown"
            and shutdown_job.state == _runner.FINISHED
            and shutdown_job.reservation_released
            and shutdown_service.active_total == 0
        )
        return result

    _f3_service = _asyncio.run(_f3_service_contracts())
    assert (
        _f3_service.get("starting") and _f3_service.get("running")
        and _f3_service.get("normal")
    ), f"F3 state machine reaches FINISHED only after reap/EOF with split UTF-8 intact: {str(_f3_service)}"
    assert (
        _f3_service.get("missing_private")
        and _f3_service.get("oversized_snapshot")
    ), f"F3 admission refuses a missing private authority before spawn: {str(_f3_service)}"
    assert (
        _f3_service.get("first_cause_release")
          and _f3_service.get("spawn_failure")
    ), f"F3 first terminal cause wins and releases capacity exactly once: {str(_f3_service)}"
    assert _f3_service.get("cancel_off_loop"), f"F3 cancel tree kills leave the event loop and service lock responsive: {str(_f3_service)}"
    assert _f3_service.get("health_off_loop"), f"F3 cold health probes leave the event loop and service lock responsive: {str(_f3_service)}"
    assert _f3_service.get("natural_exit_beats_cancel"), f"F3 a reaped natural exit cannot be relabelled by late cancel: {str(_f3_service)}"
    assert (
        _f3_service.get("per_lesson_race")
        and _f3_service.get("global_race")
    ), f"F3 one-lock admission closes races and refunds busy rate charges: {str(_f3_service)}"
    assert (
        _f3_service.get("idempotency_first")
        and _f3_service.get("retention")
        and _f3_service.get("reader_cap")
    ), f"F3 idempotency precedes rate/capacity and terminal retention is bounded: {str(_f3_service)}"
    assert _f3_service.get("shutdown"), f"F3 shutdown stops jobs through the same exact-release path: {str(_f3_service)}"

    async def _f4_terminal_cause_matrix():
        results = {}
        for cause in sorted(_runner.TERMINAL_CAUSES):
            service = _runner.RunnerService(health_hook=lambda: None)
            request = _runner.RunnerRequest(
                lesson_key=f"lesson-{cause}", block_id="blk_matrix",
                file_rev="sha256:invented", idempotency_key=f"key-{cause}",
                runner_id="python-script-v1", filename="main.py",
                snapshot=b"print('invented')\n",
                bundle_dir="/tmp/private/lessons/demo",
                bundle_root="/tmp/private/lessons",
                private_root="/tmp/private",
            )
            job = _runner.RunnerJob(
                f"job-{cause}", request,
                _runner_registry.RUNNER_REGISTRY["python-script-v1"],
            )
            service._jobs[job.job_id] = job
            service._active_by_lesson[request.lesson_key] = 1
            service._active_total = 1
            async with service._lock:
                first = service._begin_termination_locked(job, cause)
                second = service._begin_termination_locked(job, cause)
                if cause == "exit":
                    job.exit_code = 0
                elif cause == "signal":
                    job.signal = 15
                job.process_reaped = True
                job.stdout_eof = True
                job.stderr_eof = True
                service._finish_locked(job)
                service._finish_locked(job)
            exits = [event for event in job.events if event["event"] == "exit"]
            results[cause] = (
                first and not second and job.state == _runner.FINISHED
                and job.reservation_released and service.active_total == 0
                and len(exits) == 1 and exits[0]["cause"] == cause
                and job.event_attempted.is_set()
            )
        return results

    _f4_causes = _asyncio.run(_f4_terminal_cause_matrix())
    assert (
        set(_f4_causes) == set(_runner.TERMINAL_CAUSES)
        and all(_f4_causes.values())
    ), f"F4 every terminal cause emits one exit and releases capacity once: {str(_f4_causes)}"
    assert (
        _f3_service.get("per_lesson_race")
        and _f3_service.get("global_race")
    ), f"F4 concurrent starts preserve per-lesson and global caps: {str(_f3_service)}"
    assert (
        _f3_service.get("retention")
        and _f3_service.get("reader_cap")
    ), f"F4 retention tombstones and the two-reader cap stay bounded: {str(_f3_service)}"
    assert _f3_service.get("reader_retention"), f"F4 retention pruning preserves attached streams until detach: {str(_f3_service)}"
    assert _f3_service.get("reader_global_bound"), f"F4 reader leases are idempotent and distinct-job protection is bounded: {str(_f3_service)}"
    assert _f3_service.get("reader_notifications"), f"F4 both SSE readers wake and a raced terminal event is drained: {str(_f3_service)}"

    async def _f4_disconnect_before_body_contract():
        from starlette.requests import Request as _StarletteRequest
        from app.routers.learn import stream_lesson_run as _stream_lesson_run

        service = _runner.RunnerService(health_hook=lambda: None)
        request_data = _runner.RunnerRequest(
            lesson_key="invented-disconnect-lesson", block_id="blk_demo",
            file_rev="sha256:invented", idempotency_key="invented-disconnect-key",
            runner_id="python-script-v1", filename="main.py",
            snapshot=b"print('invented')\n", bundle_dir="/tmp/invented-bundle",
            bundle_root="/tmp", private_root="/tmp/invented-private",
        )
        job = _runner.RunnerJob(
            "invented-disconnect-job", request_data,
            _runner_registry.RUNNER_REGISTRY["python-script-v1"],
            state=_runner.RUNNING,
        )
        service._jobs[job.job_id] = job
        original_service = app.state.runner_service
        app.state.runner_service = service
        scope = {
            "type": "http", "asgi": {"version": "3.0"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": f"/learn/runs/{job.job_id}/stream",
            "raw_path": f"/learn/runs/{job.job_id}/stream".encode(),
            "query_string": b"", "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8765), "app": app,
        }

        async def disconnected():
            await _asyncio.sleep(0)
            return {"type": "http.disconnect"}

        never_send = _asyncio.Event()

        async def blocked_send(_message):
            await never_send.wait()

        try:
            response = await _stream_lesson_run(
                _StarletteRequest(scope, receive=disconnected), job.job_id
            )
            await response(scope, disconnected, blocked_send)
            return job.reader_count
        finally:
            app.state.runner_service = original_service

    _f4_disconnect_readers = _asyncio.run(_f4_disconnect_before_body_contract())
    assert _f4_disconnect_readers == 0, f"F4 disconnect before SSE body iteration releases its reader lease: {str(_f4_disconnect_readers)}"

    try:
        _runner.require_runner_health()
        _f3_host_runtime = True
        _f3_runtime_detail = ""
    except _runner.RunnerUnavailableError as exc:
        _f3_host_runtime = False
        _f3_runtime_detail = str(exc)
    if _f3_host_runtime:
        _f3_probe_run = subprocess.run(
            [sys.executable, "scripts/probe_runner.py"],
            cwd=ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            timeout=180,
        )
        try:
            _f3_probe = json.loads(_f3_probe_run.stdout)
        except (TypeError, ValueError):
            _f3_probe = {}
        _f3_probe_extra = _f3_probe_run.stderr.strip() or _f3_probe_run.stdout.strip()
        assert (
            _f3_probe_run.returncode == 0
            and _f3_probe.get("success", {}).get("exit_code") == 0
            and _f3_probe.get("syntax_error", {}).get("stderr_has_syntax_error") is True
            and _f3_probe.get("timeout", {}).get("cause") == "timeout"
            and _f3_probe.get("file_limit", {}).get("failed") is True
        ), f"F3 host matrix: success, syntax error, timeout, and file backstop: {_f3_probe_extra}"
        assert (
            _f3_probe.get("output_overflow") == {
                "cause": "output-limit", "output_bytes": 1024 * 1024,
                "state": "FINISHED", "truncated": True,
            }
        ), f"F3 host matrix: raw-byte overflow kills at exactly 1 MiB: {_f3_probe_extra}"
        assert (
            _f3_probe.get("descendant_cleanup", {}).get("both_eof") is True
            and _f3_probe.get("descendant_cleanup", {}).get("cause") == "cancelled"
            and _f3_probe.get("shutdown", {}).get("cause") == "shutdown"
            and _f3_probe.get("shutdown", {}).get("active_total") == 0
        ), f"F3 host matrix: descendant cleanup and shutdown both reap to EOF: {_f3_probe_extra}"
        _f3_isolation = _f3_probe.get("isolation", {})
        assert (
            all(_f3_isolation.get(name) is True for name in (
                "repo_absent", "private_sentinel_absent", "other_bundle_absent",
                "run_empty", "network_absent",
            ))
        ), f"F3 host isolation: repo/private/other bundles/run/network are absent: {_f3_probe_extra}"
        assert (
            all(_f3_isolation.get(name) is True for name in (
                "bundle_readable", "bundle_read_only", "module_cache_read_only",
                "scratch_writable", "gocache_writable",
            ))
            and _f3_isolation.get("snapshot_mode") == "0o444"
            and _f3_isolation.get("home_entries") == [".cache", "go"]
            and set(_f3_isolation.get("runner_env", ())) == set(_runner.RUNNER_ENV)
        ), f"F3 host isolation: bundle/module cache ro; scratch/GOCACHE rw; snapshot 0444: {_f3_probe_extra}"
        assert (
            _f3_probe.get("cold_go", {}).get("exit_code") == 0
            and _f3_probe.get("cold_go", {}).get("warm_child_reported") is True
            and _f3_probe.get("cold_go", {}).get("wall_ms", 60001) < 60000
            and _f3_probe.get("go_repeated_and_changed", {}).get("repeat_ok") is True
            and _f3_probe.get("go_repeated_and_changed", {}).get("changed_source_observed") is True
            and _f3_probe.get("go_compile_error", {}).get("stderr_has_undefined") is True
        ), f"F3 cold Go and warm-within-job/repeat/change/compile-error matrix passes: {_f3_probe_extra}"
    else:
        assert True, f"F3 host matrix skipped when full runner runtime is unavailable: {_f3_runtime_detail}"

    suite_state.update({
        name: value for name, value in locals().items()
        if name not in {"client", "suite_state"}
    })
