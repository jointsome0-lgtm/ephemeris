"""Load a built lesson page the way a learner loads it, and report what broke.

The acceptance half of #161. Once the agent may install any package it likes,
the first person to find out that a page throws on load must not be the
learner — so a lesson is not "built" until a real browser has rendered it with
an empty console.

Why a real browser, and not a parse or a lint: the failures that matter here
are conditions of the *environment*, not of the source.

- **The opaque origin.** A lesson response carries `sandbox allow-scripts` as
  the first directive of its CSP, so the document has no origin of its own
  (`window.origin === "null"`) whether it is framed by the learn UI or opened
  on its own. On an opaque origin an external **module** script, an import map,
  a dynamic `import()` and a web font are all fetched in cors mode and refused,
  while a classic `<script src>` is not. That asymmetry is the entire reason
  the build emits one IIFE, and only a browser can tell you it has regressed.
- **The CSP itself.** `interactive-local-v1` is served on the response, not
  written into the document, so it applies only to a real fetch of the real
  route.

Because the `sandbox` directive travels in the header, the checker navigates
straight to the page — no wrapper document, no iframe, no extra route to
expose. It then refuses to report on a page that did not come back opaque,
since a check run under the wrong conditions is worse than no check.

Three channels are listened to, because they carry different failures:
uncaught exceptions, the console API, and the browser's own log — the last of
which is where a CSP refusal and a blocked subresource show up, and neither of
those ever reaches the console.

Everything here is throwaway: a headless browser on an ephemeral debugging
port with a disposable profile, torn down in a finally.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from .. import sandbox

_log = logging.getLogger(__name__)

CHROME_NAMES = ("google-chrome", "chromium", "chromium-browser", "chrome")
CHROME_ENV = "EPHEMERIS_RENDER_CHECK_CHROME"

# How long to keep listening after the page stops loading. Lesson pages draw on
# load — a mermaid render or a d3 join throws well after `load` fires — so the
# check has to sit still for a moment rather than sample once.
SETTLE_SECONDS = 2.5
# Whole-check ceiling, browser start included. The measured build steps are
# under a second; this is generous on purpose, because the failure it guards
# against — a page that never stops loading — must end as a refusal, not a hang.
TIMEOUT_SECONDS = 60.0
# One error message is a sentence, not a document: a page failing in a loop
# must not turn the report into a megabyte.
MAX_ERRORS = 25
MAX_MESSAGE_CHARS = 500
# How many diagnostic notifications are kept. A page erroring in a loop emits
# them as fast as the socket carries them, and for up to `TIMEOUT_SECONDS` —
# keeping all of them to report at most `MAX_ERRORS` would let a broken lesson
# spend the app's memory instead of getting a refusal. Well above what any
# honest page produces, and duplicates collapse in `_errors_from` anyway.
MAX_EVENTS = 500
# The same bound for the two URL sets, which a page could otherwise grow with
# thousands of distinct requests.
MAX_URLS = 2000

# Chrome asks for this on every top-level navigation and the app has none to
# give, so the 404 is the browser talking about itself, not about the lesson.
_NOISE_URL_SUFFIXES = ("/favicon.ico",)

# The one thing the browser reports that is a fact about the RESPONSE rather
# than about the page, and that therefore appears identically under every
# lesson: `webrtc 'block'` is in `interactive-local-v1` on purpose, and Chrome
# has not shipped the directive, so it complains once per load. Filtering the
# complaint is right; dropping the directive to silence it would weaken the
# policy for the browsers that do honour it.
#
# The whole message, not its prefix. `webrtc` is the only directive in
# `interactive-local-v1` that Chrome does not recognise, so a second
# "Unrecognized Content-Security-Policy directive" can only be about a policy
# the PAGE wrote — a misspelled `script-src` in a `<meta>` tag is a real defect
# and has to be reported, which a prefix match would have swallowed.
_NOISE_TEXTS = frozenset({"Unrecognized Content-Security-Policy directive 'webrtc'."})

def _is_diagnostic(method: str | None, params: dict) -> bool:
    """Whether `_errors_from` could turn this notification into an error.

    The same predicate at both ends on purpose. Whatever `_record` keeps is
    what the cap counts, and anything the cap counts and `_errors_from` then
    discards is reported as an error that was never collected — so a page with
    six hundred `console.log` calls, or a hundred images behind their
    `Network.*` traffic, would be refused for rendering correctly.
    """
    if method == "Runtime.exceptionThrown":
        return True
    if method == "Runtime.consoleAPICalled":
        return params.get("type") in ("error", "assert")
    if method == "Log.entryAdded":
        return (params.get("entry") or {}).get("level") == "error"
    return False


class RenderCheckUnavailable(RuntimeError):
    """The page could not be checked — which never counts as a clean page."""


@dataclass(frozen=True)
class RenderError:
    source: str
    text: str

    def as_dict(self) -> dict:
        return {"source": self.source, "text": self.text}


def chrome_binary() -> str | None:
    override = os.environ.get(CHROME_ENV)
    if override:
        return override if os.access(override, os.X_OK) else None
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def _truncate(text: str) -> str:
    text = " ".join(str(text).split())
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return text[: MAX_MESSAGE_CHARS - 1] + "…"


class _Browser:
    """A throwaway headless browser, spoken to over CDP."""

    def __init__(self, binary: str, deadline: float) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError as exc:  # pragma: no cover - uvicorn[standard] ships it
            raise RenderCheckUnavailable(f"no websocket client: {exc}") from exc
        self.profile = tempfile.mkdtemp(prefix="ephemeris-render-check-")
        self._next_id = 0
        # Every channel is bounded, because all four are driven by the page.
        # The signals the gate needs are kept as they arrive rather than
        # rediscovered from a transcript, so nothing the gate depends on can be
        # displaced by a flood of the channel that has a cap.
        self.events: list[dict] = []
        self.dropped = 0
        self.fetched: set[str] = set()
        self.executed: set[str] = set()
        self.loaded = False
        self._main_frame: str | None = None
        try:
            self.process = subprocess.Popen(
                [
                    # The page about to be loaded is the artifact this build
                    # just produced. A script retaining large buffers in a loop
                    # would otherwise exhaust the host well inside the CDP
                    # deadline, and take the one worker serving this app with
                    # it — so the browser and every renderer it forks share one
                    # bounded cgroup, torn down as a group.
                    *sandbox.render_scope_prefix(int(TIMEOUT_SECONDS)),
                    binary, "--headless=new", "--disable-gpu", "--no-sandbox",
                    "--no-first-run", "--no-default-browser-check",
                    "--disable-extensions", "--no-proxy-server",
                    "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
                    "about:blank",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            shutil.rmtree(self.profile, ignore_errors=True)
            raise RenderCheckUnavailable(f"browser did not start: {exc}") from exc
        try:
            port = self._devtools_port(deadline)
            target = self._page_target(port, deadline)
            # No ambient proxy on the way to a browser this process just
            # started on loopback; this host reaches the internet through one.
            self.ws = connect(target, proxy=None, max_size=8 * 1024 * 1024)
            self.call("Page.enable", deadline)
            self.call("Runtime.enable", deadline)
            self.call("Log.enable", deadline)
            # Neither of these is for errors. `Debugger.scriptParsed` is the
            # evidence that the artifact was compiled and run, and the network
            # log is what tells a failed build which of the two ways it failed.
            # See `console_errors`.
            self.call("Network.enable", deadline)
            self.call("Debugger.enable", deadline)
        except BaseException:
            self.close()
            raise

    def _devtools_port(self, deadline: float) -> int:
        marker = os.path.join(self.profile, "DevToolsActivePort")
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RenderCheckUnavailable("the browser exited before it opened a port")
            try:
                with open(marker, encoding="utf-8") as handle:
                    first = handle.readline().strip()
            except OSError:
                first = ""
            if first.isdigit():
                return int(first)
            time.sleep(0.05)
        raise RenderCheckUnavailable("the browser never published its debugging port")

    @staticmethod
    def _page_target(port: int, deadline: float) -> str:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline:
            try:
                with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
                    targets = json.loads(response.read().decode("utf-8"))
            except (OSError, ValueError):
                time.sleep(0.1)
                continue
            for target in targets:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return target["webSocketDebuggerUrl"]
            time.sleep(0.1)
        raise RenderCheckUnavailable("the browser never offered a page target")

    def call(self, method: str, deadline: float, **params) -> dict:
        self._next_id += 1
        message_id = self._next_id
        try:
            self.ws.send(json.dumps({"id": message_id, "method": method, "params": params}))
        except Exception as exc:  # pragma: no cover - browser died mid-check
            raise RenderCheckUnavailable(f"{method}: {exc}") from exc
        while True:
            message = self._read(deadline)
            if message.get("id") == message_id:
                if "error" in message:
                    raise RenderCheckUnavailable(f"{method}: {message['error']}")
                return message.get("result", {})

    def _read(self, deadline: float) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("render check ran out of time")
        try:
            raw = self.ws.recv(timeout=remaining)
        except TimeoutError:
            raise
        except Exception as exc:  # pragma: no cover - browser died mid-check
            raise RenderCheckUnavailable(f"the browser stopped answering: {exc}") from exc
        message = json.loads(raw)
        if "method" in message:
            self._record(message)
        return message

    def _record(self, event: dict) -> None:
        """Route one notification to the channel that will be asked for it."""
        method = event.get("method")
        params = event.get("params") or {}
        if method == "Page.loadEventFired":
            self.loaded = True
            return
        if method == "Page.frameStoppedLoading":
            if self._main_frame in (None, params.get("frameId")):
                self.loaded = True
            return
        if method == "Network.responseReceived":
            response = params.get("response") or {}
            status = response.get("status")
            url = response.get("url")
            if url and not (isinstance(status, int) and status >= 400):
                if len(self.fetched) < MAX_URLS:
                    self.fetched.add(_url_key(url))
            return
        if method == "Debugger.scriptParsed":
            url = params.get("url")
            if url and len(self.executed) < MAX_URLS:
                self.executed.add(_url_key(url))
            return
        # Only what `_errors_from` will actually read. Everything else is
        # dropped without being counted, so the cap measures how badly a page
        # is failing rather than how much it has to say.
        if not _is_diagnostic(method, params):
            return
        if len(self.events) < MAX_EVENTS:
            self.events.append(event)
        else:
            self.dropped += 1

    def begin_navigation(self, frame_id: str | None) -> None:
        """Forget the document the browser was showing before this one.

        The browser starts on `about:blank`, whose `Page.frameStoppedLoading`
        arrives after `Page.enable` and lands in the same frame the lesson then
        navigates into. Without this reset, the wait for "the page has
        finished" is satisfied by the blank page it replaced, and the settle
        window opens on a document that has not fetched anything yet.
        """
        self._main_frame = frame_id
        self.loaded = False

    def wait_for_load(self, deadline: float) -> None:
        """Block until the document has finished loading.

        `Page.navigate` returns as soon as the navigation has been *committed*,
        which on anything but a trivial page is well before the document is
        done. Settling from that moment would judge a page that is still
        fetching: a stylesheet that takes three seconds pushes the script after
        it past the settle window, and the check would call a page clean before
        the code on it had run. So the settle interval starts here, after the
        load, and this wait gets the whole remaining budget.
        """
        while not self.loaded and time.monotonic() < deadline:
            try:
                self._read(deadline)
            except TimeoutError:
                break
        if not self.loaded:
            raise RenderCheckUnavailable(
                "the page never finished loading inside the check's time budget"
            )

    def pump(self, until: float, deadline: float) -> None:
        """Collect events until `until`, never past the whole-check `deadline`."""
        while True:
            stop = min(until, deadline)
            if time.monotonic() >= stop:
                return
            try:
                self._read(stop)
            except TimeoutError:
                return

    def close(self) -> None:
        websocket = getattr(self, "ws", None)
        if websocket is not None:
            try:
                websocket.close()
            except Exception:  # pragma: no cover - teardown must not mask a result
                pass
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            self.process.kill()
            self.process.wait(timeout=10)
        shutil.rmtree(self.profile, ignore_errors=True)


def _errors_from(events: list[dict]) -> list[RenderError]:
    found: list[RenderError] = []
    seen: set[tuple[str, str]] = set()

    def add(source: str, text: str) -> None:
        text = _truncate(text)
        if not text or (source, text) in seen:
            return
        seen.add((source, text))
        found.append(RenderError(source, text))

    for event in events:
        method = event.get("method")
        params = event.get("params") or {}
        # One predicate, shared with `_record`, so the set of events kept can
        # never drift from the set of events read.
        if not _is_diagnostic(method, params):
            continue
        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            exception = details.get("exception") or {}
            add("exception", exception.get("description") or details.get("text") or "")
        elif method == "Runtime.consoleAPICalled":
            parts = []
            for argument in params.get("args") or []:
                if "value" in argument:
                    parts.append(str(argument["value"]))
                else:
                    parts.append(str(argument.get("description") or argument.get("type") or ""))
            add("console", " ".join(part for part in parts if part))
        elif method == "Log.entryAdded":
            entry = params.get("entry") or {}
            url = entry.get("url") or ""
            text = entry.get("text") or ""
            if any(url.endswith(suffix) for suffix in _NOISE_URL_SUFFIXES):
                continue
            if " ".join(text.split()) in _NOISE_TEXTS:
                continue
            # This channel is where a CSP refusal and a blocked subresource
            # land; neither reaches the console API, and both are exactly the
            # opaque-origin failures the IIFE build exists to prevent.
            add("browser", f"{text} ({url})" if url and url not in text else text)
    return found[:MAX_ERRORS]


def _url_key(url: str) -> str:
    """A URL reduced to what identifies a lesson file, for comparison.

    Chrome reports the URL it resolved from the page's own relative reference,
    which need not be byte-identical to the one this app composed: percent
    encoding and a query string are both free to differ. Scheme, authority and
    the decoded path are what actually name the file.
    """
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{unquote(parts.path)}"


def console_errors(
    url: str,
    *,
    expect_url: str | None = None,
    settle: float = SETTLE_SECONDS,
    timeout: float = TIMEOUT_SECONDS,
    binary: str | None = None,
    require_opaque_origin: bool = True,
) -> list[dict]:
    """Render `url` and return the errors it produced; empty means it passed.

    `expect_url` is a file the page is required to have fetched — the built
    artifact. Without it the gate is satisfied by any page that happens to load
    cleanly, including one that does not reference the artifact at all, and a
    source that throws on its first line would be accepted and reach the
    learner the moment somebody added the `<script>` tag.

    Anything that stops the check from happening — no browser, a browser that
    will not talk, a page that never finishes loading, a response that did not
    come back on an opaque origin — raises :class:`RenderCheckUnavailable`,
    because "could not check" must never read the same as "checked, and it was
    clean".
    """
    binary = binary or chrome_binary()
    if binary is None:
        raise RenderCheckUnavailable(
            "no headless browser on PATH; a built page cannot be accepted unchecked"
        )
    deadline = time.monotonic() + timeout
    browser = _Browser(binary, deadline)
    try:
        result = browser.call("Page.navigate", deadline, url=url)
        if result.get("errorText"):
            raise RenderCheckUnavailable(
                f"the browser could not load the page: {result['errorText']}"
            )
        browser.begin_navigation(result.get("frameId"))
        browser.wait_for_load(deadline)
        # Then sit still: lesson pages draw on load, and a mermaid render or a
        # d3 join throws well after the document is done.
        browser.pump(time.monotonic() + settle, deadline)
        if require_opaque_origin:
            origin = browser.call(
                "Runtime.evaluate", deadline,
                expression="window.origin", returnByValue=True,
            ).get("result", {}).get("value")
            if origin != "null":
                raise RenderCheckUnavailable(
                    f"the page came back on origin {origin!r}, not an opaque one: "
                    "its response is missing the sandbox CSP a learner would get, "
                    "so this run would not see an opaque-origin failure"
                )
        errors = _errors_from(browser.events)
        if browser.dropped:
            errors.append(RenderError(
                "browser",
                f"{browser.dropped} further error(s) were not collected; this "
                "page fails faster than it can be reported on",
            ))
        if expect_url is not None:
            wanted = _url_key(expect_url)
            if wanted not in browser.executed:
                fetched = wanted in browser.fetched
                errors.insert(0, RenderError("artifact", (
                    f"the page fetched {expect_url} but never ran it, so "
                    "nothing here is evidence about the built script; a "
                    "preload is not a load — reference it with a classic "
                    '<script src="…"></script> tag'
                ) if fetched else (
                    f"the page never loaded {expect_url}, so nothing here is "
                    "evidence about the built script; reference it from the "
                    'page with a classic <script src="…"></script> tag'
                )))
        return [error.as_dict() for error in errors[:MAX_ERRORS]]
    except TimeoutError as exc:
        # `_read` raises this when the whole-check budget runs out. It is a
        # "could not check", and every one of those leaves here by one door.
        raise RenderCheckUnavailable(f"the render check ran out of time: {exc}") from exc
    finally:
        browser.close()
