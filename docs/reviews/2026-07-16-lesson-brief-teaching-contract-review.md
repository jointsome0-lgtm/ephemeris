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
