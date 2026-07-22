# Lesson artifact editor backend — adversarial security review

**Scope:** the single Pending queue entry dated 2026-07-22 for commits after
`0ea44d8` on `fix/36-f2-editor-backend`, landed by merge commit `be3f9ce`.
The reviewed branch head is `31acfbc`; the merge tree and branch-head tree are
byte-identical (`1403f13`). The scope covers `app/main.py`,
`app/request_body.py`, `app/services/artifacts.py`,
`app/services/lessons.py`, `app/templates/learn.html`,
`docs/lesson-artifacts-api.md`, `verify.py`, and the queue entry, plus the
manifest reader, lesson lookup, event writer, request guard, runner-health
adapter, and other direct callers.

The branch's second commit, `31acfbc`, is a verifier-only CI portability fix:
the mocked runner-spawn contract now supplies an invented directory fd instead
of depending on the developer machine's Go module-cache path. It does not
change an application surface.

**Starting HEAD:** `be3f9ce0110480deb9986529deaacef886af53f8` on
`main`, matching `origin/main` at drain start. The starting tree had passed
`python verify.py` with **695 passed, 0 failed** and
`python verify_restore.py` with **28 passed, 0 failed** on the exact reviewed
branch head; both GitHub Actions checks were successful before merge. No live
service was restarted or signalled.

## Context and method

The deployment decision assumes the documented unauthenticated, direct-
loopback, single-user, single-worker app. Runtime lesson files and the SQLite
ledger remain in the configured private instance outside this public checkout.
Only invented files under the verifier's temporary instance and a throwaway
`/tmp` review directory were used.

The complete landed diff and every listed file were read. The review traced
manifest-derived block addressing through pure bundle reads, strict UTF-8 and
size admission, descriptor-bound reads, compare-and-write, fd-relative parent
creation and atomic replacement, event failure, route/body handling, central
write middleware, metadata exposure, and runner-health gating. Earlier bundle-
schema, write-guard, attempt-backend, check-activation, sandbox, and runner-core
reports were reconciled against the landed call graph. Queue prose, verifier
assertions, and the PR bot's clean exact-head verdict were treated as claims to
check, not as review evidence.

## Findings (severity-ranked)

### A1 — A same-inode mid-read rewrite can produce a synthetic artifact snapshot (Low, confirmed)

At the starting tree, `_open_artifact()` took an opening `fstat`, read the file
in chunks, and took a closing `fstat`, but it checked only that the closing
descriptor was still a single-link regular file within the size cap. It never
required the opening and closing descriptor identities to agree. A direct
terminal writer could therefore modify the same inode while the API was
reading it. The returned bytes and `file_rev` could describe a mixture that
was neither the pre-write nor final file.

A deterministic invented-file probe rewrote a 64 KiB single-link regular file
after the first read chunk. The API primitive accepted bytes beginning with
`AAAA` and ending with `ZZZZ`; their SHA-256 revision differed from the final
all-`Z` file. On a save compare, the same gap could also make a content-equal
request report `unchanged` against bytes that were not the final named file.
The committed regression also changes the final size by one byte so its
identity delta remains deterministic on filesystems with coarse timestamps.

This is Low under the documented posture: exploiting the race requires a
same-user filesystem writer that can already replace the private artifact, and
the frozen v1 contract intentionally does not mediate every terminal write.
It is nevertheless inside the narrower promised detection window—changes
visible during the descriptor read must not become an authoritative snapshot.
Require the complete opening and closing descriptor identities to match, fail
closed otherwise, and pin the mid-read mutation.

No Critical, High, Medium, or Info finding was found.

## Confirmed protections at the starting tree

- Lesson and block identity come from the database and record-time manifest;
  client input never supplies a filesystem path. Rejected, identity-mismatched,
  non-interactive, unknown, incompatible, and undeclared states fail closed.
