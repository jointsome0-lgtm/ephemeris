"""Every bound on how much a single write may carry (issue #23).

Before this module the caps that existed were four integers spelled inline in
four services — 500 in `tasks.py`, 200 in `items.py`, 100 in `lists.py`, 500 in
`calendar_events.py` — and the notes beside those titles had no cap at all. So
"how long may a task title be?" was a grep, and "how long may a task note be?"
had no answer: a paste of a megabyte went into SQLite, into the ledger event,
into every export from then on, and into the search LIKE that scans it.

The numbers here are deliberately generous. This is a single-user personal
instance; a cap is not a writing-style opinion, it is the line past which one
paste stops being a note and starts being a data-import accident. A note that
reaches 10 000 characters is roughly four pages, which is well past anything
the field's textarea is shaped for.

Three rules the rest of the app depends on:

* **Characters, not bytes.** `len(str)` on the decoded form value, which is
  what the four title checks already counted. Byte length would make the same
  sentence "too long" in Cyrillic and fine in ASCII, and the person typing it
  cannot see the difference.
* **The service layer enforces.** `check` raises the *caller's* domain error —
  `TaskError`, `CheckinError`, … — because the routes already catch those and
  answer with a flash redirect (or a 422 in Mode B). A shared exception type
  would need a new handler in every route that writes text.
* **`MAX_BODY_BYTES` is a ceiling over the route caps, never a substitute.**
  The perimeter (`app/security.py`) refuses an oversized body before any route
  sees it; the Learn JSON endpoints keep their own, much smaller, per-route
  caps and their own refusal messages. `tests/test_130_limits.py` fails if this
  ceiling ever drops below the largest route cap, which would silently convert
  a route's precise 413 into the perimeter's blunt one.

Deliberately NOT moved here: the retro text bound (`services/retro.py`, 1 MB)
and the lesson title/URL bounds (`services/lessons.py`). Both belong to
documented contracts of their own — the retro spec and the bundle spec — and
folding them into this module would put one number in two normative places.
"""
from __future__ import annotations

# --- titles and names ------------------------------------------------------
# Unchanged values, moved from the four services that spelled them inline.

TASK_TITLE = 500
"""`tasks.title` — also the length the search index scans."""

ROUTINE_ITEM_TITLE = 200
"""`routine_items.title` — a habit name, shown in a fixed-width row."""

LIST_NAME = 100
"""`lists.name` — shown in the sidebar rail, which cannot scroll sideways."""

EVENT_TITLE = 500
"""`calendar_events.title` — same shape as a task title."""

# --- free text -------------------------------------------------------------
# New bounds. Nothing rejected here was ever accepted by a human on purpose.

TASK_NOTE = 10_000
"""`tasks.note` — the task detail pane's textarea."""

CHECKIN_NOTE = 10_000
"""`checkins.note` — one day's remark about one habit."""

EVENT_NOTE = 10_000
"""`calendar_events.note` — the event modal's textarea."""

FOCUS_NOTE = 10_000
"""`focus_sessions.note` — what one Pomodoro span was spent on."""

DAILY_NOTE = 20_000
"""`daily_notes.text` — the whole day's journal, so twice a per-item note."""

# --- request body ----------------------------------------------------------

MAX_BODY_BYTES = 2 * 1024 * 1024
"""Perimeter ceiling on an unsafe-method request body, in BYTES on the wire.

Bytes, not characters: nothing has decoded the body yet at the point this
applies. Two MiB comfortably clears the largest route cap (512 KiB, the Learn
artifact save) and every form this app renders, while keeping an unbounded
upload from being buffered by a route that forgot to bound itself.
`EPHEMERIS_MAX_BODY_BYTES` overrides it at startup; see app/security.py.
"""

LARGEST_ROUTE_CAP = 512 * 1024
"""The biggest per-route body cap in the app — the floor under any ceiling.

The routes that bound their own bodies are the Learn JSON endpoints (artifact
512 KiB, attempt 256 KiB, assessment 64 KiB, run 16 KiB) and this is the
largest of the four; `tests/test_130_limits.py` reads the real constants and
fails if that stops being true. Spelled here rather than imported from
`app/routers/learn.py` because the perimeter must not import a router.

It exists so the *override* obeys the same invariant the default does. A
ceiling set below this would not tighten anything — the route caps already
bind — it would only convert a Learn endpoint's typed JSON refusal, which the
lesson agent parses, into the perimeter's blunt plain-text 413.
"""

# --- retention -------------------------------------------------------------

EXPORT_KEEP = 30
"""How many `events-*.jsonl` exports `data/exports/` retains, newest first.

Every export is a full serialization of an append-only stream, so export N+1
contains everything export N did. Keeping them all means paying for the whole
history once per press of the button. Thirty is enough that a mistake noticed
weeks later still has a file behind it.

Backups (`data/backups/`) are a *separate* mechanism with a separate rule:
`scripts/backup_db.py --keep N`, run by the operator, because a backup set is
not reproducible from the current database the way an export is.
"""

# --- what the storage panel calls worrying ---------------------------------

BACKUP_STALE_DAYS = 7
"""Age past which the newest backup set is reported as stale on /export.

A week is what one forgotten cron job costs. The panel warns; it never writes
a backup itself, because a backup that runs on page load would be a surprise
write triggered by reading.
"""

FREE_SPACE_FLOOR = 1024 * 1024 * 1024
"""Free bytes on the data directory's filesystem below which /export warns.

One GiB is several times the largest thing this app writes in one go (a full
backup set: the database plus a tar of the instance files), so the warning
arrives while there is still room to take one.
"""


def check(value: str | None, limit: int, label: str, error: type[Exception]) -> None:
    """Raise `error("<label> too long")` when `value` exceeds `limit` characters.

    `None` and the empty string pass: absence is the callers' own business, and
    every one of them already rejects or defaults an empty value before or
    after this call. The message shape matches the four title checks that
    predate this module, so no existing error text changes.
    """
    if value is not None and len(value) > limit:
        raise error(f"{label} too long")
