"""The read-back consent gate, driven in a real browser (drain M1).

The node harness beside this one proves the runtime's decisions; it cannot
prove the thing the gate exists for. Two facts only a browser establishes:
`window.confirm` really is a modal the owner answers before any byte crosses
the frame, and a sandboxed lesson page really can carry what it was handed to
another site by assigning `location.href` — a request no CSP on the response
covers. So these tests run the committed `learn-bridge.js` in headless Chrome
over CDP, against an invented lesson site with invented answers, and watch the
outbound request that would leak them.

Everything here is throwaway: a loopback HTTP server on an ephemeral port, a
disposable Chrome profile, and data that exists nowhere but this module. The
tests skip when no Chrome is installed rather than pretending to have run.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Invented private runtime state: the strings whose escape this watches for.
ANSWER = "invented-answer-orbit-7"
NOTE = "invented-note-orbit-7"
QUESTION_ID = "q_browser01"
BLOCK_ID = "blk_browser01"
RUN_REQUEST_ID = "r-run-orbit-7"
RUN_CONTENT = "print('invented orbit \U0001fa90')\n"
RUN_FILE_REV = "sha256:" + "c" * 64
RUN_JOB_ID = "0badcafe-0000-4000-8000-000000c0ffee"

SNAPSHOT = {
    "lesson_uid": "les_browser",
    "page_id": "pg_browser01",
    "questions": [{
        "question_id": QUESTION_ID,
        "asked": False,
        "answer": ANSWER,
        "answer_truncated": False,
        "answered_at": "2026-08-06T09:00:00Z",
        "stale": False,
        "verdict": {
            "level": "partial", "note": NOTE,
            "recorded_at": "2026-08-06T10:00:00Z",
        },
    }],
}

BRIDGE_PAGE = {
    "lesson_uid": "les_browser",
    "page_id": "pg_browser01",
    "page_rev": "sha256:" + "b" * 64,
    "questions": [QUESTION_ID],
    "blocks": [{"id": BLOCK_ID, "run": True}],
}

PARENT_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>invented learn page</title></head>
<body>
<iframe id="lesson-preview-frame" src="CHILDSRC"
        sandbox="allow-scripts"
        data-meta-url="/preview-meta"
        data-attempts-url="/attempts"
        data-artifacts-url="/artifacts"
        data-runs-url="/runs"
        data-src="/child.html"
        data-version="v1"
        data-loaded="0"
        data-record='RECORD'></iframe>
<script>
  /* The same load counter learn.html keeps, and a mailbox for what the child
   * says it received. The probe never reads the welcome itself — it only
   * repeats what crossed the boundary. */
  window.__probe = { welcomes: [], successors: [], pongs: [], loads: 0 };
  var frame = document.getElementById("lesson-preview-frame");
  frame.addEventListener("load", function () {
    window.__probe.loads += 1;
    frame.dataset.loaded = String(window.__probe.loads);
  });
  window.addEventListener("message", function (event) {
    var data = event.data;
    if (!data) return;
    if (data.probe === "welcome") window.__probe.welcomes.push(data);
    if (data.probe === "successor") window.__probe.successors.push(data);
    if (data.probe === "pong") window.__probe.pongs.push(data);
  });
</script>
<script type="module" src="/learn-bridge.js"></script>
</body></html>
"""

