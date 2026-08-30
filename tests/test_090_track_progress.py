"""Learn track progress (#81): "N of M studied" per track, from lesson.json.

Creates lessons of its own, after every earlier file's status counts are
settled.
"""
from __future__ import annotations

import json

from conftest import ROOT, write_lesson_manifest



def test_track_progress(client, suite_state):
    from app.db import get_conn
    from app.services import lessons as lessons_svc

    c = client

    # Two tracks plus an off-track lesson. Steps are declared out of order and
    # with a gap: the strip follows `step`, not creation order or id.
    conn = get_conn()
    try:
        made = {}
        for key, title in (
            ("t1_c", "Track One Third"),
            ("t1_a", "Track One First"),
            ("t1_b", "Track One Second"),
            ("t2_a", "Track Two First"),
            ("loose", "No Track At All"),
        ):
            lesson_id = lessons_svc.create_lesson(conn, f"#81 {title}")
            made[key] = lessons_svc.get_lesson(conn, lesson_id)
        lessons_svc.set_status(conn, made["t1_a"]["id"], "studied")
        lessons_svc.set_status(conn, made["t1_b"]["id"], "studying")
    finally:
        conn.close()

    write_lesson_manifest(made["t1_a"]["slug"], made["t1_a"]["uid"],
                    path="zz-track-one", step=101)
    write_lesson_manifest(made["t1_b"]["slug"], made["t1_b"]["uid"],
                    path="zz-track-one", step=105)
    write_lesson_manifest(made["t1_c"]["slug"], made["t1_c"]["uid"],
                    path="zz-track-one", step=110)
    write_lesson_manifest(made["t2_a"]["slug"], made["t2_a"]["uid"],
                    path="zz-track-two", step=100)
    # Silent degradation: a manifest without `path` belongs to no track. The
    # lesson still renders in the list — it just never reaches the strip.
    write_lesson_manifest(made["loose"]["slug"], made["loose"]["uid"])

    conn = get_conn()
    try:
        rows = lessons_svc.list_lessons(conn)
    finally:
        conn.close()
    tracks = {t["path"]: t for t in lessons_svc.track_progress(rows)}
    assert (
        "zz-track-one" in tracks and "zz-track-two" in tracks
    ), "each declared path becomes a track"
    assert (
        tracks["zz-track-one"]["total"] == 3
        and tracks["zz-track-one"]["studied"] == 1
    ), "N of M counts the track's members, studied ones as N"
    assert (
        made["loose"]["slug"] not in json.dumps(tracks)
        and sum(t["total"] for t in tracks.values()) == 4
    ), "a manifest without `path` puts its lesson in no track"
    # `studying` at step 105 precedes `backlog` at 110: the link follows step
    # order, so the next step is the earliest unstudied one, not the earliest
    # backlog one.
    assert (
        tracks["zz-track-one"]["next"]["id"] == made["t1_b"]["id"]
    ), "next step = first non-studied member in step order"

    # A track whose members are all studied has nothing left to link to.
    conn = get_conn()
    try:
        lessons_svc.set_status(conn, made["t2_a"]["id"], "studied")
        rows = lessons_svc.list_lessons(conn)
    finally:
        conn.close()
    _finished = {t["path"]: t for t in lessons_svc.track_progress(rows)}["zz-track-two"]
    assert (
        _finished["next"] is None
        and _finished["studied"] == _finished["total"] == 1
    ), "a fully studied track offers no next step"

    # A manifest that declares a path but is not usable does not enrol its
    # lesson. `_read_v2` parses `path` before the checks that reject the
    # manifest, so both cases need the gate, not just the unreadable ones.
    conn = get_conn()
    try:
        _bad = {}
        for key, title in (("rejected", "Rejected Manifest"),
                           ("foreign", "Foreign Manifest")):
            _bad[key] = lessons_svc.get_lesson(
                conn, lessons_svc.create_lesson(conn, f"#81 {title}"))
    finally:
        conn.close()
    # duplicate page id: rejected on §9.2 grounds, `path` still parsed
    write_lesson_manifest(_bad["rejected"]["slug"], _bad["rejected"]["uid"],
                    path="zz-track-one", step=120,
                    pages=[{"id": "pg_track00001", "path": "index.html"},
                           {"id": "pg_track00001", "path": "other.html"}])
    # a bundle whose manifest names a different lesson entirely
    write_lesson_manifest(_bad["foreign"]["slug"],
                    "0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60",
                    path="zz-track-one", step=130)
    conn = get_conn()
    try:
        rows = lessons_svc.list_lessons(conn)
    finally:
        conn.close()
    _gated = {t["path"]: t for t in lessons_svc.track_progress(rows)}
    assert (
        _gated["zz-track-one"]["total"] == 3
        and _gated["zz-track-one"]["next"]["id"] == made["t1_b"]["id"]
    ), "a rejected or foreign manifest joins no track and is never the next step"

    # A read the caller already took wins over re-reading the file: /learn
    # hands over the selected lesson's single ensured read (test_050 S4).
    from app.services import bundle_schema as _bschema

    _supplied = _bschema.rejected_read("manifest-unreadable", "supplied read")
    _reused = lessons_svc.track_progress(rows, reads={made["t1_a"]["id"]: _supplied})
    assert (
        {t["path"]: t["total"] for t in _reused}["zz-track-one"] == 2
    ), "a supplied manifest read replaces the file read for that lesson"

    # Membership travels with the counts, so a caller grouping rows applies the
    # same gate that produced them rather than deriving it a second time.
    assert (
        _gated["zz-track-one"]["ids"] == [made["t1_a"]["id"], made["t1_b"]["id"],
                                          made["t1_c"]["id"]]
        and _bad["rejected"]["id"] not in _gated["zz-track-one"]["ids"]
    ), "`ids` lists exactly the counted members, in step order"

    # --- the rendered groups -------------------------------------------------
    # A track renders as one collapsible group carrying its progress, and the
    # #81 strip is not rendered beside it: two copies of the same numbers on one
    # narrow panel is what folding the track into the list set out to remove.
    r = c.get("/learn")
    assert r.status_code == 200, "GET /learn 200"
    assert (
        'class="lesson-group-head"' in r.text
        and 'data-track="zz-track-one"' in r.text
        and ">1 of 3</span>" in r.text
    ), "each track is a group head with its count"
    assert (
        'style="width: 33%"' in r.text
    ), "the progress bar fills to the studied share of the track"
    assert (
        f'class="lesson-group-next" href="/learn?lesson={made["t1_b"]["id"]}"' in r.text
    ), "the next-step link points at the lesson, with no status filter attached"

    def _row_ids(html: str) -> list[int]:
        """Every lesson id the list rendered, in order, one hit per row."""
        import re

        return [int(m) for m in re.findall(r"/learn/lessons/(\d+)/(?:archive|restore)",
                                           html)]

    _ids = _row_ids(r.text)
    assert (
        _ids.count(made["t1_a"]["id"]) == 1 and _ids.count(made["loose"]["id"]) == 1
    ), "grouping moves a row, it never renders one twice"
    # The loose lesson has no track, so it follows every group in the tail.
    assert (
        _ids.index(made["loose"]["id"]) > _ids.index(made["t1_c"]["id"])
    ), "lessons in no track render after the groups, not inside one"
    # Members follow the track's own step order, not the list's recency order:
    # t1_c was created first but declares the last step.
    assert (
        _ids.index(made["t1_a"]["id"])
        < _ids.index(made["t1_b"]["id"])
        < _ids.index(made["t1_c"]["id"])
    ), "a group orders its rows by step, not by the list's order"

    # The group holding the lesson on screen is open; the others start folded,
    # which is the whole point when a seeded course is fourteen rows long.
    _sel = c.get(f"/learn?lesson={made['t1_b']['id']}")
    assert (
        'data-track="zz-track-one" open>' in _sel.text
        and 'data-track="zz-track-two" open>' not in _sel.text
    ), "only the selected lesson's group renders open"
    # A <details> parsed with `open` queues a toggle event of its own, so the
    # state-remembering script must compare against what it already knows
    # instead of trusting the event. Trusting it recorded "the learner opened
    # this track" for every track merely visited — and /learn always selects a
    # lesson, so the folded list unfolded itself again one track per visit.
    assert (
        "if (d.open === known) return;" in _sel.text
    ), "the open-state script ignores a toggle that reports no change"

    # Recommendation 1 in the #81 brief: the numbers are the whole active list,
    # so clicking a status pill cannot make "N of M" jump — even though the pill
    # does decide which of the track's rows appear under the head.
    _filtered = c.get("/learn?status=backlog")
    assert (
        ">1 of 3</span>" in _filtered.text
    ), "the status filter does not change the track numbers"
    _filtered_ids = _row_ids(_filtered.text)
    assert (
        made["t1_c"]["id"] in _filtered_ids
        and made["t1_a"]["id"] not in _filtered_ids
    ), "the filter still decides which rows render inside the group"
    # zz-track-two's only member is studied, so nothing of it survives `backlog`
    # and an empty head would be a track that looks like it has no lessons.
    assert (
        'data-track="zz-track-two"' not in _filtered.text
    ), "a track with no row left under the filter is dropped, not left empty"

    # An archived member leaves the track: it left the active list.
    conn = get_conn()
    try:
        lessons_svc.archive_lesson(conn, made["t1_c"]["id"])
    finally:
        conn.close()
    assert (
        ">1 of 2</span>" in c.get("/learn").text
    ), "archiving a member drops it from its track"
    _arch = c.get("/learn?archived=1")
    assert (
        "lesson-group-head" not in _arch.text
        and made["t1_c"]["id"] in _row_ids(_arch.text)
    ), "an archived member renders flat: archiving already removed it from the track"

    # --- deploy safety -------------------------------------------------------
    # Jinja re-reads templates per render, so the live pre-change process serves
    # this template against a context with neither `groups` nor `ungrouped` (and
    # the pre-#81 one without `tracks` either). It must fall back to the flat
    # list, not raise, or the merge breaks /learn until the next restart.
    from starlette.requests import Request as _Request

    from app.templating import templates as _tpl

    _req = _Request({
        "type": "http", "method": "GET", "path": "/learn", "headers": [],
        "query_string": b"", "client": ("127.0.0.1", 1234),
    })
    _base_ctx = dict(
        request=_req, rail="learn", status_filter=None,
        show_archived=False, counts={"all": 0, "archived": 0,
                                     **{k: 0 for k in lessons_svc.STATUSES}},
        status_tabs=[], selected=None, self_url="/learn", flash=None,
    )
    _no_ctx = _tpl.get_template("learn.html").render(rows=[], **_base_ctx)
    assert (
        "Lesson title" in _no_ctx
    ), "learn.html renders without a `tracks` variable"

    # The CSS the groups name actually exists.
    _css = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
    assert (
        ".lesson-group {" in _css and ".lesson-group-bar {" in _css
    ), "the groups have their styles"
    # The bug this layout was fixed for: the panel's chrome shrank under a
    # column that no longer scrolls (#132), and the add form — the one child
    # whose explicit `min-height` replaced its automatic minimum — collapsed to
    # one row's height and painted its other rows over the filters.
    assert (
        ".lesson-panel > .learn-add," in _css
        and "min-height: 46px" not in _css
    ), "the panel's chrome is pinned against the flex shrink that overlapped it"

    suite_state["track_progress"] = sorted(tracks)
