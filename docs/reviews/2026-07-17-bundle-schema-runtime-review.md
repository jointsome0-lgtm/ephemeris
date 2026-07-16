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

## Addendum — `41224b5` fix review

**Scope:** commit `41224b5`, whose exact parent is `1227d29`. The fix changes
the manifest reader, lesson service, and verifier to address B1–B5 above plus a
PR-bot finding about symlinked selected-page outcome aggregation. Its additions
to this report and the queue were treated as claims to verify, not review
evidence.

**Method:** diffed `1227d29..41224b5`, read both changed service modules in
full, and traced the affected paths through the file and preview-meta routes,
event writer/exporter, frozen bundle contract, and earlier lesson-security
reports. Invented temporary probes repeated every original B1–B5 reproducer,
exercised a Python-valid oversized integer token, swapped the legacy source
between its stat and open, requested reserved, artifact, declared-asset, and
undeclared files, and checked the selected-page outcome and creation-event
payload.

**Addendum verdict:** B2, B5, and the PR-bot finding are resolved. B1, B3, and
B4 are materially narrowed but not fully resolved, so three Low findings remain
under the direct-loopback posture. The fix commit introduces no separate new
finding; the newly confirmed integer-token failure is another B1 totality case,
and the B3/B4 issues below are residual parts of the original findings.

### Per-finding status

#### B1 — Partially resolved; parser totality still has an uncaught valid-JSON failure (Low remains)

The fix closes all four original reproducers. `_valid_source_url()` now catches
`urlsplit()` failures; `json.loads()` recursion becomes
`manifest-unreadable`; `read_manifest_path()` opens nonblocking, requires a
regular file by `fstat()`, and catches read errors; list walks truncate at their
contract limits; and findings/details are capped
(`app/services/bundle_schema.py:47-50`, `167-172`, `234-241`, `263-332`,
`476-486`, `530-539`, `583-594`, `669-678`, `725-732`). Focused probes
confirmed that the malformed URL returned `stale-metadata`, 5,000-level JSON
nesting rejected, and directory/FIFO manifests returned immediately as
`manifest-unreadable`. The former 200 KiB amplification shape now produced 100
findings and a 9,371-byte JSON representation rather than 100,003 findings and
9.4 MiB.

However, the parse boundary catches `JSONDecodeError` and `RecursionError`, not
the other `ValueError` that Python's JSON decoder can raise
(`app/services/bundle_schema.py:274-279`). A 5,000-digit integer in an unknown
field is valid JSON and only about 5 KiB, but current Python raises its integer-
conversion limit `ValueError`; `read_manifest_text()` propagates it instead of
returning `manifest-unreadable`. The preview-meta caller does not catch that
exception, so a planted manifest can still turn a poll into a 500. This remains
Low for the same reason as B1: it needs local/importer influence over private
runtime bundle bytes. Make the JSON decode boundary catch this parse-time
`ValueError` as unreadable and add the large-integer probe alongside the depth
case.

#### B2 — Resolved

All filesystem `OSError` details returned by `read_manifest_path()` now use
`exc.strerror` (or a fixed fallback), and non-regular nodes use a constant
detail (`app/services/bundle_schema.py:304-326`). An invented `ENOTDIR` open
failure surfaced only `Not a directory`; the temporary root did not appear in
the finding. No other changed reader path attaches the manifest pathname to a
client-visible detail.

#### B3 — Partially resolved; the legacy read remains check/use raceable (Low remains)

A source that is already a symlink is now refused, and the original planted-
symlink probe no longer created `index.html`. The implementation still performs
three separate operations, though: `is_symlink()`, following `is_file()`, then
following `read_text()` (`app/services/lessons.py:210-219`). It does not bind
the regular-file decision to the file descriptor from which bytes are read.

