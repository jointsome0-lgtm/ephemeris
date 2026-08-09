# Lesson build workspace — adversarial security review

**Date:** 2026-08-09

## Review parameters

- **Scope:** the sole Pending entry present at review start: squash `6f7d893`
  of `lesson-build-workspace`, PR #164. The executable diff is
  `6f7d893^..6f7d893`, covering `app/sandbox.py`,
  `app/services/bundle_schema.py`, `app/services/lessons.py`,
  `app/terminal.py`, `docs/learn-bundle-spec.md`, the changed tests, and queue
  bookkeeping named by the entry.
- **Starting tree:** `main` at `66acf04`, equal to `origin/main`; `6f7d893` is
  an ancestor of that head. The tracked worktree was clean before review work.
- **Report file:**
  `docs/reviews/2026-08-09-lesson-build-workspace-review.md`.
- **Prior reports reconciled:** the persistent-agent-home, sandbox-launcher,
  lesson-agent/learner, lesson brief-writer, terminal opt-in, and backup/restore
  reports were re-read for the same path, mount, workspace, collision,
  credential, role, and recovery surfaces. Their current dispositions are
  stated below.

## Context and method

The deployment decision assumes the documented unauthenticated, single-worker
application bound directly to loopback with no forwarded-header rewriting. The
lesson-agent remains a trusted owner tool with host networking and deliberately
re-exposed CLI credentials; this review does not treat it as a boundary for a
less-trusted principal.

The complete squash diff and the full current touched application modules were
read, together with direct callers, the bundle specification, changed tests,
backup/archive and restore implementation, and earlier reports. Static review
traced slug and lesson identity, directory and collision handling, lexical
mount authority, mount ordering, role selection, fail-closed spawn behavior,
cross-lesson visibility, preview denial, v1 compatibility, PTY lifecycle,
backup inclusion, and wider-listener behavior. Tests and commit prose were
treated as claims to verify against the landed source.

The first full-suite run inside the nested reviewer sandbox stalled at the
known TestClient/bubblewrap boundary and was interrupted without assertion
output. The exact command was rerun on the host; only the completed host run
counts below. No live database, lesson bundle, package tree, credential,
browser state, screenshot, or service was read, changed, restarted, or
signalled.

## Findings

### Low — Full backups silently omit symlinks from persistent package trees

The new workspace is persistent private instance state at
`DATA_DIR/lesson-builds/<slug>/node_modules` and is explicitly where installed
packages live (`app/services/lessons.py:43-49,2085-2156`). Full backup is
documented as archiving everything else under `ACTIVITY_DATA_DIR` by exclusion,
including whatever new feature is added beside the named instance directories
(`docs/backup-restore.md:17-37`).

The backup walker nevertheless drops every symlink without listing it in
`instance_files`, `instance_files_vanished`, or `excluded`: directory symlinks
are pruned and file symlinks are skipped (`scripts/backup_db.py:360-415`). That
was a safe default for links in arbitrary instance data, but it is not a
faithful backup of an installed `node_modules` tree. Package trees routinely
use internal symlinks, notably executable entries under `.bin/`; after restore,
the regular package files return while those links do not, leaving a package
installation that can be present but unusable.

An invented end-to-end probe created
`lesson-builds/invented-lesson/node_modules/demo-tool/index.js` and an internal
`.bin/demo-tool -> ../demo-tool/index.js` link, then used the real full-backup
and restore functions. The regular file was restored; the link was absent, and
the manifest disclosed neither its source presence nor its omission. Backup
verification still passed because it compares the archive only with the
already-incomplete manifest.

This does not expose another lesson or widen the running terminal boundary,
and the missing dependency tree can normally be rebuilt from package metadata,
so severity is Low rather than Medium. It is still a concrete recovery-contract
failure: a completed, verified “full backup” silently loses part of the new
persistent workspace. A dedicated repair should either preserve validated
in-tree links safely, or declare `lesson-builds` reconstructible and exclude it
explicitly from both backup and restore with matching documentation and tests.

## Confirmed protections

