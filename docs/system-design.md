# Ephemeris — System Design Document

Status: Living implementation design and v0.1 baseline
Primary target: Linux browser
Storage model: local-first SQLite  
Product type: personal activity/routine/path tracker  
UX reference: TickTick-like execution interface, not TickTick clone

---

## 1. Summary

Ephemeris is a small personal tracker for daily routine, activity check-ins, simple history, and future integration with personal systems.

The app should initially replace the overloaded/limited TickTick usage for personal tracking, while preserving the main thing that makes TickTick useful: a fast operational interface.

The product is not a task manager, not a full habit tracker clone, and not a personal operating system. The first version is a small daily execution surface backed by our own data.

Core idea:

```text
Open Today → mark routine/status → optionally add note → save → leave.
````

The app should work from:

```text
Linux browser
```

Initial deployment:

```text
Linux machine = local server and browser client
SQLite = source of truth
JSONL/Markdown = export layer
Git = backup/history later
```

---

## 2. Product Goal

Build a personal Ephemeris with a TickTick-like execution experience, but with our own data model:

```text
routine items
daily check-ins
status levels
daily notes
history
event log
exports
```

The main purpose is to track P0 Core Routine and small daily actions without being blocked by TickTick habit limits, subscriptions, sync issues, or proprietary data model constraints.

---

## 3. Core Philosophy

We do not need TickTick as a service.

We need:

```text
TickTick-like interaction speed
+
our own memory
+
future integration layer
```

The UX goal is to capture what feels useful in TickTick:

```text
fast daily view
compact rows
clear completion state
low friction
mobile-friendly layout
quick check-in
easy return tomorrow
```

But we must not copy:

```text
TickTick branding
TickTick assets
TickTick icons
TickTick CSS
TickTick exact layouts
TickTick proprietary text
TickTick paid-feature bypasses
```

We are extracting UX patterns, not cloning the product.

---

## 4. Initial Scope

### 4.1 MVP Scope

MVP must include:

```text
1. Today page
2. Routine item list
3. Four check-in statuses:
   - full_done
   - light_done
   - skipped
   - failed
4. Optional note per item
5. Daily note
6. History by date
7. Manage routine items
8. JSONL export
9. SQLite persistence
10. Mobile-first responsive UI
```

### 4.2 First Real Use Case

Track P0 Core Routine:

```text
Sleep
Food
Sport / show up
Evening walk
Daily output
```

Optional small extras:

```text
Cleaning 15 min
Rustlings 15 min
TypeScript 15 min
CodeCrafters 15–30 min
```

Large projects should not be modeled deeply in v0.

Story, Atlas, BitGN, worldbuilding, learning paths, and agents are future integrations, not MVP scope.

---

## 5. Non-Goals

MVP must not include:

```text
React
native Android app
Electron app
auth
multi-user accounts
VPS deployment
S3
cloud sync
Telegram bot
AI assistant
calendar sync
notifications
gamification
complex graphs
social features
worldbuilding model
Story translation model
agent workflows
complex path management
```

The app should not become another large infrastructure project.

---

## 6. User Model

Primary user:

```text
one person
Linux desktop/laptop
wants low-friction daily tracking
wants future machine-readable history
does not want to depend on TickTick long-term
```

Primary environment:

```text
same-machine loopback
```

---

## 10. System Architecture

### 10.1 MVP Architecture

```text
Linux Browser
      ↓
FastAPI web app
      ↓
SQLite database
      ↓
JSONL / Markdown export
      ↓
Git backup later
```

### 10.2 Initial Deployment

Run locally on Linux over loopback:

```bash
# Loopback is the only supported binding:
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

A diary-bearing instance (sec35) concentrates the most sensitive personal text
in the app, so it and its full unfiltered export replay stay behind the same
loopback boundary. Export lines include private entries; the selfos adapter is
the privacy gate.

Open on Linux:

```text
http://localhost:8000
```

---

## 11. Tech Stack

MVP stack:

```text
Python
FastAPI
SQLite
Jinja2
vanilla HTML
vanilla CSS
optional HTMX later
vanilla TypeScript/JS (MVP-allowed; framework-free progressive enhancement; Mode B, sec16.4)
```

Avoid in v0:

```text
React
Next.js
Tailwind dependency
Docker
Postgres
Redis
Celery
S3
OAuth
mobile native app
```

Reason:

```text
The first version should be understandable, hackable, and disposable.
```

---

## 13. Data Model

### 13.1 Core Tables

All TEXT dates are `'YYYY-MM-DD'`; all `*_at` timestamps are ISO-8601 with
offset. See 13.3 for the timezone rule, connection PRAGMAs, ordering, and
migrations.

```sql
CREATE TABLE routine_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL CHECK(length(trim(title)) > 0),
  group_name TEXT NOT NULL DEFAULT 'P0 Core Routine'
             CHECK(length(trim(group_name)) > 0),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deactivated_at TEXT            -- set when active flips to 0; never hard-delete
);

CREATE TABLE checkins (
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

CREATE TABLE daily_notes (
  date TEXT PRIMARY KEY,
  text TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,        -- ISO-8601 with offset
  type TEXT NOT NULL,
  payload_version INTEGER NOT NULL DEFAULT 1,
  payload_json TEXT NOT NULL
);

CREATE INDEX idx_checkins_date ON checkins(date);
```

Later schema versions add the task-manager tables (`lists`, `tasks`, `tags`,
`task_tags` — v2, sec30.1), habit fields on `routine_items` (v3, sec31),
`focus_sessions` (v4, sec15.4), `calendar_events` (v5, sec32 §3 — the row IS
the recurring series; occurrences are expanded on read, never materialized),
the Learn `lessons` table and its bundle-navigation columns (v6/v7),
`focus_sessions.lesson_id` (v8), the timer's own tables (v19, sec34 — the
Pomodoro modes converted to countdown/open, target columns for habit and task,
and `focus_runs` for the one timer currently running), and a persistent unique
`events.uuid` (v9 —
service-owned identity stamped by `append_event()` and returned to the caller;
pre-v9 rows are backfilled once by the migration, payload history untouched).

### 13.2 Status Enum

Allowed check-in statuses:

