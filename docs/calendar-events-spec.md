# Spec — Calendar Events (timed + recurring)

> Spec for adding **time-of-day, recurring calendar events** to the ephemeris
> TickTick clone. Written to drop into `docs/system-design.md` as **sec32**
> (scope change, same style as sec30 Task Manager / sec31 Habit Tab). Grounded in
> the current code: schema v4 (`app/db.py`), services (`app/services/tasks.py`,
> `items.py`), calendar route + `_month_grid` (`app/main.py`), `calendar.html`.
>
> Author: planning pass. Implementer: a coding model. **Section 13 lists the few
> genuinely-open choices** — confirm those before building.

---

## 1. Goal & scope

Today the Calendar (`/calendar`, `_month_grid` in `app/main.py:575`) is a month
grid that renders **tasks by `due_date` only** — a date, no time-of-day, no
recurrence (`tasks.due_between`, `app/services/tasks.py:229`). We add a new
first-class entity, **calendar events**, that have:

- a **time slot** (`start_time`–`end_time`, wall-clock local), or an all-day flag;
- a **recurrence rule** (single / daily / weekly-by-weekday), expanded on read —
  **no materialized per-occurrence rows**;
- soft-delete of a series + **skip of a single occurrence** (EXDATE);
- a new **Week view** with time rows so a 09:10–09:55 block is actually visible,
  plus event chips merged into the existing Month grid.

### In scope
- Schema v5 + migration; `calendar_events` table.
- Service `app/services/calendar_events.py` (CRUD + occurrence expansion).
- Routes: extend `/calendar` (month), add `/calendar/week`, event CRUD POSTs.
- Templates/CSS: month chips with time, new timed week view, event form.
- Audit events (sec14.1) + JSONL export hook (sec18).
- Unit tests for the expansion engine + route smoke tests (sec21.2).

### Non-goals (keep MVP honest — sec5)
- **No reminders firing.** Same stance as habit reminders (sec31): a time can be
  stored, but nothing schedules/pushes — there is no background scheduler.
- No external calendar sync (Google/CalDAV/ICS import-export), no attendees/invites.
- No timezone *conversion* — single ledger zone (`app_tz`, sec13.3). DST note in §9.
- Minute precision only; no seconds.
- **Per-occurrence override** (move/rename just one instance, keeping the series)
  is a **stretch goal** (§13.3), not v1. v1 supports skip-one + edit-whole-series.

---

## 2. Worked example (the canonical demo fixture)

Use this invented demo schedule as the acceptance fixture (§11). It is authored
by the synthetic persona Vera Example ([selfos docs/persona.md](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/persona.md))
and is not copied from the user's calendar, tasks, habits, or notes.

- **Orbit Drill** — 09:10–09:55, **weekly Mon/Wed/Fri**, starts **2027-04-07 (Wed)**, open-ended.
- **Signal Lab**  — 09:10–09:55, **weekly Tue/Thu**, starts **2027-04-07 (Wed)**, open-ended.

Expected expansion (ledger zone), proving the `start_date` lower bound:

- Week Sun **2027-04-04 … Sat 2027-04-10**: only **Wed 04-07 Orbit Drill**,
  **Thu 04-08 Signal Lab**, **Fri 04-09 Orbit Drill**. Mon 04-05 / Tue 04-06
  are **before** `start_date` → nothing.
- Week **2027-04-11 … 04-17**: Mon Orbit Drill, Tue Signal Lab, Wed Orbit Drill,
  Thu Signal Lab, Fri Orbit Drill.

> The two series are entered via the UI once built; do **not** auto-seed them.

---

## 3. Data model — schema v5

Add to `app/db.py` exactly like the existing migration ladder
(`_SCHEMA_V2/3/4`, `_MIGRATIONS`, `SCHEMA_VERSION`). Bump `SCHEMA_VERSION = 5`.