- The build-workspace path is server-derived from the DB-resolved closed slug
  grammar. Workspace, source mount, and bundle-side mount point are created as
  real directories; a link or non-directory on any of those three names is
  moved aside, and a populated pre-reservation bundle `node_modules` is
  preserved under a collision name before the empty mount point is recreated
  (`app/services/lessons.py:2085-2156,2524-2574`).
- The sandbox accepts `build_workspace` only for `lesson-agent`, requires an
  absolute traversal-free strict descendant of the private instance root,
  rejects it under the bundle root, and emits its bind after the bundle bind
  and before `--chdir`. Learner and runner profiles reject the argument, and
  omitting it leaves their argv and the original agent argv unchanged
  (`app/sandbox.py:274-296,330-370,461-496`).
- The workspace exposes only its `node_modules` child at the reserved bundle
  name. No writable Bun cache was added, so this change does not introduce the
  cross-lesson hardlink channel found and removed during PR review. A real
  bubblewrap probe wrote through `<bundle>/node_modules`, observed the bytes at
  `<workspace>/node_modules`, and confirmed the on-disk bundle mount point
  remained empty.
- `node_modules` is rejected as a v2 page, block file, or artifact root and is
  denied by the v1 preview surface. The pre-version-bump compatibility break is
  explicitly recorded in the bundle specification, and populated legacy bytes
  are preserved rather than shadowed.
- Only the server-selected agent role calls the mutating preparation path and
  passes the returned workspace to `spawn_sandboxed`; the learner receives
  `build_workspace=None`. Preparation or spawn failure remains a visible
  refusal with no unsandboxed or wrong-directory fallback
  (`app/terminal.py:799-947`).

## Prior-condition reconciliation

- **Persistent-agent-home and E1 mount authority — REMAIN RESOLVED.** The new
  bind uses a separate app-owned sibling root, does not alter home overlays,
  and leaves read-only credential/config mounts in their established order.
- **E2/E3 role separation and fail-closed launch — REMAIN RESOLVED.** Only the
  trusted lesson-agent receives the writable package directory; learner and
  runner cannot request it, and failure does not retry outside bubblewrap.
- **Brief-writer collision and reserved-name protections — REMAIN RESOLVED.** A
  legacy populated `node_modules` is moved aside, not deleted or silently
  hidden, and no preview path can serve through the app-owned mount point.
- **Terminal Host/Origin, opt-in, attach/reaper, PTY, environment, and
  capability protections — REMAIN RESOLVED.** The change passes one prepared
  path through the existing spawn seam and changes none of those controls.
- **Full-backup regular-file inclusion — REMAINS TRUE, but is insufficient for
  this surface.** Regular files below `lesson-builds` are included by the
  exclusion walk; the Low finding is the silent loss of package-tree symlinks.

## Verification

- `git diff --check 6f7d893^ 6f7d893` — passed.
- `uv run pytest` on the host — **308 passed**, one existing TestClient
  deprecation warning.
- `uv run python verify_restore.py` on the host — **34 passed, 0 failed**.
- Focused real-bubblewrap mount probe — passed: a sandbox write landed only in
  the external workspace, while the bundle's on-disk mount point stayed empty.
- Focused full-backup/restore link probe — reproduced the Low finding: the
  regular package file returned, the internal `.bin` symlink did not, and the
  manifest did not list it.
- `git diff --check` — passed after the report and queue edit.
- `python scripts/check_public_hygiene.py` — passed after the report and queue
  edit; ignored-status inspection showed only the two intended documentation
  changes plus pre-existing ignored local state.

## Verdict

The reviewed change has **0 Critical, 0 High, 0 Medium, 1 Low, and 0 Info**
findings. Its live terminal, sandbox, role, preview, and public-data boundaries
remain sound for the documented direct-loopback, single-worker,
unauthenticated single-user deployment. The Low recovery finding remains open:
a verified full backup does not faithfully preserve the new persistent package
tree when it contains symlinks. Wider, proxy-adjacent, multi-user, or
less-trusted deployment remains unsupported. Per the standing brief, the
reviewed queue entry is moved to Done with this report pointer. No application
repair or live-service action was performed.
