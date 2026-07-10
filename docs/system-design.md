# Activity Ledger — System Design Document

Status: Draft v0.1  
Primary target: Linux + Samsung browser  
Storage model: local-first SQLite  
Product type: personal activity/routine/path tracker  
UX reference: TickTick-like execution interface, not TickTick clone

---

## 1. Summary

Activity Ledger is a small personal tracker for daily routine, activity check-ins, simple history, and future integration with personal systems.

The app should initially replace the overloaded/limited TickTick usage for personal tracking, while preserving the main thing that makes TickTick useful: a fast operational interface.

The product is not a task manager, not a full habit tracker clone, and not a personal operating system. The first version is a small daily execution surface backed by our own data.

Core idea:

```text
Open Today → mark routine/status → optionally add note → save → leave.
````

The app should work from:

```text
Linux browser
Samsung phone browser
```

Initial deployment:

```text
Linux machine = local server
Samsung = browser client over local Wi-Fi
SQLite = source of truth
JSONL/Markdown = export layer
Git = backup/history later
VPS = later only if always-on access is needed
```

---

## 2. Product Goal

Build a personal Activity Ledger with a TickTick-like execution experience, but with our own data model:

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
Samsung phone
wants low-friction daily tracking
wants future machine-readable history
does not want to depend on TickTick long-term
```

Primary environment:

```text
home/local Wi-Fi first
later possible VPS
```

---

## 7. UX Reference Strategy

TickTick is used as a UX reference only.

The agent should inspect TickTick’s web UI through Playwright and produce a reference report.

This research is an OPTIONAL spike, not an MVP dependency (sec22, sec23); it is gated by a Terms-of-Service preflight and a stop rule (sec28).

Playwright is suitable for this because it supports page screenshots, full-page screenshots, and screenshot files through `page.screenshot`; it also supports reusing authenticated browser state via storage state, which helps avoid logging in repeatedly during research. ([Playwright][1])

### 7.1 UX Research Goal

The agent should answer:

```text
What makes TickTick fast for daily execution?
How dense is the Today view?
How are items grouped?
How are completed items displayed?
How many taps does a check-in require?
What is visible without opening details?
What is hidden behind secondary screens?
How does mobile navigation work?
What can we copy conceptually?
What should we avoid?
```

### 7.2 Allowed Research

The agent may:

```text
take screenshots
inspect layout structure
record interaction notes
compare mobile/desktop density
use accessibility tree / locators
write UX primitives
```

### 7.3 Forbidden Research / Actions

The agent must not:

```text
copy TickTick CSS
copy TickTick icons
copy TickTick logos
copy TickTick proprietary text
commit credentials
commit cookies
commit Playwright auth state
scrape private user data
bypass paid features
depend on TickTick DOM structure
build a patched TickTick client
```

---

## 8. Playwright UX Research Task

### 8.1 Target Viewports

The agent should inspect at minimum:

```text
Mobile viewport:
width: 390
height: 844

Desktop viewport:
width: 1366
height: 768
```

Primary design target:

```text
Samsung browser / mobile web
```

Secondary target:

```text
Linux desktop browser
```

### 8.2 Flows to Inspect

The agent should inspect:

```text
1. Today view
2. Habits/routines view
3. Add task/item flow
4. Add habit/routine flow
5. Completion/check-in flow
6. History/review/calendar-like flow if available
7. Mobile navigation
8. Desktop navigation
```

### 8.3 Deliverables

The agent should create:

```text
docs/reference/ticktick-ux-report.md
docs/reference/ux-primitives.md
docs/reference/screenshots/
  ticktick-mobile-today.png
  ticktick-mobile-habits.png
  ticktick-mobile-add-item.png
  ticktick-desktop-today.png
  ticktick-desktop-habits.png
```

### 8.4 UX Primitives to Extract

The report should extract primitives, not visuals:

```text
daily list anatomy
section grouping
item row structure
completion affordance
status visualization
date navigation
bottom navigation
floating action button behavior
detail drawer behavior
note/detail access
empty states
error states
mobile tap target density
desktop density
```

---

## 9. Playwright Research Skeleton

```ts
// tests/reference/ticktick-reference.spec.ts
import { test } from "@playwright/test";

test.describe("TickTick UX reference capture", () => {
  test.use({
    storageState: "playwright/.auth/ticktick.json",
  });

  test("capture mobile reference screens", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });

    await page.goto("https://ticktick.com/webapp/", {
      waitUntil: "networkidle",
    });

    await page.screenshot({
      path: "docs/reference/screenshots/ticktick-mobile-home.png",
      fullPage: true,
    });

    // Agent should navigate manually or with stable locators
    // and capture Today / Habits / Add Item flows.
  });

  test("capture desktop reference screens", async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 });

    await page.goto("https://ticktick.com/webapp/", {
      waitUntil: "networkidle",
    });

    await page.screenshot({
      path: "docs/reference/screenshots/ticktick-desktop-home.png",
      fullPage: true,
    });
  });
});
```

