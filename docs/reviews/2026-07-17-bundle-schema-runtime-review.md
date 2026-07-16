# Lesson bundle schema v2 runtime — adversarial security review

**Scope:** queue-listed commit `5250768`, plus follow-up fixes `5d37a97` and
`1227d29` that landed on the same branch while this review was in progress.
The original commit adds the v1/v2 manifest reader and canonical writer,
schema-v10 lesson identity, v2 bundle creation, stricter page selection and
symlink handling, event identity echoes, and preview findings. The follow-ups
close three findings in that original implementation. The full listed diff,
the complete new schema module and lesson service, the changed DB/main/verifier
surfaces, the frozen bundle contract, route/security callees, and earlier lesson
reports were read.

**Context:** v0 has no authentication. Per `AGENTS.md`, the deployment decision
for this review assumes a service bound directly to loopback; public exposure
remains unsupported. Lesson bundles are private runtime data and may eventually
be imported or authored by less-trusted tools, so malformed-bundle behavior is
still a security boundary even though the current user can also edit the files
directly.

**Method:** diffed `5250768^..5250768`, then reviewed both follow-up diffs and
the exact current head `1227d29`. Re-checked earlier reports for generated-brief
prompt injection, bundle/brief symlinks, special-file writes, path disclosure,
and terminal trust/lifecycle. Invented temporary fixtures exercised malformed
URLs, deep JSON, directories and FIFOs at `lesson.json`, finding amplification,
legacy-source symlinks, rejected direct-file reads, stale selections, duplicate
identity ordering, generic artifact reads, UID migration/echo, and atomic
replacement.

**Verdict:** no Critical, High, or Medium finding under the documented
direct-loopback posture. Four Low findings and one informational contract
mismatch remain. The direct-render rejection bypass and two reader-visibility /
identity-ordering gaps present in `5250768` are fixed by `5d37a97` and
`1227d29`. The schema v10 identity work, canonical writer, primary symlink
checks, and earlier generated-brief protections otherwise hold.

## Findings (severity-ranked)

### B1 — A size-bounded hostile manifest can escape the outcome model or amplify one poll into large work (Low, confirmed)

The 256 KiB byte cap is useful, but it is not a total resource/error boundary.
`read_manifest_bytes()` catches only UTF-8 and `JSONDecodeError` failures around
`json.loads()` (`app/services/bundle_schema.py:250-275`), while metadata URL
validation calls `urlsplit()` without handling its `ValueError`
(`app/services/bundle_schema.py:224-228`). `read_manifest_path()` opens any
non-symlink filesystem node as blocking `O_RDONLY`, checks only `st_size`, and
then reads it without first requiring a regular file
(`app/services/bundle_schema.py:278-310`). Finally, list limits add a rejected
finding but validation still walks every supplied item and appends one finding
object per bad item (for pages, `app/services/bundle_schema.py:455-505`); the
preview endpoint serializes all of them (`app/main.py:1173-1195`).

Focused invented probes confirmed four consequences on current head:

- a regular v2 manifest with `source_url` shaped as malformed bracketed HTTP
  authority raises `ValueError` instead of returning `stale-metadata`;
- a 1,200-level unknown JSON array raises `RecursionError` instead of the visible
  unreadable outcome;
- a directory at `lesson.json` raises `IsADirectoryError`, while a FIFO blocks
  the reader until another process opens the writer end;
- a 200,132-byte manifest containing 100,000 non-object page items creates
  100,003 findings and a 9,400,287-byte findings JSON representation. An open
  Learn page polls preview metadata every 1.2 seconds, so this persists without
  further interaction.

Today planting these states requires a local/manual/agent-side bundle write,
which keeps the finding Low under the single-user loopback model. It becomes a
straight availability primitive as soon as bundles can come from a less-trusted
importer or writer. Treat non-regular manifest nodes as unreadable before a
blocking read, make parsing/field validators total, and bound repeated finding
materialization after a list-count rejection.

### B2 — Unreadable-manifest findings re-expose the absolute runtime path (Low, confirmed regression)

