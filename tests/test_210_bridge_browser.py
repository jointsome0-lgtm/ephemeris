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
    "blocks": [],
}

PARENT_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>invented learn page</title></head>
<body>
<iframe id="lesson-preview-frame" src="/child.html?doc=1&amp;egress=EGRESS"
        sandbox="allow-scripts"
        data-meta-url="/preview-meta"
        data-attempts-url="/attempts"
        data-src="/child.html"
        data-version="v1"
        data-loaded="0"
        data-record='RECORD'></iframe>
<script>
  /* The same load counter learn.html keeps, and a mailbox for what the child
   * says it received. The probe never reads the welcome itself — it only
   * repeats what crossed the boundary. */
  window.__probe = { welcomes: [], loads: 0 };
  var frame = document.getElementById("lesson-preview-frame");
  frame.addEventListener("load", function () {
    window.__probe.loads += 1;
    frame.dataset.loaded = String(window.__probe.loads);
  });
  window.addEventListener("message", function (event) {
    if (event.data && event.data.probe === "welcome") {
      window.__probe.welcomes.push(event.data);
    }
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
  function announce() {
    parent.postMessage(
      { ephemeris: "lesson-bridge", type: "ready", abi: [1], want: ["attempts"] },
      "*",
    );
  }
  window.addEventListener("message", function (event) {
    var data = event.data;
    if (!data || data.ephemeris !== "lesson-bridge" || data.type !== "welcome") return;
    /* Report the WHOLE welcome, so the test can assert on bytes rather than
     * on this page's reading of them. */
    parent.postMessage({
      probe: "welcome",
      doc: params.get("doc"),
      raw: JSON.stringify(data),
      answer: data.record && data.record.questions.length
        ? data.record.questions[0].answer : null,
    }, "*");
    /* The residual the gate is about: permitted script navigates the frame
     * and the private text rides along in the URL. */
    if (params.get("egress") === "1" && data.record
        && data.record.questions.length) {
      location.href = "/captured?value="
        + encodeURIComponent(data.record.questions[0].answer)
        + "&note=" + encodeURIComponent(
          data.record.questions[0].verdict.note);
    }
  });
  announce();
  /* The documented retry: the parent module can initialise after this load. */
  var retry = setInterval(function () {
    announced += 1;
    if (announced > 8) return clearInterval(retry);
    announce();
  }, 150);
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

    def __init__(self, egress: bool) -> None:
        self.captured: list[dict] = []
        self.meta_reads = 0
        self.version = "v1"
        site = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # noqa: A003 - silence the test server
                pass

            def _send(self, body: bytes, content_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                path = urlparse(self.path).path
                if path == "/parent.html":
                    body = (
                        PARENT_HTML
                        .replace("RECORD", json.dumps(SNAPSHOT).replace('"', "&quot;"))
                        .replace("EGRESS", "1" if egress else "0")
                    )
                    return self._send(body.encode("utf-8"), "text/html; charset=utf-8")
                if path == "/child.html":
                    return self._send(
                        CHILD_HTML.encode("utf-8"), "text/html; charset=utf-8")
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


def _run(answers: list[bool], *, egress: bool = False, settle: float = 3.0):
    """Load the invented lesson page once and return (browser, site)."""
    site = _Site(egress)
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
        assert [query["value"][0] for query in site.captured] == [ANSWER]
        assert site.captured[0]["note"] == [NOTE]
    finally:
        browser.close()
        site.close()


def test_consent_is_scoped_to_the_document_that_asked():
    """A grant covers the document it was given for and nothing after it.

    The frame is reloaded the way the app itself reloads it — a new version on
    the metadata poll — and the successor document has to ask again. It is
    refused this time, so the same lesson gets the answers on one load and
    none on the next.
    """
    browser, site = _run([True, False], egress=False, settle=3.0)
    try:
        first = _welcomes(browser)
        assert first and first[0]["answer"] == ANSWER
        assert len(browser.dialogs) == 1

        site.version = "v2"  # the parent-owned reload, through its own poll
        deadline = time.monotonic() + 15
        while len(_welcomes(browser)) < 2 and time.monotonic() < deadline:
            browser.pump(0.5)

        welcomes = _welcomes(browser)
        assert len(welcomes) >= 2, "the frame never loaded a second document"
        assert len(browser.dialogs) == 2, "the successor document asked again"
        second = welcomes[-1]
        assert second["answer"] is None
        assert ANSWER not in second["raw"] and NOTE not in second["raw"]
    finally:
        browser.close()
        site.close()
