# Lesson attempt backend — adversarial security review

**Scope:** the Pending queue entry dated 2026-07-20 and the exact checked-out
tree at `83cc652` on branch `fix/36-d4-attempt-backend`. The review covered the
listed D4 commits from `c2bf554` through `0edef9e`, the final queue-description
commit, and the resulting diff from parent `2f0ed2b`. The full attempt service,
the changed DB schema, route, bundle-reader, lesson-service, contract, and D4
verifier sections were read, together with their security middleware, event,
manifest, preview-serving, hashing, and projection-write callees. Earlier
reports for the same bundle, CSP, bridge, generated-brief, migration, and
central-write-guard surfaces were re-checked.

Per the caller's explicit instruction, this report does not edit
`docs/reviews/QUEUE.md`; queue bookkeeping remains a separate maintainer step.

**Context:** v0 has no authentication. The deployment decision below assumes
the service is bound directly to loopback and uses the single-worker systemd
shape documented by the repository. Runtime lesson data, attempt answers, the
SQLite authority, and `attempts.jsonl` are private-instance data. ABI v1 still
grants an empty capability set and handles ping/pong only; D4 adds the HTTP
backend but does not yet connect an untrusted lesson document to it.

**Method:** treated the committed verifier and the PR-round descriptions as
claims rather than evidence. Static tracing covered request framing and origin
admission, JSON/UTF-8 grammar, rate-window and idempotency races, transaction
boundaries, cross-process ordering, projection failure modes, filesystem node
substitution, answer propagation, record-time manifest/page validation, and
the prior D2 bridge gates. Invented temporary probes exercised mismatched ASGI
body framing and a growing projection while a second SQLite writer contended
for the database.

**Summary verdict:** no Critical, High, or Medium finding under the documented
direct-loopback posture. Two Low availability findings remain. First, the
application's body cap is checked against delivered bytes only after Starlette
has buffered the whole body, making the cap dependent on the HTTP parser's
framing enforcement. Second, every projection append performs work linear in
the lesson's complete immutable attempt history while holding a database-wide
SQLite writer lock. The authority/event transaction, idempotency behavior,
record-time manifest validation, projection integrity repairs, and prior
bundle-data protections otherwise hold.

## Findings (severity-ranked)

### A1 — The body cap is enforced after full buffering and depends on HTTP-parser framing (Low, confirmed)

The handler rejects a declared `Content-Length` above 256 KiB before reading,
but for an admitted header it calls `await request.body()` and checks the
delivered length only afterwards (`app/main.py:1328-1344`). Starlette's
`Request.body()` collects every stream chunk into a list and joins it before
returning. The post-read comparison therefore limits parsing, not admission or
memory consumption.

An invented ASGI probe declared one byte, delivered 2 MiB in 32 chunks, and
confirmed that the handler consumed all 2 MiB before returning 413. The locked
default `httptools` 0.8.0 parser rejects an HTTP/1.1 request carrying both
`Content-Length` and `Transfer-Encoding`, which materially narrows the finding
for the documented current Uvicorn deployment. However, the equally installed
h11 0.16.0 parser accepts that framing, preserves the one-byte Content-Length,
and delivers the chunked body. The same application behavior is reachable if
Uvicorn falls back to or is explicitly run with h11, or if another ASGI front
end supplies a mismatched body.

Under direct loopback this requires a same-machine client and remains Low. On a
wider unauthenticated binding, concurrent oversized streams can consume service
memory before any 413, origin-less clients pass the deliberate non-browser
write policy, and the per-lesson rate limiter is not reached until after body
buffering and JSON parsing. Enforce the delivered-byte limit while iterating
`request.stream()` (and reject negative declared lengths), stopping once the
cap is crossed; keep the early Content-Length rejection as an optimization,
not the security boundary. Add a multi-chunk mismatched-length ASGI regression
case independent of the selected HTTP parser.

### A2 — Projection append is linear in lifetime history under a database-wide writer lock (Low, confirmed)

After the authoritative row and event commit, `_project_attempt()` opens a
separate `BEGIN IMMEDIATE` transaction (`app/services/attempts.py:281-294`,
`342-361`). While that SQLite writer lock is held, the fast path fetches every
authority row, serializes every earlier row into one in-memory prefix, opens the
projection, and rereads the entire file to compare it byte-for-byte
(`app/services/attempts.py:364-402`). Any mismatch invokes a rebuild that again
serializes every row before atomically replacing the file
(`app/services/attempts.py:297-323`, `427-433`). The authority is immutable and
has no retention bound, while one answer may contribute 32 KiB to both the DB
and projection (`app/services/attempts.py:45-46`). Thus even the successful
"append" path is O(total historical answer bytes), not O(new line bytes).

An invented 200-row authority with 32 KiB answers produced a 6,601,228-byte
expected prefix; one nominal append regenerated and reread all 6,601,228 bytes.
A second connection attempting an unrelated `BEGIN IMMEDIATE` during that read
received `OperationalError: database is locked`, confirming that the work
blocks writers for every app surface, not only this lesson. At larger histories
the duplicate row objects, rendered prefix, on-disk read, and possible rebuild
can also approach the systemd service's memory limit; slow `fsync` or
replacement keeps the same global lock held.

The 20-per-minute per-process window slows growth but does not bound it, and it
is explicitly an abuse damper rather than a security boundary. A loopback
client can accumulate the state persistently, and the cost survives rate-window
resets and service restarts. This remains Low in the documented single-user
loopback model, but becomes a direct unauthenticated availability primitive on
any wider binding. Give the fast path bounded/constant work (for example, a
durable authority cursor or digest), bound reconciliation memory, and isolate
per-bundle projection serialization from SQLite's global writer lock without
reintroducing the stale-rebuild race fixed in round 10. Add a growth test that
asserts one append does not reread/re-render all prior answers, plus a
concurrent unrelated-writer test.