An invented deterministic swap immediately after `is_file()` returned true
replaced the checked regular source with a symlink; `read_text()` followed it
and copied the external decoy bytes into `index.html`. This is a narrower,
concurrent-filesystem version of B3, not a new finding, but it contradicts the
§2 rule and the new comment that a linked source is never read. Retain the Low
severity under the existing posture: a race requires local bundle-tree
influence, while a future less-trusted importer/writer would make the boundary
meaningful. Open the legacy source with `O_NOFOLLOW`, require a regular file by
`fstat()`, and read from that same descriptor.

#### B4 — Partially resolved; the generic route is still a denylist (Low remains)

The route now returns missing for a rejected manifest, every reserved top-level
name, and every path at or below the validated artifact roots; the original
`lesson.json`, generated-brief, and `attempts/note.txt` probes all returned
missing (`app/services/lessons.py:307-328`). Declared pages and files under
`assets/` remained available as intended.

It still serves every other regular file in the bundle, because the allow path
is simply “not rejected, reserved, artifact-rooted, or symlinked.” An invented
`undeclared-private.html` outside `artifact_roots` was reported as existing and
active even though it was absent from `pages[]`. Thus the original fix direction
— declared pages plus the intended public asset area — is not implemented, and
private/misplaced files remain reachable by a guessed `/files/` path. Keep B4
Low on loopback; for a future wider client this remains a content-disclosure
surface. Make page eligibility come from `read.page_paths()` and define the
non-page asset area positively rather than treating all remaining bundle files
as preview resources.

#### B5 — Resolved

`create_lesson()` no longer includes `title` in `lesson_created`; it retains the
stable UID and the previously allowed lifecycle/location echoes
(`app/services/lessons.py:551-577`). An invented in-memory creation produced a
payload with `lesson_id`, `lesson_uid`, `source_url`, `slug`, and `status`, but
no `title`; the export path preserves that corrected payload unchanged.

#### PR-bot selected-page outcome finding — Resolved

`_file_info()` now promotes an otherwise `ok` outcome to `degraded` when the
selected page resolves through a symlink, alongside the existing
`symlinked-path` finding (`app/services/lessons.py:250-298`). That is the helper
used by preview-meta. The focused probe returned `exists: false`, outcome
`degraded`, and `symlinked-path`.

### New findings introduced by `41224b5`

None. The large-integer exception is a newly exercised instance of B1's
unresolved totality requirement. The B3 stat/open window and B4 denylist are
unclosed portions of their existing findings, not separate regressions created
elsewhere by this fix.

### Addendum verification

- `git diff 1227d29..41224b5 --check` — passed.
- B1 probes — malformed URL, deep JSON, directory/FIFO, and bounded finding
  materialization fixes passed; a 5,000-digit JSON integer raised uncaught
  `ValueError`.
- B2–B5 and PR-bot probes — sanitized open detail, planted legacy symlink
  refusal, reserved/artifact 404 decisions, title-free creation event, and
  selected-page degraded outcome passed. The deterministic legacy-source swap
  copied the decoy, and an undeclared HTML file remained directly available.
- `PYTHONDONTWRITEBYTECODE=1 timeout 90s .venv/bin/python -u verify.py` passed
  the two terminal-wiring checks, then timed out at the known TestClient startup
  boundary (exit 124). `verify_restore.py` timed out at the same boundary (exit
  124). No failing assertion was observed, so this addendum does not
  independently claim the commit message's 419+28 result.

### Superseding deploy verdict

**Direct-loopback deployment: YES, with three Low follow-ups (B1, B3, B4).**
B2's path disclosure and B5's contract mismatch are closed. **Wider deployment:
NO** — v0 still has no authentication, and the residual generic-file exposure
alone prevents treating `/files/` as a wider-client-safe preview boundary.

## Final addendum — `53b5232`, `ca4a7fd`, and `9c188d7`

