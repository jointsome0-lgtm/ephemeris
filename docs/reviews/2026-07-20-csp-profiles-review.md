# Per-profile lesson preview CSP — adversarial security review

**Scope:** the Pending queue entry dated 2026-07-20 and its code commit
`66defd3` on branch `fix/39-d1-csp-profiles` (review checkout head `79eeff6`;
the head-only delta is the queue entry). The commit adds profile-selected
preview CSP headers, `ManifestRead.bridge_eligible`, profile/bridge metadata,
verification checks, and the landed D1 contract text. The complete changed
functions and their manifest-reader, file-resolution, preview-route,
live-reload, template-sandbox, and security-middleware call paths were read.

**Context:** v0 has no authentication. Per `AGENTS.md`, the deployment decision
assumes a service bound directly to loopback. Lesson bundles are private
runtime data and can be edited by the study agent, so a manifest transition is
normal runtime behavior rather than a hypothetical corrupt-state-only case.
Wider exposure remains unsupported independently of this review.

**Method:** diffed `66defd3^..66defd3`, checked the queue-only delta
`66defd3..79eeff6`, and treated the new verifier assertions as claims rather
than evidence. Re-read the earlier bundle-schema/runtime, write-guard, terminal
opt-in, and generated-brief reports for the same Learn surface. Static tracing
was supplemented by an invented temporary bundle probe that changed only
`runtime.profile` while keeping the page bytes and mtime fixed.

**Summary verdict:** no Critical, High, or Medium finding under the documented
direct-loopback posture. One Low finding remains: a manifest-only profile
upgrade does not invalidate the open iframe's reload token, so the document can
continue executing under the old permissive CSP after metadata already reports
the strict profile and bridge eligibility.

## Findings (severity-ranked)

### C1 — A profile change does not reload the iframe, leaving policy and bridge metadata out of sync (Low, confirmed)

For an existing page, `_file_info()` derives `version` only from the page
file's `st_mtime_ns` (`app/services/lessons.py:319-330`). The newly added
`profile` and `bridge` values come independently from the current manifest read
(`app/services/lessons.py:331-338`). Preview metadata returns that unchanged
file version alongside the new profile/bridge state
(`app/main.py:1237-1252`), while the Learn poller reloads the iframe only when
the version string changes (`app/static/app.js:112-127`). Manifest state is
folded into version tokens for missing/rejected placeholders, but not for an
existing page.

The focused invented probe created one regular v2 page under
`legacy-display`, read its file metadata, canonically rewrote only the manifest
profile to `interactive-local-v1`, and read the metadata again. It produced:

```text
before: [same-page-mtime, legacy-display, false]
after:  [same-page-mtime, interactive-local-v1, true]
version_unchanged: true
```

Consequently an already open legacy document keeps the historical CSP — remote
HTTPS loads, `unsafe-eval`, forms, popups, and downloads — until a page-byte
change or manual navigation reloads it, even though subsequent metadata says
the strict interactive policy governs it. The reverse transition is stale in
the safer direction, but the normal legacy-to-interactive upgrade is precisely
where D1 is expected to tighten enforcement.

There is no postMessage bridge in this commit, so current impact is limited to
stale policy in a loopback-only page and is Low. The mismatch becomes a hard D2
boundary: a parent that offers the port from the new `bridge: true` metadata
could hand it to a document that was actually loaded under the legacy policy.
`bridge_eligible` is correct as a manifest fact, but it is not bound to the
currently loaded response.

Include manifest policy state in the version/revision token for existing pages
(for example, page mtime plus manifest mtime or an effective-profile token), so
every effective-profile transition forces a new request and CSP response. D2
must additionally bind bridge setup to that exact loaded revision rather than
trusting a later uncorrelated metadata read. Add a regression check that flips
both profile directions without touching page bytes and requires the advertised
version to change.

## Confirmed protections and regression checks

- **Fresh responses select the intended registered policy.** Interactive HTML
  and SVG responses receive the local-only CSP; legacy responses retain the
  historical compatibility policy. `_preview_csp()` defaults an actually
  unregistered input to the narrow policy (`app/main.py:1116-1154`).
- **The strict policy itself does not preserve the legacy capabilities.** Its
  header sandbox grants only scripts, `default-src` and `connect-src` are
  `none`, remote/eval script sources are absent, and forms, objects, and base
  URLs are denied (`app/main.py:1126-1142`). The unchanged iframe sandbox can
  only make the combined restriction narrower, not restore capabilities the
  header sandbox omits.
- **Bridge eligibility matches the landed manifest rule.** It requires v2,
  a non-rejected read, and the interactive profile
  (`app/services/bundle_schema.py:158-173`). Unknown/missing profiles, v1,
  rejected reads, and identity mismatches remain legacy/bridge-off; degraded
  findings retain eligibility as the contract explicitly specifies. C1 is the
  separate load-to-metadata binding problem.
- **Earlier bundle protections did not regress.** Manifest reads remain
  bounded and total; rejected manifests do not direct-render pages; v2 resource
  serving remains limited to declared pages and `assets/`; reserved/artifact
  paths and symlinked paths remain blocked; public finding details stay
  path-sanitized. The changed code does not alter the generated agent brief or
  terminal surfaces.