```sql
-- v5 — calendar_events: timed, recurring events for the Calendar week/month views
-- (sec32). The ROW IS THE SERIES; concrete occurrences are expanded on read from
-- the recurrence rule, never materialized. Soft-archived, never hard-deleted, so a
-- series stays joinable to its audit events (sec14.1 / recovery goal sec16.5).
CREATE TABLE IF NOT EXISTS calendar_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  title       TEXT NOT NULL CHECK(length(trim(title)) > 0),
  emoji       TEXT,
  list_id     INTEGER REFERENCES lists(id),      -- optional grouping/colour, like tasks
  note        TEXT,

  all_day     INTEGER NOT NULL DEFAULT 0 CHECK(all_day IN (0,1)),
  start_time  TEXT,            -- 'HH:MM' local; NULL iff all_day=1
  end_time    TEXT,            -- 'HH:MM' local; NULL = point-in-time event

  freq        TEXT NOT NULL DEFAULT 'once'
              CHECK(freq IN ('once','daily','weekly')),
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
```

Wiring in `app/db.py`:
```python
SCHEMA_VERSION = 5
_SCHEMA_V5 = """<the SQL above>"""
def _migrate_to_5(conn): conn.executescript(_SCHEMA_V5)
_MIGRATIONS = [ (1,_migrate_to_1), (2,_migrate_to_2), (3,_migrate_to_3),
                (4,_migrate_to_4), (5,_migrate_to_5) ]
```

**`byweekday` convention:** a 7-char string of `0/1`, index = Python
`date.weekday()` (**Mon=0 … Sun=6**). Orbit Drill MWF = `'1010100'`,
Signal Lab TT = `'0101000'`. NULL/absent only allowed when `freq != 'weekly'`.

Also update **sec13.1 Core Tables** and **sec30.1** in `system-design.md` to list
this table.

---

## 4. Recurrence semantics — the expansion engine (nail this first)

This is the heart of the feature; write it test-first (§10). A single pure
predicate decides whether a series has an occurrence on a given date `d`:

```
occurs_on(ev, d):
  if d < ev.start_date:                      return False
  if ev.end_date and d > ev.end_date:        return False
  if d in ev.exdates:                         return False
  if ev.freq == 'once':                       return d == ev.start_date
  if ev.freq == 'daily':
      return days_between(ev.start_date, d) % ev.interval_n == 0
  if ev.freq == 'weekly':
      if ev.byweekday[d.weekday()] != '1':    return False
      # interval in *weeks*, anchored to the Monday of start_date's week
      wk = weeks_between(monday_of(ev.start_date), monday_of(d))
      return wk % ev.interval_n == 0
```

Public read API (mirror `tasks.due_between` shape — returns lightweight dicts the
templates consume, **not** ORM rows):

```python
def occurrences_between(conn, start: str, end: str) -> list[dict]:
    """Every concrete occurrence in [start, end] for all non-archived series,
    sorted by (date, all_day DESC, start_time, id). Each item:
        { 'event_id', 'date', 'all_day', 'start_time', 'end_time',
          'title', 'emoji', 'color', 'list_id', 'note' }"""

def occurrences_on(conn, day: str) -> list[dict]: ...
```

Implementation notes:
- Load candidate series once: `archived_at IS NULL AND start_date <= end AND
  (end_date IS NULL OR end_date >= start)`, then walk each date in the window and
  apply `occurs_on`. The calendar windows are ≤ 42 days (month) or 7 (week), so a
  day-by-day walk is fine — **do not** prematurely optimise.
- `exdates` parsed from JSON once per series.
- All date math via `datetime.date` + `timedelta`; reuse `is_valid_date` from `db`.

---

## 5. Service layer — `app/services/calendar_events.py`

New module, same conventions as `tasks.py` / `items.py`: module-level `_event`
audit helper, a `_clean` validator, `CalendarEventError(ValueError)`, every write
wrapped in `with conn:` and paired with its audit event in the **same transaction**
(sec14.1). Signatures:

```python
FREQS = ('once','daily','weekly')

def create_event(conn, title, *, start_date, freq='once', byweekday=None,
                 interval_n=1, all_day=False, start_time=None, end_time=None,
                 end_date=None, list_id=None, emoji=None, note=None,
                 color=None) -> int
def update_event(conn, event_id, **fields) -> None          # patch-style like update_task
def archive_event(conn, event_id) -> None                   # sets archived_at (soft)
def get_event(conn, event_id) -> sqlite3.Row | None
def list_events(conn, include_archived=False) -> list[sqlite3.Row]

def skip_occurrence(conn, event_id, date: str) -> None      # append date to exdates JSON
def unskip_occurrence(conn, event_id, date: str) -> None    # remove from exdates

def occurrences_between(conn, start, end) -> list[dict]      # §4
def occurrences_on(conn, day) -> list[dict]
```

`_clean` rules:
- title trim, non-empty, ≤ 500 (match tasks).
- `start_date` required + `is_valid_date`; `end_date` optional + valid + `>= start_date`.
- `freq` ∈ FREQS else `'once'`; `interval_n` int ≥ 1 else 1.
- if `freq=='weekly'`: `byweekday` must be 7 chars of `0/1` with ≥ one `1`, else error.
- `all_day` → force `start_time=end_time=None`. Else `start_time` required, format
  `^\d{2}:\d{2}$` and `00:00..23:59`; `end_time` optional, if present `>= start_time`.
- `list_id` if given must exist (`lists_svc.get_list`).

Audit event types: `calendar_event_created`, `calendar_event_updated`,
`calendar_event_archived`, `calendar_occurrence_skipped`,
`calendar_occurrence_unskipped`.

---

## 6. Routes — `app/main.py`

All POSTs: same-origin guarded + **303 PRG redirect** like every other write
(sec15.3 / sec20). New view switcher uses the existing `Month ⌄` pill placeholder
in `calendar.html`.

**Reads**
- `GET /calendar?month=YYYY-MM` *(extend)* — in `_month_grid`, after building
  `by_date` from `tasks.due_between`, also merge `calendar_events.occurrences_between`
  for the same 42-day window into each cell's `events`, tagging
  `kind='event'` and carrying `start_time/end_time/all_day`. Sort each cell:
  all-day & timed events first by `start_time`, then tasks. (Decide ordering in §13.)
