"""Templating + redirect helpers shared by app/main.py and the routers.

The Jinja environment has one owner so every surface renders with the same
globals: main.py registers them (`templates.env.globals.update(...)`) and the
routers import the same object. `_with_flash` / `_safe_return` live here for the
same reason — the redirect tails of the write contract (sec16.4) are used by
routes on both sides of the split (#24).
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _with_flash(url: str, flash: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}flash={quote(flash)}"


def _safe_return(to: str | None, default: str = "/today") -> str:
    """A same-origin path to redirect back to after a task write (no open redirects)."""
    if to and to.startswith("/") and not to.startswith("//"):
        return to
    return default