**Scope:** the three commits after `41224b5`, reviewed at exact branch head
`9c188d7`. `53b5232` addresses exact v2 selection comparison and non-standard
JSON constants. `ca4a7fd` claims the remaining B1/B3/B4 fixes; `9c188d7`
adjusts that new `/files/` policy so an exact declared page wins over an
overlapping artifact root. Together this is the base C3 commit plus all six
same-branch fix follow-ups.

**Method:** diffed `41224b5..9c188d7`, read the changed schema/lesson code and
its route, DB-write, canonical-writer, and contract call sites, and treated the
new verifier cases as claims rather than evidence. Independent invented probes
exercised the huge-integer and all three non-standard constant tokens, a
regular/symlink/FIFO legacy source, a deterministic source-path swap after
open, exact and normalizable v2 read/write selections, and the v2 resource
matrix (declared page, overlapping root, asset, undeclared file, artifact, and
reserved manifest).

**Final addendum verdict:** B1, B3, and B4 are resolved. B2 and B5 remain
resolved, as does the earlier selected-page outcome finding. Both `53b5232`
PR-bot findings and `9c188d7`'s declared-page precedence claim also hold. No
Critical, High, Medium, or Low finding remains from this review. One new
informational canonical-JSON closure issue was found; it does not reopen the
preview availability or `/files/` findings.

### Per-finding status

#### B1 — Resolved

The JSON boundary now maps every decoder `ValueError` to
`manifest-unreadable`, in addition to the existing UTF-8 and recursion handling
(`app/services/bundle_schema.py:270-289`). A 5,000-digit integer token returned
a rejected `ManifestRead` carrying `manifest-unreadable`; it no longer escaped
the reader. The original malformed-URL, deep-nesting, special-file, and finding-
amplification cases remain closed by `41224b5`. N1 below concerns accepted
numeric semantics and writer output, not an exception escaping a preview read.

#### B2 — Resolved (unchanged)

The sanitized filesystem-error details from `41224b5` remain in place
(`app/services/bundle_schema.py:303-342`). None of the three later commits
reintroduces a client-visible absolute manifest path.

#### B3 — Resolved

`_read_regular_no_follow()` opens the legacy source with nonblocking
`O_NOFOLLOW`, requires the opened descriptor itself to be regular via
`fstat()`, and reads bytes through that same descriptor
(`app/services/lessons.py:175-192`, `231-240`). Regular input still bridged;
an already linked source and FIFO were refused immediately. In the decisive
race probe, the pathname was replaced with a symlink after `open()` but before
`fstat()`; the helper returned the original opened file's bytes, not the
symlink target. The prior check/use gap is closed.

#### B4 — Resolved

For v2, `bundle_resource_info()` now positively admits only exact declared
pages and `assets/`, then blocks learner work under artifact roots, reserved or
undeclared paths, rejected manifests, and symlinked paths
(`app/services/lessons.py:327-378`). The independent matrix served
`index.html`, a declared related page, and an asset; it refused an undeclared
top-level HTML file, an undeclared file below an artifact root,
`attempts/note.txt`, and `lesson.json`.

`9c188d7` correctly gives an exact declared page precedence over an overlapping
artifact root while leaving other content under that root blocked
(`app/services/lessons.py:339-355`). A declared `related/page.html` remained
servable with `related` also declared as an artifact root, while
`related/draft.html` stayed unavailable. V1 retains its explicitly documented
compatibility tolerance behind the same reserved/artifact/symlink exclusions;
that is not a residual v2 allowlist gap.

#### B5 — Resolved (unchanged)

The title-free `lesson_created` payload from `41224b5` is unchanged
(`app/services/lessons.py:585-618`). The later commits do not modify lesson
event payloads.

#### Earlier PR-bot selected-page outcome finding — Resolved (unchanged)

The selected-page symlink branch still promotes an otherwise `ok` outcome to
`degraded` and surfaces `symlinked-path`
(`app/services/lessons.py:270-318`). No later diff weakens that aggregation.

#### PR-bot exact v2 selection comparison — Resolved

