# Lesson Claude shim — adversarial security review

**Scope:** commit `92e585a` — generation of a lesson-local `CLAUDE.md` that
contains a static `@AGENTS.md` include, the shared `O_NOFOLLOW` writer in
`app/services/lessons.py`, and the two added checks in `verify.py`. The lesson
service was read in full together with its HTTP creation route and terminal
workspace call chain.

**Context:** v0 has no authentication and the live instance is assumed to bind
to loopback only. A lesson terminal still passes the terminal's loopback,
Host, and Origin gate before any lesson parameter is used. The relevant new
boundary is different: lesson metadata is copied into an instruction file that
an OS-capable coding agent automatically loads.

**Method:** diffed `92e585a` from its parent, traced lesson creation through
workspace generation and PTY startup, used invented temporary-directory inputs
to probe instruction rendering and file-link behavior, and re-checked the prior
L1 symlink finding and all recorded non-findings in
`learn-lesson-terminal-review.md`.

**Verdict:** no PTY/WS trust-gate regression was introduced, and the earlier
symlink escape remains fixed. One Medium stored prompt-injection path and one
Low residual hard-link clobber were confirmed. The Medium issue is bounded by
the current single-user/loopback posture, but matters immediately for lesson
metadata created by any less-trusted local, LAN, imported, or rebinding-reached
source.

## Findings

### L1 — Lesson metadata becomes auto-loaded Claude instructions (Medium, confirmed)

Lesson titles are only stripped and length-limited, so embedded newlines and
instruction-shaped text are accepted (`app/services/lessons.py:36-42`). Source
URLs receive URL-shape and length validation but are likewise not separated
from instruction content (`app/services/lessons.py:45-54`). Both values are
interpolated verbatim into headings and bullets in the generated agent brief
(`app/services/lessons.py:331-387`). The new static shim tells Claude Code to
load that brief as instructions (`app/services/lessons.py:370-379`) and writes
both files whenever a lesson terminal is prepared
(`app/services/lessons.py:417-447`).

A concrete invented case with a newline followed by an agent directive in a
lesson title survived `create_lesson()`, appeared near the top of `AGENTS.md`,
and was then reached through the generated `CLAUDE.md` include. The application
exposes creation through the unauthenticated lesson POST
(`app/main.py:1199-1216`); its Origin guard permits an absent Origin and only
compares a supplied Origin authority with Host (`app/main.py:185-190`). Under
the assumed loopback posture, planting requires a same-machine writer or the
already documented DNS-rebinding weakness plus later owner action. In the
documented LAN mode, any reachable LAN client can plant the metadata. Execution
still requires the owner to open that lesson terminal and run Claude, which
keeps this below High despite the possible agent-mediated shell impact.

The underlying generated `AGENTS.md` already exposed this class to agents that
read it directly; `92e585a` expands the same confused-deputy path to Claude Code
by making the include automatic.

*Fix direction:* in a dedicated change, keep attacker-influenced lesson fields
out of the agent instruction layer. Put them in a separate data file or clearly
delimited runtime context that the instruction file labels as untrusted; control
character filtering alone is not a complete prompt-injection boundary.

### L2 — `O_NOFOLLOW` still permits hard-link clobber and FIFO blocking (Low, confirmed)

`_write_brief()` opens an existing path with `O_TRUNC | O_NOFOLLOW`
(`app/services/lessons.py:407-414`). `O_NOFOLLOW` rejects a final symbolic link,
but a hard link is a normal directory entry and is opened normally. In an
invented temporary-directory probe, pre-creating lesson `CLAUDE.md` as a hard
link to another same-filesystem file caused workspace-style writing to replace
the other file's contents with the static shim. A named pipe is also not
rejected: because the open is blocking, a pre-created FIFO at either generated
filename waits indefinitely for a reader. `_create_session()` holds the global
creation lock while it awaits workspace preparation (`app/terminal.py:412-441`),
so that FIFO can stall all new terminal-session creation. The new path therefore
adds clobber and availability primitives that the symlink test does not cover.
The existing `AGENTS.md` writer has the same residual behavior.

Today this is Low: a same-user process able to create those filesystem nodes can
already write the usual same-user targets or disrupt the process, and no current
HTTP route creates hard links or FIFOs. It matters more if lesson bundles become
importable or are prepared by a less trusted process, the same future-facing
condition that motivated the original symlink hardening.

*Fix direction:* in a dedicated follow-up, create a new regular temporary file
inside the verified directory and atomically replace the generated filename (or
open without truncation, verify the inode with `fstat()`, then truncate/write),
rather than treating `O_NOFOLLOW` as protection against every link or file type.

## Earlier lesson-terminal findings — regression check

- **Prior L1, symlinked bundle directory/final brief:** remains fixed. Existing
  lesson directories must be real direct children of the resolved lessons root
  (`app/services/lessons.py:390-404`), and both generated final filenames use
  the no-follow writer (`app/services/lessons.py:407-414`,
  `app/services/lessons.py:442-445`). L2 is a distinct hard-link residual, not a
  regression of the confirmed symbolic-link fix.
- **Slug traversal:** remains blocked before DB or filesystem use by the strict
  slug grammar and length check (`app/services/lessons.py:417-440`).
- **Terminal trust-gate ordering:** remains intact. `_ws_is_trusted()` completes
  before accept and before `sid` or `lesson` is read
  (`app/terminal.py:557-573`). Attach-by-sid still ignores the lesson parameter.
- **Threading, capacity, and lifecycle:** workspace DB/file work still runs via
  `asyncio.to_thread` while session creation remains serialized under
  `_CREATE_LOCK` (`app/terminal.py:412-457`). No PTY fd lifecycle code changed.
- **Import cycle:** remains absent: `terminal.py` imports the lesson service;
  the lesson service imports only `db` from the application layer
  (`app/terminal.py:39-41`, `app/services/lessons.py:18`).

## Regression coverage and limitation

The added checks verify the static include and refusal of a symlinked
`CLAUDE.md` without truncating the decoy (`verify.py:295-301`,
`verify.py:319-353`). They do not cover instruction-shaped metadata or hard
links/FIFOs, the two findings above.

The full `verify.py` run could not reach the lesson checks because the local
`TestClient.__enter__()` call stalled; a blank FastAPI app reproduced that
environmental failure. Focused temporary-directory probes and full source
inspection were used for this report instead.

## Deploy verdict

The review is complete and the prior terminal/symlink protections have not
regressed. L1 prevents blessing the shim for untrusted, imported, or
other-client-created lesson metadata; it needs its own follow-up change. L2 is
defense-in-depth hardening under the current same-user model.
