# Ephemeris

A small, local-first personal routine/activity tracker. FastAPI + SQLite +
Jinja2 + vanilla HTML/CSS. TickTick-like execution speed, our own data model.
Formerly known as *tick-like* — old repo URLs redirect here.

See [`docs/system-design.md`](docs/system-design.md) for the full design.

**Status:** runnable and actively implemented. Today/Tasks, Calendar (month),
Eisenhower Matrix, Focus (Pomodoro + persisted stats), Habits, Countdown,
Search, and JSONL Export are available, with light/dark themes and Mode A
(no-JS PRG) + Mode B (fetch) progressive enhancement. Security, backup,
cleanup, and Learn work continues through focused issues and the repository's
normal review and verification protocols; it is not waiting on a
repository-wide SDD freeze.

Integration v1 composes separately owned Atlas and Exp2Res views through
optional configured URLs on the same-machine/loopback topology. Ephemeris does
not implement either peer system and remains fully usable when those URLs are
unset; deterministic cross-system adapters live in Selfos.

## Run locally

This project uses [uv](https://docs.astral.sh/uv/) for dependency management;
`uv.lock` pins the exact, tested version set.

Ephemeris refuses to start until `ACTIVITY_DATA_DIR` names an explicitly
configured private path outside the public checkout.

```bash
uv sync                      # build .venv from uv.lock
export ACTIVITY_DATA_DIR=~/.local/share/ephemeris

# Desktop-only (safe default — not reachable from other devices):
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>. The SQLite file and seed items are created on first
start under `$ACTIVITY_DATA_DIR/activity.sqlite`.

No uv? A pinned `requirements.txt` (generated from `uv.lock`) is the pip fallback:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export ACTIVITY_DATA_DIR=~/.local/share/ephemeris
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Open from your phone (same Wi-Fi)

The app has **no auth** — only do this on a network you trust (see
`docs/system-design.md` §20).

```bash
# Trusted home Wi-Fi only — lets other devices on the LAN connect:
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

hostname -I    # find your Linux box's LAN IP
```

Then on the phone browse to `http://<linux-lan-ip>:8000`.

## Security

The supported boundary is localhost by default, or a trusted LAN when explicitly
enabled; public-internet deployment is unsupported in v0. The embedded terminal
remains loopback-only. See the [security model](docs/security-model.md) for the
deployment assumptions, known limitations, and terminal kill switch.
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
host choice never lands back in Git.

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
if you set `TICKLIKE_DISABLE_TERMINAL` or `TICKLIKE_TERM_PROXY`, re-set them
as `EPHEMERIS_DISABLE_TERMINAL` / `EPHEMERIS_TERM_PROXY` before restarting,
or the terminal comes back enabled / the proxy override is ignored.

## Configuration

| Env var             | Default                              | Meaning                                                        |
|---------------------|--------------------------------------|----------------------------------------------------------------|
| `APP_TIMEZONE`      | host local zone                      | The ledger clock; defines "today" (§13.3).                     |
| `ACTIVITY_DATA_DIR` | (required — refuses to start if unset) | Private data path outside the public checkout.                 |
| `ACTIVITY_DB`       | `<data>/activity.sqlite`             | Override the DB path directly.                                 |

## Data

- `$ACTIVITY_DATA_DIR/activity.sqlite` — source of truth (WAL mode). **Not** committed.
- Back it up with `sqlite3 "$ACTIVITY_DATA_DIR/activity.sqlite" ".backup '$ACTIVITY_DATA_DIR/backup.sqlite'"`
  (or `VACUUM INTO`), never a raw copy mid-write.
- Exports land in `$ACTIVITY_DATA_DIR/exports/` and can contain private
  notes/tasks — also not committed.

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
