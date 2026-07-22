# Lesson bridge ABI (v1)

Status: frozen with D2; extended additively by D5 (§3.1, `attempts`) and
phase F (§3.2, `editor`; §3.3, `run`). This is the contract between the Learn page's parent runtime
(`app/static/src/learn-bridge.ts`, emitted `learn-bridge.js`) and a lesson
page running inside the sandboxed preview iframe. The bundle contract that
decides *whether* a page may be bridged lives in
[learn-bundle-spec.md](learn-bundle-spec.md) (§5 profiles, §6.3 identity);
this document owns the wire shapes. The handshake is unchanged since D2.

ABI v1 shipped with **no write capability** — the membrane itself
(versioning, identity ownership, teardown rules) landed before any
state-changing operation existed. D5 added the one write capability,
`attempts` (§3.1), and phase F adds the independent editor and run operations
(§3.2–§3.3), within v1: a child that never asks for them sees exactly the
original protocol.

## 1. Trust model

- The lesson document is **untrusted content** in an opaque-origin sandbox
  (`sandbox allow-scripts`, CSP per §5). The parent runtime is trusted app
  code on the app origin.
- The **parent owns identity**: `lesson_uid`, `page_id`, `page_rev`, and the
  granted capability set all come from the parent, which reads them from the
  app's preview metadata (server-derived, spec §6.3). The child never
  supplies a route, slug, path, or id — a child claim about "which page I
  am" has no channel to travel on.
- The parent binds identity to the **loaded revision**: it arms a handshake
  only while the metadata's version token equals the token of the navigation
  it performed itself (D1 folds the effective profile into that token). A
  metadata read that disagrees triggers reload, never a grant.
- Messages **to** the child use `targetOrigin: "*"` — an opaque origin is
  not addressable by value. This is acceptable because (a) the parent posts
  only to the specific `contentWindow` it navigated, (b) the welcome payload
  contains page identity, no secrets, and (c) the actual channel is the
  transferred `MessagePort`: capability follows possession of the port, not
  the ability to observe a broadcast.
- Messages **from** the child are accepted only when
  `event.source === frame.contentWindow`, the parent is armed for the
  current document, and no grant has been consumed yet.

## 2. Handshake

The child initiates (the parent cannot know when the child's listener is
installed; a child that never asks is simply a display page):

```text
child → parent (window.parent.postMessage)
  { "ephemeris": "lesson-bridge", "type": "ready",
    "abi": [1],                  // supported ABI versions, 1–8 integers
    "want": ["attempts", "editor", "run"] } // optional wishes, ≤16 × ≤64 chars
```

Constraints: serialized JSON ≤ 4096 UTF-8 bytes; `abi` entries are integers
1–999.
Malformed or oversized announcements are ignored (no reply — nothing to
negotiate with). The child SHOULD post to `window.parent` with targetOrigin
`new URL(location.href).origin` (its own document URL is the app origin even
though `window.origin` is `"null"` in the sandbox).

A child usually announces the moment its script runs — before the parent
has finished binding identity for the freshly loaded document (and, across
a parent-initiated reload, possibly before the parent processed the load).
Children MUST therefore re-announce `ready` periodically (every 250–500 ms
is fine) until they receive a `welcome` or `reject`, giving up after the
~2 s silence budget below. The parent answers announcements only on live
receipt while it is armed; earlier ones are deliberately dropped, never
buffered — an announcement held across the parent's async identity binding
could otherwise be answered into a successor document after a same-frame
navigation. In the common case a retry within the first half-second
completes the handshake. Duplicate announcements after a `welcome` are
ignored (one grant per document).

On a valid announcement whose `abi` contains a version the parent speaks
(v1: the literal `1`), the parent replies **once per loaded document**:

```text
parent → child (postMessage with one transferred MessagePort)
  { "ephemeris": "lesson-bridge", "type": "welcome",
    "abi": 1,                    // the selected version
    "lesson": { "lesson_uid": "…", "page_id": "pg_…",
                "page_rev": "sha256:…" },
    "capabilities": ["attempts", "editor", "run"] }
                                      // granted = want ∩ available routes/metadata
```

