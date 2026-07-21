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
 * D5 adds the one write capability, `attempts`: the child asks (`want`),
 * and when this runtime can reach the attempt endpoint the welcome grants
 * it. The child supplies ONLY {v, op, request_id, question_id, answer}; the
 * parent derives page identity from its own armed binding, re-validates it
 * against fresh preview metadata per operation (D2 review gate: possession
 * of the port is never authority — and neither is having been armed once),
 * refuses questions the manifest does not declare for the armed page, and
 * owns idempotency by mapping the child's request_id onto the endpoint's
 * idempotency_key. Confirmation is an app toast; the lesson document only
 * gets the structured result. */
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
/** Attempt operations in flight at once per document; beyond it the op is
 * answered `busy` (a Check press is human-scale — this only stops a loop). */
const MAX_ATTEMPTS_INFLIGHT = 4;
/** The op-envelope version the attempt operation speaks (independent of the
 * handshake ABI so the submission shape can evolve additively). */
const ATTEMPT_OP_VERSION = 1;
const QUESTION_ID_RE = /^q_[a-z0-9]{4,32}$/;
const frame = document.getElementById("lesson-preview-frame");
if (frame && frame.dataset["metaUrl"] && frame.getAttribute("src")) {
    const metaUrl = frame.dataset["metaUrl"];
    const fallbackSrc = frame.getAttribute("src");
    /* Attempt endpoint (D4). Absent on a stale template render: the attempts
     * capability is then simply never granted (fail closed, no error). */
    const attemptsUrl = frame.dataset["attemptsUrl"] || null;
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
    const toast = (msg) => {
        const ui = window.alUI;
        if (ui && typeof ui.toast === "function")
            ui.toast(msg);
    };
    /* Attempt refusals are ANSWERS, not protocol violations: they reuse the
     * endpoint's error codes verbatim (docs/lesson-attempts-api.md) and never
     * count toward the port-closing budget — a page retrying a retired
     * question must not lose its whole bridge. */
    const answerError = (to, code, requestId) => {
        to.postMessage({ op: "error", code, request_id: requestId });
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
    const postAttempt = async (boundPort, gen, requestId, questionId, answer) => {
        attemptsInflight.add(requestId);
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
            attemptsInflight.delete(requestId);
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
        /* Capability negotiation (D5): the one defined capability is `attempts`,
         * granted only when the child asked for it and this runtime has the
         * endpoint to carry it. The grant is a routing fact, not authority —
         * every operation still re-validates per-op, parent- and server-side. */
        const want = Array.isArray(data.want) ? data.want : [];
        capabilities = attemptsUrl !== null && want.includes("attempts")
            ? ["attempts"]
            : [];
        child.postMessage({
            ephemeris: "lesson-bridge",
            type: "welcome",
            abi: ABI_VERSION,
            lesson: armed,
            capabilities,
        }, "*", [channel.port2]);
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
        const size = jsonLength(ev.data);
        if (size === null || size > MAX_READY_CHARS)
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