```text
full_done
light_done
skipped
failed
```

Meaning:

```text
full_done  = normal/full version completed
light_done = minimum viable version completed; chain preserved
skipped    = conscious skip
failed     = forgot, avoided, or day broke
```

This is important. The product is not binary done/undone.

The `light_done` state is a first-class concept.

### 13.3 Schema Rules, Connection Policy, Timezone, Migrations

Timezone / ledger day (the single owning clock):

```text
- A configured APP_TIMEZONE (env var; default = host local zone) is the authority.
- 'today' (the default day AND the day-boundary) = date(now(APP_TIMEZONE)),
  formatted 'YYYY-MM-DD', computed SERVER-SIDE only. The client never DEFINES
  what "today" is.
- Reads (GET /today, GET /history) MAY omit the day ⇒ the server uses 'today'.
- A read/write MAY target an explicit prior day (e.g. fixing yesterday from
  History, sec16.4): that 'date' is a server-rendered, server-VALIDATED selector
  (format-checked, never after today, never trusted as a client clock) — not a
  free-form client date. Writes (POST /checkins) REQUIRE the date and reject a
  missing one, so a History edit can never silently retarget to today.
- checkins.date / daily_notes.date store that resolved 'YYYY-MM-DD'.
- Row/event *_at timestamps are ISO-8601 with offset (e.g. 2026-06-05T21:10:00+03:00).
- Add a boundary test at 23:59 / 00:01 local time.
```

Connection policy (every SQLite connection, in db.py):

```text
- PRAGMA foreign_keys = ON;   # OFF by default in SQLite — required for the checkins FK
- PRAGMA journal_mode = WAL;  # one browser tab can read while another writes
- PRAGMA busy_timeout = 5000; # brief writer contention waits instead of erroring
```

Deterministic ordering:

```text
- Today / Manage list: ORDER BY group_name, sort_order, id
- Export:              events ORDER BY id, then calendar_events ORDER BY id
```

Migrations (no framework):

```text
- PRAGMA user_version holds the schema version (start at 1).
- On startup db.py runs ordered, idempotent migration steps for any version gap.
- A schema change must NEVER require deleting the ledger to upgrade.
```

---

## 14. Event Model

Every meaningful change should append an event.

Example routine check-in event:

```json
{
  "timestamp": "2026-06-05T21:10:00+03:00",
  "type": "routine_checkin_upserted",
  "payload_version": 1,
  "payload": {
    "date": "2026-06-05",
    "routine_item_id": 1,
    "item_title": "Evening walk",
    "status": "light_done",
    "note": "Short walk, but showed up."
  }
}
```

Example daily note event:

```json
{
  "timestamp": "2026-06-05T22:00:00+03:00",
  "type": "daily_note_updated",
  "payload_version": 1,
  "payload": {
    "date": "2026-06-05",
    "text": "System worked because check-ins were quick."
  }
}
```

The event log exists for future integration with:

```text
personal review
agent-readable memory
Markdown exports
Git history
later Story/Atlas integrations
```

### 14.1 Event Log Role & Rules

Role (decided for v0): the event log is an **append-only audit / derived feed**.
The typed tables remain the source of truth; the JSONL export (sec18) serializes
events plus the calendar-series snapshot exception (sec32 §8). Restore can
replay only the semantic subset identified in `docs/restore-from-export.md`.

Atomicity: every state change writes the table row AND its event in ONE SQLite
transaction (roll back both on failure). See sec16.4.

Event type catalog (`payload_version = 1`):

```text
routine_checkin_upserted   {date, routine_item_id, item_title, status, note}
routine_checkin_cleared    {date, routine_item_id, item_title}
daily_note_updated         {date, text}
routine_item_created       {routine_item_id, title, group_name, sort_order}
routine_item_updated       {routine_item_id, title, group_name, sort_order}
routine_item_deactivated   {routine_item_id, title}
```

`item_title` in payloads is an IMMUTABLE SNAPSHOT at event time, so the durable
log preserves what an item was called then, even after a later rename.

---

## 15. Routes

### 15.1 Today

```text
GET  /
GET  /today
POST /checkins
POST /daily-note
```

Purpose:

```text
show today's routine items
mark statuses
edit item notes
edit daily note
```

### 15.2 History

```text
GET /history
GET /history?date=YYYY-MM-DD
```

Purpose:

```text
view check-ins and the daily note for a selected date
correct a prior day (check-ins and note) — writes are date-targeted, sec16.4
navigate previous/next day
```

Rules:

```text
- Default date = today (APP_TIMEZONE) when no ?date is given.
- Validate ?date as YYYY-MM-DD; reject malformed; do not allow dates after today.
- History is CHECKINS-FIRST: list every check-in stored for the date, joining
  routine_items REGARDLESS of active (a deactivated item with a check-in that day
  still shows). Never filter active=1 in History.
- Display uses the item's CURRENT title/group; the immutable as-at-time name is
  preserved in the event log (sec14.1). (Future upgrade: snapshot the name onto
  the checkin row.)
- prev/next move the date by one calendar day in APP_TIMEZONE.
- Empty state: if nothing is logged for the date, show an explicit
  "nothing logged" message, not a blank page.
```

### 15.2a Habit Detail

```text
GET /habit/{id}
GET /habit/{id}?month=YYYY-MM
```

Purpose:

```text
per-item streaks + stat cards + monthly calendar heatmap + habit log (sec16.6)
```

Rules:

```text
- Read-only / derived from checkins (sec14); no writes here.
- Unknown id -> 404; non-integer id -> 422 (path type). Works for deactivated
  items too (history stays viewable).
- ?month defaults to the current month; malformed -> current month; the "next
  month" control is disabled once it would point past the current month.
```

### 15.3 Manage Items

```text
GET  /items
POST /items
POST /items/{id}/edit
POST /items/{id}/deactivate
POST /items/{id}/reactivate
```

Purpose:

```text
add routine item
edit title/group
deactivate item (soft retire)
reactivate a previously deactivated item
```

Rules:

```text
- add / edit / deactivate / reactivate each append the matching event (sec14.1)
  in one transaction (created -> routine_item_created, edit & reactivate ->
  routine_item_updated, deactivate -> routine_item_deactivated).
- edit sets updated_at; deactivate sets active=0 AND deactivated_at (soft retire);
  reactivate sets active=1 AND clears deactivated_at.
- New items get sort_order = MAX(sort_order)+10 within their group; group defaults
  to "P0 Core Routine" when blank.
- title is required (trimmed, non-empty, <=200 chars); a rejected write redirects
  back to /items with a ?flash= message and writes nothing (no partial row).
- Never hard-delete an item that has check-ins; deactivation keeps history joinable.
  Deactivated items are hidden from Today/History but listed (and reactivatable)
  in a "Deactivated" section on /items.
- All POSTs are same-origin guarded (docs/security-model.md) and use 303 PRG redirects.
```

### 15.4 Export

```text
GET  /export
POST /export/jsonl
```

Purpose:

```text
export JSONL = append-only events stream plus calendar-series snapshots
(sec18.1). Every check-in and daily note is already an event (sec14.1), so
they ride along as event payloads; `calendar_events` rows are the explicit
snapshot exception (sec32 §8).
```

---

## 16. UI Design

### 16.1 Main UX Principle

The Today screen must be faster than thinking.

Target interaction:

```text
open app
tap statuses
write optional note
close app
```

No complex navigation.

No required project planning.

No guilt dashboard.

### 16.2 Today Screen Layout

Mobile-first wireframe:

```text
┌──────────────────────────────────┐
│ Today                            │
│ Jun 6 · 2/5 kept                 │
│ Su Mo Tu We Th Fr [Sa]           │  week strip (today highlighted,
│ 31  1  2  3  4  5  [6]           │  tap a day -> that day's view)
├──────────────────────────────────┤
│ ⌄ P0 Core Routine            5   │  collapsible section + count
│ ┌──────────────────────────────┐ │
│ │ 😴 Sleep      ·············(✓)⋮│ │  avatar · name+streak · 7-day dots
│ │   🔥 24 days  best 24          │ │  (active/today dot = the affordance)
│ └──────────────────────────────┘ │
│ ┌──────────────────────────────┐ │
│ │ 🍽 Food       ·············( )⋮│ │  dots colour-coded by the 4 statuses;
│ │   🔥 20 days  best 20          │ │  past = history, future = faint
│ └──────────────────────────────┘ │
│   …tap a card to reveal:         │
│   📊 Stats & calendar →          │
│   [✓Full][◐Light][–Skip][✕Fail]  │
│   [⌫Clear]   [ note…     ][Save] │
├──────────────────────────────────┤
│ Daily note                       │
│ [                               ]│
├──────────────────────────────────┤
│ ◎ Today   ◷ History    ≡ Items   │
└──────────────────────────────────┘
```

Row contract (390px) — the row mirrors a TickTick habit row (avatar · name +
streak stats · weekly dots), but the dots are coloured by our four-status model
so a row shows more than binary done/not-done (pattern-level only, no copied assets):

```text
- Anatomy: [emoji/letter avatar] [name + streak line] [7-day status dots] [⋮].
  The streak line is "🔥 <current> days · best <best>" (services.stats); the flame
  is muted when the current streak is 0.
- The 7 dots align to the week strip's 7 days. Each is coloured + glyphed by that
  day's status (✓ full / ◐ light / – skip / ✕ fail); empty past days and future
  days render faint. They give at-a-glance week history without opening anything.
- ONE primary affordance per row: the ACTIVE day's dot (today on
  Today; the selected day on History) is a larger button — tap = full_done (1 tap,
  re-tap clears). The other six dots are read-only history (the week strip handles
  navigation). Active dot ~34px; comfortable on touch and the obvious target.
- light_done / skipped / failed, the per-item note, AND a "Stats & calendar →" link
  to the habit detail (sec16.6) live in a panel revealed by tapping the card (Mode A:
  the card IS a native <details>; Mode B enhances it). Keeps light_done one gesture
  away without crowding the row.
- Status reads as colour AND glyph for grayscale / colour-blind users (sec16.5).
- The header "<kept>/<total> kept" reflects full_done + light_done; the count AND
  the row's streak are recomputed live in Mode B from the check-in's JSON response
  (the server returns current/best streak so the number is correct beyond 7 days).
- Sections are collapsible group headers with a count. A Sun–Sat week strip
  at the top moves between days (today highlighted, future days disabled).
- Dark theme by default; our own styling/assets only.
- Empty state (no active items): link to Manage Items, not a blank page.
- Validated against the MVP speed target (5-10 items in <60s) at 390px.
```

### 16.3 Desktop Layout

Desktop can use more width, but must not become a different product. The choice
between the mobile and desktop presentations is made by **responsive CSS (a single
`@media (min-width: 900px)` breakpoint), NOT by user-agent sniffing or separate
routes** — one set of routes/templates, the layout reflows by viewport width. This
survives window resize and split-screen, and keeps Mode A/B and the write contract
identical on every device.

```text
Desktop (>= 900px)                         Mobile (< 900px)
┌──────────┬───────────────────────────┐   ┌────────────────────┐
│ ◫ brand  │ Today                     │   │ Today              │
│          │ Jun 6 · 2/5 kept          │   │ Jun 6 · 2/5 kept   │
│ ◎ Today  │ Su..[Sa] week strip       │   │ week strip         │
│ ◷ History│ ┌─────────────┬─────────┐ │   │ sections + cards   │
│ ≡ Items  │ │ sections +  │ daily   │ │   │ …                  │
│          │ │ cards (1fr) │ note    │ │   │ daily note         │
│ (sticky  │ │             │ status  │ │   ├────────────────────┤
│  sidebar)│ │             │ key     │ │   │ ◎ Today ◷ Hist ≡ It│
└──────────┴─┴─────────────┴─────────┴─┘   └────────────────────┘
                                           (bottom tab bar)
```

```text
- Desktop: a sticky left sidebar (~240px) holds the brand + primary nav
  (Today / History / Items); the bottom tab bar is hidden. The day view becomes a
  two-column grid: check-in sections (minmax(0,1fr)) on the left, a right rail
  (~330px, sticky) with the daily note + a status-key legend.
- Mobile: the sidebar is hidden, content is a single column, the right rail stacks
  below the sections, and primary nav is the bottom tab bar (P10/P11).
- Same DOM for both; only display/grid rules differ across the breakpoint. The
  Items screen is one centered column (max ~760px) in both presentations.
```

