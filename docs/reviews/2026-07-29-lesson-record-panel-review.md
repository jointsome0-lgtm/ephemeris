# Lesson record panel follow-up — adversarial security review

**Scope:** the sole Pending entry in `docs/reviews/QUEUE.md`: issue #4 phase-S
slice s4 on `fix/4-s4-record-panel`, including application commits `a0ae9dd`,
`981400a`, `c91d002`, `18d4195`, `b8b3e02`, repair `5bcd585`, the subsequent
round-5 through round-10 application commits `89f0b77`, `49e5de8`,
`cb42265`, `4d3a8bc`, `cabdd7f`, and current branch tip `75b7452`, together
with their queue-only bookkeeping. The listed surface is `app/main.py`,
`app/services/assessments.py`, `app/services/attempts.py`,
`app/services/focus.py`, `app/templates/learn.html`, `app/static/style.css`,
`verify.py`, and `docs/reviews/QUEUE.md`; direct callers include the lesson
bundle read/selection model, assessment and attempt SQLite authority and
indexes, focus aggregate, Jinja environment, and Learn request/render path.

**Starting HEAD:** `75b74529f5f727d1002b47dea036ebac1ce6821a` on
`fix/4-s4-record-panel`, matching
`origin/fix/4-s4-record-panel`. `git status --short --branch` showed only
`## fix/4-s4-record-panel...origin/fix/4-s4-record-panel`; the starting
worktree was clean.

**Reviewed tree:** every commit named by the entry, including the queue
bookkeeping through `ce2ad38`, repair `5bcd585`, round-6 marker `89f0b77`,
and the current tip, is an ancestor of the starting HEAD. The starting tree is
`46080c1b7f08e8337a5263fcdf6f4dd6bf197bfb`. The branch is 18 commits ahead
of `main` / `origin/main` at `29bbe85`; no entry commit is out of scope, no
merge commit exists yet, and no GitHub preview hash was used.

**Report file:** `docs/reviews/2026-07-29-lesson-record-panel-review.md`,
derived from the entry's lesson-record-panel subject and today's date.

**Prior reports to reconcile:** every existing
`docs/reviews/*-review.md` closing verdict was scanned. The immediately
binding report is `2026-07-28-lesson-record-panel-review.md`; its explicit-null
retirement finding and its requested shared-reader alignment are reconciled
separately below. The inherited conditions it names remain binding:

- assessment authority, central write-guard W1, active-fold semantics, and
  projection publication/cache/replay protections;
- s3 capability lifecycle, sitting provenance, proxy bypass, summary rule,
  and bounded tutor consumption;
- attempt A1/A2, streaming admission, record-time identity, replay, and
  projection-cost protections;
- bundle degraded/rejected behavior, page selection, retirement truth, and
  pure phase-F reads;
- bridge-runtime D5 L1 mitigation, D5 L2/L3 resolution, terminal/sandbox
  isolation, public-data hygiene, and recovery posture.

The closing reconciliation states whether each remains resolved, mitigated,
open/accepted, or unchanged for this tree.

**Validation baseline:** approved host runs at the clean starting HEAD passed:
`python verify.py` — **880 passed, 0 failed**; `python verify_restore.py` —
**28 passed, 0 failed**.

## Context and method

The deployment decision assumes the documented unauthenticated,
direct-loopback `127.0.0.1:8765`, single-user, single-worker app. This review
did not merge or open a PR, restart or signal a service, or read or write the
live data directory. Its executable probe used one invented lesson in a
throwaway `/tmp` data directory.

The complete branch diff and post-drain repair/round diffs were read, followed
by the changed production helpers and their direct callers. Static review
covered manifest degraded/rejected/version/identity states, page-selection
persistence, DB/manifest ordering, SQLite snapshot ownership, active-row
folding and winner hydration, earlier-review aggregates, bounded answer
excerpts, focus totals, template autoescaping in text/class/title contexts,
and the stale-process guard. Tests, queue prose, commit messages, and prior
reports were treated as claims and checked against the current source.