- `GET /calendar/week?date=YYYY-MM-DD` *(new)* — a **Sunday-start week** (match the
  month grid's `firstweekday=6`) containing `date` (default today). Context:
  - 7 day columns (date, dow, is_today),
  - an **all-day row** (all_day events + tasks due that day),
  - **hour rows** for the timed grid; default visible band **06:00–23:00**, expand
    to fit any earlier/later occurrence in the window,
  - each timed occurrence positioned by `start_time`/`end_time` (default 30-min
    block when `end_time` is NULL).
  - **overlapping occurrences share width by column-packing — see §6.1.**
  - prev/next week URLs (±7 days), "Today" link.
- `GET /calendar/day?date=` *(optional / stretch)* — week view already covers it.

**Writes**
- `POST /calendar/events` — create (form fields map to `create_event`).
- `POST /calendar/events/{id}` — update series.
- `POST /calendar/events/{id}/archive` — soft-delete series.
- `POST /calendar/events/{id}/skip` (body: `date`) — skip one occurrence.
- `POST /calendar/events/{id}/unskip` (body: `date`).

Add an `events_count` / nav badge only if trivial; otherwise skip.

### 6.1 Overlap layout (week/day timed grid)

Two timed occurrences on the same day may overlap (even by a minute). They must
**share the day-column width side-by-side**, like Google Calendar / TickTick — never
stack on top of each other. This is a **pure render-layout concern**: the expansion
engine (§4), the data model, and the month grid are unaffected (the month grid
shows chips in document order, no time geometry). Implement as a small helper that
the week-view route (or a template filter) runs **per day** over that day's timed
occurrences:

```
layout_day(occs):                      # occs: timed occurrences for ONE day
  sort occs by (start_time, end_time)
  # 1. cluster: maximal runs of transitively-overlapping events
  clusters = []; cur = []; cur_end = None
  for e in occs:
      if cur and e.start < cur_end:    cur.append(e)
      else:                            (cur and clusters.append(cur)); cur = [e]
      cur_end = max(cur_end or e.end, e.end)
  clusters.append(cur) if cur
  # 2. greedy column assignment within each cluster
  for cluster in clusters:
      col_end = []                     # end time of last event per column
      for e in cluster:               # already start-sorted
          c = first index where col_end[c] <= e.start, else append new column
          e.col = c; col_end[c] = e.end
      ncols = len(col_end)
      for e in cluster: e.width = 1/ncols; e.left = e.col/ncols
  return occs                          # each now carries col / left / width
```

- `end` for layout = `end_time` or `start_time + 30min` (NULL end) — events still
  collide correctly.
- Geometry the template uses per block: `top = (start − band_start)·px_per_min`,
  `height = max(duration·px_per_min, MIN_BLOCK_PX)`, `left = e.left·100%`,
  `width = e.width·100%` of the day column. `MIN_BLOCK_PX` keeps a 15-min slot
  legible even if it then slightly overruns a neighbour — readability > pixel-exact.
- **Optional refinement (`expand-to-fill`, §13.7):** after assigning columns, let an
  event widen rightward across adjacent columns that have no event overlapping its
  span — fills whitespace exactly like Google Calendar. Defaults off for v1.
- **All-day** events never enter this grid — they live in the separate sticky
  all-day row and simply stack.

---

## 7. Templates & CSS

- **`calendar.html` (month)** — extend the `cm-event` loop: for `kind=='event'`
  prepend a time chip (`start_time`) and use a distinct class (e.g.
  `cm-event ev`). Keep task rendering unchanged.
- **`calendar_week.html` (new)** — CSS-grid timed week:
  - header row of 7 day columns;
  - sticky all-day row;
  - a `position:relative` grid of hour rows; event blocks absolutely positioned by
    `top = (minutes_from_band_start) px-per-min`, `height = duration`.
  - Match TickTick week look (see `docs/reference/screenshots/tt-calendar.png`).
- **Event form** — modal or right-pane, reuse task-form styles
  (`docs/reference/ux-primitives.md`): title, emoji, list, all-day toggle,
  start/end time, repeat (none/daily/weekly + weekday checkboxes when weekly),
  start date, end date, note. On a recurring occurrence, the edit affordance
  offers **"This event"** (→ skip + create override, stretch) vs **"All events"**
  (→ update series); v1 may ship only "All events" + a separate **Skip** action.
- **`style.css`** — `.cm-event.ev`, the week-grid classes, event block colours
  (reuse priority/countdown colour vocabulary already in the month grid).
- Respect the existing **tri-state theme** (light/dark/system) — use CSS vars, no
  hard-coded colours (consistent with R10).

---

## 8. Audit + export

- Every write appends its event to the `events` table (sec14.1) — already covered
  by the service `_event` helper.
- **Export (`app/services/export.py`, sec18):** include `calendar_events` series
  (the source of truth) in the JSONL export. Do **not** export expanded
  occurrences. Add a short note to sec18.1.

---

## 9. Timezone (sec13.3)

Times are **wall-clock in the ledger zone** (`app_tz()` or host local). No
cross-zone conversion, no UTC storage — the app is single-user, single-zone. One
caveat to document, not solve: across a **DST** transition a fixed `09:10` stays
09:10 wall-clock (correct for this use case). No DST-arithmetic needed because we
store wall-clock strings, not instants.

---

## 10. Testing (sec21.2 — `tests/`)

**`tests/test_calendar_events.py`** — the expansion engine is the priority:
- weekly MWF over a full month → exact expected date list;
- `interval_n=2` (every other week) anchored correctly;
- `start_date` lower bound (the §2 first-week case: Mon/Tue before start excluded);
- `end_date` upper bound inclusive;
- `exdates` removes exactly one occurrence; unskip restores it;
- `freq='once'` yields exactly its `start_date`;
- `freq='daily'` with interval;
- month-boundary window (occurrences spanning two months in the 42-day grid).
- **The §2 canonical demo fixture** → asserts the two listed weeks exactly.

**`tests/test_calendar_layout.py`** — the overlap column-packing (§6.1), pure data:
- two events overlapping by 1 min → 2 columns, each `width=0.5`, `left` 0 and 0.5;
- non-overlapping back-to-back (A ends 08:00, B starts 08:00) → 1 column, full width;
- transitive chain A∩B, B∩C (A∌C) → one cluster, 2 columns with a freed column reused;
- three mutually overlapping → `width=1/3` each;
- NULL `end_time` treated as 30-min for collision.

**`tests/test_calendar_routes.py`** (smoke, mirror existing route tests):
- create event → `GET /calendar` and `/calendar/week` render it;
- skip one occurrence → it disappears from that day only;
- archive series → gone from both views;
- invalid form (weekly without weekday, bad time) → rejected, no row.

Keep the suite green (the repo tracks a "N/N verified" bar).

---

## 11. Acceptance criteria

1. Migration v4→v5 runs idempotently on the live DB; existing data untouched.
2. Can create the two §2 series via the UI; both appear in Month (chips with time)
   and Week (positioned 09:10–09:55 blocks).
3. Expansion matches §2 **exactly** for the two named weeks (unit-tested).
4. Skipping a single Orbit Drill occurrence removes only that day; the series continues.
5. Archiving a series removes all its occurrences from both views; audit events
   exist in `events` for every write.
6. JSONL export contains the two demo series.
7. Light/dark/system themes all render the week grid correctly.
8. No regression in the existing month grid's task rendering.

---

## 12. Staging (sec23-style milestones)

- **M1 — model+engine:** schema v5, `calendar_events.py`, occurrence expansion,
  full unit tests. No UI yet. (De-risks the hard part first.)
- **M2 — read views:** merge events into Month grid; build Week view (read-only).
- **M3 — write path:** event form + create/update/archive/skip routes; route tests.
- **M4 — polish:** export hook, theme pass, week-view UX (current-time line,
  click-empty-slot-to-create).

Before touching the **live** DB: `python -m scripts.backup_db --keep 20` first
(sec19), test the migration on the copy, and restart the service via
`systemctl --user restart ephemeris` (never a broad `pkill`).

---

## 13. Open decisions (confirm before/while building)

These are choices I made with a default — flag to the user/implementer:

1. **Separate table vs extend `tasks`.** I chose a **separate `calendar_events`**
   table so recurring time-blocks don't pollute the task smart-lists/Matrix and so
   events have no completion semantics (a class "happens", it isn't "done").
   *Alt:* add time+recurrence columns to `tasks`, closer to TickTick's unified
   model, but then recurrence-completion semantics must be designed. **Default:
   separate table.**
2. **Recurrence grammar.** I kept `once / daily / weekly-by-weekday + interval`,
   which fully covers the sport case and most routines. *Alt:* full iCal RRULE
   (monthly-by-nth-weekday, count-based ends, etc.) — much larger. **Default: the
   compact grammar; extend later if needed.**
3. **Per-occurrence override** (move/rename one instance) — **stretch.** v1 =
   skip-one + edit-series only. If wanted in v1, add a `calendar_event_overrides`
   child table `(event_id, date, patch_json)` consulted during expansion.
4. **Week start.** I matched the month grid's **Sunday-start**. TickTick lets you
   choose Mon/Sun. **Default: Sunday**, single setting later.
5. **Default visible hour band** for the week view: **06:00–23:00**, auto-expanding
   to fit out-of-band occurrences. Adjustable.
6. **Cell ordering in the month grid** when a day has both events and tasks:
   events (by time) first, then tasks. Confirm.
7. **Overlap layout refinement** (§6.1): v1 ships **equal-width columns** within an
   overlap cluster. The Google-style **expand-to-fill** (events widen into adjacent
   free columns) is **off by default** — nicer but more layout code. Confirm whether
   v1 or later.
