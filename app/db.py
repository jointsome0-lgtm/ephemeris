"""Database access, schema, migrations, and the ledger clock.

Implements system-design.md sec13.1 (schema) and sec13.3 (connection policy,
timezone rule, deterministic ordering, PRAGMA user_version migrations).

The typed tables are the source of truth; the events table is an append-only
audit/derived feed (sec14.1).
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from .settings import settings

# --- paths -----------------------------------------------------------------
# app/settings.py owns the environment lookups (and the hard failure when
# ACTIVITY_DATA_DIR is unset — importing it is what raises, exactly as the
# inline check here used to). These names stay: they are what the rest of the
# app and the scripts import, and they carry the same values as before.

DATA_DIR = settings.data_dir
DB_PATH = settings.db_path
EXPORTS_DIR = settings.exports_dir
BACKUPS_DIR = settings.backups_dir

# --- status enum (sec13.2) -------------------------------------------------

STATUSES = ("full_done", "light_done", "skipped", "failed")

# --- ledger clock (sec13.3): the server owns "today" -----------------------


def app_tz() -> ZoneInfo | None:
    """Configured APP_TIMEZONE, or None to mean 'host local zone'."""
    name = settings.timezone
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


def pretty_date(d: date, *, weekday: bool = False, year: bool = False) -> str:
    """Human date with an unpadded day number: 'Jul 4', 'Sat Jul 4', 'Jul 4, 2026'.

    One owner for what used to be five spellings of strftime("%b %-d"). The `-`
    no-pad flag is a glibc extension — absent from C89 strftime, so it is not
    portable (Windows spells it `%#d`, and other libcs reject it outright). Only
    the day number needs it, so it is rendered by hand and everything else stays
    with strftime, keeping the month/weekday names locale-driven and the output
    byte-identical to the old format strings.
    """
    out = d.strftime("%a %b " if weekday else "%b ") + str(d.day)
    return out + d.strftime(", %Y") if year else out


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


# --- installation metadata (schema v16) ------------------------------------
# One key/value row per fact about THIS database that is not domain data: how
# it was initialized, not what it holds. Deliberately a table and not a file
# beside it — a full backup is a byte copy of the database, so anything stored
# here travels with a restore automatically, while a sidecar file would have to
# be remembered separately and would be missing exactly when it matters.

SEEDED_AT = "seeded_at"
"""`app_meta` key: ISO timestamp of the boot that ran the demo seeders.