### 16.4 Status & Note Write Contract

Each item has AT MOST one check-in row per date (enforced by
`UNIQUE(date, routine_item_id)`); a row that exists carries exactly one non-null
status. No row = the untouched / zero-status state (see Clear / undo below).

One endpoint, `POST /checkins`, serves both the status tap and the per-item note. It behaves identically in two client modes; the server contract below is the same in both.

Request fields:

```text
date              required  YYYY-MM-DD target day. Server-rendered into the
                            form and RE-VALIDATED on POST (format; never after
                            today) — a validated selector, never a trusted
                            client clock (sec13.3). Rejected if missing.
routine_item_id   required
status            optional  one of: full_done | light_done | skipped | failed
note              optional  free text
```

Server behavior (authoritative, mode-independent) — all in ONE SQLite transaction:

```text
1. Validate status against the four-value enum (reject anything else).
2. Upsert the checkin on (date, routine_item_id):
   - insert if absent (set created_at, updated_at)
   - update if present (preserve created_at, bump updated_at)
   - 'status' changes only if the status field is present in the request
   - 'note'   changes only if the note field is present in the request
     (an absent field leaves that column untouched)
3. Append the event in the SAME transaction (routine_checkin_upserted).
   Roll back BOTH writes on any failure.
```

Clear / undo:

```text
Tapping the already-selected status (or an explicit Clear) deletes the
checkin row for that (date, item) and appends routine_checkin_cleared,
returning the item to the untouched state. This is the only undo path;
'status' stays NOT NULL, so there are no note-only rows in v0.
```

Note ordering: the flow is status-first (sec16.1). A note attaches to an
existing check-in; if no row exists for that (date, routine_item_id) — i.e. no
status is set for the SUBMITTED target date (not necessarily today) — the note
save is rejected with a hint to pick a status first. (Only if note-only rows are
wanted later: make `status` nullable.)

Mode A — no JavaScript (baseline, always works):

```text
- Each status is a tiny <form method="post" action="/checkins">
  carrying hidden date, routine_item_id, status.
- The per-item note is its own small form (date, routine_item_id, note)
  with a Save button.
- Server responds 303 See Other (POST-redirect-GET: refresh-safe; the #anchor
  restores scroll to the same row). The redirect target tracks the WRITE's date:
  a Today write -> /today#item-{id}; a History (prior-day) write ->
  /history?date=YYYY-MM-DD#item-{id}, so editing a past day stays on that day.
  Full page re-render; status highlight comes from the row.
```

Mode B — clean TypeScript (progressive enhancement over Mode A):

```text
- A small framework-free script (static/app.js) intercepts the same forms:
  - status dot tap -> fetch POST /checkins {date,item,status=full_done} (re-tap clears)
  - choose another status in the panel -> fetch POST /checkins {date,item,status}
  - note blur -> fetch POST /checkins {date,item,note} with a transient "saved" cue
- Requests carry header `X-Partial: 1`; the server returns JSON
  {ok,item_id,status,note} (or {ok:false,error} -> toast on a 422). The script
  updates that row in place (dot glyph/colour, choices, meta, note value).
- No full reload: scroll, keyboard, and other rows' unsaved text are preserved.
  This is the primary path for the <60s target; Mode A stays correct but
  is slower (a full reload per save).
- If the script is absent/disabled, the plain forms (Mode A) still work.
```

The endpoint, validation, transaction, and events are identical across both
modes; TypeScript changes only transport and feedback, never the data
contract. No modal is required in either mode.

Daily note write (`POST /daily-note`) follows the SAME date model. Request =
{date, text}; `date` is required, server-rendered and RE-VALIDATED (format,
never after today), exactly like /checkins (sec13.3). One transaction: upsert
`daily_notes` on its `date` primary key, then append `daily_note_updated` —
roll back both on failure. History MAY correct a prior day's note: Mode A
redirects by the write's date (/today or /history?date=YYYY-MM-DD), Mode B swaps
the note block in place. Saving empty text stores an empty note (a
`daily_note_updated` with empty text); there is no separate clear event for
daily notes.

### 16.5 Visual Semantics

The visual language should distinguish:

```text
full_done  = completed
light_done = saved the chain
skipped    = intentional skip
failed     = problem / missed
```

Do not overemphasize failure.

The app should support recovery, not shame.

### 16.6 Habit Detail & Streaks

Tapping a row's "📊 Stats & calendar →" link opens `GET /habit/{id}` — the
per-item motivation/analytics surface. This mirrors TickTick's habit detail pane in
PATTERN only; the data is ours and, thanks to the four-status model, the
heatmap is richer than a binary done/not-done grid.

Layout (single centered column; stat cards spread to one row on desktop):

```text
← Today
😴 Sleep   · P0 Core Routine
┌─────────┬─────────┬─────────┬─────────┐
│✅ Total  │🔥 Current│🏆 Best   │🎯 Month  │   stat cards (services.stats)
│  24 days │  24 days │  24 days │  100 %   │
└─────────┴─────────┴─────────┴─────────┘
‹  June 2026  ›                              month nav (no future months)
Su Mo Tu We Th Fr Sa                         monthly heatmap: each in-month day is
[✓][✓][✓][✓][✓][✓][ ] …                       a cell coloured/glyphed by status;
                                             today is ringed, out-of-month faint
[legend: Full · Light(keeps) · Skip(neutral) · Fail]
Habit log: <date> <glyph> <note>             check-ins that carry a note
```

Streak semantics (the differentiator — `services.stats`):

```text
full_done / light_done   KEEP the chain (a "light" minimum day still counts —
                         this is exactly what TickTick's binary model cannot do).
skipped                  NEUTRAL — a conscious rest/skip day: preserves the streak
                         but does not extend it (the "skip day" pattern).
failed / empty past day  BREAK the streak.
empty `today`            PENDING — does not break it (the day isn't over).

current streak = consecutive kept days counting back from today (per rules above).
best streak    = longest such run ever. total kept = count of full+light days.
month rate     = kept days / elapsed days this month (so an in-progress month is
                 not penalised for days that haven't happened yet).
```

