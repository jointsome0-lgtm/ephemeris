# Lesson Learning output style resolution re-drain — adversarial security review

## Resolution-review parameters — current main

**Scope:** the sole unchecked Pending entry in `docs/reviews/QUEUE.md`: issue
#84's lesson-agent workspace output-style change, its original application
commit `eabb9bb`, correction `f7f2877`, merge `6209fa3`, dedicated collision
repair `633172e`, PR-bot repair `0a74699`, repair merge `7e8a850`, and their
queue-only bookkeeping. The listed surface is
`app/services/lessons.py`, `app/services/bundle_schema.py`,
`docs/learn-bundle-spec.md`, `tests/test_010_platform_ui.py`, and
`docs/reviews/QUEUE.md`; direct callers include terminal role selection and
workspace creation, manifest reads and path grammar, and the v1/v2 lesson-file
serving path.

**Starting HEAD:** `083fa3621eb1a8d2cea9e40dc176991093d8a76d` on clean
`main`, equal to `origin/main`; starting tree
`3f69767e179a149c595150ae640a210f450c75e9`.

**Reviewed tree:** every entry and repair commit named above is an ancestor of
the starting HEAD. PR #106's original merge was a real three-way merge, but
the five entry paths were byte-identical between branch tip `f4501d4` and
merge `6209fa3`. PR #112 merged repair tip `0a74699` as `7e8a850` with an
exact tree match (`28058dd` for both). From that repair merge to the starting
HEAD, the production module, bundle-schema module, and spec are unchanged;
`tests/test_010_platform_ui.py` gained only the unrelated issue-#24 settings
and request-connection probes, and the queue gained merge bookkeeping. The
verdict is therefore about the landed repair bytes and the current direct
callers actually read at starting HEAD. No entry commit was excluded for
missing ancestry.

**Report file:**
`docs/reviews/2026-07-29-lesson-learning-output-style-review.md`. Today's date
and the entry's subject derive the same path as the diagnosis preserved
below, so this superseding resolution section is added at the top rather than
creating a second same-topic report.

**Prior reports to reconcile:** every existing
`docs/reviews/*-review.md` closing verdict was rescanned. The immediately
binding report is the original July 29 diagnosis below: preserve collision
bytes, take an explicit v1/v2 compatibility decision, and pin both old
contract shapes. The lesson Claude-shim and brief-writer reviews, final
teaching-contract addenda, bundle-schema runtime review, lesson-agent sandbox
and platform-support reviews, assessments projection review, and latest
lesson-record-panel review retain the conditions enumerated in the original
report. Their dispositions are reconciled below.

**Validation baseline:** the template's literal `python verify.py` command
still cannot run because `verify.py` was deleted by `9195e6a`; it exits 2
with `can't open file`. The repository's current canonical replacement,
`uv run pytest`, passed on the host with **9 passed, 1 warning** in 35.19
seconds. The required host `python verify_restore.py` run passed
**28 passed, 0 failed**. Initial nested-sandbox runs stalled at the known
TestClient boundary and were interrupted without being counted.

## Context and method

The deployment decision assumes the documented unauthenticated,
direct-loopback `127.0.0.1:8765`, single-user, single-worker app. This review
did not merge or open a PR, restart or signal a service, or read or write the
live data directory.

The complete original and repair diffs, current touched production modules,
the relevant specification and tests, terminal workspace caller, and lesson
file-serving caller were read. Static review covered collision discovery,
ordinary/generated-file classification, unreadable files, symlinks, hard
links, special files, directory squatters, rename and atomic publication,
permissions, role separation, v1 serving, v2 path rejection, and the explicit
compatibility exception. Queue prose, commit messages, tests, and prior
reports were treated as claims until established from current source or an
independent invented-data probe.

The independent probe created a throwaway v2 lesson whose exact old-contract
shape used `.claude` as an artifact root and `.claude/settings.json` as an
editor block file. The current reader reported the documented
`invalid-path` degradation. A real `prepare_terminal_workspace()` call then
published the constant settings file while preserving the foreign learner
bytes in exactly one `settings.json.collision-<hex>` aside. The scratch
directory was removed after the probe.