## Earlier findings and gates

- **D2 L1 remains Low and is still a D5 blocker.** The bridge runtime retains
  the documented `WindowProxy` document-generation blind windows. That is safe
  for the current empty-capability ping-only port, but a successor document
  that receives the expected identity could submit a declared question once an
  attempts operation is added. D4's server checks do not authenticate which
  document held the port; they correctly treat port possession as insufficient
  and validate data, but cannot resolve the browser-generation ambiguity.
- **D2 L2 remains Low and is still a D5 blocker.** Metadata hashes one page
  descriptor, while the normal route later lets `FileResponse` reopen the path
  (`app/main.py:1217-1237`); the placeholder path likewise hashes and reads by
  separate opens. D4 re-hashes the current disk path
  (`app/services/attempts.py:218-238`, `app/services/lessons.py:457-468`), so it
  can conservatively detect current drift, but it cannot prove that the
  submitted `page_rev` described the exact bytes the learner received. A
  `stale: false` result therefore must not be treated as closing the earlier
  served-byte binding condition.
- **D2 L3 remains Low, not regressed.** Stable page bytes use the existing
  inode/ctime-aware digest cache; a cold or invalidated page still has no size
  bound. D4 adds one record-time hash for a new, currently bound submission but
  does not change the cache or polling behavior.
- **The D3 generated-brief findings remain resolved.** The brief authenticates
  the parent response, requires lesson-wide fresh request ids for changed
  submissions, and explicitly classifies answers and `attempts.jsonl` as
  untrusted data rather than agent instructions.
- **The earlier bundle/runtime findings remain resolved.** Manifest parsing is
  bounded and total; identity/profile failures deny attempts; v2 preview files
  remain a positive allowlist that excludes `attempts.jsonl` and artifact
  roots; manifest/page symlinks fail closed; reserved paths stay blocked; and
  the nine changed identity/value grammars use `\Z`, so trailing newlines do
  not enter rows or projections.

These inherited Lows are not introduced by D4, and ABI v1 still exposes no
attempt operation (`app/static/src/learn-bridge.ts:24-25`, `281-332`). They do,
however, preserve the earlier explicit gate: adding the D5 capability remains
unsafe until L1/L2 are resolved and browser-tested with the server-side checks
retained.

## Confirmed protections

- **Authority atomicity holds.** `append_event()` and the `lesson_attempts`
  insert share one `with conn` transaction; the row stores the committed
  event UUID. A projection error occurs only after this authority commits and
  cannot roll it back.
- **Idempotency races converge.** The per-lesson unique constraint is backed by
  early and locked replay checks; refusals and rate-limit races re-check the
  durable key, cross-process `IntegrityError` resolves through the authority,
  and replay/conflict outcomes do not consume rate budget.
- **Record-time inputs fail closed where required.** Lesson UID comes from the
  DB, rejected/mismatched/non-interactive manifests refuse new writes,
  undeclared questions return the distinct 422, and page binding/current bytes
  determine `stale` server-side. Submitted identity is comparison data, not an
  authority source.
- **Projection corruption does not become authority corruption.** The fast
  path uses `O_NOFOLLOW`, regular-file and single-link checks, complete write
  loops, `fsync`, and byte-exact authority-prefix verification. Missing, torn,
  reordered, forged, special-file, hard-link, and directory-collision states
  fall back to an atomic authority rebuild; cross-process projection sections
  serialize through SQLite. A projection failure returns `pending` after the
  durable write.
- **The HTTP perimeter is unchanged.** The central trusted-Host and
  unsafe-method origin middleware still rejects cross-origin and
  `Origin: null` writes. The deliberate origin-less non-browser path and
  no-auth posture remain reasons not to widen the listener, not hidden
  assumptions.
- **Private answers are not added to public preview routes.** Answers live in
  the private DB/event stream and app-owned projection. The generic v2 file
  route still refuses the reserved `attempts.jsonl` name and learner artifact
  roots; success and refusal responses use `Cache-Control: no-store`.

## Verification

- Commit/tree check — branch and head matched the requested
  `fix/36-d4-attempt-backend` / `83cc652`; the application-code head is
  `0edef9e` and the final head delta is queue documentation.
- `git diff --check 2f0ed2b..83cc652` — passed.
- Invented projection-growth probe — confirmed A2: 200 rows, 6,601,228 bytes
  regenerated and reread for one append; a concurrent unrelated writer failed
  with `database is locked`; projection completed correctly after release.
- Invented ASGI/body-parser probes — confirmed A1: all 2 MiB were delivered
  before the 413 despite declared length 1; h11 0.16.0 admitted CL+TE and
  delivered the chunked body, while pinned `httptools` 0.8.0 rejected the same
  framing.
- `python -u verify.py` — the two terminal-wiring subprocess checks passed,
  then the run stalled at the previously documented TestClient startup boundary
  in this execution environment and was interrupted; no failing assertion was
  emitted. This review therefore does not independently claim the caller's
  reported 579-check green result at this exact head.

## Deploy verdict

**Current D4 HTTP backend on the documented direct-loopback, single-worker
deployment: YES, with A1 and A2 as Low follow-ups. Future D5 capability-bearing
bridge: NO until the bridge-runtime report's remaining L1 document-generation
and L2 served-byte conditions are resolved and browser-tested; retain D4's
per-operation validation. Wider deployment: NO — v0 is unauthenticated, and
A1/A2 provide concrete availability reasons not to widen the listener.**
