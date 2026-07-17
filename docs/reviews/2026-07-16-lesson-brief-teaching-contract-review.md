# Lesson brief teaching contract — adversarial security review

**Scope:** commit `5ae5017` against parent `ba4676b` — the constant generated
lesson `AGENTS.md` was expanded from bundle mechanics into a teaching contract,
and three string-presence checks were added to `verify.py`. The complete touched
files were read at the target commit, together with the terminal caller, lesson
preview route and iframe, frozen Learn bundle contract, and earlier reports for
the lesson workspace, Claude shim, brief writer, terminal opt-in, and central
write guard. Commit `dfda47a` only appends the queue entry and does not change the
reviewed implementation.

**Context:** v0 has no authentication and the live instance is assumed to bind
directly to loopback only. The terminal remains opt-in and applies its stricter
loopback peer plus exact loopback Host/Origin gate before opening a shell. A
lesson agent nevertheless runs with the service user's OS permissions and
network path, so content promoted into its working context is a security
boundary even when the HTTP listener remains local.

**Method:** diffed the listed commit from its parent, traced generation of both
agent entry-point files through `prepare_terminal_workspace()` and PTY startup,
compared every v1/v2, identity, attempt, artifact, and write-authority statement
with `docs/learn-bundle-spec.md`, and examined how agent-authored active pages are
served. The earlier confirmed prompt-injection, link/file-type, path-disclosure,
workspace-fallback, and terminal trust/lifecycle findings were re-checked for
regression.

**Verdict:** one Medium and one Low finding are confirmed. The brief keeps the
previous fix that treats lesson title and source URL strings as data, but it now
directs an OS-capable agent to consume source material, free-text answers, and
learner files without saying that their contents are untrusted data rather than
instructions. It also directs the agent to read `attempts/` without carrying
forward the frozen whole-bundle rule that no consumer may follow symlinks. Do
not clear the lesson-terminal deploy gate until both findings receive dedicated
fixes and regression coverage.

## Findings (severity-ranked)

### L1 — Source and attempt content re-open stored/indirect prompt injection (Medium, confirmed)

The previous brief-writer fix explicitly says that the title and source URL in
`lesson.json` are ordinary user-entered content and never instructions
(`app/services/lessons.py:397-403`). The new teaching contract does not extend
that boundary to the content reached through those values. It calls course
steps, articles, and notes "raw input" and tells the agent to add what they omit
(`app/services/lessons.py:345-368`), then expressly requires reading
`attempts.jsonl` and every file under `attempts/` and responding to the learner's
answers (`app/services/lessons.py:364-366`,
`app/services/lessons.py:412-416`). Nowhere does it say that instructions found
in those sources are data to analyze, not commands to follow.

That is a materially broader instruction input than the title/source strings
fixed in the earlier reports. The frozen contract defines `answer` as up to 32
KiB of free text and makes `attempts/` learner-authored
(`docs/learn-bundle-spec.md:314-333`,
`docs/learn-bundle-spec.md:370-400`). An invented answer or article can therefore
contain a directive to abandon the lesson task, inspect an invented
out-of-bundle note, or publish content elsewhere. The agent receives that text
because the trusted generated brief told it to read and react to it; if the
model follows the embedded directive, the lesson shell supplies the impact of
the service user's filesystem and network permissions. User action is still
required to launch an agent, which keeps this below High.

At `5ae5017` the attempt backend is not implemented: the only application
references to attempts are the new brief and its verifier checks. That limits
the attempt-answer planting path today to local/bundle content. It does not
remove the current risk from externally sourced course material, and the
finding becomes directly network-plantable when the specified free-text
attempt endpoint lands or if bundles become importable. With no authentication,
exposing that future write surface beyond loopback would let a non-browser
client store an answer for the agent to consume later.

The new verifier asserts only the presence of `attempts.jsonl` and the
read-only wording (`verify.py:305-317`). It has no negative case proving that an
instruction-shaped answer/source remains data or that the brief establishes a
precedence rule for conflicting content.

*Fix direction:* in a dedicated brief change, classify all fetched source
material and every bundle file other than the generated briefs as untrusted
data. State explicitly that embedded instructions, tool requests, links, and
commands are never followed merely because they occur in an article, page,
asset, attempt record, or learner file; the generated brief retains precedence.
Add an invented instruction-shaped attempt/source regression check. Because
prompt wording is defense in depth rather than a hard sandbox, do not widen the
terminal or future attempt surface on the strength of that wording alone.

### L2 — Mandatory `attempts/` reads omit the bundle's no-symlink rule (Low, confirmed)

The frozen contract says symlinks are never followed by any consumer, for the
bundle itself or any file inside it, and calls out the need for per-segment
enforcement (`docs/learn-bundle-spec.md:59-78`). The study agent is one of the
contract's named consumers (`docs/learn-bundle-spec.md:8-14`). The new brief
instead tells it to read the files under `attempts/`
(`app/services/lessons.py:364-366`, `app/services/lessons.py:412-413`) while only
saying to work inside the apparent bundle directory
(`app/services/lessons.py:341-343`). It never says to inspect path components,
skip links, or refuse content reached through a symlink.

Workspace preparation checks that the lesson directory itself is a real direct
child, but it does not inspect nested learner paths
(`app/services/lessons.py:475-489`,
`app/services/lessons.py:533-552`). Consequently a conventional agent command
that opens `attempts/invented-note` follows a pre-planted link even if its target
is outside the bundle. This violates the promised bundle-only scope, can expose
unrelated local content to the model, and also supplies another indirect
instruction channel. It is Low under today's same-user, non-importable bundle
posture because a process able to plant the link already has comparable local
filesystem authority. It matters for the explicitly anticipated imported or
less-trusted bundle case, the same future-facing condition that motivated the
earlier lesson-directory link defense.

