# Lesson brief bridge conventions — adversarial security review

**Scope:** commit `6e7b7b5` against parent `1565bd4` — the generated lesson
`AGENTS.md` gains bridge-client conventions and `verify.py` gains one static
presence check. The complete changed template section and its surrounding
teaching contract were read in the current tree, together with the unchanged
brief writer and lesson-terminal caller, the bridge ABI and parent runtime, the
runtime-profile CSP/sandbox path, the bundle attempt contract, and the earlier
reports for the lesson brief, writer, CSP profiles, and bridge runtime. The
relevant implementation and contracts are unchanged between `6e7b7b5` and the
current tree (`50fb404`).

**Context:** v0 has no authentication and the deployment verdict assumes the
service is bound directly to loopback. Interactive lesson pages are untrusted
opaque-origin iframe documents. ABI v1 grants no write capability and implements
only ping/pong; the new brief is nevertheless intended to shape pages that will
use the future D4/D5 attempt capability.

**Method:** diffed the exact commit, treated its verifier anchor as a claim
rather than proof, traced the generated text through atomic brief publication
and lesson PTY startup, compared every handshake, identity, capability,
question, and retry statement with `docs/lesson-bridge-abi.md` and
`docs/learn-bundle-spec.md`, and re-checked the earlier instruction/data,
symlink, writer, CSP, document-confusion, served-byte, and network-posture
findings for regression.

**Verdict:** no Critical, High, or Medium finding. Two Low instruction-contract
findings affect future capability-bearing pages, not the current empty-capability
runtime. The direct-loopback ABI-v1 deployment remains safe to make live. D4/D5
must not rely on pages generated from this exact brief until both findings are
fixed, in addition to the bridge runtime report's still-binding L1/L2 gate.

## Findings (severity-ranked)

### B1 — The brief does not authenticate an inbound `welcome` or transferred port (Low, confirmed)

The new handshake instruction tells a page where to send `ready` and says that
`welcome` transfers the port, but gives no receiving rule for `welcome` or
`reject` (`app/services/lessons.py:795-803`). In particular it does not require
the handler to verify `event.source === window.parent`, require the expected app
origin, validate the lesson-bridge marker/type/selected ABI, require exactly one
transferred `MessagePort`, or consume only one terminal handshake result. The
verifier likewise pins only outbound wording and broad identity/capability
phrases (`verify.py:325-334`).

A page produced from this instruction can therefore reasonably install a
generic `message` handler and accept a forged `welcome` carrying
`capabilities: ["attempts"]` and an attacker-controlled port. Any window with a
reference to that lesson window can send such a message and transfer one end of
a channel. The page would then send the learner's invented answer to that port
instead of degrading read-only. The risk is most direct in the brief's promised
standalone/"any context" case (`app/services/lessons.py:811-817`): a file-opened
page has no HTTP `frame-ancestors` policy and must not treat an arbitrary
embedder or opener as the Learn app.

This remains Low under the reviewed deployment because the normal served page
has the app's trusted parent, the strict response policy limits embedding, and
ABI v1 never grants `attempts`. It becomes a learner-data disclosure and
confused-deputy boundary once generated pages can send answers. Require inbound
handshake authentication: accept only the parent window at the exact expected
non-opaque app origin, validate the full protocol envelope and selected ABI,
require the transferred port, and consume at most one valid result. An opaque
or non-app origin must skip the handshake and stay read-only. Add a generated
client/browser case in which a foreign message with a forged capability and
port is ignored.

### B2 — `request_id` guidance omits lesson-wide uniqueness across distinct submissions (Low, confirmed)

The brief says only that a page chooses a 1–128 character `request_id` and must
reuse it while retrying one submission (`app/services/lessons.py:809-810`). The
authoritative attempt contract additionally requires the idempotency key to be
unique per lesson. Replaying a known key for the same question/page returns the
old attempt without writing, while reusing it for another question/page returns
an idempotency conflict (`docs/learn-bundle-spec.md:390-400`).

Without the missing positive rule, a generated page can plausibly choose a
constant or question-derived key and reuse it for every later click. A changed
answer to the same question then appears successfully deduplicated but is not
recorded; sharing that key across questions rejects otherwise valid attempts.
That is a durable attempt-integrity failure, and it is not caught by the static
check that merely requires the string `` `request_id` `` to occur
(`verify.py:325-334`).

There is no current write impact because ABI v1 rejects every non-ping operation
and grants an empty capability set. Before attempts land, state explicitly that
each new logical submission gets a fresh opaque key unique across the lesson,
that the key is retained only for retries of those exact answer bytes, and that
editing or submitting again gets a new key. Pin that distinction in regression
coverage.

## Confirmed protections and regression checks

- **The capability and identity defaults remain fail-closed.** The brief says
  that identity comes only from the parent, forbids the child from supplying
  lesson/page identity, requires a declared manifest `question_id`, and treats
  silence, rejection, or a missing `attempts` grant as read-only. The shipped
  parent still returns `capabilities: []` and supports only ping/pong. B1 is the
  missing child-side authentication of the message that claims those facts;
  B2 is the missing distinct-submission side of the future idempotency rule.
