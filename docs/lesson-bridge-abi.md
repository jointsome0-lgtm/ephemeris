# Lesson bridge ABI (v2)

Status: v1 froze with D2 and was extended additively by D5 (§3.1, `attempts`),
phase F (§3.2, `editor`; §3.3, `run`) and #133 tier 2 (§2.1, the `record`
snapshot on the welcome — a field, not an operation). **v2 changes the
handshake transport and nothing else** (§2, §5): the child transfers its own
`MessagePort` with `ready`, and every handshake result is delivered on that
port instead of to the frame's `WindowProxy`. Port protocol, capabilities,
operations, and every payload shape are byte-identical to v1.
This is the contract between the Learn page's parent runtime
(`app/static/src/learn-bridge.ts`, emitted `learn-bridge.js`) and a lesson
page running inside the sandboxed preview iframe. The bundle contract that
decides *whether* a page may be bridged lives in
[learn-bundle-spec.md](learn-bundle-spec.md) (§5 profiles, §6.3 identity);
this document owns the wire shapes.

ABI v1 shipped with **no write capability** — the membrane itself
(versioning, identity ownership, teardown rules) landed before any
state-changing operation existed. D5 added the one write capability,
`attempts` (§3.1), and phase F adds the independent editor and run operations
(§3.2–§3.3): a child that never asks for them sees exactly the original
protocol.

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
- The parent **never posts into the frame's `WindowProxy`** (v2). That object
  survives navigation and is not addressable by origin (an opaque origin has
  no value to name), so a message addressed to it can land in a document that
  replaced the intended recipient. Every handshake result — `welcome` and
  `reject` alike — is instead posted on the `MessagePort` the announcing
  document transferred with its own `ready` (§2). A port dies with the
  document holding it and cannot be carried across a same-frame navigation:
  a sandboxed opaque-origin successor gets a fresh origin and has no channel
  to receive one. Delivery is therefore scoped to one document by the object
  graph, not by timing.
- Messages **from** the child are accepted only when
  `event.source === frame.contentWindow`, the parent is armed for the
  current document, and no grant has been consumed yet.

## 2. Handshake

The child initiates (the parent cannot know when the child's listener is
installed; a child that never asks is simply a display page):

```text
child → parent (window.parent.postMessage, transferring one MessagePort)
  const reply = new MessageChannel();
  reply.port1.onmessage = …            // where the result will arrive
  window.parent.postMessage(
    { "ephemeris": "lesson-bridge", "type": "ready",
      "abi": [2],                  // supported ABI versions, 1–8 integers
      "want": ["attempts", "editor", "run"] }, // optional wishes, ≤16 × ≤64 chars
    appOrigin, [reply.port2]);
```

Constraints: serialized JSON ≤ 4096 UTF-8 bytes; `abi` entries are integers
1–999; **exactly one transferred `MessagePort`**. Malformed, oversized, and
port-less announcements are ignored (no reply — nothing to negotiate with, and
under v2 nowhere to send it). A pre-v2 child transfers no port, so it hears
nothing at all and degrades through the documented silence budget below; there
is no `WindowProxy` fallback and no reject for it, because a reject would
itself have to travel the channel v2 exists to abolish. The child SHOULD post
to `window.parent` with targetOrigin `new URL(location.href).origin` (its own
document URL is the app origin even though `window.origin` is `"null"` in the
sandbox).

The child accepts a terminal handshake result only on a port **it** created and
transferred, and only when the message has the lesson-bridge marker plus the
expected envelope. A `welcome` must select an announced ABI and transfer
exactly one `MessagePort` — the bridge port, a second and separate channel;
a `reject` has its own `reason`/`supported` shape and no port. Nothing arrives
on `window.addEventListener("message", …)` any more: a lesson-bridge-shaped
message on the window is by definition not from the parent runtime.
The first valid result is final for the loaded document; later messages cannot
replace its port or upgrade its capabilities. A direct/embedded document whose
URL origin is `"null"` skips the handshake and remains read-only.

