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
  the reviewer by file reference, never restated inline.
- Deploy gate: the live service does not restart with code whose entries are
  still Pending (AGENTS.md → Public-Safety Check).

Entry format: `- [ ] YYYY-MM-DD — <commits> — <paths> — <what changed>`

## Pending

## Done

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
  kept — verify 473, verify_restore 28)

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