Required `.gitignore` entries:

```gitignore
.agents/
.claude/
.codex/
.playwright-mcp/
playwright/.auth/
test-results/
playwright-report/
docs/reference/screenshots/
ticktick-*.png
tt-*.png
my-*.png
# Source-of-truth DB, exports, and backups are private by default:
data/
*.sqlite
*.sqlite3
*.sqlite-wal
*.sqlite-shm
*.db
*.jsonl
.env
.env.*
```

Screenshots may be stored locally for design reference, but should not become the source of copied UI assets.

---

## 10. System Architecture

### 10.1 MVP Architecture

```text
Samsung Browser
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

Run locally on Linux. Two modes:

```bash
# Desktop-only (safe default — not reachable from other devices):
uvicorn app.main:app --host 127.0.0.1 --port 8000

# Trusted home Wi-Fi (lets Samsung connect — read sec20 first):
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Bind `0.0.0.0` ONLY on a network you trust: the app has no auth (sec20), so on
`0.0.0.0` anyone on the LAN can read and write it. The app should print a
warning on startup when it binds `0.0.0.0`. Committed scripts/README default to
`127.0.0.1`.

Open on Linux:

```text
http://localhost:8000
```

Open on Samsung in the same Wi-Fi network:

```text
http://<linux-lan-ip>:8000
```

Find Linux LAN IP:

```bash
hostname -I
```

### 10.3 Later Deployment

Only after the app is actually useful:

```text
small VPS
Caddy or Nginx
HTTPS
basic auth or single-user password
daily backup
```

VPS is not required for MVP.

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

## 12. Project Structure

```text
activity-ledger/
  app/
    main.py
    db.py
    models.py
    services/
      checkins.py
      export.py
    templates/
      base.html
      today.html
      history.html
      items.html
      export.html
    static/
      style.css
  data/
    activity.sqlite
    exports/
  docs/
    system-design.md
    reference/
      ticktick-ux-report.md
      ux-primitives.md
      screenshots/
  tests/
    reference/
      ticktick-reference.spec.ts
  README.md
  .gitignore
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
`focus_sessions` (v4, sec15.4), and `calendar_events` (v5, sec32 §3 — the row IS
the recurring series; occurrences are expanded on read, never materialized).

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
- PRAGMA journal_mode = WAL;  # phone can read while desktop writes
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
- All POSTs are same-origin guarded (sec20) and use 303 PRG redirects.
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
so a row shows more than binary done/not-done (sec7.3 pattern-level only):

```text
- Anatomy: [emoji/letter avatar] [name + streak line] [7-day status dots] [⋮].
  The streak line is "🔥 <current> days · best <best>" (services.stats); the flame
  is muted when the current streak is 0.
- The 7 dots align to the week strip's 7 days. Each is coloured + glyphed by that
  day's status (✓ full / ◐ light / – skip / ✕ fail); empty past days and future
  days render faint. They give at-a-glance week history without opening anything.
- ONE primary affordance per row (ux-primitives P3): the ACTIVE day's dot (today on
  Today; the selected day on History) is a larger button — tap = full_done (1 tap,
  re-tap clears). The other six dots are read-only history (the week strip handles
  navigation). Active dot ~34px; comfortable on touch and the obvious target.
- light_done / skipped / failed, the per-item note, AND a "Stats & calendar →" link
  to the habit detail (sec16.6) live in a panel revealed by tapping the card (Mode A:
  the card IS a native <details>; Mode B enhances it). Keeps light_done one gesture
  away (P7) without crowding the row.
- Status reads as colour AND glyph for grayscale / colour-blind users (sec16.5,
  sec22 criterion 6).
- The header "<kept>/<total> kept" reflects full_done + light_done; the count AND
  the row's streak are recomputed live in Mode B from the check-in's JSON response
  (the server returns current/best streak so the number is correct beyond 7 days).
- Sections are collapsible group headers with a count (P2). A Sun–Sat week strip
  at the top moves between days (P8; today highlighted, future days disabled).
