# Security review queue

Pending adversarial security reviews for the sensitive surfaces: the terminal
PTY/WS core (`app/terminal.py` + `app/static/terminal.js`), the future
`app/agent/`, and anything about to be exposed on a live port.

How it works:

- Whoever lands a change touching those surfaces appends one entry under
  **Pending** — date, commits, paths, one factual line about what changed.
  Entries stay neutral: facts only, no threat analysis.
- Draining an entry = applying `docs/reviews/review-prompt.md` (the standing
  brief) to it and writing a report next to this file. The brief is handed to
  the reviewer by file reference, never restated inline. Entries drain in one
  batch before the restart that would carry them, not one pass per merge;
  entries over the same surface share a scope and a report. The drain
  diagnoses and does not repair: findings land in the report, the fix is an
  ordinary reviewed PR, and an entry reaches Done with that PR — so no code
  arrives on `main` without a second pair of eyes.
- Deploy gate: the live service does not restart with code whose entries are
  still Pending (AGENTS.md → Public-Safety Check).

Entry format: `- [ ] YYYY-MM-DD — <commits> — <paths> — <what changed>`

## Pending

- [ ] 2026-08-03 — `0b25126`..`113b1dd` on `fix/23-limits-retention`, merged to
  `main` as `898bbed` — `app/security.py`, `app/limits.py`,
  `tests/test_130_limits.py` (rest of the change: `app/db.py`,
  `app/settings.py`, eight modules under `app/services/`,
  `app/routers/export.py`, `app/routers/habits.py`, `scripts/backup_db.py`,
  `app/templates/`, `app/static/style.css`, `docs/`) — issue #23, a request-body ceiling added to the HTTP perimeter
  plus service-layer field caps, export retention and a read-only status
  section. `SecurityMiddleware` gains a body count on the four unsafe methods:
  `_body_ceiling()` runs at import and sets the module constant
  `MAX_BODY_BYTES` from `limits.MAX_BODY_BYTES` (2 MiB), overridable by the
  `EPHEMERIS_MAX_BODY_BYTES` environment variable; a non-integer, non-positive,
  or below-`limits.LARGEST_ROUTE_CAP + limits.BODY_CEILING_HEADROOM` value
  falls back to the constant. `_declared_length()` parses `Content-Length` and
  a declared value over the ceiling is answered 413 before the app is called.
  Otherwise `receive` is wrapped in the new `_CappedBody`, which sums the
  `body` of each `http.request` message as the app pulls it and, once over the
  limit, returns `http.disconnect` from then on (sticky, nothing buffered).
  When that has happened the send wrapper discards the app's own response and
  sends one plain-text 413 instead, and a `ClientDisconnect` propagating out of
  the app is converted to the same 413 unless it is a genuine one, which is
  re-raised. GET, HEAD and the WebSocket handshake path take neither wrapper.
  New module `app/limits.py` holds every write bound: the four existing title
  caps at unchanged values, five previously unbounded note fields (10 000
  chars, daily note 20 000), `MAX_BODY_BYTES` (2 MiB), `LARGEST_ROUTE_CAP`
  (512 KiB, spelled here rather than imported from `app/routers/learn.py`,
  with a test reading the four real Learn constants to check it is still the
  largest), `BODY_CEILING_HEADROOM` (1 MiB), `EXPORT_KEEP` (30),
  `EXPORT_GRACE`, `BACKUP_STALE_DAYS` (7) and `FREE_SPACE_FLOOR` (1 GiB).
  Enforcement is `limits.check` in the service layer, raising each caller's
  existing domain error; `POST
  /daily-note` gains its first failure branch (422 under `X-Partial`, else 303
  and a flash). `export_events` prunes to the 30 newest `events-*.jsonl` after
  each write, best-effort, with a directory fsync. New `app/services/storage.py`
  computes read-only figures rendered in a section of `/export` (database size
  including WAL sidecars, event count, newest parsing backup manifest with
  size and age, export count and total, free disk, three warning conditions).
  `Settings` gains `backups_dir`, exposed as `db.BACKUPS_DIR` and imported by
  `scripts/backup_db.py::_load_live_paths` instead of recomputing the path. At
  merge: `uv run pytest` 175 passed, `verify_restore.py` 34 passed 0 failed,
  public hygiene clean. Codex PR review approved at exact HEAD `113b1dd`. No
  service action was performed. Diagnosis-only drain 2026-08-04 →
  `docs/reviews/2026-08-04-limits-retention-review.md`: 2 Low findings remain
  open; the entry stays Pending for a separate reviewed repair PR.

## Done

- [x] 2026-08-02 — `06f6df5` and `63e7a1e` on `fix/terminal-job-control`,
  merged to `main` as `ddbdddb` — `app/terminal.py`, `tests/test_040_core_surfaces.py`,
  `tests/test_050_sandbox_learning.py`, `tests/test_060_role_runner.py`,
  `docs/reviews/QUEUE.md` — issue #116, two changes to the child-spawn path.
  (1) `_child_setup` becomes the factory `_child_setup_for(slave_fd)`: the
  parent resolves `os.ttyname(slave_fd)` and the `(fcntl, termios)` pair, and
  the returned closure runs post-fork as `os.setsid()`, `os.open(slave_path,
  os.O_RDWR)`, `TIOCSCTTY` on that descriptor, `os.close`. It previously ran
  `os.setsid()` then `TIOCSCTTY` on fd 0 under `except OSError: pass`. The
  closure raises instead of swallowing, so a failure aborts the spawn; both
  spawn sites in `_spawn_on_pty` pass the per-spawn closure, the factory call
  is wrapped to close both pty ends on `OSError`, and the non-sandboxed branch
  additionally catches `subprocess.SubprocessError` before closing its ends and
  returning `None`. `logging` and `subprocess` are new module imports and
  `_log` is a new module-level logger named `activity_ledger`; two spawn
  failure paths now emit `_log.warning`. (2) `_child_env(role="lesson-learner")`
  inserts `/usr/local/go/bin` into `PATH` after `{USER_HOME}/.local/bin`. Tests
  add a uvloop-scoped spawn of `/bin/sleep` asserting `os.tcgetpgrp` on the pty
  master, replace two `preexec_fn` identity assertions with `__qualname__`
  checks, and update the learner `PATH` assertion. Drained 2026-08-02 →
  `2026-08-02-terminal-job-control-review.md`: no Critical, High, Medium, Low,
  Info, or open finding. The uvloop regression and an invented `/tmp` probe
  through the real learner bubblewrap wrapper both confirmed a live foreground
  process group; failure paths retain complete PTY and assessment-capability
  cleanup, and the fixed learner `PATH` grants no new sandbox authority. Prior
  terminal, sandbox, role, and capability findings retain the report's stated
  dispositions. Final verdict: SAFE TO MAKE LIVE for the documented direct-
  loopback, single-worker, unauthenticated single-user deployment; wider,
  proxy-adjacent, or multi-user deployment NO. No service action was performed.

