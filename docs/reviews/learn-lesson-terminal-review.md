# Learn lesson-scoped terminal — pre-deploy review

**Scope:** commit `2b2878f` — a new client-controlled websocket query parameter
`?lesson=<slug>` on `/terminal/ws` that scopes a NEW PTY session's `cwd` to the
lesson bundle (`data/lessons/<slug>/`) and regenerates an agent-facing `AGENTS.md`
brief there. Touches `app/services/lessons.py` (`prepare_terminal_workspace`),
`app/terminal.py` (`_create_session`/`_serve_ws`), `app/static/terminal.js`,
`app/templates/learn.html`.

**Context:** single-user, no-auth personal app. The live service binds
**`127.0.0.1:8765`** and runs **without `--proxy-headers`**, so `ws.client.host` is
the real peer and the loopback trust gate is sound. Findings are **same-machine**
exposure, not network exposure. Threat model = a same-machine local attacker and a
malicious/compromised web page the local user visits (confused-deputy / CSWSH).

**Method:** independent adversarial pass by Codex (routed per AGENTS.md "Security
Reviews Go To Codex"), converged against the source by Claude (correctness half).
**Verdict: nothing blocks restarting the live loopback service; one Low hardening
applied before merge.**

## Findings

### L1 — Bundle dir / AGENTS.md write followed symlinks (Low, confirmed → fixed)
Slug traversal is blocked, but a pre-planted symlink at `data/lessons/<slug>` (to a
directory) would make the manifest + `AGENTS.md` writes and the spawned shell's
`cwd` land outside the bundle tree; a symlinked `AGENTS.md` inside a real lesson dir
would be truncated by the write. Not exploitable under today's single-user model (an
attacker who can plant the link already owns the user's shell), but the app's
trajectory is agent-generated / importable lesson bundles, where a hostile bundle
could carry such a link.
*Fix (landed in `1fd1a63`):* `_bundle_dir_is_safe()` refuses a lesson dir that is or
resolves through a symlink, checked before any write; `_write_agents_md()` opens with
`O_NOFOLLOW` so a symlinked brief raises instead of truncating its target. On either,
`prepare_terminal_workspace` returns `None` and the shell falls back to the repo root.

## Verified blocked / not exploitable

- **Path traversal via `?lesson=`** — blocked before any filesystem use: length ≤ 80
  and `_SLUG_RE = ^[a-z0-9]+(?:-[a-z0-9]+)*$` reject dots, slashes, absolute paths,
  unicode homoglyphs, NUL/control bytes, and trailing separators; the DB lookup and
  slug guard run before `_lesson_dir(slug)` is ever used.
- **Trust gate not weakened** — `_ws_is_trusted()` runs to completion before
  `accept()` and before `sid`/`lesson` are read; `?lesson=` cannot short-circuit the
  loopback-peer / same-origin (CSWSH) / loopback-Host (DNS-rebind) checks.
- **Concurrency / lifecycle** — `prepare_terminal_workspace` opens its own SQLite
  connection inside `asyncio.to_thread` (no connection shared across threads); the new
  `await` sits inside `_CREATE_LOCK` before `pty.openpty()`, so the capacity check
  stays atomic and no fd/reaper race is introduced. Attach-by-`sid` ignores `?lesson=`,
  so a reaped lesson session heals into a fresh shell in the same lesson dir.
- **Info disclosure** — the `cwd` banner in the replayed scrollback is a slug-existence
  oracle only to clients that already pass the terminal trust gate.
- **Import cycle** — none: `terminal.py` → `app.services.lessons` → `db`; `lessons.py`
  imports neither `terminal` nor `main`.

## Regression coverage

`verify.py`: `prepare_terminal_workspace` resolves a valid slug to its bundle dir and
generates the brief; rejects junk/unknown/`None` slugs; refuses a symlinked lesson dir
and a symlinked `AGENTS.md` (no truncation of the decoy). 336 passed.
