# Pedagogy template E — adversarial security review

**Scope:** exactly one Pending entry was present at review start: the 2026-07-22
entry for commits after `be2e6e5` on `fix/35-pedagogy-template-e`, covering
`app/services/lessons.py`, `verify.py`, and `docs/reviews/QUEUE.md`, plus their
direct callers. The exact scoped range is `be2e6e5..0258dc3`: `d5f628f` and
`0258dc3`. It replaces the generated lesson brief with the PEDAGOGY.md section
4 draft and adjusts the verifier anchors for that brief.

**Starting HEAD:** `0258dc34f4d7dfd743ae0ddd869a5122800b7d9d` on
`fix/35-pedagogy-template-e`. `git status --short --branch
--untracked-files=all` showed only the branch/upstream line, with no tracked,
untracked, or ignored worktree change. The starting worktree was clean.

**Fix target:** `main` was `be2e6e5873cdac7183fcc2adc2e6ffd4a59e80ea`
and is an ancestor of the review head. Both `git merge-base --is-ancestor
d5f628f main` and the same check for `0258dc3` returned 1: neither entry commit
is reachable from `main`. The entry is therefore an unmerged branch change,
so any drain fixes and the final report/queue commit go on
`fix/35-pedagogy-template-e`.

**Report file:**
`docs/reviews/2026-07-22-pedagogy-template-e-review.md`, derived from the
entry's “pedagogy template E” subject.

**Prior reports to reconcile:** every existing
`docs/reviews/*-review.md` closing verdict was scanned. The binding reports for
this generated-brief and lesson-agent feature area are:

- `2026-07-16-brief-writer-review.md`: lesson metadata must remain data rather
  than interpolated instructions, and atomic mode-0600 publication must retain
  the symlink, hard-link, FIFO, and interrupted-write protections.
- `2026-07-16-lesson-brief-teaching-contract-review.md`: the source/learner
  instruction-data boundary (L1), whole-bundle no-symlink rule (L2), bounded
  regular-file discovery (N1), exact artifact-root grammar (N2/N3), and
  unknown-manifest-field preservation rule were all resolved at its final
  verdict and must not regress.
- `2026-07-20-lesson-brief-bridge-conventions-review.md`: inbound parent
  authentication (B1), lesson-wide request-id uniqueness (B2), conforming
  reject handling (N1), and the no-invented-write limitation were closed; the
  separate bridge-runtime gate remained binding.
- `2026-07-21-check-activation-review.md`, superseding the earlier bridge
  runtime and attempt-backend deployment gates: D5 L1 remains mitigated rather
  than eliminated, D5 L2 and L3 are resolved, and D4 A1/A2 remain accepted Low
  follow-ups that bar wider unauthenticated deployment.
- `2026-07-21-lesson-learner-sandbox-review.md` and
  `2026-07-22-terminal-surfaces-review.md`: learner isolation and the
  two-surface lesson client are resolved; runner support remains later scope;
  terminal-opt-in T1 is resolved for `lesson-agent` and `lesson-learner` but
  remains accepted for the deliberately plain owner shell; the trusted agent's
  intentional network/credential posture is unchanged. Earlier terminal,
  workspace, bundle, and lesson-writer protections remain resolved.

The closing verdict below must state whether each condition is resolved,
mitigated, still open/accepted, or unchanged for this code.

**Validation baseline:** at the clean starting HEAD, approved host runs of the
exact required commands passed: `python verify.py` — **641 passed, 0 failed**;
`python verify_restore.py` — **28 passed, 0 failed**. Initial restricted-sandbox
runs emitted no assertion result and were interrupted at the repository's
known TestClient execution-environment stall; only the complete host counts are
used as baselines.

## Context and method

The deployment decision assumes an unauthenticated, single-user, single-worker
app bound directly to `127.0.0.1:8765`. No service was restarted or signalled,
and no live database, lesson bundle, export, browser profile, screenshot, or
authenticated state was read or written.

