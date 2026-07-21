# Check activation — adversarial security review

**Scope:** the single Pending queue entry dated 2026-07-21, covering issue #36
session D5 and its listed code commits (`3931339`, `4a019be`, `165481c`,
`1467750`, `ef533d9`, `43c4b1d`, `c357bc5`) as landed by merge commit
`4a1d95f`. The landed tree and the approved PR #60 branch-head tree at
`68b9686` were independently checked and are both `5cd5c80`. Review references
in the initial section below name the starting tree at `9b80bab`.

The complete D5 diff, every listed path, the full TypeScript source and
committed JavaScript emit, the lesson page readers and digest cache, Learn
template and routes, attempt service, security middleware, ABI/API contracts,
verifier additions, and direct callers were read. The earlier bridge-runtime,
attempt-backend, generated-brief, CSP, bundle-schema, and central-write-guard
reviews were reconciled against the landed behavior.

**Context:** v0 has no authentication. This verdict assumes the documented
service shape: bound directly to `127.0.0.1:8765`, one worker, and one trusted
local user. Lesson pages remain untrusted opaque-origin iframe content. D5 is
the client half of the state-changing attempt capability; the D4 endpoint is
the server half and remains the final authority at record time.

**Method:** treated verifier checks, queue summaries, and the prior PR-bot
approval as claims rather than review evidence. Static tracing covered page
snapshot identity, versioned navigation, document transitions, capability
granting, per-operation metadata and manifest validation, settle/teardown
ordering, server-side revalidation, idempotency, page-size work bounds, and
cache concurrency. A deterministic invented-data concurrency probe exercised
the digest-cache admission policy.

**Initial summary:** no Critical, High, or Medium finding. One Low availability
finding is confirmed: simultaneous cold page-digest misses can violate the
nominal 64-entry cache bound. The per-page 4 MiB identity limit and the
served-byte binding remain effective, so this is not an identity-confusion or
write-authority bypass. The queue remains Pending until the finding is fixed
and the exact resulting tree is re-verified.

## Findings (severity-ranked)

### C1 — concurrent misses can exceed the page-digest cache bound (Low, confirmed)

At the starting tree, `_cache_page_digest()` checks the cache length, evicts,
and inserts through separate unsynchronized operations
(`app/services/lessons.py:215-227`). Multiple cold requests for distinct pages
can all observe a cache just below `_PAGE_DIGEST_CACHE_MAX`, all skip eviction,
and then all insert. Once the cache is over the bound, later ordinary
evict-and-insert operations preserve the excess rather than converging it back
to the configured maximum. Both preview metadata and page serving reach this
helper through the descriptor-bound hashing path.

An invented probe preloaded 63 entries, synchronized 12 unique cold misses at
the length check, and completed with 75 entries and no thread errors
(`configured_max=64`, `actual_entries=75`). The race-tolerant `pop()` added in
PR-bot round 7 prevents an exception, but it does not make the admission and
eviction policy atomic.

Impact is Low in the documented loopback/single-user deployment: each admitted
entry is small, each identity-producing read remains capped at 4 MiB, and an
attacker needs concurrent cold misses across distinct declared pages. This is
nevertheless an availability-policy defect and weakens the bridge-runtime
report's L3 mitigation; it would be a more direct unauthenticated resource
primitive on a wider listener. Serialize cache lookup and admission/eviction,
then add a deterministic concurrent-miss regression that fails on this tree.

## Earlier findings and gates

- **Bridge-runtime L1 is mitigated, not fully resolved.** D5 does not create a
  browser primitive stronger than the navigation-stable `WindowProxy`, so the
  document-generation ambiguity remains in principle. For every attempt,
  however, the parent waits through the 250 ms settle window, requires the
  same still-armed document state, fetches fresh metadata, compares the full
  page identity and declared-question set, and only then calls D4. Teardown
  invalidates the document-local in-flight set. D4 independently re-reads the
  record-time manifest and page binding before writing. A confusing successor
  can therefore request only a question currently authorized for the already
  selected lesson page; it cannot choose lesson, page identity, page revision,
  or undeclared question authority. The residual remains Low and is acceptable
  only inside the direct-loopback, same-trust deployment.
- **Bridge-runtime L2 is resolved for the landed D5 capability path.** Bridge
  pages are navigated with the content-bound `?v` token from the initial
  server render onward. Declared v2 pages are hashed and served from one
  descriptor snapshot, and the route returns 409 instead of serving bytes
  whose token mismatches. Metadata carries the full page digest, the parent
  compares it per operation, and D4 revalidates current bytes at record time.
- **Bridge-runtime L3 is mitigated but C1 remains open initially.** Identity
  work is capped at 4 MiB per page; oversized pages lose bridge identity and
  remain display-only. The digest cache uses an eviction policy rather than an
  unbounded value-size read, but C1 shows that its entry-count bound is not
  concurrency-safe at the starting tree.
- **Attempt-backend A1 and A2 remain accepted follow-ups, not D5 regressions.**
  Issue #59 covers request-body admission before full buffering; issue #58
  covers lifetime-linear projection reconciliation under the SQLite writer
  lock. D5 adds neither server behavior and does not make either acceptable for
  a wider unauthenticated binding.
- **Earlier D3/CSP/bundle findings remain resolved.** The child authenticates
  the parent handshake, request ids remain lesson-wide idempotency material,
  answers stay untrusted data, interactive CSP/profile selection fails closed,
  manifest parsing remains bounded, and preview/resource allowlists continue
  to exclude private attempt projections and artifact roots.