All of the above is DERIVED from the `checkins` table (sec14) — no new stored
state. Stats are read-only; the only writes remain the sec16.4 check-in contract.

---

## 17. Routine Item Management

MVP item fields:

```text
title
group_name
active
sort_order
```

Example groups:

```text
P0 Core Routine
Extra
Background
```

Seed items:

```text
Sleep
Food
Sport / show up
Evening walk
Daily output
```

---

## 18. Export Design

### 18.1 JSONL Export

Export path:

```text
data/exports/events-YYYY-MM-DD-HHMMSS.jsonl
```

Each line:

```json
{"timestamp":"...","type":"...","payload_version":1,"payload":{...}}
```

Contract (decided for v0): export is the append-only `events` table serialized
to JSONL, one event per line, ORDERED BY id, plus the calendar-series snapshot
records below. The export omits `events.id`; several typed tables also have
incomplete or absent journal coverage. It therefore supports only the semantic
restore subset in `docs/restore-from-export.md`, not full-fidelity recovery or
idempotent redelivery. Each line carries `payload_version` for
forward-compatibility. (Future option: a discriminated full-table snapshot with
stable record IDs, `record_type`, and `schema_version`.)

The explicit snapshot exception (sec32 §8): the export also appends one
`calendar_event_series` line per `calendar_events` row (including soft-archived
ones), because series-update audit events journal only id+title — the audit
stream alone can't rebuild a recurrence rule. The series rows are the source of
truth; expanded occurrences are never exported.

`POST /export/jsonl` writes the file above AND streams it back as a download so
the browser can save it; `GET /export` renders a one-button page.