- **Network posture is unchanged.** No listener, Host/origin rule, or global
  response middleware changed. Route CSP still overrides the middleware's
  default `frame-ancestors 'none'` exactly as the earlier write-guard review
  confirmed.

## Verification

- `git diff --check 66defd3^ 66defd3` — passed.
- Commit topology — confirmed: `79eeff6` differs from code head `66defd3` only
  by `docs/reviews/QUEUE.md`.
- Focused invented manifest-transition probe — confirmed C1: profile and bridge
  changed while the existing page version stayed identical.
- `PYTHONPATH=. python verify.py` — made no progress/output and was interrupted
  after approximately 71 seconds. A second bounded unbuffered run passed the
  two terminal-wiring checks and then timed out after 30 seconds at the known
  TestClient startup boundary, with no failing assertion observed. This review
  therefore does not independently claim the commit message's 514-check full
  suite result.

## Final verdict

**Direct-loopback deployment: YES, with C1 as a Low follow-up. D2 bridge work:
NO until the loaded document's CSP/profile is bound to the metadata/revision
used to grant the bridge. Wider deployment: NO — v0 remains unauthenticated.**

## Addendum — follow-up commits `2ce1c0e` and `38ef45e`

Reviewed both commits against C1 and the original direct-loopback security
posture, including the effective-profile read path, existing-page metadata,
preview response headers, live-reload consumer, and the added verifier checks.

- **`2ce1c0e` — no new finding; C1 not yet resolved by this commit.** This is a
  documentation-only clarification at the policy definition and in spec §5.
  It accurately records the pre-existing same-frame-navigation residual and
  assigns observable teardown to D2 without changing or overstating D1's
  enforcement. The residual remains acceptable under the reviewed
  direct-loopback posture, but this commit does not alter the reload token and
  therefore does not itself address C1.
- **`38ef45e` — no new finding; C1 resolved.** Existing-page versions are now
  `mtime:effective-profile`, while missing and rejected pages retain their
  manifest-derived tokens. Because the live-reload consumer treats the value
  as opaque, either effective-profile transition changes the token and forces
  a fresh preview request/CSP response without introducing a compatibility
  issue. The spec records the binding, and the two added checks cover both
  profile-flip directions with unchanged page bytes plus the
  identity-mismatch fail-closed path (`legacy-display`, bridge revoked).

Verification: `git diff --check 79eeff6..38ef45e` passed. A bounded
`PYTHONPATH=. python -u verify.py` run again passed the two terminal-wiring
checks, then made no further progress at the previously observed TestClient
startup boundary and timed out after 120 seconds; no failing assertion was
observed, so this addendum does not independently claim the full-suite result.

**Updated verdict: Direct-loopback deployment: YES; C1 is resolved by
`38ef45e`, and neither follow-up commit introduces a new finding. D2 bridge
work: YES to proceed, provided it binds bridge setup to the exact loaded
revision as required by spec §5. Wider deployment: NO — v0 remains
unauthenticated.**

## Second addendum — follow-up commit `f7db9e1`

Reviewed `f7db9e1` against the prior findings and verdict. It introduces no new
finding. Adding CSP3 `webrtc 'block'` to the interactive policy narrows an
additional egress channel on engines that enforce the directive and does not
widen the policy on engines that ignore it. Spec §5 accurately records the
partial browser enforcement and carries Chromium WebRTC into the accepted
direct-loopback residual instead of overstating the protection. The added
verifier assertion confirms that the strict policy contains the directive;
the change does not disturb the effective-profile revision binding that
resolved C1.

Verification: `git diff --check 38ef45e..f7db9e1` passed, and commit topology
confirms that `f7db9e1` is directly based on `38ef45e`.

**Updated verdict: Direct-loopback deployment: YES; C1 remains resolved, and
`f7db9e1` introduces no new finding. D2 bridge work: YES to proceed, provided
it binds bridge setup to the exact loaded revision as required by spec §5.
Wider deployment: NO — v0 remains unauthenticated.**

## Third addendum — follow-up commit `625bbb8`

Reviewed `625bbb8` against the prior findings and verdict. It introduces no new
finding. `ManifestRead.effective_profile` makes the already required
fail-closed result explicit for every consumer: once a read is rejected it
reports `legacy-display`, even when v2 parsing assigned
`interactive-local-v1` before a later `no-pages`, `duplicate-id`, or other
rejecting finding was discovered. All lesson metadata, resource, preview-CSP,
revision-token, and bridge-eligibility paths now use that effective value, so
rejected metadata cannot advertise the parsed interactive profile or enable
the bridge. The existing-page revision binding that resolved C1 is preserved;
it now also uses the fail-closed effective profile.

The added verifier check covers one representative late rejection
(`no-pages`) end to end: legacy profile metadata, bridge disabled, and the
legacy-CSP rejected placeholder. Static inspection confirms the accessor also
covers other late rejection causes, including duplicate ids.

Verification: `git diff --check f7db9e1..625bbb8` passed, and commit topology
confirms that `625bbb8` is directly based on `f7db9e1`.

**Updated verdict: Direct-loopback deployment: YES; C1 remains resolved, and
`625bbb8` introduces no new finding. D2 bridge work: YES to proceed, provided
it binds bridge setup to the exact loaded revision as required by spec §5.
Wider deployment: NO — v0 remains unauthenticated.**