## Confirmed protections

- The child supplies only `question_id`, answer, and request id. The parent
  derives all lesson/page identity from its armed binding and obtains the
  attempts URL from the trusted server-rendered template.
- Capability grant requires both child opt-in and an available attempts URL;
  every call rechecks fresh metadata identity, version, and the manifest's
  declared questions before any HTTP write.
- The 250 ms settle delay is followed by a state/document check, and teardown
  replaces the document-local in-flight set. A completing navigation therefore
  cancels rather than releasing the pending write.
- D4 remains authoritative: idempotency, origin/Host middleware, body and value
  grammar, rate policy, current manifest/question validation, and current-page
  staleness are enforced server-side. Port possession is not trusted as write
  authority.
- Exact response bytes, digest, and stat for bridge-eligible declared pages
  come from one no-follow descriptor. Version mismatch fails closed with 409;
  oversized pages are not granted bridge identity.
- No listening, authentication, terminal, live-data, service, or deployment
  configuration changed in D5.

## Initial verification

- Branch/start check — `main` was clean, `HEAD` and `origin/main` were both
  `9b80bab`, and that head descends from the caller-specified starting commit.
- Merge proof — `4a1d95f` has approved branch head `68b9686` as its second
  parent; both commits have tree `5cd5c80`. No GitHub preview hash is used.
- `git diff --check` over the landed D5 code range — passed.
- TypeScript emit — the committed JavaScript matched a fresh compiler emit as
  exercised by the baseline verifier.
- Concurrent cache probe — confirmed C1: 63 initial plus 12 synchronized
  unique misses produced 75 entries for a configured maximum of 64.
- `python -u verify.py` — **598 passed, 0 failed** at `9b80bab`. An earlier
  sandboxed run was interrupted at the known TestClient execution-environment
  boundary without emitting a failure; the complete baseline was then run in
  the normal app environment.

## Initial deploy verdict

**Not yet: the current D5 tree is functionally safe for the documented
direct-loopback, single-worker deployment, but C1 must be closed before this
queue drain removes the restart gate. Wider deployment remains NO — v0 is
unauthenticated, and accepted A1/A2 plus the L1 residual materially depend on
the narrow deployment posture.**

## CLOSING ADDENDUM — fix commit `edd0cf7` (cycle 1 of 10)

**Re-review scope:** the C1 repair in `edd0cf7`, its deterministic regression,
and the resulting D5 call paths. No new security-severity finding was found.

### C1 — resolved

`_PAGE_DIGEST_CACHE_LOCK` now covers both coherent lookup and the complete
admission/eviction/update decision. A new path cannot observe a stale cache
length while another path is being admitted. Eviction loops until the cache is
below the configured maximum, so even an inherited over-limit map converges
before the new entry is inserted; a disabled/non-positive maximum stores
nothing. File reads and SHA-256 work remain outside the critical section, so
the repair does not serialize the bounded 4 MiB page hashing itself.

The regression starts with 63 invented entries and releases 12 distinct cold
misses together. Its instrumented length operation makes the old implementation
finish at 75 entries; at `edd0cf7` it completes without worker errors at
exactly 64. This closes the sole finding raised by this drain.

## Gate reconciliation at closing head

- **L1 — MITIGATED.** The underlying same-`WindowProxy`
  document-generation ambiguity is not fully resolved. D5's 250 ms settle,
  post-settle armed-document check, fresh per-operation identity/question
  validation, document-local in-flight teardown, and D4 record-time authority
  checks reduce it to the previously described Low residual. It is acceptable
  for the documented direct-loopback, same-trust deployment and remains a
  reason not to widen the listener.
- **L2 — RESOLVED.** Versioned iframe navigation, one-descriptor response
  snapshots, mismatch-409 behavior, full-digest metadata comparison, and D4's
  current-byte revalidation bind the capability path to the bytes served and
  fail closed on drift.
- **L3 — RESOLVED.** Identity reads stop at 4 MiB, oversized pages are denied
  bridge identity, stable-page polling uses the digest cache, and `edd0cf7`
  makes the 64-entry admission/eviction bound hold under concurrent misses.

A1 (issue #59) and A2 (issue #58) remain the attempt-backend report's accepted
Low follow-ups. They are unchanged by D5 and do not block the documented
deployment, but they continue to weigh against any wider unauthenticated
exposure.

## Closing verification

- `git diff --check` — passed.
- `python -u verify.py` at `edd0cf7` — **599 passed, 0 failed** (the new C1
  regression accounts for the increase from the 598-check baseline).
- `python -u verify_restore.py` at `edd0cf7` — **28 passed, 0 failed**.
- `python scripts/check_public_hygiene.py` — passed.
- `git status --short --ignored` — reviewed; only the intended source/test and
  report changes were present before the cycle commit, alongside the
  repository's existing ignored local tool/dependency directories. No runtime
  data, exports, screenshots, credentials, or local agent state are tracked.

## Closing verdict

**SAFE TO MAKE LIVE for the documented direct-loopback
(`127.0.0.1:8765`), single-worker, unauthenticated single-user deployment. One
Low finding was raised and fixed in cycle 1; no Critical, High, Medium, or open
Low finding remains. L1 is mitigated, L2 is resolved, and L3 is resolved for
the landed D5 capability code. Wider deployment remains NO — v0 is
unauthenticated, L1 retains a same-trust residual, and A1/A2 remain accepted
availability follow-ups. The queue entry may move to Done; restarting the live
service remains the owner's action and was not performed by this review.**