**Retention (issue #23).** `data/exports/` keeps the `limits.EXPORT_KEEP`
newest `events-*.jsonl` files and drops the rest after every write. This is
safe only because of the contract above: the stream is append-only, so the
newest export contains everything its predecessors did. Full backup sets in
`data/backups/` are a different mechanism with a different rule — the operator's
`scripts/backup_db.py --keep N` — because a backup set is a point in time the
current database cannot reproduce. Do not merge the two.

`limits.EXPORT_KEEP` is a floor rather than an exact count, on purpose. An
export is streamed to the browser by a response that outlives the call which
wrote it, so retention never removes a file touched within
`limits.EXPORT_GRACE` seconds — that covers the export in hand *and* one
written by an overlapping request, with no shared state between them. It also
makes a backward clock step (DST, an NTP correction) harmless: a fresh export
stamped behind the retained set is protected by when it was written, not by how
its name sorts. A burst can leave the directory a few files over the count for
a minute; the next export outside that window clears them.

`GET /export` also renders a read-only storage panel (issue #23): database
size, event count, the newest backup set's date and size, the export count and
their total size, and free disk space. It warns when there is no backup set,
when the newest one is older than `limits.BACKUP_STALE_DAYS`, or when free
space is under what the next backup set would need — the measured database plus
the instance archive of the last set, never less than
`limits.FREE_SPACE_FLOOR`, because a fixed number cannot describe an unbounded
ledger. It reads only — a GET in this app is
side-effect-free by contract (`app/security.py`), so it never writes a
backup or prunes anything.

The panel counts a backup set only when `scripts/backup_db.py` would accept its
manifest: current `manifest_version`, parseable `created_at`, and a `files`
object naming both the `database` and `instance` members. Anything else is
skipped and the search falls back to the next-newest set, because the question the panel answers is
"is there a backup I could restore?" — a set the bundled tooling would decline
must not silence the missing-backup warning.

### 18.3 Field and body limits

`app/limits.py` is the one place that says how much a single write may carry:
the four title bounds (task 500, habit 200, list 100, event 500 characters,
unchanged since they were inline), the free-text note bounds (task, check-in,
event and focus notes 10 000 characters; the daily note 20 000), and the
perimeter's request-body ceiling. Lengths are counted in **characters**, not
bytes, so the same sentence is not shorter in Cyrillic than in ASCII. The
checks run in the service layer, where the domain errors the routes already
render are raised. The body ceiling is documented in
`docs/security-model.md`. Pagination for the history views is deliberately not
part of this: it is a UI decision, not a limit.

---

## 26. Relationship to Other Systems

### TickTick

```text
UX reference only
temporary operational benchmark
not source of truth
```

### Ephemeris

```text
source of truth for routine/check-ins/events
```

### Story

Current meaning:

```text
novel translation project
```

Possible future meaning:

```text
story/world support system
branching worlds
canon notes
characters
versions
generation support
```

Story is not the same thing as agent workflows.

### Agent Systems

Supporting infrastructure only:

```text
automation
quality checks
review flows
memory readers
```

### Atlas

Atlas is a separate knowledge-state engine and viewer. Ephemeris may embed its
configured URL; it does not build or parse the Atlas graph.

Not part of MVP.

### Exp2Res

The verified global Mirror and unanswered Gap Questions remain an Exp2Res-owned
view. Ephemeris may embed its configured URL; it does not parse Exp2Res
assessment companion files.

Ephemeris does, however, *capture* retrospectives locally — journaled
`retro_entries` a future selfos adapter converts for Exp2Res import; see
[docs/retro-spec.md](retro-spec.md) (sec33). The boundary holds: ephemeris
never parses or calls Exp2Res, it only exports its own ledger.

The Diary tab (sec35, [docs/diary-spec.md](diary-spec.md)) is the second
capture surface on the same boundary: entries journal locally and ride the
export, and when `SELFOS_EXP2RES_URL` is set the tab renders the Exp2Res
gap-questions view by URL — nothing more; answers are ordinary diary entries.

Integration v1 embeds peer views on the same machine over loopback. Ephemeris
remains usable when either peer URL is unset.

Not part of MVP.

---

## 30. Task Manager Layer (TickTick clone) — scope change 2026-06-05

The v0.1 draft originally said *do not build a TickTick clone / full task manager*. The user
revisited that after seeing the full TickTick Today screen and explicitly chose a
**full TickTick clone** (Tasks + Habits + Lists + Tags + Filters +
Countdown + Inbox + the 3-pane app shell). This section supersedes the draft's
"do not build" line for that decision. The habit layer (§16.2/§16.6) is kept
intact and folded in as one section of the new Today, plus its own Habit tab.

### 30.1 Data model (schema v2, see `db.py` `_SCHEMA_V2`)

Added alongside the unchanged habit tables (`routine_items`, `checkins`):

- `lists(id, name, emoji, kind IN('inbox','list'), sort_order, created_at, updated_at, archived_at)`
  — exactly one built-in `inbox`; user lists are **soft-archived** (tasks reparent
  to Inbox), never hard-deleted, mirroring the routine-item rule.
- `tasks(id, title, list_id→lists, note, due_date, priority 0–3, kind IN('task','countdown'),
  completed_at, sort_order, created_at, updated_at)` — completion is a reversible
  toggle (`completed_at` timestamp ⇄ NULL); nothing is hidden/destroyed
  (recovery-not-shame, §16.5).
- `tags(id, name UNIQUE)`, `task_tags(task_id, tag_id)` — wiring present; Tags/Filters
  UI is a later milestone (T4).
- (v5, sec32) `calendar_events` — timed/recurring calendar series, deliberately
  SEPARATE from `tasks` (no completion semantics; spec §13.1): the row is the
  series, occurrences expand on read in `services/calendar_events.py`.

Migrations stay append-only (`PRAGMA user_version`, now 2). Each task/list write
appends its `events` row (task_created / task_completed / task_reopened /
task_status_changed (§30.5) /
task_updated / list_created / list_updated / list_archived) in **one** `with conn:`
transaction (§14.1).

### 30.2 Information architecture (the 3-pane shell)

`base.html` is now `.tt-shell` = **icon rail | list-sidebar | content | detail**.

- **Icon rail** (`.rail`, far left): ✓ Tasks → `/today`, 🔥 Habit → `/habits`,
  ≡ Manage → `/items`. Active state via the `rail` context var.
- **List-sidebar** (`.listbar`, `{% block middle %}`, tasks pages only): smart lists
  **Today / Next 7 Days / Inbox** (with open counts) + user **Lists** (emoji + count)
  + **Completed**.
- **Content**: the active list's task sections.
- **Detail** (`.detail`, `{% block detail %}`): server-rendered when `?sel=task-{id}`
  or `?sel=habit-{id}` is present; absent ⇒ `.detail-empty` (`display:none`, content
  reclaims the width).

Responsive: below 900px the rail + list-sidebar collapse to the existing bottom-nav
(Today / Habit / Manage), and the detail opens as a full-screen overlay.

Routes — **tasks** (all render `tasks.html`): `GET /` & `/today` (Countdown / Habit /
Tasks / Completed sections), `/next7` (grouped by day), `/list/{id}`, `/completed`.
**Habits** (unchanged behaviour, repointed): `/habits` = rich day view (was `/today`),
`/history?date=`, `/habit/{id}` = full detail page. The detail page and the inline
pane share `_habit_detail.html` via `_habit_detail_ctx()` (the route passes
`month_prev_url`/`month_next_url` so month-paging stays in context).

### 30.3 Tasks write contract

- `POST /tasks {title, list_id?, due_date?, return_to}` → create (defaults to Inbox).
- `POST /tasks/{id}/complete {return_to}` → reversible toggle; `X-Partial:1` ⇒ JSON
  `{ok, task_id, completed}` (Mode B), else 303 PRG to `return_to`.
- `POST /tasks/{id}/update {title, note, due_date, priority, list_id, return_to}` → patch.
- All carry the same-origin guard (docs/security-model.md) and a `return_to` (validated same-origin path)
  so the post-redirect lands back on the originating list / open pane.
- Habit check-ins from the compact rows on the tasks page reuse `POST /checkins` with a
  `return_to` (forms marked `data-native` so `app.js` lets them submit Mode A and the
  page reloads in place rather than running the habit-row JS).

### 30.4 Staged plan

- **T1 (done):** data model + 3-pane shell + Today-as-tasks (sections + quick-add +
  inline detail pane) + Lists/Inbox/Next7/Completed views + habit rows + habit pane.
- **T2:** Lists CRUD UI (create/rename/archive) from the sidebar; Trash.
- **T3:** richer task detail (subtasks, reminders) + dedicated Countdown editor.
- **T4:** Tags + Filters + search.
- **T5 (optional):** Calendar view.

### 30.5 Kanban board (schema v18, #53) — 2026-08-07

Owner decision 2026-07-19: Tasks stays, converted into a **board**. `GET /board`
is now the primary Tasks view and the destination of the rail's Tasks icon; the
older views (`/today`, `/next7`, `/list/{id}`, `/completed`) are unchanged and
still linked from the calendar and the day view.

- **Model.** `tasks.status TEXT NOT NULL DEFAULT 'backlog' CHECK(status IN
  ('backlog','doing','done'))`. Three fixed columns, no configuration and no WIP
  limits. `completed_at` remains the single owner of *when* a task was finished
  — statistics, `/completed` and the export are untouched — and one invariant
  binds the two: **status='done' ⇔ completed_at IS NOT NULL**. Completing sets
  `status='done'`, reopening returns the task to `'backlog'`, and moving in or
  out of Done through the board completes/reopens it. `db.backfill_task_status`
  is that rule as a repair, run once by the v18 migration.
- **Writes.** `POST /tasks/{id}/status {status, return_to}` → validated move;
  `X-Partial:1` ⇒ JSON `{ok, task_id, status, completed}` (Mode B), else 303 PRG.
  It journals `task_status_changed` for the move plus the existing
  `task_completed` / `task_reopened` when the move crosses Done.
- **Board reads.** `services.tasks.board()` returns the three columns: Backlog
  and Doing hold *every* open task (nothing may be invisible on the primary
  view), Done is the newest `DONE_LIMIT` completions — `/completed` remains the
  unbounded list.
- **Interaction.** Drag between columns lives in `app/static/board.js`, its own
  file next to the calendar's `dnd.js` (shared shape, nothing else). Every drag
  has a plain POST form behind it — the ←/→ buttons on each card — so the board
  is fully usable with JavaScript off; quick-add stays on top and files into
  Backlog.
- **Rail link.** The rail link is the Jinja global `tasks_home`
  (`app/templating.py`). The service restarts on every deploy (README, "Run as
  a background service"), so templates read the global plainly.

## 31. Habit Tab (TickTick parity) — 2026-06-06

The user sent TickTick's **Habit tab** ("У нас такой вкладки нет" — "we don't have
a tab like that") — a habit list
with an **inline detail pane** (stat cards + monthly calendar + log), a per-habit
**⋯ menu** (Edit / Checked-in Style / Archive / Delete), and a **Create Habit**
modal. T1 had repointed `/habits` to a daily-note rail; this section brings the tab
itself up to parity inside the §30.2 shell, reusing the habit layer (§16.2/§16.6)
rather than adding a parallel one.

### 31.1 Data model (schema v3, `db.py` `_SCHEMA_V3`)

`routine_items` gains the Create-Habit fields (additive, `PRAGMA user_version` now
**3**, migration is idempotent — re-checks `PRAGMA table_info` before each
`ADD COLUMN`):

- `emoji TEXT` — shown in the avatar in place of the generated letter (`_item_row`,
  `_habit_listrow`, `_habit_detail` all do `{{ item.emoji or a.emoji or a.letter }}`).
- `frequency TEXT='daily'` ∈ `daily|weekdays|weekly`, `goal TEXT='achieve_all'` ∈
  `achieve_all|custom`, `goal_days TEXT='forever'` ∈ `forever|21|30|66|100`,
  `start_date TEXT` (defaults to today on create), `reminder TEXT` (HH:MM),
  `constant_reminder INTEGER=0`.

Since #18 only `emoji` and `start_date` are written: `items._clean_habit_fields`
trims them (`emoji[:8]` so ZWJ sequences like 🧘‍♂️ survive), `create_item`/`update_item`
take just those two as keyword args (`update_item` uses an `_UNSET` sentinel so a
partial edit only touches supplied columns), and both append the existing
`routine_item_created`/`updated` events with `emoji` and `start_date` in the payload.
The other four columns keep their schema defaults; the service neither accepts nor
emits them (#220). **Reminders were stored for parity only** — firing them needs a
scheduler, which is out of scope (no background process; the app is a
request/response server).

### 31.2 Routes (`main.py`)

`/habits` now renders `habits.html` (the tab), **not** the day view:

- `GET /habits {sel?, month?, edit?, flash?}` → `_render_habits`: icon-rail `habit`,
  week strip, sections (`items.list_sections` = distinct `group_name` ordered by
  `MIN(sort_order)`) of `_habit_listrow`s, a collapsed daily-note fold, the Create
  modal, and — when `?sel=habit-{id}` — the inline pane via `_habit_selection_ctx`
  (`?...&edit=1` swaps the pane body for the edit form).
- `POST /habits` → create (title, section, emoji, start date).
- `POST /habits/{id}/edit` → `update_item`.
- `POST /habits/{id}/archive` → `items.deactivate_item` (**soft**, `active=0`; row +
  history kept, hidden from the tab — same recovery-not-shame rule as lists/§16.5).
- `POST /habits/{id}/delete` → `items.delete_item` (**hard**: deletes the row and its
  `checkins` in one `with conn:`, appends a `routine_item_deleted` audit event so the
  ledger still records it).
- The rich day-review view moved to `GET /history` (still `today.html`,
  `day-layout`); `GET /habit/{id}` stays the standalone full detail page.

All four POSTs carry the same-origin guard (docs/security-model.md) and a validated `return_to`
(`_safe_return`), 303-redirecting back to the tab/open pane; `ItemError` (e.g. empty
title) round-trips as a `?flash=` message.

### 31.3 Templates & pane

- `_habit_form.html` (shared by the create modal and the edit pane) — emoji + title,
  Frequency / Goal / Start Date / Goal Days / **Section** (text + `<datalist>` of
  existing sections) / Reminder / Constant Reminder, hidden `return_to`, Cancel +
  Save. `item` (row|None) drives prefill.
- `_habit_listrow.html` — a row whose name is a **pane link** (`?sel=habit-{id}`),
  not a `<details>`: colour icon · title · `🔥 N days streak`, with a **circular
  check-in ring** (`.hl-check`) on the right (TickTick's row affordance). The ring is
  the full_done toggle — `data-dot` for Mode B (app.js now selects the check via
  `[data-dot]`, so it drives both this ring and the day-view dot) and a hidden
  `dot-{id}` form for the Mode-A fallback. Ring colour/glyph track the four statuses.
- `_habit_detail.html` (rewritten) — in the pane it adds a close ×, the **⋯ menu**
  (`<details class="rowmenu">` → Edit / Open full page / Archive / Delete, Delete
  behind `confirm()`), and either the inline edit form or a **Today** check-in card.
  TickTick's habit check-in is binary; ours keeps the four-status entry (§16.4) — the
  card's choices + note post to `/checkins` with `return_to=pane_return` and are
  marked `data-native` (full Mode-A reload) to dodge cross-component DOM updates
  between the pane and the list row.
- The Create modal uses the no-JS `:target` pattern (`#new-habit`); the ⋯ menu uses
  `<details>` — both work with JS off.

**Visual parity pass (2026-06-06).** A redesign brought the tab visually in line with
TickTick. Layout was matched against a **live logged-in reference** (TickTick's own web
Habit tab, observed via Playwright) and re-created in our own CSS — TickTick's
assets/CSS/icons/logos/text are *not* copied, per the security rules; only the
structure/layout is reproduced:
- The four **stat cards** match TickTick's layout: a small **icon + label on top**
  (✅ **Monthly check-ins** `month_stats.kept` · ⚡ **Total Check-Ins** `total` ·
  🎯 **Monthly check-in rate** `month_stats.rate` · 🔥 **Current Streak**
  `current_streak`) and a **big value + unit below**, left-aligned — all from existing
  derived data, no backend change. (Best-streak is still computed but no longer a card.)
- The monthly calendar keeps the **day number always visible** with a **small status
  circle below it** (`.cal-mark`, coloured by the four statuses; today's number in the
  accent colour) — matching TickTick's cell rather than a fully-coloured cell.
- The habit log heading reads **"Habit Log on {month}"** (matches TickTick exactly).
- The far-left icon rail gained **text labels** (Tasks / Habit / Manage) so the Habit
  tab is discoverable — previously a bare 🔥 glyph was easy to miss. (TickTick's rail is
  icon-only; this is a deliberate, helpful deviation.)
- Confirmed faithful by the live reference: the **top week strip** *is* present in
  TickTick's Habit tab (so ours is parity, not a carry-over). The pane's **Today
  four-status control** and the **daily-note fold** remain our own additions (TickTick's
  check-in is binary and has neither). **Theme:** the reference account is light; we keep
  our **dark** theme by the user's explicit choice (2026-06-06) — intentional, not a gap.

Mapping to TickTick vocabulary: a habit **is** a `routine_item`; **Section** =
`group_name`; **Archive** = soft `deactivate`; **Delete** = hard `delete_item`.
"Checked-in Style" from the ⋯ screenshot is intentionally omitted (it only swaps the
check glyph; our four-status glyphs are fixed). The top **week strip** matches TickTick
and additionally serves as our only entry point to `/history` (the all-habits
day-review). The collapsed **daily-note** fold at the bottom is our one extra (no
TickTick equivalent on this tab).

### 31.4 Verification

`verify.py` covers the tab end-to-end (now **112/112**): pane renders
(`pane-today` / four-status choices / `cal-grid` / TickTick stat-card labels / ⋯ menu /
edit form); full page shows the four stat-card labels + "Habit Log on" heading; rows
carry the streak + the `.hl-check` ring (`data-dot`); create persists the form's
fields (emoji 🧘 / start date) + appends the event; empty title → flash; edit leaves
the legacy columns alone; pane check-in 303s back to `?sel=habit-{id}`
and the pane reflects the new status; archive (`active=0`, hidden but kept); delete
(row + check-ins gone, `routine_item_deleted` event kept); cross-origin `POST /habits`
→ 403; `/history` still serves the day-layout.

---

## 34. Focus timer (schema v19, #75) — 2026-08-08

Owner decision 2026-07-24, built 2026-08-08: the **Pomodoro tab is retired**.
The 25-minute cycle was TickTick's ritual, not this product's need — what the
day actually wants is a timer whose length the user chooses, attached to the
thing being worked on, startable from wherever he already is.

- **No Focus destination.** `GET /focus` is gone (404, not redirected); so are
  its rail entry, its mobile-nav slot and its palette *view*. The palette gained
  a `Focus timer` **action**, and the `g f` chord opens the drawer instead of
  navigating. Through the restart window `app/templates/focus.html` stayed as
  an unrouted placeholder (a pre-#75 worker still held the old handler) and
  `POST /focus/session` kept answering an old page's completed Pomodoro; both
  were removed on 2026-08-29, once no pre-#75 page could still be open.
- **A drawer on every surface.** `app/templates/_timer_drawer.html`, included
  once by `base.html`, docked bottom-right; open/minimized state persists in
  `al-timer-open` / `al-timer-min`, the same shape as the terminal drawer's
  `al-term-min`. The astrolabe ring from the old page is kept as the progress
  indicator. Minimized still shows the clock — a running timer is never
  invisible — and the rail/nav toggle carries a pip while one runs.
- **The server owns the clock (#20).** A start writes a `focus_runs` row with
  `started_at`; the duration is computed on finish from that timestamp and is
  never sent by the browser. `focus-timer.js` only interpolates between syncs
  and re-syncs on `visibilitychange`, so reloads, navigation and a sleeping
  laptop cost accuracy at worst, never the session. Pausing stamps `paused_at`
  and resuming folds the interval into `paused_seconds`, keeping elapsed time a
  pure function of stored timestamps.
- **Model.** `focus_sessions.mode` becomes `CHECK(mode IN ('countdown','open'))`
  with the chosen length in `target_seconds`; the v19 migration converts the old
  rows (`pomo` → a 1500-second countdown, `stopwatch` → open). Historical
  `focus_session_recorded` payloads keep their original words — the ledger is
  never rewritten (sec13.3) — and the restore script does not replay this table,
  so an old export stays restorable.
- **One target, at most.** `lesson_id` (v8) is joined by `habit_id` and
  `task_id`; the write refuses two at once, because a span of attention spent on
  one thing must not be double-counted in two per-target totals. Starting a
  timer at a target that is not live — deleted, archived, already completed —
  is **refused** (422), because the drawer's picker can go stale under an open
  drawer and the user is still standing in front of it; the drawer reloads its
  options on that refusal. Elsewhere — the compatibility write, an id that no
  longer resolves at replay — a stale or junk id stores as NULL rather than
  dangling. A target *retired* while the timer runs — a habit archived, a task
  completed — keeps its attribution: liveness is a question about starting, not
  about finishing. Hard deletion is the exception and detaches, exactly as it
  does to the history that target already earned (`items.delete_item` NULLs
  `focus_runs.habit_id` alongside `focus_sessions.habit_id`): a deleted row
  cannot be pointed at, and the time is kept unattached rather than lost.
- **Idempotency.** `client_token` is unique on both tables: a retried start
  reuses the run, a retried finish returns the session the first call recorded.
  A countdown is capped at its chosen length — a tab left open into minute 30 of
  a 25-minute timer records 25 minutes of intent, not 30 of credit.
- **Writes.** `POST /focus/timer/{start,pause,finish,discard}` and
  `GET /focus/timer[/targets]`, all JSON. This surface has **no Mode A twin**
  (sec16.4): a timer without JavaScript cannot tick, so there is no no-JS
  behaviour to preserve. The same-origin write guard covers them like any other
  POST.
- **Where the numbers live.** Per-target stats sit with the target — focused
  time on the habit page and the Learn record line — and the 14-day bar chart
  plus the focus-day streak moved to **Retro**. The global "Today's/Total Pomo"
  dashboard is gone: a count of cycles measured the ritual, duration measures the
  thing itself.
