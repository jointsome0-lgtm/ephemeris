# Spec — Diary Tab (sec35, issue #2)

> Free-form dated journal entries, captured per entry and journaled for the
> future selfos → exp2res diary feed. Numbered **sec35** in the
> `docs/system-design.md` sequence (sec33 is retro, sec34 the focus timer).
> Grounded in schema v20 (`app/db.py`), `app/services/diary.py`, the `/diary`
> routes (`app/routers/diary.py`) and `app/templates/diary.html`.

---

## 1. Purpose

The Diary tab is the capture surface for the activity domain in the selfos
integration model: the owner writes what happened and what they think, and
downstream systems interpret it — exp2res's own extract/verify pipeline for
facts and signals, atlas's importer for knowledge-state evidence. Ephemeris
only captures, journals and exports. It never parses the text, never routes
an entry anywhere, and never calls a peer system (sec26 holds).

## 2. Relationship to `daily_notes`

`daily_notes` (schema v1) stays what it is: the habits-page one-text-per-day
widget with upsert semantics and `daily_note_updated` events. The Diary tab
is a different thing — a per-**entry** journal, because the selfos tags
contract is entry-granular: per-entry `private`, per-entry routing tags, and
several entries (a gap-question answer is an ordinary entry) may land in one
day. Neither surface replaces the other.

## 3. Data model (schema v20)

`diary_entries` (`app/db.py` `_SCHEMA_V20`): `id`, `uuid` (minted at insert,
immutable across edits, UNIQUE), `entry_date`, `text`, `tags_json`,
`private`, `atlas_ref`, `created_at`, `updated_at`, `archived_at`.
Soft-archived, never hard-deleted (sec14.1 joinability).

**Timestamp model:** `entry_date` is the day the entry belongs to —
owner-pickable, defaulting to today, never in the future. No period grammar:
diary is day-oriented; approximate periods belong to retro (sec33).
`created_at`/`updated_at` are the append-only ledger times, retro-style.

**Structured fields, opaque semantics** (selfos
[docs/tags.md](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/tags.md)
is the canonical contract — this spec does not restate it):

- `tags` — a list of opaque strings. Stored, journaled, exported unchanged.
  Which tags carry cross-system meaning is the selfos adapter's business.
  Capture hygiene only: trimmed, empties and exact duplicates dropped,
  bounded (`app/limits.py` `DIARY_TAG` / `DIARY_TAGS_MAX`), control
  characters refused. In-text `#hashtags` stay prose; capture-time lifting
  is #27's scope.
- `private` — a boolean, opaque here. **Set-only in the UI and in the
  service:** it can be set at creation or added later, and an edit can never
  clear it — the latch rides inside the UPDATE (`MAX(private, ?)` against the
  stored value), so a concurrent privatization can't be overwritten by a
  stale-read edit. The contract makes
  de-privatization a one-way latch — authoring a new non-private entry — so
  a clearable checkbox would promise a routing effect that doesn't exist.
- `atlas_ref` — an optional opaque string passed through untouched; its
  meaning belongs to atlas's intake.

No interpretation, no routing, no export filtering: the JSONL export remains
a full ledger replay, private entries included. The selfos adapter is the
primary privacy/routing gate; consumer-side enforcement is the second line.
Text is bounded by `app/limits.py` `DIARY_TEXT` (characters), and text and
the structured fields refuse C0 controls (except tab/newline/CR) and C1
controls, same capture hygiene as retro.

## 4. Event model

Event types: `diary_entry_created`, `diary_entry_updated`,
`diary_entry_archived`, `diary_entry_unarchived` — appended in the same
transaction as the write (sec14.1). Every payload is a **full post-write
snapshot** carrying `diary_uuid`: the export serializes
timestamp/type/payload_version/payload only, so the stable identity the
adapter's `(source_system, source_record_id)` dedup key needs must ride the
payload. Consumption rule: group export lines by `diary_uuid`, latest event
wins; entries whose latest snapshot has `archived_at` set are excluded.
Archive/unarchive of an already-archived/active entry is an idempotent no-op
and appends nothing. Edits append (no silent mutation); the uuid never
changes.

## 5. Routes and UI

`GET /diary` (day-grouped list, today first, browse back; `?archived=1`
shows the archive, `?edit=<id>` pre-fills the form), `POST /diary`,
`POST /diary/{id}/edit`, `POST /diary/{id}/archive`,
`POST /diary/{id}/unarchive`. Every write is a plain form post answered with
a 303 redirect back to the list; a rejected write redirects with the error as
a `flash` query parameter. There is no JSON response path (#214). Nav: rail +
More sheet + command palette, `R == 'diary'`; the rail/sheet links read the
`diary_home` Jinja global. An entry editing an already-private row shows a "can't be cleared"
badge instead of a checkbox.

## 6. Gap-questions strip (config-only coupling)

Rendered only when `SELFOS_EXP2RES_URL` is set (`app/settings.py`): the
Diary page embeds that URL — expected to be the exp2res `/questions`
loopback view (exp2res §30) — in a fully sandboxed iframe. Ephemeris never
fetches or parses it, learns no exp2res schema, sends no gap IDs or
link-back tokens, and has no answer callback: answering happens as an
ordinary diary entry, which reaches exp2res later through the normal
export → adapter → import path. Same-machine loopback is the only supported
topology (§30 refuses any other authority). Unset or unreachable ⇒ Diary is
fully usable; the strip's caption states plainly that an empty frame means
exp2res isn't serving — the unavailable state is honest, and no server-side
probe exists (that would be ephemeris calling exp2res).

## 7. Restore note

`scripts/restore_from_export.py` re-inserts unknown event types verbatim, so
diary history survives an export→restore round-trip, but the typed
`diary_entries` table is **not** rebuilt — the same posture as retro
(sec33 §6): typed replay is a follow-up the full-snapshot payloads make
mechanical.

## 8. Privacy posture

The diary concentrates the most sensitive personal text in a deliberately
no-auth app. The operational rules live in `docs/security-model.md` and
`docs/system-design.md` sec10.2: a diary-bearing instance stays on `127.0.0.1`. Diary content stays out
of agent context by default (selfos AGENTS.md → cloud-context data boundary;
the repo-side note is in AGENTS.md). The pre-ship adversarial pass over the
combined surface (terminal + diary + export + strip) was delegated to Codex per
repo convention; findings and dispositions are recorded on the PR for issue
#2.