## Findings (severity-ranked)

### L1 — The preliminary manifest read can persist a page the rendered manifest rejects (Low, confirmed)

Round 10 now performs two selected-manifest reads. The first
`with_bundle_info_read()` resolves the requested page
(`app/main.py:980-982`), and `mark_opened()` immediately persists that result
(`app/main.py:983-988`). Only afterward does the route capture the database
snapshot and perform the final manifest read used by both displayed bundle
metadata and the record panel (`app/main.py:989-1002`).

That ordering creates two manifest authorities inside one GET. The
`mark_opened()` contract says its caller supplies an entry resolved against
the bundle read model, making a v2 selection declared by construction
(`app/services/lessons.py:1531-1542`); spec §4.2 likewise requires v2 page
selection writes to accept only declared `pages[].path` values
(`docs/learn-bundle-spec.md:211-217`). A lesson-agent can change the manifest
between the two reads, so the page persisted from the preliminary read need
not be declared by the final read that the response actually renders.

An invented-data current-code probe established the failure at the exact
round-10 ordering seam:

1. A valid v2 lesson declared `related/01-invented-old.html`, and the request
   selected that page.
2. After the preliminary read but before `_record_panel_db_state()`, the probe
   atomically published a valid manifest that no longer declared that page.
3. `GET /learn` returned **200** and rendered the final manifest's
   `index.html` fallback with the stale-selection pointer, while the database
   retained:

```text
current_entry = related/01-invented-old.html
```

The next ordinary GET therefore starts from a stored selection absent from the
manifest and keeps surfacing `invalid-entry` until the owner explicitly picks
a declared page. This is a concrete supported-deployment correctness failure:
one GET persists state contradicted by the manifest version it displays. It is
Low because it grants no authority, loses no assessment or attempt history,
and remains recoverable through the normal page selector.

**Fix direction:** the separate reviewed repair must make the final
post-database manifest read the single authority for bundle metadata, record
classification, and page-selection persistence. Persist only after that read
has resolved the candidate and confirmed it is not stale. Add a deterministic
regression that swaps a valid manifest at this ordering seam and proves
`current_entry` is never set to a page absent from the manifest rendered in
the same response.

No Critical, High, Medium, Info, or other new finding was found.

## Confirmed protections and rebutted candidates

- The 2026-07-28 L1 panel failure is corrected: key presence, not `raw.get`,
  distinguishes a missing `questions` declaration from an explicit null, and
  present non-list values retire nothing.
- Rejected, non-object, v1, and identity-mismatched manifests assert no
  retirement. A dropped-but-still-named question remains unvalidated rather
  than retired; a moved question keeps the attempt's recorded page and names
  the current binding separately.
- Attempt, assessment, and focus reads share one SQLite snapshot. The attempt
  query establishes it before assessment folding/hydration/counts, preventing
  a concurrent assessment commit from mixing database versions inside one
  panel.
- The assessment fold walks only five narrow columns, hydrates full notes only
  for its displayed winners, batches winner ids below SQLite's variable
  ceiling, and limits review winners/count aggregates to attempts the panel
  actually displays. The approved compaction-without-a-fixed-cardinality-cap
  remains explicit rather than being silently truncated.
- The latest attempt per durable question carries only the displayed columns
  and a byte-bounded answer head. Embedded NUL and wide UTF-8 answers neither
  hide truncation nor force whole-answer materialization.
- Agent- and learner-authored labels, ids, answers, notes, next actions, and
  concept refs still render through ordinary autoescaped Jinja expressions.
  No `safe` filter, markdown, string-built HTML, URL construction, or new
  script reaches the parent page. Class suffixes remain closed DB
  vocabularies.
- The requested `focus_sessions.lesson_id` index remains rebutted under the
  repository's concrete-failure rule: this review established no current
  failure from the one aggregate per selected lesson and did not turn a
  hypothetical growth optimization into a finding.

## Verification

