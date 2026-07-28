# Lesson record panel — adversarial security review

**Scope:** the owner selected the 2026-07-28 issue-#4 phase-S s4 Pending
entry as the second entry in this batch, separate from the s3 capability/brief
entry because the two changes cover different surfaces. The reviewed
application commits are `a0ae9dd`, `981400a`, `c91d002`, `18d4195`, and branch
head `b8b3e02` on `fix/4-s4-record-panel`. The listed surface is
`app/main.py`, `app/services/assessments.py`, `app/services/attempts.py`,
`app/services/focus.py`, `app/templates/learn.html`, `app/static/style.css`,
`verify.py`, and `docs/reviews/QUEUE.md`, plus the assessment/attempt SQLite
authority and indexes, bundle read model, manifest retirement semantics, Learn
request/template render, Jinja environment, and focus aggregate direct callers.

**Starting HEAD:** `b8b3e02a01021f27e3222c4d02bcd8ed4d9aae3a` on
`fix/4-s4-record-panel`, matching
`origin/fix/4-s4-record-panel`. `git status --short --branch` showed only
`## fix/4-s4-record-panel...origin/fix/4-s4-record-panel`; the starting
worktree was clean.

**Reviewed tree:** every listed s4 commit is an ancestor of the starting HEAD.
The entry is intentionally reviewed before merge: `b8b3e02` is the branch
head and has tree `e9bc828770661422cf86a0e69d40e2ef264547e9`. There is no
merge commit, no synthetic merge or GitHub preview hash was used, and no tree
comparison against a nonexistent merge was claimed. The seven commits after
`origin/main` are the five application/review-round commits plus the two queue
bookkeeping commits; no listed entry is out of scope.

**Report file:** `docs/reviews/2026-07-28-lesson-record-panel-review.md`,
derived from the entry's lesson-record-panel subject.

**Prior reports to reconcile:** every existing
`docs/reviews/*-review.md` closing verdict was scanned. The binding conditions
for this surface are:

- `2026-07-28-lesson-assessment-authority-review.md`: central write-guard W1,
  strict bounded admission, same-lesson references, fingerprint idempotency,
  archive ordering, row/event atomicity, and the active-state fold are
  resolved. Origin-less non-browser writes remain accepted only inside the
  documented direct-loopback posture.
- `2026-07-28-lesson-assessments-projection-review.md`: active assessment
  state is a compaction without a fixed cardinality ceiling; its publication,
  cache-identity, replay, and terminal-open cost findings are resolved. The
  same-user bundle-publication window remains accepted by design.
- `2026-07-28-lesson-assessment-capability-brief-review.md`: the s3 L1
  whole-file-read finding is resolved by `b41b632` / merge `52329cf`; token
  provenance, terminal lifecycle, sitting derivation, summary enforcement,
  proxy bypass, and bounded, honest tutor consumption remain resolved.
- `2026-07-20-attempt-backend-review.md`,
  `2026-07-23-attempt-body-stream-cap-review.md`, and
  `2026-07-23-attempts-projection-cost-review.md`: attempt A1/A2, streaming
  admission, row/event atomicity, record-time authority, idempotency, and the
  private projection cost guards remain resolved.
- The bundle, bridge, artifact, runner, template, terminal, sandbox, platform,
  public-data, and recovery reports: degraded/rejected manifest behavior,
  pure GET reads, text-only parent/child boundaries, server-owned identities,
  and fail-closed isolation remain resolved. Bridge-runtime D5 L1 remains
  mitigated and D5 L2/L3 remain resolved. Terminal-opt-in T1 remains
  open/accepted only for the deliberately plain owner shell.

The closing verdict states explicitly whether these conditions remain
resolved, mitigated, open/accepted, or unchanged for the reviewed code.

**Validation baseline:** approved host runs at the clean starting HEAD passed:
`python verify.py` — **870 passed, 0 failed**; `python verify_restore.py` —
**28 passed, 0 failed**.

## Context and method

The deployment decision assumes the documented unauthenticated,
direct-loopback `127.0.0.1:8765`, single-user, single-worker app. This review
did not merge or create a merge commit, restart or signal a service, or read or
write the live data directory. Executable checks used invented values and an
unused `/tmp` configuration path that was not created.