*Fix direction:* carry the no-symlink rule into the generated teaching contract
and tell the agent to skip any file whose path contains a symbolic-link
component. Add an invented out-of-bundle link case to the generated-brief
checks. C3's planned application-side path enforcement does not automatically
protect shell tools run by the study agent, so this consumer needs an explicit
rule or a real filesystem sandbox rather than relying on `cwd` wording.

## Confirmed protections and regression checks

- **Lesson metadata interpolation remains fixed.** `_AGENTS_TEMPLATE` is still
  constant, lesson fields remain in `lesson.json`, and workspace preparation
  writes the constant without formatting title or URL into either auto-loaded
  instruction file (`app/services/lessons.py:335-460`,
  `app/services/lessons.py:519-552`). L1 concerns newly introduced content
  sources, not a regression to string interpolation.
- **Brief publication remains safe.** The unchanged same-directory mode-0600
  temporary-file, `fsync()`, and `os.replace()` path never opens the destination,
  so the earlier final-path symlink, hard-link, FIFO, and partial-publication
  findings remain fixed (`app/services/lessons.py:492-516`). L2 concerns links
  encountered later by the agent under `attempts/`, not the generated brief
  filenames.
- **Manifest/write-authority wording is consistent.** The v1/v2 split,
  immutable `schema_version` and `lesson_uid`, `pg_`/`q_` lifecycle,
  `questions[]`, app-owned `attempts.jsonl`, and learner-owned work files match
  the frozen schema and authority table. The commit does not add a manifest
  parser or writer.
- **Terminal exposure and failure behavior are unchanged.** The opt-in route,
  loopback peer and Host/Origin checks, fail-closed workspace preparation,
  allowlisted child environment, and PTY ownership/lifecycle bodies did not
  change. No listener or route is added by `5ae5017`.
- **Offline-page wording is an instruction, not current enforcement.** The new
  brief bans remote page resources, but the unchanged legacy preview CSP still
  permits HTTPS sources, inline script, `unsafe-eval`, network connections,
  forms, popups, and downloads (`app/main.py:1090-1099`,
  `app/templates/learn.html:130-138`). This is not scored as a regression because
  the frozen contract explicitly leaves today's v1 bundles in
  `legacy-display` and assigns the strict no-network profile to D1
  (`docs/learn-bundle-spec.md:266-286`). It remains a reason not to treat prompt
  compliance as a security boundary.

## Verification

- `git diff --check 5ae5017^ 5ae5017` — passed.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile
  app/services/lessons.py verify.py` — passed.
- Full source/contract scan — confirmed that only `lesson.json` title/source
  values receive explicit "never instructions" treatment; attempts/source
  content and the whole-bundle no-symlink rule have no corresponding generated
  instruction or negative verifier case.
- `env -u ACTIVITY_DATA_DIR PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 timeout 55s
  .venv/bin/python verify.py` — inconclusive in this environment: it emitted
  only the known TestClient deprecation warning, produced no assertion output,
  and exited `124` at the bound. No failing assertion was observed, and this
  report does not independently claim the queue entry's 379-check result.

## Deploy verdict

**SAFE TO MAKE LIVE: NO for the lesson-agent teaching workflow.** The HTTP and
terminal trust gates themselves are unchanged, but the auto-loaded brief creates
a Medium instruction/data-boundary regression and omits a Low frozen
filesystem rule. Fix both in a dedicated change, add negative regression
coverage, and re-review before clearing the queue entry. Wider network exposure
remains unsupported regardless of these fixes.

## Addendum — re-review of fix commit `eeb71f1`

**Scope and method:** re-applied the standing brief to the exact fix diff
`eeb71f1^..eeb71f1`. The complete `app/services/lessons.py` and `verify.py` at
the fix commit were re-read, together with the generated-brief publication and
lesson-terminal call chain, the frozen whole-bundle symlink and attempt-data
contract, and the earlier reports for this surface. The fix changes only the
constant lesson brief and its string-presence regression checks; it does not
change a listener, route, PTY/WS lifecycle, filesystem writer, or preview path.

### Finding status

- **L1 — Resolved.** The brief now labels everything the learner wrote as data,
  never instructions (`app/services/lessons.py:364-368`), and extends the same
  boundary to fetched or handed-in source material, lesson pages, assets,
  `attempts.jsonl`, and files under `attempts/`. Embedded instructions,
  commands, links, and tool requests are explicitly material to analyze rather
  than directives, with the generated brief retaining precedence
  (`app/services/lessons.py:399-409`). This closes the missing instruction/data
  separation identified by L1. The verifier now pins both the attempt-specific
  wording and the general untrusted-data/precedence wording
  (`verify.py:310-323`). These checks validate publication of the static
  contract; they cannot prove that a model will obey it, so the original
  defense-in-depth limitation remains and is not a basis for widening terminal
  or future attempt exposure.
- **L2 — Resolved.** The generated contract now says never to follow symlinks
  anywhere in the bundle and to skip a file if any component of its path passes
  through a symbolic link (`app/services/lessons.py:410-412`). That is the
  explicit study-agent rule required by the frozen whole-bundle policy and by
  L2's fix direction. The verifier pins the no-symlink instruction
  (`verify.py:319-323`). Application readers still need the C3 per-segment
  enforcement already required by `docs/learn-bundle-spec.md`; that pre-existing
  implementation gap is outside this agent-brief fix and does not leave L2 open.

### New findings introduced by the fix

No new Critical, High, Medium, Low, or Info findings. The added wording does not
promote lesson content into trusted instructions, does not interpolate lesson
data into either generated entry-point file, and does not relax the existing
brief writer, workspace refusal, terminal opt-in, loopback peer, or exact
Host/Origin protections. The added verifier checks only inspect the generated
constant and introduce no attacker-controlled file access.

### Addendum verification

- `git diff --check eeb71f1^ eeb71f1` — passed.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile
  app/services/lessons.py verify.py` — passed.