The complete scoped diff, touched files, generated brief, publication path,
terminal/workspace caller, frozen bundle and bridge contracts, and the direct
consumers of the generated instructions are reviewed adversarially. Verifier
anchors and queue prose are treated as claims to verify, not as authority.

## Findings (severity-ranked)

### L1 — Every agent session is told to ingest an unbounded permanent projection (Low, confirmed)

The new learner-record section makes its first operation “read
`attempts.jsonl` in full” (`app/services/lessons.py:879-888`). There is no file
or row retention bound behind that instruction. Attempt rows are immutable and
never deleted (`app/db.py:489-517`; `docs/learn-bundle-spec.md:84-90`), each
projection line may reach 64 KiB (`app/services/attempts.py:41-47`), and both
the append check and rebuild deliberately cover every historical row
(`app/services/attempts.py:297-323`, `364-401`). D4 A2 already records the
related lifetime-linear projection cost as an accepted Low follow-up; this
commit newly imports that whole lifetime history into every lesson-agent
context before useful work begins.

An invented lesson can accumulate roughly 32 MiB in only 512 maximum-size
lines, and the contract permits it to grow forever. Repeatedly loading the
whole learner-controlled file can exhaust an agent's context or session budget
and amplifies the very untrusted instruction-shaped data that the surrounding
brief correctly classifies as data. The rate window slows writes but neither
bounds retained bytes nor the next session's read. Impact remains Low for the
documented loopback single-user posture because the current writer is local,
human-scale, and rate-damped; it would be a direct persistent agent-availability
primitive on a wider unauthenticated listener.

Limit this pre-read to a recent bounded byte window, handle a partial first
line and malformed/unknown-version lines explicitly, and pin the bound in the
generated-brief verifier. This is a reader-side mitigation only: D4 A2 remains
open because it concerns the app's projection writer lock and rebuild work.

### L2 — “Quote the learner's actual words” omits active-HTML output encoding (Low, confirmed)

The new adaptation rule tells the agent to quote the learner's actual words
back, then permits the response to become a new lesson section/page
(`app/services/lessons.py:899-908`). Attempts accept arbitrary UTF-8 free text,
and JSON serialization does not neutralize HTML markup: an invented
`<script>location=…</script>` answer remains literal markup in the projection
(`app/services/attempts.py:175-190`, `241-259`). The instruction/data boundary
stops the answer from becoming an agent command, but the brief gives no rule to
HTML-escape the quoted bytes before inserting them into a page.

Interactive lesson pages intentionally allow inline script under
`sandbox allow-scripts` (`app/main.py:1152-1163`). The sandbox and bridge
validation prevent parent-origin or undeclared-question authority, but the
accepted same-frame navigation residual means injected page script can still
navigate its own frame; it can also alter what the learner sees and issue the
same declared-question operations as the page. This is Low rather than Medium
under the single-user loopback posture and because an agent must first copy the
answer into active HTML. It is still a stored-content execution path introduced
by an explicit new instruction.

Require only a short relevant excerpt, inserted as text after HTML escaping;
never splice learner text into markup, attributes, URLs, CSS, or script. Pin
that output boundary in `verify.py`.

### N1 — The lossy projection is still used to infer “never attempted” and page visits (Info, confirmed)

The section accurately says the projection may lag or miss a durable answer
and is never proof of absence (`app/services/lessons.py:879-884`), but the next
instructions ask the agent to decide “what was never attempted” and to
resurface unanswered questions on pages the learner visited
(`app/services/lessons.py:885-901`). `attempts.jsonl` contains attempt rows,
not page-visit records, and a missing row may be only projection lag. The
generated verifier pins the contradictory phrases but not the no-negative-
inference rule (`verify.py:349-357`).

This is fail-soft tutoring behavior rather than a security boundary, so it is
Info. State that a missing projected row means unknown, never “not attempted,”
and remove the unsupported visited-page inference. Pin the rule so a later
pedagogy refresh cannot silently restore it.

No Critical, High, or Medium finding.

## Confirmed protections and rebutted candidates at starting HEAD