- [x] 2026-07-29 — `eabb9bb` and `f7f2877` on `fix/84-learning-output-style`,
  with queue-only bookkeeping in between; the entry stays current with the
  branch and the merge commit is appended before any restart —
  `app/services/lessons.py`, `app/services/bundle_schema.py`,
  `docs/learn-bundle-spec.md`, `tests/test_010_platform_ui.py`,
  `docs/reviews/QUEUE.md` — issue #84 adds a third generated file to the
  lesson-agent workspace. `prepare_terminal_workspace` now writes
  `<bundle>/.claude/settings.json` after the two existing briefs, through the
  same `_write_brief` writer: a `tempfile.mkstemp` 0600 file in the
  destination directory, `write` + `flush` + `os.fsync`, then `os.replace`
  onto the name, with the destination never opened and the temporary file
  unlinked on any exception. The content is the constant
  `{\n  "outputStyle": "Learning"\n}\n`; nothing from the lesson row or the
  manifest is interpolated into it. A new `_ensure_settings_dir` supplies the
  parent: it calls `os.unlink` when `<bundle>/.claude` is a symlink or an
  existing non-directory, then `os.mkdir(path, 0o700)`; on `FileExistsError`
  it re-tests the name and raises `NotADirectoryError` unless the name is now
  a non-symlink directory. `prepare_terminal_workspace` already catches
  `OSError` and answers `None`, so both the unlink failure and that raise
  return "no workspace". An existing real directory is kept and only
  `settings.json` inside it is written. `resolve_terminal_workspace` (the
  learner path) still writes nothing. `bundle_schema.RESERVED_NAMES` gains
  `.claude`, which `valid_v2_path` compares against the first path segment
  (equal to, or nested under, a reserved name) and which
  `lessons.bundle_resource_info` consults on its v1 branch — the v1 preview
  surface, which serves any non-reserved bundle-relative ref, therefore stops
  serving refs under `.claude`. The v2 effect runs through the path grammar
  instead: `valid_v2_path` previously accepted a path whose first segment was
  `.claude`, so a v2 manifest could declare `.claude/page.html` as a page and
  the v2 declared-page allowlist would serve it. Such a path is now
  `invalid-path` (or `invalid-entry` as an entry), and a manifest whose only
  page was such a path becomes rejected with `no-pages`. No bundle in the
  live data directory and no manifest fixture in the repository declares a
  path under `.claude`. Spec §2 gains the `.claude/`
  layout lines, `.claude` in the reserved-name list, and a paragraph stating
  the regenerated-never-authored rule and the app-owns-one-file-only scope.
  No schema migration, route, HTTP contract, sandbox profile, terminal trust
  gate, WS protocol, bridge ABI, `_AGENTS_TEMPLATE` text, `CLAUDE.md` shim,
  attempts or assessments machinery changed. The manifest schema v2 parser is
  unchanged as code; only its reserved-name input grew, with the accept/reject
  consequence stated above.
  Empirical basis recorded in the PR: with `claude` 2.1.220 and a session
  started in a directory outside any git repository, `claude doctor` names
  that directory's `.claude/settings.json` under "Invalid settings" when the
  file is malformed JSON, and identical `--print` prompts answer under the
  style named by the file in that directory. `f7f2877` carries the two Low
  findings of the independent correctness re-check: the preview-surface
  regression now builds a v1 manifest bundle, because the previous one ran
  against a v2 bundle whose declared-page allowlist refuses an undeclared
  path with or without the reserved name, and this entry's v2 compatibility
  account was corrected to the one above. No application code changed in that
  commit. Host verification at `f7f2877`:
  pytest 9 passed, verify_restore 28 passed, public hygiene clean. Python
  only; the merge does not change what the live process runs. Merged into
  `main` as `6209fa3` (PR #106) at branch tip `f4501d4`. The merged tree is
  NOT byte-identical to that tip: `main` advanced while the PR was open, by
  PR #103's Learn-router extraction and PR #110's attempts fix, so this was a
  real three-way merge. The five files this entry covers —
  `app/services/lessons.py`, `app/services/bundle_schema.py`,
  `docs/learn-bundle-spec.md`, `tests/test_010_platform_ui.py`,
  `docs/reviews/QUEUE.md` — are byte-identical between `f4501d4` and
  `6209fa3` (`git diff` over exactly those paths is empty), and neither
  incoming change touches them. Host verification on merged `main` at
  `6209fa3`: pytest 9 passed, verify_restore 28 passed, public hygiene clean.
  No restart was performed.
  Diagnosis-only drain 2026-07-29 →
  `2026-07-29-lesson-learning-output-style-review.md`: one Medium finding
  remains open, so this entry stays Pending for a separate reviewed repair PR.
  The frozen contract previously allowed `.claude` as an ordinary v1 file area
  and as a v2 artifact root; an invented v2 bundle accepted by the exact
  pre-#84 reader used `.claude/settings.json` as a block file, and the current
  real workspace open silently replaced those bytes with the generated
  Learning output-style JSON. The repair needs an owner decision on
  compatibility/versioning, non-destructive collision handling or migration,
  and exact v1/v2 regressions before this restart gate can open. No application
  code, merge, or restart was performed by the drain.
  The repair is on `fix/84-settings-collision`, carried by its own PR, and the
  owner took both decisions the drain asked for. Non-destructive handling:
  `_preserve_foreign` moves whatever sits at `<bundle>/.claude` or
  `<bundle>/.claude/settings.json` and did not come from this writer to
  `<name>.collision-<hex>` before the write — the same aside name and
  deterministic rule `app/services/assessments.py` already applies to its own
  reserved name. A node that is not an ordinary single-link file is moved
  unread, so a planted link or special file is neither followed nor opened;
  the previous `os.unlink` of such a node at `.claude` is gone. An ordinary
  file is compared with the constant and left in place when it matches, and
  the comparison reads only a file whose size already equals the constant's —
  so the writer's own output is republished in place and no aside accumulates
  per terminal open. Versioning: the reservation stays in v2 and is recorded
  as a named exception rather than driving a v3. Spec §2 states the aside
  rule, §9.2 records the v1 serving change as the second deliberate v1
  behavior change, and §9.3 records the reservation as one named exception to
  its own "a meaning change requires v3" rule, with the reason a v3 would not
  serve the purpose (the app writes into every bundle it prepares) and the
  statement that a bundle carrying the older shape loses a manifest binding,
  never its bytes. No schema, route, HTTP contract, sandbox profile, terminal
  trust gate, template or other application file changed. Coverage exercises
  the report's own scenario — a pre-reservation `.claude/settings.json`
  holding a learner artifact in a v1 bundle survives the first regen as an
  aside copy — plus the no-accumulation rule and both squatter kinds
  preserved rather than unlinked, with the symlink moved without being
  followed. Merged into `main` as `7e8a850` (PR #112) at branch tip
  `0a74699`; the merged tree is byte-identical to that tip (both trees
  `28058dd`). PR-bot round 1 makes an unreadable file of the constant's size a
  non-match rather than an error: `read_bytes` on a `chmod 000` node raised
  `PermissionError`, which `prepare_terminal_workspace` catches as a refusal
  of the whole workspace, so a lesson lost its terminal over one unreadable
  generated file; the read is now guarded and a failure falls through to the
  rename the writable bundle directory permits without opening the file. The
  same round removes a contradiction the repair introduced into the spec:
  §9.2's corrupt-manifest sentence called itself "the one deliberate v1
  behavior change" while the new paragraph called the reservation the second,
  so the earlier sentence now names itself the first and points at the other.
  Host verification at the round-1 branch state: pytest 9 passed,
  verify_restore 28 passed, public hygiene clean. The entry stays Pending for
  the owner's re-drain; no merge or restart was performed.
  Diagnosis-only resolution re-drain 2026-07-29 →
  `2026-07-29-lesson-learning-output-style-review.md`: zero new findings. The
  superseding section reviews `main` at `083fa36` / tree `3f69767` and finds
  the prior Medium resolved: foreign bytes are moved aside before either the
  `.claude` directory name or its `settings.json` name is replaced, an
  unreadable same-sized file is treated as foreign, and the v1/v2
  compatibility exception is explicit in the spec. An invented-data probe of
  the exact formerly valid v2 artifact-root/block shape confirmed that the
  current reader reports `invalid-path`, the generated setting is exact, and
  the learner bytes survive in one collision aside. The prior request for a
  durable test of that exact v2 shape remains open as non-blocking
  verification debt: the suite pins the v1 shape and the shared writer, not
  that manifest spelling. The repaired tree is safe only for the documented
  direct-loopback, single-worker, unauthenticated single-user posture once the
  owner closes this entry. The Pending box is unchanged; no application code,
  merge or restart was performed by the re-drain.
  Closure 2026-07-29, taken by the owner in this session (the R1 brief's
  zero-findings branch, reconfirmed in-session before this line was written):
  the re-drain read `main` itself at `083fa36` / tree `3f69767`, so the
  reviewed bytes are the merged bytes and no branch-to-merge tree comparison
  is owed. The report's one unmet request — a durable regression constructing
  the exact formerly valid v2 artifact-root/block manifest — is recorded as
  test-only debt and is NOT treated as a restart blocker: `_preserve_foreign`
  branches on the node's kind and bytes, not on manifest version or read
  outcome; the merged suite pins the v1 shape and the shared writer; and the
  review's own invented-data probe established the current v2 behaviour. It is
  left for a later test-only change rather than repaired here, under the
  concrete-failure rule — no failure of the deployed app is named. The
  owner-level versioning decision it descends from was already taken and is
  frozen in spec §9.3. Independent host verification on `main` at `083fa36`:
  pytest 9 passed, verify_restore 28 passed. The restart gate for this entry
  is open.

- [x] 2026-07-28 — `a0ae9dd`, `981400a`, `c91d002`, `18d4195`, `b8b3e02`,
  `5bcd585`, `89f0b77`, with queue-only bookkeeping through `ce2ad38` and the
  PR-bot round-6 through round-8 repairs at the current
  `fix/4-s4-record-panel` branch tip; the owner selected branch head `b8b3e02`
  for the drain of 2026-07-28, and the later code commits are NOT covered by
  that pass — the entry stays current with the branch, and the eventual merge
  commit is appended before any restart —
  `app/main.py`, `app/services/assessments.py`, `app/services/attempts.py`,
  `app/services/focus.py`, `app/templates/learn.html`, `app/static/style.css`,
  `verify.py`, `docs/reviews/QUEUE.md` — issue #4 phase S slice s4 renders the
  recorded assessments on `/learn`. `GET /learn` computes one additional
  context object for the selected lesson: evidence per concept, the latest
  summary with its `next_action`, one entry per declared question carrying its
  latest attempt and the latest active review of that attempt, an entry per
  attempted question absent from the manifest, and a counts line. The values
  come from four reads — `assessments.panel_state` (the existing active-rows
  query, the fold, a count of active non-`retraction` rows, and a new
  `GROUP BY attempt_id` count of every `review` row),
  `attempts.lesson_attempt_summary` (a `COUNT(*)` and one row per question at
  `MAX(id)`), `focus.lesson_total` (`SUM(seconds)` over
  `focus_sessions.lesson_id`), and `lessons.read_bundle_readonly` for the
  declared question list. `active_state`'s body moved unchanged into a pure
  `fold_rows` that both it and `panel_state` call. A rejected manifest read
  yields no declared list, no retired entries, and the attempted questions
  under their durable ids. The learner's answer is cut to its first 400
  characters for display; notes, `next_action`, labels, concept refs and page
  ids are passed through whole. The template renders every one of those values
  through `{{ }}` with Jinja autoescape on, adds no `|safe`, no markdown, no
  script and no new JS file, and wraps the block in
  `{% if selected.record is defined %}`. Concept refs, levels, modes and the
  `stale` flag also reach `class` and `title` attributes. `style.css` gains the
  `.lesson-record` / `.rec-*` rules inside the Learn block plus three lines in
  the existing 860px media query. No POST route, write path, projection,
  service write function, schema, event, capability, brief, sandbox surface,
  terminal file or bridge ABI is touched, and no other template changed.
  `c91d002` (PR-bot round 1) replaces the earlier-review marker's
  total-minus-one arithmetic with a count of the review ids preceding the
  displayed one, narrows the per-question attempt query from `SELECT *` to the
  displayed columns with the excerpt bound applied in SQL, and splits attempted
  questions three ways instead of two: a question the manifest read dropped
  under a DEGRADED finding but the raw document still declares is rendered as
  a question of unknown validity rather than as retired, so only a question
  absent from the document reaches the retired block. `18d4195` (independent
  correctness re-check) excludes a `retraction`-struck review from that count
  regardless of the order the two rows were written in, and takes both the
  excerpt bound and the truncation comparison over `CAST(answer AS BLOB)` —
  SQLite's TEXT `substr`/`length` stop at an embedded NUL, which attempt
  validation admits, so an answer containing one was previously cut to the
  bytes before it with no truncation marker; the byte budget is the character
  bound times four plus three, and Python decodes with `errors="ignore"` and
  cuts to the character bound. The same commit puts a double quote and angle
  brackets into the escaping fixture's note text, which the chip renders into a
  `title` attribute, and asserts the escaped attribute form. PR-bot round 3
  adds two more read-side distinctions. A manifest whose `questions` value is
  not a list is DEGRADED rather than rejecting and yields an empty typed list,
  which the previous split read as "the author declares none": every attempted
  question was marked retired. The document reader now answers `None` for a
  wrong-typed value and for a non-object manifest, an absent `questions` key
  still answers the empty set, and the panel treats `None` exactly as it
  treats a rejected read. Separately, a declared question's row took its page
  from the manifest's current binding while showing an answer recorded against
  whatever page held the question then; a question may move pages and the
  stored `stale` flag was decided at record time, so the row now shows the
  attempt's own `page_id` and carries the current binding beside it as a
  `moved` marker, with `page_id=None` passed for the retired and unvalidated
  rows that have no readable current binding. `style.css` gains one
  `.rec-moved` rule. Verify 870, verify_restore 28.
  Diagnosis-only drain 2026-07-28 →
  `2026-07-28-lesson-record-panel-review.md`: one Low finding remains open;
  this entry stays Pending for a separate reviewed repair PR. The finding is
  the explicit-null spelling of the wrong-typed declaration case:
  `"questions": null` is conflated with an absent key, so the panel presents
  every historical attempted question as retired without observing a list
  from which absence could be established. No merge or restart was performed.
  The repair is `5bcd585` on the same unmerged branch, carried by the open PR
  #92 rather than a separate one: the branch has not landed, so the repair
  reaches `main` only through that PR's own bot verdict at its exact head, and
  the second pair of eyes the protocol asks for is the same one. The panel now
  tests for the KEY rather than reading the value, so a document that never
  mentions questions still means the author declares none while any present
  non-list value — an explicit null included — reads as declaration-unknown
  and retires nothing. Coverage exercises both documents through the real
  bundle reader and the rendered panel. The reader's own silent handling of
  null is unchanged: `_read_questions` treats it as absent and emits no
  `type-mismatch`, which deviates from spec §4, and the report's request to
  align it is declined here as a change to shared manifest-read semantics
  (the projection, the generated brief and the F-phase readers all consume
  `codes()`) that belongs to its own reviewed change; the panel no longer
  depends on it either way. PR-bot round 5 narrows the panel's own read:
  `panel_state` walked the active rows with `SELECT *`, materializing every
  active `note` (bounded at 8 KiB each, with no ceiling on how many rows stand
  active) on every `/learn` render, to keep one row per concept, one per
  attempt and one summary. `row_view`'s first five keys moved into a shared
  `_fold_keys`, both queries are now formatted from one `_ACTIVE_SQL` so the
  narrow walk returns exactly the rows the wide one would, and the fold's
  winners are re-read whole by id in statements of at most 500 winner ids on
  the same connection with no write in between. The fold's inputs, outputs and
  `active_count` are unchanged, `active_rows`/`active_state` and the s2
  projection still read whole rows, and the verifier asserts the narrow
  columns carry no `note`, that only winner ids are hydrated, that 501 winners
  split into two variable-bounded statements, and that the narrow fold equals
  the wide one. PR-bot round 6 also treats a DEGRADED `identity-mismatch` as
  declaration-unknown for this panel: the foreign manifest's question list is
  ignored, attempted questions render under this lesson's durable ids, and
  none is marked retired. The bundle reader's shared DEGRADED and
  legacy-profile behaviour is unchanged. The round changes only the GET-side
  declaration helper, winner hydration and verifier; it does not change a
  POST path, write path, projection, template, static asset or escaping rule.
  Host verification at the round-6 branch state: verify 874, verify_restore
  28; public hygiene clean. PR-bot round 7 makes a supported v1 manifest
  declaration-unknown for the panel because v1 has no question declaration:
  recorded v2 attempts render under durable ids and none is called retired.
  It also replaces the Python lists of every non-retracted historical review
  seq with SQL `COUNT` aggregates preceding each active review winner.
  Superseded readings remain counted, while any review struck by a
  `retraction` remains excluded; only one aggregate per winner reaches Python.
  The round changes only GET-side declaration and assessment read helpers plus
  verifier coverage. Host verification at the round-7 branch state: verify
  876, verify_restore 28; public hygiene clean. PR-bot round 8 puts the
  attempt summary, assessment fold/hydration/counts and focus total inside one
  SQLite read snapshot, so a concurrent assessment commit cannot make one
  panel mix database versions. `panel_state` also owns a snapshot for direct
  callers that do not already have one. The latest attempt ids from that
  snapshot now filter review winners before full-row hydration, so notes for
  reviewed historical attempts the panel cannot display remain unread. The
  active-row count, evidence fold, summary and settled earlier-review rule are
  unchanged. Host verification at the round-8 branch state: verify 878,
  verify_restore 28; public hygiene clean. PR-bot round 9 limits the
  earlier-review aggregate to the review winners for attempts the panel
  actually displays, passing those winner pairs through fixed-size SQL
  batches rather than deriving winners for every historical attempt. The
  settled count rule is unchanged: a review corrected by another review is
  acknowledged, while a review targeted by a retraction is excluded in either
  write order. The same round passes the exact manifest read used for the
  selected lesson's bundle metadata into the record panel, so one GET cannot
  mix labels or retirement from a second manifest version. A requested
  `focus_sessions.lesson_id` index is declined under the repository's
  concrete-failure rule: the deployed single-user table has two rows in a
  217 KB database, the aggregate runs once per GET, and no measurable failure
  exists; any index belongs to its own migration if growth makes it useful.
  The focused-minutes count remains intact and no schema, template, escaping,
  POST or write path changes in this round. Host verification at the round-9
  worktree state: verify 880. PR-bot round 10 orders the selected lesson's
  final cross-store reads as database state first and manifest second. The
  preliminary manifest read remains only to validate entry persistence; after
  the DB state is captured, one final manifest read supplies both bundle
  metadata and the record's labels and retirement decisions. This respects
  the lesson-agent write order (manifest declaration before attempt POST), so
  a newly committed attempt cannot be classified against the older
  declaration set. The declaration-unknown annotation is also neutral now:
  it says question declarations are unavailable rather than calling readable
  v1 and identity-mismatched manifests unreadable. Autoescape, the stale-
  process guard and the no-JS posture are unchanged. Host verification at the
  round-10 worktree state: verify 880. Diagnosis-only follow-up drain
  2026-07-29 →
  `2026-07-29-lesson-record-panel-review.md`: one Low finding remains open, so
  this entry stays Pending for a separate reviewed repair PR. Round 10
  persists the selected entry from its preliminary manifest read, then uses a
  second final read for the response's bundle metadata and record. An
  invented-data interleaving confirmed that a page removed between those
  reads remains stored as `current_entry` even while the same 200 response
  renders the final manifest's fallback and stale-selection finding. The
  repair must make page-selection persistence use the same final manifest
  authority as the response and add a deterministic manifest-swap regression.
  No application code, merge, or restart was performed by the drain. The
  follow-up repair at the current branch tip removes the preliminary manifest
  read: `_record_panel_db_state` runs first, one final
  `with_bundle_info_read` supplies bundle metadata, selection persistence and
  the record, and `mark_opened` remains gated by that read's
  `stale_selection`. A deterministic verifier swap removes a valid requested
  page after DB-state capture and proves the same response falls back without
  persisting the removed page. The DB-state-before-final-manifest invariant,
  autoescape, stale-process guard and no-JS posture remain unchanged. Host
  verification at the repair worktree state: verify 880. The entry remains
  Pending for the owner's re-drain; no merge or restart was performed.
  Diagnosis-only resolution re-drain 2026-07-29 →
  `2026-07-29-lesson-record-panel-review.md`: zero new findings. The
  superseding section at the top of that report reviews exact branch head
  `167df06` / tree `cecc0a4`; the July 29 page-selection Low is resolved
  because one final manifest read now owns response metadata, record
  classification and selection persistence, and the July 28 false-retirement
  Low remains resolved. The separate shared-reader null diagnostic remains an
  open bundle-contract follow-up outside s4. The exact reviewed s4 tree is
  safe only for the documented direct-loopback, single-worker,
  unauthenticated single-user posture once landed. This entry keeps its
  Pending box for owner closure; no application code, merge or restart was
  performed by the re-drain. Owner closure 2026-07-29: the branch merged into
  `main` as `b199a20` (PR #92) at branch tip `619e69f`; the merged tree is
  byte-identical to that tip (both trees `c7d61fb`), and the tip differs from
  the reviewed head `167df06` (tree `cecc0a4`) only by the re-drain's own
  `docs/reviews/` commit — the merged application code is exactly the
  reviewed tree. The restart gate for this entry is open.

- [x] 2026-07-28 — `f40bc2f`, `76b2021`, `3706562`, `419ccbc`, `2cef3b4` on
  `fix/4-s3-capability-brief`, merged into `main` as `42eabf4` (PR #90); the
  merged tree is byte-identical to the reviewed branch head `2cef3b4` (both
  trees `b953ef1`) — `app/terminal.py`, `app/services/assessments.py`,
  `app/services/lessons.py`, `app/main.py`,
  `docs/lesson-assessments-api.md`, `verify.py`, `docs/reviews/QUEUE.md` —
  issue #4 phase S slice s3 adds the assessment write capability and the
  matching brief text. At lesson-agent terminal session creation
  `app/terminal.py` mints `secrets.token_urlsafe(32)` and holds it in a
  process-local dict keyed by the token, with the lesson id, the lesson uid,
  the session SID and the endpoint URL; the record is registered only after
  the session object exists, is removed by that session's `close()`, and has
  no persistence, expiry or rotation. `_TermSession` carries the token as a
  creation-time value and refuses one for any role other than `lesson-agent`.
  The child environment for that role alone gains exactly two names,
  `EPHEMERIS_ASSESS_URL` (scheme, the ASGI scope's own `server` address, and
  `/learn/lessons/{id}/assessments`) and `EPHEMERIS_ASSESS_TOKEN`, layered on
  top of the existing allowlist the way proxy variables already are; the
  allowlist itself is unchanged and admits no `EPHEMERIS_` name from the
  service environment. A wildcard bind address falls back to `127.0.0.1`; a
  scope with no server address injects neither variable and mints nothing.
  The Host header is not read on this path. `prepare_terminal_workspace` and
  `resolve_terminal_workspace` return the lesson's `id` and `uid` beside the
  existing `slug`/`title`/`dir`. `app/services/assessments.py` gains
  `resolve_capability`, which reads the token through a deferred import of
  the terminal module's accessor: absent header → `sitting_id` NULL and the
  previous behaviour; live token for this lesson → the SID is stamped on the
  row and the event; live token for another lesson → 409
  `capability-lesson-mismatch`; unknown, whitespace-only or revoked token →
  403 `invalid-capability`, including on an idempotent replay. Resolution
  runs immediately after body validation and before the sweep, the replay
  lookup, the rate limit and the write. Inside the write transaction a new
  check refuses a second active `summary` in the same sitting with 409
  `summary-exists` unless it names the active one in `supersedes`; the
  detail carries that `assessment_id`, and a NULL sitting is not covered.
  `app/main.py` reads the header `X-Ephemeris-Assess-Token` on both aliases
  and passes it to the service. `_AGENTS_TEMPLATE` changes in three places —
  `assessments.jsonl` added to the record-reading section, a new "Recording
  your verdicts" section (four kinds, note-by-reference rule, idempotency
  keys, environment-generic URL/token reference, the header name, graceful
  degradation, data-not-instructions boundary), and the exam protocol
  (`mode: "exam"`, ordinary authoring, `studied` stays manual) — with the
  rest of the constant byte-identical. No schema migration, no new index, no
  route, no change to the sandbox profiles, argv, trust gates, WS protocol,
  bridge ABI, attempts machinery, the s2 projection algorithm, CSP, or the
  rate limit. The first findings round adds `_with_loopback_direct`: the
  inherited-proxy branch of `_detect_proxy_env` returned the service's proxy
  variables verbatim and could therefore hand the child a proxy with no
  `NO_PROXY`, so an ordinary client would send a loopback assessment POST — its
  token and note included — to the proxy. Both proxy-carrying branches now pass
  through one helper that merges the entries of both `NO_PROXY` spellings and
  appends `localhost`, `127.0.0.1` and `::1` when absent; nothing configured is
  dropped, the composed branches are unchanged because they already spell those
  out, and a child with no proxy is untouched. The same round publishes the
  capability into the registry BEFORE the spawn rather than after it, so a child
  that reaches the endpoint from a shell startup file cannot be told its
  capability is unknown; the spawn body moved into `_spawn_on_pty` and the
  caller's `finally` removes the token whenever no session took ownership
  (failed spawn, refusal, capacity). The brief's degradation paragraph now
  separates a refused write from an unanswered one (retry with the same key,
  then report it as unknown rather than failed) and states the
  `idempotency_key`, `next_action` and concept-ref bounds. Verify 849,
  verify_restore 28. Diagnosis-only drain 2026-07-28 →
  `2026-07-28-lesson-assessment-capability-brief-review.md`: one Low finding
  remains open; this entry stays Pending for a separate reviewed repair PR.
  That repair is `b41b632` on `fix/4-s3-assessments-read-bound`, merged into
  `main` as `52329cf` (PR #91); that merged tree is likewise byte-identical
  to its reviewed branch head (both trees `f015d08`) —
  `app/services/lessons.py`, `docs/learn-bundle-spec.md`, `verify.py`. The
  record-reading bullet of `_AGENTS_TEMPLATE` no longer tells the agent to
  read `assessments.jsonl` whole and no longer calls the file small without
  qualification. It now states that the file has one line per active concept
  and reviewed attempt and no fixed ceiling, directs a whole read only while
  the file fits in 2 MiB, and otherwise directs a read of the meta line plus
  the newest complete lines within 2 MiB together with a statement of the
  omission to the learner and in the session summary, and it names the
  unread remainder omitted rather than absent. Nothing else in the constant
  changes, and the anchors for the surrounding text are unchanged except the
  one sentence that rewrapped. Spec §6.5 gains a sentence placing the bound
  on the reading side and restating that the writer never truncates; the
  projection code, the endpoint, the capability registry and the sandbox
  surfaces are untouched. Verify 850, verify_restore 28.
  Resolution review 2026-07-28 →
  `2026-07-28-lesson-assessment-capability-brief-review.md`: the one Low
  finding is resolved by the separate reviewed repair; no Critical, High,
  Medium, Low, Info, or open s3 finding remains. Final host verification on
  the batch starting head: verify 870, verify_restore 28. Final verdict:
  SAFE TO MAKE LIVE for the documented direct-loopback single-worker
  deployment; wider, proxy-adjacent, or multi-user deployment NO. The
  separate s4 entry remains Pending and keeps the repository restart gate
  closed. No merge or restart was performed.

- [x] 2026-07-28 — `a05d5fc`, `f03ae5d`, `49ae440`, `d18f755` on
  `fix/4-s2-assessments-projection`, merged into `main` as `876b879`
  (PR #88) —
  `app/services/assessments.py`, `app/services/lessons.py`,
  `app/services/bundle_schema.py`, `docs/learn-bundle-spec.md`,
  `docs/lesson-assessments-api.md`, `verify.py`, `docs/reviews/QUEUE.md` —
  issue #4 phase S slice s2 adds `assessments.jsonl`, an app-written file at
  the lesson bundle root that the lesson-agent and lesson-learner sandboxes
  can read. It holds one `assessments_meta` line (`v: 1`, `lesson_uid`,
  `as_of_seq`, `generated_at`) followed by one line per active-fold row —
  latest active evidence per concept, latest active review per attempt,
  latest active summary — each the full `lesson_assessments` record echo
  including `note` text, ascending `seq`; superseded, retracted and
  retraction rows are not written. The whole file is rewritten after the
  write transaction commits, never inside it: the entry point returns
  without acting when the connection is in a transaction, then takes an
  app-private per-lesson `flock` on `<DATA_DIR>/assessment-projections/
  <lesson_uid>.lock` (a separate file from the attempts projection lock),
  re-reads the committed fold, renders it, and publishes it through an
  `O_EXCL` 0600 temporary file, `fsync`, and `os.replace` relative to one
  bundle-root descriptor opened with `O_NOFOLLOW | O_DIRECTORY`. The staged
  descriptor stays open until after the replace and is `fstat`ed immediately
  before it: a temporary file whose link count is no longer 1 is discarded
  unpublished. Before
  publishing, a pure manifest read (no directory, skeleton or file creation)
  compares the manifest's `lesson_uid` with the DB uid and refuses to
  publish on a contradiction; missing, v1 and rejected manifests publish. A
  lesson with no rows and no existing file is left without one. A directory,
  symlink or multi-link file on the name is renamed to
  `assessments.jsonl.collision-<hex>` — an empty directory is removed — and
  its bytes are never read. Failure to publish — every exception raised
  anywhere below the entry point is caught — leaves the response's
  `projection` field at `pending` and never raises past the committed write;
  a pending file is rewritten at the next lesson-agent terminal open
  (`prepare_terminal_workspace`, after the briefs and unable to refuse the
  workspace), at an idempotent replay, or at the first assessment call for
  that lesson in the process. `bundle_schema.RESERVED_NAMES` and spec §2
  gain `assessments.jsonl`, so the §4.1 path grammar refuses any page, block
  file or artifact root claiming it; spec §6.5 and the §12 write-authority
  row are new. `app/services/attempts.py`, its projection, cursor, seal and
  lock files, `app/terminal.py`, the sandbox profiles, the bridge ABI, the
  generated brief and the manifest schema readers beyond the reserved-name
  list are unchanged, and no route or HTTP contract changed except the
  `projection` field's value. Verify 819, verify_restore 28. The initial
  review raised one Low (L1): the active fold had no cardinality bound and
  the replay path, which is outside the rate budget, drove a full rewrite per
  duplicate. The PR #89 entry below supplies its merged follow-up and was
  reviewed together with this entry as one owner-selected combined scope.
  `39fc478` (combined drain cycle 1) closes the remaining unmetered
  terminal-open rewrite and false cached-file identity: the production
  terminal hook uses the ordinary skip, while the cached seal now includes
  device and ctime beside watermark/inode/size/mtime; regression coverage
  proves intact repeated terminal opens do no fold/publish, deleted files
  still heal, and a same-inode/same-size/restored-mtime mutation reprojects.
  Verify 826, verify_restore 28. Drained together with PR #89 on 2026-07-28
  → `2026-07-28-lesson-assessments-projection-review.md`: two Low findings,
  both resolved in one combined cycle; no Critical, High, Medium, Low, Info,
  or open finding remains. Central write-guard W1 and attempt A1/A2 remain
  resolved; D5 L1 remains mitigated and D5 L2/L3 remain resolved; bundle,
  publication, lesson-role, terminal, sandbox, artifact, runner,
  platform-support, public-data, and recovery conditions retain the report's
  explicit dispositions. Final verdict: SAFE TO MAKE LIVE for the documented
  direct-loopback single-worker deployment; wider, proxy-adjacent, or
  multi-user deployment NO. Live restart is owner-only and was not performed.

- [x] 2026-07-28 — `7161e08`, `5ca48f2`, `95b78d9` on
  `fix/4-s2-projection-replay-budget`, merged into `main` as `5ebdc78`
  (PR #89) —
  `app/services/assessments.py`, `app/services/lessons.py`, `app/db.py`,
  `docs/learn-bundle-spec.md`, `verify.py`, `docs/reviews/QUEUE.md` —
  L1 follow-up. `reconcile_projection` gained a `force` flag and the module a
  process-local map of what it last published per lesson uid: the watermark
  (`MAX(id)`, insert-only rows) plus the published file's inode, size and
  mtime. When the watermark is unchanged AND the file is still that exact
  inode — a regular single-link file, metadata only, bytes never read — the
  reconcile returns without materializing the fold, rendering, or touching
  the filesystem; the watermark is read first inside the same read snapshot,
  and schema v15 adds `idx_assessments_lesson_seq (lesson_id, id)` so that
  `MAX(id)` seeks to the lesson's last row instead of walking its history
  (measured 9.3 ms → 2.2 µs at 200k rows; same index shape and reason as
  v13's `idx_attempts_lesson_cursor`). The identity gate runs on the skip
  path too: a manifest that now names another lesson answers `pending` at an
  unchanged watermark, as it does on the write path. Anything else rewrites:
  a watermark this process never published (pending, or from before a
  restart), a file that was deleted, resized, replaced or hard-linked, and
  the lesson-agent terminal open, which passes `force=True` because it fires
  when the file may be gone rather than when state changed. Spec §6.5 and the
  module header now state that the size is a compaction, not a cap. No change
  to the rate limit, the fold, the file format, the response contract, the
  lock, or the publication path. Verify 824, verify_restore 28. Reviewed
  together with the PR #88 entry above as one owner-selected combined scope.
  `39fc478` (combined drain cycle 1) closes two Low findings in the summed
  state: terminal creation no longer bypasses the unchanged-projection skip,
  and the cached metadata identity gains device and ctime so an in-place
  same-size edit with restored mtime cannot produce a false `projected`
  response. Missing or changed files still take the full heal path. Verify
  826, verify_restore 28. Drained together with PR #88 on 2026-07-28 →
  `2026-07-28-lesson-assessments-projection-review.md`: two Low findings,
  both resolved in one combined cycle; no Critical, High, Medium, Low, Info,
  or open finding remains. Historical projection L1 is resolved under the
  explicit compaction-without-a-fixed-cap contract; W1 and attempt A1/A2
  remain resolved; D5 L1 remains mitigated and D5 L2/L3 remain resolved; all
  other named prior conditions retain the report's explicit dispositions.
  Final verdict: SAFE TO MAKE LIVE for the documented direct-loopback
  single-worker deployment; wider, proxy-adjacent, or multi-user deployment
  NO. Live restart is owner-only and was not performed.

- [x] 2026-07-28 — `f614614` and `491f0e4` on
  `fix/4-s1-assessments-authority`, merged into `main` as `5976697` (PR #85) —
  `app/db.py`,
  `app/services/assessments.py` (new), `app/main.py`,
  `docs/lesson-assessments-api.md` (new), `verify.py`,
  `docs/reviews/QUEUE.md` — issue #4 phase S slice s1 adds a write route
  `POST /learn/lessons/{lesson_id}/assessments` and its `by-slug` alias,
  recording tutor assessments. Schema v14 adds the `lesson_assessments` table
  with per-kind CHECK constraints, `UNIQUE(event_uuid)`,
  `UNIQUE(assessment_id)` and `UNIQUE(lesson_id, idempotency_key)`; each row is
  written in one transaction with a `lesson_assessment` ledger event whose
  payload echoes the record. The handler admits at most 64 KiB through the
  shared capped-stream helper, requires `application/json`, refuses unknown
  top-level fields, validates four kinds against closed vocabularies, bounds
  `note` at 8 KiB and `next_action` at 512 UTF-8 bytes, accepts 1–8 opaque
  concept refs deduplicated server-side, copies `question_id` from the
  referenced `lesson_attempts` row, validates `attempt_id` and `supersedes`
  against the same lesson, refuses archived lessons, and applies a per-lesson
  30-per-60s in-process rate limit with the attempts-style refund table.
  Idempotency stores a SHA-256 fingerprint of the canonical validated
  submission; a replayed key with a matching fingerprint returns the original
  row and a differing one is a conflict, and the replay lookup precedes every
  mutable-state refusal. The route reads no manifest and applies no bridge
  eligibility gate. `sitting_id` is always NULL and `projection` is always
  `pending` in this slice. `app/db.py`'s `append_event` gains an optional
  caller-supplied `event_uuid`; every existing caller is unchanged.
  `app/services/attempts.py`, the bridge ABI, `app/terminal.py`, the sandbox
  profiles, the generated brief and `docs/learn-bundle-spec.md` are unchanged,
  and no existing route changed. The first PR-findings round moves the binding
  archived-lesson refusal inside the write transaction, which is opened with
  `BEGIN IMMEDIATE` so the committed archive state is read under the write lock
  rather than in autocommit, and adds
  `idx_assessments_lesson_supersedes (lesson_id, supersedes)` to the same v14
  script so the active-state fold's correlated deactivation lookup is bounded.
  The independent correctness re-check over the same diff added no code change:
  its one Medium concerned SQL-level enforcement of the concepts array shape,
  rebutted in the PR body, and its five Low findings were verifier-strength
  gaps, all closed by additional checks. `9f177b6` (drain cycle 1) makes
  assessment refusal details UTF-8-safe at the response boundary, so an escaped
  lone-surrogate unknown field returns the strict controlled 400 instead of
  raising during JSON response encoding, and adds raw-body coverage for both
  aliases. Verify 798, verify_restore 28. Drained 2026-07-28 →
  `2026-07-28-lesson-assessment-authority-review.md`: one Low finding, resolved
  in one cycle; no Critical, High, Medium, Low, Info, or open finding remains.
  Central write-guard W1 and attempt A1/A2 remain resolved; D5 L1 remains
  mitigated and D5 L2/L3 remain resolved; artifact, runner, bundle, lesson-role,
  terminal, sandbox, platform-support, public-data, and recovery conditions
  retain the report's explicit dispositions. Final verdict: SAFE TO MAKE LIVE
  for the documented direct-loopback single-worker deployment; wider,
  proxy-adjacent, or multi-user deployment NO. Live restart is owner-only and
  was not performed.

- [x] 2026-07-27 — `c159a2b`, `0d32ad` on
  `fix/terminal-copy-affordance`, landing after the drain of the terminal copy
  affordance entry below — `app/static/src/terminal.ts` (+ emitted
  `app/static/terminal.js`), `verify.py`, `docs/reviews/QUEUE.md` — the
  terminal client's custom key handler calls `preventDefault()` on the copy
  path when Shift is held, so the Ctrl+Shift+C alias no longer leaves the
  event's browser default action to run after the selection is written;
  xterm's `_keyDown` returns at the custom handler before its own `cancel()`,
  so the handler's `false` return does not cancel the event. Plain Ctrl+C is
  not cancelled, and the no-selection path is unchanged. `verify.py` anchors
  the added line inside the same branch. No other application file, and no
  Python runtime path, changed. No drain fix commit was required; verify 767,
  verify_restore 28. Drained 2026-07-27 →
  `2026-07-27-terminal-copy-alias-event-cancel-review.md`: no Critical, High,
  Medium, Low, Info, or open finding; zero fix cycles. The E4 two-surface and
  generated-runtime protections and terminal-tab L1 remain resolved;
  terminal-opt-in T1 remains accepted only for the deliberately plain owner
  shell; the trusted agent's network/credential posture and the
  direct-loopback mitigation are unchanged. Final verdict: SAFE TO MAKE LIVE
  for the documented direct-loopback single-worker deployment; wider,
  proxy-adjacent, or multi-user deployment NO. Live restart is owner-only and
  was not performed.

- [x] 2026-07-27 — `d1d487d`, `4b398e6` on
  `fix/terminal-copy-affordance` —
  `app/static/src/terminal.ts` (+ emitted `app/static/terminal.js`),
  `app/templates/base.html`, `app/templates/learn.html`,
  `app/templates/_icons.html`, `app/static/style.css`, `verify.py` — the
  terminal client's custom key handler no longer requires Shift to be up in its
  Ctrl+C branch, so Ctrl+Shift+C takes the same selection-only copy path; with
  no selection both combinations still return true to xterm. Both drawer
  headers gain a `<button id="<prefix>-copysel">` that reads and writes the
  existing per-surface `al-term-copyselect` / `al-term-learner-copyselect`
  localStorage flag, reflects it through `aria-pressed` and an `.active` class,
  and is a no-op when the element is absent; the stored default is unchanged.
  The button's `title`/`aria-label` name the copy and paste shortcuts.
  `_icons.html` gains a `copy` glyph; `style.css` gains a `.term-btn.active`
  rule and one selector in the right-dock minimized hide list. The WebSocket
  protocol, the loopback/Origin gates, `writeOnlyClipboardProvider`,
  `EPHEMERIS_ENABLE_TERMINAL`, session/tab persistence and the drawer metrics
  are unchanged. No Python runtime path changed. `4b398e6` adds a `storage`
  listener, guarded on that same key, that re-runs the button's state sync when
  another document writes the flag, and anchors the new verifier checks to the
  full key-handler branch, the missing-element guard, the load-time sync, the
  write's exception handling, the listener, and the complete icon entry. No
  drain fix commit was required; verify 767, verify_restore 28. Drained
  2026-07-27 → `2026-07-27-terminal-copy-affordance-review.md`: no Critical,
  High, Medium, Low, Info, or open finding; zero fix cycles. The E4
  two-surface and generated-runtime protections and terminal-tab L1 remain
  resolved; terminal-opt-in T1 remains accepted only for the deliberately
  plain owner shell; the trusted agent's network/credential posture and the
  direct-loopback mitigation are unchanged. Final verdict: SAFE TO MAKE LIVE
  for the documented direct-loopback single-worker deployment; wider,
  proxy-adjacent, or multi-user deployment NO. Live restart is owner-only and
  was not performed.

- [x] 2026-07-26 — commits after `4dea680` on `fix/25-platform-support`;
  LANDED via merge commit `a2aa560`, whose tree is byte-identical to reviewed
  branch head `95593ba` —
  `app/terminal.py`, `app/sandbox.py`, `app/services/attempts.py`, `app/db.py`,
  `app/main.py`, `app/services/focus.py`, `app/services/stats.py`, `verify.py`,
  `README.md` — issue #25 moves the `fcntl`/`pty`/`termios` imports in
  `app/terminal.py`, the `fcntl`/`resource` imports in `app/sandbox.py` and the
  `fcntl` import in `app/services/attempts.py` from module level into the
  functions that use them. `terminal.py` gains `_pty_stack()`, which caches the
  three modules and raises `_UnsupportedPlatformError` when they are absent;
  `setup_terminal()` calls it when the opt-in switch is on, and
  `_create_session()` calls it before `pty.openpty()`. `_child_setup()` and
  `sandbox.profile_preexec_fn()`'s `setup()` read the cached modules; the
  imports are performed in the parent before the fork. `sandbox._GENEROUS_LIMITS`
  is keyed by rlimit name instead of `resource.RLIMIT_*` value. The loopback peer
  check, Host/Origin validation, the `EPHEMERIS_ENABLE_TERMINAL` switch and the
  no-`--proxy-headers` contract are unchanged. `app/db.py` gains `pretty_date()`,
  replacing seven `strftime("%-d")` call sites. `verify.py` adds a subprocess
  probe that imports `app.main` with `fcntl`/`pty`/`termios`/`resource` blocked,
  and repoints two mock targets from module globals to the stdlib modules. No
  drain fix commit was required; verify 762, verify_restore 28. Drained
  2026-07-27 → `2026-07-27-platform-support-review.md`: no Critical, High,
  Medium, Low, Info, or open finding; zero fix cycles. The earlier PR P1 is
  resolved by `95593ba`; terminal, sandbox, runner, attempt, bridge, and
  data-boundary conditions retain the report's explicit dispositions. Final
  verdict: SAFE TO MAKE LIVE for the documented direct-loopback single-worker
  deployment; wider, proxy-adjacent, or multi-user deployment NO. Live restart
  is owner-only and was not performed.

- [x] 2026-07-23 — commits after `13cab1b` on
  `fix/58-attempts-projection-cost`; LANDED via merge commit `f004546`, whose
  tree is byte-identical to reviewed branch head `4256a2c` —
  `app/services/attempts.py`, `app/db.py`,
  `verify.py`, `docs/lesson-attempts-api.md`, `docs/reviews/QUEUE.md` — issue
  #58 replaces the attempt projection's database-wide writer-lock section and
  full-prefix append verification with private lesson-UID file locking, a
  durable private cursor/seal, bounded-row fast append, streaming reconcile,
  schema-v13 cursor indexing, and growth/concurrency/race regressions. The
  first bot-findings round makes busy locks return projection-pending, refuses
  reconcile from active transactions, treats recursively malformed state as
  repair input, and compares the full post-append descriptor/name seal. It
  second round keeps the rendered temp descriptor open across rebuild
  publication and compares its stable render fields plus full published-name
  seal before advancing the cursor. The third round validates cursor-id and
  sort-tail authority anchors, binds append state to the immediate post-write
  descriptor plus a bounded tail read, detects mtime-restored rebuild
  rewrites, and never retries a failed close. It does not change attempt
  authority, HTTP responses, refusal ordering, rate-limit semantics, or the
  `attempts.jsonl` line format. No drain fix commit was required; verify 770,
  verify_restore 28. Drained 2026-07-23 →
  `2026-07-23-attempts-projection-cost-review.md`: no Critical, High, Medium,
  Low, Info, or open finding; zero fix cycles. Attempt-backend A2 is resolved;
  A1 is resolved by the preceding body-stream-cap drain. D5 L1 remains
  mitigated and D5 L2/L3 remain resolved. Final verdict: SAFE TO MAKE LIVE for
  the documented direct-loopback single-worker deployment; wider,
  proxy-adjacent, or multi-user deployment NO. Live restart is owner-only and
  was not performed.

- [x] 2026-07-23 — commits after `c125534` on
  `fix/59-attempt-body-stream-cap`; LANDED via merge commit `06e4674`, whose
  tree is byte-identical to reviewed branch head `9d9b8ee` — `app/main.py`,
  `verify.py`, `docs/reviews/QUEUE.md` — issue #59 changes both lesson-attempt
  aliases to enforce the existing 256 KiB request-body limit while consuming
  the ASGI stream, rejects negative declared lengths, and adds direct-ASGI
  admission regressions; it does not change in-cap attempt semantics,
  persistence, the projection format, or any listener configuration. No drain
  fix commit was required; verify 770, verify_restore 28. Drained 2026-07-23 →
  `2026-07-23-attempt-body-stream-cap-review.md`: no Critical, High, Medium,
  Low, Info, or open finding; zero fix cycles. Attempt-backend A1 is resolved;
  A2 is resolved by the later projection-cost drain above. D5 L1 remains
  mitigated and D5 L2/L3 remain resolved. Final verdict: SAFE TO MAKE LIVE for
  the documented direct-loopback single-worker deployment; wider,
  proxy-adjacent, or multi-user deployment NO. Live restart is owner-only and
  was not performed.

- [x] 2026-07-23 — commits after `1c04bd2` on
  `fix/35-f5-blocks-activation`; reviewed application head `418615f` —
  `app/services/lessons.py`, `verify.py`, `docs/reviews/QUEUE.md` — issue #35
  phase F activation replaces only the generated lesson brief's inactive
  editor/run-block section with active manifest, artifact-placement,
  registered-runner, bridge-operation, text-only-output,
  graceful-degradation, and teaching-purpose conventions, plus matching
  verifier anchors. The initial PR findings round preserves learner-artifact
  ownership, requires the interactive profile, and supplies the concrete
  editor/run ready request. `d2f97f4` (drain cycle 1) gates editor and Run
  independently and repeats the minimum frozen operation/revision/run-ownership
  rules; `c148e6b` (cycle 2) handles parent-relayed `run.error`; `a0e694f`
  (cycle 3) handles request-scoped errors and negotiates `attempts` only for
  declared answers; `418615f` (cycle 4) closes terminal `job-missing`,
  mutating-error revision resync, no-stdin runner guidance, and stale-report
  verdict handling. Verify 756, verify_restore 28. It changes no runtime code
  path, schema, ABI, sandbox, CSP, route, terminal surface, or other brief
  section. Drained 2026-07-23 →
  `2026-07-23-blocks-activation-review.md`: nine Low findings, all resolved in
  four cycles; no Critical, High, Medium, Low, Info, or open finding remains.
  Brief-writer, teaching/data-boundary, bridge, artifact, runner, run-API,
  frontend, lesson-role, and terminal protections retain the report's stated
  dispositions; F4's generated-pedagogy activation condition is resolved.
  Final verdict: SAFE TO MAKE LIVE for the documented direct-loopback
  single-worker deployment; wider, proxy-adjacent, or multi-user deployment
  NO. Live restart is owner-only and was not performed.

- [x] 2026-07-22 — commits after `fd9f54a` on
  `fix/36-f4-editor-run-frontend` — `app/static/src/learn-bridge.ts`,
  emitted `app/static/learn-bridge.js`, `docs/lesson-bridge-abi.md`,
  `fixtures/lesson-bridge/`, `verify.py`, `docs/reviews/QUEUE.md` — issue #36
  phase F frontend slice adds the editor bridge membrane first and the run
  membrane in a later commit, plus their text-only child conventions fixture,
  additive ABI documentation, and regression coverage. The editor boundary's
  first Codex findings round aligns the client block bound with the
  manifest, repeats fresh block validation after the save-settle window, and
  refreshes block metadata before granting the editor; its second round
  preserves the attempt operation's 32 KiB semantic bound inside the wider
  editor membrane. The run boundary's first Codex findings round repeats
  fresh block validation after artifact reads, rejects backend-invalid
  idempotency keys before composite saves, and keeps owned-job cancellation
  available after block removal while retaining fresh page-identity checks.
  Its second Codex findings round drains complete SSE frames before bounding
  the retained partial frame and derives the backend idempotency key from the
  whole save/run operation before the save; generated-brief activation remains
  the separate following F5 slice. It does not change backend routes, attempt
  persistence, CSP, sandbox tokens, or terminal statics. Its third Codex
  findings round computes that key without secure-context-only Web Crypto so
  supported plain-HTTP trusted-LAN clients retain Run. Its fourth Codex
  findings round makes private artifact reads fail closed until a sticky
  per-document parent confirmation explicitly accepts the lesson page's
  same-frame navigation egress, and repeats fresh block/Run validation after
  start before exposing the returned job or output. `9e3956b` (drain cycle 1)
  authenticates and consumes exactly one child handshake result and gives each
  loaded conventions example a random request-id namespace across reloads and
  tabs; verify 754, verify_restore 28. Drained 2026-07-23 →
  `2026-07-23-lesson-editor-run-frontend-review.md`: two Low findings, both
  resolved in one cycle; no Critical, High, Medium, Low, Info, or open finding
  remains. Bridge-conventions, served-byte, artifact, runner, run-API,
  generated-pedagogy, lesson-role, and terminal protections retain the
  report's stated dispositions. Final verdict: SAFE TO MAKE LIVE for the
  documented direct-loopback single-worker deployment; wider, proxy-adjacent,
  or multi-user deployment NO. Live restart is owner-only and was not
  performed.

- [x] 2026-07-22 — commits after `f2487ee` on `fix/36-f3-run-api`; LANDED via
  merge commit `b40a099`, whose tree is byte-identical to reviewed branch head
  `c660ba4` — `app/main.py`, `app/runner.py`, `app/security.py`,
  `app/services/artifacts.py`, `app/services/runs.py`,
  `app/templates/learn.html`, `docs/lesson-artifacts-api.md`, `verify.py`,
  `docs/reviews/QUEUE.md` — issue #36 phase F slice F4 wires the reviewed
  runner core into the app lifecycle and adds manifest-addressed,
  revision-bound run start/status/SSE/cancel routes, bounded
  reader/replay/retention state, run rate accounting, and best-effort terminal
  telemetry; it does not change the static bridge runtime, terminal
  PTY/WebSocket core, sandbox limits, or attempt endpoint. Three Codex review
  rounds hardened same-origin reader admission, health/refund behavior,
  attached-stream retention and replay, nonblocking health/kill control paths,
  late-cancel semantics, independent reader wakeups, and raced-exit draining.
  `f7e9aef` (drain cycle 1) gives each SSE attachment an idempotent response-
  lifecycle lease, caps distinct reader-protected jobs at the terminal
  retention bound, and makes the process-lifetime runner health probe
  single-flight across concurrent callers; verify 719, verify_restore 28.
  Drained 2026-07-22 → `2026-07-22-lesson-run-api-review.md`: three Low
  findings, all resolved in one cycle; no Critical, High, Medium, Low, Info, or
  open finding remains. Runner-core, artifact, sandbox, bundle, terminal, and
  lesson-role protections remain resolved; D5 L1 remains mitigated, D5 L2/L3
  remain resolved, D4 A1/A2 and the plain-owner-shell condition remain accepted
  follow-ups. Final verdict: SAFE TO MAKE LIVE for the documented direct-
  loopback single-worker deployment; wider, proxy-adjacent, or multi-user
  deployment NO. Static bridge/client activation remains later scope. Live
  restart is owner-only and was not performed.

- [x] 2026-07-22 — commits after `0ea44d8` on
  `fix/36-f2-editor-backend`; LANDED via merge commit `be3f9ce`, whose tree is
  byte-identical to reviewed branch head `31acfbc` — `app/main.py`,
  `app/request_body.py`, `app/services/artifacts.py`,
  `app/services/lessons.py`, `app/templates/learn.html`,
  `docs/lesson-artifacts-api.md`, `verify.py`, `docs/reviews/QUEUE.md` — issue
  #36 phase F slice F1 adds pure bundle reads, manifest-addressed artifact
  GET/save routes, descriptor-bound safe-file checks, fd-relative atomic
  publication, revisions/conflicts, save telemetry, bounded request admission,
  and armed-page block metadata; it does not add a run route or change the
  existing attempt endpoint. `240511d` (drain cycle 1) rejects an artifact
  whose same descriptor changes identity while its bytes are read, preventing
  a mixed snapshot/revision; verify 696, verify_restore 28. Drained 2026-07-22
  → `2026-07-22-lesson-artifact-editor-backend-review.md`: one Low finding,
  resolved in one cycle; no Critical, High, Medium, Low, Info, or open finding
  remains. Bundle, write-guard, runner-core, sandbox, and lesson-role
  protections remain resolved; D5 L1 remains mitigated, D5 L2/L3 remain
  resolved, D4 A1/A2 and the plain-owner-shell condition remain accepted
  follow-ups. Final verdict: SAFE TO MAKE LIVE for the documented direct-
  loopback single-worker deployment; wider, proxy-adjacent, or multi-user
  deployment NO. Live restart is owner-only and was not performed.

- [x] 2026-07-22 — commits after `e3cb882` on
  `fix/36-f1-runner-core`; LANDED via merge commit `76e521d`, whose tree is
  byte-identical to reviewed branch head `f6715f5` — `app/runner.py`,
  `app/sandbox.py`, `app/services/runner_registry.py`,
  `app/services/bundle_schema.py`, `app/services/lessons.py`,
  `fixtures/runner/`, `scripts/probe_runner.py`,
  `scripts/probe_sandbox_profiles.py`, `verify.py`,
  `docs/learn-bundle-spec.md`, `docs/reviews/QUEUE.md` — issue #36 phase F
  slice F3 adds the fixed single-file runner registry, immutable fd-backed
  snapshot sandbox profile, bounded async job owner, cached health probes,
  admission and retention state, and throwaway isolation/execution fixtures;
  no HTTP route or live spawn integration is added. `68045f6` (drain cycle 1)
  caps admitted/retained snapshots at 32 MiB, clears Run authority on rejected
  or non-interactive manifests, and changes the Go module-cache mount and
  health check to use a no-follow directory fd; `3347293` (drain cycle 2)
  refuses symlinks in every component of that fd authority path; verify 672,
  verify_restore 28. Drained 2026-07-22 →
  `2026-07-22-runner-core-review.md`: two Medium and one Low findings, all
  resolved in two cycles; no Critical, High, Medium, Low, Info, or open finding
  remains. E1, lesson-role, terminal-surface, pedagogy, and bundle-schema
  protections remain resolved; D5 L1 remains mitigated, D5 L2/L3 remain
  resolved, D4 A1/A2 and the plain-owner-shell T1 remain accepted follow-ups;
  route/client/lifecycle runner integration remains later scope. Final verdict:
  SAFE TO MAKE LIVE for the documented direct-loopback single-worker
  deployment; wider, proxy-adjacent, multi-user, or live-runner deployment NO.
  Live restart is owner-only and was not performed.

- [x] 2026-07-22 — `d5f628f`, `0258dc3` on
  `fix/35-pedagogy-template-e` — `app/services/lessons.py`, `verify.py`,
  `docs/reviews/QUEUE.md` — issue #35 pedagogy template E replaces the
  generated lesson brief with the PEDAGOGY.md §4 draft and updates verifier
  anchors for the new brief sections. `7897148` (drain cycle 1) bounds each
  tutor session's projection read to the newest 2 MiB of complete records,
  treats absent projected rows and page visits as unknown, and requires short
  learner quotations to remain HTML-escaped text; verify 642,
  verify_restore 28. Drained 2026-07-22 →
  `2026-07-22-pedagogy-template-e-review.md`: two Low findings and one Info
  finding, all resolved in cycle 1; no Critical, High, Medium, Low, Info, or
  open finding remains. Earlier brief/writer/bridge/terminal/workspace/sandbox/
  bundle protections remain resolved; D5 L1 remains mitigated, D5 L2/L3 remain
  resolved, and D4 A1/A2 remain accepted Low follow-ups; runner remains later
  scope. Final verdict: SAFE TO MAKE LIVE for the documented direct-loopback
  single-worker deployment; wider, proxy-adjacent, multi-user, or runner
  deployment NO. Live restart is owner-only and was not performed.

- [x] 2026-07-21 — `9c85795`, `f7d3b4c`, `862d3b3` on
  `fix/42-e4-terminal-surfaces`; LANDED via merge commit `d355af1`, whose tree
  is byte-identical to reviewed branch head `862d3b3` —
  `app/static/src/terminal.ts`, emitted
  `app/static/terminal.js`, `app/templates/base.html`,
  `app/templates/learn.html`, `app/static/style.css`, `.gitattributes`,
  `verify.py`; drain fix also touches `.github/workflows/ci.yml` — phase E
  session E4 converts the existing terminal client to strict TypeScript and
  adds a lesson-only bottom learner drawer beside the existing agent surface.
  The learner drawer opens and connects only on a deliberate click, requests
  the server's `lesson-learner` role on creation, accepts role from the server
  session message, scopes stored tabs to the current lesson, and uses storage
  keys distinct from the retained agent keys. `6af3af1` (drain cycle 1) makes
  clean CI install the pinned TypeScript toolchain and fail closed if it cannot
  check source/emitted-runtime parity, and retains the newest/current learner
  lesson inside the bounded persisted tab set; verify 638, verify_restore 28.
  Drained 2026-07-22 → `2026-07-22-terminal-surfaces-review.md`: one Medium M1
  and one Low L1, both resolved in cycle 1; no Critical, High, Info, or open
  finding. Closing gates: E3's two-surface client condition is resolved while
  runner remains later scope; terminal-tab L1 remains resolved;
  terminal-opt-in T1 is resolved for both lesson roles and accepted for the
  plain owner shell; agent network/credential posture is unchanged; earlier
  terminal, workspace, sandbox, bundle, D5, and D4 conditions retain the
  report's explicit dispositions. Final verdict: SAFE TO MAKE LIVE for the
  documented direct-loopback single-worker deployment; wider, proxy-adjacent,
  multi-user, or runner deployment NO. Live restart is owner-only and was not
  performed.

- [x] 2026-07-21 — `d6e3563..1a4f640` on `fix/36-e3-learner-role` —
  `app/terminal.py`, `app/sandbox.py`, `app/services/lessons.py`,
  `scripts/verify_e3_sessions.py`, `verify.py` — phase E session E3 adds the
  closed server role enum and the optional `role` create selector, routes
  `lesson-learner` sessions through the no-network learner sandbox in the
  validated lesson bundle without regenerating briefs, masks runtime sockets
  and non-bundle private roots, refuses invalid selector combinations, and
  verifies concurrent lesson-agent and learner WebSocket sessions against a
  throwaway loopback server. LANDED via merge commit 4fc89e9; its tree is
  byte-identical to reviewed branch head 1a4f640. 20f4c57 (drain cycle 1)
  refuses a learner DB override inside the writable bundle before PTY/spawn and
  validates malformed role selectors before capacity eviction; verify 629,
  verify_restore 28. Drained 2026-07-21 →
  `2026-07-21-lesson-learner-sandbox-review.md`: one Medium M1 and one Low L1,
  both resolved in cycle 1; no Critical, High, Info, or open finding. Closing
  gates: E1 S1 remains resolved; E3 resolves learner isolation for the terminal
  path; terminal-opt-in T1 is resolved for both lesson roles and remains
  accepted for the plain owner shell; intentional agent network/credential
  posture is unchanged; earlier terminal/workspace/lesson/bundle/D5 conditions
  retain the report's stated dispositions. Final verdict: SAFE TO MAKE LIVE for
  the documented direct-loopback single-worker deployment; wider,
  proxy-adjacent, multi-user, or runner deployment NO. Live restart is
  owner-only and was not performed.

- [x] 2026-07-21 — de51c00, def1b111 — `app/terminal.py`, `app/sandbox.py`,
  `verify.py` — phase E session E2 classifies every session-create request with
  a `lesson` parameter as `lesson-agent`, spawns it through the E1 sandbox
  launcher with visible fail-closed refusal, records immutable creation-time
  role/workspace/profile on the session, reports role in the WS handshake, and
  limits proxy injection to host-network roles; plain sessions retain the
  direct repository shell path. LANDED via merge commit def1b111; its tree is
  byte-identical to reviewed branch head de51c00. Drained 2026-07-21 →
  `2026-07-21-lesson-agent-sandbox-review.md`: no Critical, High, Medium, Low,
  or Info finding; zero fix cycles. Closing gates: E1 S1 remains resolved;
  terminal-opt-in T1 is resolved for lesson-agent and remains accepted for the
  deliberately plain owner shell; intentional agent network/credential posture
  is unchanged; terminal-tab L1 and earlier terminal/workspace/writer
  protections remain resolved. Final verdict: SAFE TO MAKE LIVE for the
  documented direct-loopback single-worker deployment; wider/proxy-adjacent,
  multi-user, learner, or runner deployment NO. Live restart is owner-only and
  was not performed.

- [x] 2026-07-21 — 6aa80ca, 53b8481, 4161f76, f59e84a —
  `app/sandbox.py`, `scripts/probe_sandbox_profiles.py`, `verify.py` — phase E
  session E1 adds the bubblewrap runtime probe, pure three-profile argv builder,
  fail-closed sandbox spawn helper, agent/learner rlimit hook, on-host profile
  probe, and verifier coverage; no live terminal spawn path changes. f59e84a
  (drain cycle 1) requires an explicit non-root bundle authority and confines
  the mounted bundle to a strict lexical descendant, closing the arbitrary
  late-bind mask replacement; verify 609, verify_restore 28. Drained 2026-07-21
  → `2026-07-21-sandbox-launcher-review.md`: one Medium S1, resolved in cycle 1;
  no Critical/High/Low/Info or open finding. Closing gates: terminal-opt-in T1
  is mitigated by the E1 primitive but remains accepted for the live terminal
  until E2 integration; deliberate agent network/login posture is unchanged;
  earlier terminal and lesson protections remain resolved. Final verdict: SAFE
  TO MAKE LIVE for the documented direct-loopback single-worker deployment;
  wider deployment NO. Live restart is owner-only and was not performed.

- [x] 2026-07-21 — 3931339, 4a019be, 165481c, 1467750, ef533d9, 43c4b1d,
  c357bc5, edd0cf7 — the entry stays current with the branch: any further
  branch commit, and the merge commit itself once the PR lands, is appended
  here before any drain or restart (this repository merges via merge commits,
  never squash, so the landed tree is the reviewed branch head's tree and the
  listed branch commits are ancestors of the landed merge; ephemeral GitHub
  test-merge/squash preview hashes are not repository commits and are never
  tracked here) — `app/static/src/learn-bridge.ts` (+ emitted
  `app/static/learn-bridge.js`), `app/services/lessons.py`, `app/main.py`,
  `app/templates/learn.html`, `docs/lesson-bridge-abi.md`,
  `docs/lesson-attempts-api.md`, `verify.py` — issue #36 session D5: the bridge
  parent runtime now negotiates the `attempts` capability and implements the
  port `attempt` operation calling the D4 endpoint. The child supplies
  question_id/answer/request_id; the parent derives page identity from its
  armed binding, re-fetches preview metadata per operation and compares
  version, bridge identity, and the per-page declared-question list before the
  HTTP call; idempotency_key is the child's request_id; results and refusals
  are answered on the port (refusals reuse endpoint codes and do not count
  toward the protocol-error budget); a recorded attempt raises the app toast.
  preview-meta's `bridge_page` gains a `questions` array. Declared v2 pages are
  served from a one-descriptor snapshot (bytes, digest, and stat from the same
  open) with a content-bound version header; a new `PAGE_IDENTITY_MAX_BYTES`
  bound (4 MiB) excludes oversized pages from bridge identity with a visible
  finding while display falls back to the streaming response; the page digest
  cache evicts one entry when full instead of clearing. learn.html passes
  `data-attempts-url` to the runtime. The lesson-brief bridge bullet now states
  the frozen attempt call. ABI doc gains §3.1. verify.py adds a D5 section
  (592). 4a019be (PR-bot round 1): the parent navigates the frame with
  `?v=<version token>` and the file route refuses snapshot bytes that no
  longer hash to it (409 + self-reload), from the server-rendered first
  navigation on; both one-descriptor readers enforce the size bound inside the
  read loop; attempt operations wait a 250 ms settle delay between validation
  and the HTTP call so a completing self-navigation tears down the port before
  the write leaves (stalled-load residual documented in ABI §3.1). verify 594.
  165481c (PR-bot round 2): the file route computes the identical
  mtime:profile[:digest16] token for every declared v2 page (legacy profiles
  included) and enforces the `?v` comparison on that surface even when no
  snapshot could be taken — the streaming fallback never serves bytes the
  requested token does not describe. verify 596. 1467750 (PR-bot round 3): the
  size pre-check tolerates a page vanishing between is_file() and stat() —
  OSError falls through to the descriptor-bound hash open instead of a 500.
  verify 597. ef533d9 (PR-bot round 4): the pre-check is no-follow (lstat +
  S_ISREG) — a symlink raced in after the guard is never sized by target and
  falls through to the O_NOFOLLOW open. verify 598. 43c4b1d (PR-bot round 5):
  each attempt call cleans up its own document's in-flight set (teardown
  replaces it), and the vanish probe stages the real deleted-file race against
  os.lstat. c357bc5 (PR-bot round 7): digest-cache eviction is race-tolerant
  (pop with default + iteration guard) — concurrent cache misses can no longer
  500 a poll or page serve. PR #60 bot 👍 APPROVED head 68b9686
  2026-07-21T09:38:57Z; LANDED 2026-07-21 via merge commit 4a1d95f — the landed
  tree is byte-identical to the approved branch head's tree (both trees
  5cd5c80). edd0cf7 (drain cycle 1): digest-cache lookup and admission/eviction
  are serialized, the bound converges from an oversized state, and a
  concurrent-cold-miss regression was added; verify 599, verify_restore 28.
  Drained 2026-07-21 → `2026-07-21-check-activation-review.md`: one Low C1,
  resolved in cycle 1; no Critical/High/Medium or open Low. Closing gates: L1
  mitigated, L2 resolved, L3 resolved. Final verdict: SAFE TO MAKE LIVE for the
  documented direct-loopback single-worker deployment; wider deployment NO
  (unauthenticated; L1 residual; accepted A1/A2 follow-ups). Live restart is
  owner-only and was not performed.

- [x] 2026-07-20 — c2bf554, 4e7997f, 142ea74, 6be555e, 9da7758, 89b4bc2,
  ac08a7c, 9a34e33, e0e9697, 69af6fe, 906322d, 780c028, 89b4cd2, 0edef9e —
  the entry stays current with the branch: any further branch commit, and
  the merge commit itself once the PR lands (this repository merges via
  merge commits, so the landed tree is the reviewed branch head's tree),
  is appended here before any drain or restart — `app/db.py`, `app/services/attempts.py` (new),
  `app/services/bundle_schema.py` (round 8 only),
  `app/services/lessons.py`, `app/main.py`, `docs/lesson-attempts-api.md`
  (new), `verify.py` —
  issue #36 session D4: new write endpoint `POST
  /learn/lessons/{id}/attempts` (+ `by-slug` alias) recording learner
  attempts. Schema v12 adds the `lesson_attempts` table; each row is
  written in one transaction with a `lesson_attempt` ledger event. The
  handler validates submissions against the record-time bundle manifest
  (declared questions only; eligibility from the manifest read; staleness
  derived server-side from the current page binding and bytes), applies
  idempotency keys unique per lesson, per-lesson rate limiting, and body
  size caps, and synchronously appends a projection line to the bundle's
  `attempts.jsonl` (per-bundle lock; falls back to a full rebuild from
  SQLite). lessons.py gains two public read helpers (`read_bundle`,
  `hash_bundle_page`); no bridge/client code changed. The endpoint is
  behind the existing app-wide unsafe-method middleware. New contract doc
  describes request/response codes. verify.py adds a D4 section (565).
  4e7997f (PR-bot round 1): the idempotency replay lookup moved ahead of
  the record-time refusals, and the projection append loops on short
  write(2) counts; verify 567. 142ea74 (PR-bot round 2): refusals raised
  between the early replay check and the locked insert re-check the
  idempotency key under the bundle lock and return a committed duplicate;
  `created_at` carries microseconds and the projection fast path appends
  only when the file's tail sorts strictly before the new row by
  (created_at, attempt_id), otherwise rebuilding; verify 569. 6be555e
  (round 3): the projection fd's close(2) is guarded — a delayed write
  error counts as not-appended instead of raising past the durable
  write. 9da7758 (round 4): RecursionError from json.loads on a deeply
  nested body maps to the documented invalid-json 400. 89b4bc2 (round
  5, verify-only): the projection-outage check injects EIO by file name
  instead of chmod. ac08a7c (round 6): the projection fast path drops
  the count/tail heuristics and appends only when the file's bytes
  equal the §6.1 rebuild of every earlier authority row exactly (the
  appended line renders from the authority row). Round 7: attempt_number
  is counted inside the write transaction (a sibling process could
  inflate it post-commit); verify 572. Round 8: all nine identity/value
  grammar regexes in bundle_schema.py and attempts.py are \Z-anchored —
  Python's $ under .match() accepted a trailing newline, letting
  "pg_x\n"-style page/rev identities into the row and projection (and
  into manifest id validation); verify 573. Round 9: the idempotency
  replay lookup also precedes the rate limit — a retry of the
  window-exhausting attempt returns its duplicate, not a 429; replays
  and key conflicts consume no window budget; verify 574. Round 10:
  every projection section (snapshot, verify, append or rebuild) runs
  inside a BEGIN IMMEDIATE SQLite txn, serializing it cross-process
  against sibling commits and projection writes (a stale rebuild
  snapshot could otherwise overwrite a newer file); a directory
  planted at attempts.jsonl resolves as a deterministic collision
  (removed when empty, moved aside otherwise) instead of a permanent
  projection-pending state; verify 576. Round 11: the projection fast
  path additionally requires st_nlink == 1 (a planted hard link would
  leak the append into its other name; the rebuild replaces the name
  only), and the rate limit moved inside the refusal re-check block —
  a retry whose original committed after the early replay check gets
  its duplicate instead of a 429; verify 578. Round 12: rate-limit
  slots are charged per call but refunded on every replay/conflict
  outcome (they are not new writes; refusals of new writes stay
  charged), so retries racing a slow original cannot starve real
  attempts; the locked write section split into _record_locked;
  verify 579. Round 13 (docs/comment only): the per-process in-memory
  scope of the rate window is documented as the deployment contract
  (one worker; brief 2x during rolling-restart overlap; abuse damper,
  not a security boundary); no code change. Round 14 (docs-only): the
  commit list drops the self-referential round-commit placeholder for
  the standing append-before-drain/restart rule. Drained 2026-07-20 →
  `2026-07-20-attempt-backend-review.md` (Codex, standing brief by
  file reference, at head 83cc652): no Critical/High/Medium; two Low
  availability findings — A1 body cap enforced only after Starlette
  buffers the whole body (parser-framing dependent; issue #59),
  A2 projection append linear in lifetime history under the
  database-wide writer lock (issue #58) — both accepted as follow-ups,
  not blockers. Independent Opus second pass: no findings, concurs.
  Converged verdict: YES for the documented direct-loopback
  single-worker deployment; D5 capability-bearing bridge NO until the
  D2 report's L1 document-generation and L2 served-byte conditions are
  resolved (D4's per-operation server-side validation is retained and
  is the server half of that requirement); wider deployment NO
  (unauthenticated). PR #57: rounds 1–13 fixed on their threads;
  rounds 14–16 (phantom test-merge hashes) rebutted, review loop
  closed without a bot verdict. LANDED 2026-07-20 via merge commit
  12ae229 at branch head de2ed93 — the landed tree is byte-identical
  to the reviewed branch head's tree (verified: both trees 0c64b04).

- [x] 2026-07-20 — 6e7b7b5, 8c82f1b, 841c37c — `app/services/lessons.py`,
  `verify.py` —
  issue #35 stage 2 (session D3): the generated lesson `AGENTS.md` brief
  (`_AGENTS_TEMPLATE`) gains a "Bridge conventions" section telling study
  agents how to wire interactive pages: Check actions via bridge port
  operations only, the ready/welcome handshake per
  `docs/lesson-bridge-abi.md` (retry cadence, ~2 s silence budget,
  handshake skipped on an opaque file-opened origin), parent-owned
  identity, `question_id` taken from the manifest's declared `questions[]`
  ids, and read-only degradation when no bridge or no `attempts`
  capability is present; states that the ABI v1 granted capability set is
  empty today and that pages scaffold to the conventions without inventing
  a write operation. Template text only — `_write_brief`, the `CLAUDE.md`
  shim, and all runtime code paths unchanged; verify anchors added (535).
  Drained → `2026-07-20-lesson-brief-bridge-conventions-review.md`:
  initial review at 6e7b7b5 (two Low: B1 inbound-handshake
  authentication, B2 lesson-wide `request_id` uniqueness — both fixed in
  8c82f1b); closing addendum (B2 + recording-contract limitation
  resolved, B1 residual `event.origin` rule + non-security N1
  reject-envelope scoping — both fixed in 841c37c); second closing note
  (B1/N1 fully resolved, no new findings). Final verdict: SAFE TO MAKE
  LIVE under the direct-loopback ABI-v1 posture; D4/D5 capability work
  remains gated on the bridge-runtime report's L1/L2 conditions; wider
  deployment NO (unauthenticated). The entry stays current with the
  branch: any further commit touching the brief — and the merge commit
  once the PR lands (this repository merges via merge commits) — is
  appended here before any restart.

- [x] 2026-07-20 — e57d6bd, 7630977 —
  `app/static/src/learn-bridge.ts` + emitted `app/static/learn-bridge.js`
  (new), `app/static/app.js`, `app/templates/learn.html`, `app/main.py`,
  `app/services/lessons.py`, `docs/lesson-bridge-abi.md` (new),
  `fixtures/lesson-bridge/` (new), `package.json`/`tsconfig.json` (new,
  dev-only) —
  issue #36 session D2: new Learn-page parent runtime for the lesson preview
  iframe — it now owns the preview reload poll (moved out of app.js), sets
  the iframe `sandbox` attribute from the manifest's runtime profile, and
  implements the postMessage/MessageChannel handshake documented in
  `docs/lesson-bridge-abi.md` (versioned, one grant per loaded document,
  identity from the preview metadata; ABI v1 has no write operations —
  ping/pong only). The preview-meta endpoint additionally returns per-page
  `lesson_uid`/`page_id`/`page_rev` (sha256 of page bytes) and the sandbox
  token string. Browser e2e fixtures for six handshake scenarios are
  committed under `fixtures/lesson-bridge/`. First TypeScript sources in the
  repo (issue #42): tsc-emitted JS is committed and served as-is.
  Follow-ups on the same surface: 8cfcb9d (poll re-arms an unarmed settled
  document), b74fd0e (inline early-load observer anchors navPending),
  4315bab (arm only settled documents; reload on manifest-only identity
  drift), 1565bd4 (round 4: content-bound version token for bridge
  pages + inode-keyed digest cache; announcements answered on live receipt
  only, buffer removed), edf0f8b (round 5: exhausting the re-assert budget
  sets a terminal quarantine checked before arming; only a parent-owned
  navigation clears it), 4fdc572 (drain R1 fix: more than one load observed
  before runtime init means the settled document is never armed — the
  runtime re-asserts the expected src instead; commit also carries the
  drain addendum covering through 1565bd4), 927e8b1 (round 6: a
  rescueBinding latch admits one in-flight late-initialisation rescue
  bind; the poll remains the retry mechanism), 9dd4111 (round 7,
  docs-only: the ABI records the armed-window successor-ready residual
  next to the pre-own-load and in-flight-delivery residuals). PR #55
  merged 2026-07-20 via merge commit 0565f66; the merged tree is
  identical to branch head 9dd4111. Drained →
  `2026-07-20-lesson-bridge-runtime-review.md`: initial review at
  7630977 (three Low, L1–L3); first addendum through 1565bd4 (L1–L3
  partially resolved, one new Low regression R1); closing addendum over
  1565bd4..9dd4111 plus merge check (R1 resolved by 4fdc572, edf0f8b
  fail-closed quarantine confirmed, 927e8b1 rescue latch confirmed, no
  new security-severity finding, L1–L3 remain Low; tsc emit re-verified
  byte-identical independently). Closing verdict at merge head 0565f66:
  YES for the current ping-only ABI-v1 direct-loopback deployment;
  NO for D4/D5 capability extensions on this handshake until the L1
  document-confusion residuals and L2 served-byte binding are resolved
  (per-operation server-side re-validation mandatory); wider deployment
  NO — v0 unauthenticated.

- [x] 2026-07-20 — 66defd3, 2ce1c0e, 38ef45e, f7db9e1, 625bbb8 —
  `app/main.py`, `app/services/bundle_schema.py`, `app/services/lessons.py`,
  `verify.py`, `docs/learn-bundle-spec.md` —
  issue #39 session D1: the lesson preview/file routes now select the
  Content-Security-Policy header by the manifest's runtime profile
  (`legacy-display` keeps the previous policy, `interactive-local-v1` gets a
  new stricter one); `ManifestRead` gains a `bridge_eligible` property and
  the preview metadata / bundle info now report `profile` and `bridge`
  fields; spec §5 records the landed details; iframe sandbox attributes in
  templates are unchanged. Follow-ups: same-frame-navigation residual
  documented (2ce1c0e); existing-page reload token folds the effective
  profile in (38ef45e); `webrtc 'block'` added to the strict policy with
  partial-enforcement note (f7db9e1); `effective_profile` accessor forces
  legacy on late-rejected reads (625bbb8). Drained →
  `2026-07-20-csp-profiles-review.md` (one Low C1, resolved in 38ef45e;
  three addenda, closing verdict YES for direct-loopback). Opus second pass
  APPROVE (its 'self'-opaque-origin Low refuted by live browser probe;
  identity-mismatch Info became a verify check). PR #54 bot 👍 APPROVED
  head 625bbb8 2026-07-19T22:02:42Z after 3 finding rounds.

- [x] 2026-07-19 — ec3c112, a7acb6c, 40a7888, 3310e2b, 41c5134, fbd315b,
  f487b30, 7e3ead9, c4c9b62, fe6012a —
  `scripts/migrate_bundles.py` (new), `verify.py` —
  issue #39 session C4: offline migration tool that rewrites v1 `lesson.json`
  manifests to schema v2 per spec §10 (the rewritten manifests are consumed by
  the live Learn preview/file routes); dry-run, idempotent rerun, atomic
  replacement, rollback manifest under `data/migrations/`, hash
  post-verification of manifest and page bytes. Follow-ups: apply refuses a
  manifest changed since planning (a7acb6c); collision stop covers dropped
  object-form items, rollback copy path derived from the validated slug
  (40a7888); DB-slug grammar gate before joins, bundle-dir containment at
  write time, no-follow streamed page hashing, no-follow rollback-copy read +
  ledger shape validation, fsynced rollback material and directories (3310e2b);
  rollback dir + parents fsynced before the first mutation (41c5134); DB-row
  stale guard before apply, bundle-dir fsync after rollback restore (fbd315b);
  migrated manifests always carry usable slug/title, DB row fills missing
  copies (f487b30); pre-apply guard covers the DB title, title bound on the
  emitted length (7e3ead9); invalid source_url copy never emitted (c4c9b62);
  pre-apply guard covers source_url too (fe6012a); verify 506. This list
  names every code commit on the branch — later branch commits are
  docs-only (this entry + the review report). PR #51: bot reviewed every
  push (👍 APPROVED fbd315b 19:48Z; each later head's findings fixed in
  the next named commit)
  → `2026-07-19-bundle-migration-tool-review.md` (M1+L5 resolved in
  a7acb6c/40a7888; addendum: L1–L3 resolved at 3310e2b; closing notes:
  L4 resolved at 41c5134, fbd315b clean, f487b30's two Lows resolved at
  7e3ead9; final chain closes at the last code head — private-instance
  migration verdict per the report's final closing note; wider deployment
  remains NO/unauthenticated)

- [x] 2026-07-17 — 5250768, 5d37a97, 1227d29, 41224b5, 53b5232, ca4a7fd,
  9c188d7, 5388efe, 6a690b2, 825bec6, 5a9fd04, 6fde64a, 4d5b20d, cdeda5b,
  dd9c1c3, 4b88b6f, 36e7142, 1484362 —
  `app/services/bundle_schema.py` (new), `app/services/lessons.py`, `app/db.py`,
  `app/main.py`, `docs/learn-bundle-spec.md`, `verify.py` — issue #39 session
  C3: typed v1/v2
  lesson-manifest readers and findings, canonical/atomic writer, v2 creation,
  stable `lessons.uid`, declared-page selection, lesson-event UID echoes, and
  preview metadata; follow-ups block direct page renders for rejected manifests,
  surface stale v2 selections, harden standard bundle-subdir creation/path
  checks, reject duplicate identities even when another field drops the item,
  harden manifest error/bounds handling, remove the creation-event title echo,
  aggregate selected-page symlink outcome, compare v2 selections exactly,
  reject non-standard JSON constants and huge-integer parse failures, bind the
  legacy bridge read to one no-follow regular-file descriptor, restrict v2
  `/files/` to declared pages plus assets, preserve exact declared pages
  over overlapping artifact roots, derive placeholder version tokens from
  manifest state, reject a dangling bundle-directory symlink as
  `symlinked-bundle` instead of erroring, keep artifact roots out of the
  `assets/` preview area (spec §7 amendment), run the injected `attempts` root
  through the overlap pass while keeping v1's full historical file surface,
  snapshot bundle outcome/findings after selection resolution, fold the
  current page's symlink degradation into the top-level `bundle_info`
  outcome, report a dropped block's outside-root file independently of
  its unknown kind (completed in 1484362: page, kind, and root checks are
  fully independent), and reject §4.1 paths carrying edge whitespace (spec
  amendment) so the reader and the disk resolver always name the same file
  → `2026-07-17-bundle-schema-runtime-review.md` (final addendum through
  `9c188d7`: B1–B5 and the PR-bot findings resolved; no remaining security-
  severity finding; the one Info canonical-JSON closure finding N1 fixed in
  `5388efe` per the closing note; direct-loopback deploy allowed, wider
  deployment unsupported; resolution section covers `6a690b2..1484362` —
  PR #48 review rounds 7–18, each commit reviewed individually by the PR
  review bot on push, head `cdeda5b` approved 2026-07-16T23:20:56Z, the
  round-13 follow-up `dd9c1c3` reviewed on push with no code finding
  against it; this entry stays current with the branch: any further C3
  commit touching these paths — and the merge commit itself once the PR
  lands (this repository merges via merge commits, so the landed tree is
  the reviewed branch head's tree) — is appended here before any restart;
  c7a315e merges main back into the branch: retro_entries (#49) keeps
  schema v10 as landed on main, the `lessons.uid` step is renumbered to
  v11 with its content unchanged, `verify.py` keeps main's
  SCHEMA_VERSION-relative version check, both branches' Done entries
  kept — verify 473, verify_restore 28; fe98b63 (PR-bot round 19 on the
  merge): `_migrate_to_11` re-runs the IF-NOT-EXISTS retro DDL so a DB
  that ran the uid step under its pre-renumber v10 label still gains
  `retro_entries`, and a stale v2 selection is no longer erased by its
  own fallback — `bundle_info` exposes `stale_selection`, `GET /learn`
  skips persisting the fallback, and the preview-meta poll URL carries
  the stale candidate so each poll re-derives the invalid-entry finding
  — verify 477, verify_restore 28; LANDED 2026-07-19 via merge commit
  63a037d at approved head add17ec — the landed tree is the reviewed
  branch head's tree)

- [x] 2026-07-16 — 5ae5017, eeb71f1, ecee1f2, ff9a3f0, 4b04757, ba2bc3c,
  2851f69, 89adcbc, 38dd11b, 9dc0fc6, e7a2068, 250cd66 —
  `app/services/lessons.py`, `docs/learn-bundle-spec.md`, `verify.py` —
  issue #35 stage 1: the generated lesson `AGENTS.md` brief (constant
  `_AGENTS_TEMPLATE`, regenerated on every lesson-terminal open) rewritten
  from bundle-layout mechanics into a teaching contract: tutor mission,
  per-section concept/visualization/prediction/reveal loop, self-check,
  no-fabricated-references rule, pinned-libraries-in-`assets/` rule (remote
  URLs disallowed), and the frozen v2 manifest names from
  `docs/learn-bundle-spec.md` (v1/v2 branches, `pg_`/`q_` id lifecycle,
  `questions[]`, `attempts.jsonl` read-only, agent must not change
  `schema_version`/`lesson_uid`); `_write_brief` and the `CLAUDE.md` shim
  unchanged; verify 379 (+3, later 380); the later commits mirror the
  frozen discovery contract into the pre-read (attempts.jsonl optional;
  depth/entry/regular-file bounds; every declared artifact root, roots
  valid only as disjoint in-bundle paths, ≤ 8; `attempts/` stated as always
  part of the root set even when a declared list omits it, mirroring the
  frozen read model's injection); drained on 5ae5017 →
  `2026-07-16-lesson-brief-teaching-contract-review.md` (one Medium, one
  Low — both fixed in eeb71f1) + seven addenda, one per fix commit: L1/L2,
  N1 (bounds wording), N2 (root grammar/containment) all resolved; the
  closing addendum's verdict at 89adcbc, the seventh (convergence) addendum
  on 38dd11b, and the eighth addendum — a standing-brief pass over the
  38dd11b delta (no new findings, verdict unchanged) — and the ninth
  addendum, the same standing-brief pass over 9dc0fc6 (unknown-field
  preservation bullet; no new findings) — and the tenth addendum over
  e7a2068 (full §4.1/§7 root grammar in the brief; one Low: two cited
  rules were C3 spec amendments not yet on this branch — resolved by
  250cd66 mirroring them verbatim, resolution verified with a superseding
  YES verdict) — clear this entry under the loopback-only posture; the entry
  stays current with the branch: any further commit touching the brief —
  and the merge commit itself once the PR lands (this repository merges
  via merge commits, so the landed tree is the reviewed branch head's
  tree) — is appended here before any restart

- [x] 2026-07-16 — 61b6d65, 5d7c226, ad11d31 — `app/terminal.py`,
  `app/services/lessons.py`, `app/main.py`, `app/templates/learn.html`,
  `deploy/ephemeris.service.example`, `docs/security-model.md`, `README.md`,
  `verify.py`, `verify_restore.py` — issue #16 first slice: terminal websocket
  route and UI now register only when `EPHEMERIS_ENABLE_TERMINAL` is truthy
  (previous opt-out var no longer honored; systemd example ships it commented
  out with UMask/MemoryMax/TasksMax added); a `?lesson=` request whose
  workspace cannot be prepared — including a present-but-empty or junk slug —
  is refused with a visible message instead of spawning at the repo root; the
  child shell env is built from an allowlist plus `_detect_proxy_env` output
  instead of full `os.environ`; the proxy banner strips URL userinfo; Learn
  UI, preview-meta, and the missing-file preview placeholder expose a
  bundle-relative lesson path; verify wiring probes inverted, 368+28 green
  → `2026-07-16-terminal-optin-review.md` (drained on 61b6d65: two Low, one
  Info — T2/T3 fixed in 5d7c226, T1 accepted posture documented; addendum
  covers 5d7c226 + ad11d31: resolved, no new findings)

- [x] 2026-07-16 — a74eab1, e50090d — `app/security.py`, `app/main.py`,
  `verify.py`, `verify_restore.py`, `docs/security-model.md` — issue #15
  first slice: new ASGI middleware owns a trusted-host allowlist
  (`EPHEMERIS_TRUSTED_HOSTS`, loopback defaults), one origin policy for all
  unsafe methods (serialized http(s) origin == scheme+host+effective port,
  `null` rejected, absent Origin allowed only without cross-site fetch
  metadata), and global response headers (nosniff, Referrer-Policy, CSP
  `frame-ancestors 'none'` unless the route sets its own); the 28 per-route
  `_check_origin()` calls are removed; verify 361
  → `2026-07-16-write-guard-review.md` (one Low fixed in e50090d)

- [x] 2026-07-16 — 10a8a71 — `app/services/lessons.py`, `verify.py` —
  issue #14: generated lesson brief is now a constant (title/source URL no
  longer interpolated; the brief points the agent at `lesson.json` as data);
  `_write_brief` switched to same-directory 0600 tempfile + fsync + atomic
  `os.replace` (destination entry never opened); verify 345
  → `2026-07-16-brief-writer-review.md` (no findings)

- [x] 2026-07-16 — 9747fc9, a3683d7 — `app/static/terminal.js` —
  issue #37: tab-active pointer split into durable (`storedActiveId`, the only
  value persisted) and in-memory (`activeId`); off-Learn boot activates the
  first non-lesson tab in memory only; `connectAllTabs()` skips lesson tabs
  off-Learn (explicit switch still connects)
  → `2026-07-16-terminal-tab-scoping-review.md` (one Low fixed in a3683d7)

- [x] 2026-07-03 — multi-session terminal core — `app/terminal.py` —
  detach/reattach + fd lifecycle → `terminal-multisession-review.md`
  (F1–F4 fixed in 6f9538b)
- [x] 2026-07-06 — 2b2878f, 1fd1a63 — `app/terminal.py`,
  `app/templates/learn.html` — lesson-scoped terminal sessions →
  `learn-lesson-terminal-review.md` (one Low fixed in 1fd1a63)
- [x] 2026-07-07 — 92e585a — `app/services/lessons.py` —
  lesson workspace prep now also writes a `CLAUDE.md` brief shim (static
  `@AGENTS.md` include) via the same `O_NOFOLLOW` writer; +2 verify checks (338)
  → `2026-07-11-lesson-claude-shim-review.md`
- [x] 2026-07-11 — 4855e8e — `app/terminal.py`, `verify.py` —
  terminal websocket registration and local-only UI gating now honor
  `TICKLIKE_DISABLE_TERMINAL`; subprocess checks cover both switch states
  → `2026-07-11-terminal-disable-switch-review.md`
- [x] 2026-07-14 — d56b617 — `app/terminal.py`, `verify.py`, `verify_restore.py` —
  project rename: terminal controls renamed from `TICKLIKE_*` to `EPHEMERIS_*`
  → `2026-07-14-terminal-env-rename-review.md` (one Medium and two Low confirmed)
