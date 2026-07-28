# Lesson assessment authority — adversarial security review

**Scope:** exactly one Pending entry was present at review start: the
2026-07-28 entry for `f614614` and `491f0e4` on
`fix/4-s1-assessments-authority`, landed by ordinary merge commit `5976697`
(PR #85). The listed surface is `app/db.py`,
`app/services/assessments.py`, `app/main.py`,
`docs/lesson-assessments-api.md`, `verify.py`, and
`docs/reviews/QUEUE.md`, plus the request-body, security, lesson, attempt,
bundle-grammar, event, archive, export, and restore direct callers.

**Starting HEAD:** `07a865221774f7c98863cf2739498e304ba28a4e` on
`main`, matching refreshed `origin/main`. `git status --short --branch`
showed only `## main...origin/main`; the starting worktree was clean.

**Fix target:** `f614614`, `491f0e4`, and merge commit `5976697` are all
ancestors of `main`. The merge tree and reviewed branch-head tree are
byte-identical at `6e460ddd02f193108162eb35c8be144b40e13546`; the only
change from `5976697` to the starting HEAD is the queue-entry documentation
that pins the merge hash. The entry is merged, so repo tradition and the drain
brief put every fix directly on `main`.

**Report file:**
`docs/reviews/2026-07-28-lesson-assessment-authority-review.md`, derived from
the entry's lesson-assessment-authority subject.

**Prior reports to reconcile:** every existing
`docs/reviews/*-review.md` closing verdict was scanned. The binding conditions
for this surface are:

- `2026-07-16-write-guard-review.md`: central write-guard finding W1 is
  resolved. Origin-less non-browser clients remain deliberately admitted only
  within the direct-loopback posture; proxy-adjacent and wider deployment
  remain unsupported.
- `2026-07-20-attempt-backend-review.md`,
  `2026-07-23-attempt-body-stream-cap-review.md`, and
  `2026-07-23-attempts-projection-cost-review.md`: attempt follow-ups A1/A2 are
  resolved, and attempt row/event atomicity, record-time authority,
  idempotency, streaming admission, and bounded private projection remain
  resolved. This entry must not regress the unchanged attempt writer.
- `2026-07-21-check-activation-review.md` and the later Phase-F reports:
  bridge-runtime D5 L1 remains mitigated, while D5 L2/L3 remain resolved.
  Artifact, runner, run-API, generated-pedagogy, bundle, and lesson-role
  protections remain resolved unless this new route changes one of their
  authorities.
- The terminal, workspace, and sandbox reports leave terminal-opt-in T1
  open/accepted only for the deliberately plain owner shell; the trusted
  lesson-agent's intentional loopback network and credential posture is
  unchanged. The same-user final artifact-publication window also remains
  open/accepted by design.
- `2026-07-27-platform-support-review.md`: portable import/startup,
  terminal/sandbox/runner, attempt, bridge, and public-data-boundary
  dispositions remain binding. This Python-only entry adds no platform-specific
  import and changes no listener or service command.

The closing verdict must state whether each condition remains resolved,
mitigated, open/accepted, or unchanged for the closing tree.

**Validation baseline:** approved host runs at the clean starting HEAD passed:
`python verify.py` — **797 passed, 0 failed**; `python verify_restore.py` —
**28 passed, 0 failed**.

## Context and method

The deployment decision assumes the documented unauthenticated, direct-
loopback `127.0.0.1:8765`, single-user, single-worker app. No service was
restarted or signalled, and no live database, lesson bundle, export, browser
profile, screenshot, or authenticated state was read or written. Executable
probes used an invented request and a throwaway directory under `/tmp`.

The complete landed diff and the assessment service, schema migration, route,
API contract, and verifier section were read in full. Direct tracing covered
the capped ASGI stream, central Host/origin perimeter, lesson id/slug
resolution, archive ordering, attempt and supersedes authority, canonical
fingerprints, rate-window refunds, explicit SQLite transaction boundaries,
caller-supplied event identity, active-state folding, JSON response encoding,
export, and restore posture. The frozen Phase-S memo, queue prose, tests, and
the PR's earlier review summaries were treated as claims to verify, not as
security-review evidence.

## Findings (severity-ranked)

### L1 — A lone-surrogate unknown field turns the strict 400 into an uncaught response-encoding failure (Low, confirmed)

The strict unknown-field path sorts and joins the request's JSON object keys
directly into `AssessmentError.detail`
(`app/services/assessments.py:226-234`). JSON permits an escaped lone surrogate
such as `"\uD800"` to reach Python as a `str`, even though that string cannot
be encoded as UTF-8. The known value fields all pass through `_utf8_len()` or
emit input-independent details, but the unknown key bypasses that guard.

The route catches the intended `unknown-field` error and hands its detail to
Starlette's `JSONResponse` (`app/main.py:1736-1744`,
`app/main.py:1804-1809`). `JSONResponse.render()` serializes with
`ensure_ascii=False` and then encodes as UTF-8, which raises
`UnicodeEncodeError` for the surrogate. A focused invented probe reproduced
the complete local path: `_clean_submission()` produced
`unknown-field / 'unknown fields: \ud800'`, and `_assessment_refusal()` then
raised `UnicodeEncodeError: surrogates not allowed`. No row is written, but a
malformed request becomes an application exception/500 instead of the
documented controlled 400.