## Findings

**No new Critical, High, Medium, Low, Info, or other finding was established
against the reviewed tree.**

## Prior finding and repair disposition

- **M1 destructive collision — RESOLVED by `633172e` and `0a74699`.**
  `_preserve_foreign()` classifies only an ordinary single-link file with the
  exact generated length and bytes as the writer's own output. Every other
  node is renamed aside before publication; an unreadable same-sized file is
  a non-match rather than a workspace refusal
  (`app/services/lessons.py:1407-1445`). The same rule protects both a node at
  `.claude` before directory creation and a node at
  `.claude/settings.json` before the atomic write
  (`app/services/lessons.py:1448-1468,1554-1579`).
- **M1 owner-level compatibility decision — RESOLVED in the frozen spec.**
  Section 2 states the non-destructive aside rule; §9.2 names the reservation
  as the second deliberate v1 behavior change; §9.3 keeps v2 and names the
  reservation as one explicit version-policy exception, including why a v3
  would not preserve an author-addressable `.claude` directory and the
  guarantee that older-shaped bundles lose a binding, not their bytes
  (`docs/learn-bundle-spec.md:63-72,631-665,673-691`).
- **Exact v2 regression pin requested by the diagnosis — STILL OPEN as
  verification debt, not as a deployed-code finding.** The merged suite pins
  the complete v1 collision and the shared version-independent writer, plus
  current v2 reserved-path rejection; it does not construct the diagnosis's
  exact old-valid v2 artifact-root/block manifest in a durable test
  (`tests/test_010_platform_ui.py:858-966`). The independent probe above
  established that exact current behavior, and neither manifest version nor
  read outcome branches the preservation helper. The missing dedicated pin
  therefore does not make the current application behavior false or reopen
  M1, but this report does not call the requested test condition resolved.

## Confirmed protections and rebutted candidates

- A clean bundle receives a strict constant JSON file with no lesson,
  manifest, attempt, assessment, or other runtime text interpolated. The
  publication remains a mode-0600 temporary file, flush, `fsync`, and atomic
  replace in the destination directory.
- An existing generated file is republished without accumulating an aside.
  A foreign ordinary file, hard link, symlink, FIFO, special file, directory,
  or unreadable file is never adopted as generated content. Moving a symlink
  does not follow it.
- A real `.claude` directory remains in place and unrelated siblings inside it
  remain untouched. A foreign node occupying the directory name is preserved
  as a bundle-root collision aside before a new mode-0700 directory is made.
- `resolve_terminal_workspace()` remains read-only. Only the server-selected
  `lesson-agent` role invokes `prepare_terminal_workspace()`; the
  `lesson-learner` path does not regenerate the config
  (`app/terminal.py:748-805`).
- The v1 file route denies the reserved first segment and v2 remains a positive
  declared-page/assets allowlist over the same reserved path grammar
  (`app/services/lessons.py:666-708`;
  `app/services/bundle_schema.py:33-36,216-238`). The compatibility narrowing
  is documented rather than silently treated as additive.
- The finite random aside suffix repeats the already reviewed assessment
  collision idiom. No concrete overwrite, refusal, or loss was established
  in the supported single-user workload, so no hypothetical same-user race or
  collision finding is padded into this report.

## Prior-condition reconciliation at the reviewed main tree

- **Original learning-output-style M1 — RESOLVED.** Foreign bytes survive both
  v1 and the exact old-valid v2 collision shape. The explicit compatibility
  decision is recorded. The requested v2-specific durable test remains
  **STILL OPEN as non-blocking verification debt**, with current behavior
  independently established.
- **Lesson-shim prompt-injection and hard-link/FIFO findings — REMAIN
  RESOLVED.** The generated content is constant, final destinations are
  atomically replaced, and foreign settings nodes are now preserved.
