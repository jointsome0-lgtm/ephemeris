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
 * D5 adds the `attempts` capability and phase F adds the editor membrane.
 * Both are child-requested routing facts, never authority: every operation
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
/** The op-envelope version the attempt operation speaks (independent of the
 * handshake ABI so the submission shape can evolve additively). */
const ATTEMPT_OP_VERSION = 1;
const EDITOR_OP_VERSION = 1;
const MAX_ANSWER_BYTES = 32 * 1024;
const MAX_CONTENT_BYTES = 64 * 1024;
const QUESTION_ID_RE = /^q_[a-z0-9]{4,32}$/;
const BLOCK_ID_RE = /^blk_[a-z0-9]{4,32}$/;
const FILE_REV_RE = /^sha256:[a-f0-9]{64}$/;
const BASE_REV_RE = /^(?:absent|sha256:[a-f0-9]{64})$/;
const UTF8 = new TextEncoder();
const frame = document.getElementById("lesson-preview-frame");
if (frame && frame.dataset["metaUrl"] && frame.getAttribute("src")) {
    const metaUrl = frame.dataset["metaUrl"];
    const fallbackSrc = frame.getAttribute("src");
    /* Attempt endpoint (D4). Absent on a stale template render: the attempts
     * capability is then simply never granted (fail closed, no error). */
    const attemptsUrl = frame.dataset["attemptsUrl"] || null;
    /* Phase-F artifact endpoint prefix. An older live backend renders no data
     * attribute, so `editor` is never granted while the static is ahead. */
    const artifactsUrl = frame.dataset["artifactsUrl"] || null;
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
    let armed = null;
    let granted = false;
    let port = null;
    let protocolErrors = 0;
    let rejects = 0;
    /* D5 per-document write state: the capability set the welcome granted and
     * the request_ids with an attempt HTTP call still pending. Navigation ends
     * both — a successor document never inherits a grant or an in-flight slot
     * (the durable outcome is still reachable: the child retries the same
     * request_id after reload and the server replays it). */
    let capabilities = [];
    let attemptsInflight = new Set();
    let editorInflight = new Set();
    let armedBlocks = [];
    /* Serialises a handshake-time metadata refresh. An object token prevents a
     * stale document's finally block from clearing a successor's refresh. */
    let grantToken = null;
    const teardown = () => {
        if (port)
            port.close();
        port = null;
        armed = null;
        granted = false;
        protocolErrors = 0;
        rejects = 0;
        capabilities = [];
        attemptsInflight = new Set();
        editorInflight = new Set();
        armedBlocks = [];
        grantToken = null;
    };
    const fetchMeta = async () => {
        try {
            const r = await fetch(metaUrl, { cache: "no-store" });
            const data = await r.json();
            if (typeof data !== "object" || data === null)
                return null;
            return data;
        }
        catch {
            return null; // best-effort; the next tick retries
        }
    };
    const SANDBOX_OK = /^[a-z][a-z -]{0,255}$/;
    const applySandbox = (meta) => {
        /* The server owns the token policy (one owner next to the CSP map); the
         * client only re-applies it across profile flips. Absent/odd values
         * (e.g. a pre-D2 backend) leave the attribute as rendered. */
        const tokens = meta.sandbox;
        if (typeof tokens === "string" && SANDBOX_OK.test(tokens)
            && frame.getAttribute("sandbox") !== tokens) {
            frame.setAttribute("sandbox", tokens);
        }
    };
    const navigate = (meta) => {
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
    const identityMatches = (meta) => {
        if (armed === null)
            return true; // nothing bound, nothing to drift
        if (meta.bridge !== true || !isBridgePage(meta.bridge_page))
            return false;
        return meta.bridge_page.lesson_uid === armed.lesson_uid
            && meta.bridge_page.page_id === armed.page_id
            && meta.bridge_page.page_rev === armed.page_rev;
    };
    const isBridgePage = (value) => {
        if (typeof value !== "object" || value === null)
            return false;
        const page = value;
        return ["lesson_uid", "page_id", "page_rev"].every((key) => {
            const field = page[key];
            return typeof field === "string" && field.length > 0 && field.length <= 256;
        });
    };
    /* Block routing metadata for the armed page. `null` means a malformed or
     * pre-F backend response; the attempts membrane remains independently
     * usable, while editor capability fails closed. */
    const metaBlocks = (meta) => {
        if (typeof meta.bridge_page !== "object" || meta.bridge_page === null)
            return null;
        const list = meta.bridge_page["blocks"];
        if (!Array.isArray(list) || list.length > MAX_BRIDGE_BLOCKS)
            return null;
        const blocks = [];
        for (const value of list) {
            if (typeof value !== "object" || value === null)
                return null;
            const block = value;
            if (typeof block["id"] !== "string" || !BLOCK_ID_RE.test(block["id"]))
                return null;
            if (typeof block["run"] !== "boolean")
                return null;
            blocks.push({ id: block["id"], run: block["run"] });
        }
        return blocks;
    };
    const armFromMeta = (meta) => {
        /* Single choke point (PR-55 round 3): never arm while a navigation is
         * pending — the outgoing document can still announce into the gap and
         * would be granted the INCOMING page's identity — nor while the frame
         * is quarantined after exhausting the self-navigation re-assert budget
         * (round 5). Consequence: grants only ever go to settled documents the
         * parent itself navigated to. */
        if (quarantined || navPending || armed !== null || granted)
            return;
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
    const bind = async (gen) => {
        const meta = await fetchMeta();
        if (gen !== generation || meta === null)
            return;
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
            }
            else {
                quarantined = true;
            }
            return;
        }
        navPending = false;
        void bind(generation);
    });
    /* ---- handshake membrane (the only global listener; everything after the
     * welcome flows over the transferred MessagePort) ---- */
    const serializedByteLength = (value) => {
        try {
            const text = JSON.stringify(value);
            return typeof text === "string" ? UTF8.encode(text).byteLength : null;
        }
        catch {
            return null; // cyclic or otherwise non-JSON structured-clone payload
        }
    };
    const isReady = (value) => {
        if (typeof value !== "object" || value === null)
            return false;
        const msg = value;
        if (msg["ephemeris"] !== "lesson-bridge" || msg["type"] !== "ready")
            return false;
        if (!Array.isArray(msg["abi"]) || msg["abi"].length === 0 || msg["abi"].length > 8)
            return false;
        if (!msg["abi"].every((v) => Number.isInteger(v) && v >= 1 && v <= 999))
            return false;
        if ("want" in msg) {
            const want = msg["want"];
            if (!Array.isArray(want) || want.length > 16)
                return false;
            if (!want.every((v) => typeof v === "string" && v.length <= 64))
                return false;
        }
        return true;
    };
    const protocolError = (code, requestId) => {
        protocolErrors += 1;
        if (port) {
            port.postMessage(requestId === null
                ? { op: "error", code }
                : { op: "error", code, request_id: requestId });
            if (protocolErrors >= MAX_PROTOCOL_ERRORS) {
                /* Fail closed for THIS document: the port dies, the grant stays
                 * consumed (no second port until a fresh navigation). */
                port.close();
                port = null;
            }
        }
    };
    const toast = (msg) => {
        const ui = window.alUI;
        if (ui && typeof ui.toast === "function")
            ui.toast(msg);
    };
    /* Attempt refusals are ANSWERS, not protocol violations: they reuse the
     * endpoint's error codes verbatim (docs/lesson-attempts-api.md) and never
     * count toward the port-closing budget — a page retrying a retired
     * question must not lose its whole bridge. */
    const answerError = (to, code, requestId, fields = {}) => {
        to.postMessage({ op: "error", code, request_id: requestId, ...fields });
    };
    /* Declared question ids for the armed page, taken from FRESH metadata at
     * operation time (never the arm-time copy: a manifest-only edit can
     * declare or retire questions without moving the page's version token).
     * null = absent or malformed — e.g. a pre-D5 backend — and fails closed. */
    const metaQuestions = (meta) => {
        if (typeof meta.bridge_page !== "object" || meta.bridge_page === null)
            return null;
        const list = meta.bridge_page["questions"];
        if (!Array.isArray(list) || list.length > 512)
            return null;
        return list.every((q) => typeof q === "string" && QUESTION_ID_RE.test(q))
            ? list
            : null;
    };
    const contentByteLength = (content) => UTF8.encode(content).byteLength;
    const endpointError = (to, requestId, record) => {
        const code = typeof record["error"] === "string" && record["error"].length <= 64
            ? record["error"] : "unavailable";
        if (code === "file-conflict") {
            const rev = record["file_rev"];
            if (rev === null || (typeof rev === "string" && FILE_REV_RE.test(rev))) {
                answerError(to, code, requestId, { file_rev: rev });
            }
            else {
                answerError(to, "unavailable", requestId);
            }
            return;
        }
        answerError(to, code, requestId);
    };
    const freshBlock = async (boundPort, gen, requestId, blockId) => {
        const meta = await fetchMeta();
        if (gen !== generation || port !== boundPort || armed === null)
            return null;
        if (meta === null) {
            answerError(boundPort, "unavailable", requestId);
            return null;
        }
        if (navPending || quarantined
            || String(meta.version ?? "0") !== expectedVersion
            || meta.bridge !== true || !identityMatches(meta)) {
            answerError(boundPort, "stale-page", requestId);
            return null;
        }
        const blocks = metaBlocks(meta);
        if (blocks === null) {
            answerError(boundPort, "unavailable", requestId);
            return null;
        }
        const block = blocks.find((candidate) => candidate.id === blockId);
        if (!block) {
            answerError(boundPort, "unknown-block", requestId);
            return null;
        }
        return block;
    };
    const artifactEndpoint = (blockId) => `${artifactsUrl}/${encodeURIComponent(blockId)}/file`;
    const readEndpointJson = async (url, init) => {
        try {
            const response = await fetch(url, { cache: "no-store", ...init });
            const body = await response.json();
            return typeof body === "object" && body !== null
                ? body : null;
        }
        catch {
            return null;
        }
    };
    const getArtifact = async (boundPort, gen, requestId, blockId) => {
        const inflight = editorInflight;
        inflight.add(requestId);
        try {
            if (await freshBlock(boundPort, gen, requestId, blockId) === null)
                return;
            const rec = await readEndpointJson(artifactEndpoint(blockId));
            if (gen !== generation || port !== boundPort)
                return;
            if (rec === null)
                return answerError(boundPort, "unavailable", requestId);
            if (rec["ok"] !== true)
                return endpointError(boundPort, requestId, rec);
            const exists = rec["exists"];
            const content = rec["content"];
            const size = rec["size"];
            const rev = rec["file_rev"];
            if (typeof exists !== "boolean"
                || typeof content !== "string"
                || !Number.isInteger(size) || size < 0 || size > MAX_CONTENT_BYTES
                || contentByteLength(content) > MAX_CONTENT_BYTES
                || (exists && (typeof rev !== "string" || !FILE_REV_RE.test(rev)))
                || (!exists && rev !== undefined)) {
                return answerError(boundPort, "unavailable", requestId);
            }
            const reply = {
                op: "artifact.get", request_id: requestId, exists, content, size,
            };
            if (exists)
                reply["file_rev"] = rev;
            boundPort.postMessage(reply);
        }
        finally {
            inflight.delete(requestId);
        }
    };
    const saveArtifact = async (boundPort, gen, requestId, blockId, content, baseRev) => {
        const inflight = editorInflight;
        inflight.add(requestId);
        try {
            if (await freshBlock(boundPort, gen, requestId, blockId) === null)
                return;
            await new Promise((resolve) => setTimeout(resolve, EDITOR_SETTLE_MS));
            if (gen !== generation || port !== boundPort || navPending || quarantined) {
                return answerError(boundPort, "stale-page", requestId);
            }
            /* A manifest-only edit does not necessarily navigate the iframe. Repeat
             * the full identity/block lookup after the settle window so old-page
             * content cannot target a block moved or repointed during that delay. */
            if (await freshBlock(boundPort, gen, requestId, blockId) === null)
                return;
            const rec = await readEndpointJson(artifactEndpoint(blockId), {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ content, base_rev: baseRev }),
            });
            if (gen !== generation || port !== boundPort)
                return;
            if (rec === null)
                return answerError(boundPort, "unavailable", requestId);
            if (rec["ok"] !== true)
                return endpointError(boundPort, requestId, rec);
            const result = rec["result"];
            const rev = rec["file_rev"];
            if ((result !== "saved" && result !== "unchanged")
                || typeof rev !== "string" || !FILE_REV_RE.test(rev)) {
                return answerError(boundPort, "unavailable", requestId);
            }
            boundPort.postMessage({
                op: "artifact.save", request_id: requestId, result, file_rev: rev,
            });
        }
        finally {
            inflight.delete(requestId);
        }
    };
    const postAttempt = async (boundPort, gen, requestId, questionId, answer) => {
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
            if (gen !== generation || port !== boundPort || armed === null)
                return;
            if (meta === null)
                return answerError(boundPort, "unavailable", requestId);
            if (navPending || quarantined
                || String(meta.version ?? "0") !== expectedVersion
                || meta.bridge !== true || !identityMatches(meta)) {
                return answerError(boundPort, "stale-page", requestId);
            }
            const declared = metaQuestions(meta);
            if (declared === null)
                return answerError(boundPort, "unavailable", requestId);
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
            let body;
            try {
                const res = await fetch(attemptsUrl, {
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
            }
            catch {
                if (gen === generation && port === boundPort) {
                    answerError(boundPort, "unavailable", requestId);
                }
                return;
            }
            if (gen !== generation || port !== boundPort)
                return; // navigated away; the write (if any) is durable
            const rec = typeof body === "object" && body !== null
                ? body
                : {};
            if (rec["ok"] !== true) {
                const code = typeof rec["error"] === "string" && rec["error"].length <= 64
                    ? rec["error"] : "unavailable";
                return answerError(boundPort, code, requestId);
            }
            const result = rec["result"] === "duplicate" ? "duplicate" : "recorded";
            const reply = {
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
            }
            else {
                toast("attempt already recorded");
            }
            boundPort.postMessage(reply);
        }
        finally {
            inflight.delete(requestId);
        }
    };
    const onPortMessage = (ev) => {
        if (!port)
            return;
        const size = serializedByteLength(ev.data);
        if (size === null)
            return protocolError("malformed", null);
        if (size > MAX_PORT_BYTES)
            return protocolError("oversized", null);
        const msg = ev.data;
        if (typeof msg !== "object" || msg === null || typeof msg["op"] !== "string") {
            return protocolError("malformed", null);
        }
        const rawId = msg["request_id"];
        const requestId = typeof rawId === "string" && rawId.length >= 1 && rawId.length <= 128
            ? rawId : null;
        if (msg["op"] === "ping") {
            if (requestId === null)
                return protocolError("malformed", null);
            port.postMessage({ op: "pong", request_id: requestId, abi: ABI_VERSION });
            return;
        }
        if (msg["op"] === "attempt") {
            if (requestId === null)
                return protocolError("malformed", null);
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
            if (attemptsInflight.has(requestId))
                return;
            if (attemptsInflight.size >= MAX_ATTEMPTS_INFLIGHT) {
                return answerError(port, "busy", requestId);
            }
            void postAttempt(port, generation, requestId, questionId, answer);
            return;
        }
        if (msg["op"] === "artifact.get" || msg["op"] === "artifact.save") {
            if (requestId === null)
                return protocolError("malformed", null);
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
            if (editorInflight.has(requestId))
                return;
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
        return protocolError("unknown-op", requestId);
    };
    const finishReady = (data, child) => {
        if (armed === null || granted || frame.contentWindow !== child)
            return;
        const channel = new MessageChannel();
        port = channel.port1;
        port.onmessage = onPortMessage;
        granted = true; // one welcome per loaded document
        /* Capability negotiation is routing, not authority. `editor` additionally
         * requires at least one declared block from the handshake-time metadata;
         * an old backend omits data-artifacts-url, so it stays absent. */
        const want = Array.isArray(data.want) ? data.want : [];
        capabilities = [];
        if (attemptsUrl !== null && want.includes("attempts"))
            capabilities.push("attempts");
        if (artifactsUrl !== null && armedBlocks.length > 0 && want.includes("editor")) {
            capabilities.push("editor");
        }
        child.postMessage({
            ephemeris: "lesson-bridge",
            type: "welcome",
            abi: ABI_VERSION,
            lesson: armed,
            capabilities,
        }, "*", [channel.port2]);
    };
    const handleReady = async (data) => {
        const child = frame.contentWindow;
        if (armed === null || granted || grantToken !== null || !child)
            return;
        if (!data.abi.includes(ABI_VERSION)) {
            if (rejects < MAX_REJECTS) {
                rejects += 1;
                child.postMessage({
                    ephemeris: "lesson-bridge",
                    type: "reject",
                    reason: "abi-unsupported",
                    supported: [ABI_VERSION],
                }, "*");
            }
            return;
        }
        const want = Array.isArray(data.want) ? data.want : [];
        if (artifactsUrl !== null && want.includes("editor")) {
            const gen = generation;
            const token = {};
            grantToken = token;
            try {
                const meta = await fetchMeta();
                if (grantToken !== token || gen !== generation || frame.contentWindow !== child
                    || armed === null || granted || navPending || quarantined)
                    return;
                /* A transient metadata failure gets silence, not a permanently reduced
                 * welcome: the child's documented ready retry can recover. */
                if (meta === null)
                    return;
                if (String(meta.version ?? "0") !== expectedVersion
                    || meta.bridge !== true || !identityMatches(meta))
                    return;
                armedBlocks = metaBlocks(meta) ?? [];
                finishReady(data, child);
            }
            finally {
                if (grantToken === token)
                    grantToken = null;
            }
            return;
        }
        finishReady(data, child);
    };
    /* In-flight latch for the late-initialisation rescue bind below (PR-55
     * round 6): child `ready` retries (or a hostile fast poster) must not fan
     * out one /preview-meta fetch each while the first is still pending. */
    let rescueBinding = false;
    window.addEventListener("message", (ev) => {
        /* Narrow by state first, then by source — only the document this runtime
         * navigated into the preview frame can ever be answered. */
        if (granted)
            return;
        const child = frame.contentWindow;
        if (!child || ev.source !== child)
            return;
        const size = serializedByteLength(ev.data);
        if (size === null || size > MAX_READY_BYTES)
            return;
        if (!isReady(ev.data))
            return;
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
    const tick = async () => {
        if (document.hidden || inFlight)
            return;
        inFlight = true;
        try {
            const meta = await fetchMeta();
            if (meta !== null) {
                if (String(meta.version ?? "0") !== expectedVersion) {
                    navigate(meta);
                }
                else if (!navPending) {
                    if (armed !== null && !identityMatches(meta)) {
                        /* Identity drift without a byte/profile change (PR-55 round 3):
                         * a manifest-only edit — a corrected pages[].id, a revoked
                         * grant — moves bridge_page but not the file's reload token.
                         * The armed (possibly granted) identity no longer describes
                         * this page: reload, so the next document binds fresh. */
                        navigate(meta);
                    }
                    else {
                        /* Same version, settled document: if the load-driven bind lost
                         * its best-effort meta fetch, this is the retry that arms an
                         * eligible document (PR-55 round 1). */
                        armFromMeta(meta);
                    }
                }
            }
        }
        finally {
            inFlight = false;
        }
    };
    setInterval(() => void tick(), POLL_MS);
    document.addEventListener("visibilitychange", () => void tick());
}