This is Low under the supported same-machine, direct-loopback deployment: it
is a per-request availability/log-noise defect with no authority bypass or
data loss. On any wider unauthenticated listener it would be a trivial remote
exception primitive, reinforcing the existing wider-deployment prohibition.
Make refusal details UTF-8-safe at the assessment response boundary while
preserving ordinary Unicode, and add a raw-body regression that proves both
aliases return a controlled `unknown-field` response for an escaped surrogate
key.

No Critical, High, Medium, or Info finding was found.

## Confirmed protections at the starting tree

- Both aliases remain behind the central trusted-Host and unsafe-method origin
  middleware. `Origin: null` and cross-origin browsers are refused before the
  handler; the origin-less lesson-agent/owner path is the documented
  direct-loopback allowance, not authentication.
- `Content-Length` is an early check and the shared streaming reader is the
  body-size authority. Dishonest multi-chunk bodies stop once the 64 KiB cap is
  crossed, before JSON parsing or database work.
- The client supplies no lesson, question, sitting, sequence, or event
  authority. Lesson identity comes from SQLite, `question_id` comes from a
  same-lesson immutable attempt, `supersedes` must already exist in the same
  lesson, `seq` is the inserted rowid, and `sitting_id` remains server-owned
  and `NULL` in this slice.
- Replay uses an indexed `(lesson_id, idempotency_key)` lookup and the complete
  canonical validated submission fingerprint. Same-key conflicts and late
  duplicates cannot create a second row and refund any speculative rate slot.
- The binding archive check runs after `BEGIN IMMEDIATE`, so archive and
  assessment writes have a definitive order. The assessment row and
  `lesson_assessment` event commit or roll back together, with no filesystem
  work inside the transaction.
- Per-kind vocabularies and required/null conjuncts are enforced by both the
  service and schema. The active-state deactivation lookup is same-lesson and
  uses `idx_assessments_lesson_supersedes`; the fold is by rowid rather than
  timestamp.
- Admission reads no manifest and adds no iframe capability. Legacy or
  rejected-manifest lessons can retain tutor memory as designed, while the
  attempt writer, bridge ABI, terminal, sandbox, runner, templates, and bundle
  spec are unchanged.
- Assessment notes remain private-instance SQLite/event data. They are
  exportable as an audit feed but are not added to public fixtures or search;
  restore does not fabricate the typed table, so authoritative recovery
  remains a consistent private SQLite backup.

## Initial verification and verdict

- Reachability/tree proof — all listed commits are ancestors of starting
  `main`; `5976697^{tree}` equals `491f0e4^{tree}`.
- `git diff --check 5976697^1..5976697` — passed.
- Starting-head approved host `python verify.py` — **797 passed, 0 failed**.
- Starting-head approved host `python verify_restore.py` — **28 passed,
  0 failed**.
- Focused surrogate-key probe — confirmed L1 before any database write.

**NOT YET SAFE TO MAKE LIVE for the new assessment route.** The existing live
process remains unchanged until the owner restarts it, but L1 leaves a
documented strict refusal as an uncaught application exception. The queue
entry stays Pending until L1 is fixed and the exact closing tree is
re-verified. Wider, proxy-adjacent, or multi-user deployment remains **NO**
independently.

