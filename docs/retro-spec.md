# Spec — Retro Capture (sec33, issue #49)

> Owner-typed retrospectives over approximate periods, captured in ephemeris
> and journaled for the future selfos → exp2res adapter. Numbered **sec33** in
> the `docs/system-design.md` sequence (same pattern as sec32 living in
> `docs/calendar-events-spec.md`). Grounded in schema v10 (`app/db.py`),
> `app/services/retro.py`, the `/retro` routes (`app/main.py`) and
> `app/templates/retro.html`.

---

## 1. Purpose

A retro is the owner's answer to "what was period X about?" — free text over an
*approximate* time span ("Q1 2026", "2026-05-01/2026-06-15", "no idea when").
In the selfos ecosystem (sec26) this is experience evidence: exp2res owns
retrospection semantics (its SDD §19/§23), but the capture UX belongs in
ephemeris, the daily surface the owner already lives in. Ephemeris stores and
journals entries; a selfos adapter later converts the exported snapshots for
exp2res import. Ephemeris never parses or calls exp2res (sec26 still holds).

## 2. Data model (schema v10)

`retro_entries` (`app/db.py` `_SCHEMA_V10`): `id`, `uuid` (minted at insert,
immutable across edits, UNIQUE), `period_raw`, `precision`, `confidence`,
`period_start`, `period_end`, `project`, `text`, `created_at`, `updated_at`,
`archived_at`. Soft-archived, never hard-deleted (sec14.1 joinability).

**Raw vs derived rule:** `period_raw` is the owner-typed truth and the only
temporal field a future adapter ships downstream (together with `precision` and
`confidence`); exp2res re-resolves it in its own workspace timezone.
`period_start`/`period_end` are ephemeris-local derivations (APP_TIMEZONE or
host zone) computed at write time, kept *only* for list ordering and display —
they also catch typos ("2026-13", "Q5 2026", a reversed range) at the moment
the owner can still fix them.

## 3. Period contract

The vocabularies and grammar **mirror exp2res `services/time_input.py` and
`domain/enums.py` verbatim — do not diverge**; the invariant is acceptance-set
equality: anything ephemeris accepts must later import into exp2res cleanly.

Precision: `exact_datetime`, `exact_day`, `week`, `month`, `quarter`, `year`,
`date_range`, `approximate_range`, `unknown`.
Confidence: `low`, `medium`, `high`, `unknown`.

| precision | accepted `period` | resolved start | end |
| --- | --- | --- | --- |
| `year` | `2026` | Jan 1 00:00 local | — |
| `month` | `2026-05` | day 1 00:00 | — |
| `quarter` | `Q1 2026` or `2026-Q1` | quarter's first day | — |
| `week` | `2026-W23` (ISO week) | Monday 00:00 | — |
| any above, named form missed | falls through to full ISO 8601 parse (exp2res does the same) | as parsed | — |
| `exact_day`, `exact_datetime` | ISO 8601 datetime, `Z` → `+00:00` | as parsed | — |
| `date_range`, `approximate_range` | `START/END`, both sides ISO 8601; **end > start strict** | both | both |
| `unknown` | must be empty (stricter than exp2res, which ignores it — a typed period with `unknown` is a mis-picked dropdown) | — | — |

Shared rules ported with the grammar: out-of-range anchors (month 13, week 99)
are input errors, not crashes; naive local times that are DST-ambiguous or
nonexistent are refused ("add an explicit offset"); range endpoints are parsed
verbatim (a space around `/` fails, exactly as in exp2res — only the outer
period string is trimmed before storing); text is non-empty, ≤ 1 MB UTF-8, no
C0 controls except tab/newline/CR and no C1 controls (U+007F–U+009F) — the
same hygiene applies to `project`, which is otherwise optional
(whitespace-only → NULL).

Timezone for derived bounds: `APP_TIMEZONE` if set, else the IANA zone
`/etc/localtime` resolves to, else (last resort) the host's current fixed
offset — set `APP_TIMEZONE` for correct cross-DST anchors and ambiguity
detection.

**No precision inflation** (exp2res §16.7) is not machine-checkable at capture;
the form copy steers the owner: fuzzy memory → `approximate_range` with
low/medium confidence, because downstream can never sharpen what is recorded.

## 4. Event model

Event types: `retro_entry_created`, `retro_entry_updated`,
`retro_entry_archived`, `retro_entry_unarchived` — appended in the same
transaction as the write (sec14.1). Every payload is a **full post-write
snapshot** including `retro_uuid`: the JSONL export serializes
timestamp/type/payload_version/payload only (no `events.uuid`), so the stable
identity the adapter needs for its `(source_system, source_record_id)` dedup
key must ride the payload. Consumption rule for the adapter: group export lines
by `retro_uuid`, **latest event wins**; entries whose latest snapshot has
`archived_at` set are excluded. Archive/unarchive of an already-archived/active
entry is an idempotent no-op and appends nothing.

## 5. Routes and UI

`GET /retro` (list + create-or-edit form; `?archived=1` shows the archive,
`?edit=<id>` pre-fills the form), `POST /retro`, `POST /retro/{id}/edit`,
`POST /retro/{id}/archive`, `POST /retro/{id}/unarchive`. All writes follow the
sec16.4 dual-mode contract (Mode A no-JS form + 303 PRG with flash; Mode B
`x-partial: 1` → JSON, errors 422). The browser page currently submits Mode A
only (like Manage/Habits forms); Mode B is the verified server contract for
scripts and future enhancement. Nav: rail + More sheet + command palette,
`R == 'retro'`. The precision dropdown's option labels double as period-format
hints so the form explains itself without JS.

Edits are allowed: a retro is the owner's current phrasing of a memory, and the
ledger keeps every prior snapshot; downstream, an edit is just a changed
content hash under the same `retro_uuid`, which exp2res's idempotency /
correction machinery already models.

## 6. Restore note

`scripts/restore_from_export.py` re-inserts unknown event types into the ledger
verbatim, so retro history survives an export→restore round-trip, but the typed
`retro_entries` table is **not** rebuilt (reported under unknown types). Typed
replay is a follow-up; the full-snapshot payloads make it mechanical.

## 7. Open question (adapter-time, not capture-time)

exp2res §19.1 activity import labels records `imported_activity_event`, while
its native retro path (`capture_retro`) labels them `manual_claim` /
`user_memory`. Which evidence class ephemeris-captured retros should carry is
an exp2res/selfos spec decision to make when the adapter is built; the snapshot
carries everything either mapping needs. Tracked in issue #49.
