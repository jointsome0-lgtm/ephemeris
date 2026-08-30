"""Role selection and fixed runner verification."""
from __future__ import annotations

import json
import os
import stat as stat_module
import subprocess
import threading
from pathlib import Path

from conftest import ROOT


async def _finished(service, job_id):
    job = await service.get(job_id)
    await job.finished.wait()
    return job



def test_role_runner(client, suite_state):
    from app.main import app

    c = client
    _E2Sock = suite_state["_E2Sock"]
    _asyncio = suite_state["_asyncio"]
    _lt = suite_state["_lt"]
    _mock = suite_state["_mock"]
    _terminal = suite_state["_terminal"]
    _time = suite_state["_time"]
    _types = suite_state["_types"]
    bschema = suite_state["bschema"]
    lessons_svc = suite_state["lessons_svc"]
    ws_info = suite_state["ws_info"]
    import resource as _resource_mod
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
    assert _plain_lesson_refused, "E3 explicit plain cannot be lesson-scoped"
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
    with _mock.patch.object(_terminal, "_ws_is_trusted", return_value=True), \
            _mock.patch.object(_terminal, "_reap_idle"), \
            _mock.patch.object(_terminal, "_ensure_reaper"), \
            _mock.patch.object(
                _terminal, "_create_session",
                new=_mock.AsyncMock()) as _sid_role_create:
        _asyncio.run(_terminal._serve_ws(_sid_role_ws))
    assert (
        _selector_refusals == 2
        and _sid_role_create.call_count == 0
        and _sid_role_ws.accepted and _sid_role_ws.closed
        and b"invalid session request" in b"".join(_sid_role_ws.sent_bytes)
    ), "E3 selector validation refuses no-lesson, unknown, and sid attach"

    async def _e3_invalid_selector_at_capacity():
        with _mock.patch.object(_terminal, "_MAX_SESSIONS", 0), \
                _mock.patch.object(_terminal, "_reap_idle") as reap:
            try:
                await _terminal._create_session(_lt["slug"], "unknown")
            except _terminal._SessionRequestError:
                refused = True
            else:
                refused = False
        return refused and reap.call_count == 0

    assert _asyncio.run(_e3_invalid_selector_at_capacity()), "E3 invalid selector cannot evict a detached session at capacity"
    with _mock.patch.dict(os.environ, {
        "SSH_AUTH_SOCK": "/run/user/1000/agent.sock",
        "HOME": "/root",
        "PATH": "/root/private-bin:/usr/bin",
        "SHELL": "/root/private-shell",
        "EPHEMERIS_INVENTED_CONFIG": "leak",
    }):
        _agent_env = _terminal._child_env("lesson-agent", "/root/private-shell")
        _learner_env = _terminal._child_env("lesson-learner", "/bin/bash")
    from app import runner as _runner_toolchain
    assert (
        _agent_env.get("SSH_AUTH_SOCK") == "/run/user/1000/agent.sock"
        and _learner_env.get("SSH_AUTH_SOCK") == "/run/user/1000/agent.sock"
        and "EPHEMERIS_INVENTED_CONFIG" not in _agent_env
        and "EPHEMERIS_INVENTED_CONFIG" not in _learner_env
        and _agent_env.get("HOME") == "/root" and _learner_env.get("HOME") == "/root"
        and _agent_env.get("SHELL") == "/root/private-shell"
        and _learner_env.get("SHELL") == "/bin/bash"
        and _agent_env.get("PATH")
            == "/root/.local/bin:/usr/local/bin:/root/private-bin:/usr/bin"
        and _learner_env.get("PATH")
            == "/root/.local/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin"
        # The learner shell resolves the same toolchain the runner compiles with.
        and "/usr/local/go/bin" in _runner_toolchain.RUNNER_ENV["PATH"]
        and "PROMPT_COMMAND" not in _agent_env
        and _learner_env.get("PROMPT_COMMAND") == r"PS1='\W $ '"
    ), "E3 both lesson roles get the allowlisted service env, each with its own PATH and prompt"

    async def _e3_learner_plumbing():
        workspace = {"dir": ws_info["dir"], "slug": _lt["slug"], "title": "demo"}
        proc = _types.SimpleNamespace(returncode=0)
        with _mock.patch.object(
                _terminal, "resolve_terminal_workspace", return_value=workspace) as resolve, \
                _mock.patch.object(
                    _terminal, "prepare_terminal_workspace") as prepare, \
                _mock.patch.object(
                    _terminal, "_detect_proxy_env", return_value={}) as proxy, \
                _mock.patch.object(
                    _terminal.asyncio, "create_subprocess_exec",
                    new=_mock.AsyncMock(return_value=proc)) as spawn, \
                _mock.patch.object(_terminal._TermSession, "start"):
            session = await _terminal._create_session(
                _lt["slug"], "lesson-learner")
        call = spawn.call_args
        result = (
            resolve.call_count == 1 and prepare.call_count == 0
            and proxy.call_count == 1
            and call.args == ("/bin/bash", "-i")
            and call.kwargs["cwd"] == workspace["dir"]
            and session.role == "lesson-learner"
            and session.sid.startswith(_terminal._LEARNER_SID_PREFIX)
            and not any(name in call.kwargs["env"] for name in _terminal._PROXY_ENV_VARS)
        )
        _terminal._SESSIONS.pop(session.sid, None)
        os.close(session.master_fd)
        return result

    assert _asyncio.run(_e3_learner_plumbing()), "E3 learner spawn is bash in the bundle, briefs untouched, no proxy env"

    # --- F3: fixed runner registry, process limits, job owner ---------------
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
    _f3_compatible = bschema.read_manifest_text(json.dumps(_f3_manifest))
    _f3_manifest["blocks"][0]["file"] = "attempts/blk_demo/main.go"
    _f3_incompatible = bschema.read_manifest_text(json.dumps(_f3_manifest))
    assert (
        _f3_compatible.blocks[0]["run_enabled"] is True
        and _f3_incompatible.blocks[0]["run_enabled"] is False
        and "incompatible-runner" in _f3_incompatible.codes()
    ), "F3 manifest runner suffix gates Run but retains the editor"
    _f3_manifest["blocks"][0]["file"] = "attempts/blk_demo/main.py"
    _f3_legacy_manifest = json.loads(json.dumps(_f3_manifest))
    _f3_legacy_manifest.pop("runtime")
    _f3_legacy = bschema.read_manifest_text(json.dumps(_f3_legacy_manifest))
    _f3_rejected_manifest = json.loads(json.dumps(_f3_manifest))
    _f3_rejected_manifest.pop("lesson_uid")
    _f3_rejected = bschema.read_manifest_text(json.dumps(_f3_rejected_manifest))
    assert (
        _f3_legacy.effective_profile == bschema.PROFILE_LEGACY
        and _f3_legacy.blocks[0]["run_enabled"] is False
        and _f3_rejected.rejected
        and _f3_rejected.blocks[0]["run_enabled"] is False
    ), "F3 fail-closed manifests never grant the Run affordance"

    _f3_kill_job = _types.SimpleNamespace(process=_types.SimpleNamespace(pid=778899))
    with _mock.patch.object(_runner.os, "killpg") as _killpg:
        _runner.RunnerService._kill_tree(_f3_kill_job)
        _runner.RunnerService._kill_tree(_types.SimpleNamespace(process=None))
    assert (
        _killpg.call_count == 1
        and _killpg.call_args.args == (778899, _runner.signal.SIGKILL)
    ), "F3 kill reaches the job's whole process group and skips a job that never spawned"

    with _mock.patch.object(
            _resource_mod, "getrlimit",
            return_value=(0, _resource_mod.RLIM_INFINITY)), \
            _mock.patch.object(_resource_mod, "setrlimit") as _setlimit:
        _runner._runner_rlimits(60)()
    _f3_limit_calls = dict(call.args for call in _setlimit.call_args_list)
    assert (
        _f3_limit_calls == {
            _resource_mod.RLIMIT_CPU: (60, 60),
            _resource_mod.RLIMIT_AS: (
                _runner.RUNNER_ADDRESS_SPACE_BYTES,
                _runner.RUNNER_ADDRESS_SPACE_BYTES,
            ),
            _resource_mod.RLIMIT_NOFILE: (
                _runner.RUNNER_NOFILE, _runner.RUNNER_NOFILE,
            ),
            _resource_mod.RLIMIT_FSIZE: (
                _runner.RUNNER_FILE_BYTES, _runner.RUNNER_FILE_BYTES,
            ),
        }
    ), "F3 runner preexec applies CPU/AS/NOFILE/FSIZE backstops"

    _runner._cached_runner_health.cache_clear()
    with _mock.patch.object(_runner, "_probe_result", return_value="") as _allprobe:
        _f3_health_a = _runner.runner_health()
        _f3_health_b = _runner.runner_health()
    _f3_probed = [call.args[0] for call in _allprobe.call_args_list]
    assert (
        _f3_health_a.available and _f3_health_b.available
        and _f3_probed == [
            ["/usr/bin/python3", "--version"], ["/usr/local/go/bin/go", "version"],
        ]
    ), "F3 health probes each registry executable once per process"

    _runner._cached_runner_health.cache_clear()
    _f3_health_workers = 4
    _f3_health_gate = threading.Barrier(_f3_health_workers)
    _f3_health_started = threading.Event()
    _f3_health_release = threading.Event()
    _f3_health_entries = []
    _f3_health_results = []

    def _f3_blocking_probe(_argv, **_kw):
        _f3_health_entries.append(threading.get_ident())
        _f3_health_started.set()
        _f3_health_release.wait(timeout=2)
        return ""

    def _f3_health_worker():
        _f3_health_gate.wait(timeout=2)
        _f3_health_results.append(_runner.runner_health())

    with _mock.patch.object(_runner, "_probe_result", side_effect=_f3_blocking_probe):
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
        len(set(_f3_health_entries)) == 1
        and len(_f3_health_results) == _f3_health_workers
        and all(result.available for result in _f3_health_results)
    ), "F3 concurrent cold health callers share one process-lifetime probe"
    _runner._cached_runner_health.cache_clear()
    with _mock.patch.object(_runner, "_probe_result", return_value="unsupported"):
        try:
            _runner.require_runner_health()
            _f3_health_refusal = False
        except _runner.RunnerUnavailableError as exc:
            _f3_health_refusal = "unsupported" in str(exc)
    assert _f3_health_refusal, "F3 unhealthy runner refuses visibly with no degraded spawn"
    _runner._cached_runner_health.cache_clear()

    class _F3Process:
        # Above any real pid_max: the natural-exit path kills the process
        # group of every job, fake ones included, and must find nobody there.
        _next_pid = 2_000_000_000

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

    async def _f3_spawn_contract():
        observed = {}

        async def fake_exec(*argv, **kwargs):
            snapshot = Path(kwargs["cwd"]) / "main.py"
            observed.update(
                argv=argv, cwd=kwargs["cwd"], env=kwargs["env"],
                new_session=kwargs.get("start_new_session"),
                preexec=kwargs.get("preexec_fn"),
                mode=stat_module.S_IMODE(snapshot.lstat().st_mode),
                content=snapshot.read_bytes(),
                entries=sorted(os.listdir(kwargs["cwd"])),
            )
            process = _F3Process()
            process.finish(0)
            return process

        service = _runner.RunnerService(health_hook=lambda: None)
        request = _runner.RunnerRequest(
            "spawn", "blk_demo", "sha256:invented", "spawn-key",
            "python-script-v1", "attempts/blk_demo/main.py",
            b"print('invented')\n", "/tmp/private/lessons/demo",
            "/tmp/private/lessons",
        )
        with _mock.patch.object(_runner, "_create_leader", side_effect=fake_exec):
            job = (await service.admit(request)).job
            await _finished(service, job.job_id)
        observed["workdir"] = job.workdir
        observed["workdir_removed"] = (
            not os.path.exists(job.workdir) and not os.path.exists(f"{job.workdir}.tmp")
        )
        observed["exit_code"] = job.exit_code

        failed_request = _runner.RunnerRequest(
            "spawn", "blk_demo", "sha256:invented", "spawn-failed-key",
            "python-script-v1", "attempts/blk_demo/main.py",
            b"print('invented')\n", "/tmp/private/lessons/demo",
            "/tmp/private/lessons",
        )
        with _mock.patch.object(
                _runner, "_create_leader",
                side_effect=OSError("invented exec refusal")):
            failed = (await service.admit(failed_request)).job
            await _finished(service, failed.job_id)
        observed["failed_cause"] = failed.cause
        observed["failed_workdir_removed"] = bool(failed.workdir) and not any(
            os.path.exists(p) for p in (failed.workdir, f"{failed.workdir}.tmp")
        )
        return observed

    _f3_spawn = _asyncio.run(_f3_spawn_contract())
    assert (
        _f3_spawn["argv"] == ("/usr/bin/python3", f"{_f3_spawn['workdir']}/main.py")
        and _f3_spawn["cwd"] == _f3_spawn["workdir"]
        and os.path.basename(_f3_spawn["workdir"]).startswith("ephemeris-runner-")
        and _f3_spawn["entries"] == ["main.py"]
        and _f3_spawn["mode"] == 0o444
        and _f3_spawn["content"] == b"print('invented')\n"
        and _f3_spawn["env"] == {
            **_runner.RUNNER_ENV,
            "TMPDIR": f"{_f3_spawn['workdir']}.tmp", "GOTMPDIR": f"{_f3_spawn['workdir']}.tmp",
        }
        and _f3_spawn["new_session"] is True
        and callable(_f3_spawn["preexec"])
        and _f3_spawn["exit_code"] == 0
        and _f3_spawn["workdir_removed"]
        and _f3_spawn["failed_cause"] == "spawn-failed"
        and _f3_spawn["failed_workdir_removed"]
    ), f"F3 a snapshot runs read-only from its own fresh directory, removed after the job or a failed spawn: {_f3_spawn}"

    async def _f3_real_execution():
        from app.services.runner_registry import SNAPSHOT_PATH, RunnerSpec

        registry = {
            "py": RunnerSpec(("/usr/bin/python3", SNAPSHOT_PATH), (".py",), wall_seconds=1),
        }
        service = _runner.RunnerService(health_hook=lambda: None, registry=registry)

        sleep_marker = f"300.{os.getpid()}"

        def req(key, snapshot):
            return _runner.RunnerRequest(
                "real", "blk_demo", "sha256:invented", key, "py", "main.py",
                snapshot, "/tmp/private/lessons/demo", "/tmp/private/lessons",
            )

        async def run(key, snapshot):
            job = (await service.admit(req(key, snapshot))).job
            await _finished(service, job.job_id)
            await job.event_attempted.wait()
            output = "".join(
                event["text"] for event in job.events if event["event"] == "output"
            )
            return job, output

        printed, printed_output = await run(
            "printed", b"import os, sys\nprint('invented', os.getcwd())\nsys.stderr.write('warned\\n')\n",
        )
        stuck, _ = await run(
            "stuck",
            f"import subprocess, time\nsubprocess.Popen(['/bin/sleep', '{sleep_marker}'])\n"
            "while True: time.sleep(1)\n".encode(),
        )
        flood, _ = await run(
            "flood", b"import sys\nwhile True: sys.stdout.write('x' * 65536)\n",
        )
        # A child left behind by a leader that exits normally, holding the
        # output pipes: the job must still finish, and the child must not
        # outlive it.
        orphan_marker = f"301.{os.getpid()}"
        orphan, _ = await run(
            "orphan",
            f"import subprocess\nsubprocess.Popen(['/bin/sleep', '{orphan_marker}'])\n"
            "print('leader done')\n".encode(),
        )
        return printed, printed_output, stuck, flood, sleep_marker, orphan, orphan_marker

    (
        _f3_printed, _f3_printed_output, _f3_stuck, _f3_flood, _f3_sleep_marker,
        _f3_orphan, _f3_orphan_marker,
    ) = _asyncio.run(_f3_real_execution())
    assert (
        _f3_printed.cause == "exit" and _f3_printed.exit_code == 0
        and f"invented {_f3_printed.workdir}" in _f3_printed_output
        and "warned" in _f3_printed_output
        and not os.path.exists(_f3_printed.workdir)
    ), f"F3 a real snapshot runs and its output comes back: {_f3_printed_output!r}"
    assert (
        _f3_stuck.cause == "timeout" and _f3_stuck.signal == 9
        and subprocess.run(
            ["pgrep", "-f", f"sleep {_f3_sleep_marker}"], capture_output=True, text=True,
        ).returncode != 0
    ), "F3 a snapshot over its wall limit is killed with the children it spawned"
    assert (
        _f3_flood.cause == "output-limit" and _f3_flood.truncated
        and _f3_flood.output_bytes == _runner.OUTPUT_LIMIT_BYTES
    ), "F3 a snapshot flooding stdout is stopped at the output cap"
    assert (
        _f3_orphan.cause == "exit" and _f3_orphan.exit_code == 0
        and _f3_orphan.state == _runner.FINISHED
        and subprocess.run(
            ["pgrep", "-f", f"sleep {_f3_orphan_marker}"], capture_output=True, text=True,
        ).returncode != 0
    ), "F3 a child outliving a normally exiting leader is killed with the job"

    async def _f3_service_contracts():
        def req(
            lesson="lesson-a", key="key-a", block="blk_demo",
            snapshot=b"print('invented')\n",
        ):
            return _runner.RunnerRequest(
                lesson, block, "sha256:invented", key,
                "python-script-v1", "attempts/blk_demo/main.py",
                snapshot, "/tmp/private/lessons/demo",
                "/tmp/private/lessons",
            )

        result = {}
        processes = []

        async def spawn(_job):
            process = _F3Process()
            processes.append(process)
            return process

        service = _runner.RunnerService(spawn_hook=spawn, health_hook=lambda: None)
        admission = await service.admit(req())
        result["starting"] = admission.job.state == _runner.STARTING
        await _asyncio.sleep(0)
        await _asyncio.sleep(0)
        result["running"] = admission.job.state == _runner.RUNNING
        processes[0].stdout.feed_data(b"split:\xe2")
        await _asyncio.sleep(0)
        processes[0].stdout.feed_data(b"\x82\xac\n")
        processes[0].finish(0)
        finished = await _finished(service, admission.job.job_id)
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
        await _finished(health_service, health_admission.job.job_id)
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
        with _mock.patch.object(
                _runner.RunnerService, "_kill_tree") as natural_kill:
            natural_cancelled = await natural_service.cancel(natural.job_id)
        natural_processes[0].finish(0)
        natural = await _finished(natural_service, natural.job_id)
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

        with _mock.patch.object(
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
        cancelled = await _finished(cancel_service, cancelled.job_id)
        result["first_cause_release"] = (
            first and not second and cancelled.cause == "cancelled"
            and cancelled.reservation_released
            and cancel_service._active_total == 0 and kill.call_count == 1
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
        broken = await _finished(broken_service, broken.job_id)
        result["spawn_failure"] = (
            broken.cause == "spawn-failed" and broken.state == _runner.FINISHED
            and broken.reservation_released and broken_service._active_total == 0
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
                await _finished(race_service, item.job.job_id)

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
                await _finished(global_service, item.job.job_id)

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
            and len(rate_calls) == 1 and replay_service._active_total == 1
        )
        await _asyncio.sleep(0)
        replay_processes[0].finish(0)
        await _finished(replay_service, replay_results[0].job.job_id)

        retention_service = _runner.RunnerService(
            spawn_hook=broken_spawn, health_hook=lambda: None,
            max_terminal_jobs=1,
        )
        old = (await retention_service.admit(req("old", "old-key"))).job
        await _finished(retention_service, old.job_id)
        new = (await retention_service.admit(req("new", "new-key"))).job
        await _finished(retention_service, new.job_id)
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
        await _finished(retention_service, later.job_id)
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
        await _finished(notify_service, notify_job.job_id)
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
        with _mock.patch.object(_runner.RunnerService, "_kill_tree"):
            shutdown_task = _asyncio.create_task(shutdown_service.shutdown())
            await _asyncio.sleep(0)
            shutdown_processes[0].finish(-9)
            await shutdown_task
        result["shutdown"] = (
            shutdown_job.cause == "shutdown"
            and shutdown_job.state == _runner.FINISHED
            and shutdown_job.reservation_released
            and shutdown_service._active_total == 0
        )
        return result

    _f3_service = _asyncio.run(_f3_service_contracts())
    assert (
        _f3_service.get("starting") and _f3_service.get("running")
        and _f3_service.get("normal")
    ), f"F3 state machine reaches FINISHED only after reap/EOF with split UTF-8 intact: {str(_f3_service)}"
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
                lesson_uid=f"lesson-{cause}", block_id="blk_matrix",
                file_rev="sha256:invented", idempotency_key=f"key-{cause}",
                runner_id="python-script-v1", filename="main.py",
                snapshot=b"print('invented')\n",
                bundle_dir="/tmp/private/lessons/demo",
                bundle_root="/tmp/private/lessons",
            )
            job = _runner.RunnerJob(
                f"job-{cause}", request,
                _runner_registry.RUNNER_REGISTRY["python-script-v1"],
            )
            service._jobs[job.job_id] = job
            service._active_by_lesson[request.lesson_uid] = 1
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
                and job.reservation_released and service._active_total == 0
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
            lesson_uid="invented-disconnect-lesson", block_id="blk_demo",
            file_rev="sha256:invented", idempotency_key="invented-disconnect-key",
            runner_id="python-script-v1", filename="main.py",
            snapshot=b"print('invented')\n", bundle_dir="/tmp/invented-bundle",
            bundle_root="/tmp",
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
            "query_string": b"", "headers": [(b"host", b"127.0.0.1")],
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

    suite_state.update({
        name: value for name, value in locals().items()
        if name not in {"client", "suite_state"}
    })
