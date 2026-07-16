# Learn bundle contract — `lesson.json` schema v1/v2, attempts, artifacts

Frozen by [#39](https://github.com/jointsome0-lgtm/ephemeris/issues/39)
(session C1, 2026-07-16). This document is the single schema owner for the
lesson bundle: field names, identity, limits, compatibility, and discovery
rules that were previously scattered across #1, #35, #36, and #38.

Consumers, in dependency order:

- the Learn UI and preview (`app/services/lessons.py` — implements the v1
  subset today; v2 readers land in C3);
- the study agent, via the generated `AGENTS.md` teaching contract (#35);
- the attempt surfaces: postMessage bridge (D2), attempt backend (D4),
  editor/runner (F1–F5);
- deterministic external adapters owned by Selfos
  ([selfos#26](https://github.com/jointsome0-lgtm/selfos/issues/26)), per the
  integration decision in
  [selfos#25](https://github.com/jointsome0-lgtm/selfos/issues/25).

Conformance language: MUST/MUST NOT are contract; SHOULD is a default a
conforming writer follows unless it documents why not. Until C3 lands, the
running code implements only the v1 subset; this document still governs what
C3+ is allowed to build.

## 1. Boundary rules (non-negotiable)

- The manifest is the machine-readable index; HTML pages remain presentation.
  A conforming consumer never parses lesson HTML to discover structure.
- Everything in a bundle is a plain file under the bundle directory. No
  bespoke API, no hidden database.
- Atlas-facing values (`path`, `concepts`) are **opaque references**. No
  Ephemeris validation reads Atlas files, calls an Atlas URL, or imports
  Atlas code. Unknown refs are valid Ephemeris data; resolution findings
  belong to the adapter/Atlas side.
- A bundle remains fully usable when no Atlas or Selfos integration is
  configured.
- `studied` and the other lesson statuses are Ephemeris lifecycle values,
  never an Atlas confidence/mastery assertion.
- Bundles may contain private study content. Fixtures and docs use invented
  demo data only (see `fixtures/lesson-manifests/`, marked "Vera Example").

## 2. Bundle layout and reserved names

```
data/lessons/<slug>/
  lesson.json      manifest (this contract)
  index.html       conventional entry page
  related/         further pages, one self-contained HTML file per stage
  assets/          images/data referenced by pages (relative paths)
  attempts/        learner-authored work (default artifact root)
  attempts.jsonl   app-owned projection of recorded attempts (§6)
  AGENTS.md        app-generated agent brief — regenerated, never authored
  CLAUDE.md        app-generated shim for AGENTS.md — regenerated
```

Reserved names, which no page, block file, or artifact root may claim:
`lesson.json`, `attempts.jsonl`, `AGENTS.md`, `CLAUDE.md`.

Symlink policy (whole bundle, all consumers): symlinks are never followed —
not for the bundle directory itself, not for any file inside it. A path that
resolves through a symlink is treated as missing/invalid, mirroring the
existing `_bundle_path` / `_bundle_dir_is_safe` behavior.

## 3. Identity model

Five identifiers, five owners:

| id            | minted by                    | lives in                        | stable across                                   |
|---------------|------------------------------|---------------------------------|-------------------------------------------------|
| `lesson_uid`  | Ephemeris service (SQLite)   | `lessons.uid` column, manifest echo | title/slug/rename, path membership, page edits, page order |
| `page_id`     | manifest author (agent/app/migration) | `pages[].id`             | content edits, file rename, reorder             |
| `question_id` | manifest author              | `questions[].id`                | page edits that keep the question's meaning     |
| `block_id`    | manifest author              | `blocks[].id`                   | content edits, file path changes                |
| `attempt_id`  | attempt backend (SQLite, D4) | `lesson_attempts` row, projection record | forever (immutable once written)          |

Formats:

- `lesson_uid`, `attempt_id`: RFC 4122 UUID, lowercase, string form
  (same family as `events.uuid` from #17/B4).
- `page_id`, `question_id`, `block_id`: `^(pg|q|blk)_[a-z0-9]{4,32}$`.
  The suffix carries no meaning; it MUST NOT be derived from the title and
  MUST NOT be re-derived when the underlying file is renamed.

Authority: the SQLite `lessons` table (column `uid`, added in C3) is the mint
source and the truth for `lesson_uid`; the manifest carries an echo so a
bundle is self-describing for the agent and adapters. On mismatch the DB
wins, the reader reports `identity-mismatch`, and attempt writes are refused
until the mismatch is resolved explicitly (re-keying is a migration-tool
concern, not a read-path side effect).

### 3.1 ID lifecycle across page edits

- **Editing page content** keeps `page_id` and path. Revision is carried by
  `page_rev` (§6.3), not by identity.
- **Renaming a page file** keeps `page_id`; the same manifest edit updates
  `pages[].path`.
- **Reordering `pages[]`** changes presentation order only; ids untouched.
- **Deleting a page** removes its entry and retires the id forever — a
  retired id MUST NOT be reused for new content. Historical attempts keep
  referencing it; adapters treat ids as durable keys.
- **Splitting a page**: the original keeps its id; new pages mint new ids. A
  question moves with its id only while it remains the same question; if the
  question's meaning changes, mint a new `question_id` and retire the old.
- The same rules apply to `block_id` (a block's `file` may move between
  artifact-root locations without changing identity).

## 4. Manifest schema v2

Top-level fields, in canonical writer order:

| field                 | req | type / limits |
|-----------------------|-----|---------------|
| `schema_version`      | yes | integer `2` |
| `lesson_uid`          | yes | UUID string (§3) |
| `slug`                | yes | copy of DB slug, `^[a-z0-9]+(-[a-z0-9]+)*$`, ≤ 80 |
| `title`               | yes | copy of DB title, ≤ 240 |
| `source_url`          | no  | http(s) URL ≤ 1000, or `null` (treated as absent) |
| `entry`               | yes | bundle path (§4.1) of the default page; MUST equal some `pages[].path` |
| `pages`               | yes | ordered list, 1–200 items (§4.2) |
| `questions`           | no  | list, ≤ 200 items (§4.3) |
| `blocks`              | no  | list, ≤ 100 items (§4.4) |
| `path`                | no  | opaque learning-path ref (§4.5) |
| `step`                | no  | integer 1–10000; present only when `path` is present |
| `concepts`            | no  | ordered list of opaque refs, ≤ 64 items (§4.5) |
| `runtime`             | no  | `{"profile": …}` (§5); missing ⇒ `legacy-display` |
| `artifact_roots`      | no  | list of bundle dir paths, ≤ 8 (§7); missing ⇒ `["attempts"]` |
| `updated_by_agent_at` | no  | ISO-8601 timestamp, set by the agent on page/manifest edits |

`slug` and `title` are non-authoritative conveniences (the DB row is the
truth); a mismatch is an informational `stale-metadata` finding and the DB
value wins for display. Unknown fields at any level are ignored by readers
and MUST be preserved by writers (§9.3).

### 4.1 Path grammar

Applies to `entry`, `pages[].path`, `blocks[].file`, `artifact_roots[]`:

- bundle-relative POSIX path, 1–200 characters;
- no backslash, no control characters (U+0000–U+001F, U+007F);
- not absolute, no `.` or `..` segments, no empty segments (`//`), no
  trailing slash;
- must not be, or be nested under, a reserved name (§2);
- `entry` and `pages[].path` must end in `.html`.

Paths are compared exactly (case-sensitive); authors SHOULD stay lowercase
kebab-case. The `related/NN-topic.html` naming is the #35 teaching-contract
convention, not a schema rule.

### 4.2 `pages[]`

`{"id": "pg_…", "path": "related/01-….html", "title": "…"?}`

- `id`: required, §3 grammar, unique within the manifest;
- `path`: required, §4.1 grammar, unique within the manifest;
- `title`: optional display label ≤ 240 (tabs/adapters; not identity).

`pages[]` lists **every** page, entry included, in reading order. Reading
order is the list order — there is no separate ordering field.

### 4.3 `questions[]`

`{"id": "q_…", "page": "pg_…", "kind": "prediction"?, "label": "…"?}`

- `id`: required, unique; the durable key attempts reference;
- `page`: required, must reference an existing `pages[].id`;
- `kind`: optional, `^[a-z0-9_-]{1,40}$`; readers treat unknown kinds as
  `free_text`; recommended values: `prediction`, `free_text`, `self_check`;
- `label`: optional adapter-facing summary ≤ 200. The full prompt lives in
  the page HTML; the manifest only declares existence and identity.

A question that is not declared here does not exist: the attempt backend
rejects writes for undeclared `question_id`s (D4/D5).

### 4.4 `blocks[]` — editor/run blocks

`{"id": "blk_…", "page": "pg_…", "kind": "editor", "language": "python"?,
"file": "attempts/blk_…/main.py", "runner_id": "python-script-v1"?}`

- `id`: required, unique; `page`: required, must resolve;
- `kind`: required; v2 defines only `editor`. Unknown kind ⇒ the block is
  inert (dropped from the read model with a finding), the bundle still
  renders;
- `language`: optional editor hint, `^[a-z0-9+.-]{1,40}$`;
- `file`: required, §4.1 grammar, MUST be under an artifact root (§7),
  unique across blocks — this is the only place the app learns where a
  block's artifact lives (F1 saves resolve paths from the manifest, never
  from client input);
- `runner_id`: optional, `^[a-z0-9-]{1,64}$`. **The manifest never contains
  commands, arguments, env vars, or shell text.** A `runner_id` is an opaque
  key into a registry of fixed command templates compiled into the app
  (F3). Absent ⇒ save-only editor, no Run affordance. Unknown ⇒ Run
  disabled with a visible `unknown-runner` finding; the editor still works.

### 4.5 Opaque refs: `path`, `step`, `concepts`

- A ref is a string of 1–200 characters, no control characters. It is an
  atom: never resolved locally, never validated against Atlas, never used in
  a filesystem operation, never interpreted as a URL.
- `path` + `step` place the lesson on one learning path; `step` without
  `path` is dropped with an `invalid-ref` finding.
- `concepts` is ordered and deduplicated by exact string match; duplicates
  are deduped (first occurrence wins) with an informational
  `duplicate-concept` finding.
- There is no canonical local vocabulary file: ontology, drift, and merge
  belong to Atlas (selfos#25). Agents SHOULD reuse refs already present in
  the bundle's own history before inventing near-synonyms (#35 wording).

## 5. Runtime profiles

`runtime.profile` selects between **app-defined** policy sets; the manifest
can only pick a registered profile, never compose or widen policy (no CSP
fragments, no capability lists in the manifest).

| profile                | rendering | bridge | attempts | editor/run blocks |
|------------------------|-----------|--------|----------|-------------------|
| `legacy-display`       | sandboxed iframe as today | no | no | inert (visible in preview meta) |
| `interactive-local-v1` | sandboxed iframe + strict CSP (D1: `connect-src 'none'`, no remote scripts, no eval, no forms/popups/downloads) | eligible when the manifest is valid v2 (D2) | via bridge for declared questions (D4/D5) | active as features land (F phases) |

- Missing `runtime` ⇒ `legacy-display` (fail-closed default).
- Unknown profile ⇒ forced `legacy-display` + `unknown-profile` finding.
  Fail-closed means: never grant bridge, attempts, or run affordances under
  an unrecognized profile.
- v1 manifests are always `legacy-display`; there is no way to opt a v1
  manifest into interactivity.
- The preview metadata surfaces the effective profile (D1) and any findings.

New app-created bundles start as `interactive-local-v1`; migrated v1 bundles
start as `legacy-display` and are upgraded deliberately (§10, §12).

## 6. Attempts

### 6.1 Authority and projection

- **Authority**: the `lesson_attempts` SQLite table (D4). Each attempt row
  and its `lesson_attempt` ledger event are written in ONE transaction (the
  repo's standard write idiom; event identity via `events.uuid` from B4).
- **Projection**: `attempts.jsonl` at the bundle root — written by the app
  so the study agent reads attempts as a plain file, never touching the DB.
  The projection may lag or be lost; a reconcile pass rebuilds it from
  SQLite idempotently (dedupe by `attempt_id`, ascending `created_at`, ties
  by `attempt_id`; the pass may atomically rewrite the whole file).
- The projection is app-owned and read-only for everyone else. A projection
  failure never fails the authoritative write; the attempt response
  distinguishes durable+projected / durable+projection-pending / duplicate /
  stale-revision (D4).
- The manifest does NOT list attempts. Their canonical locations are fixed:
  the table, the projection file, and learner files under artifact roots.

### 6.2 Projection record format (`attempts.jsonl`, one JSON object per line)

```json
{"kind": "attempt", "v": 1,
 "attempt_id": "0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60",
 "event_uuid": "9c1e5a7b-3d2f-4b8e-9f4a-1b6d8c0e2a53",
 "lesson_uid": "7f2a4c88-9d3b-4e21-8b5a-6c0d1e9f3a72",
 "page_id": "pg_bubble001", "question_id": "q_predictswap1",
 "page_rev": "sha256:…64 lowercase hex…",
 "answer": "Vera Example: I predict the largest value reaches the end.",
 "created_at": "2026-07-16T12:00:00+00:00", "stale": false}
```

- `v` versions the record shape; unknown record versions and malformed lines
  are skipped by readers with a finding, never a crash;
- `answer` is free text, ≤ 32 KiB UTF-8; whole line ≤ 64 KiB;
- `created_at` is UTC ISO-8601.

### 6.3 Idempotency and content revision

- Idempotency key: an opaque client token ≤ 128 chars, unique per lesson,
  stored with the attempt row. Replaying a submission with a known key
  returns the original `attempt_id` and writes nothing. The bridge's
  `request_id` (D2/D5) maps onto it; the parent page owns the mapping — the
  iframe never supplies identity (lesson/page ids) directly.
- `page_rev` = `"sha256:" + hex` of the page file bytes as served when the
  learner loaded the page (parent-derived, D2). If the current file hash
  differs at record time, the attempt is still recorded, flagged
  `stale: true` — learning data is never dropped for being late.

## 7. Artifact roots and deterministic discovery

`artifact_roots` (default `["attempts"]`) bounds where learner-authored work
lives. Rules:

- each root follows §4.1, is a directory path, and MUST NOT be (or nest
  under) a reserved name; roots MUST be disjoint (no root is a path prefix
  of another); the list MUST always include `attempts`;
- every `blocks[].file` MUST fall under some artifact root (`outside-root`
  finding otherwise; the block is dropped from the read model);
- adapters and the app enumerate learner work ONLY inside artifact roots.

Deterministic adapter enumeration (no HTML parsing, no Atlas access):

1. read `lesson.json` (reject > 256 KiB), apply the version policy (§9);
2. identity = `lesson_uid`; pages/questions/blocks from the manifest;
3. `path`/`step`/`concepts` passed through verbatim as opaque values;
4. attempts from `attempts.jsonl` if present (per §6.2 tolerance);
5. learner files: walk each artifact root lexicographically, depth ≤ 4,
   at most 512 entries per root, regular files only, symlinks skipped with a
   finding; files > 2 MiB are listed but not read.

## 8. Ledger event echo policy

- All `lesson_*` events written after C3 include `lesson_uid` (alongside the
  existing `lesson_id` and, where already present, `slug`). History is never
  rewritten; pre-C3 events stay as-is, and a consumer needing identity for
  them joins on `lesson_id` against the local DB.
- `lesson_attempt` (D4) event payload: `lesson_uid`, `lesson_id`, `slug`,
  `attempt_id`, `page_id`, `question_id`, `page_rev`, `answer`, `stale`.
- Never echoed into events: `title`, `path`, `step`, `concepts`, `pages`.
  The manifest is the single truth for those; adapters read them from the
  bundle at delivery time, keyed by `lesson_uid`, so events can't carry
  stale copies. Idempotent adapter delivery rides `events.uuid` (B4).

## 9. Versioning, compatibility, and reader outcomes

### 9.1 Version policy

- Readers dual-read `schema_version` 1 and 2.
- A missing `schema_version` on an otherwise readable manifest is read as
  v1 (flat-file-era compatibility).
- Any other version (non-integer, `< 1`, `> 2`) ⇒ **visible reject**: the
  preview shows an explicit "unsupported manifest version" placeholder (same
  pattern as the missing-file placeholder), the lesson stays listed, and
  nothing is silently coerced to defaults.
- The read path NEVER rewrites a manifest on disk. Auto-creating a missing
  manifest for a fresh lesson is creation, not migration; upgrading v1 → v2
  happens only through the explicit migration tool (C4).

### 9.2 Reader outcomes and finding codes

Three outcomes: `ok` (render, possibly with informational findings),
`degraded` (render continues; specific items dropped or features disabled),
`rejected` (visible placeholder; no page render; attempt writes refused).
Readers MUST surface findings to the preview metadata — silently discarding
a finding is non-conforming.

| finding              | outcome   | condition |
|----------------------|-----------|-----------|
| `manifest-unreadable`| rejected  | file present but not valid JSON, or not a JSON object |
| `manifest-too-large` | rejected  | manifest > 256 KiB |
| `unsupported-version`| rejected  | §9.1 |
| `missing-identity`   | rejected  | v2 without `lesson_uid`, or `lesson_uid` not a UUID |
| `duplicate-id`       | rejected  | id repeated within pages/questions/blocks |
| `duplicate-path`     | rejected  | `pages[].path` repeated, or two blocks claim one file |
| `limit-exceeded`     | rejected  | any §4 list/size limit exceeded |
| `no-pages`           | rejected  | v2 with zero valid pages |
| `invalid-entry`      | degraded  | `entry` invalid or not in `pages[]`; fall back to first valid page |
| `invalid-path`       | degraded  | a page/root path violates §4.1; that item is dropped |
| `outside-root`       | degraded  | `blocks[].file` outside every artifact root; block dropped |
| `dangling-ref`       | degraded  | question/block references a missing `page_id`; item dropped |
| `unknown-profile`    | degraded  | §5; forced `legacy-display` |
| `unknown-runner`     | degraded  | §4.4; Run disabled, editor stays |
| `invalid-ref`        | degraded  | malformed `path`/`concepts` entry or orphan `step`; ref dropped |
| `identity-mismatch`  | degraded  | manifest `lesson_uid` ≠ DB `uid`; render as `legacy-display`, refuse attempt writes |
| `stale-metadata`     | info      | `slug`/`title` differ from DB; DB wins |
| `duplicate-concept`  | info      | §4.5; deduped |

`manifest-unreadable` and `manifest-too-large` apply to v1 reads too — this
is the one deliberate v1 behavior change: a corrupt manifest becomes a
visible reject instead of silently rendering an empty default (#39's
"silent projection" complaint). A *missing* manifest keeps today's behavior
(a fresh default is created — creation, not repair).

v1 reads otherwise keep today's tolerant normalization: invalid `entry`
falls back to `index.html`, invalid/duplicate `related[]` items are dropped,
`related` deduplicates against `entry`. C3 readers SHOULD emit the matching
findings (`invalid-entry`, `invalid-path`, `duplicate-path`) for v1 instead
of dropping silently; behavior (what renders) is unchanged.

### 9.3 Unknown fields

Unknown fields — top-level or nested — are ignored by readers and preserved
byte-faithfully by every writer (agent, app, migration tool). Additive
evolution inside v2 means new OPTIONAL fields; any change to the meaning of
an existing field requires v3. Writers use the canonical serialization:
UTF-8, 2-space indent, `ensure_ascii=False`, trailing newline, known keys in
§4 table order, unknown keys after them in their original relative order.
Round-tripping a valid manifest MUST preserve unknown fields and page order;
byte-identity beyond that is not required (but the canonical writer achieves
it, which C4 uses for hash-based post-verification).

## 10. v1 → v2 migration mapping (consumed by C4)

| v2 field | source |
|----------|--------|
| `schema_version` | `2` |
| `lesson_uid` | SQLite `lessons.uid` (minted/backfilled in C3; the tool never mints) |
| `slug`, `title`, `source_url` | copied from v1 (they already mirror the DB) |
| `entry` | v1 `entry`, verbatim after v1 validation |
| `pages` | `[entry, *related]`, order preserved; ids minted deterministically: `"pg_" + sha256(lesson_uid + "\n" + path)` first 16 hex — reproducible across dry-run/run/re-verification; NOT re-derived on later renames (§3.1) |
| `questions`, `blocks`, `path`, `step`, `concepts` | absent (the agent adds them later per #35) |
| `runtime` | `{"profile": "legacy-display"}` |
| `artifact_roots` | `["attempts"]` |
| `updated_by_agent_at` | preserved |
| unknown v1 fields | preserved verbatim |

Invariants: HTML page bytes untouched; the DB `current_entry` selection
untouched; idempotent (a v2 input is a no-op); dry-run, atomic replacement,
rollback manifest, and hash post-verification are C4 tool requirements. The
fixture pair `v1-valid.json` → `v1-migrated.json` is the executable form of
this table (given the fixture's `lesson_uid`).

## 11. Fixtures

`fixtures/lesson-manifests/` — invented demo data (every file carries the
"Vera Example" marker required by `scripts/check_public_hygiene.py`; no real
lesson content). `cases.json` is the machine-readable expectation table:
each case names a fixture, the expected outcome, and finding codes that MUST
appear (others MAY). Consumers: C3 `verify.py` reader tests, C4 migration
tool tests.

| fixture | expectation |
|---------|-------------|
| `v1-valid.json` | v1 read, `ok` |
| `v1-migrated.json` | v2 read, `ok`; byte-equal to migrating `v1-valid.json` with the fixture's `lesson_uid` |
| `v2-valid.json` | v2 read, `ok` (full feature surface) |
| `v2-unknown-fields.json` | v2 read, `ok`; unknown fields preserved on round-trip |
| `v2-invalid-paths.json` | `degraded`: `invalid-entry`, `invalid-path`, `outside-root` |
| `v2-duplicate-refs.json` | `rejected`: `duplicate-id`, `duplicate-path` |
| `v2-missing-identity.json` | `rejected`: `missing-identity` |
| `v99-unsupported-version.json` | `rejected`: `unsupported-version` |

The projection record example lives inline in §6.2 (a committed
`attempts.jsonl` would trip the repo-wide `*.jsonl` hygiene denial — the
real file is runtime data and never enters Git).

## 12. Write authority

| surface | app | study agent | migration tool (C4) | learner |
|---------|-----|-------------|---------------------|---------|
| `schema_version` | at creation | — | v1→v2 only | — |
| `lesson_uid` | mints (SQLite) + echoes | — | copies from DB | — |
| `slug`, `title`, `source_url` | owns (DB + echo) | — | copies | — |
| `entry`, `pages`, `questions`, `blocks` | creation skeleton | ✎ mints ids per §3, keeps them stable | pages from `related[]` | — |
| `path`, `step`, `concepts` | — | ✎ may add, revise, remove (this is how the knowledge map accrues, #38) | — | — |
| `runtime.profile` | sets at creation/migration | ✎ may switch between **registered** profiles (e.g. upgrade a reworked legacy bundle); unknown values fail closed | sets `legacy-display` | — |
| `artifact_roots` | default | ✎ may append (≤ 8, must keep `attempts`) | default | — |
| `updated_by_agent_at` | — | ✎ sets on edits | preserved | — |
| lesson pages / `assets/` | creation placeholder | ✎ authors | byte-preserved | — |
| `attempts.jsonl` | owns (projection + reconcile) | read-only | — | read-only |
| `attempts/` files | editor save endpoint (F1) | read (SHOULD not edit learner work — #35) | — | ✎ via terminal/editor |
| `AGENTS.md`, `CLAUDE.md` | regenerates (B1 writer) | overwritten | — | — |

The agent MUST NOT: change `lesson_uid` or `schema_version`, reuse retired
ids, write `attempts.jsonl`, or put commands into the manifest (§4.4).
Agent-caused violations degrade or reject visibly per §9.2 — the app never
silently rewrites an agent's manifest to "fix" it.

## 13. Out of scope here (owned elsewhere)

- Bridge ABI: handshake, `MessageChannel`, capability negotiation — D2.
- CSP profile enforcement details — D1.
- Attempt endpoint semantics, rate limits, responses — D4/D5.
- Runner registry contents and sandbox profiles — F3/E1.
- Teaching-contract wording in `_AGENTS_TEMPLATE` — #35 (C2), which cites
  the names frozen here.
- Atlas viewer embedding — #38 / selfos#25 (URL-only, gated on a full
  integration contract).