A child usually announces the moment its script runs — before the parent
has finished binding identity for the freshly loaded document (and, across
a parent-initiated reload, possibly before the parent processed the load).
Children MUST therefore re-announce `ready` periodically (every 250–500 ms
is fine) until they receive a `welcome` or `reject`, giving up after the
~2 s silence budget below. **Each retry needs a fresh `MessageChannel`**: a
transferred port is spent, and it cannot be transferred a second time. Keep
every unanswered `port1` listening until one of them answers — the parent
replies on whichever announcement it decided to answer — then close the rest.
The parent answers announcements only on live receipt while it is armed;
earlier ones are deliberately dropped and their ports closed, never buffered.
In the common case a retry within the first half-second completes the
handshake. Duplicate announcements after a `welcome` are ignored (one grant
per document).

On a valid announcement whose `abi` contains a version the parent speaks
(v2: the literal `2`), the parent replies **once per loaded document**, on the
port that announcement transferred:

```text
parent → child (reply.port2.postMessage, transferring the bridge MessagePort)
  { "ephemeris": "lesson-bridge", "type": "welcome",
    "abi": 2,                    // the selected version
    "lesson": { "lesson_uid": "…", "page_id": "pg_…",
                "page_rev": "sha256:…" },
    "capabilities": ["attempts", "editor", "run"],
                                      // granted = want ∩ available routes/metadata
    "record": { "questions": [ … ] } } // optional read-back snapshot, §2.1
```

The port transferred **with** the welcome is the bridge. Everything after the
welcome flows over it; the port the child announced on carries the one
handshake result and nothing else. The parent's global `message` listener
never answers the same document twice (a second `ready` is ignored, and its
port closed, until the next navigation).

### 2.1 `record` — the read-back snapshot (#133 tier 2)

Read-back is a **field on the welcome, not an operation**. There is no
`record.get`: the port stays write-only, and what the page may know about its
own record is decided once, by the parent, at grant time.

```text
"record": {
  "questions": [
    { "question_id": "q_…",        // a question declared on THIS page
      "asked": false,              // true = recorded as a question to the tutor
      "answer": "…",               // the recorded answer, panel excerpt
      "answer_truncated": false,   // true = `answer` is a cut of a longer body
      "answered_at": "…",          // ISO-8601 UTC, when it was recorded
      "stale": false,              // page/manifest had already changed AT record time
      "verdict": {                 // the standing review OF THAT answer, or null
        "level": "correct" | "partial" | "incorrect" | "unclear",
        "note": "…",
        "recorded_at": "…" } }
  ] }
```

Semantics:

- **Additive.** The field is present only when the parent has a snapshot to
  give AND `attempts` was granted AND the owner allowed it (below); otherwise
  it is omitted entirely and the welcome is byte-for-byte the pre-#133 shape. A child that ignores `record`
  is unaffected in every case — it is not a capability, nothing is negotiated
  for it, and no operation depends on it.
- **Gated by `attempts`.** A page that did not ask to record answers has none
  to restore, so nothing crosses the frame boundary for it. One negotiation
  governs both directions.
- **Scope: this page's declared questions**, the same list the metadata's
  `bridge_page.questions` carries (spec §6.3) — never the whole lesson,
  never a retired id.
- **Bound to the document it was taken for.** The parent may navigate the frame
  to another page without a new `/learn` render (an entry removed or renamed,
  or a fallback), and the snapshot belongs to the render that built the parent.
  It carries that render's `lesson_uid`/`page_id`; the parent compares them
  against the armed identity and omits `record` entirely on a mismatch, so a
  successor page is never handed the predecessor's answers. Those two fields do
  not cross the boundary — the child receives `questions` only.
- **Re-projected onto what the loaded page declares now.** A manifest-only edit
  retires or moves a question without changing the page's version token, so the
  reloaded document can arm under the same identity with a shorter question
  list. Every entry is therefore filtered against the declared ids in the
  metadata that document armed from, and `record` is omitted whole when that
  list is missing or malformed (the same fail-closed rule the attempt path
  uses). An entry never survives for an id the page no longer declares.