- **Earlier lesson-brief findings have not regressed.** `_AGENTS_TEMPLATE`
  remains constant; lesson metadata, sources, pages, assets, and attempt data
  remain explicitly untrusted data rather than instructions. The whole-bundle
  no-symlink rule, bounded artifact discovery, immutable identity fields,
  unknown-field preservation, and app-owned/read-only `attempts.jsonl` rules
  remain present.
- **Brief publication and terminal trust gates are unchanged.** The mode-0600
  same-directory temporary-file writer still fsyncs and atomically replaces
  both generated entry points without opening planted destinations
  (`app/services/lessons.py:855-879`). Workspace preparation still refuses an
  invalid or unsafe lesson before spawning, and the terminal keeps its opt-in,
  loopback peer, exact Host/Origin, allowlisted-environment, and fail-closed
  lesson-workspace behavior. No listener, route, PTY/WS lifecycle, or runtime
  filesystem write was added by `6e7b7b5`.
- **The bridge runtime's residual gate remains visible rather than regressed.**
  The current ABI accurately records the navigation-stable `WindowProxy`
  document-confusion residuals and mandates per-operation server-side identity
  validation for D4+. The earlier review's L2 served-byte binding gap also
  remains. This text-only commit neither worsens nor resolves them, so its
  future-facing "pick up recording" sentence cannot authorize capability work
  around that gate.

## Non-security contract limitations

- The statement that pages built today will "pick up recording with no edits"
  overpromises the frozen contract: ABI v1 defines only the ping request shape;
  the attempt operation and response shapes do not yet exist. A safe page can
  scaffold handshake/capability detection now, but must implement the frozen D4
  operation when it lands rather than inventing a write message today.
- For a direct `file:` open, `new URL(location.href).origin` is opaque
  (`"null"`), not an app origin suitable for the instructed exact-target
  handshake. The standalone path should bypass bridge setup (and catch setup
  failures) so the promised read-only interactions remain usable.

## Verification