The transferred port is the bridge. Everything after the welcome flows over
it; the parent's global `message` listener never answers the same document
twice (a second `ready` is ignored until the next navigation).

If no supported version overlaps:

```text
parent → child
  { "ephemeris": "lesson-bridge", "type": "reject",
    "reason": "abi-unsupported", "supported": [1] }
```

At most 3 rejects are answered per document; further announcements are
dropped silently.

If the page is not bridge-eligible (legacy profile, rejected manifest,
undeclared page, direct open outside /learn — §5), the child hears
**nothing**. Child pages MUST treat handshake silence (recommended: ~2s
timeout) as "no persistence available" and stay fully usable read-only.

## 3. Port protocol

Requests carry a child-chosen `request_id` (string, 1–128 chars); responses
echo it. Bounds are measured on `JSON.stringify(message)` encoded as UTF-8,
not JavaScript character count. Any message over **512 KiB serialized UTF-8
bytes**, any non-JSON-serializable payload, and any message without a string
`op` is answered with an error and counted; after 8 protocol errors the
parent closes the port and the document stays unbridged until reloaded.

The 512 KiB membrane bound is derived from the largest editor value: 64 KiB
of raw UTF-8 content can expand to 6 bytes per input byte when every byte is
represented as a JSON `\uXXXX` escape (384 KiB), then receives a bounded
operation envelope. The larger membrane bound does not relax semantic
limits: answers remain 32 KiB raw, artifact content 64 KiB raw, and (when
added) output chunks 32 KiB raw. Hostile escaping and multibyte strings are
therefore checked in bytes at both the membrane and their operation-specific
limit.

v1 operations:

```text
child → parent   { "op": "ping", "request_id": "r1" }
parent → child   { "op": "pong", "request_id": "r1", "abi": 1 }
```

Anything else: `{ "op": "error", "code": "unknown-op", "request_id": … }`.
Protocol error codes are `malformed`, `oversized`, `unknown-op`; they count
toward the 8-error port budget. Operation refusals (§3.1–§3.3) use the same
`error` envelope but are ordinary answers — they reuse endpoint codes and
never count. Unknown fields in any message are ignored, never an error — that
is the forward-compatibility room minor additions use.

### 3.1 The `attempts` capability (D5)

Granted in the welcome only when the child's `want` included `"attempts"`
and the parent can reach the attempt endpoint
([lesson-attempts-api.md](lesson-attempts-api.md)). The grant is routing,
not authority: every operation is re-validated at use, twice.

```text
child → parent   { "op": "attempt", "v": 1, "request_id": "a1",
                   "question_id": "q_…", "answer": "…" }
parent → child   { "op": "attempt", "request_id": "a1",
                   "result": "recorded",       // or "duplicate"
                   "attempt_id": "…uuid…", "stale": false,
                   "attempt_number": 3,        // recorded only
                   "projection": "projected" } // recorded only
```

- `v` is the operation-envelope version (independent of the handshake ABI;
  a changed submission shape bumps it additively). `v ≠ 1` is answered
  `unsupported-version`.
- `answer` remains limited to 32 KiB of raw UTF-8. The parent answers
  `answer-too-large` before HTTP, independently of the wider 512 KiB
  all-operation membrane needed by escaped editor content.
- The child supplies **only** `question_id` (the declared manifest id) and
  `answer`. The parent derives the rest: `page_id`/`page_rev` from its own
  armed identity, and the endpoint's `idempotency_key` from the child's
  `request_id` verbatim — so the D3 brief's request-id conventions (fresh
  per logical submission, reused only to retry that exact submission) give
  replay across reloads for free: a response lost to navigation is
  recovered by retrying the same `request_id`, which replays the durable
  original (`result: "duplicate"`) instead of double-recording.