`_resolve_entry()` compares the requested/stored v2 string directly with
`page_paths()` before any cleanup, falling back with `invalid-entry`; only v1
uses `_clean_html_ref()` (`app/services/lessons.py:253-267`). The explicit DB
write follows the same version split (`app/services/lessons.py:635-655`). A
`./related/page.html` probe degraded and fell back on read, was refused without
mutating `current_entry` on write, and the exact `related/page.html` value was
accepted and stored.

#### PR-bot NaN/Infinity rejection — Resolved as stated

`json.loads()` now uses a `parse_constant` callback that rejects `NaN`,
`Infinity`, and `-Infinity`; the encompassing `ValueError` handler returns
`manifest-unreadable` (`app/services/bundle_schema.py:252-289`). Independent
probes confirmed that result for all three tokens. N1 is adjacent but distinct:
its input uses valid JSON number syntax rather than any of those extension
tokens.

#### PR-bot declared-page precedence — Resolved

The `declared_page` decision is computed once and exempts only that exact path
from the artifact-root block (`app/services/lessons.py:339-348`). The overlap
probe described under B4 confirms both halves: the page stays servable and
undeclared sibling work does not.

### New finding

#### N1 — Exponent overflow can make an accepted read serialize as invalid JSON (Info, confirmed)

The decoder's default float conversion accepts a valid JSON number such as
`1e9999` as Python positive infinity. `parse_constant` is not invoked because
the input contains no `Infinity` token. A complete invented v2 manifest with an
unknown `"x_number": 1e9999` therefore read as `ok` and preserved `inf` in
`ManifestRead.raw`; `canonical_dumps()` then emitted `"x_number": Infinity`,
which the hardened reader rejected on the next read
(`app/services/bundle_schema.py:270-300`, `787-809`). This violates the
unknown-field semantic-preservation and accepted canonical round-trip contract
(`docs/learn-bundle-spec.md:512-540`).

This is informational in the current C3 runtime: the app calls the manifest
writer for a missing-manifest creation skeleton, not to rewrite an existing
accepted v2 manifest (`app/services/lessons.py:217-221`). It is nevertheless a
real writer-boundary defect for a later migration/editor that round-trips
`read.raw`. Reject non-finite `parse_float` results at read time and make the
writer fail closed with `allow_nan=False`; add the exponent form beside the
literal-token probes.

### Final addendum verification

- Commit ancestry from `5250768` through the six fix follow-ups is linear;
  exact head was `9c188d7`. `git diff 41224b5..9c188d7 --check` passed.
- Independent targeted assertions — 19/19 passed: huge integer; three literal
  constants; regular, linked, FIFO, and swapped legacy sources; seven v2
  resource decisions; exact/normalizable selection reads and DB writes.
- Existing fixture expectations — 11/11 passed; canonical fixture byte-round-
  trips — 10/10 passed.
- The separate exponent-overflow probe read the valid v2 input as `ok`, emitted
  `Infinity` through the canonical writer, then rejected that output as
  `manifest-unreadable`, confirming N1.
- `PYTHONDONTWRITEBYTECODE=1 timeout 90s .venv/bin/python -u verify.py` on
  `9c188d7` passed both terminal-wiring subprocess checks, then timed out at the
  previously documented TestClient startup boundary (exit 124). No failing
  assertion appeared, so this addendum does not independently claim the commit
  messages' full-suite count.

### Final superseding deploy verdict

**Direct-loopback deployment: YES, with no remaining security-severity
follow-up from this review; N1 is an informational canonical-writer cleanup.**
**Wider deployment: NO** — v0 remains unauthenticated, so resolving B4's file-
route policy does not authorize widening the documented loopback-only posture.

## Closing note — `5388efe` resolves N1