For non-symlink `os.open()` failures, the reader stores `str(exc)` verbatim as
the public finding detail (`app/services/bundle_schema.py:295-298`). The lesson
adapter copies every detail unchanged (`app/services/lessons.py:221-225`), and
preview-meta returns it to the client (`app/main.py:1184-1195`). An invented
mode-000 manifest produced a `manifest-unreadable` detail containing the full
temporary path through `.../lessons/<slug>/lesson.json`.

The 2026-07-16 terminal opt-in review had confirmed that absolute preview paths
were removed from every client surface. The new findings channel regresses that
confidentiality property for unreadable manifests. This remains Low on loopback,
but would disclose the service account/data layout to any wider client. Keep
diagnostic paths server-side and expose a fixed public detail or a bundle-relative
path only.

### B3 — The flat-file compatibility bridge follows a symlinked source and republishes its bytes (Low, confirmed)

The stricter bundle paths reject symlinks, but the old flat-file bridge still
tests `data/lessons/<slug>.html` with `is_file()` and then reads it normally
(`app/services/lessons.py:210-216`). Both operations follow a source symlink.
The destination check protects only `index.html`; it does not protect the read.
An invented symlink from the legacy source name to an external decoy caused the
decoy bytes to be copied into the real bundle's `index.html`, where the normal
file route could serve them.

This requires local filesystem influence today, so it is Low, consistent with
the earlier lesson-link findings. It matters for imported or mixed-provenance
runtime trees and becomes a confidentiality leak to wider network clients.
Refuse a symlinked legacy source and require a no-follow regular-file read before
performing the one-time compatibility copy.

### B4 — The generic bundle file route serves reserved manifests and learner artifacts (Low, confirmed)

For an accepted manifest, `bundle_resource_info()` applies containment and
symlink checks but no content allowlist (`app/services/lessons.py:300-328`). The
unauthenticated GET route then returns any regular file for which that helper
reports `exists` (`app/main.py:1127-1147`). Consequently the route accepts the
reserved `lesson.json`, `attempts.jsonl`, generated agent files, and arbitrary
paths below artifact roots; an invented `attempts/invented-note.txt` was reported
as directly servable. This bypasses the manifest path grammar's reserved-name
rule (`docs/learn-bundle-spec.md:56-57`) and makes future learner answers/work
reachable through the preview resource surface rather than a purpose-built
attempt/editor API.

On direct loopback the same user owns both browser and files, so severity is
Low. On a trusted-LAN or wider binding, every reachable client can enumerate
lesson IDs from `/learn`, fetch `lesson.json`, learn declared artifact paths,
and download private work. Restrict this route to the preview surface (declared
pages plus the intended public asset area), and leave reserved names/artifact
work to dedicated APIs with their own policy.

### B5 — New `lesson_created` events still duplicate `title` against the frozen echo policy (Info, confirmed)

The contract says post-C3 lesson events carry `lesson_uid` but never echo
`title`, so adapters obtain current manifest metadata by stable identity
(`docs/learn-bundle-spec.md:410-421`). `create_lesson()` correctly adds the UID
but still writes `title` into the append-only event payload
(`app/services/lessons.py:535-560`), and exports preserve that payload. A focused
creation probe confirmed both fields are present.

This is not a direct privilege or availability flaw and the event store is
private runtime data, so it is informational. It does defeat the stated
single-truth/data-minimization rule and preserves a stale title copy in later
exports. Remove the field in a dedicated event-contract change or explicitly
amend the frozen policy; do not leave implementation and adapter guidance in
disagreement.

## Findings resolved by the same-branch follow-ups

- **Rejected manifest direct-render bypass — fixed in `5d37a97`.** In
  `5250768`, `bundle_resource_info()` discarded the manifest read result and
  would serve `index.html` through `/files/...` even while preview rendered the
  rejected placeholder (`5250768:app/services/lessons.py:290-301`). Current
  code requires a non-rejected read before reporting the resource present
  (`app/services/lessons.py:300-312`), and the added route-level check covers
  the 404.
