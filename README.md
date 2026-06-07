# Activity Ledger

A small, local-first personal routine/activity tracker. FastAPI + SQLite +
Jinja2 + vanilla HTML/CSS. TickTick-like execution speed, our own data model.

See [`docs/system-design.md`](docs/system-design.md) for the full design.

**Status:** Milestone 0–1 (Today page + check-in write contract, Mode A no-JS).
History / Manage Items / Export and the Mode B progressive enhancement are next.

## Run locally

```bash
pip install -r requirements.txt

# Desktop-only (safe default — not reachable from other devices):
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://localhost:8000>. The SQLite file and seed items are created on first
start under `data/activity.sqlite`.

## Open from your phone (same Wi-Fi)

The app has **no auth** — only do this on a network you trust (see
`docs/system-design.md` §20).

```bash
# Trusted home Wi-Fi only — lets other devices on the LAN connect:
uvicorn app.main:app --host 0.0.0.0 --port 8000

hostname -I    # find your Linux box's LAN IP
```

Then on the phone browse to `http://<linux-lan-ip>:8000`.

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
