# Lesson manifest fixtures (Vera Example demo data)

Invented-from-scratch demo manifests for the Learn bundle contract
([`docs/learn-bundle-spec.md`](../../docs/learn-bundle-spec.md), frozen by
issue #39). No file here contains real lesson content, titles, or URLs; every
file carries the "Vera Example" marker that
`scripts/check_public_hygiene.py` requires of public fixtures.

- `cases.json` is the machine-readable expectation table. `expect` is the
  reader outcome (`ok` / `degraded` / `rejected`); `findings` lists finding
  codes that MUST appear (informational extras MAY appear on top). Its
  `context.runner_registry` is a fixture-only registry that tests MUST
  install (runner expectations are not executable against the real, later
  F3 registry); the migration case carries its DB context (`lesson_uid`,
  `db_current_entry`) the same way.
- Every `*.json` manifest is stored in the **canonical serialization** of
  spec §9.3 (`json.dumps(…, ensure_ascii=False, indent=2)` + newline,
  recursive key order): C3's verify round-trips each accepted fixture
  through the canonical writer and asserts byte-identity.
- `v1-valid.json` → `v1-migrated.json` is the executable form of the
  migration mapping (spec §10): migrating the v1 fixture with its
  `lesson_uid` (`1b7e…1f04`) must reproduce `v1-migrated.json`
  byte-for-byte, deterministic page ids included.
- `v2-unreadable.json.broken` is deliberately invalid JSON for the
  `manifest-unreadable` case; the `.broken` suffix keeps it out of `*.json`
  globs and JSON-parsing tooling.
- There is deliberately no `attempts.jsonl` fixture: `*.jsonl` is denied
  repo-wide by the hygiene policy (the real file is runtime data). The
  projection record format is specified with an inline example in spec §6.2.
- Codes and behaviors with no fixture here need runtime context and are
  synthesized in C3/C4 tests instead: `manifest-too-large` (an oversized
  file), `identity-mismatch` / `stale-metadata` (need a DB row to disagree
  with), `symlinked-path` / `symlinked-bundle` (need a filesystem), migration
  rerun/idempotency, rename/edit id stability, and the §10 current-entry
  head-insertion.

Consumers: C3 (`verify.py` dual-read/normalization tests), C4 (migration
tool dry-run/idempotency/byte-equality tests).
