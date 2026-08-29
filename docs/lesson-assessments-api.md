# Lesson assessment endpoint (phase S, slices s1–s3)

Status: the authority layer (s1), its bundle projection (s2), and the session
write capability (s3). This is the
HTTP contract
for recording what the **tutor concluded** — the counterpart to
[lesson-attempts-api.md](lesson-attempts-api.md), which records what the
**learner did**. The design it implements is `S-DESIGN.md` D-S1-1 … D-S1-4 and
D-S2-2 (issue [#4](https://github.com/jointsome0-lgtm/ephemeris/issues/4)).

The caller is the lesson-agent terminal session — `curl` or any HTTP-capable
tool from the lesson-agent shell — or the owner from a shell. Nothing here is reachable from a lesson page: the bridge
ABI is the *learner's* channel and is untouched by phase S. Granting pages a
verdict-writing operation would let lesson content grade itself.

Trust model: the client supplies no identity it does not own. `lesson_uid`
comes from the DB row, `question_id` is copied from the referenced attempt row,
`seq` is the authority rowid, and `sitting_id` is derived from the session write
capability below. A `question_id` or `sitting_id` in the request body is an
unknown field and is refused.

## Routes

```
POST /learn/lessons/{lesson_id}/assessments
POST /learn/lessons/by-slug/{slug}/assessments   (alias, same handler)
```

Both are unsafe-method routes behind the app perimeter (`app/security.py`):
same-origin browser fetch and origin-less non-browser clients pass; a
cross-origin or `Origin: null` request (the sandboxed lesson iframe itself) is
refused with 403 before the handler runs. Requests must be `application/json`
with a `Content-Length` ≤ 64 KiB.

Unlike the attempt endpoint, these routes are **not** bridge-gated (D-S1-4):
no interactive runtime profile is required and the admission path reads no
manifest. The tutor's memory must work on every lesson, including the
`legacy-display` bundles that can never record attempts — a `review` implies
its attempt existed, and `evidence`/`summary` are exactly the kinds a tutor
produces on a legacy lesson (terminal experiments, spoken answers).

## Write capability (the sitting)

When the app opens a **lesson-agent** terminal session it mints an opaque token
bound in-process to that lesson and that session, and puts it — with the
complete per-lesson endpoint URL — into the session environment as
`EPHEMERIS_ASSESS_URL` and `EPHEMERIS_ASSESS_TOKEN`. Those two variables reach
the lesson-agent role only: recording assessments is the tutor's job, so the
learner and runner shells receive neither variable, and no broad `EPHEMERIS_`
prefix joins the child environment allowlist. The URL is built from the app's own bound address (the ASGI scope's
`server`), never from a client-supplied `Host` header.

The caller returns the token in the `X-Ephemeris-Assess-Token` header:

| header | resolution | effect |
|--------|-----------|--------|
| absent | — | admitted; `sitting_id` is NULL (the owner/manual `curl` path) |
| live token, this lesson | the session's SID | `sitting_id` is stamped server-side |
| live token, another lesson | — | 409 `capability-lesson-mismatch` |
| unknown, empty, or dead token | — | 403 `invalid-capability` |

This is **provenance, not authentication**: it answers "which lesson and which
sitting is this write from" without letting the request body claim either. The
endpoint stays open to the tokenless owner path by design — the deployment is
loopback, single-user, single-worker — and requiring a token would kill that
path and the bootstrapping of legacy lessons for nothing inside that boundary.

The registry is in-process only: no persistence, no TTL, no rotation. An entry
is created with its terminal session, revoked when that session closes, and
gone after an app restart. A token from a previous process therefore resolves
to 403 rather than silently degrading to an anonymous write — the tutor must
know its writes lost their provenance, and the generated brief tells it to say
so and keep tutoring.

`sitting_id` also carries one service rule: **one active `summary` per
sitting** (D-S0-1). A tutoring session closes with one synthesis; a second
summary in the same sitting must name the first in `supersedes`, or it is
refused with 409 `summary-exists` naming the row to supersede. A summary
written without a capability has no sitting and is not covered by the rule.

## Request body

```json
{"kind": "review",
 "mode": "tutoring",
 "level": "partial",
 "attempt_id": "…uuid of a recorded attempt of this lesson…",
 "note": "free text ≤ 8 KiB UTF-8",
 "idempotency_key": "opaque token, 1–128 chars"}
```

| field | required | applies to | value |
|-------|----------|------------|-------|
| `kind` | yes | all | `review` \| `evidence` \| `summary` \| `retraction` |
| `note` | yes | all | non-blank UTF-8 text ≤ 8 KiB |
| `idempotency_key` | yes | all | opaque, 1–128 chars, no control characters, unique per lesson |
| `mode` | no | all | `tutoring` (default) \| `exam` |
| `level` | yes | `review`, `evidence` | review: `correct` \| `partial` \| `incorrect` \| `unclear`; evidence: `seen` \| `weak` \| `developing` \| `passed` |
| `basis` | yes | `evidence` only | `attempts` \| `artifacts` \| `runs` \| `live` \| `mixed` |
| `attempt_id` | yes for `review` | `review`, `evidence` | UUID of a recorded attempt **of this lesson** |
| `concepts` | yes for `evidence` | `review`, `evidence`, `summary` | 1–8 opaque refs (learn-bundle-spec.md §4.5: 1–200 chars, no control characters), deduplicated server-side in first-occurrence order |
| `next_action` | no | `summary` only | non-blank UTF-8 text ≤ 512 bytes |
| `supersedes` | yes for `retraction` | all | `assessment_id` of an existing assessment **of this lesson** |

- Unknown top-level fields are refused (strict, unlike the attempt endpoint's
  forward-compatible stance): a tutor typo such as `conepts` must fail loudly,
  not silently drop the mastery statement.
- An explicit `null` reads as absent for every optional field, so a generator
  that emits all keys uniformly is not punished for it. Nothing is coerced: a
  present non-null value is validated as given.
- A field outside its kind's column above is refused with that field's own
  code (`basis` on a review, `next_action` on an evidence, and so on). The
  same rules are enforced a second time by per-kind schema CHECK constraints,
  so the typed authority stays structurally valid under restore tooling or any
  future second writer.
- `level` vocabularies are closed and mutually exclusive judgments. Practice
  *volume* is derivable from the attempts/runs record and is deliberately not
  restated in evidence.
- `basis` records what grounded a judgment. `live` marks a non-replayable
  tutor observation (a spoken answer, watched terminal work) and is admissible
  for **any** level including `passed` — a single-user trust posture: the
  record is honest about its grounding rather than gatekept.

## Success responses (HTTP 200)

```json
{"ok": true, "result": "recorded", "assessment_id": "…uuid…",
 "seq": 41, "projection": "projected"}
```

- `result`: `recorded` (row + `lesson_assessment` ledger event committed in one
  transaction) or `duplicate` (idempotent replay; nothing written — the
  original row's `assessment_id` and `seq`).
- `seq`: the authority rowid. It is **the** recency/ordering authority — the
  active-state fold below is by `seq`, and `created_at` (UTC ISO-8601 with
  microseconds) is display metadata.
- `projection`: `projected` when the bundle's `assessments.jsonl` now reflects
  the committed state, `pending` when it does not — a busy or unavailable
  lock, a bundle root that cannot be opened safely, a manifest whose identity
  contradicts the lesson, or any filesystem failure. The authoritative row is
  durable either way; a pending file heals at the next reconcile trigger.
  Format and mechanics: `docs/learn-bundle-spec.md` §6.5.

## Idempotency

`idempotency_key` is unique per lesson. The service canonicalizes the complete
validated submission (absent fields omitted, concepts deduplicated in
first-occurrence order, exact `note`/`next_action` bytes, sorted keys) and
stores the SHA-256 of that canonical form as the row's fingerprint.

- same key + same fingerprint → the original `assessment_id` and `seq`,
  `result: "duplicate"`, nothing written;
- same key + different fingerprint → `idempotency-conflict`.

The attempt endpoint's "same key + same question/page" shortcut is deliberately
**not** inherited: an assessment carries a free-text judgment, so a key
replayed with a different judgment is a visible conflict, never a silent
coalesce.

The replay lookup precedes every mutable-state refusal — the archive check, the
attempt/`supersedes` reference checks, and the rate limit included. A retry of
an already-durable write returns its `assessment_id` even when the lesson has
since been archived or the window is exhausted; the refusal table below governs
only NEW writes. Capability resolution is *not* a mutable-state refusal but a
fact about the request, so it runs first, with validation: a retry presenting a
dead token is refused rather than answered with a quiet duplicate.

`sitting_id` is not part of the fingerprint (nothing client-supplied is
missing from it, and the sitting is not client-supplied at all): provenance is
recorded once, by the write that actually lands.

## Refusals

`{"ok": false, "error": "<code>", "detail": "…"}` with:

| status | error | condition |
|--------|-------|-----------|
| 400 | `unknown-field` | a top-level field outside the request table |
| 400 | `invalid-kind`, `invalid-mode`, `invalid-level`, `invalid-basis` | value outside a closed vocabulary, or a field not valid for this kind |
| 400 | `invalid-attempt-id`, `invalid-supersedes` | not a UUID, missing where the kind requires it, or supplied where the kind forbids it |
| 400 | `invalid-concepts` | not a list of 1–8 valid §4.5 refs, missing on evidence, or supplied on a retraction |
| 400 | `invalid-note`, `invalid-next-action` | blank, non-text, not valid UTF-8, or (`next_action`) supplied outside a summary |
| 400 | `note-too-large`, `next-action-too-large` | over 8 KiB / 512 UTF-8 bytes |
| 400 | `invalid-idempotency-key` | absent, > 128 chars, or carrying control characters |
| 400 | `invalid-json`, `invalid-request` | body is not a JSON object; malformed `Content-Length` |
| 403 | `invalid-capability` | the `X-Ephemeris-Assess-Token` header is present but names no live session capability (never minted, its session ended, or the app restarted). Never a silent fallback to the tokenless path |
| 404 | `unknown-lesson` | no such lesson id/slug |
| 409 | `capability-lesson-mismatch` | the capability is live but belongs to another lesson than the URL's |
| 409 | `summary-exists` | this sitting already has an active `summary` and the new one does not supersede it (the detail names the `assessment_id` to supersede) |
| 409 | `lesson-archived` | the lesson is archived — the owner restores it first; assessments are never written into a lesson that has been put away |
| 409 | `lesson-unavailable` | the lesson row carries no `uid` (unreachable after the schema-v11 backfill; fail-closed) |
| 409 | `idempotency-conflict` | known key, different submission |
| 411 / 413 / 415 | `length-required` / `payload-too-large` / `unsupported-media-type` | body admission |
| 422 | `unknown-attempt` | `attempt_id` is not a recorded attempt of this lesson |
| 422 | `unknown-supersedes` | `supersedes` is not an assessment of this lesson |
| 429 | `rate-limited` | > 30 assessments per lesson per 60 s window (`Retry-After` set). The sliding window lives in server-process memory: the deployment model is one worker (the loopback systemd unit), so the bound is per deployment in practice; during a rolling restart two processes can briefly hold separate windows (bounded 2× for the overlap). The limit is an abuse damper, not a security boundary — body caps, validation, and the durable-write semantics never depend on it. Replays and key conflicts are refunded. |

## Storage effects of one recorded assessment

1. One `lesson_assessments` row (schema v14) + one `lesson_assessment` ledger
   event, in ONE transaction. No filesystem work runs inside that transaction.
2. The event payload echoes the full record: `lesson_uid`, `lesson_id`,
   `slug`, `assessment_id`, `seq`, `kind`, `mode`, `sitting_id`, `level`,
   `basis`, `attempt_id`, `question_id`, `concepts`, `note`, `next_action`,
   `supersedes`, `created_at` — so the synthesis rides the JSONL export.
3. After that transaction commits — never inside it — the bundle's
   `assessments.jsonl` is rewritten from the freshly re-read committed active
   state under an app-private per-lesson lock outside the bundle (spec §6.5).
   The response's `projection` field reports the outcome; a `pending` file is
   reconciled at the next trigger: a lesson-agent terminal open, an idempotent
   replay, or the first assessment call for that lesson in a process.
4. Nothing else. The record panel is slice s4.

Rows are append-only: there is no update and no delete route, and none is
planned. A wrong record is corrected by a later row — `supersedes` on a new
`review`/`evidence`/`summary`, or a `retraction` whose `note` says why the
target was wrong. `supersedes` must name an existing assessment of the same
lesson, so append order makes correction chains acyclic for free.

**Active state** (the fold every consumer reads —
`app/services/assessments.py:active_state`): rows not targeted by any
`supersedes`, folded by `seq` — the latest active evidence per concept, the
latest active review per attempt, the latest active summary. Retractions carry
no state of their own; they only deactivate, and never appear in the fold. Full
history stays queryable in SQLite and rides the export.

## Export, search, and recovery

Deliberately separated (they are three contracts, not one):

- **Export** — the JSONL export carries `lesson_assessment` events, which makes
  the tutor's conclusions **exportable and inspectable**. It is an audit feed.
- **Search** — Learn's in-app search covers lesson title/notes/source_url only;
  assessment notes are **not** searchable in v1. In-app assessment search is a
  named candidate issue.
- **Recovery** — restore tooling keeps unknown events as audit and does **not**
  reconstruct typed tables from the event stream. Authoritative tutor-memory
  recovery is therefore a consistent **SQLite backup** of the private instance
  database, not a replay of the export.

## Deployment boundary

Everything above is designed for the documented deployment only: direct
loopback (127.0.0.1:8765), single user, single worker, no auth layer. Wider or
multi-user deployment is out of scope — with no auth, any local process that
can reach the port can write assessments. The per-session capability binds a
write to a lesson and a terminal sitting where one is presented; it does not
turn the endpoint into an authenticated surface, and the tokenless owner path
stays admitted by design inside this boundary.
