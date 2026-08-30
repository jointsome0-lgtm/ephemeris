"""Golden bytes of the agent brief and its reference companions.

The prose moved out of lessons.py into app/services/briefs/*.md (#232) and
is read back at import time. Each digest below was taken from the brief the
string constants rendered before the move, for the invented STATE here, so
a stray newline, encoding, or path slip in the loader shows up as a changed
digest rather than as a tutor reading a subtly different contract.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.services import lessons

STATE = (
    "\n## STATE (generated; refreshed on every terminal open)\n\n"
    "- Title: Vera Example learns tides\n"
    "- Slug: `vera-example-tides`\n"
    "- Status: studying\n"
    "- Current page: `index.html` (`pg_vera0001`)\n"
    "- Open questions (2 of 2 shown):\n"
    '  - `q_vera0001`, attempt `att_vera0001`: "Why two high tides a day?"\n'
    '  - `q_vera0002`, attempt `att_vera0002`: "Spring tide vs neap tide?"\n'
    "- Summary exists: no\n"
)

GOLDEN_BRIEF_WITH_SOURCE = (
    "a79a500e0147adc622956d7597d443005893375886c5a0d6249541c2901f4d99", 10236
)
GOLDEN_BRIEF_WITHOUT_SOURCE = (
    "ee1d29cac381445bb4c94d58b08bb141c3eccf0709e4f2e1c9817b55779bc616", 10205
)
GOLDEN_COMPANIONS = {
    "record.md": (
        "db1cdfb7e3e7c7837537d6efae7a23c7dc2435c393709febced88aa6bd9704ae", 8777
    ),
    "bridge.md": (
        "a01ea79b355ba9f3c239f3c105c50eb0d3753aec7c0809942f64bb493d40d383", 15247
    ),
    "packages.md": (
        "976cbc1d60da9ffa887838efe5a9f20b8b3b75453f15249ec3b958182e08883b", 4575
    ),
    "manifest.md": (
        "b16ae16815b92fc4e26c3e45db4fe9f6ee9b23ccd2cae8e6b9c7c30f66a09660", 5183
    ),
}


def _fingerprint(text: str) -> tuple[str, int]:
    data = text.encode("utf-8")
    return hashlib.sha256(data).hexdigest(), len(data)


def test_the_rendered_brief_matches_the_pre_move_bytes():
    assert _fingerprint(lessons._render_agents_brief(Path("made"), STATE)) == (
        GOLDEN_BRIEF_WITH_SOURCE
    ), "the brief rendered with a source/ directory changed"
    assert _fingerprint(lessons._render_agents_brief(None, STATE)) == (
        GOLDEN_BRIEF_WITHOUT_SOURCE
    ), "the brief rendered without a source/ directory changed"


def test_the_reference_companions_match_the_pre_move_bytes():
    assert set(lessons._REFERENCE_FILES) == set(GOLDEN_COMPANIONS)
    for name, text in lessons._REFERENCE_FILES.items():
        assert _fingerprint(text) == GOLDEN_COMPANIONS[name], f"{name} changed"


def test_the_briefs_directory_holds_exactly_the_loaded_files():
    on_disk = {path.name for path in lessons._BRIEFS_DIR.iterdir()}
    assert on_disk == {"agents.md", *lessons._REFERENCE_FILES}, (
        "every file under briefs/ is read at import time; an extra one is "
        "dead prose and a missing one fails the import"
    )
