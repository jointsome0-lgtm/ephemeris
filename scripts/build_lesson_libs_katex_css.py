"""Derive the shelf's `katex.min.css` with its web fonts inlined as data URLs.

Lesson pages are served with `sandbox allow-scripts` (app/routers/learn.py,
`interactive-local-v1`), which puts the document on an opaque origin. Font
fetches are CORS requests, so a page on a null origin cannot load
`fonts/KaTeX_Main-Regular.woff2` from the app — the request is blocked and
KaTeX silently falls back to system glyphs, which is exactly the failure the
shelf exists to prevent. `font-src 'self' data:` allows data URLs, and those
are not CORS-gated, so the fonts travel inside the stylesheet.

Run this at a KaTeX bump, from the extracted npm `dist/` directory:

    uv run python scripts/build_lesson_libs_katex_css.py \
        <extracted>/dist vendor/lesson-libs/katex/<version>/katex.min.css

then regenerate `vendor/lesson-libs/SHASUMS256` (see that directory's README).
Only the woff2 faces are inlined; the upstream `.woff`/`.ttf` fallbacks are
dropped, because every browser that runs a lesson page reads woff2.
"""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

# `url(fonts/NAME.EXT) format("KIND")` — minified KaTeX writes exactly this,
# woff2 first, then the woff and ttf fallbacks after a comma.
_FACE = re.compile(r'url\(fonts/([^)]+)\)\s*format\("([^"]+)"\)')


def inline_fonts(css: str, fonts_dir: Path) -> str:
    """Return `css` with woff2 faces inlined and other faces dropped."""

    def as_data_url(match: re.Match[str]) -> str:
        name, kind = match.group(1), match.group(2)
        if kind != "woff2":
            return "\x00"  # sentinel: this fallback is removed below
        payload = base64.b64encode((fonts_dir / name).read_bytes()).decode("ascii")
        return f'url(data:font/woff2;base64,{payload}) format("woff2")'

    out = _FACE.sub(as_data_url, css)
    out = re.sub(r",?\x00", "", out)
    if "fonts/" in out:
        raise SystemExit("a font reference survived inlining; check the CSS format")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise SystemExit(f"usage: {argv[0]} <katex dist dir> <output css>")
    dist = Path(argv[1])
    out = Path(argv[2])
    css = (dist / "katex.min.css").read_text(encoding="utf-8")
    derived = inline_fonts(css, dist / "fonts")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(derived, encoding="utf-8")
    print(f"{out}: {len(derived)} bytes, {derived.count('data:font/woff2')} faces inlined")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
