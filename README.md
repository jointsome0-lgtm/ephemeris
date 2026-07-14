# Ephemeris

A small, local-first personal routine/activity tracker. FastAPI + SQLite +
Jinja2 + vanilla HTML/CSS. TickTick-like execution speed, our own data model.
Formerly known as *tick-like* — old repo URLs redirect here.

See [`docs/system-design.md`](docs/system-design.md) for the full design.

**Status:** v0 feature-complete — Today/Tasks, Calendar (month), Eisenhower
Matrix, Focus (Pomodoro + persisted stats), Habits, Countdown, Search and JSONL
Export, with light/dark themes and Mode A (no-JS PRG) + Mode B (fetch)
progressive enhancement. `python verify.py` covers the write contracts.

## Run locally

This project uses [uv](https://docs.astral.sh/uv/) for dependency management;
`uv.lock` pins the exact, tested version set.

```bash
uv sync                      # build .venv from uv.lock

# Desktop-only (safe default — not reachable from other devices):
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>. The SQLite file and seed items are created on first
start under `data/activity.sqlite`.

No uv? A pinned `requirements.txt` (generated from `uv.lock`) is the pip fallback:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
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

## Configuration

| Env var             | Default            | Meaning                                            |
|---------------------|--------------------|----------------------------------------------------|
| `APP_TIMEZONE`      | host local zone    | The ledger clock; defines "today" (§13.3).         |
| `ACTIVITY_DATA_DIR` | `./data`           | Where `activity.sqlite` and `exports/` live.       |
| `ACTIVITY_DB`       | `<data>/activity.sqlite` | Override the DB path directly.                |

## Data

- `data/activity.sqlite` — source of truth (WAL mode). **Not** committed.
- Back it up with `sqlite3 data/activity.sqlite ".backup data/backup.sqlite"`
  (or `VACUUM INTO`), never a raw copy mid-write.
- Exports land in `data/exports/` and can contain private notes/tasks — also not
  committed.

## Public repository hygiene

This repo is designed to be safe as a public code repository, not as a public
hosted service. Keep runtime data, exports, screenshots, auth state, cookies, and
local agent/tool state out of Git. Public examples must be invented demo data,
not sanitized copies of a real ledger.

Before publishing or opening a PR, run:

```bash
python scripts/check_public_hygiene.py
git status --short --ignored
```