- GET uses the pure bundle reader and creates no bundle, manifest, standard
  directory, legacy copy, or artifact parent. POST remains behind the central
  Host/origin write guard.
- Request admission counts the ASGI stream before buffering past 512 KiB.
  Artifact reads and saves stop at 64 KiB, use strict UTF-8, and refuse
  symlinks, non-regular nodes, and multi-link files.
- Save traversal is anchored to a no-follow lesson-root descriptor. Every
  parent is opened or created fd-relatively, the mode-0600 temporary file is
  written and fsynced in the pinned destination directory, publication uses an
  fd-relative replace, and the directory is fsynced. The four-level discovery
  bound is enforced at save time.
- Artifact-API writers serialize under one per-bundle lock. Content-equal
  retries are uncharged and event-free; conflicts and the final named-
  descriptor identity check fail closed. The documented same-user final
  publication window remains intentionally last-write-wins.
- Save telemetry is file-first, best-effort, omits content and title, and makes
  event loss visible. It does not write `attempts.jsonl`.
- Preview metadata exposes block ids and a health-gated Run boolean only. It
  exposes neither paths nor commands, and rejected/legacy manifests retain no
  Run authority.

## CLOSING ADDENDUM — fix commit `240511d` (cycle 1 of 10)

### A1 — resolved

`_open_artifact()` now compares the complete opening and closing descriptor
identity—device, inode, mode, link count, size, mtime, and ctime—before
returning any bytes. A same-inode write visible during the read therefore
returns the existing fail-closed `unsafe-file` refusal rather than content or
a revision. The exact deterministic probe described above is now a verifier
regression and passes only when no mixed snapshot escapes.

Fresh review of `240511d` found no new Critical, High, Medium, Low, Info, or
other finding. Reads still happen outside any new global lock; only the
already-bounded descriptor snapshot gains a start/end identity comparison.
The frozen final-publication calibration for owner-controlled terminal writes
is unchanged.

## Prior-condition reconciliation at closing tree

- The bundle-schema reader's bounded/total parsing, identity/profile
  fail-closed behavior, v2 preview allowlist, no-follow manifest/page handling,
  and sanitized findings **REMAIN RESOLVED**.
- The central write-guard scheme/origin finding W1 **REMAINS RESOLVED**. The
  documented origin-less local-client allowance and direct-loopback posture are
  unchanged; no listener or proxy behavior changed.
- Runner-core M1, M2, and L1 **REMAIN RESOLVED**. F2 consumes the fixed registry
  and cached health result but adds no run route or application lifecycle hook.
- Check-activation L1 **REMAINS MITIGATED** and L2/L3 **REMAIN RESOLVED**.
  Attempt-backend A1/A2 **REMAIN OPEN/ACCEPTED** Low availability follow-ups;
  this slice adds a capped helper but deliberately does not migrate the attempt
  route or touch projection reconciliation.
- Sandbox and lesson-agent/learner isolation **REMAIN RESOLVED**. The trusted
  agent network/credential posture and the deliberately plain owner shell
  **REMAIN OPEN/ACCEPTED by design**. No terminal, PTY, WebSocket, or sandbox
  path changed.

## Closing verification and verdict

- `git diff --check` — passed.
- `python verify.py` at `240511d` — **696 passed, 0 failed**.
- `python verify_restore.py` at `240511d` — **28 passed, 0 failed**.
- `python scripts/check_public_hygiene.py` — passed.
- `git status --short --ignored` — inspected; only the intended source/test
  changes and established ignored local tool/dependency paths were present.

**SAFE TO MAKE LIVE for the documented direct-loopback
`127.0.0.1:8765`, single-worker, unauthenticated single-user deployment.** One
Low finding was raised and resolved in **1 of 10** cycles; no Critical, High,
Medium, Low, Info, or open review finding remains. Wider, proxy-adjacent, or
multi-user deployment remains **NO**. The queue entry may move to Done. A live
restart remains owner-only and was not performed.
