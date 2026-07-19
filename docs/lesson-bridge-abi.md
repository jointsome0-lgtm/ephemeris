# Lesson bridge ABI (v1)

Status: frozen with D2. This is the contract between the Learn page's parent
runtime (`app/static/src/learn-bridge.ts`, emitted `learn-bridge.js`) and a
lesson page running inside the sandboxed preview iframe. The bundle contract
that decides *whether* a page may be bridged lives in
[learn-bundle-spec.md](learn-bundle-spec.md) (§5 profiles, §6.3 identity);
this document owns the wire shapes. D4/D5 extend the operation set; they do
not change the handshake.

ABI v1 deliberately carries **no write capability**: capability negotiation
always resolves to the empty set and the only port operation is `ping`. The
value of v1 is the membrane itself — versioning, identity ownership, and the
teardown rules — landing before any state-changing operation exists.

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
    "want": ["attempts"] }       // optional capability wishes, ≤16 × ≤64 chars
```

Constraints: JSON text ≤ 4096 chars; `abi` entries are integers 1–999.
Malformed or oversized announcements are ignored (no reply — nothing to
negotiate with). The child SHOULD post to `window.parent` with targetOrigin
`new URL(location.href).origin` (its own document URL is the app origin even
though `window.origin` is `"null"` in the sandbox).

A child usually announces the moment its script runs — before the parent
has finished binding identity for the freshly loaded document (and, across
a parent-initiated reload, possibly before the parent processed the load).
Children MUST therefore re-announce `ready` periodically (every 250–500 ms
is fine) until they receive a `welcome` or `reject`, giving up after the
~2 s silence budget below. The parent additionally buffers the latest valid
announcement while its identity binding is in flight and answers it on arm,
so in the common case the first or second announcement completes the
handshake. Duplicate announcements after a `welcome` are ignored (one grant
per document).

On a valid announcement whose `abi` contains a version the parent speaks
(v1: the literal `1`), the parent replies **once per loaded document**:

```text
parent → child (postMessage with one transferred MessagePort)
  { "ephemeris": "lesson-bridge", "type": "welcome",
    "abi": 1,                    // the selected version
    "lesson": { "lesson_uid": "…", "page_id": "pg_…",
                "page_rev": "sha256:…" },
    "capabilities": [] }         // granted set; v1 is always empty
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
echo it. Any message over 64 KiB of JSON text (the spec §6.2 line bound),
any non-JSON-serializable payload, and any message without a string `op` is
answered with an error and counted; after 8 protocol errors the parent
closes the port and the document stays unbridged until reloaded.

v1 operations:

```text
child → parent   { "op": "ping", "request_id": "r1" }
parent → child   { "op": "pong", "request_id": "r1", "abi": 1 }
```

Anything else: `{ "op": "error", "code": "unknown-op", "request_id": … }`.
Error codes are `malformed`, `oversized`, `unknown-op` (D4 adds the attempt
result codes). Unknown fields in any message are ignored, never an error —
that is the forward-compatibility room minor additions use.

## 4. Lifecycle and teardown

- **Navigation ends everything.** The port, the armed identity, and the
  one-grant flag die with the document (a `MessagePort` cannot outlive it).
  After every reload the child must announce `ready` again and gets fresh
  identity — `page_rev` may have changed.
- **Stale revision:** the parent polls the preview metadata (~1.2 s); a
  version-token change closes the port and reloads the frame with the
  server's current sandbox tokens applied first. A grant can therefore
  outlive its file's bytes by at most one poll interval; D4's server-side
  `page_rev` check is the authoritative stale-attempt handler (§6.4).
- **Self-navigation:** a load event the parent did not initiate is never
  bound or granted; the parent re-asserts the expected page (bounded — a
  document that fights the re-assert simply stays unbridged). This is the
  observer for the §5 "same-frame navigation" residual.
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
