# Lesson assessments projection — adversarial security review

**Scope:** exactly one Pending entry was present at review start: the
2026-07-28 entry for `a05d5fc`, `f03ae5d`, `49ae440`, and `d18f755` on
`fix/4-s2-assessments-projection`, landed by ordinary merge commit `876b879`
(PR #88). The listed surface is `app/services/assessments.py`,
`app/services/lessons.py`, `app/services/bundle_schema.py`,
`docs/learn-bundle-spec.md`, `docs/lesson-assessments-api.md`, `verify.py`,
and `docs/reviews/QUEUE.md`, plus the assessment authority/fold, lesson
workspace, manifest reader, bundle file-serving, sandbox mount, SQLite, and
projection-lock direct callers.

**Starting HEAD:** `9e7447d348a5b2052abafe3c687e924590143bb0` on
`main`, matching `origin/main`. `git status --short --branch` showed only
`## main...origin/main`; the starting worktree was clean.

**Fix target:** all four listed commits and merge commit `876b879` are
ancestors of `main`. `876b879^{tree}` and reviewed branch head
`d18f755^{tree}` are byte-identical at
`43a9fd9d5cff58c3ec96b1cab6d35535abaf02d8`; the only change from the merge to
the starting HEAD is queue bookkeeping that pins the landed hashes. The entry
is merged, so repo tradition and the drain brief put every fix directly on
`main`.

**Report file:**
`docs/reviews/2026-07-28-lesson-assessments-projection-review.md`, derived
from the entry's lesson-assessments-projection subject.

**Prior reports to reconcile:** every existing
`docs/reviews/*-review.md` closing verdict was scanned. The binding conditions
for this surface are:

- `2026-07-28-lesson-assessment-authority-review.md`: central write-guard W1,
  assessment request admission, same-lesson references, fingerprint
  idempotency, archive ordering, row/event atomicity, the active-state fold,
  and UTF-8-safe refusals are resolved. Origin-less non-browser access remains
  accepted only inside the direct-loopback posture; assessment recovery still
  requires a consistent private SQLite backup.
- `2026-07-23-attempts-projection-cost-review.md`: attempt A1/A2 and the
  attempts projection's bounded nominal append, streaming repair, cursor/seal
  checks, and private cross-process exclusion are resolved. This entry must not
  regress or copy that machinery into the separate assessments projection.
- `2026-07-17-bundle-schema-runtime-review.md` and later bundle/runner reports:
  bounded/total manifest parsing, v2 positive file allowlisting, reserved-name
  exclusion, identity/profile fail-closed behavior, and sanitized findings are
  resolved. The same-user bundle-publication window remains accepted by
  design.
- Generated-brief, lesson-role, terminal, workspace, and sandbox reports:
  metadata remains data, brief publication remains atomic, learner isolation
  and server-owned role selection remain resolved, and terminal-opt-in T1
  remains open/accepted only for the deliberately plain owner shell. The
  trusted lesson-agent's intentional host-network and credential posture is
  unchanged.
- `2026-07-21-check-activation-review` and later Phase-F reports:
  bridge-runtime D5 L1 remains mitigated; D5 L2/L3 and artifact, runner,
  run-API, and generated-pedagogy protections remain resolved.
- `2026-07-27-platform-support-review.md`: portable import/startup, missing
  `fcntl` projection-pending fallback, and public-data-boundary dispositions
  remain binding. Wider, proxy-adjacent, or multi-user deployment remains
  unsupported.

A closing verdict must state whether each condition remains resolved,
mitigated, open/accepted, or unchanged for the closing tree.

**Validation baseline:** approved host runs at the clean starting HEAD passed:
`python verify.py` — **819 passed, 0 failed**; `python verify_restore.py` —
**28 passed, 0 failed**. The initial restricted-sandbox verifier emitted no
assertion result and was interrupted after the repository's known
TestClient/startup stall; only the complete host counts are baselines.

## Context and method

The deployment decision assumes the documented unauthenticated,
direct-loopback `127.0.0.1:8765`, single-user, single-worker app. No service
was restarted or signalled, and no live database, lesson bundle, export,
browser profile, screenshot, or authenticated state was read or written.
Executable probes used an invented lesson identity and an in-memory SQLite
database.

The complete landed diff, assessment service, projection implementation,
workspace reconcile hook, reserved-name grammar, bundle serving path, API and
bundle contracts, verifier section, queue description, and direct sandbox and
SQLite callers were read. Static tracing covered authority commit boundaries,
active-fold cardinality, rate and idempotency behavior, read-snapshot
consistency, flock lifecycle, private lock naming, manifest identity gating,
bundle-root descriptor use, temporary-file publication, collision handling,
link/name substitution, fsync ordering, reconcile triggers, response honesty,
and reader exposure. Tests, commit messages, and earlier review summaries were
treated as claims to verify, not as authority.

## Findings (severity-ranked)

### L1 — Unbounded active keys restore lifetime-linear full rewrites and an unmetered replay amplifier (Low, confirmed; owner decision required)

The repository contract says `assessments.jsonl` is small by construction and
that its size is bounded by current state rather than lifetime writes
(`docs/learn-bundle-spec.md`, section 6.5). The implemented keyspace does not
provide that bound:

- each evidence write may introduce 1–8 new opaque concept refs, and the
  endpoint deliberately does not require those refs to be among the manifest's
  at-most-64 declared concepts (`app/services/assessments.py:_clean_concepts`;
  `docs/lesson-assessments-api.md`, request table);
- each new concept retains that evidence row in `evidence_by_concept`, and each
  newly reviewed attempt retains one review in `reviews_by_attempt`
  (`app/services/assessments.py:active_state`);
- attempts and distinct ad-hoc concept refs have no lifetime cardinality or
  retention bound.

A caller can therefore make every accepted assessment remain in the active
fold simply by using a fresh valid concept ref. `_fold_records()` materializes
the complete fold, `_render()` materializes the complete JSONL byte string,
and `_publish()` writes and fsyncs it synchronously. That work runs after every
successful write, on a lesson-agent terminal open, and on a matching
idempotent replay. The replay path remains outside the assessment rate budget:
the nearby claim that replay work is “one indexed SELECT” became false when
`_replay_or_conflict()` began calling `_row_response()`, which performs the
full reconcile. A local caller can create one known assessment and then issue
unlimited matching duplicates, forcing repeated full rewrites without
consuming the 30-per-60-second window.

A focused in-memory probe inserted valid-shaped active evidence with distinct
invented concepts and maximum-size 8-KiB notes. Current code produced:

| active rows | rendered bytes | fold CPU | render CPU |
|---:|---:|---:|---:|
| 300 | 2,591,045 | 0.0111 s | 0.0093 s |
| 1,000 | 8,636,948 | 0.0302 s | 0.0319 s |
| 5,000 | 43,192,948 | 0.1779 s | 0.1680 s |

Those timings exclude temporary-file writes, file and directory `fsync`, JSON
response latency, and contention. Five thousand active rows are reachable in
about 2 hours 47 minutes at the documented rate cap—the same arithmetic that
made a full-history rewrite unacceptable—because unique active keys prevent
the promised compaction. From there every accepted write or duplicate replay
can synchronously rewrite about 43 MiB; continued growth is unbounded.

This is Low, not Medium, for the supported direct-loopback single-user
deployment: the caller is a same-machine process, accepted writes remain
durable, projection failure degrades honestly to `pending`, and there is no
authority bypass, confidentiality breach, or data loss. It is nevertheless a
concrete persistent service/terminal-availability defect in the deployed
posture, not hypothetical wider-deployment hardening. On a wider
unauthenticated listener it would become a direct remote resource-exhaustion
primitive, independently reinforcing the existing wider-deployment
prohibition.

Resolving L1 requires a product/spec choice. A fixed projection byte/active-key
ceiling needs defined overflow semantics (which current judgments may be
omitted, or whether the projection becomes permanently pending). Restricting
evidence to declared concepts still leaves reviews-per-attempt unbounded and
changes the accepted ad-hoc-concept contract. Changing the fold to retain only
recent attempts/concepts changes what “active state” means. A different
incremental or sharded medium changes the frozen single-file/full-rewrite
contract. The reviewer cannot choose among those behaviors without owner
authority.

No Critical, High, Medium, or Info finding was found.

## Confirmed protections and rebutted candidates at the starting tree

- The assessment row and paired event still commit before any projection
  work. `reconcile_projection()` refuses an active caller transaction, and a
  projection exception cannot turn a durable write into a 500.
- Fold rows and `as_of_seq` come from one read snapshot. A sibling commit
  cannot be advertised by the watermark while omitted from the rendered
  state.
- The app-private lesson-UID lock is separate from the attempts lock and
  outside the agent-writable bundle. Busy or unavailable locking returns
  projection-pending without holding SQLite's writer lock.
- A non-rejected manifest with a contradictory lesson UID blocks publication;
  missing, legacy, and rejected manifests retain the explicit publish
  behavior. Projection reads do not create bundle state.
- Publication opens one no-follow directory descriptor, stages a mode-0600
  exclusive file, fsyncs it, atomically replaces the reserved name, and fsyncs
  the directory. Directories, symlinks, and multi-link collisions are not
  opened or adopted.
- Same-UID mutation or name substitution inside the writable bundle is
  reproducible but is not a new finding. The file is explicitly derived and
  non-authoritative; the same actor can rewrite it immediately after any
  validation. SQLite plus the paired event remain the authority, and the
  existing attempts-projection report already records this trust-boundary
  calibration.
- `assessments.jsonl` and anything nested under that name are rejected by the
  v2 path grammar. Both the v2 positive allowlist and the v1 reserved-name
  denylist keep it off the generic `/files/` preview surface.
- Neither lesson terminal role receives a filesystem-enforced read-only bundle
  mount, but that is unchanged established same-UID posture rather than a new
  authority claim by this entry. The learner iframe and HTTP bridge receive no
  assessment-write capability.

## Initial verification and stop verdict

- Reachability/tree proof — all listed commits and merge commit `876b879` are
  ancestors of starting `main`; `876b879^{tree}` equals `d18f755^{tree}`.
- `git diff --check a05d5fc^..d18f755` — passed.
- Starting-head approved host `python verify.py` — **819 passed, 0 failed**.
- Starting-head approved host `python verify_restore.py` — **28 passed,
  0 failed**.
- Focused active-key growth probe — confirmed L1 and the figures above without
  touching a runtime bundle or private instance.

**NOT YET SAFE TO MAKE LIVE for the new assessments projection.** L1 leaves a
documented bounded-current-state design with no actual bound and restores the
lifetime-linear synchronous rewrite class that the projection design claims
to avoid. The queue entry remains Pending. Per the drain brief's owner-level
product/specification stop rule, no fix was selected, no code or queue
bookkeeping was changed, no commit was created or pushed, and **0 of 10**
cycles have been consumed. Wider, proxy-adjacent, or multi-user deployment
remains **NO** independently. A live restart remains owner-only and was not
performed.
