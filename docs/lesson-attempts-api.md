# Lesson attempt endpoint (D4)

Status: frozen with D4. This is the HTTP contract for recording learner
attempts — the backend the D5 bridge `attempts` capability calls, and the
curl surface for the study agent / owner. The data model it implements
(authority table, projection, idempotency, staleness) is
[learn-bundle-spec.md §6](learn-bundle-spec.md); the wire membrane that will
carry it from a lesson page is [lesson-bridge-abi.md](lesson-bridge-abi.md).
D5 wired the bridge side: the `attempts` capability and the port `attempt`
operation ([lesson-bridge-abi.md §3.1](lesson-bridge-abi.md)) call this
endpoint from the parent runtime with parent-derived identity and
`idempotency_key = request_id`. Nothing here wakes an agent (Check v1 is
save-only).

Trust model, restated from the D2 review gate: possession of a bridge port
is never authority. This endpoint re-validates every submission against the
**record-time** manifest server-side — the lesson uid comes from the DB row,
the question must be declared in `questions[]`, and `stale` is derived by
the server from the current binding and current page bytes. The client
supplies only what it saw at load time, and only for comparison.

## Routes

```
POST /learn/lessons/{lesson_id}/attempts
POST /learn/lessons/by-slug/{slug}/attempts     (alias, same handler)
```

Both are unsafe-method routes behind the app perimeter (`app/security.py`):
same-origin browser fetch and origin-less non-browser clients pass; a
cross-origin or `Origin: null` request (the sandboxed lesson iframe itself)
is refused with 403 before the handler runs. Requests must be
`application/json` with a `Content-Length` ≤ 256 KiB.

## Request body

```json
{"question_id": "q_5a1c380f6e",
 "page_id": "pg_3f8ba65c12",
 "page_rev": "sha256:…64 lowercase hex…",
 "answer": "free text ≤ 32 KiB UTF-8",
 "idempotency_key": "opaque token, 1–128 chars"}
```

- `question_id` / `page_id`: §3 grammar (`q_…` / `pg_…`). `page_id` and
  `page_rev` are the **load-time** identity the learner actually saw — the
  D2 parent runtime takes them from the `welcome` it issued; they are
  compared for staleness, never trusted as current.
- `page_rev`: `sha256:` + 64 lowercase hex.
- `answer`: any UTF-8 string ≤ 32 KiB; additionally the serialized §6.2
  projection line must fit 64 KiB (heavy escaping can exceed it first).
- `idempotency_key`: opaque, unique **per lesson** (§6.3). The D5 parent
  maps the bridge `request_id` onto it. Replaying a key whose stored
  (`question_id`, `page_id`) match returns the original attempt; the same
  key with a different question/page is a distinct conflict. The replay
  check precedes every record-time refusal: a retry of an already-durable
  write returns its `attempt_id` even when the manifest has since been
  rejected or the question retired — the refusal table below governs only
  new writes.
- Unknown fields are ignored (forward compatibility).

## Success responses (HTTP 200)

```json
{"ok": true, "result": "recorded", "attempt_id": "…uuid…",
 "stale": false, "attempt_number": 3, "projection": "projected"}
```

- `result`: `recorded` (row + `lesson_attempt` ledger event committed in one
  transaction) or `duplicate` (idempotent replay; nothing written —
  `attempt_id`/`stale` are the original's, `attempt_number`/`projection`
  absent).
- `stale`: the §6.4 record-time flag — `true` when the question is now bound
  to a different page than submitted, the current page bytes hash differently
  from `page_rev`, or the current revision is unknowable (file missing,
  unreadable, or symlinked). Stale attempts are recorded, never dropped.
- `attempt_number`: 1-based count of recorded attempts for this
  (lesson, question) — the D5 toast's "attempt #N recorded".
- `projection`: `projected` when `attempts.jsonl` now reflects the write
  (fast append or full reconcile rebuild), `pending` when the filesystem
  refused — the authoritative row is durable regardless, and the next
  successful write or reconcile heals the file.

## Refusals

`{"ok": false, "error": "<code>", "detail": "…"}` with:

| status | error | condition |
|--------|-------|-----------|
| 400 | `invalid-question-id`, `invalid-page-id`, `invalid-page-rev`, `invalid-idempotency-key`, `invalid-answer`, `invalid-json`, `invalid-request` | grammar/shape violations |
| 400 | `answer-too-large` | answer > 32 KiB UTF-8, or projection line > 64 KiB |
| 404 | `unknown-lesson` | no such lesson id/slug |
| 409 | `manifest-rejected` | record-time manifest read is rejected (§9.2 — attempt writes refused) |
| 409 | `identity-mismatch` | manifest `lesson_uid` ≠ DB uid (§3 — resolved explicitly, never by a write) |
| 409 | `attempts-unavailable` | profile grants no attempt affordance (v1, `legacy-display`, unknown profile — §5) |
| 409 | `idempotency-conflict` | known key, different question/page (§6.3) |
| 411 / 413 / 415 | `length-required` / `payload-too-large` / `unsupported-media-type` | body admission |
| 422 | `unknown-question` | `question_id` not declared in the record-time manifest (§4.3/§6.4) — the mandated distinct response |
| 429 | `rate-limited` | > 20 attempts per lesson per 60 s window (`Retry-After` set). The sliding window lives in server-process memory: the deployment model is one worker process (the loopback systemd unit), so the bound is per deployment in practice; during a rolling restart two processes can briefly hold separate windows (bounded 2× for the overlap). The limit is an abuse damper, not a security boundary — body caps, grammar/manifest validation, and the durable-write semantics never depend on it. Replays and key conflicts are not charged. |

The D5 bridge attempt operation reuses these `error` codes verbatim as its
port-level result codes (the slot reserved in lesson-bridge-abi.md §3).

## Storage effects of one recorded attempt

1. `lesson_attempts` row + `lesson_attempt` event (payload per spec §8:
   `lesson_uid`, `lesson_id`, `slug`, `attempt_id`, `page_id`,
   `question_id`, `page_rev`, `answer`, `stale`) — one transaction, event
   identity via `events.uuid` (B4). `created_at` is UTC ISO-8601 and is the
   same string the projection echoes.
2. One §6.2 line appended to the bundle's `attempts.jsonl` under a
   private per-lesson UID lock (O_APPEND + fsync, `O_NOFOLLOW`, singly linked
   regular files only). A small durable cursor/seal under the configured
   private data root lets the fast path select at most the next two authority
   rows and render at most one new line; projection filesystem work holds no
   SQLite writer transaction. The UID lock, not SQLite's database-wide writer
   lock, provides cross-process projection exclusion across cursor check,
   append/rebuild, and publication.
3. When the file or cursor is missing, torn, behind the table, reordered, or
   replaced by a special/multi-link file, the append falls back to an
   idempotent full rebuild from SQLite (ascending `created_at`, ties by
   `attempt_id`, atomic replace). Rebuild streams rows into the replacement
   file rather than retaining history in memory. A row committed after the
   rebuild's read snapshot cannot be projected by another process until the
   UID lock is released; that next lock holder observes it beyond the durable
   cursor and advances the file, so the PR #57 round-10 stale-rebuild race
   remains structurally excluded. `app/services/attempts.py:reconcile_projection`
   is the same rebuild as a public entry point.
