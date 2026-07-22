/* GENERATED-SOURCE NOTICE: app/static/learn-bridge.js is emitted from this
 * file by `npm run build` (tsc, issue #42) and committed so deploy stays
 * zero-build. Edit THIS file and re-emit; never edit the .js by hand. */

/* Learn preview runtime + lesson bridge parent (D2, ABI v1 — see
 * docs/lesson-bridge-abi.md).
 *
 * Owns the whole preview-frame lifecycle on /learn, because the bridge grant
 * must be BOUND to the loaded revision (spec §5/D1): the same code that
 * decides what document the iframe shows is the code that decides which
 * identity a handshake may carry. Replaces the meta-poll block that lived in
 * app.js.
 *
 * Trust model: the iframe document is untrusted lesson content in an
 * opaque-origin sandbox. The parent owns lesson_uid/page_id/page_rev and the
 * capability set; the child supplies none of them (it only announces itself
 * and asks). Messages TO the child go with targetOrigin "*" — an opaque
 * origin is not addressable — which is safe here because we post only to the
 * specific contentWindow we navigated, the payload holds page identity (no
 * secrets), and the capability channel is the transferred MessagePort, held
 * by whoever received it, not by whoever can read a broadcast. Messages FROM
 * the child are accepted only when `event.source === frame.contentWindow`.
 *
 * D5 adds the `attempts` capability and phase F adds the editor/run membranes.
 * All are child-requested routing facts, never authority: every operation
 * re-fetches preview metadata, re-validates the armed identity and the
 * operation's question/block membership, and lets the server independently
 * enforce the record-time manifest. */
export {};

const ABI_VERSION = 1;
/** Hard cap on a child "ready" announcement (serialized UTF-8 bytes). */
const MAX_READY_BYTES = 4096;
/** Hard cap on any port message in serialized UTF-8 bytes. 512 KiB is
 * derived from a 64 KiB artifact at worst-case JSON escaping (6×) plus a
 * bounded envelope; semantic per-operation bounds stay narrower. */
const MAX_PORT_BYTES = 512 * 1024;
/** Port protocol errors tolerated per document before the port is closed. */
const MAX_PROTOCOL_ERRORS = 8;
/** Handshake rejections answered per document (then silence — no help for
 * a probing loop). */
const MAX_REJECTS = 3;
/** Off-manifest self-navigations forced back per document generation chain;
 * a page that fights the re-assert just stays unbridged. */
const MAX_REASSERTS = 3;
const POLL_MS = 1200;
/** Attempt operations in flight at once per document; beyond it the op is
 * answered `busy` (a Check press is human-scale — this only stops a loop). */
const MAX_ATTEMPTS_INFLIGHT = 4;
/** Editor operations in flight at once per document. */
const MAX_EDITOR_INFLIGHT = 4;
/** Run operations in flight at once per document. */
const MAX_RUN_INFLIGHT = 4;
/** Failed/reconnectable relays retained per document; terminal exits delete eagerly. */
const MAX_OWNED_RUNS = 16;
/** Mirrors bundle_schema.MAX_BLOCKS: every backend-valid page stays routable. */
const MAX_BRIDGE_BLOCKS = 100;
/** Settle delay before the attempt HTTP call (PR-60 round 1, D2 L1): a
 * self-navigation whose successor completes its load within this window
 * tears the port and generation down BEFORE the write is sent, so the
 * navigation-gap residual shrinks to a successor that deliberately stalls
 * its own load — same-trust content chosen by the granted document itself
 * (ABI §3.1). Human-scale Check presses don't notice a quarter second. */
const ATTEMPT_SETTLE_MS = 250;
/** The same navigation-settle membrane applies before artifact saves. */
const EDITOR_SETTLE_MS = 250;
/** Navigation-settle membrane before composite save and cancel mutations. */
const RUN_SETTLE_MS = 250;
/** The op-envelope version the attempt operation speaks (independent of the
 * handshake ABI so the submission shape can evolve additively). */
const ATTEMPT_OP_VERSION = 1;
const EDITOR_OP_VERSION = 1;
const RUN_OP_VERSION = 1;
const MAX_ANSWER_BYTES = 32 * 1024;
const MAX_CONTENT_BYTES = 64 * 1024;
const MAX_OUTPUT_BYTES = 32 * 1024;
const QUESTION_ID_RE = /^q_[a-z0-9]{4,32}$/;
const BLOCK_ID_RE = /^blk_[a-z0-9]{4,32}$/;
const FILE_REV_RE = /^sha256:[a-f0-9]{64}$/;
const BASE_REV_RE = /^(?:absent|sha256:[a-f0-9]{64})$/;
const JOB_ID_RE = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const RUN_CAUSES = new Set([
  "exit", "signal", "timeout", "cancelled", "output-limit", "spawn-failed", "shutdown",
]);
const UTF8 = new TextEncoder();

const SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
  0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
  0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
  0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
  0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
  0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
  0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
  0xc67178f2,
]);

const rotateRight = (value: number, shift: number): number =>
  (value >>> shift) | (value << (32 - shift));

/** Small dependency-free SHA-256 for the bounded bridge idempotency tuple.
 * Exported only so verify.py can execute standard vectors against the emitted
 * module; ESM exports do not create a page or iframe global. */
export const sha256Hex = (bytes: Uint8Array): string => {
  const paddedLength = Math.ceil((bytes.length + 9) / 64) * 64;
  const message = new Uint8Array(paddedLength);
  message.set(bytes);
  message[bytes.length] = 0x80;
  const view = new DataView(message.buffer);
  const bitLength = bytes.length * 8;
  view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x100000000), false);
  view.setUint32(paddedLength - 4, bitLength >>> 0, false);

  let h0 = 0x6a09e667;
  let h1 = 0xbb67ae85;
  let h2 = 0x3c6ef372;
  let h3 = 0xa54ff53a;
  let h4 = 0x510e527f;
  let h5 = 0x9b05688c;
  let h6 = 0x1f83d9ab;
  let h7 = 0x5be0cd19;
  const words = new Uint32Array(64);

  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let i = 0; i < 16; i += 1) {
      words[i] = view.getUint32(offset + i * 4, false);
    }
    for (let i = 16; i < 64; i += 1) {
      const w15 = words[i - 15]!;
      const w2 = words[i - 2]!;
      const s0 = rotateRight(w15, 7) ^ rotateRight(w15, 18) ^ (w15 >>> 3);
      const s1 = rotateRight(w2, 17) ^ rotateRight(w2, 19) ^ (w2 >>> 10);
      words[i] = (words[i - 16]! + s0 + words[i - 7]! + s1) >>> 0;
    }

    let a = h0;
    let b = h1;
    let c = h2;
    let d = h3;
    let e = h4;
    let f = h5;
    let g = h6;
    let h = h7;
    for (let i = 0; i < 64; i += 1) {
      const sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
      const choice = (e & f) ^ (~e & g);
      const temp1 = (h + sum1 + choice + SHA256_K[i]! + words[i]!) >>> 0;
      const sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (sum0 + majority) >>> 0;
      h = g;
      g = f;
      f = e;
      e = (d + temp1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) >>> 0;
    }

    h0 = (h0 + a) >>> 0;
    h1 = (h1 + b) >>> 0;
    h2 = (h2 + c) >>> 0;
    h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0;
    h5 = (h5 + f) >>> 0;
    h6 = (h6 + g) >>> 0;
    h7 = (h7 + h) >>> 0;
  }

  return [h0, h1, h2, h3, h4, h5, h6, h7]
    .map((word) => word.toString(16).padStart(8, "0")).join("");
};