- **Entries only for what exists.** A declared question with no recorded
  attempt has no entry. An absent id means *nothing known*, never *not
  attempted* (spec §6.1: the record can lag).
- **`asked` is the recorded direction, and it wins.** It says the learner sent
  this to the tutor instead of answering it, as recorded — not as the control
  is kinded now. Re-kinding a durable id between `prediction` and `ask_tutor`
  therefore cannot make a page read a grading verdict as the tutor's reply, or
  the reverse. Present the entry by `asked`, never by the control's current
  kind (§6.1 record-time rule, same as `stale`).
- **`stale` is a record-time fact.** It says the page or manifest had ALREADY
  changed when the attempt was written. It is decided once, at record time, and
  never recomputed: `stale: false` does not promise the page has not changed
  since. Say "was written against an older version of this page", not "is
  current".
- **A snapshot, not a feed.** It is projected from the same read of the record
  that rendered the `/learn` page's Record panel, and it is frozen at module
  init. A verdict recorded while the page is open reaches the document only on
  the next `/learn` load; the tier-1 unread badge is what signals it live.
  Binding it to the panel's own reading is deliberate: a fresher snapshot
  would show the lesson page a verdict the badge beneath it still counts
  unread.
- **Nothing new crosses the boundary.** Every field is already rendered in the
  Record panel of the same document — including `answer`, which is the
  panel's excerpt (`answer_truncated` marks the cut), not a re-read of the
  full 32 KiB body. The snapshot is bounded by the panel it mirrors, which is
  why it carries no separate size limit.
- **Gated by an owner decision, per lesson.** What crosses is the
  learner's own answers and the tutor's notes for this page's questions — the
  same rows the Record panel renders under the frame — into a document whose
  profile leaves same-frame navigation open (spec §5 residual): permitted
  script can assign `location.href` and carry those bytes to a destination the
  response CSP does not cover. So the parent asks first, through the same
  sticky consent the artifact READ path uses (`allowPrivateRead`), with a
  warning that names that egress. A refusal omits `record` entirely — not an
  empty list — and leaves the rest of the welcome and the whole write
  direction untouched. Both answers are remembered for the **lesson**, keyed on
  `lesson_uid` (owner, 2026-08-08 — per-document meant one modal per page of a
  multi-page bundle, which taught the click rather than the question). The
  runtime holds one in-memory working copy and swaps it when the armed lesson
  changes; each lesson's own decision is written to `sessionStorage` under its
  uid and rehydrated whenever that lesson is armed again, so `A → B → A` in one
  tab reuses A's earlier answer rather than re-asking. **The revocation
  boundary is the browsing session, not the lesson switch** — closing the tab,
  exactly what the prompt tells the owner. Storage is what makes the stated
  lifetime true at all: the lesson page tabs are ordinary `/learn` links whose
  navigation reloads the parent, so module memory alone would keep the per-page
  interrogation under a new name. `localStorage` is deliberately not
  used: a consent outliving the browsing session would answer for bundle
  content the owner has never seen, since the study agent rewrites a lesson's
  pages under the same uid. Unreadable or unrecognised stored state reads as
  undecided, which costs a prompt and never invents a grant. The
  decision therefore reaches the pages of that one bundle and no further: only
  an armed document can ask, arming requires the fresh metadata identity, and
  the re-assert/quarantine path (§4) forces an off-manifest successor back
  before it can arm. Nothing to hand over is
  not a decision: when the filtered list is empty the field is attached
  unasked, so opening a lesson nobody has answered yet never opens a modal.
  The prompt blocks, and a document can commit a navigation while it stands
  open without `contentWindow` ever changing, so the parent still waits out
  the navigation-settle interval and re-checks the document generation before
  the welcome goes out. Under v2 that check is defence in depth rather than
  the boundary: it decides whether to build a welcome at all, while *who can
  receive one* is settled by the transferred port.