Written by the session converging this drain, documenting resolution (not an
adversarial pass): the PR review bot independently reported N1's exact shape
(a `1e10000` token) against head `9c188d7`, converging with the final
addendum. `5388efe` closes it at the same total parse boundary —
`json.loads` now runs with a `parse_float` that rejects non-finite values, so
an overflowing exponent reads as `manifest-unreadable` and the canonical
writer is never handed a non-finite value to re-emit as `Infinity`. verify
carries the matching probe ("overflowing float token is manifest-unreadable");
427 green on `5388efe`. With N1 closed, no finding from this review — any
severity — remains open on the branch head.

## Resolution section — rounds 7–12 (`6a690b2..cdeda5b`)

Written by the session converging this drain, documenting resolution (not an
adversarial pass). After the closing note above, PR #48 went through six more
PR-review-bot rounds; every push was reviewed individually by the bot and each
round's finding was fixed in its own commit:

- `6a690b2` (round 7) — placeholder version tokens now derive from manifest
  state (`lstat` mtime), so a rejected→repaired manifest transition is visible
  to the live-reload poller instead of pinning at a constant token.
- `825bec6` (round 8) — a dangling symlink at the bundle directory itself
  rejects as `symlinked-bundle`; previously `mkdir` hit `FileExistsError` and
  the route returned 500.
- `6fde64a` (round 9) — the preview surface (declared pages + `assets/`) wins
  over an overlapping artifact root, so a manifest cannot 404 its own declared
  pages by declaring a root above them.
- `5a9fd04` (round 10) — the injected `attempts` root participates in the
  root-overlap pass (injection happens before the pass), and v1 bundles are
  exempt from artifact-root blocking, restoring their full historical file
  surface.
- `4d5b20d` (round 11) — artifact roots at or under `assets/` are dropped with
  an `overlapping-roots` finding; `docs/learn-bundle-spec.md` §7 amended to
  state the rule (roots MUST NOT be or nest under `assets`).
- `cdeda5b` (round 12) — `bundle_info` snapshots outcome/findings after
  selection resolution, so a stale v2 selection's `invalid-entry` finding
  reaches the top-level `outcome` that agent-facing callers key off.

Verify on the head: 434 passed, 0 failed; all canonical fixtures still
byte-round-trip. Bot verdict on head `cdeda5b`: APPROVED — 👍 reaction on the
PR body from chatgpt-codex-connector[bot] at 2026-07-16T23:20:56Z. No finding
from any of the three review streams (this drain, the Opus second pass, the
PR bot) remains open on the branch head. The deploy verdict above is
unchanged: direct-loopback YES, wider deployment NO.

### Round 13 — `dd9c1c3`

The bot's review of the docs push `f331ec5` carried one more code finding:
`bundle_info()`'s top-level outcome/findings snapshot, moved after selection
resolution in round 12, was still taken before `_file_info()` — so a current
page that resolves through a symlink degraded only the nested `file` object
while the agent-facing top level said `ok`. `dd9c1c3` makes the top-level
snapshot the current file's own outcome/findings (a superset of the manifest
read's), with a matching verify check; 435 passed, 0 failed. The bot's
follow-up review of `dd9c1c3` raised no finding against the code change —
only that this report and the queue entry must stay current with the branch
head, which this section and the amended entry now do. (That review cited a
commit `96e224e` that exists neither locally nor on GitHub — the third
hallucinated hash this PR; the repository merges via merge commits, not
squash. The coverage-gap substance was real and is addressed.)

### Round 15 — `4b88b6f`

The bot's review of `50e41b0` found one more finding-masking case in the
block reader: an unrecognized `kind` dropped the block before the
file-vs-roots containment check ran, so a block violating both fields
reported only `unknown-kind`. `4b88b6f` validates the two fields jointly —
`unknown-kind` and `outside-root` are both recorded before the drop, with a
matching verify probe; 436 passed, 0 failed. (Round 14 itself was a
documentation-coverage finding against the queue entry, addressed in
`50e41b0`; its cited squash commit `96e224e` exists neither locally nor on
GitHub and was rebutted in-thread.)
