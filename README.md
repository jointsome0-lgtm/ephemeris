# Ephemeris

A small, local-first personal routine/activity tracker. FastAPI + SQLite +
Jinja2 + vanilla HTML/CSS. TickTick-like execution speed, our own data model.
Formerly known as *tick-like* — old repo URLs redirect here.

See [`docs/system-design.md`](docs/system-design.md) for the full design.

**Status:** runnable and actively implemented. Today/Tasks, Calendar (month),
a Focus timer in a drawer on every surface, Habits, Search, and JSONL Export are
available, with light/dark themes and Mode A (no-JS PRG) + Mode B (fetch)
progressive enhancement. Recovery is a documented contract: full
[backup and restore](docs/backup-restore.md) for the instance, the
[JSONL restore contract](docs/restore-from-export.md) for the audit stream.
Security, backup, cleanup, and Learn work continues
through focused issues and the repository's normal review and verification
protocols; it is not waiting on a repository-wide SDD freeze.

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

# Desktop-only (safe default — not reachable from other devices):
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

The tracker itself runs anywhere Python does. **Learn** does not: a lesson
terminal, and the build and run steps behind it, execute somebody else's code
inside a [bubblewrap](https://github.com/containers/bubblewrap) sandbox and a
systemd user scope. Neither exists on macOS or Windows, so Learn is
**Linux-only** — and it fails closed, refusing to open a shell rather than
opening an unsandboxed one, so a missing requirement shows up as
`[terminal: lesson sandbox unavailable ...]` and nothing worse.

| Requirement | Why | Check |
| --- | --- | --- |
| Linux | bubblewrap and systemd user scopes | `uname -s` |
| `bubblewrap` installed | the sandbox itself | `bwrap --version` |
| a systemd **user** session | per-run memory/task/time limits | `systemctl --user is-system-running` |
| unprivileged user namespaces permitted | bubblewrap needs one to build the sandbox | see below |

Ephemeris looks for `bwrap` at `~/.local/bin/bwrap` first and `/usr/bin/bwrap`
second — deliberately not through `$PATH`, which whoever starts the service
controls. Any build that accepts the options `app/sandbox.py` uses will do;
there is no version floor to memorise, because the app asks each candidate what
it accepts and refuses at startup, by name, if the answer is short. The
bubblewrap 0.9.0 in Ubuntu 24.04 is enough.

Install it with `sudo apt install bubblewrap` (Debian/Ubuntu),
`sudo dnf install bubblewrap` (Fedora), or `sudo pacman -S bubblewrap` (Arch).

### Ubuntu 24.04 and newer: allow bubblewrap a user namespace

Ubuntu 24.04 restricts unprivileged user namespaces to programs with an
AppArmor profile that grants them. Without one, every sandbox spawn dies at
`bwrap: setting up uid map: Permission denied`. Check whether it applies:

```bash
sysctl kernel.apparmor_restrict_unprivileged_userns
```

`1` means you need the profile; `0` or "no such file" means you do not. To
install the one this repository ships — it uses AppArmor's `@{HOME}` tunable,
so it covers both `bwrap` locations for any user:

```bash
sudo install -m 644 packaging/apparmor/bwrap /etc/apparmor.d/bwrap
sudo apparmor_parser -r /etc/apparmor.d/bwrap
```

Then re-check with `bwrap --unshare-user --die-with-parent --ro-bind / / true`,
which should exit `0` silently.

### After fixing anything on this list, restart the service

The sandbox probe and the `bwrap` path are resolved **once per process** and
cached, so a host-side fix does not reach a running Ephemeris:

```bash
systemctl --user restart ephemeris
```

## Open from your phone (same Wi-Fi)

The app has **no auth** — only do this on a network you trust (see
`docs/system-design.md` §20).

```bash
hostname -I    # find your Linux box's LAN IP first

# Trusted home Wi-Fi only — lets other devices on the LAN connect.
# The host allowlist admits only loopback names by default, so include
# the LAN IP (or hostname) your phone will put in the URL:
EPHEMERIS_TRUSTED_HOSTS="localhost,127.0.0.1,::1,<linux-lan-ip>" \
  uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers
```

Then on the phone browse to `http://<linux-lan-ip>:8000`. Without the
`EPHEMERIS_TRUSTED_HOSTS` entry the app answers LAN requests with
`400 untrusted Host` (see `docs/security-model.md`).

## Security

The supported boundary is localhost by default, or a trusted LAN when explicitly
enabled; public-internet deployment is unsupported in v0. The embedded terminal
is off by default (opt in with `EPHEMERIS_ENABLE_TERMINAL=1`) and remains
loopback-only when enabled. See the [security model](docs/security-model.md)
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
# For phone/LAN access, change --host to 0.0.0.0 in the copy (trusted Wi-Fi only).
systemctl --user daemon-reload
systemctl --user enable --now ephemeris
loginctl enable-linger "$USER"        # keep running after logout / across reboots
```

Status: `systemctl --user status ephemeris` · logs: `journalctl --user -u ephemeris -f`.
The template ships with `127.0.0.1`; copy-and-edit (don't symlink) so your local
host choice never lands back in Git. Copies made before `--no-proxy-headers` was
added to the template keep the old `ExecStart` — add the flag by hand, then
`systemctl --user daemon-reload && systemctl --user restart ephemeris` so the
edited unit is the one that starts (see [security model](docs/security-model.md)).

### Migrating an existing in-checkout data directory

Stop the service before moving an existing data directory, then set the
`ACTIVITY_DATA_DIR` environment line in your service copy and restart:

```bash
systemctl --user stop ephemeris
mv ~/projects/ephemeris/data ~/.local/share/ephemeris
# In ~/.config/systemd/user/ephemeris.service, set:
# Environment=ACTIVITY_DATA_DIR=%h/.local/share/ephemeris
systemctl --user daemon-reload
systemctl --user restart ephemeris
```

### Upgrading from tick-like

The project was renamed (repo, package, systemd unit, env vars). Pulling the
rename commit does not migrate an existing install — do it explicitly:

```bash
systemctl --user disable --now tick-like
mv ~/projects/tick-like ~/projects/ephemeris
cp deploy/ephemeris.service.example ~/.config/systemd/user/ephemeris.service
# ...re-apply any local edits (host, port, env) to the copy, then:
rm ~/.config/systemd/user/tick-like.service
systemctl --user daemon-reload
systemctl --user enable --now ephemeris
systemctl --user status ephemeris   # verify THIS unit is the listener
```

The env switches were renamed and the old names are **no longer honored** —
`TICKLIKE_TERM_PROXY` must be re-set as `EPHEMERIS_TERM_PROXY` or the proxy
override is ignored. The terminal toggle also changed polarity: it is now
**off by default** and only `EPHEMERIS_ENABLE_TERMINAL=1` turns it on
(`TICKLIKE_DISABLE_TERMINAL` / `EPHEMERIS_DISABLE_TERMINAL` are ignored).

## Configuration

| Env var             | Default                              | Meaning                                                        |
|---------------------|--------------------------------------|----------------------------------------------------------------|
| `APP_TIMEZONE`      | host local zone                      | The ledger clock; defines "today" (§13.3).                     |
| `ACTIVITY_DATA_DIR` | (required — refuses to start if unset) | Private data path outside the public checkout.                 |
| `ACTIVITY_DB`       | `<data>/activity.sqlite`             | Override the DB path directly.                                 |
| `EPHEMERIS_ENABLE_TERMINAL` | unset (terminal off)         | Opt-in: `1`/`true`/`yes`/`on` registers the loopback-only terminal websocket and UI. |

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