- `git diff --check 6e7b7b5^ 6e7b7b5` — passed.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile
  app/services/lessons.py verify.py` — passed.
- Contract comparison — confirmed that the generated brief contains no inbound
  `welcome` source/origin/protocol validation rule and no requirement that
  distinct submissions receive lesson-wide unique keys.
- `env -u ACTIVITY_DATA_DIR PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 timeout 20s
  .venv/bin/python -u verify.py` — inconclusive in this environment: the two
  terminal-wiring checks passed, then the known TestClient startup boundary
  stalled until exit `124`; no failing assertion was emitted. This review does
  not independently claim the queue entry's 535-check result.

## Deploy verdict

**Current ABI-v1 direct-loopback deployment: YES**, with B1 and B2 as Low
future-capability findings because the shipped bridge grants no write
capability. **D4/D5 capability-bearing deployment: NO** until B1 and B2 are
fixed and the bridge runtime review's L1 document-confusion and L2 served-byte
gates are resolved with per-operation server-side identity/revision validation.
**Wider deployment: NO** — v0 remains unauthenticated.

Per the caller's explicit instruction, this report does not edit
`docs/reviews/QUEUE.md`; the entry remains Pending for the maintainer to close.

## Closing addendum — commit `8c82f1b`

**Scope:** re-review of the exact `8c82f1b^..8c82f1b` diff against the findings
above and the standing brief in `docs/reviews/review-prompt.md`. The resulting
bridge-conventions section, the frozen handshake envelopes in
`docs/lesson-bridge-abi.md`, the lesson-wide idempotency contract in
`docs/learn-bundle-spec.md`, and the bridge runtime review's remaining L1/L2
gates were checked. This commit changes only generated instruction text and its
static presence anchors; it does not add a lesson client or change runtime
authority.

**Result:** B2 and the non-security recording-contract limitation are resolved.
B1 is materially narrowed but remains Low because the receiving rule still
omits the expected-origin check required by the original finding. The fix also
introduces one fail-closed, non-security reject-envelope inconsistency. No new
Critical, High, Medium, or Low security finding is introduced.

### B1 — partially resolved; expected-origin residual remains Low

The new brief now requires `event.source === window.parent`, the lesson-bridge
marker and expected type, an announced ABI, exactly one transferred port for a
`welcome`, and at most one accepted handshake result. It also skips the
handshake for a direct `file:` open, whose URL origin is `"null"`
(`app/services/lessons.py:800-811`). Those rules close the original arbitrary-
window and opaque-file-embed cases: a sibling, opener, or other window can no
longer supply the accepted port, and a file-opened page never asks for one.

The rule does not, however, require
`event.origin === new URL(location.href).origin`. A lesson copy served over
HTTP outside the app can be embedded by a cross-origin parent in a context that
does not carry Ephemeris's `frame-ancestors 'self'` response policy. That parent
is exactly `window.parent`; it can send an unsolicited, correctly shaped
`welcome` with an announced ABI, a claimed `attempts` capability, and one
attacker-controlled port. Every receiving check now specified by the brief
passes, so a generated future client can still disclose an invented learner
answer to the embedder instead of remaining read-only. The outbound
`targetOrigin` does not authenticate this unsolicited inbound message.

This residual remains Low under the reviewed deployment because the normal app
response restricts framing to self and ABI v1 grants no write capability. B1 is
not fully resolved until the non-opaque path also compares `event.origin` with
the page URL's exact expected app origin (in addition to the new source,
envelope, port, and one-result checks), with a foreign-parent forged-welcome
browser case pinned before capability-bearing pages ship.

### B2 — resolved

The brief now requires a fresh opaque `request_id`, unique across the whole
lesson, for every new logical submission; permits reuse only for a retry of
that exact submission; and explicitly gives changed or re-entered answers new
ids while forbidding constant and question-derived keys
(`app/services/lessons.py:820-825`). That closes both silent coalescing of later
answers and cross-question idempotency conflicts identified by B2. The added
verifier anchor pins the lesson-wide uniqueness phrase (`verify.py:325-340`).
No new security finding is introduced by this fix.

### Non-security “pick up recording with no edits” limitation — resolved

The replacement text accurately separates safe scaffolding from the unfrozen
write operation: pages may implement handshake, capability detection, declared
ids, and degradation now, but must not invent a write request and must add the
recording call only after the app freezes the attempt operation and actually
grants `attempts` (`app/services/lessons.py:833-838`). It no longer promises
that today's page will acquire recording without an edit.

### N1 — the new reject rule requires a field the frozen reject omits (non-security)

The new receiving sentence applies “the selected `abi` is one you announced”
to both `welcome` and `reject` (`app/services/lessons.py:804-808`). The frozen
`welcome` has scalar `abi: 1`, but the frozen `reject` has only
`reason: "abi-unsupported"` and `supported: [1]`; it has no selected ABI
(`docs/lesson-bridge-abi.md:76-95`). A client following the brief literally
therefore cannot accept any conforming reject and waits for the silence timeout
instead. That is a bounded availability/diagnostic defect and stays fail-closed
and read-only, so it is not a security-severity finding. The selected-ABI test
should be scoped to `welcome`; `reject` should be validated against its own
frozen envelope.

### Verification

- `git diff --check 8c82f1b^ 8c82f1b` — passed.
- `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m py_compile
  app/services/lessons.py verify.py` — passed.
- Static contract comparison — confirmed B2's lesson-wide uniqueness and the
  no-invented-write rule; confirmed that the receiving text contains no
  `event.origin` rule and applies the selected-ABI requirement to `reject`.
- `env -u ACTIVITY_DATA_DIR PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 timeout 20s
  .venv/bin/python -u verify.py` — inconclusive in this environment: the two
  terminal-wiring checks passed, then the known TestClient startup boundary
  stalled until exit `124`; no failing assertion was emitted. This addendum
  does not independently claim the commit message's 535-check result.

## Superseding deploy verdict

**Current ABI-v1 direct-loopback deployment: YES**, with the narrowed B1
expected-origin residual Low because the shipped bridge grants no write
capability. **D4/D5 capability-bearing deployment: NO** until B1 is fully
closed and the bridge runtime review's remaining L1 document-confusion and L2
served-byte gates are resolved and browser-tested, with per-operation
server-side authority/revision validation mandatory. **Wider deployment: NO**
— v0 remains unauthenticated. B2 and the recording-contract limitation are
closed; N1 is fail-closed and does not change this verdict.

## Second closing note — commit `841c37c`

Re-review of the exact `841c37c^..841c37c` diff against the standing brief and
the frozen bridge ABI closes both remaining items. **B1 is fully resolved:** an
accepted `welcome` or `reject` must now come from `window.parent` at the exact
origin derived from the served page URL, while `welcome` alone must select an
announced ABI and transfer exactly one port. This authenticates the intended
same-origin app parent even though the sandboxed child itself has an opaque
origin. **N1 is closed:** the selected-ABI and transferred-port requirements
are now scoped to `welcome`; `reject` is correctly described as carrying its
own `reason`/`supported` fields without either item. No new security or
non-security finding was introduced. `git diff --check` and Python compilation
of the two committed files passed.

**Final superseding deploy verdict — SAFE TO MAKE LIVE: YES for this queue
entry under the documented direct-loopback ABI-v1 posture.** B1, B2, N1, and
the recording-contract limitation are closed. This does not lift the separate
bridge-runtime gate: D4/D5 capability-bearing deployment remains **NO** until
that report's L1 document-confusion and L2 served-byte conditions are resolved
and browser-tested with per-operation server-side authority/revision
validation; wider deployment remains **NO** while v0 is unauthenticated.