- **Lesson teaching-contract instruction/data and path-grammar findings —
  REMAIN RESOLVED.** The generated teaching brief is unchanged; the newly
  explicit compatibility exception removes the version-policy contradiction
  identified by M1.
- **Bundle-schema B1-B5, N1, parser totality, no-follow reads, selected-page
  outcome, and positive v2 preview allowlisting — REMAIN RESOLVED.** The path
  reservation changes accepted names as documented but does not weaken the
  reader's bounds or fail-closed behavior.
- **Lesson-agent sandbox authority, server-owned role separation, fail-closed
  workspace resolution, terminal F1-F4, and platform import/refusal —
  REMAIN RESOLVED.** The trusted lesson-agent's host-network, CLI credential,
  `SSH_AUTH_SOCK`, and proxy posture remain **UNCHANGED/ACCEPTED only for the
  documented single-user role**. Terminal-opt-in T1 remains
  **OPEN/ACCEPTED only for the deliberately plain owner shell**.
- **Assessment projection publication/cache/replay protections and the
  accepted same-user post-validation window — RETAIN THEIR PRIOR
  DISPOSITIONS.** The new preservation helper borrows only the collision-aside
  convention; it changes no assessment authority or projection path.
- **Record-panel false-retirement and page-selection findings — REMAIN
  RESOLVED.** The shared bundle-reader explicit-null diagnostic remains
  **STILL OPEN outside issue #84** and is neither changed nor masked here.
- **Direct/no-forwarded-header condition — REMAINS MITIGATED only by the
  documented direct-loopback deployment.** Wider, proxy-adjacent, and
  multi-user exposure remain unsupported independently of this repair.

## Verification

- Clean starting worktree and ancestry — passed on `main == origin/main` at
  `083fa3621eb1a8d2cea9e40dc176991093d8a76d`; every entry and repair commit is
  reachable.
- Repair landing proof — `0a74699` and merge `7e8a850` both have tree
  `28058dd`; the repaired production/spec files are unchanged through the
  starting HEAD.
- `git diff --check 6209fa3..0a74699` — passed.
- Literal `python verify.py` — unavailable at starting HEAD, exit 2; the file
  was intentionally removed by `9195e6a`.
- Canonical host `uv run pytest` — **9 passed, 1 warning** in 35.19 seconds.
- Host `python verify_restore.py` — **28 passed, 0 failed**.
- Independent exact old-valid v2 collision probe — passed: current outcome
  `degraded` with `invalid-path`; generated bytes exact; one aside; foreign
  bytes preserved.
- No application code, test, specification, service, or live runtime file was
  changed by this diagnosis-only resolution drain.

## Final superseding verdict

**SAFE TO MAKE LIVE for the exact starting tree
`3f69767e179a149c595150ae640a210f450c75e9` under the documented
direct-loopback `127.0.0.1:8765`, single-worker, unauthenticated single-user
deployment, once the owner closes the queue entry.** This resolution re-drain
found **0 Critical, 0 High, 0 Medium, 0 Low, and 0 Info** new findings. The
prior Medium's destructive behavior is resolved for v1 and v2, and the
compatibility exception is now explicit.

The entry stays **Pending** and points to this report, as required for a
diagnosis-only drain; a clean entry is closed by the owner. No application
repair PR is required by this verdict. The one unmet prior request is a
dedicated regression that constructs the exact old-valid v2
artifact-root/block shape; current behavior is established by this review's
probe and the shared writer is already covered, so that remains test debt
rather than a live-safety blocker. Any follow-up that chooses to pin it should
be test-only and preserve or grow the **9 / 28** baselines. Wider,
proxy-adjacent, and multi-user deployment remain **NO**. No merge or restart
was performed; restart remains the owner's action.

---

# Lesson Learning output style — adversarial security review

**Date:** 2026-07-29

## Self-parameterization