## CLOSING ADDENDUM — fix commit `9f177b6` (cycle 1 of 10)

### L1 — resolved

The assessment refusal boundary now converts only UTF-8-unencodable code
points in `detail` to their explicit backslash form before constructing the
JSON response. Ordinary Unicode and every existing refusal code/detail remain
unchanged. An escaped lone-surrogate unknown key therefore returns the
intended HTTP 400 with `error: "unknown-field"` and a printable
`unknown fields: \ud800` detail instead of raising during response rendering.

The verifier sends that raw body through both the numeric-id and by-slug
routes, compares their complete response objects, pins the status/code/detail,
and proves neither request writes a row. Fresh review found no new Critical,
High, Medium, Low, Info, or other finding: body admission, validation order,
database work, idempotency, and successful responses are untouched, and the
sanitization is confined to the already-refused response path.

Cycle 1 validation:

- `git diff --check` — passed.
- Focused pre-fix reproduction — `AssessmentError("unknown-field")` followed
  by `UnicodeEncodeError: surrogates not allowed`; the same composition after
  the fix produced the controlled 400 JSON body.
- Approved host `python verify.py` — **798 passed, 0 failed**, including the
  new two-alias raw-body regression.
- Approved host `python verify_restore.py` — **28 passed, 0 failed**.
- `python scripts/check_public_hygiene.py` — passed. Ignored-status inspection
  showed only established local tool, virtual-environment, dependency, cache,
  screenshot-reference, and review-work paths.

## Prior-condition reconciliation at the closing application tree

- **Central write-guard W1 — REMAINS RESOLVED.** Both assessment aliases stay
  behind the same middleware. The origin-less non-browser path and direct/no-
  forwarded-header condition remain **OPEN/ACCEPTED only for the documented
  direct-loopback deployment**; no Host, Origin, proxy, or listener behavior
  changed.
- **Attempt A1/A2 and attempt authority — REMAIN RESOLVED.** The shared capped
  reader, row/event atomicity, record-time authority, replay/conflict behavior,
  bounded projection, cursor/seal checks, and private cross-process locking are
  unchanged. The assessment route adds no attempt or projection write.
- **Bridge-runtime D5 L1 — REMAINS MITIGATED; D5 L2/L3 — REMAIN RESOLVED.**
  No iframe, bridge, page-byte, identity, or operation-admission path changed.
- **Artifact, runner, run-API, generated-pedagogy, bundle, and lesson-role
  protections — REMAIN RESOLVED.** The same-user final artifact-publication
  window remains **OPEN/ACCEPTED by design**. The assessment writer reads no
  manifest, artifact, runner state, or public bundle file.
- **Terminal, workspace, sandbox, and platform-support protections — REMAIN
  RESOLVED or unchanged with their prior dispositions.** Terminal-opt-in T1
  remains **OPEN/ACCEPTED only for the deliberately plain owner shell**; the
  trusted lesson-agent's intentional loopback network and credential posture
  is unchanged. No terminal, PTY, WebSocket, sandbox, platform-specific import,
  service command, or live process changed.
- **Public-data and recovery posture — REMAINS RESOLVED/UNCHANGED.** Assessment
  content stays in the configured private instance and audit export; no real
  user data, runtime database, export, screenshot, browser state, or public
  fixture was introduced. Typed recovery still requires a consistent SQLite
  backup.

## Closing verdict

**SAFE TO MAKE LIVE for the documented direct-loopback `127.0.0.1:8765`,
single-worker, unauthenticated single-user deployment, once repository
bookkeeping is complete.** The review found **1 Low, 0 Critical, 0 High,
0 Medium, and 0 Info** findings; L1 was resolved in **1 of 10** cycles, and no
open review finding remains for this queue entry. The assessment authority,
strict request grammar, bounded admission, same-lesson references,
fingerprint idempotency, archive ordering, row/event atomicity, active-state
fold, and honest pending-projection posture are approved. Wider,
proxy-adjacent, or multi-user deployment remains **NO**. The queue entry may
move to Done after the fix commit is recorded and final exact-head verification
passes. A live restart remains owner-only and was not performed.