CHILD_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>invented lesson page</title></head>
<body>
<script>
  var params = new URLSearchParams(location.search);
  var announced = 0;
  var ran = false;
  /* `legacy=1` announces the retired v1 way — no transferred port, listening
   * on the window. `navigate=<url>` commits a same-frame navigation and then
   * announces, so the successor is already in flight while the parent's
   * consent prompt stands open. */
  var legacy = params.get("legacy") === "1";
  var navigateTo = params.get("navigate");

  function handle(data, ports, via) {
    if (!data || data.ephemeris !== "lesson-bridge" || data.type !== "welcome") return;
    var answer = data.record && data.record.questions.length
      ? data.record.questions[0].answer : null;
    /* Report the WHOLE welcome, so the test can assert on bytes rather than
     * on this page's reading of them. */
    parent.postMessage({
      probe: "welcome", doc: params.get("doc"), via: via, ports: ports.length,
      raw: JSON.stringify(data), answer: answer,
    }, "*");
    if (ports.length === 1) {
      ports[0].onmessage = function (portEvent) {
        parent.postMessage({
          probe: "pong", doc: params.get("doc"),
          raw: JSON.stringify(portEvent.data),
        }, "*");
      };
      ports[0].postMessage({ op: "ping", request_id: "r-probe" });
      if (params.get("run") === "1" && !ran) {
        ran = true;
        ports[0].postMessage({
          op: "artifact.save_run", v: 1, request_id: RUN_REQUEST_ID_JSON,
          block_id: BLOCK_ID_JSON, content: RUN_CONTENT_JSON,
          base_rev: "absent", after: 0,
        });
      }
    }
    /* The residual the gate is about: permitted script navigates the frame
     * and the private text rides along in the URL. */
    if (params.get("egress") === "1" && answer) {
      location.href = "/captured?value=" + encodeURIComponent(answer)
        + "&note=" + encodeURIComponent(data.record.questions[0].verdict.note);
    }
  }

  /* Nothing may arrive here under ABI v2 — that is the point of the legacy
   * case, and a silent guard for every other one. */
  window.addEventListener("message", function (event) {
    handle(event.data, event.ports || [], "window");
  });

  function announce() {
    var ready = {
      ephemeris: "lesson-bridge", type: "ready", abi: [2],
      want: params.get("run") === "1" ? ["attempts", "run"] : ["attempts"],
    };
    if (legacy) return parent.postMessage(ready, "*");
    /* A transferred port is spent, so every retry mints its own channel. */
    var channel = new MessageChannel();
    channel.port1.onmessage = function (event) {
      handle(event.data, event.ports || [], "port");
    };
    parent.postMessage(ready, "*", [channel.port2]);
  }

  if (navigateTo) {
    /* Announce only once the parent has had time to arm; otherwise the
     * announcement is dropped and the run proves nothing. The test asserts a
     * dialog really opened, so a mis-timed run fails loudly. */
    setTimeout(function () {
      location.href = navigateTo;
      announce();
    }, 900);
  } else {
    announce();
    /* The documented retry: the parent module can initialise after this load. */
    var retry = setInterval(function () {
      announced += 1;
      if (announced > 8) return clearInterval(retry);
      announce();
    }, 150);
  }
</script>
</body></html>
"""

SUCCESSOR_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>invented successor page</title></head>
<body>
<!-- The slow blocking subresource: this document runs script immediately but
     its `load` event — the parent's only signal that the document changed —
     stays pending for four seconds. -->
<img src="/slow?ms=4000" alt="">
<script>
  /* Everything the retired delivery path could have posted into, watched at
   * once: the window listener, and any port that came with it. */
  function grab(data, ports, via) {
    if (!data || data.ephemeris !== "lesson-bridge") return;
    var questions = (data.record && data.record.questions) || [];
    var answer = questions.length ? questions[0].answer : null;
    parent.postMessage({
      probe: "successor", via: via, type: data.type, ports: ports.length,
      raw: JSON.stringify(data), answer: answer,
    }, "*");
    if (answer) {
      location.href = "/captured?value=" + encodeURIComponent(answer)
        + "&note=" + encodeURIComponent(questions[0].verdict.note);
    }
  }
  window.addEventListener("message", function (event) {
    grab(event.data, event.ports || [], "window");
  });
</script>
</body></html>
"""


def _chrome() -> str | None:
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


CHROME = _chrome()
pytestmark = pytest.mark.skipif(
    CHROME is None, reason="a real browser is required to drive the consent gate"
)


