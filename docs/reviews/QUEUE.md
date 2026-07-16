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

- [ ] 2026-07-16 — 10a8a71 — `app/services/lessons.py`, `verify.py` —
  issue #14: generated lesson brief is now a constant (title/source URL no
  longer interpolated; the brief points the agent at `lesson.json` as data);
  `_write_brief` switched to same-directory 0600 tempfile + fsync + atomic
  `os.replace` (destination entry never opened); verify 345

## Done

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