- Focused generated-contract probe — passed after whitespace normalization: all
  source/attempt untrusted-data, brief-precedence, and per-component no-symlink
  clauses are present in the emitted template.
- `env -u ACTIVITY_DATA_DIR PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 timeout 55s
  .venv/bin/python -u verify.py` — inconclusive in this environment: the two
  terminal-wiring checks passed, then the run stalled and exited `124` at the
  bound with no failing assertion observed.

### Revised deploy verdict

**SAFE TO MAKE LIVE: YES for the lesson-agent teaching workflow under the
documented opt-in, loopback-only posture.** L1 and L2 are resolved in
`eeb71f1`, and the fix introduces no new findings. Wider network exposure
remains unsupported and was not made safe by prompt wording.

## Second addendum — combined re-review through `ff9a3f0`

**Scope and method:** re-applied the standing brief to the exact combined diff
`eeb71f1..ff9a3f0`. That range includes the first report and queue-drain docs in
`a7bf426` and `7b56740`, then the generated-brief and verifier changes in
`ecee1f2` and `ff9a3f0`; the docs-only commits do not alter the reviewed runtime
surface. The complete target versions of `app/services/lessons.py`, `verify.py`,
this report, and `docs/reviews/QUEUE.md` were re-read, together with the lesson
workspace/terminal call chain, frozen bundle discovery contract, and earlier
reports for this surface. The branch later advanced to `4b04757` while this
review was in progress; that commit is outside the requested target and is not
credited in this addendum's finding status or verdict.

### Previously resolved finding status

- **L1 — Remains resolved.** Neither follow-up commit weakens the instruction/data
  boundary added in `eeb71f1`. The brief still classifies source material,
  lesson pages, assets, attempt records, and learner files as untrusted data,
  rejects their embedded instructions and tool requests as directives, and
  gives the generated brief precedence (`app/services/lessons.py:399-409`). The
  new optionality wording for `attempts.jsonl` and the line-wrap-only change do
  not promote any learner content into trusted instructions
  (`app/services/lessons.py:364-368`, `app/services/lessons.py:427-430`).
- **L2 — Remains resolved.** The bundle-wide rule still says never to follow a
  symlink or any path passing through one (`app/services/lessons.py:410-412`),
  and the new attempts-specific sentence also says symlinks are skipped
  (`app/services/lessons.py:423-426`). The incomplete non-symlink enumeration
  rule below is a distinct new finding; it does not reopen L2's link-following
  path.

### New findings in the combined review

#### N1 — Attempts pre-read weakens the frozen entry and file-type bounds (Low, confirmed)

The frozen discovery contract requires a lexicographic walk with depth at most
four, **at most 512 entries per root**, **regular files only**, symlinks skipped,
and files over 2 MiB listed but not read
(`docs/learn-bundle-spec.md:387-404`). The generated brief at `ff9a3f0` instead
says "at most 512 files per root" and never tells the agent to restrict reads to
regular files (`app/services/lessons.py:423-426`). The file count does not bound
directory entries traversed before files are found, and a FIFO, socket, or other
non-regular entry can still be selected by the mandatory attempts pre-read. A
FIFO can block the lesson agent indefinitely; broader non-regular reads also
break the deterministic, bounded view that every consumer is supposed to
share.

This is Low under today's loopback, same-user, non-importable-bundle posture:
planting the entry currently requires local filesystem access and the learner
must launch the agent. It becomes a stronger availability and local-resource
boundary concern for the less-trusted/imported bundle posture already
anticipated by the contract. The verifier does not close the gap: its attempts
predicate pins only the depth and 2 MiB phrases, not the 512-entry or
regular-file requirements (`verify.py:310-316`), so the incomplete contract is
green at `ff9a3f0`.

*Fix direction:* mirror the frozen wording exactly: cap enumeration at 512
entries per root and admit regular files only, explicitly skipping FIFOs,
sockets, and other non-regular entries as well as symlinks. Pin both semantics
in the generated-contract regression check and re-review the dedicated fix.

No other new Critical, High, Medium, Low, or Info findings were found. The
range does not change a listener, route, PTY/WS lifecycle, filesystem writer,
preview policy, metadata interpolation path, or terminal trust gate.

### Second-addendum verification

- `git diff --check eeb71f1..ff9a3f0` — passed.
- Exact-target syntax compilation of `app/services/lessons.py` and `verify.py`
  via Python's in-memory `compile()` — passed at both `ecee1f2` and `ff9a3f0`.
- Exact-template probe — at `ecee1f2`, all newly added optionality/depth/size
  phrases are present but the verifier's literal `never write or rewrite it`
  anchor is absent because a newline splits it; the attempts-conventions
  predicate is therefore false. At `ff9a3f0`, the anchor and full predicate are
  true. This reproduces the reported 379+1 red intermediate state and the
  anchor repair behind the reported 380 green final state.
- `env -u ACTIVITY_DATA_DIR PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 timeout 180s
  .venv/bin/python -u verify.py` in clean archive exports of both commits —
  inconclusive in this environment: each run passed the two terminal-wiring
  checks, then stalled and exited `124` without a visible failing assertion.
  The focused exact-commit probe above independently confirms the specific
  red-to-green transition, but these bounded runs do not independently claim
  the complete totals.

### Second revised deploy verdict