- The agent/learner shell description matches the server-owned roles: both
  use the same bundle cwd, only `lesson-agent` shares host networking and proxy
  variables, and `lesson-learner` gets no proxy/socket environment. The text
  does not grant a role or change a mount.
- The inactive editor/run warning matches current code. The schema can parse
  `blocks[]`, but no editor endpoint or runner registry is active; the only
  runner profile is an unused launcher primitive. Telling the tutor not to
  author or improvise blocks fails closed and introduces no authority.
- B1/B2/N1 bridge wording is byte-unchanged. Parent source/origin and envelope
  authentication, one-result handling, lesson-wide request-id uniqueness, and
  the exact attempt operation remain present. D5 still revalidates per
  operation; the new preference for declared questions does not bypass it.
- The prior L1 instruction/data boundary, L2 no-symlink rule, N1 discovery
  bounds, N2/N3 artifact-root grammar, unknown-field preservation, immutable
  ids, v1/v2 split, and app-owned `attempts.jsonl` rule remain present. L1 and
  L2 above are new read/output-boundary gaps, not regressions to trusting
  learner text as commands or following filesystem links.
- `_AGENTS_TEMPLATE` remains constant, with no title, URL, answer, or other
  runtime value interpolated into it. The mode-0600 temporary-file + `fsync` +
  `os.replace` writer and fail-closed lesson workspace caller are unchanged.

## Initial verification and deploy verdict

- `git diff --check be2e6e5..0258dc3` — passed.
- Starting-head `python verify.py` — **641 passed, 0 failed**.
- Starting-head `python verify_restore.py` — **28 passed, 0 failed**.
- Full source/contract comparison confirmed the unbounded immutable attempt
  history, literal JSONL markup, and absence/visit mismatch described above.

**Not yet.** The implementation does not change runtime authority, but L1 can
make every future tutor session ingest an indefinitely growing untrusted file,
L2 directs stored learner text toward active HTML without the output encoding
needed to keep it text, and N1 contradicts the projection's fail-soft contract.
The queue remains Pending until all three are fixed in one dedicated cycle and
the exact fixed tree is re-verified. Wider, proxy-adjacent, multi-user, or
runner deployment remains NO independently.

## CLOSING ADDENDUM — fix commit `7897148` (cycle 1 of 10)

Fresh review of the exact `0258dc3..7897148` diff found no new Critical, High,
Medium, Low, Info, or other finding. The one fix commit changes only the
generated instruction constant and its verifier anchors; it adds no runtime
reader, listener, route, filesystem authority, sandbox mount, or terminal
lifecycle change.

### L1 — resolved

The first-read rule now admits `attempts.jsonl` only when present and caps the
agent-visible history at the newest 2 MiB of complete lines. It requires a
truncated leading line to be skipped, older omission to be made explicit,
malformed and unknown-version lines to be ignored, and unbounded loading never
to occur (`app/services/lessons.py:882-891`). This bounds per-session context
and untrusted-input amplification independently of the projection's permanent
retention. `verify.py` pins the byte window and the explicit unbounded-read
prohibition.

D4 A2 is not claimed closed: the app still reconciles lifetime history under
the SQLite writer lock. This fix only prevents the lesson tutor from importing
that entire history into every model session.

### L2 — resolved

The tutor now quotes only a short relevant excerpt as text. When the response
goes into an HTML page, the brief requires HTML escaping and text-content-only
insertion, and forbids splicing learner bytes into markup, attributes, URLs,
CSS, or script (`app/services/lessons.py:902-907`). The new verifier check pins
all sides of that output boundary. Learner answers remain untrusted input, but
the explicit adaptation instruction no longer turns a literal answer into an
active-page code path.

### N1 — resolved

The record comparison now describes only what visible projected answers show.
A missing row is explicitly unknown rather than proof of non-attempt, the brief
states that the projection contains no page-visit record, and resurfacing from
absence alone is forbidden (`app/services/lessons.py:888-903`). The verifier
pins both the unknown status and the missing visit signal. The pedagogy now
matches the projection's documented fail-soft authority.