interface BridgePage {
  lesson_uid: string;
  page_id: string;
  page_rev: string;
}

interface BridgeBlock {
  id: string;
  run: boolean;
}

interface OwnedRun {
  generation: number;
  block_id: string;
}

interface ActiveRelay {
  generation: number;
  run_id: string;
  controller: AbortController;
}

interface PreviewMeta {
  version?: unknown;
  exists?: unknown;
  preview_url?: unknown;
  bridge?: unknown;
  bridge_page?: unknown;
  sandbox?: unknown;
}

const frame = document.getElementById("lesson-preview-frame") as HTMLIFrameElement | null;

if (frame && frame.dataset["metaUrl"] && frame.getAttribute("src")) {
  const metaUrl = frame.dataset["metaUrl"]!;
  const fallbackSrc = frame.getAttribute("src")!;
  /* Attempt endpoint (D4). Absent on a stale template render: the attempts
   * capability is then simply never granted (fail closed, no error). */
  const attemptsUrl = frame.dataset["attemptsUrl"] || null;
  /* Phase-F artifact endpoint prefix. An older live backend renders no data
   * attribute, so `editor` is never granted while the static is ahead. */
  const artifactsUrl = frame.dataset["artifactsUrl"] || null;
  /* Phase-F run-start endpoint prefix. It is a separate feature-detection
   * attribute because statics can temporarily run against the old backend. */
  const runsUrl = frame.dataset["runsUrl"] || null;

  /* The version token the displayed document was served under (server-
   * rendered for the initial navigation, then meta-derived); the binding
   * rule is: identity is armed only while the fresh meta token equals it. */
  let expectedVersion = frame.dataset["version"] || "";
  let expectedSrc = new URL(frame.src, window.location.href).toString();
  /* Bumped on every iframe `load`; every async continuation re-checks it so
   * a response that raced a navigation can never arm or grant. */
  let generation = 0;
  /* True while the pending navigation is one WE initiated (the server-
   * rendered src counts); a load without it is the document navigating
   * itself somewhere — never bridged, forced back while budget lasts.
   * This module is fetched separately and can initialise AFTER the initial
   * load already fired; the inline observer in learn.html (attached at
   * parse time, before any load task can have dispatched) counts loads in
   * data-loaded, so a settled document is never mistaken for our pending
   * navigation (PR-55 round 2). */
  const earlyLoads = Number(frame.dataset["loaded"]) || 0;
  let navPending = earlyLoads === 0;
  let reasserts = 0;
  /* Terminal for the current frame content (PR-55 round 5): set when a
   * document exhausts the re-assert budget by fighting the forced return
   * to the expected page. From then on nothing arms — the off-manifest
   * successor must never be granted the expected page's identity — until
   * a parent-owned navigation (version/identity change) starts fresh. */
  let quarantined = false;

  if (earlyLoads > 1) {
    /* More than one load happened before this module initialised: the
     * expected page loaded and the frame was navigated again (drain R1).
     * The currently settled document is NOT trustworthy as the expected
     * page — fail closed with a parent-owned re-assert (consuming one
     * budget slot) instead of ever arming it. */
    reasserts = 1;
    const url = new URL(expectedSrc);
    url.searchParams.set("_v", String(Date.now()));
    navPending = true;
    frame.src = url.toString();
  }

  /* Per-document handshake state (cleared on every load/teardown). */
  let armed: BridgePage | null = null;
  let granted = false;
  let port: MessagePort | null = null;
  let protocolErrors = 0;
  let rejects = 0;
  /* D5 per-document write state: the capability set the welcome granted and
   * the request_ids with an attempt HTTP call still pending. Navigation ends
   * both — a successor document never inherits a grant or an in-flight slot
   * (the durable outcome is still reachable: the child retries the same
   * request_id after reload and the server replays it). */
  let capabilities: string[] = [];
  let attemptsInflight = new Set<string>();
  let editorInflight = new Set<string>();
  /* null = no decision yet; a denial is sticky for this document so hostile
   * content cannot turn artifact.get into a browser-dialog loop. */
  let artifactReadConsent: boolean | null = null;
  let runInflight = new Set<string>();
  let runStartToken: object | null = null;
  let ownedRuns = new Map<string, OwnedRun>();
  let activeRelay: ActiveRelay | null = null;
  let armedBlocks: BridgeBlock[] = [];
  /* Serialises a handshake-time metadata refresh. An object token prevents a
   * stale document's finally block from clearing a successor's refresh. */
  let grantToken: object | null = null;

  const teardown = (): void => {
    /* Navigation stops only this document's relay. The server-side job is
     * deliberately not cancelled and remains reattachable by idempotent replay. */
    if (activeRelay) activeRelay.controller.abort();
    if (port) port.close();
    port = null;
    armed = null;
    granted = false;
    protocolErrors = 0;
    rejects = 0;
    capabilities = [];
    attemptsInflight = new Set();
    editorInflight = new Set();
    artifactReadConsent = null;
    runInflight = new Set();
    runStartToken = null;
    ownedRuns = new Map();
    activeRelay = null;
    armedBlocks = [];
    grantToken = null;
  };

  const fetchMeta = async (): Promise<PreviewMeta | null> => {
    try {
      const r = await fetch(metaUrl, { cache: "no-store" });
      const data: unknown = await r.json();
      if (typeof data !== "object" || data === null) return null;
      return data as PreviewMeta;
    } catch {
      return null; // best-effort; the next tick retries
    }
  };

  const SANDBOX_OK = /^[a-z][a-z -]{0,255}$/;

  const applySandbox = (meta: PreviewMeta): void => {
    /* The server owns the token policy (one owner next to the CSP map); the
     * client only re-applies it across profile flips. Absent/odd values
     * (e.g. a pre-D2 backend) leave the attribute as rendered. */
    const tokens = meta.sandbox;
    if (typeof tokens === "string" && SANDBOX_OK.test(tokens)
        && frame.getAttribute("sandbox") !== tokens) {
      frame.setAttribute("sandbox", tokens);
    }
  };

  const navigate = (meta: PreviewMeta): void => {
    teardown();
    expectedVersion = String(meta.version ?? "0");
    applySandbox(meta); // before src: sandbox is read at navigation time
    const src = (typeof meta.preview_url === "string" && meta.preview_url)
      || (meta.exists ? frame.dataset["src"] : fallbackSrc)
      || fallbackSrc;
    const url = new URL(src, window.location.href);
    /* Serve-time version binding (PR-60 round 1): the file route refuses a
     * snapshot whose token no longer equals this value, so the document the
     * learner sees is byte-bound to the token this runtime arms. */
    url.searchParams.set("v", expectedVersion);
    url.searchParams.set("_v", String(Date.now()));
    expectedSrc = url.toString();
    reasserts = 0;
    quarantined = false; // parent-owned navigation: fresh start
    navPending = true;
    frame.src = expectedSrc;
  };

  const identityMatches = (meta: PreviewMeta): boolean => {
    if (armed === null) return true; // nothing bound, nothing to drift
    if (meta.bridge !== true || !isBridgePage(meta.bridge_page)) return false;
    return meta.bridge_page.lesson_uid === armed.lesson_uid
      && meta.bridge_page.page_id === armed.page_id
      && meta.bridge_page.page_rev === armed.page_rev;
  };

  const isBridgePage = (value: unknown): value is BridgePage => {
    if (typeof value !== "object" || value === null) return false;
    const page = value as Record<string, unknown>;
    return (["lesson_uid", "page_id", "page_rev"] as const).every((key) => {
      const field = page[key];
      return typeof field === "string" && field.length > 0 && field.length <= 256;
    });
  };

  /* Block routing metadata for the armed page. `null` means a malformed or
   * pre-F backend response; the attempts membrane remains independently
   * usable, while editor capability fails closed. */
  const metaBlocks = (meta: PreviewMeta): BridgeBlock[] | null => {
    if (typeof meta.bridge_page !== "object" || meta.bridge_page === null) return null;
    const list = (meta.bridge_page as Record<string, unknown>)["blocks"];
    if (!Array.isArray(list) || list.length > MAX_BRIDGE_BLOCKS) return null;
    const blocks: BridgeBlock[] = [];
    for (const value of list) {
      if (typeof value !== "object" || value === null) return null;
      const block = value as Record<string, unknown>;
      if (typeof block["id"] !== "string" || !BLOCK_ID_RE.test(block["id"])) return null;
      if (typeof block["run"] !== "boolean") return null;
      blocks.push({ id: block["id"], run: block["run"] });
    }
    return blocks;
  };

  const armFromMeta = (meta: PreviewMeta): void => {
    /* Single choke point (PR-55 round 3): never arm while a navigation is
     * pending — the outgoing document can still announce into the gap and
     * would be granted the INCOMING page's identity — nor while the frame
     * is quarantined after exhausting the self-navigation re-assert budget
     * (round 5). Consequence: grants only ever go to settled documents the
     * parent itself navigated to. */
    if (quarantined || navPending || armed !== null || granted) return;
    if (meta.bridge === true && isBridgePage(meta.bridge_page)) {
      armed = {
        lesson_uid: meta.bridge_page.lesson_uid,
        page_id: meta.bridge_page.page_id,
        page_rev: meta.bridge_page.page_rev,
      };
      armedBlocks = metaBlocks(meta) ?? [];
      /* Deliberately NO buffered-announcement flush here (PR-55 round 4):
       * an announcement held across this async bind could be answered into
       * a successor document after a same-frame navigation. Announcements
       * are answered only on live receipt; children retry (ABI §2), so the
       * next announcement lands with armed set. */
    }
  };

  const bind = async (gen: number): Promise<void> => {
    const meta = await fetchMeta();
    if (gen !== generation || meta === null) return;
    if (String(meta.version ?? "0") !== expectedVersion) {
      navigate(meta); // stale before it ever bound; reload and re-enter
      return;
    }
    armFromMeta(meta);
  };

  frame.addEventListener("load", () => {
    generation += 1;
    /* armFromMeta refuses to arm while navPending, so a grant can never
     * exist when a pending navigation completes — no keep-the-grant branch
     * is needed here (one welcome per document holds because a settled
     * document's generation only changes with a real navigation). */
    teardown();
    if (!navPending) {
      /* Self-navigation: the lesson document went somewhere on its own. The
       * new document is NOT the page any identity was derived from — never
       * bind it; put the expected page back while the budget lasts, then
       * quarantine (a successor that fought the re-assert must stay
       * unbridged, not drift back into the poll's arming path). */
      if (reasserts < MAX_REASSERTS) {
        reasserts += 1;
        const url = new URL(expectedSrc);
        url.searchParams.set("_v", String(Date.now()));
        navPending = true;
        frame.src = url.toString();
      } else {
        quarantined = true;
      }
      return;
    }
    navPending = false;
    void bind(generation);
  });

  /* ---- handshake membrane (the only global listener; everything after the
   * welcome flows over the transferred MessagePort) ---- */

  const serializedByteLength = (value: unknown): number | null => {
    try {
      const text = JSON.stringify(value);
      return typeof text === "string" ? UTF8.encode(text).byteLength : null;
    } catch {
      return null; // cyclic or otherwise non-JSON structured-clone payload
    }
  };

  const isReady = (value: unknown): value is { abi: unknown[]; want?: unknown[] } => {
    if (typeof value !== "object" || value === null) return false;
    const msg = value as Record<string, unknown>;
    if (msg["ephemeris"] !== "lesson-bridge" || msg["type"] !== "ready") return false;
    if (!Array.isArray(msg["abi"]) || msg["abi"].length === 0 || msg["abi"].length > 8) return false;
    if (!msg["abi"].every((v) => Number.isInteger(v) && (v as number) >= 1 && (v as number) <= 999)) return false;
    if ("want" in msg) {
      const want = msg["want"];
      if (!Array.isArray(want) || want.length > 16) return false;
      if (!want.every((v) => typeof v === "string" && v.length <= 64)) return false;
    }
    return true;
  };

  const protocolError = (code: string, requestId: string | null): void => {
    protocolErrors += 1;
    if (port) {
      port.postMessage(
        requestId === null
          ? { op: "error", code }
          : { op: "error", code, request_id: requestId },
      );
      if (protocolErrors >= MAX_PROTOCOL_ERRORS) {
        /* Fail closed for THIS document: the port dies, the grant stays
         * consumed (no second port until a fresh navigation). */
        port.close();
        port = null;
      }
    }
  };

  const toast = (msg: string): void => {
    const ui = (window as unknown as { alUI?: { toast?: (m: string) => void } }).alUI;
    if (ui && typeof ui.toast === "function") ui.toast(msg);
  };

  /* Attempt refusals are ANSWERS, not protocol violations: they reuse the
   * endpoint's error codes verbatim (docs/lesson-attempts-api.md) and never
   * count toward the port-closing budget — a page retrying a retired
   * question must not lose its whole bridge. */
  const answerError = (
    to: MessagePort,
    code: string,
    requestId: string,
    fields: Record<string, unknown> = {},
  ): void => {
    to.postMessage({ op: "error", code, request_id: requestId, ...fields });
  };

  /* Declared question ids for the armed page, taken from FRESH metadata at
   * operation time (never the arm-time copy: a manifest-only edit can
   * declare or retire questions without moving the page's version token).
   * null = absent or malformed — e.g. a pre-D5 backend — and fails closed. */
  const metaQuestions = (meta: PreviewMeta): string[] | null => {
    if (typeof meta.bridge_page !== "object" || meta.bridge_page === null) return null;
    const list = (meta.bridge_page as Record<string, unknown>)["questions"];
    if (!Array.isArray(list) || list.length > 512) return null;
    return list.every((q) => typeof q === "string" && QUESTION_ID_RE.test(q))
      ? (list as string[])
      : null;
  };

  const contentByteLength = (content: string): number => UTF8.encode(content).byteLength;

  const deriveRunIdempotencyKey = (
    requestId: string,
    blockId: string,
    content: string,
  ): string => sha256Hex(UTF8.encode(JSON.stringify([
    "ephemeris:lesson-run:v1", requestId, blockId, content,
  ])));

  const endpointError = (
    to: MessagePort,
    requestId: string,
    record: Record<string, unknown>,
  ): void => {
    const code = typeof record["error"] === "string" && record["error"].length <= 64
      ? record["error"] : "unavailable";
    if (code === "file-conflict") {
      const rev = record["file_rev"];
      if (rev === null || (typeof rev === "string" && FILE_REV_RE.test(rev))) {
        answerError(to, code, requestId, { file_rev: rev });
      } else {
        answerError(to, "unavailable", requestId);
      }
      return;
    }
    answerError(to, code, requestId);
  };

  const freshBlocks = async (
    boundPort: MessagePort,
    gen: number,
    requestId: string,
  ): Promise<BridgeBlock[] | null> => {
    const meta = await fetchMeta();
    if (gen !== generation || port !== boundPort || armed === null) return null;
    if (meta === null) {
      answerError(boundPort, "unavailable", requestId);
      return null;
    }
    if (
      navPending || quarantined
      || String(meta.version ?? "0") !== expectedVersion
      || meta.bridge !== true || !identityMatches(meta)
    ) {
      answerError(boundPort, "stale-page", requestId);
      return null;
    }
    const blocks = metaBlocks(meta);
    if (blocks === null) {
      answerError(boundPort, "unavailable", requestId);
      return null;
    }
    return blocks;
  };

  const freshBlock = async (
    boundPort: MessagePort,
    gen: number,
    requestId: string,
    blockId: string,
  ): Promise<BridgeBlock | null> => {
    const blocks = await freshBlocks(boundPort, gen, requestId);
    if (blocks === null) return null;
    const block = blocks.find((candidate) => candidate.id === blockId);
    if (!block) {
      answerError(boundPort, "unknown-block", requestId);
      return null;
    }
    return block;
  };

  const artifactEndpoint = (blockId: string): string =>
    `${artifactsUrl!}/${encodeURIComponent(blockId)}/file`;

  const readEndpointJson = async (
    url: string,
    init?: RequestInit,
  ): Promise<Record<string, unknown> | null> => {
    try {
      const response = await fetch(url, { cache: "no-store", ...init });
      const body: unknown = await response.json();
      return typeof body === "object" && body !== null
        ? (body as Record<string, unknown>) : null;
    } catch {
      return null;
    }
  };

  const allowArtifactRead = (): boolean => {
    if (artifactReadConsent !== null) return artifactReadConsent;
    try {
      artifactReadConsent = window.confirm(
        "Allow this untrusted lesson page to read saved learner code? "
        + "A lesson page can navigate the preview and send code it reads to another site. "
        + "Allow only if you trust this lesson.",
      );
    } catch {
      artifactReadConsent = false;
    }
    return artifactReadConsent;
  };

  const getArtifact = async (
    boundPort: MessagePort,
    gen: number,
    requestId: string,
    blockId: string,
  ): Promise<void> => {
    const inflight = editorInflight;
    inflight.add(requestId);
    try {
      if (await freshBlock(boundPort, gen, requestId, blockId) === null) return;
      if (!allowArtifactRead()) {
        return answerError(boundPort, "artifact-read-denied", requestId);
      }
      /* The parent prompt may stay open while an external manifest edit
       * lands. Re-bind the page/block immediately before the private read. */
      if (await freshBlock(boundPort, gen, requestId, blockId) === null) return;
      const rec = await readEndpointJson(artifactEndpoint(blockId));
      if (gen !== generation || port !== boundPort) return;
      if (rec === null) return answerError(boundPort, "unavailable", requestId);
      if (rec["ok"] !== true) return endpointError(boundPort, requestId, rec);
      const exists = rec["exists"];
      const content = rec["content"];
      const size = rec["size"];
      const rev = rec["file_rev"];
      if (
        typeof exists !== "boolean"
        || typeof content !== "string"
        || !Number.isInteger(size) || (size as number) < 0 || (size as number) > MAX_CONTENT_BYTES
        || contentByteLength(content) > MAX_CONTENT_BYTES
        || (exists && (typeof rev !== "string" || !FILE_REV_RE.test(rev)))
        || (!exists && rev !== undefined)
      ) {
        return answerError(boundPort, "unavailable", requestId);
      }
      /* A manifest-only edit can repoint this block while the GET is in
       * flight. Do not disclose the returned content until the armed page
       * still owns the exact block under fresh metadata. */
      if (await freshBlock(boundPort, gen, requestId, blockId) === null) return;
      const reply: Record<string, unknown> = {
        op: "artifact.get", request_id: requestId, exists, content, size,
      };
      if (exists) reply["file_rev"] = rev;
      boundPort.postMessage(reply);
    } finally {
      inflight.delete(requestId);
    }
  };

  const saveArtifact = async (
    boundPort: MessagePort,
    gen: number,
    requestId: string,
    blockId: string,
    content: string,
    baseRev: string,
  ): Promise<void> => {
    const inflight = editorInflight;
    inflight.add(requestId);
    try {
      if (await freshBlock(boundPort, gen, requestId, blockId) === null) return;
      await new Promise((resolve) => setTimeout(resolve, EDITOR_SETTLE_MS));
      if (gen !== generation || port !== boundPort || navPending || quarantined) {
        return answerError(boundPort, "stale-page", requestId);
      }
      /* A manifest-only edit does not necessarily navigate the iframe. Repeat
       * the full identity/block lookup after the settle window so old-page
       * content cannot target a block moved or repointed during that delay. */
      if (await freshBlock(boundPort, gen, requestId, blockId) === null) return;
      const rec = await readEndpointJson(artifactEndpoint(blockId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, base_rev: baseRev }),
      });
      if (gen !== generation || port !== boundPort) return;
      if (rec === null) return answerError(boundPort, "unavailable", requestId);
      if (rec["ok"] !== true) return endpointError(boundPort, requestId, rec);
      const result = rec["result"];
      const rev = rec["file_rev"];
      if (
        (result !== "saved" && result !== "unchanged")
        || typeof rev !== "string" || !FILE_REV_RE.test(rev)
      ) {
        return answerError(boundPort, "unavailable", requestId);
      }
      boundPort.postMessage({
        op: "artifact.save", request_id: requestId, result, file_rev: rev,
      });
    } finally {
      inflight.delete(requestId);
    }
  };

  const runStartEndpoint = (blockId: string): string =>
    `${runsUrl!}/${encodeURIComponent(blockId)}/runs`;

  const runEndpoint = (runId: string, suffix: "stream" | "cancel"): string =>
    new URL(`/learn/runs/${encodeURIComponent(runId)}/${suffix}`, window.location.href).toString();

  const rememberOwnedRun = (runId: string, owner: OwnedRun): void => {
    ownedRuns.delete(runId); // replay moves the same job to the newest slot
    ownedRuns.set(runId, owner);
    while (ownedRuns.size > MAX_OWNED_RUNS) {
      const oldest = ownedRuns.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      ownedRuns.delete(oldest);
    }
  };

  const relayRun = async (
    boundPort: MessagePort,
    gen: number,
    runId: string,
    blockId: string,
    after: number,
  ): Promise<void> => {
    const relay: ActiveRelay = {
      generation: gen,
      run_id: runId,
      controller: new AbortController(),
    };
    activeRelay = relay;
    const ownsRelay = (): boolean => {
      const owner = ownedRuns.get(runId);
      return activeRelay === relay && gen === generation && port === boundPort
        && owner?.generation === gen && owner.block_id === blockId;
    };
    const relayError = (code: string): void => {
      if (ownsRelay()) boundPort.postMessage({ op: "run.error", run_id: runId, code });
    };
    try {
      const url = new URL(runEndpoint(runId, "stream"));
      url.searchParams.set("after", String(after));
      let response: Response;
      try {
        response = await fetch(url, {
          cache: "no-store",
          headers: { Accept: "text/event-stream" },
          signal: relay.controller.signal,
        });
      } catch {
        if (!relay.controller.signal.aborted) relayError("unavailable");
        return;
      }
      if (!ownsRelay()) return;
      if (!response.ok) {
        let code = "unavailable";
        try {
          const body: unknown = await response.json();
          if (typeof body === "object" && body !== null) {
            const raw = (body as Record<string, unknown>)["error"];
            if (typeof raw === "string" && raw.length <= 64) code = raw;
          }
        } catch {
          // keep the generic refusal
        }
        const stillOwned = ownsRelay();
        relayError(code);
        if (stillOwned && code === "job-missing") ownedRuns.delete(runId);
        return;
      }
      if (!response.body) {
        relayError("unavailable");
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8", { fatal: true });
      let buffer = "";
      let cursor = after;

      const applyFrame = (frameText: string): boolean => {
        let eventName: string | null = null;
        let eventId: string | null = null;
        const dataLines: string[] = [];
        for (const rawLine of frameText.split("\n")) {
          const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
          if (line === "" || line.startsWith(":")) continue;
          const colon = line.indexOf(":");
          const field = colon < 0 ? line : line.slice(0, colon);
          let value = colon < 0 ? "" : line.slice(colon + 1);
          if (value.startsWith(" ")) value = value.slice(1);
          if (field === "event") eventName = value;
          else if (field === "id") eventId = value;
          else if (field === "data") dataLines.push(value);
        }
        if (eventName === null && eventId === null && dataLines.length === 0) return false;
        const seq = eventId === null ? NaN : Number(eventId);
        if (!Number.isSafeInteger(seq) || seq <= 0 || dataLines.length === 0) {
          throw new Error("malformed SSE envelope");
        }
        let payload: Record<string, unknown>;
        try {
          const value: unknown = JSON.parse(dataLines.join("\n"));
          if (typeof value !== "object" || value === null) throw new Error("not an object");
          payload = value as Record<string, unknown>;
        } catch {
          throw new Error("malformed SSE data");
        }
        if (payload["seq"] !== seq) throw new Error("SSE sequence mismatch");
        if (seq <= cursor) return false; // replay overlap: already relayed
        if (!ownsRelay()) return true; // navigation/teardown: stop immediately

        if (eventName === "output") {
          const stream = payload["stream"];
          const text = payload["text"];
          if (
            (stream !== "stdout" && stream !== "stderr")
            || typeof text !== "string" || contentByteLength(text) > MAX_OUTPUT_BYTES
          ) throw new Error("malformed output event");
          const message = { op: "run.output", run_id: runId, seq, stream, text };
          const size = serializedByteLength(message);
          if (size === null || size > MAX_PORT_BYTES) throw new Error("oversized relay");
          boundPort.postMessage(message);
          cursor = seq;
          return false;
        }
        if (eventName === "exit") {
          const cause = payload["cause"];
          const truncated = payload["truncated"];
          const duration = payload["duration_ms"];
          if (
            typeof cause !== "string" || !RUN_CAUSES.has(cause)
            || typeof truncated !== "boolean"
            || !Number.isSafeInteger(duration) || (duration as number) < 0
          ) throw new Error("malformed exit event");
          const message: Record<string, unknown> = {
            op: "run.exit", run_id: runId, seq, cause, truncated, duration_ms: duration,
          };
          if (payload["exit_code"] !== undefined) {
            if (!Number.isInteger(payload["exit_code"])) throw new Error("bad exit code");
            message["exit_code"] = payload["exit_code"];
          }
          if (payload["signal"] !== undefined) {
            if (!Number.isInteger(payload["signal"])) throw new Error("bad signal");
            message["signal"] = payload["signal"];
          }
          boundPort.postMessage(message);
          cursor = seq;
          ownedRuns.delete(runId);
          return true;
        }
        throw new Error("unknown SSE event");
      };

      while (ownsRelay()) {
        const chunk = await reader.read();
        buffer += decoder.decode(chunk.value, { stream: !chunk.done });
        let boundary = buffer.indexOf("\n\n");
        while (boundary >= 0) {
          const frameText = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          if (applyFrame(frameText)) {
            await reader.cancel();
            return;
          }
          boundary = buffer.indexOf("\n\n");
        }
        /* A browser read may coalesce many individually valid SSE frames.
         * Drain every complete frame first; the cap applies only to the one
         * incomplete frame retained across reads. */
        if (UTF8.encode(buffer).byteLength > MAX_PORT_BYTES) {
          throw new Error("oversized SSE frame");
        }
        if (chunk.done) {
          if (buffer.trim() && applyFrame(buffer)) return;
          relayError("unavailable"); // a valid stream ends only with run.exit
          return;
        }
      }
      await reader.cancel();
    } catch {
      if (!relay.controller.signal.aborted) relayError("unavailable");
    } finally {
      if (activeRelay === relay) activeRelay = null;
    }
  };

  const saveAndRun = async (
    boundPort: MessagePort,
    gen: number,
    token: object,
    requestId: string,
    blockId: string,
    content: string,
    baseRev: string,
    after: number,
  ): Promise<void> => {
    const inflight = runInflight;
    inflight.add(requestId);
    try {
      const first = await freshBlock(boundPort, gen, requestId, blockId);
      if (first === null) return;
      if (!first.run) return answerError(boundPort, "run-not-enabled", requestId);
      /* Bind server idempotency to the whole logical operation before the
       * save. Same id/block/bytes replays across navigation; changed bytes or
       * block derive a different valid key instead of saving and then hitting
       * a retained server idempotency conflict. */
      const idempotencyKey = deriveRunIdempotencyKey(requestId, blockId, content);
      await new Promise((resolve) => setTimeout(resolve, RUN_SETTLE_MS));
      if (
        runStartToken !== token || gen !== generation || port !== boundPort
        || navPending || quarantined
      ) return answerError(boundPort, "stale-page", requestId);
      const beforeSave = await freshBlock(boundPort, gen, requestId, blockId);
      if (beforeSave === null) return;
      if (!beforeSave.run) return answerError(boundPort, "run-not-enabled", requestId);

      const saved = await readEndpointJson(artifactEndpoint(blockId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, base_rev: baseRev }),
      });
      if (runStartToken !== token || gen !== generation || port !== boundPort) return;
      if (saved === null) return answerError(boundPort, "unavailable", requestId);
      if (saved["ok"] !== true) return endpointError(boundPort, requestId, saved);
      const saveResult = saved["result"];
      const fileRev = saved["file_rev"];
      if (
        (saveResult !== "saved" && saveResult !== "unchanged")
        || typeof fileRev !== "string" || !FILE_REV_RE.test(fileRev)
      ) return answerError(boundPort, "unavailable", requestId);

      /* The run start is a second mutation and may follow a manifest-only edit
       * after save. Re-check this exact block's health-gated Run authority. */
      const beforeRun = await freshBlock(boundPort, gen, requestId, blockId);
      if (beforeRun === null) return;
      if (!beforeRun.run) return answerError(boundPort, "run-not-enabled", requestId);
      const started = await readEndpointJson(runStartEndpoint(blockId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_rev: fileRev, idempotency_key: idempotencyKey }),
      });
      if (runStartToken !== token || gen !== generation || port !== boundPort) return;
      if (started === null) return answerError(boundPort, "unavailable", requestId);
      if (started["ok"] !== true) return endpointError(boundPort, requestId, started);
      const runId = started["job_id"];
      if (typeof runId !== "string" || !JOB_ID_RE.test(runId)) {
        return answerError(boundPort, "unavailable", requestId);
      }
      /* Start is record-time manifest-bound but not page-bound. A block may
       * move while the request is in flight; never give the old document the
       * returned job or its output unless fresh metadata still grants this
       * exact page/block Run authority. */
      const afterStart = await freshBlock(boundPort, gen, requestId, blockId);
      if (afterStart === null) return;
      if (!afterStart.run) return answerError(boundPort, "run-not-enabled", requestId);
      rememberOwnedRun(runId, { generation: gen, block_id: blockId });
      boundPort.postMessage({
        op: "artifact.save_run",
        request_id: requestId,
        result: "started",
        run_id: runId,
        file_rev: fileRev,
      });
      void relayRun(boundPort, gen, runId, blockId, after);
    } finally {
      inflight.delete(requestId);
      if (runStartToken === token) runStartToken = null;
    }
  };

  const cancelRun = async (
    boundPort: MessagePort,
    gen: number,
    requestId: string,
    runId: string,
  ): Promise<void> => {
    const inflight = runInflight;
    inflight.add(requestId);
    try {
      const owner = ownedRuns.get(runId);
      if (!owner || owner.generation !== gen) {
        return answerError(boundPort, "job-missing", requestId);
      }
      /* Cancellation is a monotonic authority reduction for a job already
       * admitted through this document's owned map. Keep fresh page identity
       * and settle checks, but do not strand the job if a manifest-only edit
       * removes or moves its former block while it is running. */
      if (await freshBlocks(boundPort, gen, requestId) === null) return;
      await new Promise((resolve) => setTimeout(resolve, RUN_SETTLE_MS));
      if (gen !== generation || port !== boundPort || navPending || quarantined) {
        return answerError(boundPort, "stale-page", requestId);
      }
      if (await freshBlocks(boundPort, gen, requestId) === null) return;
      const rec = await readEndpointJson(runEndpoint(runId, "cancel"), { method: "POST" });
      if (gen !== generation || port !== boundPort) return;
      if (rec === null) return answerError(boundPort, "unavailable", requestId);
      if (rec["ok"] !== true) {
        if (rec["error"] === "job-missing") ownedRuns.delete(runId);
        return endpointError(boundPort, requestId, rec);
      }
      if (rec["job_id"] !== runId) return answerError(boundPort, "unavailable", requestId);
      boundPort.postMessage({
        op: "run.cancel", request_id: requestId, result: "ack", run_id: runId,
      });
    } finally {
      inflight.delete(requestId);
    }
  };

  const postAttempt = async (
    boundPort: MessagePort,
    gen: number,
    requestId: string,
    questionId: string,
    answer: string,
  ): Promise<void> => {
    /* Capture THIS document's in-flight set (PR-60 round 5): teardown
     * replaces `attemptsInflight`, so a call that outlives its document
     * must clean up its own instance — deleting from the successor's set
     * would un-mark a retry whose HTTP call is still pending and let extra
     * concurrent POSTs past the per-document duplicate/cap logic. */
    const inflight = attemptsInflight;
    inflight.add(requestId);
    try {
      /* Per-operation server-side re-validation (the D2 review gate): the
       * write is allowed only while fresh metadata still advertises exactly
       * the identity this document was armed with — port possession, or
       * having been armed once, is never authority. The server then
       * re-validates the manifest and derives `stale` again at record time;
       * this check just refuses the obvious cases without spending a write. */
      const meta = await fetchMeta();
      if (gen !== generation || port !== boundPort || armed === null) return;
      if (meta === null) return answerError(boundPort, "unavailable", requestId);
      if (
        navPending || quarantined
        || String(meta.version ?? "0") !== expectedVersion
        || meta.bridge !== true || !identityMatches(meta)
      ) {
        return answerError(boundPort, "stale-page", requestId);
      }
      const declared = metaQuestions(meta);
      if (declared === null) return answerError(boundPort, "unavailable", requestId);
      if (!declared.includes(questionId)) {
        return answerError(boundPort, "unknown-question", requestId);
      }
      /* Settle delay, then re-check the document state: an iframe `load`
       * firing in this window (a self-navigation completing) bumps the
       * generation and closes the port, and the write below never leaves. */
      await new Promise((resolve) => setTimeout(resolve, ATTEMPT_SETTLE_MS));
      if (gen !== generation || port !== boundPort || navPending || quarantined) {
        return answerError(boundPort, "stale-page", requestId);
      }
      let body: unknown;
      try {
        const res = await fetch(attemptsUrl!, {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          /* The child supplied question_id and answer; everything else is
           * parent-derived: page identity from the armed binding, the
           * idempotency key from the child's request_id (stable across a
           * reload, so a retry of a response lost to navigation replays the
           * durable original instead of double-recording). */
          body: JSON.stringify({
            question_id: questionId,
            page_id: armed.page_id,
            page_rev: armed.page_rev,
            answer,
            idempotency_key: requestId,
          }),
        });
        body = await res.json();
      } catch {
        if (gen === generation && port === boundPort) {
          answerError(boundPort, "unavailable", requestId);
        }
        return;
      }
      if (gen !== generation || port !== boundPort) return; // navigated away; the write (if any) is durable
      const rec = typeof body === "object" && body !== null
        ? (body as Record<string, unknown>)
        : {};
      if (rec["ok"] !== true) {
        const code = typeof rec["error"] === "string" && rec["error"].length <= 64
          ? rec["error"] : "unavailable";
        return answerError(boundPort, code, requestId);
      }
      const result = rec["result"] === "duplicate" ? "duplicate" : "recorded";
      const reply: Record<string, unknown> = {
        op: "attempt",
        request_id: requestId,
        result,
        attempt_id: rec["attempt_id"],
        stale: rec["stale"] === true,
      };
      if (result === "recorded") {
        reply["attempt_number"] = rec["attempt_number"];
        reply["projection"] = rec["projection"];
        const n = rec["attempt_number"];
        /* Check v1 confirmation: an M5 toast, deliberately no modal. */
        toast(typeof n === "number" ? `attempt #${n} recorded` : "attempt recorded");
      } else {
        toast("attempt already recorded");
      }
      boundPort.postMessage(reply);
    } finally {
      inflight.delete(requestId);
    }
  };

  const onPortMessage = (ev: MessageEvent): void => {
    if (!port) return;
    const size = serializedByteLength(ev.data);
    if (size === null) return protocolError("malformed", null);
    if (size > MAX_PORT_BYTES) return protocolError("oversized", null);
    const msg = ev.data as Record<string, unknown> | null;
    if (typeof msg !== "object" || msg === null || typeof msg["op"] !== "string") {
      return protocolError("malformed", null);
    }
    const rawId = msg["request_id"];
    const requestId = typeof rawId === "string" && rawId.length >= 1 && rawId.length <= 128
      ? rawId : null;
    if (msg["op"] === "ping") {
      if (requestId === null) return protocolError("malformed", null);
      port.postMessage({ op: "pong", request_id: requestId, abi: ABI_VERSION });
      return;
    }
    if (msg["op"] === "attempt") {
      if (requestId === null) return protocolError("malformed", null);
      if (attemptsUrl === null || !capabilities.includes("attempts")) {
        return answerError(port, "capability-not-granted", requestId);
      }
      if (msg["v"] !== ATTEMPT_OP_VERSION) {
        return answerError(port, "unsupported-version", requestId);
      }
      const questionId = msg["question_id"];
      if (typeof questionId !== "string" || !QUESTION_ID_RE.test(questionId)) {
        return answerError(port, "invalid-question-id", requestId);
      }
      const answer = msg["answer"];
      if (typeof answer !== "string") {
        return answerError(port, "invalid-answer", requestId);
      }
      /* The all-op membrane is wide enough for worst-case editor escaping;
       * retain the attempt contract's narrower raw UTF-8 semantic bound. */
      if (contentByteLength(answer) > MAX_ANSWER_BYTES) {
        return answerError(port, "answer-too-large", requestId);
      }
      /* One outcome per in-flight request_id: a duplicate while the original
       * is pending is dropped (the pending call will answer), and total
       * concurrency is capped — a Check press is human-scale. */
      if (attemptsInflight.has(requestId)) return;
      if (attemptsInflight.size >= MAX_ATTEMPTS_INFLIGHT) {
        return answerError(port, "busy", requestId);
      }
      void postAttempt(port, generation, requestId, questionId, answer);
      return;
    }
    if (msg["op"] === "artifact.get" || msg["op"] === "artifact.save") {
      if (requestId === null) return protocolError("malformed", null);
      if (artifactsUrl === null || !capabilities.includes("editor")) {
        return answerError(port, "capability-not-granted", requestId);
      }
      if (msg["v"] !== EDITOR_OP_VERSION) {
        return answerError(port, "unsupported-version", requestId);
      }
      const blockId = msg["block_id"];
      if (typeof blockId !== "string" || !BLOCK_ID_RE.test(blockId)) {
        return answerError(port, "invalid-block-id", requestId);
      }
      if (editorInflight.has(requestId)) return;
      if (editorInflight.size >= MAX_EDITOR_INFLIGHT) {
        return answerError(port, "busy", requestId);
      }
      if (msg["op"] === "artifact.get") {
        void getArtifact(port, generation, requestId, blockId);
        return;
      }
      const content = msg["content"];
      const baseRev = msg["base_rev"];
      if (typeof content !== "string") {
        return answerError(port, "invalid-content", requestId);
      }
      if (contentByteLength(content) > MAX_CONTENT_BYTES) {
        return answerError(port, "file-too-large", requestId);
      }
      if (typeof baseRev !== "string" || !BASE_REV_RE.test(baseRev)) {
        return answerError(port, "invalid-base-rev", requestId);
      }
      void saveArtifact(port, generation, requestId, blockId, content, baseRev);
      return;
    }
    if (msg["op"] === "artifact.save_run" || msg["op"] === "run.cancel") {
      if (requestId === null) return protocolError("malformed", null);
      if (
        artifactsUrl === null || runsUrl === null || !capabilities.includes("run")
      ) return answerError(port, "capability-not-granted", requestId);
      if (msg["v"] !== RUN_OP_VERSION) {
        return answerError(port, "unsupported-version", requestId);
      }
      if (runInflight.has(requestId)) return;
      if (runInflight.size >= MAX_RUN_INFLIGHT) {
        return answerError(port, "busy", requestId);
      }
      if (msg["op"] === "run.cancel") {
        const runId = msg["run_id"];
        /* The ownership map is the authority boundary: malformed and foreign
         * ids are indistinguishable and never reach the global server route. */
        if (typeof runId !== "string" || !JOB_ID_RE.test(runId) || !ownedRuns.has(runId)) {
          return answerError(port, "job-missing", requestId);
        }
        void cancelRun(port, generation, requestId, runId);
        return;
      }

      const blockId = msg["block_id"];
      const content = msg["content"];
      const baseRev = msg["base_rev"];
      const rawAfter = msg["after"] ?? 0;
      if (typeof blockId !== "string" || !BLOCK_ID_RE.test(blockId)) {
        return answerError(port, "invalid-block-id", requestId);
      }
      if (typeof content !== "string") {
        return answerError(port, "invalid-content", requestId);
      }
      if (contentByteLength(content) > MAX_CONTENT_BYTES) {
        return answerError(port, "file-too-large", requestId);
      }
      if (typeof baseRev !== "string" || !BASE_REV_RE.test(baseRev)) {
        return answerError(port, "invalid-base-rev", requestId);
      }
      if (!Number.isSafeInteger(rawAfter) || (rawAfter as number) < 0) {
        return answerError(port, "invalid-cursor", requestId);
      }
      /* The run service consumes request_id verbatim as an idempotency key.
       * Reject values it cannot accept before the composite's artifact save,
       * so invalid input can never save bytes without starting their run. */
      let validRunKey = true;
      for (let i = 0; i < requestId.length; i += 1) {
        const code = requestId.charCodeAt(i);
        if (code < 32 || code === 127) {
          validRunKey = false;
          break;
        }
        if (code >= 0xd800 && code <= 0xdbff) {
          const next = requestId.charCodeAt(i + 1);
          if (!(next >= 0xdc00 && next <= 0xdfff)) {
            validRunKey = false;
            break;
          }
          i += 1;
        } else if (code >= 0xdc00 && code <= 0xdfff) {
          validRunKey = false;
          break;
        }
      }
      if (!validRunKey) {
        return answerError(port, "invalid-idempotency-key", requestId);
      }
      /* One document-wide relay. Refuse before save/start HTTP, so a second
       * Run click cannot start an unobservable job behind the active stream. */
      if (activeRelay !== null || runStartToken !== null) {
        return answerError(port, "busy", requestId);
      }
      const token = {};
      runStartToken = token;
      void saveAndRun(
        port, generation, token, requestId, blockId, content, baseRev, rawAfter as number,
      );
      return;
    }
    return protocolError("unknown-op", requestId);
  };

  const finishReady = (
    data: { abi: unknown[]; want?: unknown[] },
    child: Window,
  ): void => {
    if (armed === null || granted || frame.contentWindow !== child) return;
    const channel = new MessageChannel();
    port = channel.port1;
    port.onmessage = onPortMessage;
    granted = true; // one welcome per loaded document
    /* Capability negotiation is routing, not authority. Editor requires one
     * block and its endpoint; Run additionally requires a health-gated run
     * block plus both save and start endpoints. */
    const want = Array.isArray(data.want) ? data.want : [];
    capabilities = [];
    if (attemptsUrl !== null && want.includes("attempts")) capabilities.push("attempts");
    if (artifactsUrl !== null && armedBlocks.length > 0 && want.includes("editor")) {
      capabilities.push("editor");
    }
    if (
      artifactsUrl !== null && runsUrl !== null
      && armedBlocks.some((block) => block.run) && want.includes("run")
    ) {
      capabilities.push("run");
    }
    child.postMessage(
      {
        ephemeris: "lesson-bridge",
        type: "welcome",
        abi: ABI_VERSION,
        lesson: armed,
        capabilities,
      },
      "*",
      [channel.port2],
    );
  };

  const handleReady = async (
    data: { abi: unknown[]; want?: unknown[] },
  ): Promise<void> => {
    const child = frame.contentWindow;
    if (armed === null || granted || grantToken !== null || !child) return;
    if (!data.abi.includes(ABI_VERSION)) {
      if (rejects < MAX_REJECTS) {
        rejects += 1;
        child.postMessage(
          {
            ephemeris: "lesson-bridge",
            type: "reject",
            reason: "abi-unsupported",
            supported: [ABI_VERSION],
          },
          "*",
        );
      }
      return;
    }
    const want = Array.isArray(data.want) ? data.want : [];
    const needsBlockRefresh = artifactsUrl !== null && (
      want.includes("editor") || (runsUrl !== null && want.includes("run"))
    );
    if (needsBlockRefresh) {
      const gen = generation;
      const token = {};
      grantToken = token;
      try {
        const meta = await fetchMeta();
        if (
          grantToken !== token || gen !== generation || frame.contentWindow !== child
          || armed === null || granted || navPending || quarantined
        ) return;
        /* A transient metadata failure gets silence, not a permanently reduced
         * welcome: the child's documented ready retry can recover. */
        if (meta === null) return;
        if (
          String(meta.version ?? "0") !== expectedVersion
          || meta.bridge !== true || !identityMatches(meta)
        ) return;
        armedBlocks = metaBlocks(meta) ?? [];
        finishReady(data, child);
      } finally {
        if (grantToken === token) grantToken = null;
      }
      return;
    }
    finishReady(data, child);
  };

  /* In-flight latch for the late-initialisation rescue bind below (PR-55
   * round 6): child `ready` retries (or a hostile fast poster) must not fan
   * out one /preview-meta fetch each while the first is still pending. */
  let rescueBinding = false;

  window.addEventListener("message", (ev: MessageEvent) => {
    /* Narrow by state first, then by source — only the document this runtime
     * navigated into the preview frame can ever be answered. */
    if (granted) return;
    const child = frame.contentWindow;
    if (!child || ev.source !== child) return;
    const size = serializedByteLength(ev.data);
    if (size === null || size > MAX_READY_BYTES) return;
    if (!isReady(ev.data)) return;
    if (armed === null) {
      /* Not armed yet: drop the announcement (never buffered — see
       * armFromMeta) and, for a settled document that will never get a
       * load-driven bind because the module initialised after the initial
       * load, start one. The child's retry lands once armed. */
      if (generation === 0 && !navPending && !rescueBinding) {
        rescueBinding = true;
        void bind(generation).finally(() => {
          rescueBinding = false;
        });
      }
      return;
    }
    void handleReady(ev.data);
  });

  /* ---- live-reload poll (the pre-D2 app.js block, now owning binding) ---- */

  let inFlight = false;
  const tick = async (): Promise<void> => {
    if (document.hidden || inFlight) return;
    inFlight = true;
    try {
      const meta = await fetchMeta();
      if (meta !== null) {
        if (String(meta.version ?? "0") !== expectedVersion) {
          navigate(meta);
        } else if (!navPending) {
          if (armed !== null && !identityMatches(meta)) {
            /* Identity drift without a byte/profile change (PR-55 round 3):
             * a manifest-only edit — a corrected pages[].id, a revoked
             * grant — moves bridge_page but not the file's reload token.
             * The armed (possibly granted) identity no longer describes
             * this page: reload, so the next document binds fresh. */
            navigate(meta);
          } else {
            /* Same version, settled document: if the load-driven bind lost
             * its best-effort meta fetch, this is the retry that arms an
             * eligible document (PR-55 round 1). */
            armFromMeta(meta);
          }
        }
      }
    } finally {
      inFlight = false;
    }
  };
  setInterval(() => void tick(), POLL_MS);
  document.addEventListener("visibilitychange", () => void tick());
}
