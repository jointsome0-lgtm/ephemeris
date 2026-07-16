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