- Dark theme by default; our own styling/assets only (sec7.3).
- Empty state (no active items): link to Manage Items, not a blank page.
- Validated against sec22 criterion 4 (5-10 items in <60s) at 390px.
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
  This is the primary path for sec22 criterion 4 (<60s); Mode A stays correct but
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
PATTERN only (sec7.3); the data is ours and, thanks to the four-status model, the
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
the Samsung client can save it; `GET /export` renders a one-button page. (The
stop-loss fallback in sec24 replaces this with a script and no page.)

### 18.2 Future Markdown Export

Later, optionally generate:

```text
data/exports/days/2026-06-05.md
data/exports/weeks/2026-W23.md
```

Example daily markdown:

```md
# 2026-06-05

## P0 Core Routine

- Sleep: light_done
- Food: full_done
- Sport / show up: skipped
- Evening walk: light_done
- Daily output: full_done

## Daily Note

Short note here.
```

Markdown export is not source of truth.

SQLite remains source of truth.

---

## 19. Backup Strategy

MVP:

```text
manual SQLite backup via `sqlite3 .backup` or `VACUUM INTO` (consistent under WAL; not a raw cp mid-write, sec20)
manual JSONL export
```

Later:

```text
daily SQLite backup
daily JSONL export
private Git commit of encrypted/sanitized exports
optional encrypted remote backup
```

Do not store the main SQLite database directly in Git as the primary sync model.
Do not store raw JSONL/Markdown exports in the public repo; they can contain
private task titles, habit names, notes, timestamps, and behavioral history.

Public Git is for:

```text
source code
public docs
invented demo fixtures
sanitized archive material only when explicitly reviewed
human-readable traces
```

SQLite is for:

```text
working memory
querying
app state
```

---

## 20. Security Model

MVP local-only assumptions:

```text
runs on trusted Linux machine
available only on local Wi-Fi
no public internet exposure
no auth in v0
```

Security rules:

```text
do not expose local server to the internet
do not run on 0.0.0.0 on untrusted networks (sec10.2 defaults to 127.0.0.1)
do not store secrets in repo
do not commit Playwright auth state
do not commit cookies
do not commit TickTick screenshots if they contain private data
do not commit the SQLite DB or raw exports (sec9)
verify same-origin / Origin header on state-changing POSTs (lightweight CSRF guard; the app has no auth)
render user text (titles/notes) via Jinja autoescape only — never |safe, never disable autoescape
back up the SQLite file with `sqlite3 .backup` or `VACUUM INTO` (consistent under WAL), not a raw cp mid-write
(optional) set owner-only permissions on data/ if the Linux host is shared
```

If deployed to VPS later, add:

```text
HTTPS
single-user password
backup encryption
server firewall
basic rate limiting
```

---

## 21. Testing Strategy

### 21.1 Manual MVP Test

Test from Linux:

```text
open /today
mark all P0 items
write daily note
open /history
verify saved data
run export
verify JSONL file
```

Test from Samsung:

```text
open http://<linux-lan-ip>:8000
mark statuses
verify mobile layout
verify tap targets
verify no horizontal scrolling
```

### 21.2 Automated Tests

Backend tests:

```text
create routine item
upsert checkin
update status
save daily note
export JSONL
```

UI smoke tests:

```text
Today page loads
History page loads
Items page loads
Export endpoint works
```

Playwright tests for our app later:

```text
mobile Today page screenshot
mark full_done
mark light_done
save daily note
navigate to History
```

---

## 22. MVP Acceptance Criteria

MVP is accepted when:

```text
1. App runs locally on Linux.
2. Samsung can open it over local Wi-Fi.
3. Today page is usable at 390px width.
4. User can mark 5–10 routine items in under 60 seconds.
5. Each item supports:
   - full_done
   - light_done
   - skipped
   - failed
6. Selected status is visually clear.
7. Daily note is saved.
8. History by date works.
9. Routine items can be added/deactivated.
10. Data persists in SQLite.
11. JSONL export works.
12. No external service is required.
```

Note on criterion 4: the <60s speed target is met by the Mode B
progressive-enhancement path (framework-free TypeScript/JS, sec16.4). Mode A
(no-JS, POST-redirect-GET) stays fully functional and correct — it is the
fallback, just with a full-page reload per save. Both paths share one server
contract, so acceptance is judged on the Mode B path.

---

## 23. MVP Milestones

### Milestone 0 — Skeleton

```text
FastAPI app starts
SQLite file created
base template works
```

### Milestone 1 — Today Page

```text
seed routine items
show grouped items
status buttons work
checkins saved
```

### Milestone 2 — Daily Note + History

```text
daily note saved
history date view works
previous/next date navigation
```

### Milestone 3 — Manage Items

```text
add item
edit item
deactivate item
sort/group display
```

### Milestone 4 — Export

