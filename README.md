# Ephemeris

A small, local-first personal routine/activity tracker. FastAPI + SQLite +
Jinja2 + vanilla HTML/CSS. TickTick-like execution speed, our own data model.
Formerly known as *tick-like* — old repo URLs redirect here.

See [`docs/system-design.md`](docs/system-design.md) for the full design.

**Status:** runnable and in daily use. The surfaces are Today/Tasks (with the
Board, Next 7, list, completed, history and trash views), Calendar (month and
week), a Focus timer in a drawer on every page, Habits, Search, Diary, Retro,
Learn, JSONL Export, and, when `SELFOS_EXP2RES_MIRROR_URL` is set, Mirror,
plus the opt-in terminal drawer. Light/dark
themes, Mode A (no-JS PRG) + Mode B (fetch) progressive enhancement. Recovery
is a documented contract: full [backup and restore](docs/backup-restore.md)
for the instance, the [JSONL restore contract](docs/restore-from-export.md)
for the audit stream.

Integration v1 composes separately owned Atlas and Exp2Res views through
optional configured URLs on the same-machine/loopback topology. Ephemeris does
not implement either peer system and remains fully usable when those URLs are
unset; deterministic cross-system adapters live in Selfos.

## Run locally

This project uses [uv](https://docs.astral.sh/uv/) for dependency management;
`uv.lock` pins the exact, tested version set.

Ephemeris refuses to start until `ACTIVITY_DATA_DIR` names an explicitly
configured private path outside the public checkout.

Platform: the app itself runs anywhere Python does. The terminal drawer is the
one exception — it needs a Unix PTY, so it is Unix-only, and it stays off unless
`EPHEMERIS_ENABLE_TERMINAL` is set. Opting it in without a PTY fails at startup
with an explicit message rather than breaking the rest of the app. Learn's
*lesson* terminals ask for more than a PTY — see
[what the host must provide](#what-the-host-must-provide).

```bash
uv sync                      # build .venv from uv.lock
export ACTIVITY_DATA_DIR=~/.local/share/ephemeris

# Loopback is the only supported binding:
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Open <http://localhost:8000>. The SQLite file and seed items are created on first
start under `$ACTIVITY_DATA_DIR/activity.sqlite`.

### One worker

Run Ephemeris with **exactly one uvicorn worker**. The commands above and the
systemd unit already do — uvicorn defaults to one, so this is a warning against
adding `--workers N`, not a flag to set. Nothing enforces it at runtime; it is a
property of the documented deployment, which is "direct loopback, single user,
single worker" ([lesson artifacts API](docs/lesson-artifacts-api.md),
[lesson assessments API](docs/lesson-assessments-api.md)).

Three things assume it:

- **The abuse dampers are in-process.** The per-lesson rate limits on artifact
  saves and assessments are sliding windows in server-process memory, so N
  workers would permit N times the documented budget. (Even a rolling restart
  briefly overlaps two processes, which the assessments API notes as a bounded
  2×.)
- **The terminal session registry is in-process.** A detached PTY lives in the
  worker that created it, so a second worker cannot reattach to it — the drawer
  would appear to lose sessions at random depending on which worker answered.
- **The capability-token registry is in-process** — memory only, no persistence
  or TTL, so a token minted by one worker is unknown to the others.

SQLite itself is fine with concurrent readers (WAL) and would not be the thing
that breaks; the in-memory state above is.

No uv? A pinned `requirements.txt` (generated from `uv.lock`) is the pip fallback:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export ACTIVITY_DATA_DIR=~/.local/share/ephemeris
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

## What the host must provide

The tracker itself runs anywhere Python does. **Learn** additionally needs
four executables. A lesson terminal is a plain interactive shell started in
the lesson's bundle directory, and the build, render, and run steps behind it
are plain subprocesses; the app's generated `AGENTS.md` / `CLAUDE.md` in that
directory are the agent's instructions, and nothing else isolates it from the
host (see the [security model](docs/security-model.md)).

| Requirement | Used by | Check |
| --- | --- | --- |
| `bun` at `~/.bun/bin/bun` | the build step | `~/.bun/bin/bun --version` |
| a Chrome/Chromium binary on `$PATH` (`google-chrome`, `chromium`, `chromium-browser`, or `chrome`; override with `EPHEMERIS_RENDER_CHECK_CHROME=/path`) | the render gate | `chromium --version` |
| `/usr/bin/python3` | the Python runner | `/usr/bin/python3 --version` |
| `/usr/local/go/bin/go` | the Go runner | `/usr/local/go/bin/go version` |

None of these are checked at startup: a missing one surfaces the first time a
build, render, or run asks for it, and runner health reports whether the two
runner executables run.

## Security

The only supported boundary is loopback (`127.0.0.1`); public-internet
deployment is unsupported in v0. The embedded terminal is off by default (opt
in with `EPHEMERIS_ENABLE_TERMINAL=1`) and is also loopback-only. See the
[security model](docs/security-model.md)
for the deployment assumptions and known limitations, and
[`SECURITY.md`](SECURITY.md) for how to report a vulnerability.
The ecosystem-wide security policy lives in [selfos `SECURITY.md`](https://github.com/jointsome0-lgtm/selfos/blob/main/SECURITY.md);
this repo's security model stays authoritative for ephemeris-specific deployment
assumptions.

## Run as a background service (systemd)

To keep the ledger running across reboots, install the user service from the
committed template:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/ephemeris.service.example ~/.config/systemd/user/ephemeris.service
systemctl --user daemon-reload
systemctl --user enable --now ephemeris
loginctl enable-linger "$USER"        # keep running after logout / across reboots
```

Status: `systemctl --user status ephemeris` · logs: `journalctl --user -u ephemeris -f`.
The template binds `127.0.0.1`; copy-and-edit (don't symlink) so local path and
environment changes never land back in Git.

Restart the service after every deploy (`systemctl --user restart ephemeris`).
The app does not tolerate a running process older than the templates it
serves: Jinja reads templates from the working tree on each render, so a
checkout that moved under a still-running process renders new templates with
the previous commit's Python.

## Configuration

Every variable the app reads. The first six are read once at startup, so
restart the process after changing one; the Chrome variable is read each time
a render gate starts.

| Env var | Default | Meaning |
| --- | --- | --- |
| `ACTIVITY_DATA_DIR` | (required; refuses to start if unset) | Private data path outside the public checkout. |
| `ACTIVITY_DB` | `<data>/activity.sqlite` | Override the SQLite file path directly. |
| `APP_TIMEZONE` | host local zone | The ledger clock; defines "today" (system design §13.3). |
| `SELFOS_EXP2RES_URL` | unset (no strip) | Loopback http(s) URL of the Exp2Res gap-questions view the Diary tab embeds. A non-loopback URL is ignored with a warning. |
| `SELFOS_EXP2RES_MIRROR_URL` | unset (`/mirror` answers 404) | Loopback http(s) URL of the Exp2Res Mirror view the `/mirror` surface embeds. Same loopback rule. |
| `EPHEMERIS_ENABLE_TERMINAL` | unset (terminal off) | Opt-in: `1`/`true`/`yes`/`on` registers the loopback-only terminal websocket and UI. |
| `EPHEMERIS_RENDER_CHECK_CHROME` | unset (search `$PATH` for `google-chrome`, `chromium`, `chromium-browser`, `chrome`) | Path to the Chrome/Chromium binary the Learn render gate runs. |

The terminal shell does not inherit the service environment; it starts from an
allowlist (`HOME`, `USER`, `LOGNAME`, `SHELL`, `PATH`, `LANG`, `LANGUAGE`,
`LC_*`, `TZ`, `XDG_*`, `SSH_AUTH_SOCK`, and `HTTP_PROXY`, `HTTPS_PROXY`,
`NO_PROXY` in either case) with `TERM` and `PATH` normalized. The proxy
variables pass through exactly as the service has them; the app does no egress
routing of its own. `SHELL` picks the shell the drawer runs. The lesson-agent shell is additionally
handed `EPHEMERIS_ASSESS_URL`, `EPHEMERIS_ASSESS_TOKEN` and
`EPHEMERIS_BUILD_URL`, minted per session, and `CLAUDE_CODE_OAUTH_TOKEN` read
from `$ACTIVITY_DATA_DIR/claude-token` when that file exists. Those four are
outputs, not settings: setting them on the service has no effect.

## Data

- `$ACTIVITY_DATA_DIR/activity.sqlite` — source of truth (WAL mode). **Not** committed.
- Back it up with `python -m scripts.backup_db --keep 20`, never a raw copy
  mid-write. One run writes a verified set — snapshot, lesson bundles, manifest —
  to `$ACTIVITY_DATA_DIR/backups/`; see [backup and restore](docs/backup-restore.md)
  for the schedule template, the `--verify` check, and the restore command.
- Exports land in `$ACTIVITY_DATA_DIR/exports/` and can contain private
  notes/tasks — also not committed. A JSONL export is an audit stream, **not** a
  backup: see the [restore contract](docs/restore-from-export.md) for what it
  cannot carry.

## Public repository hygiene

This repo is designed to be safe as a public code repository, not as a public
hosted service. Keep runtime data, exports, screenshots, auth state, cookies, and
local agent/tool state out of Git. Public examples must be invented demo data,
not sanitized copies of a real ledger.

Ephemeris is a [public engine](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/architecture.md):
it holds code, schemas/specs, docs, and invented demo fixtures. All private
runtime state lives in an explicitly configured [private instance](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/instance.md)
outside the checkout. The ecosystem [deletion contract](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/deletion.md)
defines how data leaves this ecosystem.

Before publishing or opening a PR, run:

```bash
python scripts/check_public_hygiene.py
git status --short --ignored
```

Both layers are required, not alternatives: CI already runs the checker; enable
the committed pre-commit hook once per clone:

```bash
git config core.hooksPath .githooks
```
