"""Learn track progress (#81): "N of M studied" per track, from lesson.json.

Last file in the cumulative suite on purpose — it creates lessons, and the
status counts every earlier file asserts on are already settled by then.
"""
from __future__ import annotations

import json
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
        "pages": [{"id": "pg_track00001", "path": "index.html"}],
    }
    manifest.update(extra)
    (bundle / "lesson.json").write_text(json.dumps(manifest), encoding="utf-8")


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

    _write_manifest(made["t1_a"]["slug"], made["t1_a"]["uid"],
                    path="zz-track-one", step=101)
    _write_manifest(made["t1_b"]["slug"], made["t1_b"]["uid"],
                    path="zz-track-one", step=105)
    _write_manifest(made["t1_c"]["slug"], made["t1_c"]["uid"],
                    path="zz-track-one", step=110)
    _write_manifest(made["t2_a"]["slug"], made["t2_a"]["uid"],
                    path="zz-track-two", step=100)
    # Silent degradation: a manifest without `path` belongs to no track. The
    # lesson still renders in the list — it just never reaches the strip.
    _write_manifest(made["loose"]["slug"], made["loose"]["uid"])

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

    # A read the caller already took wins over re-reading the file: /learn
    # hands over the selected lesson's single ensured read (test_050 S4).
    from app.services import bundle_schema as _bschema

    _supplied = _bschema.rejected_read("manifest-unreadable", "supplied read")
    _reused = lessons_svc.track_progress(rows, reads={made["t1_a"]["id"]: _supplied})
    assert (
        {t["path"]: t["total"] for t in _reused}["zz-track-one"] == 2
    ), "a supplied manifest read replaces the file read for that lesson"

    # --- the rendered strip --------------------------------------------------
    r = c.get("/learn")
    assert r.status_code == 200, "GET /learn 200"
    assert (
        'class="learn-tracks"' in r.text
        and "zz-track-one" in r.text
        and "1 of 3 studied" in r.text
    ), "the strip renders one line per track, above the list"
    assert (
        f'class="learn-track-next" href="/learn?lesson={made["t1_b"]["id"]}"' in r.text
    ), "the next-step link points at the lesson, with no status filter attached"

    # Recommendation 1 in the #81 brief: the numbers are the whole active list,
    # so clicking a status pill cannot make "N of M" jump.
    _filtered = c.get("/learn?status=backlog")
    assert (
        "1 of 3 studied" in _filtered.text
    ), "the status filter does not change the track numbers"

    # An archived member leaves the track: it left the active list.
    conn = get_conn()
    try:
        lessons_svc.archive_lesson(conn, made["t1_c"]["id"])
    finally:
        conn.close()
    assert (
        "1 of 2 studied" in c.get("/learn").text
    ), "archiving a member drops it from its track"

    # --- deploy safety -------------------------------------------------------
    # Jinja re-reads templates per render, so the live pre-#81 process serves
    # this template against a context with no `tracks`. It must omit the strip,
    # not raise, or the merge breaks /learn until the next restart.
    from starlette.requests import Request as _Request

    from app.templating import templates as _tpl

    _req = _Request({
        "type": "http", "method": "GET", "path": "/learn", "headers": [],
        "query_string": b"", "client": ("127.0.0.1", 1234),
    })
    _no_ctx = _tpl.get_template("learn.html").render(
        request=_req, rail="learn", rows=[], status_filter=None,
        show_archived=False, counts={"all": 0, "archived": 0,
                                     **{k: 0 for k in lessons_svc.STATUSES}},
        status_tabs=[], selected=None, self_url="/learn", flash=None,
    )
    assert (
        "learn-tracks" not in _no_ctx and "Lesson title" in _no_ctx
    ), "learn.html renders without a `tracks` variable, omitting the strip"

    # The CSS the strip names actually exists.
    _css = (ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
    assert (
        ".learn-tracks {" in _css and ".learn-track-next {" in _css
    ), "the track strip has its styles"

    suite_state["track_progress"] = sorted(tracks)
