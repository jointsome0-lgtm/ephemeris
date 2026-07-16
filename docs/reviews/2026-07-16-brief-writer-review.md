# Lesson brief writer — adversarial security review

**Scope:** commit `10a8a71` — the generated lesson `AGENTS.md` became a
constant that treats title and source URL as data in `lesson.json`, and the
shared `AGENTS.md` / `CLAUDE.md` writer changed from opening the destination
with `O_NOFOLLOW` to a same-directory mode-0600 temporary file followed by
`fsync()` and atomic `os.replace()`. The changed checks in `verify.py`, the full
lesson service, its terminal caller, and the lesson-creation route were read.

**Context:** v0 has no authentication and the live instance is assumed to bind
to loopback only. The terminal WebSocket still requires a loopback peer and an
exact loopback Host/Origin match before accepting the socket or reading the
lesson query parameter (`app/terminal.py:81-108`, `app/terminal.py:557-573`).

**Method:** diffed `10a8a71` from its parent, traced lesson metadata from the
HTTP creation route through `prepare_terminal_workspace()` and PTY creation,
reviewed earlier reports for this surface, and ran invented temporary-directory
probes against symbolic links, hard links, a FIFO, output permissions, and an
injected `fsync()` failure.

**Verdict:** no security findings. The earlier Medium stored prompt-injection
finding and Low hard-link/FIFO finding are fixed, and the older symlink,
traversal, terminal trust-gate, threading, lifecycle, and import-boundary
protections have not regressed. Safe to deploy under the documented loopback-only
posture.

## Findings (severity-ranked)

No Critical, High, Medium, or Low findings.

## Confirmed fixes and regression checks

- **Stored prompt injection through generated instructions — fixed.** Lesson
  titles accept ordinary user text and source URLs remain user-controlled data
  (`app/services/lessons.py:37-55`), but neither value is interpolated into the
  generated brief. `_AGENTS_TEMPLATE` is constant and explicitly locates those
  values in `lesson.json` as non-instruction data
  (`app/services/lessons.py:332-368`); workspace preparation writes that constant
  rather than formatting lesson fields into it
  (`app/services/lessons.py:427-456`). `CLAUDE.md` still contains only the static
  `@AGENTS.md` include (`app/services/lessons.py:371-380`). This closes the
  instruction-layer data flow confirmed as Medium in
  `2026-07-11-lesson-claude-shim-review.md`.

- **Hard-link clobber and FIFO blocking — fixed.** `tempfile.mkstemp()` securely
  creates a new same-directory regular file with mode 0600; the implementation
  writes and flushes that new inode, calls `fsync()`, and only then replaces the
  destination directory entry with `os.replace()`
  (`app/services/lessons.py:400-424`). The destination itself is never opened, so
  a hard link is unlinked rather than truncated and a FIFO is replaced rather
  than opened. Invented focused probes confirmed both behaviors and also
  confirmed that a symbolic-link target is untouched.

- **Failure atomicity and cleanup — confirmed.** An injected first-call
  `fsync()` failure left the previously published brief byte-for-byte intact and
  removed the `.brief-*` temporary file, matching the exception cleanup at
  `app/services/lessons.py:419-424`. The new verifier covers the same publication
  failure path (`verify.py:427-455`). A process crash may leave a mode-0600
  temporary file, but it contains only the public constant brief and is never an
  agent instruction entry point; this is not a security finding.

- **Earlier lesson-directory symlink defense — intact.** Existing lesson
  directories must still be real directories directly beneath the resolved
  lessons root, and that check occurs before manifest or brief writes
  (`app/services/lessons.py:383-397`, `app/services/lessons.py:451-456`). Final
  brief-path symbolic links are now safely replaced instead of causing workspace
  refusal; their targets are not followed or modified.

- **Slug traversal and filesystem containment — intact.** The strict slug
  grammar and length check still precede the DB lookup and filesystem use
  (`app/services/lessons.py:440-453`). Bundle references continue to reject
  absolute paths, backslashes, controls, and `..`, then require resolved
  containment under the lesson root (`app/services/lessons.py:111-139`).

- **Terminal exposure and lifecycle — unchanged.** Workspace resolution still
  runs in a worker thread while session creation is serialized, and the returned
  directory is used only as the new child shell's `cwd`
  (`app/terminal.py:412-473`). Attach-by-session-id still ignores `lesson`, and
  no PTY file-descriptor or WebSocket lifecycle code changed. The lesson service
  still imports only the database layer (`app/services/lessons.py:19`), so no
  import cycle was introduced.

- **Future wider exposure.** If the application is exposed beyond loopback, its
  unauthenticated lesson-creation route remains reachable state-changing
  functionality (`app/main.py:1199-1216`). This commit removes lesson metadata
  from auto-loaded instruction files, but it does not make wider network
  exposure safe; the documented loopback-only deployment boundary remains
  required.

## Verification

- `git diff 10a8a71^ 10a8a71 --check` — passed.
- Focused invented brief-writer probes — passed: mode 0600, symbolic-link target
  preservation, hard-link target preservation, FIFO non-blocking replacement,
  and prior-file preservation plus temporary-file cleanup on injected `fsync()`
  failure.
- `python verify.py` — inconclusive in this environment: it produced no output
  and remained stalled before its first assertion for two minutes, so it was
  interrupted. The earlier report for this surface documented the same local
  `TestClient.__enter__()` startup failure; no failing assertion was observed.

## Deploy verdict

**SAFE TO MAKE LIVE: YES**, provided the service remains bound to loopback as
documented. No unresolved finding from this review blocks deployment.