- Branch proof — starting
  `fix/4-s4-record-panel == origin/fix/4-s4-record-panel` at
  `75b74529f5f727d1002b47dea036ebac1ce6821a`.
- Reachability/tree proof — all entry commits are ancestors of the starting
  HEAD; its reviewed tree is
  `46080c1b7f08e8337a5263fcdf6f4dd6bf197bfb`.
- `git diff --check main...HEAD` — passed.
- Starting-head approved host `python verify.py` — **880 passed, 0 failed**.
- Starting-head approved host `python verify_restore.py` — **28 passed,
  0 failed**.
- Invented two-manifest selection probe — returned 200, rendered the final
  fallback, and confirmed that the preliminary-only page remained persisted.
- No application, test, specification, service, or live runtime file was
  changed by this diagnosis-only drain.

## Prior-condition reconciliation at the reviewed branch head

- **Prior record-panel L1 false retirement — RESOLVED.** Explicit
  `questions: null` and every other present non-list value now make
  declaration knowledge unavailable; a genuinely missing key remains the
  authoritative empty declaration.
- **The associated shared bundle-reader null diagnostic — STILL OPEN outside
  this panel.** `_read_questions()` still treats explicit null like absence
  and emits no spec-§4 `type-mismatch`. The branch explicitly declined that
  shared semantic change; the panel mitigates it by inspecting key presence
  itself. This does not reopen the resolved false-retirement failure, but the
  reader-level contract deviation is not claimed resolved here.
- **Page-selection write authority — REGRESSED by L1.** The final manifest
  read now consistently supplies bundle metadata and record classification,
  but `mark_opened()` still uses the preliminary read and can persist a page
  contradicted by that final version.
- **Assessment authority, active fold, central write-guard W1, projection
  publication/cache/replay guards, and s3 capability/session provenance —
  REMAIN RESOLVED.** Origin-less non-browser access and direct/no-forwarded-
  header assumptions remain **OPEN/ACCEPTED only for the documented
  direct-loopback deployment**.
- **Attempt A1/A2, streaming admission, record-time identity, and private
  projection cost — REMAIN RESOLVED.** The write and projection paths are
  unchanged; the panel reads one bounded latest-attempt excerpt per durable
  question.
- **S3 bounded tutor consumption — REMAINS RESOLVED.** The assessment
  projection stays an uncapped compaction, while its generated consumer
  guidance retains the 2 MiB bound and honest omission rule.
- **Bridge-runtime D5 L1 — REMAINS MITIGATED; D5 L2/L3 — REMAIN RESOLVED.**
  Iframe/CSP, bridge identity, artifact/editor/run authority, terminal,
  sandbox, and runner paths are unchanged.
- **Public-data and recovery posture — REMAIN RESOLVED/UNCHANGED.** Only
  invented throwaway data was used. No real lesson, assessment, attempt,
  focus row, export, screenshot, credential, browser state, or private
  instance path was read or written. Typed recovery still requires a
  consistent SQLite backup.

## Closing verdict

**NOT YET SAFE TO MAKE LIVE for the phase-S s4 lesson-record-panel branch.**
This follow-up review found **1 Low, 0 Critical, 0 High, 0 Medium, and 0 Info**
new findings. The prior false-retirement Low is resolved, database reads are
snapshot-consistent, displayed bodies and SQL variable counts are bounded,
and parent-page output remains autoescaped; however, L1 lets one GET persist a
page selection contradicted by the final manifest version it renders.

The queue entry remains **Pending** and points to this report. A separate
ordinary reviewed repair PR must make page-selection persistence share the
same final manifest authority as bundle metadata and the record, carry the
deterministic manifest-swap regression, and preserve or grow the **880 / 28**
validation baselines. The separate shared-reader null diagnostic remains an
explicitly open bundle-contract follow-up rather than a claim of s4 approval.
Wider, proxy-adjacent, or multi-user deployment remains **NO** independently.
No merge or live restart was performed; restart remains the owner's action
after the queue gate closes.