- **Scope:** exactly one Pending entry existed at review start: issue #84's
  lesson-agent workspace output-style change, with application commit `eabb9bb`
  and correction commit `f7f2877`, covering
  `app/services/lessons.py`, `app/services/bundle_schema.py`,
  `docs/learn-bundle-spec.md`, `tests/test_010_platform_ui.py`, and queue
  bookkeeping, plus the workspace-preparation, terminal-session, manifest-read,
  and lesson-file-serving direct callers.
- **Starting HEAD:** `924749b8d38532420a89875c90d12147e9fa592c` on clean
  `main`, equal to `origin/main`; starting tree
  `619f2bd4b087b498e326f39298e9e1aaeb5f4af8`.
- **Reviewed tree:** both listed commits are ancestors of starting HEAD.
  PR #106 merged as `6209fa3` from branch tip `f4501d4`. The queue's five
  covered files are byte-identical between that tip and the merge; from the
  merge through starting HEAD, only `docs/reviews/QUEUE.md` changed among those
  files, to record the merge. The verdict therefore concerns the landed
  application/spec/test bytes and the current direct callers actually read at
  starting HEAD. No listed entry was excluded for missing ancestry.
- **Report file:**
  `docs/reviews/2026-07-29-lesson-learning-output-style-review.md`, derived
  from the entry's `learning-output-style` subject.
- **Prior reports to reconcile:** every existing
  `docs/reviews/*-review.md` closing verdict was scanned. The binding reports
  are the lesson Claude shim and brief-writer reviews, the final lesson
  teaching-contract addenda, the bundle-schema runtime review, the
  lesson-agent sandbox and platform-support reviews, the assessments
  projection review, and the latest lesson-record-panel review. Their current
  dispositions are stated below.
- **Validation baseline:** the template's literal `python verify.py` command
  cannot run at this HEAD because `verify.py` was deleted by `9195e6a`
  (`Split verification into ordered pytest suites`); it exits 2 with
  `can't open file`. The repository's current canonical replacement from
  `CONTRIBUTING.md` and CI, approved and run on the host because the nested
  sandbox stalled at TestClient lifespan startup, is `uv run pytest`:
  **9 passed, 1 warning** in 39.62 seconds. The required host
  `python verify_restore.py` run passed **28 passed, 0 failed**. No passing
  count is attributed to the absent legacy file.

## Context and method

The deployment decision assumes the documented unauthenticated,
single-worker application bound directly to `127.0.0.1:8765`. No service was
restarted or signalled, and no live database, bundle, export, browser state,
credential, or private runtime path was read or changed.

The complete listed diff and current production modules were read with their
terminal and file-serving direct callers. Static review covered constant-data
generation, instruction/config injection, path grammar and versioning,
pre-existing bundle compatibility, overwrite and failure behavior, symlink
and special-file handling, directory and file permissions, learner-vs-agent
role separation, preview exposure, and the documented narrow-deployment
boundary. Tests, queue prose, commit messages, and prior reports were treated
as claims until established from current source or an independent probe.

Two independent probes used only invented throwaway directories:

1. A pre-planted `.claude` symlink was replaced without touching its target;
   the created directory was mode 0700, `settings.json` was mode 0600 and
   byte-exact, and an unrelated sibling inside the real directory survived a
   regeneration.
2. A manifest accepted as `ok` by the exact pre-#84 schema reader declared
   `.claude` as an artifact root and `.claude/settings.json` as one editor
   block's file. Opening that invented lesson through the current real
   `prepare_terminal_workspace()` changed the manifest outcome to `degraded`
   and replaced the learner file with the generated output-style JSON.

## Findings (severity-ranked)

### M1 — A contract-valid existing `.claude/settings.json` is silently overwritten (Medium, confirmed)

