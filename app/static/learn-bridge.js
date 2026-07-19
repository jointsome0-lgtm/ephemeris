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
 * ABI v1 grants NO write capability: negotiation always lands on the empty
 * set, and the only port operation is ping/pong (D4/D5 add attempts). */
export {};
const ABI_VERSION = 1;
/** Hard cap on a child "ready" announcement (JSON text length). */
const MAX_READY_CHARS = 4096;
/** Hard cap on any port message (JSON text length); mirrors the spec §6.2
 * 64 KiB line bound so a message that could never persist is refused at the
 * membrane instead of deeper in. */
const MAX_PORT_CHARS = 64 * 1024;
/** Port protocol errors tolerated per document before the port is closed. */
const MAX_PROTOCOL_ERRORS = 8;
/** Handshake rejections answered per document (then silence — no help for
 * a probing loop). */
const MAX_REJECTS = 3;
/** Off-manifest self-navigations forced back per document generation chain;
 * a page that fights the re-assert just stays unbridged. */
const MAX_REASSERTS = 3;
const POLL_MS = 1200;
const frame = document.getElementById("lesson-preview-frame");
if (frame && frame.dataset["metaUrl"] && frame.getAttribute("src")) {
    const metaUrl = frame.dataset["metaUrl"];
    const fallbackSrc = frame.getAttribute("src");
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
     * itself somewhere — never bridged, forced back while budget lasts. */
    let navPending = true;
    let reasserts = 0;
    /* Per-document handshake state (cleared on every load/teardown). */
    let armed = null;
    let granted = false;
    let port = null;
    let protocolErrors = 0;
    let rejects = 0;
    /* A child typically announces `ready` the moment its script runs — before
     * the identity-binding meta fetch resolves. The latest valid announcement
     * from the current document is buffered and answered on arm; navigation
     * clears it with the rest of the document state. */
    let pendingReady = null;
    const teardown = () => {
        if (port)
            port.close();
        port = null;
        armed = null;
        granted = false;
        protocolErrors = 0;
        rejects = 0;
        pendingReady = null;
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
        url.searchParams.set("_v", String(Date.now()));
        expectedSrc = url.toString();
        reasserts = 0;
        navPending = true;
        frame.src = expectedSrc;
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
    const armFromMeta = (meta) => {
        if (armed !== null || granted)
            return;
        if (meta.bridge === true && isBridgePage(meta.bridge_page)) {
            armed = {
                lesson_uid: meta.bridge_page.lesson_uid,
                page_id: meta.bridge_page.page_id,
                page_rev: meta.bridge_page.page_rev,
            };
            if (pendingReady !== null) {
                const queued = pendingReady;
                pendingReady = null;
                if (isReady(queued))
                    handleReady(queued);
            }
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
        if (navPending && granted) {
            /* Gen-0 rescue path: the grant already went to this same pending
             * navigation's document (its script announced before its own load
             * event completed) — completing the load must not tear it down and
             * hand the SAME document a second welcome. */
            navPending = false;
            return;
        }
        teardown();
        if (!navPending) {
            /* Self-navigation: the lesson document went somewhere on its own. The
             * new document is NOT the page any identity was derived from — never
             * bind it; put the expected page back while the budget lasts. */
            if (reasserts < MAX_REASSERTS) {
                reasserts += 1;
                const url = new URL(expectedSrc);
                url.searchParams.set("_v", String(Date.now()));
                navPending = true;
                frame.src = url.toString();
            }
            return;
        }
        navPending = false;
        void bind(generation);
    });
    /* ---- handshake membrane (the only global listener; everything after the
     * welcome flows over the transferred MessagePort) ---- */
    const jsonLength = (value) => {
        try {
            const text = JSON.stringify(value);
            return typeof text === "string" ? text.length : null;
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
    const onPortMessage = (ev) => {
        if (!port)
            return;
        const size = jsonLength(ev.data);
        if (size === null)
            return protocolError("malformed", null);
        if (size > MAX_PORT_CHARS)
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
        /* ABI v1 has no other operations (writes arrive with D4/D5). */
        return protocolError("unknown-op", requestId);
    };
    const handleReady = (data) => {
        const child = frame.contentWindow;
        if (armed === null || granted || !child)
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
        const channel = new MessageChannel();
        port = channel.port1;
        port.onmessage = onPortMessage;
        granted = true; // one welcome per loaded document
        child.postMessage({
            ephemeris: "lesson-bridge",
            type: "welcome",
            abi: ABI_VERSION,
            lesson: armed,
            /* Capability negotiation, v1: whatever the child `want`ed, the
             * granted set is empty — the ABI ships before any capability. */
            capabilities: [],
        }, "*", [channel.port2]);
    };
    window.addEventListener("message", (ev) => {
        /* Narrow by state first, then by source — only the document this runtime
         * navigated into the preview frame can ever be answered. */
        if (granted)
            return;
        const child = frame.contentWindow;
        if (!child || ev.source !== child)
            return;
        const size = jsonLength(ev.data);
        if (size === null || size > MAX_READY_CHARS)
            return;
        if (!isReady(ev.data))
            return;
        if (armed === null) {
            pendingReady = ev.data; // answered when (and if) identity arms
            if (generation === 0) {
                /* No load event has been observed, yet the frame's document is
                 * demonstrably running (it just announced): the document finished
                 * loading before this module initialised, so no load-driven bind is
                 * coming — bind here. Children re-announce until answered (ABI §2),
                 * so the flush-or-retry pair completes the handshake. */
                void bind(generation);
            }
            return;
        }
        handleReady(ev.data);
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
                    /* Same version, settled document: if the load-driven bind lost its
                     * best-effort meta fetch, this is the retry that arms an eligible
                     * document (PR-55 round 1). Never during a pending navigation —
                     * the outgoing document could still announce into the gap and be
                     * granted the incoming page's identity. */
                    armFromMeta(meta);
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