```text
export JSONL = events stream + calendar series snapshots (sec18.1; sec32 §8)
events table serialized one-per-line, ORDER BY id
check-ins ride along AS routine_checkin_* event payloads (not a separate record)
daily notes ride along AS daily_note_updated event payloads (not a separate record)
calendar_events ride along AS calendar_event_series snapshot records
```

### Milestone 5 — UX Pass

```text
mobile layout improved
tap targets usable (>=44px, sec16.2)
no copied assets/design
```

### Optional Spike (NOT MVP-blocking) — TickTick UX reference

```text
- Run BEFORE Milestone 1 if done at all; MVP acceptance (sec22) does NOT depend on it.
- Requires a Terms-of-Service preflight and a hard stop rule (sec28) before any
  authenticated automation; prefer logged-out/marketing pages or manual observation;
  use a disposable account only if login is truly needed.
- Screenshots and Playwright auth state are gitignored (sec9). The markdown
  deliverables (ux-report, primitives) carry no private or proprietary text and
  MAY be committed; keep raw screenshots local-only.
```

---

## 24. Stop-Loss Rules

If implementation starts expanding, cut scope.

Invoke the stop-loss (reduce to the fallback below) if, after Milestone 1, the
Today page fails sec22 criterion 3 (usable at 390px) or criterion 4 (5-10 items
in under 60s) in real use, OR if Milestones 0-1 are not working after a fixed
time-box (e.g. a weekend). Reduce to:

```text
single app.py
one SQLite file
one today.html
hardcoded seed items
no manage screen
no export UI, only script
```

This fallback is a PRE-MVP SURVIVAL build, NOT an MVP-acceptance path: with
hardcoded items and no manage screen it does not satisfy sec22 (notably
criterion 9 — routine items can be added/deactivated). It keeps a working Today
page while you regroup; full sec22 acceptance still requires restoring the
manage screen.

Forbidden scope creep:

```text
"Let's add AI"
"Let's add S3"
"Let's deploy VPS first"
"Let's make it native Android"
"Let's model Story now"
"Let's build full path graph now"
"Let's add charts"
```

The app exists to support routine, not replace routine.

---

## 25. Future Roadmap

Only after MVP is used for real.

### v0.2

```text
weekly review
Markdown export
simple stats
better mobile UI
PWA manifest
```

### v0.3

```text
basic auth
VPS deployment
HTTPS
daily backups
```

### v0.4

```text
paths
steps
artifacts
Story progress references
learning traces
```

### v0.5+

```text
agent-readable API
Atlas integration
Story translation/workflow support
worldbuilding references
calendar/telegram integrations
```

---

## 26. Relationship to Other Systems

### TickTick

```text
UX reference only
temporary operational benchmark
not source of truth
```

### Activity Ledger

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

Future knowledge/path layer.

Not part of MVP.

---

## 27. Coding Agent Prompt

```text
Build Activity Ledger v0 according to docs/system-design.md.

Goal:
A local-first personal activity tracker with a TickTick-like execution interface, but our own data model and SQLite source of truth.

Stack:
- Python
- FastAPI
- SQLite
- Jinja2
- vanilla HTML/CSS
- vanilla TypeScript/JS (framework-free, progressive enhancement; sec16.4 Mode B)
- no React
- no auth
- no Docker
- no VPS
- no external services

Must build:
1. Today page
2. Grouped routine items
3. Four statuses per item:
   - full_done
   - light_done
   - skipped
   - failed
4. Optional note per check-in
5. Daily note
6. History by date
7. Manage routine items
8. JSONL export
9. Mobile-first responsive UI

Important:
- Do not clone TickTick visually.
- Do not copy TickTick assets, CSS, icons, logos, or text.
- Use TickTick only as UX reference.
- Keep implementation simple.
- SQLite is source of truth.
- Append events for meaningful changes.
- README must explain how to run locally and open from Samsung over Wi-Fi.

Do not add:
- AI
- S3
- VPS
- auth
- React
- native app
- notifications
- calendar sync
- complex analytics
- Story/worldbuilding model
```

---

## 28. Reference UX Agent Prompt