Before `eabb9bb`, `.claude` was not reserved. The frozen path grammar therefore
accepted it as a v2 artifact root and accepted `.claude/settings.json` as a
block file; the v1 compatibility surface likewise allowed ordinary files
there. The #84 change adds `.claude` to the shared reserved-name tuple
(`app/services/bundle_schema.py:33-36`) and to the frozen spec
(`docs/learn-bundle-spec.md:42-68`). That is not additive v2 evolution: the
same spec says the only deliberate v1 behavior change is corrupt-manifest
rejection, otherwise v1 render behavior is unchanged
(`docs/learn-bundle-spec.md:630-654`), and says that changing the meaning of
an existing v2 field requires v3 (`docs/learn-bundle-spec.md:656-665`).

The filesystem writer then makes the compatibility break destructive.
`prepare_terminal_workspace()` calls `_ensure_bundle_manifest()` but does not
use its outcome, and on every lesson-agent open proceeds to atomically replace
`.claude/settings.json` with the constant
(`app/services/lessons.py:1511-1532`). Atomic replacement correctly avoids
following a final symlink, but it also replaces an ordinary pre-existing file
without preserving its bytes (`app/services/lessons.py:1376-1402`). The new
regression test explicitly requires an edited file to be overwritten on the
next open (`tests/test_010_platform_ui.py:748-758`); it does not distinguish a
file generated by #84 from content that was valid under the preceding frozen
contract.

The invented compatibility probe made the failure concrete:

- the exact parent reader returned `outcome: ok`, no finding, roots
  `["attempts", ".claude"]`, and the block file
  `.claude/settings.json`;
- the starting-HEAD reader returned `outcome: degraded` with
  `invalid-path`;
- the real workspace open still succeeded and changed
  `Vera Example learner artifact\n` into
  `{"outputStyle": "Learning"}` in the generated formatting.

The queue records that no current live bundle or repository fixture declares a
path under `.claude`; this review did not inspect the prohibited live data
directory and does not turn that queue claim into a compatibility guarantee.
The frozen public contract admitted the shape, and an engine update can be
used with another conforming private instance. The result is silent loss of a
previously valid learner artifact on an ordinary owner action, with no backup
or refusal. That earns Medium even though it is not remotely triggerable under
the supported loopback deployment.

**Fix direction:** this is an owner-level compatibility decision, not a repair
for this diagnostic run. Choose explicitly between preserving v1/v2 behavior
and introducing a versioned reservation. In either case, the repair must
detect a pre-existing non-generated `.claude/settings.json` before replacement
and refuse or migrate it without losing bytes. If `.claude` is to become
reserved retroactively, reconcile that choice with the frozen v1 and v2
version policy and provide an explicit collision migration/backup path; if the
existing contracts remain authoritative, do not reinterpret their accepted
paths in place. Add regressions for both a v1 collision and the exact
previously valid v2 artifact-root/block manifest above, proving an agent
workspace open cannot silently destroy either file.

No separate Critical, High, Low, or Info finding survived review.

## Confirmed protections and non-findings

- The generated settings value is a fixed, strict JSON constant. No lesson
  title, slug, uid, manifest value, attempt, assessment, or other runtime data
  is interpolated into agent-harness configuration.
- The new parent-directory handling closes the pre-planted cases it claims:
  a symlink or non-directory at `.claude` is unlinked, a real directory is
  retained, and only `settings.json` is app-owned. The shared temporary-file
  writer does not follow a final symlink, hard link, FIFO, or other special
  destination node.
- Newly created `.claude` and `settings.json` have the intended 0700 and 0600
  modes in the host probe. An unrelated sibling in an existing real directory
  is preserved.
- `resolve_terminal_workspace()` remains read-only. Only the server-selected
  `lesson-agent` role calls `prepare_terminal_workspace()`; the learner path
  does not generate or rewrite the configuration.
- The v1 file route and v2 path grammar both withhold `.claude` from preview
  after this change. Their retroactive compatibility effect is part of M1,
  not a separate disclosure finding.
- No terminal trust gate, role selector, PTY/WS lifecycle, sandbox profile,
  bridge ABI, route, listener, assessment/attempt authority, or generated
  teaching instruction changed.
- A concurrent same-user rename of the `.claude` parent remains outside the
  code's stated best-effort filesystem boundary: the supported deployment
  does not treat another process under the same user as a separate principal.
  No finding is padded from that hypothetical race.