**SAFE TO MAKE LIVE: NO for the exact lesson-agent teaching workflow at
`ff9a3f0`.** L1 and L2 remain resolved, and the intermediate verifier regression
is repaired, but N1 leaves the mandatory attempts pre-read broader than the
frozen deterministic discovery contract. Land and re-review a dedicated fix
before treating this target as deployable. Wider network exposure remains
unsupported regardless of prompt wording.

## Third addendum — re-review of discovery-contract fix `4b04757`

**Scope and method:** re-applied the standing brief to the exact fix diff
`ff9a3f0..4b04757`; `4b04757` is also the current branch head. The range changes
only the attempts-discovery sentence in the constant generated lesson brief.
The complete target lesson service and its workspace/terminal call chain were
re-read together with the frozen artifact-discovery contract, verifier checks,
and earlier reports for this surface. No listener, route, PTY/WS lifecycle,
filesystem operation, preview policy, or attacker-controlled interpolation path
changes in this range.

### Finding status

- **L1 — Remains resolved.** The diff does not change the instruction/data
  boundary: learner answers, source material, lesson pages, assets, attempt
  records, and learner files remain untrusted data whose embedded directives do
  not override the generated brief (`app/services/lessons.py:364-368`,
  `app/services/lessons.py:399-409`).
- **L2 — Remains resolved.** The bundle-wide per-component no-symlink rule is
  unchanged (`app/services/lessons.py:410-412`), and the repaired attempts
  sentence continues to require skipping symlinks
  (`app/services/lessons.py:423-427`).
- **N1 — Resolved.** The brief now mirrors the material frozen discovery bounds:
  depth at most four, at most 512 **entries** per root, **regular files only**,
  symlinks/FIFOs/sockets skipped, and files over 2 MiB listed but not read
  (`app/services/lessons.py:423-427`; `docs/learn-bundle-spec.md:398-400`).
  "Regular files only" also excludes other non-regular filesystem objects, so
  the mandatory pre-read no longer admits the blocking or non-deterministic
  objects identified by N1.

### New findings introduced by the fix

No new Critical, High, Medium, Low, or Info findings. The change narrows the
agent's permitted discovery behavior and adds no executable file-discovery
implementation or new trust input. The existing verifier predicate still pins
only the depth and 2 MiB portions of this sentence, not the repaired 512-entry
and regular-file clauses (`verify.py:310-316`). That regression-coverage gap was
already identified in N1 and is not new behavior introduced by `4b04757`; it
does not leave the generated production contract incomplete.

### Third-addendum verification

- `git diff --check ff9a3f0..4b04757` — passed.
- Exact-target syntax and template probe — passed: `4b04757` contains both
  `at most 512 entries per root` and `regular files only (skip symlinks, FIFOs,
  sockets)`, while the obsolete `512 files` wording is absent.
