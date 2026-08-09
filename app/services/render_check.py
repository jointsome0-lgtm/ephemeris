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

# Chrome asks for this on every top-level navigation and the app has none to
# give, so the 404 is the browser talking about itself, not about the lesson.
_NOISE_URL_SUFFIXES = ("/favicon.ico",)

# Two things the browser reports that are facts about the RESPONSE, not about
# the page, and that therefore appear identically under every lesson:
#
# - `webrtc 'block'` is in `interactive-local-v1` on purpose, and Chrome does
#   not implement the directive, so it says so once per load. Filtering the
#   complaint is right; dropping the directive to silence it would weaken the
#   policy for the browsers that do honour it.
# - A missing favicon, above.
#
# Matched on a prefix rather than a substring: a lesson that logs the words
# itself still gets reported.
_NOISE_TEXT_PREFIXES = ("Unrecognized Content-Security-Policy directive",)


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
        self.events: list[dict] = []
        try:
            self.process = subprocess.Popen(
                [
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
            self.events.append(message)
        return message

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
        if method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            exception = details.get("exception") or {}
            add("exception", exception.get("description") or details.get("text") or "")
        elif method == "Runtime.consoleAPICalled":
            if params.get("type") not in ("error", "assert"):
                continue
            parts = []
            for argument in params.get("args") or []:
                if "value" in argument:
                    parts.append(str(argument["value"]))
                else:
                    parts.append(str(argument.get("description") or argument.get("type") or ""))
            add("console", " ".join(part for part in parts if part))
        elif method == "Log.entryAdded":
            entry = params.get("entry") or {}
            if entry.get("level") != "error":
                continue
            url = entry.get("url") or ""
            text = entry.get("text") or ""
            if any(url.endswith(suffix) for suffix in _NOISE_URL_SUFFIXES):
                continue
            if text.startswith(_NOISE_TEXT_PREFIXES):
                continue
            # This channel is where a CSP refusal and a blocked subresource
            # land; neither reaches the console API, and both are exactly the
            # opaque-origin failures the IIFE build exists to prevent.
            add("browser", f"{text} ({url})" if url and url not in text else text)
    return found[:MAX_ERRORS]


def console_errors(
    url: str,
    *,
    settle: float = SETTLE_SECONDS,
    timeout: float = TIMEOUT_SECONDS,
    binary: str | None = None,
    require_opaque_origin: bool = True,
) -> list[dict]:
    """Render `url` and return the errors it produced; empty means it passed.

    Anything that stops the check from happening — no browser, a browser that
    will not talk, a response that did not come back on an opaque origin —
    raises :class:`RenderCheckUnavailable`, because "could not check" must
    never read the same as "checked, and it was clean".
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
        # Sit still for `settle` past the last frame that finished loading, so
        # a page whose subresources arrive late is judged on its finished self.
        quiet_until = time.monotonic() + settle
        while time.monotonic() < min(quiet_until, deadline):
            before = len(browser.events)
            browser.pump(quiet_until, deadline)
            if any(event.get("method") == "Page.frameStoppedLoading"
                   for event in browser.events[before:]):
                quiet_until = time.monotonic() + settle
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
        return [error.as_dict() for error in _errors_from(browser.events)]
    finally:
        browser.close()