- **Delivery is scoped to the announcing document (closed in v2).** The answer
  authorises the document it was asked about, and only that document can
  collect it: the welcome — the bridge port, and with it whatever `record` the
  owner approved — is posted on the `MessagePort` the announcer transferred
  with its own `ready` (§1, §2). A successor produced by a same-frame
  navigation, including one that delays its own `load` event behind a slow
  blocking subresource, never holds that port and cannot be handed one; it
  gets its own handshake, on its own load. What it does *not* repeat is the
  owner's question: delivery is per document, the §2.1 decision is per lesson,
  and the two are different mechanisms — the successor must still arm and
  announce for itself before an inherited `record` can be built for it, and an
  unarmed or quarantined successor is answered with silence. This retires the
  read-back half of the §4 `WindowProxy` delivery residual (D5 L1) at its
  root — the earlier gate could only narrow it from automatic on every load to
  requiring an explicit yes. The alternative, a non-navigable isolation
  profile, was considered and not taken: it would change what lesson pages may
  do, where the port change costs one `MessageChannel` in the child.
  The remaining cost is honest and known: a learner who declines sees the
  blank controls this feature exists to end, which is why the ask is once per
  lesson rather than once per read.
- **Children:** insert every value as text; `answer` is learner-authored and
  `note` is agent-authored. Never resubmit a truncated `answer` as a new
  attempt — it would replace the full body with a fragment.

If no supported version overlaps:

```text
parent → child (on the transferred port, no port of its own)
  { "ephemeris": "lesson-bridge", "type": "reject",
    "reason": "abi-unsupported", "supported": [2] }
```

At most 3 rejects are answered per document; further announcements are
dropped silently. A reject answers only an announcement that transferred a
port — a v1 child announcing `abi: [1]` over the old transport is not rejected,
it is unheard (§2).

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

Each new logical action uses a fresh opaque `request_id`, unique across the
lesson even across reloads and tabs. The child reuses an id only to retry the
exact same logical action: changed content, a changed block, or an intentional
new Run gets a new id. This is response correlation for all operations and the
idempotency boundary for attempts and composite runs.

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
parent → child   { "op": "pong", "request_id": "r1", "abi": 2 }
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
                   "kind": "attempt",          // or "question" (#136)
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
- Residual, narrowed by v2 (§4's L1 windows, restated for writes): the bridge
  port now reaches only the document that announced with its own channel, and
  under the deployed `allow-scripts`-only profile that document has no way to
  pass it on — every navigation gets a fresh opaque origin, so
  `BroadcastChannel`, shared workers, and same-origin window handles are all
  closed to it. A successor document therefore cannot hold the port at all.
  What the announcer can still do is keep the port itself, stall behind an
  unfinished navigation, and write; the parent's settle delay (~250 ms between
  validation and the HTTP call) refuses that as soon as the successor's load
  completes and tears down port and generation. Beyond it, the writer is the
  very document that owned the grant and could have submitted the same attempt
  without navigating at all; it still records only questions the manifest
  declares, against the on-disk revision the server re-hashes at record time.
  Port possession buys nothing the armed page did not already have — which is
  why every operation re-validates rather than trusting it.
- `kind` is the server-derived record direction (`attempt`, or `question`
  when the manifest declares that question `ask_tutor` — lesson-attempts-api.md).
  The child never sends it; a backend that predates it sends none, and the
  field is then absent from the reply rather than guessed.