The complete branch diff and changed production functions, query fragments,
template, styles, and verifier section were read with their direct callers.
Static review traced selected-lesson resolution; the active assessment fold;
review retraction/supersession ordering; latest-attempt selection; UTF-8 and
embedded-NUL answer excerpts; declared, unvalidated, retired, rejected, and
wrong-typed manifest states; focus totals; local-date conversion; template
autoescaping in text, class, and title contexts; the stale-process template
guard; query indexes; and GET side effects. Queue prose, commit messages, and
existing tests were treated as claims to verify against the branch source.

## Findings (severity-ranked)

### L1 — Explicit `questions: null` retires every attempted question without observing absence (Low, confirmed)

The new document classifier says a missing `questions` key is an authoritative
empty declaration, while a present value of the wrong type is unknown because
the panel cannot observe absence from a list that is not there
(`app/main.py:1099-1114`). Its implementation uses
`raw.get("questions")`, however, and returns the empty set whenever that result
is `None` (`app/main.py:1116-1123`). That conflates two distinct documents:
one with no `questions` key and one with an explicit JSON null.

The bundle contract makes the distinction binding. `questions` is an optional
list (`docs/learn-bundle-spec.md:141-159`), and a field whose JSON type
contradicts the schema is treated as absent with a degraded `type-mismatch`
finding (`docs/learn-bundle-spec.md:167-180`). The direct reader has the same
pre-existing null conflation: `_read_questions()` calls `raw.get`, does not
emit `type-mismatch` for `None`, and returns an empty typed list
(`app/services/bundle_schema.py:594-600`).

An invented-data current-code probe supplied a valid v2 manifest carrying
`"questions": null`, one durable attempted question, empty assessment state,
and no focus. The manifest reader returned no finding and no typed questions.
The new panel then returned:

```text
declared_known = true
questions = []
retired = ["q_invented1"]
```

That reaches the visible “No longer declared in the manifest” retired block
through `app/main.py:1186-1227` and
`app/templates/learn.html:18-20,259-265`, even though the document supplied no
list from which absence could be established. The learner and owner are
therefore shown a false lifecycle claim about every historical attempted
question after one schema-type mistake.

The finding is Low: it is a concrete record-correctness failure in the
supported deployment, but it writes nothing, destroys no history, grants no
authority, and the durable attempt/review rows remain intact. Wider
unauthenticated deployment remains prohibited independently.

**Fix direction:** the separate repair PR must distinguish key absence from a
present null or other non-list value. A genuinely absent key may still mean an
authoritative empty declaration; any present non-list value, including null,
must make declaration knowledge unknown so no attempt is labelled retired.
Add a regression that exercises explicit null through the real bundle reader
and rendered panel. The repair should also align `_read_questions()` with the
spec's `type-mismatch` rule for explicit null rather than preserving the
underlying silent reader discrepancy.

No Critical, High, Medium, Info, or other finding was found.

## Confirmed protections and rebutted candidates

- The panel is a pure GET-side composition. It adds no route, write, event,
  projection, reconciliation, terminal, sandbox, bridge, or service-lifecycle
  path. `read_bundle_readonly()` does not create a missing directory, manifest,
  skeleton, artifact root, or projection.
- Assessment display state comes from the existing indexed active-row query
  and one shared pure fold. Superseded rows and retracted targets do not
  reappear; a retraction carries no state of its own; only active evidence,
  the latest active review per attempt, and the latest active summary render.
  The earlier-review marker excludes struck reviews on either side of the
  displayed verdict.
- Attempt totals are scoped by lesson. The displayed row per durable question
  is selected by maximum authority row id, and SQL selects only the displayed
  metadata plus a bounded answer head. The BLOB-byte bound handles embedded
  NULs and wide UTF-8 without materializing each full 32-KiB answer or hiding
  truncation.
- A question still named by a readable list but dropped from the typed model is
  shown as unread rather than retired. A rejected/non-object manifest and
  ordinary non-list values are treated as declaration-unknown. A moved
  question keeps the attempt's recorded page identity and names the current
  binding separately. L1 is the one missed null spelling.
- Agent and learner strings render through ordinary `{{ }}` expressions under
  Jinja autoescape. There is no `safe` filter, markdown renderer, HTML
  concatenation, URL construction from record text, or new script. The closed
  DB vocabularies that reach CSS class suffixes are CHECK-enforced; labels,
  answers, notes, next actions, concepts, and page ids remain escaped in text
  and title attributes.