Its PRESENCE is the contract, not its value: startup calls the seeders only
when the key is absent. The timestamp is for a human reading the row.
"""


def meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    """Read one `app_meta` value, or None when the key was never written."""
    row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    # Positional, not by name: these helpers are also called from the restore
    # script on a plain sqlite3 connection, which has no Row factory.
    return None if row is None else row[0]


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Write one `app_meta` value, replacing any earlier one for that key."""
    conn.execute(
        "INSERT INTO app_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# --- event ledger (sec14.1): append-only audit feed -------------------------


def append_event(
    conn: sqlite3.Connection,
    type_: str,
    payload: dict,
    *,
    event_uuid: str | None = None,
) -> str:
    """Append one audit event — call inside the same transaction as the write it
    describes. One owner of the ledger write contract (payload_version, JSON form,
    timestamp source) for every service.

    Returns the event's persistent UUID (schema v9): the stable identity that
    survives export/redelivery, for callers that need to reference the event
    (issue #17 audit-export slice).

    `event_uuid` lets a caller mint that identity first, for the one shape this
    function could not otherwise serve: a row whose `event_uuid` column is NOT
    NULL *and* whose own rowid must be echoed into the event payload (schema v14
    `lesson_assessments` / `seq`). The row must then be inserted before the
    event, so the uuid cannot come back from here. Callers that pass one own its
    uniqueness; every existing caller keeps the minted-here default."""
    event_uuid = event_uuid or str(uuid4())
    conn.execute(
        "INSERT INTO events (uuid, timestamp, type, payload_version, payload_json) "
        "VALUES (?, ?, ?, 1, ?)",
        (event_uuid, now_iso(), type_, json.dumps(payload, ensure_ascii=False)),
    )
    return event_uuid


# --- connections (sec13.3 connection policy) -------------------------------


def get_conn(*, check_same_thread: bool = True) -> sqlite3.Connection:
    """A configured SQLite connection. PRAGMAs are set before any transaction.

    `check_same_thread` defaults to sqlite3's own strict setting and should stay
    that way for everything that opens, uses and closes a connection inside one
    call — the lifespan, the services, the scripts, the terminal. Only `get_db`
    relaxes it, for the reason documented there.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    # foreign_keys is OFF by default in SQLite and is per-connection; required
    # for the checkins -> routine_items FK. journal_mode=WAL lets the phone read
    # while the desktop writes. busy_timeout briefly waits out writer contention.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def get_db():
    """Request-scoped connection, for `conn = Depends(get_db)` (#24 cut 5).

    One owner of the open/close plumbing every route handler used to spell out
    by hand. Deliberately FastAPI-free — it is an ordinary generator, so this
    module keeps no web-framework import and the contract is testable without
    a request.

    NOT for long-lived responses. A dependency's `finally` runs only after the
    response has finished, so a streaming or long-polling route would pin its
    connection for the whole stream; those keep an explicit `get_conn()` scoped
    to the work that actually needs the database. `get_conn` also stays the way
    in for non-request contexts: the lifespan, the services, and the scripts.

    `check_same_thread=False` is load-bearing, not a loosened default. FastAPI
    dispatches a sync generator dependency's setup and its teardown as two
    SEPARATE threadpool tasks, so the `conn.close()` below routinely runs on a
    different worker thread than the `get_conn()` above — under concurrency,
    most requests. The hand-written try/finally this replaced was immune
    because open, use and close all happened in one handler call on one thread.
    Relaxing the check is safe HERE and only here: the connection is per
    request and never escapes it, and setup, handler and teardown run strictly
    one after another, so no two threads ever touch it at the same time.
    """
    conn = get_conn(check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def immediate(conn: sqlite3.Connection):
    """A write transaction that takes the writer lock up front (#22).

    `with conn:` leaves sqlite3 in its legacy mode: the implicit `BEGIN` is
    DEFERRED and is emitted before the first DML, never before a `SELECT`. A
    path that reads a value, decides something in Python and then writes it
    back therefore reads outside the write lock, and two such paths interleave
    freely — the classic lost update. `BEGIN IMMEDIATE` moves the lock to the
    top, so the read and the write it feeds see one serialized version of the
    row. The other writer waits out `busy_timeout` and then raises
    `sqlite3.OperationalError`, which is the honest answer: nothing was lost.

    Use it only where the application needs the value in Python. Where the
    value is needed only inside SQL — `MAX(sort_order) + 10` — a single
    `INSERT ... SELECT` is cheaper and just as safe, because one statement
    already runs under the lock it takes.

    Two ways to hold it wrong, both pinned by tests:

    - It cannot nest. `BEGIN` inside a transaction is an error from SQLite
      with a message that says nothing about the caller, so the precondition
      is checked here instead.
    - Do not write `with conn:` inside the block. That context manager commits
      on exit, which would end THIS transaction early and hand the rest of the
      body to autocommit. Plain `conn.execute` calls only.
    """
    if conn.in_transaction:
        raise RuntimeError(
            "immediate() needs a connection that is not already in a transaction"
        )
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


# --- schema + migrations (sec13.1 / sec13.3) -------------------------------

SCHEMA_VERSION = 16

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
# routine_item; these columns added the Create-Habit fields (section is the
# existing group_name).
#
# Since #18 only `emoji` and `start_date` are still written and read: start_date
# bounds which days a habit appears on and where its statistics begin. The other
# four — frequency, goal, goal_days, reminder, constant_reminder — are LEGACY.
# No form posts them and no reader consults them; frequency's "weekdays" and
# "weekly" options never even had columns for their own parameters, and the app
# has no channel through which a reminder could fire. The columns are kept so
# values written before #18 survive untouched (and keep round-tripping through
# export/restore); do not add readers for them without building the scheduling
# engine that #18 tracks.
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
# and carry no "done" semantics (a class happens, it isn't completed — that's what the
# Habit tab is for).
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
    # Renumbering hazard: a DB that ran the uid step while it was numbered
    # v10 sits at user_version=10 WITHOUT retro_entries, so the landed v10
    # step above is skipped on its way here. The retro DDL is IF NOT EXISTS
    # throughout — re-running it converges that shape (fresh DBs no-op).
    conn.executescript(_SCHEMA_V10)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(lessons)")}
    if "uid" not in have:
        conn.execute("ALTER TABLE lessons ADD COLUMN uid TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_lessons_uid ON lessons(uid)")
    backfill_lesson_uids(conn)


# v12 — lesson_attempts (learn-bundle-spec.md §6, session D4): the authority
# for recorded learner attempts. Each row is written in ONE transaction with
# its `lesson_attempt` ledger event (the row stores the event's uuid so the
# attempts.jsonl projection can echo it, §6.2). `attempt_id` is the durable
# public identity (§3); the idempotency key is unique per lesson (§6.3) so a
# replayed submission maps back to the original row instead of duplicating.
# `created_at` is UTC ISO-8601 (§6.2 record shape — projection and authority
# carry the same string). Rows are immutable once written and never deleted:
# attempts are learning history (§6.4 — late data is flagged, not dropped).
_SCHEMA_V12 = """
CREATE TABLE IF NOT EXISTS lesson_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_id TEXT NOT NULL UNIQUE,
  event_uuid TEXT NOT NULL,
  lesson_id INTEGER NOT NULL REFERENCES lessons(id),
  lesson_uid TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  page_id TEXT NOT NULL,
  question_id TEXT NOT NULL,
  page_rev TEXT NOT NULL,
  answer TEXT NOT NULL,
  stale INTEGER NOT NULL CHECK(stale IN (0,1)),
  created_at TEXT NOT NULL,
  UNIQUE(lesson_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_attempts_lesson_created
  ON lesson_attempts(lesson_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_lesson_question
  ON lesson_attempts(lesson_id, question_id);
"""


def _migrate_to_12(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V12)


# v13 — bounded lesson-attempt projection cursor lookup (issue #58): the
# app-private durable sidecar advances by authority row id and retains the
# projection sort tail. These indexes let one append validate both anchors and
# ask for at most the next two rows without scanning unrelated attempts or
# historical answer bytes.
_SCHEMA_V13 = """
CREATE INDEX IF NOT EXISTS idx_attempts_lesson_cursor
  ON lesson_attempts(lesson_id, id);
CREATE INDEX IF NOT EXISTS idx_attempts_lesson_order
  ON lesson_attempts(lesson_id, created_at, attempt_id);
"""


def _migrate_to_13(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V13)


# v14 — lesson_assessments (S-DESIGN D-S1-1, phase S slice s1): the authority
# for what the TUTOR concluded, beside `lesson_attempts` (what the learner did).
# One table with a `kind` discriminator: the four kinds share writer, transport,
# validation skeleton and ledger event, and differ only in which references are
# required — which is exactly what the per-kind CHECKs below encode. Those
# CHECKs are deliberate schema-level self-enforcement: the typed authority must
# stay structurally valid under restore tooling or any future second writer, not
# only under the endpoint's own validation.
#
# `id` (rowid) is THE recency/ordering authority, exposed as `seq` in the event
# and the API — `created_at` is UTC-microsecond display metadata. Rows are
# append-only: a wrong record is corrected by a later row naming it in
# `supersedes` (or by a `retraction`), never by UPDATE or DELETE, so the fold in
# `app/services/assessments.py:active_state` is the current-state view and the
# table stays the full history.
_SCHEMA_V14 = """
CREATE TABLE IF NOT EXISTS lesson_assessments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  assessment_id TEXT NOT NULL UNIQUE,
  event_uuid TEXT NOT NULL UNIQUE,
  lesson_id INTEGER NOT NULL REFERENCES lessons(id),
  lesson_uid TEXT NOT NULL,
  sitting_id TEXT,
  mode TEXT NOT NULL DEFAULT 'tutoring'
       CHECK(mode IN ('tutoring','exam')),
  idempotency_key TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  kind TEXT NOT NULL
       CHECK(kind IN ('review','evidence','summary','retraction')),
  level TEXT,
  basis TEXT,
  attempt_id TEXT,
  question_id TEXT,
  concepts_json TEXT,
  note TEXT NOT NULL CHECK(length(note) > 0),
  next_action TEXT,
  supersedes TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(lesson_id, idempotency_key),
  -- The IS NOT NULL conjuncts are load-bearing, not belt-and-braces: SQLite is
  -- three-valued, so `NULL IN (...)` is NULL, `false OR NULL` is NULL, and a
  -- CHECK only fails on false. Without them a review with a NULL level, or an
  -- evidence with a NULL level/basis, would pass the constraint and defeat the
  -- reason these CHECKs exist (S-M1: stay structurally valid under restore
  -- tooling or a future second writer, not only under this endpoint).
  CHECK(kind != 'review' OR (attempt_id IS NOT NULL
        AND level IS NOT NULL
        AND level IN ('correct','partial','incorrect','unclear'))),
  CHECK(kind != 'evidence' OR (concepts_json IS NOT NULL
        AND level IS NOT NULL
        AND level IN ('seen','weak','developing','passed')
        AND basis IS NOT NULL
        AND basis IN ('attempts','artifacts','runs','live','mixed'))),
  CHECK(kind != 'summary' OR (level IS NULL AND attempt_id IS NULL)),
  CHECK(kind != 'retraction' OR (supersedes IS NOT NULL AND level IS NULL
        AND attempt_id IS NULL AND concepts_json IS NULL)),
  CHECK(kind = 'summary' OR next_action IS NULL),
  CHECK(kind = 'evidence' OR basis IS NULL)
);

CREATE INDEX IF NOT EXISTS idx_assessments_lesson_kind
  ON lesson_assessments(lesson_id, kind, id);

-- The active-state fold asks, for every row of a lesson, whether any sibling
-- names it in `supersedes`. Without this index that correlated lookup scans
-- the lesson's whole history once per row, so the fold — which s2 runs on
-- every write and s4 on every render — is quadratic in lifetime assessments.
CREATE INDEX IF NOT EXISTS idx_assessments_lesson_supersedes
  ON lesson_assessments(lesson_id, supersedes);
"""


def _migrate_to_14(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V14)


# v15 — the assessment projection's watermark (s2 L1). Deciding whether a
# reconcile can skip its rewrite costs one `MAX(id)` per lesson, and a replay
# can ask for it without limit (replays are outside the rate budget by
# design). The v14 indexes all order a lesson's entries by something other
# than the row id, so that query iterated the lesson's whole history; with
# `(lesson_id, id)` SQLite seeks straight to the last entry. Same index shape
# and same reason as v13's `idx_attempts_lesson_cursor`.
_SCHEMA_V15 = """
CREATE INDEX IF NOT EXISTS idx_assessments_lesson_seq
  ON lesson_assessments(lesson_id, id);
"""


def _migrate_to_15(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V15)


# v16 — the initialization marker (issue #17). Startup seeded demo habits,
# lists and tasks per table, each seeder asking only `COUNT(*) == 0` of its
# own table. That question cannot tell "never initialized" from "restored
# from a backup whose owner had deleted every task": both are empty, so the
# first boot after a restore quietly poured demo rows into real history and
# appended their events to the audit stream.
#
# The marker answers the question the row counts cannot. It is a one-way door
# per installation: `seeded_at` is written once, after the seeders have had
# their turn, and its presence alone decides that they never run again.
_SCHEMA_V16 = """
CREATE TABLE IF NOT EXISTS app_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# Every table startup would seed, plus the audit stream. A database holding a
# row in any of them is a live installation that was initialized long before
# this marker existed, so the migration records that fact instead of leaving
# it to be re-decided on the next boot.
_SEEDED_EVIDENCE = ("routine_items", "lists", "tasks", "events")


def _migrate_to_16(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_V16)
    seeded = any(
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
        for table in _SEEDED_EVIDENCE
    )
    if seeded:
        # An empty database is the only one that still needs the seeders, so it
        # is the only one that leaves this migration without a marker.
        meta_set(conn, SEEDED_AT, now_iso())


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
    (12, _migrate_to_12),
    (13, _migrate_to_13),
    (14, _migrate_to_14),
    (15, _migrate_to_15),
    (16, _migrate_to_16),
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