- Per-operation re-validation, parent side: before the HTTP call the parent
  re-fetches the preview metadata and requires a settled, unquarantined
  frame whose version token and `bridge_page` identity still equal the
  armed binding (else `stale-page`), and requires `question_id` to be
  declared for the armed page in that fresh metadata (else
  `unknown-question`, without spending a server write). The server then
  re-validates the record-time manifest and derives `stale` again —
  the L1 `WindowProxy` residuals mean port possession is never authority.
- The displayed document is byte-bound too: the parent navigates the frame
  with `?v=<version token>` and the file route refuses snapshot bytes that
  no longer hash to that token (409 + self-reload), so the learner is never
  shown a revision the armed `page_rev` does not describe.
- Refusal codes: the endpoint's codes verbatim (`unknown-question`,
  `answer-too-large`, `rate-limited`, `idempotency-conflict`, …), plus the
  parent-local `capability-not-granted`, `unsupported-version`,
  `invalid-question-id`, `invalid-answer`, `stale-page`, `busy` (in-flight
  cap), and `unavailable` (metadata/endpoint unreachable, or a backend
  without D5 support).
- Concurrency: one outcome per in-flight `request_id` (a duplicate op while
  the original is pending is dropped — the pending call answers), at most 4
  in flight per document; beyond that, `busy`.
