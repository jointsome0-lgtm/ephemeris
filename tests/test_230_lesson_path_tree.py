"""Nested Learn tracks: `path` as an address, rendered as a tree.

Runs last in the cumulative suite: it seeds lessons of its own, and every
earlier file's lesson counts are settled before it starts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from conftest import ROOT


def _write_manifest(slug: str, uid: str, **extra) -> None:
    """A valid v2 manifest for `slug`, plus whatever the case declares."""
    from app.services import lessons as lessons_svc

    bundle = Path(lessons_svc.LESSONS_DIR) / slug
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "index.html").write_text("<p>page</p>", encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "lesson_uid": uid,
        "entry": "index.html",
        "pages": [{"id": "pg_tree000001", "path": "index.html"}],
    }
    manifest.update(extra)
    (bundle / "lesson.json").write_text(json.dumps(manifest), encoding="utf-8")


def _row_ids(html: str) -> list[int]:
    """Every lesson id the list rendered, in order, one hit per row."""
    return [
        int(m)
        for m in re.findall(r"/learn/lessons/(\d+)/(?:archive|restore)", html)
    ]


def test_path_ref_is_an_address():
    """§4.5 splitting: permissive on read, and the writer bounds hold."""
    from app.services import bundle_schema as bs

    assert (
        bs.split_path_ref("zt-cc/concepts/network-protocols")
        == ["zt-cc", "concepts", "network-protocols"]
    ), "an address splits into its nesting segments"
    assert (
        bs.split_path_ref("zt-cc//concepts/") == ["zt-cc", "concepts"]
    ), "empty segments collapse, so a stray slash cannot fork a track"
    assert (
        bs.split_path_ref(None) == [] and bs.split_path_ref("") == []
        and bs.split_path_ref("//") == []
    ), "an absent or all-separator ref places the lesson on no path"
    # Reading stays permissive where writing is bounded (§4.5 allows six
    # levels): an over-long address still groups rather than losing the tree.
    _deep = "/".join(f"zt-seg-{n}" for n in range(10))
    assert (
        len(bs.split_path_ref(_deep)) == 10
    ), "an address past the writer bounds still groups rather than vanishing"


def test_nested_tracks(client, suite_state):
    from app.db import get_conn
    from app.services import lessons as lessons_svc

    c = client

    # One course, two branches, plus a lesson sitting directly on the course
    # node — an ancestor that is a track in its own right AND a parent.
    conn = get_conn()
    try:
        made = {}
        for key, title in (
            ("intro", "Course Intro"),
            ("proto", "Network Protocols"),
            ("dns", "DNS Concepts"),
            ("chal", "Build Your Own Redis"),
            ("other", "Unrelated Course"),
        ):
            lesson_id = lessons_svc.create_lesson(conn, f"#tree {title}")
            made[key] = lessons_svc.get_lesson(conn, lesson_id)
        lessons_svc.set_status(conn, made["proto"]["id"], "studied")
    finally:
        conn.close()

    _write_manifest(made["intro"]["slug"], made["intro"]["uid"],
                    path="zt-cc", step=1)
    _write_manifest(made["proto"]["slug"], made["proto"]["uid"],
                    path="zt-cc/concepts/network-protocols", step=1)
    # A doubled separator: the same address as its sibling once normalized, so
    # both land in one node instead of two heads reading identically.
    _write_manifest(made["dns"]["slug"], made["dns"]["uid"],
                    path="zt-cc//concepts/dns", step=2)
    _write_manifest(made["chal"]["slug"], made["chal"]["uid"],
                    path="zt-cc/challenges/redis", step=1)
    _write_manifest(made["other"]["slug"], made["other"]["uid"],
                    path="zt-solo", step=1)

    conn = get_conn()
    try:
        rows = lessons_svc.list_lessons(conn)
    finally:
        conn.close()
    tracks = {t["path"]: t for t in lessons_svc.track_progress(rows)}
    assert (
        "zt-cc/concepts/dns" in tracks
        and "zt-cc//concepts/dns" not in tracks
    ), "membership is by the normalized address the tree groups on"

    tree = {n["path"]: n for n in lessons_svc.path_tree(tracks.values())}
    # Earlier files seeded their own flat tracks, which stay roots beside these.
    assert (
        {"zt-cc", "zt-solo"} <= set(tree)
        and not [path for path in tree if "/" in path]
    ), "only first segments are roots; a deeper address is somebody's child"
    _cc = tree["zt-cc"]
    assert (
        [child["path"] for child in _cc["children"]]
        == ["zt-cc/challenges", "zt-cc/concepts"]
    ), "an address with no lessons of its own still exists to hold its children"
    assert (
        _cc["rows_ids"] == [made["intro"]["id"]]
    ), "a node keeps its own lessons alongside its children"
    # The whole reason an ancestor exists: folded, its numbers have to count
    # what unfolding it would reveal.
    assert (
        _cc["total"] == 4 and _cc["studied"] == 1
    ), "an ancestor's count is its whole subtree's, not its own rows'"
    assert (
        _cc["ids"] == [made["intro"]["id"], made["chal"]["id"],
                       made["dns"]["id"], made["proto"]["id"]]
    ), "subtree order is own rows first, then children by address"
    # `next` follows that same walk, so the resume link of a folded course is
    # the first unstudied lesson a reader opening it would meet.
    assert (
        _cc["next"]["id"] == made["intro"]["id"]
    ), "an ancestor resumes at the first unstudied lesson of its subtree"
    _concepts = {child["path"]: child for child in _cc["children"]}
    assert (
        _concepts["zt-cc/concepts"]["next"]["id"] == made["dns"]["id"]
    ), "a fully studied branch is skipped when its sibling still has steps"
    assert (
        _concepts["zt-cc/concepts"]["name"] == "concepts"
    ), "a node is headed by its own segment, not the whole address"

    # --- rendering -----------------------------------------------------------
    r = c.get("/learn")
    assert r.status_code == 200, "GET /learn 200"
    assert (
        'data-track="zt-cc"' in r.text
        and 'data-track="zt-cc/concepts"' in r.text
        and 'data-track="zt-cc/concepts/network-protocols"' in r.text
    ), "every level of the address renders as its own group"
    assert (
        ">1 of 4</span>" in r.text
    ), "the course head carries its rolled-up count"
    _ids = _row_ids(r.text)
    assert (
        _ids.count(made["proto"]["id"]) == 1
    ), "nesting moves a row, it never renders one twice"
    assert (
        _ids.index(made["intro"]["id"]) < _ids.index(made["chal"]["id"])
    ), "a node's own rows render above its child groups"
    # Nesting is a section inside the parent's rows box — that containment is
    # what indents it, so a flattened render would still pass the count checks.
    _cc_html = r.text.split('data-track="zt-cc"')[1].split('data-track="zt-solo"')[0]
    assert (
        'data-track="zt-cc/concepts"' in _cc_html
    ), "a child group renders inside its parent, not beside it"

    # An ancestor survives on its descendants' rows alone: folding a branch
    # away must never be able to hide the only lesson that matched the filter.
    _filtered = c.get("/learn?status=studied")
    assert (
        'data-track="zt-cc"' in _filtered.text
        and 'data-track="zt-cc/concepts/network-protocols"' in _filtered.text
        and 'data-track="zt-cc/challenges"' not in _filtered.text
    ), "a branch the filter emptied is dropped; its filled siblings stay"
    assert (
        ">1 of 4</span>" in _filtered.text
    ), "the filter does not move the rolled-up numbers"

    # Navigating into a leaf unfolds every ancestor of it, or the lesson on
    # screen is inside a group the page renders closed.
    _sel = c.get(f"/learn?lesson={made['dns']['id']}")
    assert (
        'data-track="zt-cc" open>' in _sel.text
        and 'data-track="zt-cc/concepts" open>' in _sel.text
        and 'data-track="zt-cc/challenges" open>' not in _sel.text
    ), "the selected lesson's whole ancestor chain renders open"

    # --- deploy safety -------------------------------------------------------
    # Jinja re-reads templates per render, so the live pre-nesting process
    # serves this template with FLAT groups: `path` and no `name`, no
    # `children`. It must render the single level it always did, not raise.
    from starlette.requests import Request as _Request

    from app.templating import templates as _tpl

    _req = _Request({
        "type": "http", "method": "GET", "path": "/learn", "headers": [],
        "query_string": b"", "client": ("127.0.0.1", 1234),
    })
    conn = get_conn()
    try:
        _live_rows = lessons_svc.list_lessons(conn)
    finally:
        conn.close()
    _flat = _tpl.get_template("learn.html").render(
        request=_req, rail="learn", status_filter=None, show_archived=False,
        counts={"all": 0, "archived": 0,
                **{k: 0 for k in lessons_svc.STATUSES}},
        status_tabs=[], selected=None, self_url="/learn", flash=None,
        rows=_live_rows, tracks=[],
        groups=[{"path": "zt-cc", "studied": 1, "total": 4, "pct": 25,
                 "selected": False, "next": None,
                 "rows": [_live_rows[0]]}],
        ungrouped=_live_rows[1:],
    )
    assert (
        'data-track="zt-cc"' in _flat
        and ">zt-cc</span>" in _flat
        and len(_row_ids(_flat)) == len(_live_rows)
    ), "a pre-nesting backend's flat groups render, headed by the full address"

    # The live process has the globals it booted with, so a merged template
    # calling `star_body()` there is an UndefinedError, not a missing colour.
    # The `is defined` guard is tested with the global actually absent.
    _saved = _tpl.env.globals.pop("star_body")
    try:
        _preglobal = _tpl.get_template("learn.html").render(
            request=_req, rail="learn", status_filter=None, show_archived=False,
            counts={"all": 0, "archived": 0,
                    **{k: 0 for k in lessons_svc.STATUSES}},
            status_tabs=[], selected=None, self_url="/learn", flash=None,
            rows=_live_rows, tracks=[],
            groups=[{"path": "zt-cc", "studied": 1, "total": 4, "pct": 25,
                     "selected": False, "next": None,
                     "rows": [_live_rows[0]]}],
            ungrouped=_live_rows[1:],
        )
    finally:
        _tpl.env.globals["star_body"] = _saved
    assert (
        'data-track="zt-cc"' in _preglobal
        and '<details class="lesson-group-d" data-track="zt-cc"' in _preglobal
        and 'data-readout="25% · 4 lessons"' in _preglobal
    ), "without the global the tree still renders, unclassed and in the default gold"

    # --- the vine ------------------------------------------------------------
    # Each branch carries one shoot, filled to the same share its head counts.
    # The fill is a CSS gradient stop, so `--pct` on the rows box is the whole
    # contract between the numbers and the drawing: without it the shoot is
    # unlit everywhere and a finished track looks untouched.
    _vines = re.findall(
        r'<div class="lesson-group-rows" style="--pct: (\d+)%">\s*'
        r'<span class="lesson-vine vine-(\d)"',
        r.text,
    )
    assert _vines, "every branch draws a shoot, filled from its own `--pct`"
    assert (
        all(0 <= int(pct) <= 100 for pct, _ in _vines)
        and {shape for _, shape in _vines} <= {"0", "1", "2", "3"}
    ), "the fill is a percentage and the curl is one of the four shapes"
    # zt-cc is 1 of 4 studied; its head and its shoot must not disagree.
    _cc_rows = r.text.split('data-track="zt-cc"')[1]
    assert (
        '<div class="lesson-group-rows" style="--pct: 25%">' in _cc_rows.split("</details>")[0]
    ), "the shoot is filled to the rolled-up share its head prints"
    assert (
        'data-pct="25"' in _cc_rows.split("<div class=")[0]
    ), "the head carries the exact share for the hover readout"

    _css = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
    assert (
        ".lesson-group-rows .lesson-group-name {" in _css
    ), "a nested group head is styled a step down from its parent"
    assert (
        ".lesson-vine {" in _css
        and ".vine-0 {" in _css and ".vine-3 {" in _css
        and "content: attr(data-readout)" in _css
    ), "the shoot, its four curls and the hover readout have their styles"
    # Both ends have to be unambiguous, and only a fade band that shrinks
    # toward them delivers that — a fixed band leaves a finished branch dimming
    # at the tip and an untouched one lit at the head.
    assert (
        "min(var(--pct, 0%), 100% - var(--pct, 0%))" in _css
    ), "the fill's soft edge collapses at 0% and at 100%"
    # A tray revealed by hover is a tray a touch device cannot reach: it has no
    # hover, and `pointer-events: none` denies it the tap that would focus into
    # it. These forms are the only way to restatus or archive a lesson, so the
    # whole treatment stays behind a hover-capability query.
    _tray = _css.split(".lesson-group-rows > .lesson-row .lesson-actions {")[0]
    assert (
        _tray.rstrip().endswith("@media (hover: hover) {")
    ), "the hover-only action tray is gated on the device having hover"

    # --- spectral class ------------------------------------------------------
    # Every part that used to be hard-coded gold has to read `--star` instead,
    # or a blue giant grows gold tendrils.
    assert (
        ".lesson-group-d { --star: var(--astral); }" in _css
        and all(f".lesson-group-d.{c} {{ --star:" in _css
                for c in ("star-m", "star-k", "star-g", "star-f", "star-b", "star-bh"))
    ), "every spectral class sets --star, and an unclassed branch stays gold"
    _drawn = _css.split(".lesson-group-count {")[1].split(".lesson-list {")[0]
    assert (
        "var(--astral)" not in _drawn
    ), "past the ladder the tree draws in --star only, never in raw gold"
    assert (
        ".star-bh > .lesson-group-head > .lesson-body {" in _css
        and "var(--bh, 12px)" in _css
    ), "the black hole takes its diameter from the template, with a floor"
    # #169 dropped the caret, so the body is the only thing left that can say a
    # branch is folded — at every level, and for a black hole too, which is
    # already dark-cored and cannot answer by going hollow.
    assert (
        ".lesson-group-d:not([open]):not(.star-bh) > .lesson-group-head > .lesson-body {" in _css
        and ".lesson-group-d:not([open]).star-bh > .lesson-group-head > .lesson-body {" in _css
    ), "a folded cluster is marked at every level, black holes included"

    suite_state["path_tree"] = sorted(tree)


def test_star_body_ladder():
    """Mass picks the body, and past the ladder the body grows logarithmically.

    The thresholds are a product decision (owner, 2026-08-09: "x10 on all of
    it") — the ladder is meant to take years to climb, so an off-by-one here is
    a whole tier of the tree wearing the wrong colour.
    """
    from app.templating import BLACK_HOLE_MAX_PX, star_body

    # Each boundary from both sides: the limit is inclusive.
    for total, cls in ((0, "star-m"), (20, "star-m"), (21, "star-k"),
                       (50, "star-k"), (51, "star-g"),
                       (110, "star-g"), (111, "star-f"),
                       (230, "star-f"), (231, "star-b"),
                       (999, "star-b"), (1000, "star-bh")):
        assert star_body(total)["cls"] == cls, f"{total} lessons is a {cls}"

    # A star wears its size in CSS; only the hole carries a computed one.
    assert star_body(42)["size"] is None
    assert star_body(None)["cls"] == "star-m" and star_body(-5)["cls"] == "star-m"
    assert star_body(1)["label"] == "red dwarf"
    assert star_body(5000)["label"] == "black hole"

    # +3px per doubling, and a ceiling — otherwise a big enough platform root
    # eventually draws a disc the width of the panel.
    assert star_body(1000)["size"] == 12
    assert star_body(2000)["size"] == 15
    assert star_body(4000)["size"] == 18
    assert star_body(8000)["size"] == 21
    assert star_body(10_000_000)["size"] == BLACK_HOLE_MAX_PX