- The record context is built only for the selected lesson. A pre-s4 running
  process serving the working-tree template lacks `selected.record`; the
  `is defined` guard omits the complete block rather than partially rendering
  mismatched data.

## Verification

- Branch proof — starting
  `fix/4-s4-record-panel == origin/fix/4-s4-record-panel` at
  `b8b3e02a01021f27e3222c4d02bcd8ed4d9aae3a`.
- Reachability/tree proof — all five application commits are ancestors of
  `b8b3e02`; its reviewed tree is
  `e9bc828770661422cf86a0e69d40e2ef264547e9`. No merge commit exists or was
  created.
- `git diff --check origin/main..b8b3e02` — passed.
- Starting-head approved host `python verify.py` — **870 passed, 0 failed**.
- Starting-head approved host `python verify_restore.py` — **28 passed,
  0 failed**.
- Focused invented-null probe — confirmed that an explicit wrong-typed null is
  silently read as an authoritative empty declaration and moves the durable
  attempted question into `retired`.
- No repair was attempted and no application, test, specification, service, or
  live runtime file was changed by this diagnosis-only drain.

## Prior-condition reconciliation at the reviewed branch head

- **Assessment authority, active fold, and central write-guard W1 — REMAIN
  RESOLVED.** The new panel calls the existing read model and adds no write
  authority. Origin-less non-browser access and direct/no-forwarded-header
  assumptions remain **OPEN/ACCEPTED only for the documented direct-loopback
  deployment**.
- **Assessment projection publication, cache honesty, and replay/terminal-open
  cost guards — REMAIN RESOLVED.** The compaction-without-a-fixed-cap contract
  is unchanged; the panel reads SQLite and never publishes or reconciles the
  file. The same-user publication window remains **UNCHANGED/ACCEPTED**.
- **S3 capability lifecycle, proxy bypass, sitting provenance, summary rule,
  and bounded tutor consumption — REMAIN RESOLVED.** The s3 L1 repair is
  present and reachable. S4 reads the resulting authority but changes none of
  its token, terminal, endpoint, brief, or sandbox paths.
- **Attempt A1/A2, streaming admission, record-time authority, and private
  projection cost — REMAIN RESOLVED.** The write and projection paths are
  untouched. The new summary query reads a bounded excerpt of the newest
  attempt per durable question.
- **Bundle degraded/rejected behavior and retirement truth — L1 IS OPEN for
  explicit null only.** Rejected manifests, non-object documents, ordinary
  wrong-typed values, dropped declarations, moved questions, and pure-read
  behavior retain their intended dispositions. Explicit `questions: null`
  is the one spelling that violates the same rule and prevents approval.
- **Bridge-runtime D5 L1 — REMAINS MITIGATED; D5 L2/L3 — REMAIN RESOLVED.**
  The iframe/CSP, bridge handshake, artifact/editor/run membrane, runner,
  operation-admission, terminal, and sandbox paths are unchanged.
- **Public-data and recovery posture — REMAIN RESOLVED/UNCHANGED.** Only
  invented probe values entered the report. No live lesson, assessment,
  attempt, focus row, export, screenshot, credential, browser state, or
  private-instance path was read or written. Typed recovery still requires a
  consistent SQLite backup.

## Closing verdict

**NOT YET SAFE TO MAKE LIVE for the phase-S s4 lesson-record-panel branch.**
The review found **1 Low, 0 Critical, 0 High, 0 Medium, and 0 Info** findings.
The panel is read-only, bounded where it displays answer bodies, and
autoescaped for the documented direct-loopback `127.0.0.1:8765`,
single-worker, unauthenticated single-user deployment, but L1 presents a
wrong-typed null declaration as proof that every attempted question was
retired.

The queue entry remains **Pending** and points to this report. A separate
ordinary reviewed repair PR must preserve the missing-key behavior, treat
every present non-list value as declaration-unknown, align the reader's null
finding with the bundle contract, and preserve or grow the **870 / 28**
validation baselines. Wider, proxy-adjacent, or multi-user deployment remains
**NO** independently. No merge or live restart was performed; restart remains
the owner's action after the queue gate closes.