class _Site:
    """The invented lesson site, plus the log of what tried to leave it."""

    def __init__(self, egress: bool, child_query: str = "doc=1") -> None:
        self.captured: list[dict] = []
        self.saves: list[dict] = []
        self.run_starts: list[dict] = []
        self.meta_reads = 0
        self.version = "v1"
        self.child_query = child_query
        site = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # noqa: A003 - silence the test server
                pass

            def _send(self, body: bytes, content_type: str) -> None:
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    # A page that navigates away mid-response cancels its
                    # pending subresources; that is the behaviour under test,
                    # not a server fault worth a traceback.
                    pass

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                path = urlparse(self.path).path
                if path == "/parent.html":
                    src = (
                        "/child.html?" + site.child_query
                        + "&amp;egress=" + ("1" if egress else "0")
                    )
                    body = (
                        PARENT_HTML
                        .replace("RECORD", json.dumps(SNAPSHOT).replace('"', "&quot;"))
                        .replace("CHILDSRC", src)
                    )
                    return self._send(body.encode("utf-8"), "text/html; charset=utf-8")
                if path == "/child.html":
                    body = (
                        CHILD_HTML
                        .replace("RUN_REQUEST_ID_JSON", json.dumps(RUN_REQUEST_ID))
                        .replace("BLOCK_ID_JSON", json.dumps(BLOCK_ID))
                        .replace("RUN_CONTENT_JSON", json.dumps(RUN_CONTENT))
                    )
                    return self._send(body.encode("utf-8"), "text/html; charset=utf-8")
                if path == "/successor.html":
                    return self._send(
                        SUCCESSOR_HTML.encode("utf-8"), "text/html; charset=utf-8")
                if path == "/slow":
                    # The blocking subresource that holds the successor's
                    # `load` open. A threading server keeps the rest alive.
                    delay = parse_qs(urlparse(self.path).query).get("ms", ["1000"])
                    time.sleep(min(int(delay[0]), 10000) / 1000)
                    return self._send(b"", "image/png")
                if path == "/learn-bridge.js":
                    return self._send(
                        (ROOT / "app" / "static" / "learn-bridge.js").read_bytes(),
                        "text/javascript; charset=utf-8",
                    )
                if path == "/preview-meta":
                    site.meta_reads += 1
                    return self._send(json.dumps({
                        "version": site.version,
                        "exists": True,
                        "bridge": True,
                        "preview_url": "/child.html?doc=" + site.version,
                        "sandbox": "allow-scripts",
                        "bridge_page": BRIDGE_PAGE,
                    }).encode("utf-8"), "application/json")
                if path == "/captured":
                    site.captured.append(parse_qs(urlparse(self.path).query))
                    return self._send(b"captured", "text/plain")
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                path = urlparse(self.path).path
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if path == f"/artifacts/{BLOCK_ID}/file":
                    site.saves.append(body)
                    return self._send(json.dumps({
                        "ok": True, "result": "saved", "file_rev": RUN_FILE_REV,
                    }).encode("utf-8"), "application/json")
                if path == f"/runs/{BLOCK_ID}/runs":
                    site.run_starts.append(body)
                    return self._send(json.dumps({
                        "ok": True, "job_id": RUN_JOB_ID,
                    }).encode("utf-8"), "application/json")
                self.send_error(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class _Browser:
    """A throwaway headless Chrome, spoken to over CDP.

    Dialogs are the point: `Page.enable` makes Chrome PAUSE on
    `window.confirm` and wait for an explicit answer, which is what lets a test
    play the owner. `answers` is consumed one dialog at a time; anything beyond
    it is dismissed (a refusal), so an unexpected extra prompt can never pass
    as a grant.
    """

    def __init__(self, answers: list[bool]) -> None:
        self.profile = tempfile.mkdtemp(prefix="invented-chrome-")
        self.answers = list(answers)
        self.dialogs: list[str] = []
        self._next_id = 0
        self.process = subprocess.Popen(
            [
                CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
                "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--no-proxy-server",
                "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        port = self._devtools_port()
        target = self._page_target(port)
        # No proxy on loopback, and none of the ambient proxy environment:
        # this connection must reach the browser we just started.
        from websockets.sync.client import connect

        self.ws = connect(target, proxy=None, max_size=8 * 1024 * 1024)
        self.call("Page.enable")
        self.call("Runtime.enable")

    def _devtools_port(self) -> int:
        marker = Path(self.profile) / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("the browser exited before it opened a port")
            if marker.exists():
                lines = marker.read_text(encoding="utf-8").splitlines()
                if lines and lines[0].strip().isdigit():
                    return int(lines[0].strip())
            time.sleep(0.05)
        raise TimeoutError("the browser never published its debugging port")

    @staticmethod
    def _page_target(port: int) -> str:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
                    targets = json.loads(r.read().decode("utf-8"))
            except (OSError, ValueError):
                time.sleep(0.1)
                continue
            for target in targets:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return target["webSocketDebuggerUrl"]
            time.sleep(0.1)
        raise TimeoutError("the browser never offered a page target")

    def call(self, method: str, timeout: float = 20.0, **params) -> dict:
        self._next_id += 1
        message_id = self._next_id
        self.ws.send(json.dumps(
            {"id": message_id, "method": method, "params": params}))
        deadline = time.monotonic() + timeout
        while True:
            message = self._read(deadline, method)
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    def pump(self, seconds: float) -> None:
        """Let the page run, answering dialogs as they open."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                self._read(deadline, "pump")
            except TimeoutError:
                return

    def _read(self, deadline: float, what: str) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(what)
        raw = self.ws.recv(timeout=remaining)
        message = json.loads(raw)
        if message.get("method") == "Page.javascriptDialogOpening":
            self.dialogs.append(message["params"].get("message", ""))
            accept = self.answers.pop(0) if self.answers else False
            self._next_id += 1
            self.ws.send(json.dumps({
                "id": self._next_id,
                "method": "Page.handleJavaScriptDialog",
                "params": {"accept": accept},
            }))
        return message

    def evaluate(self, expression: str):
        result = self.call(
            "Runtime.evaluate", expression=expression,
            returnByValue=True, awaitPromise=False,
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(str(result["exceptionDetails"]))
        return result["result"].get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        finally:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                self.process.kill()
            shutil.rmtree(self.profile, ignore_errors=True)


def _run(answers: list[bool], *, egress: bool = False, settle: float = 3.0,
         child_query: str = "doc=1"):
    """Load the invented lesson page once and return (browser, site)."""
    site = _Site(egress, child_query)
    browser = _Browser(answers)
    try:
        browser.call("Page.navigate", url=f"{site.base}/parent.html")
        browser.pump(settle)
        # A hidden page would silence the reload poll and make every timing
        # assertion below meaningless. Fail loudly instead of passing quietly.
        assert browser.evaluate("document.hidden") is False
        return browser, site
    except BaseException:
        browser.close()
        site.close()
        raise


def _welcomes(browser: _Browser) -> list[dict]:
    return browser.evaluate("window.__probe.welcomes") or []


def _successors(browser: _Browser) -> list[dict]:
    return browser.evaluate("window.__probe.successors") or []


def test_a_refused_read_back_sends_no_answer_bytes_into_the_page():
    """The owner says no: the page completes its handshake and never sees a
    byte of the learner's answer or the tutor's note."""
    browser, site = _run([False], egress=True)
    try:
        welcomes = _welcomes(browser)
        assert len(welcomes) >= 1
        assert len(browser.dialogs) == 1
        assert "navigate" in browser.dialogs[0]

        for welcome in welcomes:
            assert welcome["answer"] is None
            assert ANSWER not in welcome["raw"]
            assert NOTE not in welcome["raw"]
            assert QUESTION_ID not in welcome["raw"]
            # The rest of the grant is intact — refusing to read back does not
            # take away the capability that writes the next answer.
            assert '"attempts"' in welcome["raw"]
        # Nothing could be carried out of the frame, because nothing arrived.
        assert site.captured == []
    finally:
        browser.close()
        site.close()


def test_an_allowed_read_back_is_what_the_page_can_carry_away():
    """The other half of the same fact, and the reason the prompt exists: once
    the owner allows it, the page really can put those bytes on an outbound
    request that no response CSP covers."""
    browser, site = _run([True], egress=True)
    try:
        welcomes = _welcomes(browser)
        # At least one: the self-navigation below makes the parent re-assert
        # the expected page, and that successor document asks for itself.
        assert browser.dialogs and "navigate" in browser.dialogs[0]
        assert any(welcome["answer"] == ANSWER for welcome in welcomes)

        deadline = time.monotonic() + 10
        while not site.captured and time.monotonic() < deadline:
            browser.pump(0.5)
        # Not a count: the page navigates itself away, the parent re-asserts
        # the expected page, and every document of that chain inherits the
        # lesson-scoped grant and repeats the egress. What is asserted is what
        # the prompt is about — the bytes that leave are the learner's answer
        # and the tutor's note.
        assert site.captured
        assert {query["value"][0] for query in site.captured} == {ANSWER}
        assert {query["note"][0] for query in site.captured} == {NOTE}
    finally:
        browser.close()
        site.close()


def _reload_the_frame(browser: _Browser, site: _Site) -> list[dict]:
    """Reload the frame the way the app itself does — a new version on the
    metadata poll — and return the welcomes once a second document has landed.
    """
    site.version = "v2"
    deadline = time.monotonic() + 15
    while len(_welcomes(browser)) < 2 and time.monotonic() < deadline:
        browser.pump(0.5)
    welcomes = _welcomes(browser)
    assert len(welcomes) >= 2, "the frame never loaded a second document"
    return welcomes


def test_a_grant_covers_the_next_page_of_the_same_lesson():
    """The decision's unit is the lesson, not the document.

    A bundle is many pages and the owner walks through them; asking again on
    each one taught the click, not the question. So the successor document of
    the same lesson inherits the answer and opens no second modal. Only ONE
    answer is queued, so a second prompt would fail this test loudly rather
    than silently reuse a stale one.
    """
    browser, site = _run([True], egress=False, settle=3.0)
    try:
        first = _welcomes(browser)
        assert first and first[0]["answer"] == ANSWER
        assert len(browser.dialogs) == 1

        welcomes = _reload_the_frame(browser, site)
        assert len(browser.dialogs) == 1, "the successor asked a second time"
        assert welcomes[-1]["answer"] == ANSWER
    finally:
        browser.close()
        site.close()


def test_a_grant_survives_the_page_tab_navigation_it_exists_for():
    """The navigation the owner actually performs, and the one that made the
    per-document rule feel like a per-page interrogation.

    A lesson's page tabs are ordinary `/learn?...&entry=…` links, so stepping
    through a bundle reloads the whole parent document — module memory would
    not survive it. This drives the same thing: the parent page is navigated
    again, in the same browser tab, and the decision has to still be there.
    """
    browser, site = _run([True], egress=False, settle=3.0)
    try:
        assert len(browser.dialogs) == 1
        assert _welcomes(browser)[0]["answer"] == ANSWER

        browser.call("Page.navigate", url=f"{site.base}/parent.html")
        browser.pump(3.0)
        welcomes = _welcomes(browser)
        assert welcomes, "the reloaded parent never granted a bridge"
        assert len(browser.dialogs) == 1, "the reloaded parent asked again"
        assert welcomes[-1]["answer"] == ANSWER
    finally:
        browser.close()
        site.close()


def test_a_refusal_covers_the_next_page_of_the_same_lesson():
    """The other direction of the same rule, and the one that must not slip: a
    refusal is not re-litigated by the next document either, so a bundle cannot
    reload its way to a modal loop until the owner clicks the wrong button."""
    browser, site = _run([False], egress=False, settle=3.0)
    try:
        assert len(browser.dialogs) == 1
        first = _welcomes(browser)
        assert first and first[0]["answer"] is None

        welcomes = _reload_the_frame(browser, site)
        assert len(browser.dialogs) == 1, "the successor asked a second time"
        last = welcomes[-1]
        assert last["answer"] is None
        assert ANSWER not in last["raw"] and NOTE not in last["raw"]
    finally:
        browser.close()
        site.close()


# --- ABI v2: the welcome travels on the announcer's own port -----------------


def test_a_delayed_load_successor_receives_neither_record_nor_port():
    """The document the owner was asked about is the only one that can collect
    the answer.

    The formula this repair exists for. Page A commits a same-frame navigation
    and announces in the same turn; its successor B runs script at once but
    holds its own `load` event open behind a four-second image, so every signal
    the parent has — `contentWindow`, the armed identity, the document
    generation — still says "A". Under the retired `WindowProxy` delivery, the
    welcome the owner approved for A landed in B. It cannot now: the result
    goes out on the `MessagePort` A transferred, and a port does not survive
    the navigation that destroyed A.
    """
    browser, site = _run(
        [True], egress=True, settle=6.0,
        child_query="doc=1&navigate=/successor.html",
    )
    try:
        # The run is only meaningful if A's announcement really was answered
        # far enough to ask the owner. A mis-timed run fails here, not silently.
        assert len(browser.dialogs) >= 1
        assert "navigate" in browser.dialogs[0]

        # Give the parent well past its 250 ms settle, and B well short of its
        # own load, for anything to arrive.
        browser.pump(2.5)

        successors = _successors(browser)
        for message in successors:
            assert message["answer"] is None
            assert ANSWER not in message["raw"]
            assert NOTE not in message["raw"]
            assert QUESTION_ID not in message["raw"]
            assert message["ports"] == 0
        # Not one byte, and not one port: B never became a recipient at all.
        assert successors == []
        assert site.captured == []
    finally:
        browser.close()
        site.close()


def test_the_transferred_port_is_where_the_whole_welcome_arrives():
    """The other half: the ordinary path still works, end to end.

    A page that announces with its own channel receives the complete welcome
    on it — capabilities, the approved `record`, and the transferred bridge
    port — and that bridge port really carries the port protocol.
    """
    browser, site = _run([True], egress=False)
    try:
        welcomes = _welcomes(browser)
        assert len(welcomes) == 1
        welcome = welcomes[0]
        # Delivered on the announcer's channel, never on the window.
        assert welcome["via"] == "port"
        assert welcome["ports"] == 1
        assert welcome["answer"] == ANSWER
        assert '"attempts"' in welcome["raw"] and '"abi":2' in welcome["raw"]

        deadline = time.monotonic() + 10
        pongs = browser.evaluate("window.__probe.pongs") or []
        while not pongs and time.monotonic() < deadline:
            browser.pump(0.5)
            pongs = browser.evaluate("window.__probe.pongs") or []
        assert pongs, "the transferred bridge port answers the port protocol"
        assert '"op":"pong"' in pongs[0]["raw"]
        assert '"abi":2' in pongs[0]["raw"]
    finally:
        browser.close()
        site.close()


def test_an_announcement_without_a_port_gets_neither_port_nor_record():
    """The retired v1 contract is answered with silence, not a fallback.

    A page that announces the old way — no transferred port, listening on the
    window — is indistinguishable from an ineligible page: no welcome, no
    reject, and above all no consent prompt, because the runtime drops the
    announcement before it ever reaches the snapshot.
    """
    browser, site = _run([True], egress=True, child_query="doc=1&legacy=1")
    try:
        assert _welcomes(browser) == []
        assert browser.dialogs == [], "silence is decided before the owner is asked"
        assert site.captured == []
    finally:
        browser.close()
        site.close()


def test_the_run_idempotency_key_is_the_sha256_of_the_bound_tuple():
    """A run's idempotency key is the standard SHA-256 of the JSON tuple the
    bridge binds it to, taken end to end: the page asks for a run, the bridge
    saves and starts it, and the start request carries the key."""
    browser, site = _run([False], child_query="doc=1&run=1")
    try:
        deadline = time.monotonic() + 10
        while not site.run_starts and time.monotonic() < deadline:
            browser.pump(0.5)
        assert site.saves == [{"content": RUN_CONTENT, "base_rev": "absent"}]
        bound = json.dumps(
            ["ephemeris:lesson-run:v1", RUN_REQUEST_ID, BLOCK_ID, RUN_CONTENT],
            separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        assert site.run_starts == [{
            "file_rev": RUN_FILE_REV,
            "idempotency_key": hashlib.sha256(bound).hexdigest(),
        }]
    finally:
        browser.close()
        site.close()