```text
Use Playwright to inspect TickTick web UI as a UX reference for Activity Ledger.

Goal:
Extract interaction patterns that make TickTick fast for daily execution.

Do not:
- clone TickTick
- copy assets
- copy CSS
- copy logos/icons
- bypass paid features
- scrape private data
- commit credentials/cookies/session files
- proceed if TickTick Terms or bot controls are unclear (STOP; use public/marketing pages or manual observation instead)
- treat this spike as required for MVP (it is optional — sec23)

Use:
- a Terms-of-Service preflight before any authenticated automation
- a disposable TickTick account only if login is truly needed
- Playwright storageState under playwright/.auth/
- .gitignore for playwright/.auth/ and screenshots; the markdown report/primitives carry no private text and may be committed

Inspect:
1. Today view
2. Habits/routines view
3. Add item/habit flow
4. Completion/check-in flow
5. Mobile navigation
6. Desktop navigation
7. History/review-like views if available

Viewports:
- mobile: 390x844
- desktop: 1366x768

Deliver:
- docs/reference/ticktick-ux-report.md
- docs/reference/ux-primitives.md
- screenshots under docs/reference/screenshots/

The report should answer:
- What makes the Today screen fast?
- How many taps does a check-in require?
- What information is visible immediately?
- What is hidden behind secondary interactions?
- How does the UI avoid feeling overloaded?
- Which patterns should Activity Ledger adopt conceptually?
- Which patterns should Activity Ledger avoid?
```

---

## 29. Final Design Decision

Build:

```text
TickTick-like execution UI
+
Activity Ledger data model
+
SQLite local memory
+
JSONL export
```

Do not build:

```text
TickTick clone
full task manager
personal OS
cloud sync platform
agent system
Story system
```

The first successful version is simple:

```text
open Today
mark full/light/skip/fail
write note
save
review later
export data
```

```
```

[1]: https://playwright.dev/docs/screenshots?utm_source=chatgpt.com "Screenshots"

## 30. Task Manager Layer (TickTick clone) — scope change 2026-06-05

§29 originally said *do not build a TickTick clone / full task manager*. The user
revisited that after seeing the full TickTick Today screen and explicitly chose a
**full TickTick clone** (Tasks + Habits + Lists + Tags + Filters +
Countdown + Inbox + the 3-pane app shell). This section supersedes §29's
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

- `POST /tasks {title, list_id?, due_date?, kind?, return_to}` → create (defaults to Inbox).
- `POST /tasks/{id}/complete {return_to}` → reversible toggle; `X-Partial:1` ⇒ JSON
  `{ok, task_id, completed}` (Mode B), else 303 PRG to `return_to`.
- `POST /tasks/{id}/update {title, note, due_date, priority, list_id, return_to}` → patch.
- All carry the same-origin guard (§20) and a `return_to` (validated same-origin path)
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

Validated/clamped in `items._clean_habit_fields` (whitelists each enum, `emoji[:8]`
so ZWJ sequences like 🧘‍♂️ survive). **Reminders are stored for parity only** — firing
them needs a scheduler, which is out of scope (no background process; the app is a
request/response server). `create_item`/`update_item` take the fields as keyword args
(`update_item` uses an `_UNSET` sentinel so a partial edit only touches supplied
columns); both append the existing `routine_item_created`/`updated` events with the
new fields in the payload.

### 31.2 Routes (`main.py`)

`/habits` now renders `habits.html` (the tab), **not** the day view:

- `GET /habits {sel?, month?, edit?, flash?}` → `_render_habits`: icon-rail `habit`,
  week strip, sections (`items.list_sections` = distinct `group_name` ordered by
  `MIN(sort_order)`) of `_habit_listrow`s, a collapsed daily-note fold, the Create
  modal, and — when `?sel=habit-{id}` — the inline pane via `_habit_selection_ctx`
  (`?...&edit=1` swaps the pane body for the edit form).
- `POST /habits` → create (all §31.1 fields; `constant_reminder=bool(...)`).
- `POST /habits/{id}/edit` → `update_item`.
- `POST /habits/{id}/archive` → `items.deactivate_item` (**soft**, `active=0`; row +
  history kept, hidden from the tab — same recovery-not-shame rule as lists/§16.5).
- `POST /habits/{id}/delete` → `items.delete_item` (**hard**: deletes the row and its
  `checkins` in one `with conn:`, appends a `routine_item_deleted` audit event so the
  ledger still records it).
- The rich day-review view moved to `GET /history` (still `today.html`,
  `day-layout`); `GET /habit/{id}` stays the standalone full detail page.

All four POSTs carry the same-origin guard (§20) and a validated `return_to`
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
carry the streak + the `.hl-check` ring (`data-dot`); create persists all fields
(emoji 🧘 / weekdays / 66 / 07:30 / constant=1) + appends the event; empty title →
flash; edit (partial, reminder cleared); pane check-in 303s back to `?sel=habit-{id}`
and the pane reflects the new status; archive (`active=0`, hidden but kept); delete
(row + check-ins gone, `routine_item_deleted` event kept); cross-origin `POST /habits`
→ 403; `/history` still serves the day-layout.