## Prior-condition reconciliation

- **Lesson-shim prompt-injection and hard-link/FIFO findings — REMAIN
  RESOLVED.** The new file is constant and reuses the atomic destination
  replacement. M1 is a compatibility collision with an ordinary formerly
  valid file, not a regression to following links or interpolating metadata.
- **Lesson teaching-contract N3 and its earlier instruction/data findings —
  REMAIN RESOLVED as template/spec wording.** `_AGENTS_TEMPLATE` is unchanged
  and still matches the current path grammar. M1 instead establishes that the
  newly amended current grammar conflicts with the frozen version-policy text;
  that new owner-level conflict is **OPEN**.
- **Bundle-schema B1-B5, N1, selected-page outcome, parser totality,
  no-follow reads, and positive v2 preview allowlisting — REMAIN RESOLVED.**
  The reader stays bounded and fail-closed. M1 is the new semantic change to
  which paths the same schema versions accept.
- **Lesson-agent sandbox authority, role separation, fail-closed workspace,
  atomic brief publication, terminal F1-F4, and platform import/refusal —
  REMAIN RESOLVED.** The trusted agent's host network, CLI credentials,
  `SSH_AUTH_SOCK`, and proxy posture remain **UNCHANGED/ACCEPTED only for the
  documented single-user role**. Terminal-opt-in T1 remains
  **OPEN/ACCEPTED only for the deliberately plain owner shell**.
- **Assessment projection publication/cache/replay protections and the
  accepted same-user post-validation window — RETAIN THEIR PRIOR
  DISPOSITIONS.** The additional generated file does not change projection
  authority or reconciliation.
- **Record-panel false-retirement and page-selection findings — REMAIN
  RESOLVED.** The separate shared bundle-reader explicit-null diagnostic
  remains **STILL OPEN outside issue #84** and is neither resolved nor
  regressed here.
- **Direct/no-forwarded-header condition — REMAINS MITIGATED only by the
  documented direct-loopback deployment.** Wider, proxy-adjacent, or
  multi-user exposure remains unsupported independently of M1.

## Verification

- Clean starting worktree and ancestry checks — passed: both listed commits
  are ancestors of `924749b`.
- Covered-file landing proof — passed: `git diff` between branch tip
  `f4501d4` and merge `6209fa3` is empty for all five entry paths; after that
  merge only queue bookkeeping changes among them.
- `git diff --check eabb9bb^..f7f2877` — passed.
- Literal `python verify.py` — unavailable at starting HEAD, exit 2; the file
  was intentionally removed by `9195e6a`.
- Canonical host `uv run pytest` — **9 passed, 1 warning** in 39.62 seconds.
- Host `python verify_restore.py` — **28 passed, 0 failed**.
- Independent pre-planted-node/mode/preservation probe — passed.
- Independent old-reader/current-workspace compatibility probe — confirmed
  M1 and the byte replacement described above.
- No application code, test, specification, service, or live runtime file was
  changed by this diagnosis-only drain.

## Closing verdict

**NOT SAFE TO MAKE LIVE.** This review found **1 Medium, 0 Critical, 0 High,
0 Low, and 0 Info** findings. The implementation safely generates a constant
configuration for a clean new bundle, but it retroactively reclassifies paths
accepted by the frozen v1/v2 contract and can silently destroy a formerly
valid `.claude/settings.json` on the first lesson-agent open.

The queue entry stays **Pending** and points to this report. A separate
owner-decided, ordinarily reviewed repair PR must preserve or explicitly
migrate collision bytes, reconcile the v1/v2 version-policy contradiction,
add the v1 and v2 collision regressions, preserve or grow the **9 / 28**
current baselines, and then receive a fresh drain. Until that lands, the live
service must remain on its current process image; no restart gate is opened.
Wider, proxy-adjacent, or multi-user deployment remains **NO** independently.
No merge or restart was performed; restart remains the owner's action.