- Exact-base regression probe — passed: `ff9a3f0` contains `512 files per root`
  and lacks the regular-files-only clause, reproducing the reviewed defect.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile
  app/services/lessons.py verify.py` — passed.

### Third revised deploy verdict

**SAFE TO MAKE LIVE: YES for the lesson-agent teaching workflow under the
documented opt-in, loopback-only posture.** L1 and L2 remain resolved, N1 is
resolved by `4b04757`, and the fix introduces no new findings. Wider network
exposure remains unsupported and was not made safe by prompt wording.

## Fourth addendum — re-review of all-artifact-roots fix `ba2bc3c`

**Scope and method:** re-applied the standing brief to the exact fix diff
`4b04757..ba2bc3c`. The range changes only the constant generated lesson brief:
the mandatory learner pre-read and bundle-layout description now cover every
manifest-declared artifact root rather than only `attempts/`. The complete
target lesson service, terminal workspace caller, frozen manifest/path and
artifact-discovery contract, exact-target verifier, and earlier reports for
this surface were re-read. No listener, route, PTY/WS lifecycle, filesystem
writer, preview policy, or metadata-interpolation path changes in this range.

### Previously resolved finding status

- **L1 — Remains resolved.** The expanded pre-read keeps learner-authored files
  classified as data, never instructions (`app/services/lessons.py:364-370`),
  while the general boundary still makes everything read during tutoring
  untrusted and preserves the generated brief's precedence
  (`app/services/lessons.py:403-411`). Additional valid artifact roots therefore
  do not reopen the stored/indirect instruction channel identified by L1.
- **L2 — Remains resolved.** The bundle-wide rule still rejects every path that
  passes through a symlink (`app/services/lessons.py:412-414`), and the
  per-root discovery sentence still skips symlinks
  (`app/services/lessons.py:425-431`). N2 below concerns a raw absolute or
  traversal-shaped root with no symlink; it is distinct from L2's link-following
  path.
- **N1 — Remains resolved.** Every named root retains the repaired depth-four,
  512-entry, regular-file-only, non-regular-file skip, and 2 MiB read bounds
  (`app/services/lessons.py:425-431`). Extending those same rules per root does
  not re-admit the FIFO/socket or unbounded-entry behavior fixed by `4b04757`.

### New findings introduced by the fix

#### N2 — Raw declared roots are not constrained to valid in-bundle paths (Low, confirmed)

The frozen contract does not treat every raw `artifact_roots[]` value as a
root. It accepts at most eight directory paths, requires each to be a
bundle-relative POSIX path, rejects absolute paths and `.`/`..` segments,
backslashes, control/empty segments, trailing slashes, and reserved names, and
drops invalid or overlapping roots from the read model
(`docs/learn-bundle-spec.md:155-156`,
`docs/learn-bundle-spec.md:180-190`,
`docs/learn-bundle-spec.md:370-385`,
`docs/learn-bundle-spec.md:465-475`). The new trusted brief instead directs the
agent to read files under **every declared** artifact root
(`app/services/lessons.py:364-367`) and repeats that a v2 manifest may declare
more roots (`app/services/lessons.py:425-431`) without telling this consumer to
apply the path grammar or use only roots that survive validation.

The opening instruction to work only inside the bundle
(`app/services/lessons.py:341-343`) is useful but leaves a conflict for an
invented raw root such as an absolute path or a `../outside-note`-shaped value:
the later, specific pre-read says to traverse it, and the no-symlink rule does
not reject a direct absolute or parent-relative path. An OS-capable lesson agent
that resolves the manifest value literally can therefore read outside-bundle
files into model context, breaking the same local-data boundary the artifact
root is meant to establish. This is Low under today's direct-loopback,
same-user, manually launched, non-importable-bundle posture; it becomes more
material when less-trusted/imported v2 bundles arrive.

The exact-target verifier checks the depth and size phrases but not
`artifact_roots`, root validation, or in-bundle containment
(`verify.py:310-316`), so it remains green when this guard is absent.

*Fix direction:* tell the agent to enumerate only artifact roots that satisfy
the frozen path grammar and survive manifest validation, never raw declared
values; explicitly skip absolute, `.`/`..`-bearing, reserved, non-directory,
overlapping, over-limit, or otherwise invalid roots, and never resolve a root
outside the bundle. Preserve default/injected `attempts/`, the per-root
discovery limits, untrusted-data treatment, read-only rule, and no-symlink rule.
Pin the path-validity/containment condition in the generated-contract regression
check and re-review the dedicated fix.

No other new Critical, High, Medium, Low, or Info findings were found. The diff
does not alter execution code or relax the terminal and brief-publication
protections confirmed by the earlier reviews.

### Fourth-addendum verification

- `git diff --check 4b04757..ba2bc3c` — passed.
- Exact-target syntax compilation of `app/services/lessons.py` via Python's
  in-memory `compile()` — passed.
- Exact-template target probe — confirmed that `ba2bc3c` names every declared
  `artifact_roots` entry, retains default `attempts/`, and applies the same
  read-only/data, depth, entry-count, file-type, symlink, and size rules to each
  root.
- Exact-template base/guard probe — confirmed that `4b04757` names only
  `attempts/`, while `ba2bc3c` adds no bundle-relative/path-grammar validation
  for the newly trusted manifest values. Exact-target `verify.py` likewise has
  no artifact-root or root-containment anchor.

### Fourth revised deploy verdict

**SAFE TO MAKE LIVE: NO for the exact lesson-agent teaching workflow at
`ba2bc3c`.** L1, L2, and N1 remain resolved, and the valid-root coverage is
correct, but N2 lets a malformed manifest steer the mandatory pre-read beyond
the frozen artifact-root boundary. Land and re-review a dedicated path-validity
guard before clearing this target. Wider network exposure remains unsupported
regardless of prompt wording.

## Fifth addendum — re-review of artifact-root containment fix `2851f69`

**Scope and method:** re-applied the standing brief to the exact diff
`ba2bc3c..2851f69`; `2851f69` is the current branch head. The runtime change is
limited to the constant generated lesson brief, with accompanying verifier
anchors and review/queue bookkeeping. The complete target lesson service,
verifier, lesson-terminal caller, frozen path/root/discovery rules, and earlier
reports for this surface were re-read. No listener, route, PTY/WS lifecycle,
filesystem writer, preview policy, metadata interpolation path, or executable
artifact reader changes in this range.

### Finding status

- **L1 — Remains resolved.** The diff does not weaken the instruction/data
  boundary: learner answers, source material, lesson pages, assets, attempt
  records, and learner files remain untrusted data whose embedded directives
  cannot override the generated brief (`app/services/lessons.py:364-370`,
  `app/services/lessons.py:403-411`).
- **L2 — Remains resolved.** The bundle-wide per-component no-symlink rule is
  unchanged (`app/services/lessons.py:412-414`), and every counted artifact root
  retains the regular-file-only rule that explicitly skips symlinks
  (`app/services/lessons.py:431-434`).
- **N1 — Remains resolved.** The target still states depth at most four, at most
  512 entries per root, regular files only, non-regular entries skipped, and
  files over 2 MiB listed but not read (`app/services/lessons.py:431-434`). The
  repaired verifier predicate now pins the entry-count and regular-file phrases
  as well (`verify.py:310-320`).
- **N2 — Narrowed, but not fully resolved (Low remains open).** The direct
  containment escape reported by N2 is fixed: the brief now rejects absolute
  roots and roots containing `.`/`..` segments, ignores values that do not meet
  its stated condition, and says that manifest content never permits leaving
  the bundle (`app/services/lessons.py:425-430`). A literal absolute or
  parent-relative root therefore no longer conflicts with the opening
  bundle-only instruction.

  The finding's required valid-root/read-model condition is still incomplete.
  The frozen grammar also requires a 1–200-character POSIX path with no
  backslash, controls, empty segments, or trailing slash, and forbids paths
  nested under a reserved name (`docs/learn-bundle-spec.md:180-190`). The root
  rules further cap `artifact_roots` at eight, reject an over-limit manifest,
  and drop nested/overlapping roots from the read model
  (`docs/learn-bundle-spec.md:148-156`,
  `docs/learn-bundle-spec.md:370-385`,
  `docs/learn-bundle-spec.md:457-475`). The generated brief names none of those
  conditions: for example, a ninth otherwise relative directory or an
  overlapping root still "counts" under its text. Because the 512-entry limit
  is per root, reading every such raw declaration removes the frozen aggregate
  bound and can make a malformed/imported bundle drive duplicate or excessive
  traversal. No implemented v2 reader normalizes the manifest before the study
  agent sees it; the brief is the consumer's only rule today.

  The verifier remains only a partial anchor. It requires the strings
  `artifact_roots` and `never absolute`, but does not pin the no-dot-segment,
  invalid-value-ignore, reserved-name, bundle-containment, eight-root, or
  disjoint-root conditions (`verify.py:310-320`). It would therefore stay green
  if most of the new guard or the remaining read-model bounds were absent. This
  is regression-coverage evidence for the still-open N2, not a separate
  finding.

### New findings introduced by the fix

No new distinct Critical, High, Medium, Low, or Info findings. The unresolved
root-set validation and aggregate-bound gap is part of N2 as already described
in the fourth addendum and its fix direction; the new wording materially narrows
that finding by closing absolute and parent-traversal escapes. The queue
bookkeeping assertion that no finding remains open is not credited by this
independent re-review.

### Fifth-addendum verification

- `git diff --check ba2bc3c..2851f69` — passed.
- Exact-target in-memory syntax compilation of `app/services/lessons.py` and
  `verify.py` — passed.
- Exact-template base/target probe — passed: `ba2bc3c` has the per-root
  discovery bounds but no root-validity guard; `2851f69` adds the relative,
  absolute/dot-segment, reserved-name, ignore-invalid, and stay-in-bundle
  phrases while preserving the untrusted-data, read-only, no-symlink, depth,
  entry-count, file-type, and size rules.
- Exact-target root-set probe — confirmed that neither the generated brief nor
  its verifier predicate states the eight-root cap or disjoint/overlap handling;
  the verifier also does not anchor most of the newly added containment text.
- `env -u ACTIVITY_DATA_DIR PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 timeout 55s
  .venv/bin/python -u verify.py` — inconclusive in this environment: the two
  terminal-wiring checks passed, then the run stalled and exited `124` without
  a visible failing assertion.

### Fifth revised deploy verdict

**SAFE TO MAKE LIVE: NO for the exact lesson-agent teaching workflow at
`2851f69`.** L1, L2, and N1 remain resolved, and N2's direct outside-bundle
escape is closed, but N2 remains open because raw roots still do not have to
survive the frozen complete path and root-set validation before the mandatory
pre-read. Require the normalized valid-root set (including the eight-root and
disjoint-root rules), pin that condition, and re-review before clearing the
workflow. Wider network exposure remains unsupported regardless of prompt
wording.

## Sixth addendum — CLOSING re-review at branch head `89adcbc`

**Scope and method:** re-applied the standing brief to the exact diff
`2851f69..89adcbc`; the exact current branch head is
`89adcbc118839a4f4e7b16a92543d4d6be2346ed`. The range is one commit: it appends
the fifth addendum and adds the remaining root-set rules to the constant
generated lesson brief. The complete target lesson service, exact verifier,
lesson-terminal caller, frozen path/root/discovery contract, and earlier reports
for the same surface were re-read. No listener, route, PTY/WS lifecycle,
filesystem writer, preview policy, metadata interpolation path, or executable
artifact reader changes in this range.

### Closing finding status

- **L1 — Remains resolved.** Learner answers and files remain data, never
  instructions (`app/services/lessons.py:364-370`), and the general boundary
  still classifies source material, pages, assets, attempt records, and learner
  files as untrusted data whose embedded directives cannot override the brief
  (`app/services/lessons.py:403-411`). The new root-set qualifiers do not add a
  trusted content source.
- **L2 — Remains resolved.** The whole-bundle per-component no-symlink rule is
  unchanged (`app/services/lessons.py:412-414`), and every counted root retains
  the regular-file-only rule that explicitly skips symlinks and other named
  non-regular entries (`app/services/lessons.py:431-435`).
- **N1 — Remains resolved.** The mandatory learner-file pre-read still carries
  depth at most four, at most 512 entries per root, regular-files-only,
  non-regular-entry skip, and 2 MiB read bounds
  (`app/services/lessons.py:431-435`). Nothing in the diff broadens those
  per-root limits.
- **N2 — Resolved.** `2851f69` had already closed the direct containment escape
  by limiting counted roots to bundle-relative directory paths, rejecting the
  named absolute/dot-segment/reserved cases, ignoring invalid values, and
  requiring the agent to stay inside the bundle. The Low residual in the fifth
  addendum was the root-set behavior: overlapping declarations could still be
  counted and an over-eight list could still drive the mandatory pre-read.
  `89adcbc` now says that a root nested under another root does not count and
  that more than eight roots invalidate the manifest
  (`app/services/lessons.py:425-435`). Those are the frozen disjoint-root drop
  and list-limit rejection semantics
  (`docs/learn-bundle-spec.md:370-385`,
  `docs/learn-bundle-spec.md:465-475`). The earlier instruction to read declared
  roots is therefore qualified by a valid, disjoint set of at most eight roots;
  the per-root discovery limits no longer multiply across nested duplicates or
  an over-limit manifest.

The exact verifier still does not pin the new eight-root and nested-root
phrases (`verify.py:310-320`). That is the already documented regression-check
limitation, not a new behavior or a remaining production-contract defect: the
target generated brief itself now contains both missing semantics, as the
focused exact-template probe confirms.

### NEW findings

No new Critical, High, Medium, Low, or Info findings. The runtime change narrows
the agent's permitted manifest interpretation, and the appended historical
review text changes no executable or trust-boundary behavior. The constant
brief, atomic publication path, fail-closed lesson-workspace preparation,
terminal opt-in and trust gates, and prior instruction/data and filesystem
protections remain intact.

### Sixth-addendum verification

- `git diff --check 2851f69..89adcbc` — passed.
- Exact-target in-memory syntax compilation of `app/services/lessons.py` and
  `verify.py` — passed.
- Exact base/target root-set probe — passed: `2851f69` lacks both closing
  clauses; `89adcbc` contains both “a root nested under another root does not
  count” and “more than eight roots invalidate the manifest.”
- Focused exact-template probe — passed: the target retains the untrusted-data,
  no-symlink, bundle-containment, depth, entry-count, regular-file, and size
  clauses together with both new root-set rules.
- `env -u ACTIVITY_DATA_DIR PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 timeout 55s
  .venv/bin/python -u verify.py` — inconclusive in this environment: both
  terminal-wiring checks passed, then the run stalled and exited `124` with no
  visible failing assertion, matching the limitation recorded by the earlier
  addenda.

### Final deploy verdict

**SAFE TO MAKE LIVE: YES for the exact lesson-agent teaching workflow at branch
head `89adcbc`, under the documented terminal opt-in and direct-loopback-only
posture.** L1, L2, and N1 remain resolved; N2's final Low root-set residual is
resolved; and this closing pass found no new findings. Wider network exposure
remains unsupported and was not made safe by prompt wording.

## Seventh addendum — `38dd11b`

Written by the session converging this drain, documenting resolution (not an
adversarial pass). After the closing verdict at `89adcbc`, the PR review bot's
round-6 pass raised one more contract-fidelity finding against the brief: the
pre-read and layout sections said "default `attempts/`", which reads as
declared-list-or-default — but the frozen read model (learn-bundle-spec.md §7)
always injects `attempts` into the normalized root set when a declared
`artifact_roots` list omits it, so a tutor following the brief literally could
adapt the lesson without reading learner work the app records there.

`38dd11b` is a wording-only change to `_AGENTS_TEMPLATE` (no code paths): both
passages now state that `attempts/` always counts as an artifact root, even
when the manifest's declared list omits it. No new capability, path, or bound
is introduced; the change narrows the gap between the brief and the frozen
read model. verify 380 passed, 0 failed on `38dd11b`.

The closing deploy verdict is unchanged and extends to this head: safe for the
documented terminal opt-in, direct-loopback-only posture; wider exposure
remains unsupported.

## Eighth addendum — standing-brief pass over 38dd11b

**Scope and method:** independently re-applied the standing brief to the exact
one-commit diff `38dd11b^..38dd11b`, then read the complete current
`app/services/lessons.py`, the full generated `_AGENTS_TEMPLATE`, its atomic
publication and lesson-terminal caller, the frozen artifact-root/read-model
contract, the exact verifier anchors, and the earlier reports for this surface.
The current `app/services/lessons.py` and `verify.py` blobs are byte-identical to
their versions at `38dd11b`; the only later commit is review/queue
documentation. No listener, route, PTY/WS lifecycle, filesystem operation,
preview policy, metadata interpolation path, or executable artifact reader
changes in the reviewed delta.

### Finding status

- **L1 — Remains resolved.** The new mandatory read covers the fixed
  `attempts/` root when a v2 manifest's declared list omits it, but the learner's
  content remains data, never instructions (`app/services/lessons.py:364-371`),
  and the general boundary still classifies attempt records and learner files
  as untrusted data whose embedded directives cannot override the generated
  brief (`app/services/lessons.py:402-412`). The delta therefore does not
  re-open the stored/indirect instruction channel.
- **L2 — Remains resolved.** `attempts/` is a fixed in-bundle root rather than a
  manifest-selected outside path, and the unchanged whole-bundle rule still
  rejects any path that passes through a symlink
  (`app/services/lessons.py:413-415`). The per-root discovery sentence also
  continues to skip symlinks and other named non-regular entries
  (`app/services/lessons.py:430-439`).
- **N1 — Remains resolved.** Unconditionally including `attempts/` does not
  weaken the depth-four, 512-entry, regular-file-only, non-regular-entry skip,
  or 2 MiB read bounds that apply to every artifact root
  (`app/services/lessons.py:430-439`).
- **N2 — Remains resolved.** The delta distinguishes the injected fixed
  `attempts/` root from additional manifest-declared roots. Those declarations
  still count only after the existing in-bundle path, reserved-name,
  disjointness, and list-limit rules are applied
  (`app/services/lessons.py:426-439`). Stating that `attempts/` remains in the
  read model when omitted matches the frozen contract exactly
  (`docs/learn-bundle-spec.md:370-385`) and does not grant a manifest a new path
  or escape primitive.

### New findings

No new Critical, High, Medium, Low, or Info findings. The change closes the
declared-list-or-default ambiguity without weakening the previously reviewed
instruction/data, filesystem, publication, terminal opt-in, or Host/Origin
boundaries. The exact verifier still does not pin either new attempts-injection
phrase (`verify.py:310-320`), so its reported 380-check result would not by
itself distinguish this target from its parent. Consistent with the earlier
addenda's treatment of unpinned root-set clauses, that is a regression-coverage
limitation rather than a defect in the target production contract; the focused
base/target probe below checks this delta directly.

### Eighth-addendum verification

- `git diff --check 38dd11b^ 38dd11b` — passed.
- Exact runtime-blob comparison — passed: both
  `38dd11b:app/services/lessons.py` and `38dd11b:verify.py` have the same object
  ids as their current-HEAD counterparts.
- Exact-target in-memory syntax compilation of `app/services/lessons.py` with
  Python 3.10 — passed.
- Exact base/target template probe — passed: the parent lacks both explicit
  attempts-injection clauses; `38dd11b` contains them in the learner pre-read
  and bundle-layout sections while retaining the untrusted-data, no-symlink,
  root-validity, disjointness, list-limit, depth, entry-count, regular-file, and
  size clauses.
- Full source/contract scan — confirmed that the added fixed root matches the
  frozen default/injection semantics, including the informational
  `missing-attempts-root` read-model repair
  (`docs/learn-bundle-spec.md:370-400`,
  `docs/learn-bundle-spec.md:457-483`).
- `env -u ACTIVITY_DATA_DIR PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 timeout 90s
  /home/aina/projects/ephemeris/.venv/bin/python -u verify.py` — inconclusive in
  this worktree: both terminal-wiring checks passed, then the run made no
  further visible progress and exited `124` at the bound with no failing
  assertion observed. This pass does not independently claim the full 380-check
  total recorded by the converging session.

### Eighth revised deploy verdict

**SAFE TO MAKE LIVE: YES for the exact lesson-agent teaching workflow at
`38dd11b`, under the documented terminal opt-in and direct-loopback-only
posture.** L1, L2, N1, and N2 remain resolved, the missing `attempts/`
read-model rule is now explicit, and this standing-brief pass found no new
finding. Wider network exposure remains unsupported and was not made safe by
prompt wording.

## Ninth addendum — standing-brief pass over 9dc0fc6

**Scope and method:** independently re-applied the standing brief to the exact
one-commit diff `9dc0fc6^..9dc0fc6`. The delta adds one paragraph to the
constant generated `_AGENTS_TEMPLATE`; no executable parser, writer, listener,
route, PTY/WS lifecycle, preview policy, metadata interpolation path, or
filesystem operation changes. The complete current `app/services/lessons.py`
and full generated template were read together with the atomic publication and
lesson-terminal caller, the frozen manifest writer contract, the exact verifier
anchors, and the earlier reports for this surface.

### Finding status

- **L1 — Remains resolved.** The new paragraph describes unrecognized manifest
  fields as data to preserve; it does not promote their contents to
  instructions. The unchanged metadata boundary still treats `lesson.json`
  values and everything else read while tutoring as untrusted data whose
  embedded directives cannot override the generated brief
  (`app/services/lessons.py:402-415`). Preserving an unknown value therefore
  does not re-open the stored/indirect instruction channel.
- **L2 — Remains resolved.** Requiring an agent to preserve unknown fields when
  it edits `lesson.json` does not override the whole-bundle rule to skip every
  path that passes through a symlink (`app/services/lessons.py:413-415`). The
  delta changes no application-side path resolution or write primitive.
- **N1 — Remains resolved.** The paragraph does not change artifact discovery.
  Every counted root retains the depth-four, 512-entry, regular-file-only,
  non-regular-entry skip, and 2 MiB read bounds
  (`app/services/lessons.py:426-439`).
- **N2 — Remains resolved.** The existing bundle-relative path, reserved-name,
  disjointness, and eight-root rules remain intact
  (`app/services/lessons.py:426-439`). Unknown manifest fields do not become
  artifact-root declarations merely because the agent must preserve them.
- **Unknown-field writer gap — resolved.** The target now tells the agent to
  preserve every unrecognized top-level and nested field in relative order and
  to avoid regenerating `lesson.json` from only known keys
  (`app/services/lessons.py:463-467`). That carries the material §9.3 writer
  rule into the generated brief: unknown values survive semantically and
  unknown keys retain their original relative order
  (`docs/learn-bundle-spec.md:512-537`). It also composes with the immediately
  preceding immutable-version/identity rule and the following version-specific
  conventions rather than weakening either.

### New findings

No new Critical, High, Medium, Low, or Info findings. The change narrows the
agent's permitted manifest-editing behavior and adds no new input source,
capability, trust transition, path, or filesystem operation. The exact verifier
does not pin any part of the new unknown-field paragraph
(`verify.py:298-328`), so a reported full green total would not distinguish
this target from its parent. Consistent with the earlier addenda's treatment of
unpinned root-set clauses, that is a regression-coverage limitation rather than
a defect in the target production contract; the focused base/target probe below
checks this delta directly.

### Ninth-addendum verification

- `git diff --check 9dc0fc6^ 9dc0fc6` — passed.
- Exact delta-scope check — passed: `git diff --name-only` lists only
  `app/services/lessons.py`, and the diff changes only `_AGENTS_TEMPLATE`.
- Exact-target in-memory syntax compilation of `app/services/lessons.py` with
  Python 3 — passed.
- Exact base/target template probe after whitespace normalization — passed: the
  parent lacks the complete unknown-field paragraph; `9dc0fc6` contains its
  top-level/nested preservation, relative-order, and no-known-key-template
  clauses while retaining the untrusted-data, no-symlink, root-set,
  schema-version, and durable-identity rules.
- Full source/contract scan — confirmed that the new paragraph matches the
  frozen semantic-preservation and relative-key-order requirements and does not
  conflict with the canonical writer's allowance to normalize representation
  (`docs/learn-bundle-spec.md:512-540`).
- `env -u ACTIVITY_DATA_DIR PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 timeout 90s
  /home/aina/projects/ephemeris/.venv/bin/python -u verify.py` — inconclusive in
  this worktree: both terminal-wiring checks passed, then the run made no
  further visible progress and exited `124` at the bound with no failing
  assertion observed. This pass does not independently claim the full
  380-check total recorded in the commit message.

### Ninth revised deploy verdict

**SAFE TO MAKE LIVE: YES for the exact lesson-agent teaching workflow at
`9dc0fc6`, under the documented terminal opt-in and direct-loopback-only
posture.** L1, L2, N1, and N2 remain resolved, the unknown-manifest-field
writer rule is now explicit, and this standing-brief pass found no new finding.
Wider network exposure remains unsupported and was not made safe by prompt
wording.