- Known residual (§4's L1 windows, restated for writes): the browser has no
  document-generation token, so a successor document that runs before its
  own `load` event — reachable only by the granted lesson document
  navigating itself — can hold the port inside that window. The parent's
  settle delay (~250 ms between validation and the HTTP call) closes every
  case where the successor's load completes in time: the load tears down
  port and generation first and the write is refused. What remains is a
  successor that deliberately stalls its own load — content chosen by the
  very document that owned the grant, which could have submitted the same
  attempt without navigating at all; it still records only questions the
  manifest declares, against the on-disk revision the server re-hashes at
  record time. Port possession buys nothing the armed page did not already
  have.
- Confirmation UX is parent-owned: a recorded attempt raises the app's
  toast ("attempt #N recorded"); there is no modal, and the child receives
  only the structured reply above.

### 3.2 The `editor` capability (phase F)

Granted only when all three routing conditions hold: the child's `want`
included `"editor"`, the armed page has at least one declared `blocks[]`
entry in fresh preview metadata, and the parent template supplied its
`data-artifacts-url`. The attribute is deliberately optional: new statics
served against an old live backend never grant the capability, so pages keep
their read-only presentation.

```text
child → parent   { "op": "artifact.get", "v": 1, "request_id": "g1",
                   "block_id": "blk_demo1" }
parent → child   { "op": "artifact.get", "request_id": "g1",
                   "exists": true, "content": "print('orbit')\n",
                   "file_rev": "sha256:…", "size": 15 }

child → parent   { "op": "artifact.save", "v": 1, "request_id": "s1",
                   "block_id": "blk_demo1", "content": "print('star')\n",
                   "base_rev": "sha256:…" }
parent → child   { "op": "artifact.save", "request_id": "s1",
                   "result": "saved", "file_rev": "sha256:…" }
```

- `v` is the editor operation-envelope version; values other than `1` are
  `unsupported-version`. `block_id` must match `blk_[a-z0-9]{4,32}`.
- A missing file returns `exists: false`, empty `content`, `size: 0`, and no
  `file_rev`; its first save uses the literal `base_rev: "absent"`.
- Content is limited to 64 KiB raw UTF-8 bytes. `base_rev` is either
  `"absent"` or the exact revision returned by `artifact.get` or the previous
  successful save. A `file-conflict` answer carries the current `file_rev`
  (or `null` when absent) and never overwrites the file.
- Before either HTTP request, the parent fetches fresh metadata, requires the
  current generation/port and armed version/identity still to match, and
  requires `block_id` in that fresh page's `blocks[]`. Before the mutating
  save it also waits the navigation-settle interval and re-checks the
  generation and port. The artifact endpoint then independently repeats the
  manifest, block, revision, path, and file-node checks.
- Endpoint refusals are relayed by code (`file-conflict`, `unsafe-file`,
  `rate-limited`, and the rest of the artifact refusal matrix). Parent-local
  refusals are `capability-not-granted`, `unsupported-version`,
  `invalid-block-id`, `invalid-content`, `invalid-base-rev`, `file-too-large`,
  `unknown-block`, `stale-page`, `busy`, and `unavailable`.
- A duplicate `request_id` while its editor request is pending is dropped;
  the pending operation supplies the one response. At most four editor
  operations are in flight per document.
- Child convention: begin with a useful read-only document. Keep the
  textarea read-only and controls disabled until `editor` is actually listed
  in the welcome; silence, an absent grant, an old backend, and direct-open
  mode all preserve the readable content. Render statuses and any returned
  data through `textContent` or text nodes, never `innerHTML`. The invented
  executable example is
  `fixtures/lesson-bridge/editor-run-conventions.html`.

### 3.3 The `run` capability (phase F)

Granted only when the child asked for `"run"`, both artifact-save and
run-start template endpoints are present, and fresh handshake metadata has at
least one block with `run: true`. That metadata flag already folds in the
registry, suffix, and process-local runner health. It routes the child to the
membrane; it is not authority for a selected block.

The only child-facing start is the composite save-and-run operation:

```text
child → parent   { "op": "artifact.save_run", "v": 1,
                   "request_id": "run-1", "block_id": "blk_demo1",
                   "content": "print('star')\n", "base_rev": "sha256:…",
                   "after": 0 }
parent → child   { "op": "artifact.save_run", "request_id": "run-1",
                   "result": "started", "run_id": "…uuid…",
                   "file_rev": "sha256:…" }

parent → child   { "op": "run.output", "run_id": "…", "seq": 1,
                   "stream": "stdout", "text": "invented output\n" }
parent → child   { "op": "run.exit", "run_id": "…", "seq": 2,
                   "cause": "exit", "exit_code": 0,
                   "truncated": false, "duration_ms": 42 }

child → parent   { "op": "run.cancel", "v": 1,
                   "request_id": "cancel-1", "run_id": "…uuid…" }
parent → child   { "op": "run.cancel", "request_id": "cancel-1",
                   "result": "ack", "run_id": "…uuid…" }
```

- `artifact.save_run` first applies the exact editor-save contract. Only a
  `saved` or `unchanged` response advances; `file-conflict` and every other
  refusal stop without a run start. The parent then starts the selected block
  with the returned `file_rev` and uses the child `request_id` verbatim as the
  HTTP `idempotency_key`. There is no bare child-facing run-start operation.
  For this composite op the id must also be well-formed Unicode without ASCII
  control characters or DEL; the parent rejects an invalid idempotency key
  before it sends the artifact save.
- The selected block itself must be present and carry `run: true` in fresh
  metadata before save and again before start. A page-wide `run` grant caused
  by another block does not confer authority; a selected non-run block is
  `run-not-enabled`. Both mutations remain behind the generation/port,
  identity/version, settle, and server-side record-time checks.
- `after` is the last output sequence the child applied: a non-negative safe
  integer, default `0`. The SSE request uses that cursor. The parent validates
  the SSE id against its JSON `seq`, ignores replay overlap, permits only
  `stdout`/`stderr` text chunks up to 32 KiB raw UTF-8, and relays output as
  data only. Exit causes are the server's closed set; malformed, oversized,
  or prematurely closed streams produce `run.error {run_id, code:
  "unavailable"}` rather than invented output or an invented terminal cause.
- The parent owns `run_id → {document generation, block_id}`. Cancel, output,
  and exit resolve through that map; malformed or foreign run ids are
  `job-missing` without touching the global HTTP routes. Cancel repeats fresh
  page-identity validation and the settle gate, but does not require the job's
  former block to remain declared: removing, moving, or revoking the block
  must not prevent stopping a job already owned by this document. This is a
  narrow D-FE-4 exception because cancellation only reduces an authority that
  the owned map proves was previously admitted. Terminal exits are removed
  immediately; at most 16 failed/reconnectable relay entries are retained for
  one document.
- One `artifact.save_run`/stream relay may be active for the document. A
  second start receives `busy` before save or start HTTP. Run request ids are
  deduplicated while in flight and at most four run operations are pending.
- Navigation aborts the client relay and drops the document-owned map; it does
  **not** call cancel. The bounded server job survives. A new document retries
  the same logical `request_id`, identical content/base state, and its last
  applied `after` cursor: the save is `unchanged`, the start idempotently
  returns the same `run_id`, and the relay resumes strictly after the cursor.

## 4. Lifecycle and teardown

- **Navigation ends all document-owned bridge state.** The port, armed
  identity, operation in-flight state, run ownership/relay, and the one-grant
  flag die with the document (a `MessagePort` cannot outlive it). A server-side
  run is not document-owned and is never cancelled by teardown (§3.3).
  After every reload the child must announce `ready` again and gets fresh
  identity — `page_rev` may have changed.
- **Stale revision:** the parent polls the preview metadata (~1.2 s); a
  version-token change closes the port and reloads the frame with the
  server's current sandbox tokens applied first. Identity drift without a
  token change (a manifest-only edit moving `bridge_page` — corrected page
  id, revoked eligibility — while the file bytes and profile stay put)
  triggers the same reload. A grant can therefore outlive its identity by
  at most one poll interval; D4's server-side `page_rev` check is the
  authoritative stale-attempt handler (§6.4).
- **Self-navigation:** a load event the parent did not initiate is never
  bound or granted; the parent re-asserts the expected page (bounded — a
  document that fights the re-assert simply stays unbridged). This is the
  observer for the §5 "same-frame navigation" residual.

  Known residual within that residual: a document that navigates the frame
  BEFORE its own load event completes is indistinguishable from the
  parent's navigation completing — the frame is opaque-origin, load events
  carry no URL, and no other browser signal exists (same class as the spec
  §5 `navigate-to` gap). The successor document can then be bound and
  announce under the expected page's identity. This grants nothing the
  redirecting page did not already have: it is itself lesson content that
  could have completed the handshake and rendered arbitrary content under
  that identity, and `page_rev` still describes the manifest page's bytes
  on disk — which is what the attempt backend records against (§6.3).
  Post-load self-navigation IS detected and re-asserted.

  The same blind window recurs AFTER the expected page settles and is
  armed: a self-navigation's successor runs scripts before its own `load`
  event fires, and until that load the parent's armed identity is still
  standing while `event.source` still matches the navigation-stable
  `WindowProxy`. A live `ready` posted in that window is therefore answered
  with the expected page's identity and a fresh port. The port dies at the
  successor's `load` (teardown + re-assert); a successor that stalls its
  own load keeps the port — and the visible frame — until the parent next
  navigates (version/identity change). The trust argument above applies
  unchanged: the successor was chosen by the armed lesson document itself,
  which could equally have kept the identical grant and rendered the same
  content. Like the two residuals flanking this one, it is why the
  state-changing operations (§3.1–§3.3) re-validate identity per operation —
  parent-side against fresh metadata and server-side against the
  record-time manifest — instead of trusting port possession.

  Related delivery residual: the iframe's `WindowProxy` survives
  navigation, so a `welcome` (or port message) already in flight when the
  frame self-navigates can be delivered to the successor document — the
  browser gives the sender no way to scope delivery to one document. The
  successor is the same trust domain (lesson content), and the write
  capabilities (§3.1–§3.3) never trust delivery: every operation is
  re-validated per use, parent- and server-side.
- **Profile flip:** the effective profile is folded into the version token,
  so a manifest-only flip reloads the document under the new CSP and sandbox
  tokens; the metadata never advertises a policy the displayed document was
  not served under.

## 5. Versioning

- `abi` is a single integer lockstep version. Additive, ignorable fields may
  ship within v1 (receivers ignore unknown fields); anything a v1 child
  could misinterpret bumps to 2.
- The parent speaks exactly one version per app build; the child announces
  the list it supports. There is no post-handshake renegotiation.