## Prior-condition reconciliation at closing tree

- **Brief-writer metadata and publication protections — REMAIN RESOLVED.** The
  template is still constant; no lesson, attempt, title, URL, or source bytes
  are interpolated into it. Atomic mode-0600 publication, symlink/hard-link/FIFO
  replacement, interrupted-write preservation, and fail-closed workspace
  resolution are unchanged.
- **Teaching-contract L1/L2/N1/N2/N3 and unknown-field rule — REMAIN
  RESOLVED.** All source, page, asset, attempt, and learner content remains
  untrusted data; the whole-bundle no-symlink rule, regular-file discovery
  bounds, exact root grammar, immutable identity, and unknown-field
  preservation clauses remain present. This drain's L1/L2/N1 are distinct and
  resolved above.
- **Bridge-conventions B1/B2/N1 and no-invented-write limitation — REMAIN
  RESOLVED.** Parent source/origin and envelope authentication, conforming
  reject handling, one accepted handshake result, lesson-wide request-id
  uniqueness, declared questions, and the frozen D5 attempt operation are
  unchanged.
- **D5 L1 — MITIGATED; D5 L2/L3 — RESOLVED; D4 A1/A2 — STILL
  OPEN/ACCEPTED.** The template does not alter document generations, served-byte
  binding, page identity limits/cache admission, HTTP request buffering, or
  projection writer-lock duration. The new 2 MiB tutor pre-read does not claim
  to resolve A2. These residuals remain reasons not to widen the listener.
- **Learner isolation and E4 two-surface client — REMAIN RESOLVED; runner
  support — STILL OPEN/out of scope.** Both sandboxed lesson roles retain their
  server-owned selection and bundle cwd; the learner remains no-network and
  separated from the private instance. The template accurately fences the
  inactive editor/run feature and grants no runner surface.
- **Terminal-opt-in T1 — RESOLVED for `lesson-agent` and `lesson-learner`,
  STILL OPEN/ACCEPTED for the deliberately plain owner shell.** The trusted
  agent's intentional host network, CLI login material, `SSH_AUTH_SOCK`, and
  proxy posture is UNCHANGED/ACCEPTED for that role only. Terminal F1–F4,
  terminal-tab L1, workspace refusal, relative-path display, sandbox E1 S1,
  bundle-reader protections, and the direct/no-forwarded-header deployment
  mitigation all retain their prior dispositions.

## Closing verification

- Starting HEAD `0258dc3`: `python verify.py` — **641 passed, 0 failed**;
  `python verify_restore.py` — **28 passed, 0 failed**.
- `git show --check 7897148` and `python -m py_compile
  app/services/lessons.py verify.py` — passed.
- Fix-cycle `python verify.py` — **642 passed, 0 failed**, including the new
  inert-HTML quotation check and the bounded/absence-safe learner-record
  anchors.
- Fix-cycle `python verify_restore.py` — **28 passed, 0 failed**.
- `python scripts/check_public_hygiene.py` — passed. The ignored-status review
  showed only established local tool, environment, screenshot-reference, and
  review-work paths; no private runtime file or unmarked fixture is tracked.

## Closing verdict

**SAFE TO MAKE LIVE for the documented direct-loopback
(`127.0.0.1:8765`), single-worker, unauthenticated single-user deployment. Two
Low findings and one Info finding were raised and resolved in cycle 1; no
Critical, High, Medium, Low, Info, or other finding remains open for this queue
entry. Earlier generated-brief, writer, bridge, terminal, workspace, sandbox,
bundle, and two-surface protections remain resolved; D5 L1 remains mitigated,
D5 L2/L3 remain resolved, and D4 A1/A2 remain accepted Low follow-ups; runner
support remains outside this surface. Wider, proxy-adjacent, multi-user, or
runner deployment remains NO. The queue entry may move to Done. Restarting the
live service remains the owner's action and was not performed by this review.**
