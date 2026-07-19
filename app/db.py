"""Database access, schema, migrations, and the ledger clock.

Implements system-design.md sec13.1 (schema) and sec13.3 (connection policy,
timezone rule, deterministic ordering, PRAGMA user_version migrations).

The typed tables are the source of truth; the events table is an append-only
audit/derived feed (sec14.1).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

# --- paths -----------------------------------------------------------------

_data_dir = os.environ.get("ACTIVITY_DATA_DIR")
if not _data_dir:
    raise RuntimeError(
        "ACTIVITY_DATA_DIR is required: the destination must be an explicitly "
        "configured private path outside the public checkout (for example, "
        "~/.local/share/ephemeris); see "
        "https://github.com/jointsome0-lgtm/selfos/blob/main/docs/instance.md"
    )

DATA_DIR = Path(_data_dir)
DB_PATH = Path(os.environ.get("ACTIVITY_DB", DATA_DIR / "activity.sqlite"))
EXPORTS_DIR = DATA_DIR / "exports"

# --- status enum (sec13.2) -------------------------------------------------

STATUSES = ("full_done", "light_done", "skipped", "failed")

# --- ledger clock (sec13.3): the server owns "today" -----------------------


def app_tz() -> ZoneInfo | None:
    """Configured APP_TIMEZONE, or None to mean 'host local zone'."""
    name = os.environ.get("APP_TIMEZONE")
    return ZoneInfo(name) if name else None


def _now() -> datetime:
    """Offset-aware 'now' in the ledger zone (APP_TIMEZONE or host local)."""
    tz = app_tz()
    return datetime.now(tz) if tz is not None else datetime.now().astimezone()


def today_str() -> str:
    """Server-authoritative 'today' as 'YYYY-MM-DD' (sec13.3)."""
    return _now().date().isoformat()


def now_iso() -> str:
    """ISO-8601 timestamp with offset, e.g. 2026-06-06T21:10:00+03:00."""
    return _now().isoformat(timespec="seconds")


def now_stamp() -> str:
    """Compact, filename-safe local timestamp, e.g. 2026-06-06-211000 (sec18.1)."""
    return _now().strftime("%Y-%m-%d-%H%M%S")


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_valid_date(s: str | None) -> bool:
    """True if s is a real 'YYYY-MM-DD' calendar date."""
    if not s or not _DATE_RE.match(s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def is_not_future(s: str) -> bool:
    """True if date string s is today or earlier (lexicographic works for ISO)."""
    return s <= today_str()


# --- event ledger (sec14.1): append-only audit feed -------------------------


def append_event(conn: sqlite3.Connection, type_: str, payload: dict) -> str:
    """Append one audit event — call inside the same transaction as the write it
    describes. One owner of the ledger write contract (payload_version, JSON form,
    timestamp source) for every service.

    Returns the event's persistent UUID (schema v9): the stable identity that
    survives export/redelivery, for callers that need to reference the event
    (issue #17 audit-export slice)."""
    event_uuid = str(uuid4())
    conn.execute(
        "INSERT INTO events (uuid, timestamp, type, payload_version, payload_json) "
        "VALUES (?, ?, ?, 1, ?)",
        (event_uuid, now_iso(), type_, json.dumps(payload, ensure_ascii=False)),
    )
    return event_uuid


# --- connections (sec13.3 connection policy) -------------------------------


def get_conn() -> sqlite3.Connection:
    """A configured SQLite connection. PRAGMAs are set before any transaction."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # foreign_keys is OFF by default in SQLite and is per-connection; required
    # for the checkins -> routine_items FK. journal_mode=WAL lets the phone read
    # while the desktop writes. busy_timeout briefly waits out writer contention.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# --- schema + migrations (sec13.1 / sec13.3) -------------------------------

SCHEMA_VERSION = 11

_INITIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS routine_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL CHECK(length(trim(title)) > 0),
  group_name TEXT NOT NULL DEFAULT 'Core Routine'
             CHECK(length(trim(group_name)) > 0),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deactivated_at TEXT
);

CREATE TABLE IF NOT EXISTS checkins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date TEXT NOT NULL,
  routine_item_id INTEGER NOT NULL,
  status TEXT NOT NULL
         CHECK(status IN ('full_done','light_done','skipped','failed')),
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(date, routine_item_id),
  FOREIGN KEY(routine_item_id) REFERENCES routine_items(id)
);

CREATE TABLE IF NOT EXISTS daily_notes (
  date TEXT PRIMARY KEY,
  text TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,
  type TEXT NOT NULL,
  payload_version INTEGER NOT NULL DEFAULT 1,
  payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkins_date ON checkins(date);
"""


def _migrate_to_1(conn: sqlite3.Connection) -> None:
    conn.executescript(_INITIAL_SCHEMA)


# v2 — task-manager layer (lists / tasks / tags) added alongside the habit tables.
# Habits (routine_items + checkins) are unchanged; tasks are a separate entity that
# also surfaces in the Today list. (sec13.1 extended; sec21 task model.)
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS lists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL CHECK(length(trim(name)) > 0),
  emoji TEXT,
  kind TEXT NOT NULL DEFAULT 'list' CHECK(kind IN ('inbox','list')),
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  archived_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL CHECK(length(trim(title)) > 0),
  list_id INTEGER REFERENCES lists(id),
  note TEXT,
  due_date TEXT,
  priority INTEGER NOT NULL DEFAULT 0 CHECK(priority IN (0,1,2,3)),
  kind TEXT NOT NULL DEFAULT 'task' CHECK(kind IN ('task','countdown')),
  completed_at TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE CHECK(length(trim(name)) > 0),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_tags (
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  tag_id INTEGER NOT NULL REFERENCES tags(id),
  PRIMARY KEY (task_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_list ON tasks(list_id);
CREATE INDEX IF NOT EXISTS idx_tasks_completed ON tasks(completed_at);
"""


def _migrate_to_2(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V2)


# v3 — habit attributes for the TickTick-style Habit tab (sec31). The habit IS a
# routine_item; these columns add the Create-Habit fields (frequency / goal /
# start date / reminder / section is the existing group_name). Reminders are
# stored for parity; firing them needs a scheduler (out of scope, noted sec31).
_SCHEMA_V3 = """
ALTER TABLE routine_items ADD COLUMN emoji TEXT;
ALTER TABLE routine_items ADD COLUMN frequency TEXT NOT NULL DEFAULT 'daily';
ALTER TABLE routine_items ADD COLUMN goal TEXT NOT NULL DEFAULT 'achieve_all';
ALTER TABLE routine_items ADD COLUMN goal_days TEXT NOT NULL DEFAULT 'forever';
ALTER TABLE routine_items ADD COLUMN start_date TEXT;
ALTER TABLE routine_items ADD COLUMN reminder TEXT;
ALTER TABLE routine_items ADD COLUMN constant_reminder INTEGER NOT NULL DEFAULT 0;
"""


def _migrate_to_3(conn: sqlite3.Connection) -> None:
    # ADD COLUMN isn't idempotent, but the user_version gate runs this exactly
    # once; guard anyway so a half-applied upgrade can be re-run safely.
    have = {r["name"] for r in conn.execute("PRAGMA table_info(routine_items)")}
    for stmt in _SCHEMA_V3.strip().split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        col = stmt.split("ADD COLUMN", 1)[1].split()[0]
        if col not in have:
            conn.execute(stmt)


# v4 — focus_sessions: persist completed Pomodoro / Stopwatch sessions so the
# Focus view's Overview stats + Focus Record stop being static 0s. A session is a
# finished span of focused time; `mode='pomo'` rows also count as one Pomodoro.
# Read-only derived stats (today/total pomo + focus duration) come from here.
_SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS focus_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT NOT NULL DEFAULT 'pomo' CHECK(mode IN ('pomo','stopwatch')),
  seconds INTEGER NOT NULL CHECK(seconds >= 0),
  note TEXT,
  date TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_focus_date ON focus_sessions(date);
"""


def _migrate_to_4(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V4)


# v5 — calendar_events: timed, recurring events for the Calendar week/month views
# (sec32). The ROW IS THE SERIES; concrete occurrences are expanded on read from the
# recurrence rule, never materialised. Soft-archived, never hard-deleted, so a series
# stays joinable to its audit events (sec14.1 / recovery goal sec16.5). Kept SEPARATE
# from `tasks` on purpose: recurring time-blocks must not pollute the task smart-lists
# / Matrix and carry no "done" semantics (a class happens, it isn't completed — that's
# what the Habit tab is for).
_SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS calendar_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  title       TEXT NOT NULL CHECK(length(trim(title)) > 0),
  emoji       TEXT,
  list_id     INTEGER REFERENCES lists(id),      -- optional grouping/colour, like tasks
  note        TEXT,

  all_day     INTEGER NOT NULL DEFAULT 0 CHECK(all_day IN (0,1)),
  start_time  TEXT,            -- 'HH:MM' local; NULL iff all_day=1
  end_time    TEXT,            -- 'HH:MM' local; NULL = point-in-time event

  freq        TEXT NOT NULL DEFAULT 'once' CHECK(freq IN ('once','daily','weekly')),
  byweekday   TEXT,            -- 7-char Mon..Sun bitmask, e.g. '1010100'; used when freq='weekly'
  interval_n  INTEGER NOT NULL DEFAULT 1 CHECK(interval_n >= 1),  -- every N days/weeks
  start_date  TEXT NOT NULL,   -- 'YYYY-MM-DD' first eligible date (anchor)
  end_date    TEXT,            -- 'YYYY-MM-DD' inclusive; NULL = open-ended
  exdates     TEXT,            -- JSON array of 'YYYY-MM-DD' skipped occurrences

  color       TEXT,            -- optional hex or css class for the block
  created_at  TEXT NOT NULL,
  updated_at  TEXT,
  archived_at TEXT             -- soft-delete the whole series
);

CREATE INDEX IF NOT EXISTS idx_calevents_start ON calendar_events(start_date);
"""


def _migrate_to_5(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V5)


# v6 — Learn lessons: a small backlog/archive for research items and generated
# lessons. The rendered HTML lesson body lives under data/lessons later; this
# table stores public-safe metadata and emits ledger events on every write.
_SCHEMA_V6 = """
CREATE TABLE IF NOT EXISTS lessons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL CHECK(length(trim(title)) > 0),
  source_url TEXT,
  slug TEXT NOT NULL UNIQUE CHECK(length(trim(slug)) > 0),
  status TEXT NOT NULL DEFAULT 'backlog'
         CHECK(status IN ('backlog','studying','paused','studied')),
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  started_at TEXT,
  completed_at TEXT,
  archived_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_lessons_status ON lessons(status, archived_at);
CREATE INDEX IF NOT EXISTS idx_lessons_created ON lessons(created_at);
"""


def _migrate_to_6(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V6)


# v7 — Learn bundle navigation: the agent owns a runtime lesson folder
# (lesson.json + index.html + related/*.html), while SQLite keeps app-state for
# the currently selected entry and last time the lesson was opened.
_SCHEMA_V7 = """
ALTER TABLE lessons ADD COLUMN current_entry TEXT;
ALTER TABLE lessons ADD COLUMN last_opened_at TEXT;
"""


def _migrate_to_7(conn: sqlite3.Connection) -> None:
    have = {r["name"] for r in conn.execute("PRAGMA table_info(lessons)")}
    for stmt in _SCHEMA_V7.strip().split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        col = stmt.split("ADD COLUMN", 1)[1].split()[0]
        if col not in have:
            conn.execute(stmt)


# v8 — Focus ↔ Lesson link: a focus session may name the lesson being studied, so
# study time stops being a silo (surfaces on the Focus record + in the ledger). The
# column is nullable — an unattached Pomodoro/stopwatch span is still the norm.
_SCHEMA_V8 = """
ALTER TABLE focus_sessions ADD COLUMN lesson_id INTEGER REFERENCES lessons(id);
"""


def _migrate_to_8(conn: sqlite3.Connection) -> None:
    have = {r["name"] for r in conn.execute("PRAGMA table_info(focus_sessions)")}
    for stmt in _SCHEMA_V8.strip().split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        col = stmt.split("ADD COLUMN", 1)[1].split()[0]
        if col not in have:
            conn.execute(stmt)


# v9 — persistent event identity (issue #17, audit-export slice): every ledger
# row carries a service-owned UUID so an exported event can later be redelivered
# idempotently downstream. The backfill stamps ONLY the new column on pre-v9
# rows — payload_json / timestamp / type history is never rewritten. The unique
# index tolerates NULLs (SQLite), so a not-yet-restarted pre-v9 process can
# still insert rows into an already-migrated database; backfill_event_uuids()
# runs on every init_db() to heal any such rows on the next start.


def backfill_event_uuids(conn: sqlite3.Connection) -> int:
    """Stamp a UUID on every event row that lacks one; returns how many were
    stamped. Idempotent: rows that already carry a uuid are never touched, so a
    rerun is a no-op. Only the uuid column is written — payload history stays
    byte-identical."""
    ids = [r["id"] for r in conn.execute("SELECT id FROM events WHERE uuid IS NULL")]
    conn.executemany(
        "UPDATE events SET uuid = ? WHERE id = ? AND uuid IS NULL",
        [(str(uuid4()), event_id) for event_id in ids],
    )
    return len(ids)


def _migrate_to_9(conn: sqlite3.Connection) -> None:
    have = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    if "uuid" not in have:
        conn.execute("ALTER TABLE events ADD COLUMN uuid TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_events_uuid ON events(uuid)")
    backfill_event_uuids(conn)


# v10 — retro_entries (docs/retro-spec.md): owner-typed retrospectives captured
# for the future exp2res feed (issue #49). period_raw is the owner-typed truth —
# the field a selfos adapter hands to exp2res, which re-resolves it in its own
# workspace timezone; period_start/period_end are ephemeris-local derivations
# kept only for list ordering and display. The period grammar (precision /
# confidence vocabularies, accepted period formats) mirrors exp2res
# services/time_input.py verbatim so anything accepted here imports cleanly
# there. Soft-archived, never hard-deleted (sec14.1 joinability).
_SCHEMA_V10 = """
CREATE TABLE IF NOT EXISTS retro_entries (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  uuid         TEXT NOT NULL UNIQUE,
  period_raw   TEXT NOT NULL DEFAULT '',   -- '' iff precision='unknown'
  precision    TEXT NOT NULL CHECK(precision IN
               ('exact_datetime','exact_day','week','month','quarter','year',
                'date_range','approximate_range','unknown')),
  confidence   TEXT NOT NULL CHECK(confidence IN ('low','medium','high','unknown')),
  period_start TEXT,            -- ISO 8601 with offset; NULL iff precision='unknown'
  period_end   TEXT,            -- non-NULL iff range precision; > period_start
  project      TEXT,            -- optional label; validated non-blank under NFC+casefold
  text         TEXT NOT NULL CHECK(length(text) > 0),
  created_at   TEXT NOT NULL,
  updated_at   TEXT,
  archived_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_retro_start ON retro_entries(period_start);
"""


def _migrate_to_10(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V10)


# v11 — lesson identity (learn-bundle-spec.md §3): `lessons.uid` is the mint
# source and the truth for lesson_uid; the bundle manifest only carries an
# echo. Same idiom as events.uuid (v9): nullable column + unique index +
# idempotent backfill that NEVER replaces an existing uid — a lesson is minted
# exactly once, and rename/status/archive churn must not touch it. The
# backfill also reruns on every init_db() to stamp rows a not-yet-restarted
# pre-v11 process inserts after the migration ran. (This step shipped as v10
# on its feature branch and was renumbered when retro_entries landed first;
# it is column-existence-guarded, so a DB that already ran it as v10 upgrades
# cleanly.)


def backfill_lesson_uids(conn: sqlite3.Connection) -> int:
    """Mint a UUID for every lesson row that lacks one; returns how many were
    stamped. Idempotent: an existing uid is never replaced, so a rerun is a
    no-op and the uid survives title/slug renames and re-migration."""
    ids = [r["id"] for r in conn.execute("SELECT id FROM lessons WHERE uid IS NULL")]
    conn.executemany(
        "UPDATE lessons SET uid = ? WHERE id = ? AND uid IS NULL",
        [(str(uuid4()), lesson_id) for lesson_id in ids],
    )
    return len(ids)


def _migrate_to_11(conn: sqlite3.Connection) -> None:
    have = {r["name"] for r in conn.execute("PRAGMA table_info(lessons)")}
    if "uid" not in have:
        conn.execute("ALTER TABLE lessons ADD COLUMN uid TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_lessons_uid ON lessons(uid)")
    backfill_lesson_uids(conn)


# Ordered, idempotent steps. A schema change must NEVER require deleting the
# ledger to upgrade (sec13.3): add a (version, fn) row, never rewrite history.
_MIGRATIONS = [
    (1, _migrate_to_1),
    (2, _migrate_to_2),
    (3, _migrate_to_3),
    (4, _migrate_to_4),
    (5, _migrate_to_5),
    (6, _migrate_to_6),
    (7, _migrate_to_7),
    (8, _migrate_to_8),
    (9, _migrate_to_9),
    (10, _migrate_to_10),
    (11, _migrate_to_11),
]


def init_db() -> None:
    """Create/upgrade the schema using PRAGMA user_version (sec13.3)."""
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for target, migrate in _MIGRATIONS:
            if version < target:
                migrate(conn)          # idempotent (CREATE ... IF NOT EXISTS)
                conn.commit()          # persist schema before bumping the version
                conn.execute(f"PRAGMA user_version = {target}")
                conn.commit()
                version = target
        # Heal rows a pre-v9/pre-v11 process may have inserted after the
        # migration ran (the live service lags the working tree until its
        # next restart).
        healed = backfill_event_uuids(conn)
        healed += backfill_lesson_uids(conn)
        if healed:
            conn.commit()
    finally:
        conn.close()