- Confirmation UX is parent-owned: a recorded attempt raises the app's
  toast ("attempt #N recorded", or "question sent to the tutor" for the
  ask direction); there is no modal, and the child receives only the
  structured reply above.

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
- Artifact bytes are private runtime state, while lesson pages are untrusted
  and retain the documented same-frame navigation residual (§4). The first
  `artifact.get` in each lesson therefore opens a parent-owned
  confirmation that explicitly warns the learner that the page can navigate
  and send code it reads to another site. A denial is sticky and returns
  `artifact-read-denied` without HTTP; acceptance covers later reads. Both
  answers last for the lesson, not the document — see §2.1 for the lifetime
  and what bounds it. The parent repeats fresh page/block validation after the
  prompt and before the GET.
  Artifact bytes and the §2.1 read-back are decided separately but by one
  mechanism (`allowPrivateRead`), so neither answer speaks for the other kind.
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
  `artifact-read-denied`, `unknown-block`, `stale-page`, `busy`, and
  `unavailable`.
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
  with the returned `file_rev`. There is no bare child-facing run-start operation.
  Before saving, the parent derives the HTTP `idempotency_key` as
  SHA-256 over a domain-tagged serialized tuple of protocol version, child
  `request_id`, `block_id`, and content. The same logical operation therefore
  replays across navigation, while changed bytes or a changed block cannot
  save successfully and then collide with a retained key from an older run.
  This is a narrow D-FE-3 deviation from passing `request_id` verbatim: the
  original shape cannot preflight a server-retained key before the composite's
  first mutation, and the run-start API intentionally has no separate
  idempotency-preflight route. The bounded parent module computes SHA-256
  dependency-free, so the supported plain-HTTP trusted-LAN mode does not rely
  on secure-context-only Web Crypto.
  For this composite op the id must also be well-formed Unicode without ASCII
  control characters or DEL; the parent rejects an invalid idempotency key
  before it sends the artifact save.
- The selected block itself must be present and carry `run: true` in fresh
  metadata before save, again before start, and after the start response before
  the parent remembers or relays the returned job. A page-wide `run` grant
  caused by another block does not confer authority; a selected non-run block
  is `run-not-enabled`. Both mutations remain behind the generation/port,
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
  `WindowProxy`. A live `ready` posted in that window is therefore still
  answered with the expected page's identity — but only into the port that
  same announcement transferred, so the successor is answered *as itself*
  and can never collect an answer meant for its predecessor. The port dies
  at the successor's `load` (teardown + re-assert); a successor that stalls
  its own load keeps its port — and the visible frame — until the parent next
  navigates (version/identity change). The trust argument above applies
  unchanged: the successor was chosen by the armed lesson document itself,
  which could equally have kept the identical grant and rendered the same
  content. Like the residual above, it is why the state-changing operations
  (§3.1–§3.3) re-validate identity per operation — parent-side against fresh
  metadata and server-side against the record-time manifest — instead of
  trusting port possession.

  The delivery half of this residual is **closed in v2**. A `welcome` was
  previously addressed to the navigation-stable `WindowProxy`, so one already
  in flight when the frame self-navigated could be delivered to the successor
  document — carrying the bridge port and any approved §2.1 `record` to a
  document the owner was never shown. The parent now posts every handshake
  result on the channel the announcing document transferred (§1, §2), and the
  browser gives a navigated-away document no way to hand that port on, so
  delivery is scoped to one document by construction. Messages *on* an
  established bridge port are unaffected: the port belongs to the document
  that received it and dies with it.
- **Profile flip:** the effective profile is folded into the version token,
  so a manifest-only flip reloads the document under the new CSP and sandbox
  tokens; the metadata never advertises a policy the displayed document was
  not served under.

## 5. Versioning

- `abi` is a single integer lockstep version. Additive, ignorable fields may
  ship within a version (receivers ignore unknown fields); anything a child of
  the current version could misinterpret bumps it.
- The parent speaks exactly one version per app build; the child announces
  the list it supports. There is no post-handshake renegotiation.
- **v1 → v2** bumped for the handshake transport (§2): the child transfers a
  `MessagePort` with `ready` and the result comes back on it. Nothing else
  moved — a v1 child's message bodies are all still valid v2 bodies. The bump
  is honest rather than strictly required by the "could misinterpret" rule: a
  v1 child cannot misread a v2 welcome, it simply never receives one, and
  announcing `abi: [2]` is how a page states it speaks the transport that
  carries the answer. Because the transport itself changed, negotiation cannot
  rescue a v1 announcement: with no port there is no reply channel, so it gets
  silence and the ~2 s budget, not `abi-unsupported`. A page that announces
  `abi: [1, 2]` over the new transport is welcomed at v2; one that announces
  `abi: [1]` over it is rejected with `supported: [2]`.