- **Stale/undeclared v2 selection was silently normalized — fixed in
  `5d37a97`.** The original `_resolve_entry()` fell back without adding the
  required `invalid-entry` finding (`5250768:app/services/lessons.py:222-234`).
  Current code records the finding before fallback
  (`app/services/lessons.py:229-244`); a focused preview-info probe returned
  `degraded` with `invalid-entry`.
- **A duplicate identity could hide behind another dropped field — fixed in
  `1227d29`.** The original page validator checked path validity before adding
  the page ID/path to its seen sets, so an invalid-path declaration followed by
  the same valid ID produced only `degraded` and retained the interactive
  profile (`5250768:app/services/bundle_schema.py:455-500`). Current page/block
  validation records syntactically valid identity/path claims independently
  (`app/services/bundle_schema.py:455-505`, `560-645`), and the new regression
  check requires the masked duplicate to reject.

The additional no-follow directory creation and defensive path-helper guard in
`5d37a97` introduce no new finding. The app-code follow-ups were authored and
committed outside this review; this report and the queue move are the review's
only repository writes.

## Confirmed protections and regression checks

- **Lesson identity is stable and transactional.** Schema v10 adds the unique
  UID index and idempotent NULL backfill; `create_lesson()` mints once and echoes
  the same value to the v2 manifest and all scoped lifecycle events. A temporary
  database reached `user_version=10`, exposed `idx_lessons_uid`, and preserved
  one value across DB row, manifest, and event.
- **Canonical creation/publication works.** All 11 fixture cases met their
  expected version/outcome/codes, and all 10 canonical fixture manifests
  round-tripped byte-identically. The atomic writer replaced an invented
  symlink destination with a mode-0600 regular file without changing its
  target. This preserves the prior hard-link/FIFO/symlink destination fix.
- **Primary bundle/page symlink controls hold.** A symlinked bundle or manifest
  rejects, and a page path with any symlink segment is missing rather than
  followed. B3 is the distinct legacy-source read outside that checked page
  path.
- **Generated agent instructions remain non-injectable from lesson metadata.**
  `AGENTS.md`/`CLAUDE.md` are still constant templates written with the reviewed
  atomic brief writer; title/source data stays in `lesson.json`.
- **Network posture is unchanged.** This slice registers no listener and does
  not alter the trusted-Host/write-origin middleware. The main app remains
  unauthenticated and suitable only for the documented direct-loopback posture;
  B2/B3/B4 become more consequential if that boundary widens.

## Verification

- `git diff 5250768^ 1227d29 --check` — passed.
- Fixture reader expectations — 11/11 passed; canonical byte round-trips —
  10/10 passed.
- Focused temporary DB/filesystem probes — schema v10 + unique UID index +
  DB/manifest/event UID echo passed; atomic symlink-target preservation and
  mode 0600 passed; current rejected-resource, stale-selection, and masked-
  duplicate fixes passed.
- Adversarial temporary probes confirmed B1–B4 with invented data only: URL /
  deep-JSON exceptions, directory/FIFO behavior, 200,132-byte → 9,400,287-byte
  finding amplification, absolute permission-error detail, legacy-source
  symlink copying, and generic artifact-file visibility.
- `PYTHONDONTWRITEBYTECODE=1 timeout 90s .venv/bin/python -u verify.py` on exact
  current head passed the default-off and explicit-opt-in terminal wiring checks,
  then stalled at TestClient startup and timed out (exit 124). The matching
  `verify_restore.py` run stalled at the same known environment boundary and
  timed out (exit 124), with no failing assertion observed. This review therefore
  does not independently claim the commit messages' 410+28 full-suite result.

## Deploy verdict

**Direct-loopback deployment: YES, with four Low follow-ups and one Info contract
cleanup.** The remaining issues require local/runtime bundle influence or expose
data only to clients already inside the loopback trust boundary, and the initial
fail-open reader gaps are fixed on current head. **Wider deployment: NO** — v0
still has no authentication, and B2–B4 add concrete path/content disclosure
reasons not to widen the binding.
