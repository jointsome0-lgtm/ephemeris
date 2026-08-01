"""Bundle schema, bridge identity, and attempt authority verification."""
from __future__ import annotations

import hashlib
import importlib.abc as _importlib_abc
import json
import os
import sqlite3
import stat as stat_module
import subprocess
import sys
import tempfile
import threading
from datetime import date as _vdate
from pathlib import Path

from conftest import ROOT, events_of, item_row



def test_bundle_attempts(client, suite_state):
    from app.db import SCHEMA_VERSION, get_conn, pretty_date, today_str
    from app.main import app

    c = client
    _mf_meta = suite_state["_mf_meta"]
    _os = suite_state["_os"]
    lessons_svc = suite_state["lessons_svc"]
    terminal_js = suite_state["terminal_js"]
    # --- C3: bundle schema v2 (learn-bundle-spec.md) — readers, writer, identity
    from app import db as db_mod
    from app.services import bundle_schema as bschema

    # every cases.json expectation holds under the fixture-only runner registry
    _fx_dir = ROOT / "fixtures" / "lesson-manifests"
    _fx_cases = json.loads((_fx_dir / "cases.json").read_text(encoding="utf-8"))
    _fx_registry = frozenset(_fx_cases["context"]["runner_registry"]["known"])
    for _case in _fx_cases["cases"]:
        _fx_text = (_fx_dir / _case["file"]).read_text(encoding="utf-8")
        _fx_read = bschema.read_manifest_text(_fx_text, runner_registry=_fx_registry)
        assert (
            _fx_read.outcome == _case["expect"]
            and _fx_read.version == _case["read_as"]
            and set(_case["findings"]) <= _fx_read.codes()
        ), (
            f"fixture {_case['file']}: {_case['expect']}, read as {_case['read_as']}"
            + "  -- "
            + (
                f"outcome={_fx_read.outcome} version={_fx_read.version} "
                f"codes={sorted(_fx_read.codes())}"
            )
        )

    # §9.3: round-tripping a canonical manifest is byte-identical
    _fx_roundtrips = [
        bschema.canonical_dumps(
            json.loads(_fx_file.read_text(encoding="utf-8")),
            json.loads(_fx_file.read_text(encoding="utf-8")).get("schema_version", 1),
        ) == _fx_file.read_text(encoding="utf-8")
        for _fx_file in sorted(_fx_dir.glob("*.json"))
        if _fx_file.name != "cases.json"
    ]
    assert (
        len(_fx_roundtrips) == 10 and all(_fx_roundtrips)
    ), "canonical writer round-trips all 10 fixture manifests byte-identically"

    # duplicate ids are raw-declaration facts: an id repeated on an item that
    # is dropped for its path still rejects the manifest (PR-48 round 2)
    _dup_masked = bschema.read_manifest_text(json.dumps({
        "schema_version": 2,
        "lesson_uid": "0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60",
        "entry": "index.html",
        "pages": [
            {"id": "pg_maskdup01", "path": "../escape.html"},
            {"id": "pg_maskdup01", "path": "index.html"},
        ],
    }))
    assert (
        _dup_masked.outcome == "rejected"
        and {"duplicate-id", "invalid-path"} <= _dup_masked.codes()
    ), "duplicate page id behind a dropped path still rejects"

    # block page/kind/root checks are independent (§9.2 aggregation): every
    # violation of the declaration is recorded before the block is dropped
    # (PR-48 rounds 15+18)
    _blk_masked = bschema.read_manifest_text(json.dumps({
        "schema_version": 2,
        "lesson_uid": "0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60",
        "entry": "index.html",
        "pages": [{"id": "pg_blkmask01", "path": "index.html"}],
        "blocks": [{
            "id": "blk_blkmask01",
            "page": "pg_ghostpage1",
            "kind": "mystery",
            "file": "scratch/work.py",
        }],
    }))
    assert (
        {"dangling-ref", "unknown-kind", "outside-root"} <= _blk_masked.codes()
    ), "dropped block reports dangling page, unknown kind, and outside-root together"

    # §4.1: a path the request-cleaning layer would strip (edge whitespace)
    # is invalid, not repaired — the reader and the disk resolver would
    # otherwise disagree about which file the page names (PR-48 round 17)
    _sp_read = bschema.read_manifest_text(json.dumps({
        "schema_version": 2,
        "lesson_uid": "0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60",
        "entry": "index.html",
        "pages": [
            {"id": "pg_spacepad01", "path": "index.html"},
            {"id": "pg_spacepad02", "path": " spaced.html"},
        ],
    }))
    assert (
        _sp_read.outcome == "degraded"
        and "invalid-path" in _sp_read.codes()
        and " spaced.html" not in _sp_read.page_paths()
    ), "v2 page path with edge whitespace is invalid-path, not repaired"

    # lesson identity (§3): minted once at creation, echoed in manifest + event
    _uid_conn = get_conn()
    try:
        _uid_id = lessons_svc.create_lesson(
            _uid_conn, "Uid Mint Demo", "https://learning.example/uid-demo")
        _uid_lesson = lessons_svc.get_lesson(_uid_conn, _uid_id)
    finally:
        _uid_conn.close()
    assert (
        bool(_uid_lesson["uid"])
        and bschema.UUID_RE.match(_uid_lesson["uid"]) is not None
    ), "create_lesson mints a lesson uid"
    _uid_manifest_path = Path(lessons_svc.LESSONS_DIR) / _uid_lesson["slug"] / "lesson.json"
    _uid_manifest_text = _uid_manifest_path.read_text(encoding="utf-8")
    _uid_manifest = json.loads(_uid_manifest_text)
    assert (
        _uid_manifest.get("schema_version") == 2
        and _uid_manifest.get("lesson_uid") == _uid_lesson["uid"]
        and _uid_manifest.get("entry") == "index.html"
        and [p.get("path") for p in _uid_manifest.get("pages", [])] == ["index.html"]
        and bschema.PAGE_ID_RE.match(_uid_manifest["pages"][0]["id"]) is not None
        and _uid_manifest.get("runtime") == {"profile": "interactive-local-v1"}
        and _uid_manifest.get("artifact_roots") == ["attempts"]
        and _uid_manifest.get("source_url") == "https://learning.example/uid-demo"
    ), "create_lesson writes the v2 skeleton manifest (§5)"
    assert (
        bschema.canonical_dumps(_uid_manifest) == _uid_manifest_text
    ), "v2 skeleton is canonical on disk"
    assert (
        (Path(lessons_svc.LESSONS_DIR) / _uid_lesson["slug"] / "attempts").is_dir()
    ), "v2 bundle gets its default artifact root dir"
    _uid_created = json.loads(events_of("lesson_created")[-1]["payload_json"])
    assert (
        _uid_created.get("lesson_uid") == _uid_lesson["uid"]
        and _uid_created.get("lesson_id") == _uid_id
        and "title" not in _uid_created
    ), "lesson_created event echoes lesson_uid, never title (§8)"

    # rename churn never re-mints (§3): uid survives title+slug change,
    # backfill rerun is a no-op, a NULL-uid row (stale pre-v11 writer) heals
    _uid_conn = get_conn()
    try:
        with _uid_conn:
            _uid_conn.execute(
                "UPDATE lessons SET title='Uid Mint Demo Renamed', "
                "slug='uid-mint-demo-renamed' WHERE id=?", (_uid_id,))
        _restamped = db_mod.backfill_lesson_uids(_uid_conn)
        _uid_conn.commit()
        _uid_after = lessons_svc.get_lesson(_uid_conn, _uid_id)
        with _uid_conn:
            _uid_conn.execute(
                "INSERT INTO lessons (title, slug, status, created_at) "
                "VALUES ('Stale Writer Demo', 'stale-writer-demo', 'backlog', ?)",
                (db_mod.now_iso(),))
        _healed = db_mod.backfill_lesson_uids(_uid_conn)
        _uid_conn.commit()
        _stale_uid = _uid_conn.execute(
            "SELECT uid FROM lessons WHERE slug='stale-writer-demo'").fetchone()["uid"]
    finally:
        _uid_conn.close()
    assert (
        _restamped == 0 and _uid_after["uid"] == _uid_lesson["uid"]
    ), "rename does not change the lesson uid"
    assert (
        _healed == 1 and _stale_uid and bschema.UUID_RE.match(_stale_uid) is not None
    ), "uid backfill stamps exactly the NULL-uid rows"

    # v10→v11 renumbering hazard: a DB that ran the uid step while it was
    # numbered v10 sits at user_version=10 WITHOUT retro_entries, and the
    # landed v10 step is skipped on its way to 11 — _migrate_to_11 must
    # converge that shape itself
    _ren = sqlite3.connect(":memory:")
    _ren.row_factory = sqlite3.Row
    _ren.execute("CREATE TABLE lessons (id INTEGER PRIMARY KEY, title TEXT, uid TEXT)")
    _ren.execute("INSERT INTO lessons (title) VALUES ('Renumber Demo')")
    db_mod._migrate_to_11(_ren)
    _ren_tabs = {r["name"] for r in _ren.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    _ren_uid = _ren.execute("SELECT uid FROM lessons").fetchone()["uid"]
    _ren.close()
    assert (
        "retro_entries" in _ren_tabs and _ren_uid
        and bschema.UUID_RE.match(_ren_uid) is not None
    ), "v11 on a branch-v10 DB creates retro_entries and backfills uids"

    # v2 read path: declared pages only (§4.2), unknown fields preserved (§9.3)
    _v2_conn = get_conn()
    try:
        _v2_id = lessons_svc.create_lesson(_v2_conn, "V2 Reader Demo")
        _v2 = lessons_svc.get_lesson(_v2_conn, _v2_id)
    finally:
        _v2_conn.close()
    _v2_dir = Path(lessons_svc.LESSONS_DIR) / _v2["slug"]
    _v2_raw = json.loads((_v2_dir / "lesson.json").read_text(encoding="utf-8"))
    _v2_raw["pages"].append({"id": "pg_stagetwo01", "path": "related/01-stage.html"})
    _v2_raw["x_note"] = {"keep": ["me"]}
    bschema.write_manifest(_v2_dir / "lesson.json", _v2_raw)
    (_v2_dir / "index.html").write_text("<html>Vera Example index</html>", encoding="utf-8")
    (_v2_dir / "related" / "01-stage.html").write_text(
        "<html>Vera Example stage</html>", encoding="utf-8")
    _v2_view = lessons_svc.with_bundle_info(_v2)
    assert (
        [p["entry"] for p in _v2_view["pages"]] == ["index.html", "related/01-stage.html"]
        and _v2_view["bundle"]["schema_version"] == 2
        and _v2_view["bundle"]["outcome"] == "ok"
        and _v2_view["bundle"]["profile"] == "interactive-local-v1"
    ), "v2 bundle lists exactly the declared pages, in order"
    _v2_ghost = lessons_svc.bundle_info(_v2, entry="related/99-ghost.html")
    assert (
        _v2_ghost["entry"] == "index.html"
        and all(p["entry"] != "related/99-ghost.html" for p in _v2_ghost["pages"])
    ), "v2 undeclared selection falls back to the manifest entry (§4.2)"
    assert (
        _v2_ghost["outcome"] == "degraded"
        and any(f["code"] == "invalid-entry" for f in _v2_ghost["findings"])
    ), "stale selection degrades the top-level bundle outcome too"
    _v2_ghost_meta = c.get(
        f"/learn/lessons/{_v2_id}/preview-meta",
        params={"entry": "related/99-ghost.html"}).json()
    assert (
        _v2_ghost_meta["outcome"] == "degraded"
        and any(f["code"] == "invalid-entry" for f in _v2_ghost_meta["findings"])
    ), "stale v2 selection surfaces invalid-entry, never a silent ok (§4.2)"
    assert (
        json.loads((_v2_dir / "lesson.json").read_text(encoding="utf-8"))
        .get("x_note") == {"keep": ["me"]}
    ), "unknown manifest fields survive the canonical writer"
    _v2_conn = get_conn()
    try:
        _v2_refused = False
        try:
            lessons_svc.set_current_entry(_v2_conn, _v2_id, "related/99-ghost.html")
        except lessons_svc.LessonError:
            _v2_refused = True
        lessons_svc.set_current_entry(_v2_conn, _v2_id, "related/01-stage.html")
        _v2_after = lessons_svc.get_lesson(_v2_conn, _v2_id)
    finally:
        _v2_conn.close()
    _v2_entry_event = json.loads(events_of("lesson_entry_changed")[-1]["payload_json"])
    assert (
        _v2_refused and _v2_after["current_entry"] == "related/01-stage.html"
    ), "set_current_entry refuses an undeclared v2 page"
    assert (
        _v2_entry_event.get("lesson_uid") == _v2["uid"]
        and _v2_entry_event.get("to_entry") == "related/01-stage.html"
    ), "lesson_entry_changed event echoes lesson_uid"

    # a page removed from the manifest AFTER being selected leaves a stale
    # stored selection: the render falls back visibly, the fallback is NOT
    # persisted over the evidence, and the metadata poll URL carries the
    # stale candidate so every poll re-surfaces the finding (§4.2)
    _v2_cut = json.loads((_v2_dir / "lesson.json").read_text(encoding="utf-8"))
    _v2_cut["pages"] = [p for p in _v2_cut["pages"] if p["path"] != "related/01-stage.html"]
    bschema.write_manifest(_v2_dir / "lesson.json", _v2_cut)
    _v2_conn = get_conn()
    try:
        _v2_stale = lessons_svc.bundle_info(lessons_svc.get_lesson(_v2_conn, _v2_id))
        _learn_html = c.get(f"/learn?lesson={_v2_id}").text
        _v2_kept = lessons_svc.get_lesson(_v2_conn, _v2_id)["current_entry"]
    finally:
        _v2_conn.close()
    assert (
        _v2_stale["stale_selection"] == "related/01-stage.html"
        and _v2_stale["entry"] == "index.html"
        and _v2_kept == "related/01-stage.html"
    ), "stale stored selection is exposed and never silently persisted"
    assert (
        "preview-meta?entry=related%2F01-stage.html" in _learn_html
    ), "preview-meta poll URL keeps the stale candidate, not the fallback"
    _v2_stale_meta = c.get(
        f"/learn/lessons/{_v2_id}/preview-meta",
        params={"entry": "related/01-stage.html"}).json()
    assert (
        _v2_stale_meta["outcome"] == "degraded"
        and any(f["code"] == "invalid-entry" for f in _v2_stale_meta["findings"])
        and _v2_stale_meta["exists"] is True
    ), "polling the stale candidate re-surfaces invalid-entry each time"
    # restore the two-page manifest — later sections rely on it
    bschema.write_manifest(_v2_dir / "lesson.json", _v2_raw)

    # corrupt / unsupported manifests reject visibly (§9.1) and stay untouched
    _rej_conn = get_conn()
    try:
        _rej_id = lessons_svc.create_lesson(_rej_conn, "Reject Demo")
        _rej = lessons_svc.get_lesson(_rej_conn, _rej_id)
    finally:
        _rej_conn.close()
    _rej_path = Path(lessons_svc.LESSONS_DIR) / _rej["slug"] / "lesson.json"
    _rej_path.write_text('{"schema_version": 2, "broken', encoding="utf-8")
    _rej_meta = c.get(f"/learn/lessons/{_rej_id}/preview-meta").json()
    _rej_prev = c.get(f"/learn/lessons/{_rej_id}/preview")
    assert (
        _rej_meta["outcome"] == "rejected"
        and any(f["code"] == "manifest-unreadable" for f in _rej_meta["findings"])
        and "lesson.json is not readable JSON." in _rej_prev.text
        and _rej_path.read_text(encoding="utf-8") == '{"schema_version": 2, "broken'
    ), "corrupt manifest is a visible reject, not a silent default"
    assert (
        c.get(f"/learn?lesson={_rej_id}").status_code == 200
    ), "GET /learn stays 200 with a rejected manifest selected"
    _rej_path.write_text(
        json.dumps({"schema_version": 99, "entry": "index.html"}) + "\n", encoding="utf-8")
    _rej_meta2 = c.get(f"/learn/lessons/{_rej_id}/preview-meta").json()
    _rej_prev2 = c.get(f"/learn/lessons/{_rej_id}/preview")
    assert (
        _rej_meta2["outcome"] == "rejected"
        and any(f["code"] == "unsupported-version" for f in _rej_meta2["findings"])
        and "Unsupported manifest version." in _rej_prev2.text
    ), "unsupported manifest version rejects visibly"
    # placeholder-to-placeholder transitions are visible to the live-reload
    # poller: the version token tracks the manifest state, not a flat "0"
    assert (
        _rej_meta["version"].startswith("rejected:")
        and _rej_meta2["version"].startswith("rejected:")
        and _rej_meta["version"] != _rej_meta2["version"]
        and _mf_meta["version"].startswith("missing:")
    ), "placeholder version tokens track the manifest state"
    # rejected means NO page render — direct file fetches included (§9.2)
    (Path(lessons_svc.LESSONS_DIR) / _rej["slug"] / "index.html").write_text(
        "<html>Vera Example orphan page</html>", encoding="utf-8")
    assert (
        c.get(f"/learn/lessons/{_rej_id}/files/index.html").status_code == 404
    ), "rejected manifest blocks direct bundle file renders too (§9.2)"

    # v1 manifests dual-read unchanged (§9.2) and are never rewritten (§9.1)
    _v1_conn = get_conn()
    try:
        _v1_id = lessons_svc.create_lesson(_v1_conn, "V1 Dual Read Demo")
        _v1 = lessons_svc.get_lesson(_v1_conn, _v1_id)
    finally:
        _v1_conn.close()
    _v1_dir = Path(lessons_svc.LESSONS_DIR) / _v1["slug"]
    _v1_text = (_fx_dir / "v1-valid.json").read_text(encoding="utf-8")
    (_v1_dir / "lesson.json").write_text(_v1_text, encoding="utf-8")
    (_v1_dir / "index.html").write_text("<html>Vera Example v1</html>", encoding="utf-8")
    _v1_view = lessons_svc.with_bundle_info(_v1)
    assert (
        _v1_view["bundle"]["schema_version"] == 1
        and [p["entry"] for p in _v1_view["pages"]]
        == ["index.html", "related/01-gravity-gradient.html",
            "related/02-spring-and-neap.html"]
        and _v1_view["bundle"]["profile"] == "legacy-display"
        and _v1_view["bundle"]["outcome"] == "ok"
    ), "v1 manifest dual-reads with entry + related pages, legacy profile"
    assert (
        (_v1_dir / "lesson.json").read_text(encoding="utf-8") == _v1_text
    ), "v1 manifest is never rewritten by the read path"

    # profile-keyed CSP enforcement (§5, D1): interactive-local-v1 serves
    # under the strict local-only policy, legacy-display keeps the historical
    # permissive one, and the preview metadata surfaces the effective profile
    # plus bridge eligibility (v2 ∧ not rejected ∧ interactive)
    from app.routers.learn import (  # local: only these checks use them
        _LESSON_PREVIEW_CSP_INTERACTIVE as _CSP_INT,
        _LESSON_PREVIEW_CSP_LEGACY as _CSP_LEG,
        _preview_csp as _csp_for,
    )
    _d1_file = c.get(f"/learn/lessons/{_v2_id}/files/index.html")
    _d1_prev = c.get(f"/learn/lessons/{_v2_id}/preview")
    _d1_csp = _d1_file.headers.get("content-security-policy", "")
    assert (
        _d1_file.status_code == 200 and _d1_csp == _CSP_INT
        and _d1_prev.headers.get("content-security-policy") == _CSP_INT
    ), "v2 interactive pages serve under the strict D1 CSP (files + preview)"
    assert (
        "connect-src 'none'" in _d1_csp
        and "webrtc 'block'" in _d1_csp
        and "default-src 'none'" in _d1_csp
        and "form-action 'none'" in _d1_csp
        and "base-uri 'none'" in _d1_csp
        and "https:" not in _d1_csp
        and "unsafe-eval" not in _d1_csp
        and "sandbox allow-scripts;" in _d1_csp
        and "allow-forms" not in _d1_csp
        and "allow-popups" not in _d1_csp
        and "allow-downloads" not in _d1_csp
    ), "strict CSP: no network, no eval, no forms/popups/downloads"
    _d1_meta = c.get(f"/learn/lessons/{_v2_id}/preview-meta").json()
    assert (
        _d1_meta["profile"] == "interactive-local-v1"
        and _d1_meta["bridge"] is True
        and lessons_svc.bundle_info(_v2)["bridge"] is True
    ), "preview-meta surfaces interactive profile + bridge eligibility"
    # degraded v2 findings keep profile and bridge — identity stays valid,
    # D2 gates per page; only fail-closed-to-legacy paths revoke them
    _d1_stale = c.get(
        f"/learn/lessons/{_v2_id}/preview-meta",
        params={"entry": "related/99-ghost.html"}).json()
    assert (
        _d1_stale["outcome"] == "degraded"
        and _d1_stale["profile"] == "interactive-local-v1"
        and _d1_stale["bridge"] is True
    ), "degraded v2 read keeps profile + bridge"
    _d1_v1 = c.get(f"/learn/lessons/{_v1_id}/files/index.html")
    _d1_v1_meta = c.get(f"/learn/lessons/{_v1_id}/preview-meta").json()
    assert (
        _d1_v1.headers.get("content-security-policy") == _CSP_LEG
        and _d1_v1_meta["profile"] == "legacy-display"
        and _d1_v1_meta["bridge"] is False
    ), "v1 bundle keeps the legacy CSP and never gets the bridge"
    # unknown profile fails closed: forced legacy-display, no bridge; the
    # wide policy is only ever reached via the *registered* legacy profile
    bschema.write_manifest(
        _v2_dir / "lesson.json",
        dict(_v2_raw, runtime={"profile": "interactive-local-v2"}))
    _d1_unk_meta = c.get(f"/learn/lessons/{_v2_id}/preview-meta").json()
    _d1_unk_file = c.get(f"/learn/lessons/{_v2_id}/files/index.html")
    assert (
        _d1_unk_meta["profile"] == "legacy-display"
        and _d1_unk_meta["bridge"] is False
        and any(f["code"] == "unknown-profile" for f in _d1_unk_meta["findings"])
        and _d1_unk_file.headers.get("content-security-policy") == _CSP_LEG
    ), "unknown profile fails closed to legacy-display without bridge"
    bschema.write_manifest(_v2_dir / "lesson.json", _v2_raw)  # restore
    _d1_rej_meta = c.get(f"/learn/lessons/{_rej_id}/preview-meta").json()
    _d1_rej_prev = c.get(f"/learn/lessons/{_rej_id}/preview")
    assert (
        _d1_rej_meta["profile"] == "legacy-display"
        and _d1_rej_meta["bridge"] is False
        and _d1_rej_prev.headers.get("content-security-policy") == _CSP_LEG
    ), "rejected manifest: legacy profile, no bridge, placeholder CSP"
    assert (
        _csp_for("weird-unregistered") == _CSP_INT
        and _csp_for("legacy-display") == _CSP_LEG
        and _csp_for("interactive-local-v1") == _CSP_INT
    ), "an unregistered profile value selects the narrow policy"
    # drain C1: an effective-profile transition must invalidate the open
    # page's reload token in BOTH directions, page bytes untouched — the
    # displayed document must have been served under the CSP the metadata
    # advertises before D2 grants anything against it
    _d1_v_int = c.get(f"/learn/lessons/{_v2_id}/preview-meta").json()["version"]
    bschema.write_manifest(
        _v2_dir / "lesson.json", dict(_v2_raw, runtime={"profile": "legacy-display"}))
    _d1_leg_meta = c.get(f"/learn/lessons/{_v2_id}/preview-meta").json()
    _d1_leg_file = c.get(f"/learn/lessons/{_v2_id}/files/index.html")
    bschema.write_manifest(_v2_dir / "lesson.json", _v2_raw)  # restore
    _d1_v_back = c.get(f"/learn/lessons/{_v2_id}/preview-meta").json()["version"]
    assert (
        _d1_leg_meta["version"] != _d1_v_int
        and _d1_v_back == _d1_v_int
        and _d1_leg_meta["profile"] == "legacy-display"
        and _d1_leg_meta["bridge"] is False
        and _d1_leg_file.headers.get("content-security-policy") == _CSP_LEG
    ), "profile flip changes the reload token both ways, bytes untouched"
    # identity-mismatch (opus pass): a v2 manifest whose lesson_uid disagrees
    # with the DB row is forced legacy — profile and bridge revoke together
    bschema.write_manifest(
        _v2_dir / "lesson.json",
        dict(_v2_raw, lesson_uid="00000000-0000-4000-8000-000000000000"))
    _d1_mid_meta = c.get(f"/learn/lessons/{_v2_id}/preview-meta").json()
    bschema.write_manifest(_v2_dir / "lesson.json", _v2_raw)  # restore
    assert (
        any(f["code"] == "identity-mismatch" for f in _d1_mid_meta["findings"])
        and _d1_mid_meta["profile"] == "legacy-display"
        and _d1_mid_meta["bridge"] is False
    ), "identity-mismatch forces legacy profile and revokes the bridge"
    # PR-bot round 3: a v2 parse can assign the interactive profile and only
    # afterwards reject (no-pages) — the rejected metadata must still report
    # the forced legacy profile, never the parsed interactive value
    bschema.write_manifest(_v2_dir / "lesson.json", dict(_v2_raw, pages=[]))
    _d1_rejp_meta = c.get(f"/learn/lessons/{_v2_id}/preview-meta").json()
    _d1_rejp_prev = c.get(f"/learn/lessons/{_v2_id}/preview")
    bschema.write_manifest(_v2_dir / "lesson.json", _v2_raw)  # restore
    assert (
        _d1_rejp_meta["outcome"] == "rejected"
        and any(f["code"] == "no-pages" for f in _d1_rejp_meta["findings"])
        and _d1_rejp_meta["profile"] == "legacy-display"
        and _d1_rejp_meta["bridge"] is False
        and _d1_rejp_prev.headers.get("content-security-policy") == _CSP_LEG
    ), "late-rejected interactive manifest reports legacy, no bridge"

    # ---- D2: bridge page identity + sandbox tokens (§6.3, ABI doc) ----
    # The metadata is what the parent runtime (learn-bridge.ts) binds its
    # handshake to: identity present exactly for a bridge-eligible manifest's
    # declared, readable page; sandbox tokens mirror the profile-keyed CSP.
    from app.routers.learn import _preview_sandbox as _sandbox_for
    _SANDBOX_LEG = "allow-scripts allow-forms allow-popups allow-downloads"
    # entry pinned: the earlier set_current_entry checks moved this lesson's
    # durable selection to the stage page
    _d2_meta = c.get(
        f"/learn/lessons/{_v2_id}/preview-meta",
        params={"entry": "index.html"}).json()
    assert (
        _d2_meta["bridge"] is True
        and _d2_meta["bridge_page"] == {
            "lesson_uid": _v2["uid"],
            "page_id": _v2_raw["pages"][0]["id"],
            "page_rev": "sha256:" + hashlib.sha256(
                (_v2_dir / "index.html").read_bytes()).hexdigest(),
            # D5: declared questions ride the identity (none on this page)
            "questions": [],
            # F1: declared editor/run identities ride the armed page too.
            "blocks": [],
        }
        and _d2_meta["sandbox"] == "allow-scripts"
    ), "preview-meta carries parent-derived bridge identity for an eligible page"
    _d2_meta_p2 = c.get(
        f"/learn/lessons/{_v2_id}/preview-meta",
        params={"entry": "related/01-stage.html"}).json()
    assert (
        _d2_meta_p2["bridge_page"]["page_id"] == _v2_raw["pages"][1]["id"]
        and _d2_meta_p2["bridge_page"]["page_rev"] == "sha256:" + hashlib.sha256(
            (_v2_dir / "related" / "01-stage.html").read_bytes()).hexdigest()
    ), "bridge identity is per page: second declared page gets its own id + rev"
    # a page edit moves the reload token AND page_rev together — the parent
    # re-binds on the token, so the identity it arms always describes the
    # bytes the displayed document was served from
    _d2_orig = (_v2_dir / "index.html").read_bytes()
    (_v2_dir / "index.html").write_bytes(b"<html>Vera Example index edited</html>")
    _d2_meta_ed = c.get(
        f"/learn/lessons/{_v2_id}/preview-meta",
        params={"entry": "index.html"}).json()
    (_v2_dir / "index.html").write_bytes(_d2_orig)  # restore
    assert (
        _d2_meta_ed["version"] != _d2_meta["version"]
        and _d2_meta_ed["bridge_page"]["page_rev"] == "sha256:" + hashlib.sha256(
            b"<html>Vera Example index edited</html>").hexdigest()
    ), "page edit moves reload token and page_rev together"
    # drain D2 L2: a byte replacement that RESTORES the old mtime must still
    # move the token — for bridge pages it is content-bound (digest folded
    # in), so the client's version-equality check tracks bytes, not a
    # restorable timestamp
    _d2_st = (_v2_dir / "index.html").stat()
    (_v2_dir / "index.html").write_bytes(b"<html>Vera Example mtime-preserved swap</html>")
    _os.utime(_v2_dir / "index.html", ns=(_d2_st.st_atime_ns, _d2_st.st_mtime_ns))
    _d2_meta_swp = c.get(
        f"/learn/lessons/{_v2_id}/preview-meta",
        params={"entry": "index.html"}).json()
    (_v2_dir / "index.html").write_bytes(_d2_orig)  # restore
    assert (
        _d2_meta_swp["version"] != _d2_meta["version"]
        and _d2_meta_swp["version"].startswith(f"{_d2_st.st_mtime_ns}:")
        and _d2_meta_swp["bridge_page"]["page_rev"] == "sha256:" + hashlib.sha256(
            b"<html>Vera Example mtime-preserved swap</html>").hexdigest()
    ), "mtime-preserving byte swap still moves the reload token"
    # the Learn page's data-version must be the same content-bound token the
    # poll answers with, or every bridge page would reload on its first poll
    _d2_meta_now = c.get(
        f"/learn/lessons/{_v2_id}/preview-meta",
        params={"entry": "related/01-stage.html"}).json()
    assert (
        f'data-version="{_d2_meta_now["version"]}"' in c.get(
        f"/learn?lesson={_v2_id}").text
    ), "rendered data-version equals the poll's content-bound token"
    # a stale selection falls back to a DECLARED page (§4.2), so the identity
    # in the metadata describes the fallback actually rendered, never the
    # requested ghost
    _d2_ghost = c.get(
        f"/learn/lessons/{_v2_id}/preview-meta",
        params={"entry": "related/99-ghost.html"}).json()
    assert (
        _d2_ghost["bridge"] is True
        and _d2_ghost["bridge_page"]["page_id"] == _v2_raw["pages"][0]["id"]
    ), "stale selection: identity describes the rendered fallback page"
    # every no-bridge path carries no identity, and the sandbox tokens follow
    # the effective profile (legacy stays the historical token set)
    _d2_v1_meta = c.get(f"/learn/lessons/{_v1_id}/preview-meta").json()
    _d2_rej_meta = c.get(f"/learn/lessons/{_rej_id}/preview-meta").json()
    assert (
        _d2_v1_meta["bridge_page"] is None
        and _d2_v1_meta["sandbox"] == _SANDBOX_LEG
        and _d2_rej_meta["bridge_page"] is None
        and _d2_rej_meta["sandbox"] == _SANDBOX_LEG
    ), "v1 and rejected bundles: no bridge identity, legacy sandbox tokens"
    assert (
        _sandbox_for("weird-unregistered") == "allow-scripts"
        and _sandbox_for("legacy-display") == _SANDBOX_LEG
        and _sandbox_for("interactive-local-v1") == "allow-scripts"
    ), "unregistered profile selects the narrow sandbox tokens"
    # the Learn page renders the iframe sandbox attribute from the profile
    # and loads the Learn-only bridge runtime as a module
    _d2_learn_int = c.get(f"/learn?lesson={_v2_id}").text
    _d2_learn_leg = c.get(f"/learn?lesson={_v1_id}").text
    assert (
        'sandbox="allow-scripts"' in _d2_learn_int
        and f'sandbox="{_SANDBOX_LEG}"' in _d2_learn_leg
    ), "learn.html: iframe sandbox attribute follows the profile"
    assert (
        'type="module"' in _d2_learn_int
        and "learn-bridge.js" in _d2_learn_int
    ), "learn.html loads learn-bridge.js as a module"
    # the inline early-load observer must sit in the document so the late-
    # fetched module can distinguish a settled document from a pending
    # initial navigation (PR-55 round 2)
    assert (
        "this.dataset.loaded" in _d2_learn_int
        and 'addEventListener("load"' in _d2_learn_int
    ), "learn.html carries the inline early-load observer"
    # the poll moved out of app.js — one runtime owns reload AND handshake
    _d2_appjs = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    assert (
        "lesson-preview-frame" not in _d2_appjs
    ), "app.js no longer touches the preview frame"
    # structural anchors in the parent runtime: source-of-truth .ts and the
    # committed tsc emit (#42) both carry the membrane's key guards
    _d2_ts = (ROOT / "app" / "static" / "src" / "learn-bridge.ts").read_text(encoding="utf-8")
    _d2_js = (ROOT / "app" / "static" / "learn-bridge.js").read_text(encoding="utf-8")
    for _d2_name, _d2_text in (("learn-bridge.ts", _d2_ts), ("learn-bridge.js", _d2_js)):
        assert (
            "GENERATED-SOURCE NOTICE" in _d2_text
            and "ev.source !== child" in _d2_text
            and "new MessageChannel()" in _d2_text
            and "ABI_VERSION = 1" in _d2_text
            and 'msg["ephemeris"] !== "lesson-bridge"' in _d2_text
            and 'want.includes("attempts")' in _d2_text
            and "MAX_PORT_BYTES = 512 * 1024" in _d2_text
            and "serializedByteLength" in _d2_text
            and "new TextEncoder()" in _d2_text
        ), f"{_d2_name}: handshake membrane anchors"
    assert (
        "app/static/learn-bridge.js linguist-generated=true"
        in (ROOT / ".gitattributes").read_text(encoding="utf-8")
        and "app/static/terminal.js linguist-generated=true"
        in (ROOT / ".gitattributes").read_text(encoding="utf-8")
    ), ".gitattributes marks both emitted runtimes as generated"
    _ci_workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    _ci_npm = _ci_workflow.find("run: npm ci")
    _ci_verify = _ci_workflow.find("run: uv run pytest")
    assert (
        0 <= _ci_npm < _ci_verify
    ), "CI installs the pinned TypeScript toolchain before verification"
    # committed emit freshness: recompile to a scratch dir and byte-compare.
    # Clean CI installs the lockfile before this point. A local Python-only run
    # may still omit the dev toolchain, but CI must never silently skip the
    # source-to-served-artifact integrity gate.
    _d2_tsc = ROOT / "node_modules" / ".bin" / "tsc"
    if _d2_tsc.exists():
        _d2_out = Path(tempfile.mkdtemp(prefix="al-verify-tsc-"))
        _d2_cp = subprocess.run(
            [str(_d2_tsc), "-p", str(ROOT), "--outDir", str(_d2_out)],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        assert (
            _d2_cp.returncode == 0
            and (_d2_out / "learn-bridge.js").read_bytes() == _d2_js.encode("utf-8")
        ), (
            "committed learn-bridge.js matches a fresh tsc emit (#42)"
            + "  -- "
            + (
                _d2_cp.stdout + _d2_cp.stderr
            )
        )
        assert (
            _d2_cp.returncode == 0
            and (_d2_out / "terminal.js").read_bytes()
            == terminal_js.encode("utf-8")
        ), (
            "committed terminal.js matches a fresh tsc emit (#42)"
            + "  -- "
            + (
                _d2_cp.stdout + _d2_cp.stderr
            )
        )
        _d2_mjs = _d2_out / "learn-bridge.mjs"
        _d2_mjs.write_text(_d2_js, encoding="utf-8")
        _d2_sha_cp = subprocess.run(
            [
                "node", "--input-type=module", "-e",
                """
globalThis.document = {getElementById: () => null};
const {sha256Hex} = await import(process.argv[1]);
const encode = new TextEncoder();
process.stdout.write(JSON.stringify([
  sha256Hex(encode.encode("")),
  sha256Hex(encode.encode("abc")),
  sha256Hex(encode.encode("🪐 orbit")),
  sha256Hex(encode.encode("x".repeat(70000))),
]));
""",
                _d2_mjs.as_uri(),
            ],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        _d2_sha_expected = [
            hashlib.sha256(value).hexdigest()
            for value in (b"", b"abc", "🪐 orbit".encode("utf-8"), b"x" * 70000)
        ]
        assert (
            _d2_sha_cp.returncode == 0
            and json.loads(_d2_sha_cp.stdout) == _d2_sha_expected
        ), (
            "emitted dependency-free SHA-256 matches standard vectors"
            + "  -- "
            + (
                _d2_sha_cp.stdout + _d2_sha_cp.stderr
            )
        )
    else:
        if os.environ.get("CI"):
            assert (
                False
            ), (
                "CI has the repo-local TypeScript compiler for emit freshness"
                + "  -- "
                + (
                    "node_modules/.bin/tsc missing; run npm ci before verify.py"
                )
            )
        else:
            print("[info] tsc not installed; emit-freshness check skipped (npm ci to enable)")

    # ---- D4: lesson attempts — authority, projection, endpoint semantics ----
    # (learn-bundle-spec.md §6 / §8, docs/lesson-attempts-api.md)
    from uuid import uuid4 as _uuid4
    from app.services import attempts as attempts_svc
    _at_conn = get_conn()
    try:
        _at_id = lessons_svc.create_lesson(_at_conn, "Attempt Backend Demo")
        _at = lessons_svc.get_lesson(_at_conn, _at_id)
    finally:
        _at_conn.close()
    _at_dir = Path(lessons_svc.LESSONS_DIR) / _at["slug"]
    _at_raw = json.loads((_at_dir / "lesson.json").read_text(encoding="utf-8"))
    _at_pg = _at_raw["pages"][0]["id"]
    _at_raw["pages"].append({"id": "pg_atsecond01", "path": "related/01-next.html"})
    _at_raw["questions"] = [
        {"id": "q_atpredict1", "page": _at_pg, "kind": "prediction"},
        {"id": "q_atmoved001", "page": "pg_atsecond01"},
    ]
    bschema.write_manifest(_at_dir / "lesson.json", _at_raw)
    (_at_dir / "index.html").write_text(
        "<html>Vera Example attempt page</html>", encoding="utf-8")
    (_at_dir / "related" / "01-next.html").write_text(
        "<html>Vera Example next stage</html>", encoding="utf-8")
    _at_rev = "sha256:" + hashlib.sha256((_at_dir / "index.html").read_bytes()).hexdigest()
    _at_rev2 = "sha256:" + hashlib.sha256(
        (_at_dir / "related" / "01-next.html").read_bytes()).hexdigest()
    _at_url = f"/learn/lessons/{_at_id}/attempts"
    _at_proj = _at_dir / "attempts.jsonl"
    _at_body = {"question_id": "q_atpredict1", "page_id": _at_pg, "page_rev": _at_rev,
                "answer": "Vera Example: I predict it prints hello.",
                "idempotency_key": "vera-req-1"}
    attempts_svc._reset_rate_limit()

    def _at_rows():
        _c = get_conn()
        try:
            return [dict(r) for r in _c.execute(
                "SELECT * FROM lesson_attempts WHERE lesson_id = ? "
                "ORDER BY created_at, attempt_id", (_at_id,)).fetchall()]
        finally:
            _c.close()

    # recorded: row + ledger event in ONE committed transaction (§6.1),
    # projection appended synchronously
    _at_r1 = c.post(_at_url, json=_at_body)
    _at_j1 = _at_r1.json()
    assert (
        _at_r1.status_code == 200 and _at_j1["result"] == "recorded"
        and _at_j1["stale"] is False and _at_j1["projection"] == "projected"
        and _at_j1["attempt_number"] == 1
    ), "attempt recorded: durable + projected, fresh revision is not stale"
    def _at_events():
        _c = get_conn()
        try:
            return _c.execute(
                "SELECT uuid, payload_json FROM events "
                "WHERE type = 'lesson_attempt' ORDER BY id").fetchall()
        finally:
            _c.close()

    _at_row1 = _at_rows()[0]
    _at_ev = _at_events()
    _at_ev1 = json.loads(_at_ev[-1]["payload_json"])
    assert (
        len(_at_ev) == 1 and _at_ev[-1]["uuid"] == _at_row1["event_uuid"]
        and _at_row1["attempt_id"] == _at_j1["attempt_id"]
    ), "attempt row and lesson_attempt event share one txn + event uuid (B4)"
    assert (
        _at_ev1["lesson_uid"] == _at["uid"] and _at_ev1["lesson_id"] == _at_id
        and _at_ev1["slug"] == _at["slug"]
        and _at_ev1["attempt_id"] == _at_j1["attempt_id"]
        and _at_ev1["page_id"] == _at_pg and _at_ev1["question_id"] == "q_atpredict1"
        and _at_ev1["page_rev"] == _at_rev and _at_ev1["stale"] is False
        and "title" not in _at_ev1 and "pages" not in _at_ev1
    ), "lesson_attempt event payload follows the §8 echo policy"
    _at_line1 = json.loads(_at_proj.read_text(encoding="utf-8").splitlines()[0])
    assert (
        list(_at_line1.keys()) == ["kind", "v", "attempt_id", "event_uuid",
                                   "lesson_uid", "page_id", "question_id",
                                   "page_rev", "answer", "created_at", "stale"]
        and _at_line1["kind"] == "attempt" and _at_line1["v"] == 1
        and _at_line1["attempt_id"] == _at_row1["attempt_id"]
        and _at_line1["event_uuid"] == _at_row1["event_uuid"]
        and _at_line1["created_at"] == _at_row1["created_at"]
        and _at_line1["created_at"].endswith("+00:00")
    ), "projection record carries the §6.2 shape in exact field order"

    # idempotency (§6.3): replay returns the original, writes nothing
    _at_r1b = c.post(_at_url, json=_at_body)
    _at_j1b = _at_r1b.json()
    assert (
        _at_r1b.status_code == 200 and _at_j1b["result"] == "duplicate"
        and _at_j1b["attempt_id"] == _at_j1["attempt_id"]
        and "projection" not in _at_j1b and "attempt_number" not in _at_j1b
        and len(_at_rows()) == 1 and len(_at_events()) == 1
        and len(_at_proj.read_text(encoding="utf-8").splitlines()) == 1
    ), "idempotent replay: duplicate, original attempt_id, nothing written"
    # same key, different question/page: distinct conflict, never coalesced
    _at_conf = c.post(_at_url, json=dict(
        _at_body, question_id="q_atmoved001", page_id="pg_atsecond01",
        page_rev=_at_rev2))
    assert (
        _at_conf.status_code == 409
        and _at_conf.json()["error"] == "idempotency-conflict"
        and len(_at_rows()) == 1
    ), "idempotency-conflict is distinct and writes nothing"
    # §6.3 replay precedes record-time refusals (PR-57 round 1): after the
    # question is retired from the manifest, retrying the SAME submission
    # still returns the original durable attempt — only a NEW key sees the
    # unknown-question reject
    bschema.write_manifest(_at_dir / "lesson.json", dict(_at_raw, questions=[]))
    _at_rp = c.post(_at_url, json=_at_body)
    _at_rp_new = c.post(_at_url, json=dict(_at_body, idempotency_key="vera-ret-1"))
    bschema.write_manifest(_at_dir / "lesson.json", _at_raw)  # restore
    assert (
        _at_rp.status_code == 200 and _at_rp.json()["result"] == "duplicate"
        and _at_rp.json()["attempt_id"] == _at_j1["attempt_id"]
        and _at_rp_new.status_code == 422
        and _at_rp_new.json()["error"] == "unknown-question"
    ), "replay survives question retirement; a fresh key rejects"

    # slug alias records against the same lesson; uid comes from the DB row
    _at_r2 = c.post(f"/learn/lessons/by-slug/{_at['slug']}/attempts",
                    json=dict(_at_body, idempotency_key="vera-req-2",
                              answer="Vera Example: second thought."))
    assert (
        _at_r2.status_code == 200 and _at_r2.json()["result"] == "recorded"
        and _at_r2.json()["attempt_number"] == 2
        and _at_rows()[-1]["lesson_uid"] == _at["uid"]
    ), "slug-alias route records; attempt_number counts per question"

    # §6.4 staleness matrix, server-derived at record time
    (_at_dir / "index.html").write_text(
        "<html>Vera Example attempt page EDITED</html>", encoding="utf-8")
    _at_r3 = c.post(_at_url, json=dict(_at_body, idempotency_key="vera-req-3"))
    assert (
        _at_r3.status_code == 200 and _at_r3.json()["result"] == "recorded"
        and _at_r3.json()["stale"] is True
    ), "edited page bytes: recorded with stale=true, never dropped"
    _at_r4 = c.post(_at_url, json={
        "question_id": "q_atmoved001", "page_id": "pg_atsecond01",
        "page_rev": _at_rev2, "answer": "Vera Example: bound page, current bytes.",
        "idempotency_key": "vera-req-4"})
    assert (
        _at_r4.status_code == 200 and _at_r4.json()["stale"] is False
    ), "current binding + current bytes on a non-entry page: stale=false"
    _at_r5 = c.post(_at_url, json={
        "question_id": "q_atmoved001", "page_id": _at_pg, "page_rev": _at_rev,
        "answer": "Vera Example: I saw this question on the entry page.",
        "idempotency_key": "vera-req-5"})
    _at_row5 = next(r for r in _at_rows()
                    if r["attempt_id"] == _at_r5.json()["attempt_id"])
    assert (
        _at_r5.status_code == 200 and _at_r5.json()["stale"] is True
        and _at_row5["page_id"] == _at_pg and _at_row5["page_rev"] == _at_rev
    ), "question rebound elsewhere: recorded under the SUBMITTED page, stale"
    (_at_dir / "related" / "01-next.html").unlink()
    _at_r6 = c.post(_at_url, json={
        "question_id": "q_atmoved001", "page_id": "pg_atsecond01",
        "page_rev": _at_rev2, "answer": "Vera Example: file gone now.",
        "idempotency_key": "vera-req-6"})
    assert (
        _at_r6.status_code == 200 and _at_r6.json()["stale"] is True
    ), "bound page file missing: current revision unknowable -> stale=true"

    # identity that does not exist rejects with the mandated distinct response
    _at_unk = c.post(_at_url, json=dict(
        _at_body, question_id="q_neverwas99", idempotency_key="vera-unk-1"))
    assert (
        _at_unk.status_code == 422 and _at_unk.json()["error"] == "unknown-question"
        and all(r["question_id"] != "q_neverwas99" for r in _at_rows())
    ), "undeclared question: distinct unknown-question reject, nothing written"
    assert (
        c.post("/learn/lessons/999999/attempts", json=_at_body).status_code == 404
        and c.post("/learn/lessons/by-slug/no-such-lesson/attempts",
                   json=_at_body).status_code == 404
    ), "unknown lesson id and slug both 404"

    # eligibility fails closed (§5/§9.2): rejected manifest, v1, legacy
    # profile, identity mismatch — each with its own code, nothing written
    _at_rej = c.post(f"/learn/lessons/{_rej_id}/attempts",
                     json=dict(_at_body, idempotency_key="vera-rej-1"))
    _at_v1 = c.post(f"/learn/lessons/{_v1_id}/attempts",
                    json=dict(_at_body, idempotency_key="vera-v1-1"))
    assert (
        _at_rej.status_code == 409
        and _at_rej.json()["error"] == "manifest-rejected"
    ), "rejected manifest refuses attempt writes (manifest-rejected)"
    assert (
        _at_v1.status_code == 409
        and _at_v1.json()["error"] == "attempts-unavailable"
    ), "v1 bundle carries no attempt affordance (attempts-unavailable)"
    bschema.write_manifest(_at_dir / "lesson.json",
                           dict(_at_raw, runtime={"profile": "legacy-display"}))
    _at_leg = c.post(_at_url, json=dict(_at_body, idempotency_key="vera-leg-1"))
    assert (
        _at_leg.status_code == 409
        and _at_leg.json()["error"] == "attempts-unavailable"
    ), "legacy-display v2 refuses attempts (attempts-unavailable)"
    bschema.write_manifest(_at_dir / "lesson.json",
                           dict(_at_raw, lesson_uid=str(_uuid4())))
    _at_mid = c.post(_at_url, json=dict(_at_body, idempotency_key="vera-mid-1"))
    assert (
        _at_mid.status_code == 409
        and _at_mid.json()["error"] == "identity-mismatch"
    ), "manifest uid != DB uid refuses attempts (identity-mismatch)"
    bschema.write_manifest(_at_dir / "lesson.json", _at_raw)  # restore

    # body admission + grammar limits (docs/lesson-attempts-api.md)
    assert (
        c.post(_at_url, json=_at_body,
               headers={"Origin": "null"}).status_code == 403
        and c.post(_at_url, json=_at_body,
                   headers={"Origin": "http://evil.example"}).status_code == 403
        and c.post(_at_url, json=dict(_at_body, idempotency_key="vera-req-1"),
                   headers={"Origin": "http://testserver"}).status_code == 200
    ), "attempt route sits behind the B2 write guard (Origin null / cross)"
    assert (
        c.post(_at_url, content=b"question_id=x",
               headers={"content-type": "application/x-www-form-urlencoded"}
               ).status_code == 415
        and c.post(_at_url, content=b"not json {",
                   headers={"content-type": "application/json"}
                   ).status_code == 400
        and c.post(_at_url, json=[1, 2, 3]).status_code == 400
    ), "non-JSON content type is 415; malformed JSON body is 400"
    assert (
        c.post(_at_url, content=b"{" + b" " * (300 * 1024),
        headers={"content-type": "application/json"}).status_code == 413
    ), "oversized body is 413 before any parsing"

    # Exercise the ASGI app directly so parser-specific Content-Length /
    # Transfer-Encoding behavior cannot hide a dishonest declared length. The
    # fifth 64-KiB chunk crosses the 256-KiB cap; the remaining three chunks
    # must never be requested. Both public aliases share this admission path.
    import asyncio as _at_asyncio

    async def _at_direct_asgi(path, declared_length, chunks):
        sent = []
        consumed = 0

        async def receive():
            nonlocal consumed
            if consumed >= len(chunks):
                return {"type": "http.disconnect"}
            body = chunks[consumed]
            consumed += 1
            return {
                "type": "http.request",
                "body": body,
                "more_body": consumed < len(chunks),
            }

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"testserver"),
                (b"content-type", b"application/json"),
                (b"content-length", str(declared_length).encode("ascii")),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        await app(scope, receive, send)
        status = next(
            message["status"] for message in sent
            if message["type"] == "http.response.start"
        )
        return status, consumed

    _at_chunks = [b"x" * (64 * 1024) for _ in range(8)]
    _at_stream_id = _at_asyncio.run(
        _at_direct_asgi(_at_url, 1, _at_chunks))
    _at_stream_slug = _at_asyncio.run(_at_direct_asgi(
        f"/learn/lessons/by-slug/{_at['slug']}/attempts", 1, _at_chunks))
    assert (
        _at_stream_id == (413, 5) and _at_stream_slug == (413, 5)
    ), "attempt aliases abort dishonest multi-chunk bodies mid-stream"

    _at_negative_id = _at_asyncio.run(
        _at_direct_asgi(_at_url, -1, [b"{}"]))
    _at_negative_slug = _at_asyncio.run(_at_direct_asgi(
        f"/learn/lessons/by-slug/{_at['slug']}/attempts", -1, [b"{}"]))
    assert (
        _at_negative_id == (400, 0) and _at_negative_slug == (400, 0)
    ), "attempt aliases reject negative Content-Length before body reads"

    # deep nesting under the byte cap raises RecursionError inside json.loads
    # (PR-57 round 4) — still the documented invalid-json 400, never a 500
    _at_deep = c.post(_at_url, content=b"[" * 20000 + b"]" * 20000,
                      headers={"content-type": "application/json"})
    assert (
        _at_deep.status_code == 400
        and _at_deep.json()["error"] == "invalid-json"
    ), "deeply nested JSON body is invalid-json, not a crash"
    _at_badrev = c.post(_at_url, json=dict(
        _at_body, page_rev="sha256:nothex", idempotency_key="vera-bad-1"))
    _at_badkey = c.post(_at_url, json=dict(
        _at_body, idempotency_key="ctrl\x01char"))
    assert (
        _at_badrev.status_code == 400
        and _at_badrev.json()["error"] == "invalid-page-rev"
        and _at_badkey.status_code == 400
        and _at_badkey.json()["error"] == "invalid-idempotency-key"
    ), "grammar violations get their own codes"
    # $-anchored .match accepts a trailing newline (PR-57 round 8): the id
    # grammars are \Z-anchored, so "pg_x\n"-style identities never reach the
    # row or the projection
    assert (
        c.post(_at_url, json=dict(_at_body, page_id=_at_pg + "\n",
                                  idempotency_key="vera-nl-1")
               ).json().get("error") == "invalid-page-id"
        and c.post(_at_url, json=dict(_at_body, page_rev=_at_rev + "\n",
                                      idempotency_key="vera-nl-2")
                   ).json().get("error") == "invalid-page-rev"
        and c.post(_at_url, json=dict(_at_body,
                                      question_id="q_atpredict1\n",
                                      idempotency_key="vera-nl-3")
                   ).json().get("error") == "invalid-question-id"
    ), "trailing newline in identity fields is rejected by the grammar"
    assert (
        c.post(_at_url, json=dict(
        _at_body, answer="x" * (attempts_svc.MAX_ANSWER_BYTES + 1),
        idempotency_key="vera-big-1")).json().get("error") == "answer-too-large"
    ), "answer over 32 KiB UTF-8 is answer-too-large"
    # §6.2 whole-line bound: a within-budget answer whose JSON escaping blows
    # the 64 KiB projection line is refused, not recorded-then-unprojectable
    assert (
        c.post(_at_url, json=dict(
        _at_body, answer="\n" * 32700,
        idempotency_key="vera-line-1")).json().get("error") == "answer-too-large"
    ), "answer that escapes past the 64 KiB line bound is refused"
    # a lone surrogate survives json.loads but can never be written as UTF-8
    _at_sur = json.dumps(dict(_at_body, answer="SURROGATE",
                              idempotency_key="vera-sur-1")).replace(
        '"SURROGATE"', '"\\ud800"')
    assert (
        c.post(_at_url, content=_at_sur.encode("utf-8"),
        headers={"content-type": "application/json"}
        ).json().get("error") == "invalid-answer"
    ), "lone-surrogate answer is invalid-answer, not a crash"

    # crash boundaries: the authority write survives a dead projection, the
    # response says so, and the next write reconciles the file from SQLite.
    # Deterministic fault injection (PR-57 round 5): failing the projection
    # path by NAME kills both the O_APPEND fast path (os.open) and the
    # atomic rebuild (os.replace onto the projection) — POSIX modes would
    # not stop uid 0 when the suite runs in a root test container.
    from unittest import mock as _mock
    _at_real_open2 = _os.open
    _at_real_replace = _os.replace

    def _at_proj_open_down(path, *args, **kw):
        if str(path).endswith(attempts_svc.PROJECTION_NAME):
            raise OSError(5, "Input/output error")
        return _at_real_open2(path, *args, **kw)

    def _at_proj_replace_down(src, dst, *args, **kw):
        if str(dst).endswith(attempts_svc.PROJECTION_NAME):
            raise OSError(5, "Input/output error")
        return _at_real_replace(src, dst, *args, **kw)

    with _mock.patch("os.open", side_effect=_at_proj_open_down), \
            _mock.patch("os.replace", side_effect=_at_proj_replace_down):
        _at_pend = c.post(_at_url, json=dict(
            _at_body, idempotency_key="vera-pend-1",
            answer="Vera Example: projection is down."))
    assert (
        _at_pend.status_code == 200 and _at_pend.json()["result"] == "recorded"
        and _at_pend.json()["projection"] == "pending"
        and any(r["attempt_id"] == _at_pend.json()["attempt_id"]
                for r in _at_rows())
    ), "projection failure: attempt durable, response says pending"
    _at_heal = c.post(_at_url, json=dict(
        _at_body, idempotency_key="vera-heal-1",
        answer="Vera Example: back online."))
    _at_lines = _at_proj.read_text(encoding="utf-8").splitlines()
    assert (
        _at_heal.json()["projection"] == "projected"
        and len(_at_lines) == len(_at_rows())
        and [json.loads(ln)["attempt_id"] for ln in _at_lines]
        == [r["attempt_id"] for r in _at_rows()]
    ), "next write reconciles: projection again equals the authority"
    # crash between commit and append (file vanished) and a torn tail
    # (truncated mid-line) both trigger the rebuild instead of a blind append
    _at_proj.unlink()
    c.post(_at_url, json=dict(_at_body, idempotency_key="vera-gone-1"))
    assert (
        len(_at_proj.read_text(encoding="utf-8").splitlines()) == len(_at_rows())
    ), "missing projection file is rebuilt in full"
    _at_whole = _at_proj.read_bytes()
    _at_proj.write_bytes(_at_whole[: len(_at_whole) // 2])  # torn mid-line
    c.post(_at_url, json=dict(_at_body, idempotency_key="vera-torn-1"))
    _at_lines2 = _at_proj.read_text(encoding="utf-8").splitlines()
    assert (
        len(_at_lines2) == len(_at_rows())
        and all(json.loads(ln)["kind"] == "attempt" for ln in _at_lines2)
    ), "truncated projection is rebuilt: every line parses, counts match"
    # the public reconcile entry point rebuilds from scratch, idempotently
    _at_proj.write_text("junk that is not jsonl\n", encoding="utf-8")
    _at_conn = get_conn()
    try:
        _at_rec_ok = attempts_svc.reconcile_projection(_at_conn, _at)
        _at_rec_text = _at_proj.read_text(encoding="utf-8")
        _at_rec_ok2 = attempts_svc.reconcile_projection(_at_conn, _at)
    finally:
        _at_conn.close()
    _at_rec_after = _at_proj.read_text(encoding="utf-8")
    assert (
        _at_rec_ok and _at_rec_ok2
        and _at_rec_text == _at_rec_after
        and len(_at_rec_text.splitlines()) == len(_at_rows())
    ), (
        "reconcile_projection rebuilds from the authority and is idempotent"
        + "  -- "
        + (
            f"ok={_at_rec_ok} ok2={_at_rec_ok2} "
            f"stable={_at_rec_text == _at_rec_after} "
            f"lines={len(_at_rec_text.splitlines())} rows={len(_at_rows())}"
        )
    )

    # Issue #25: fcntl is imported at the point of use, so a platform without it
    # must read as an unavailable lock, not a crash. The attempt row is committed
    # before projection runs, so an ImportError escaping past the callers' narrow
    # (OSError, sqlite3.Error) handlers would 500 over an attempt that WAS
    # recorded; both entry points must degrade to False ("projection: pending").
    class _NoFcntlFinder(_importlib_abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "fcntl":
                raise ImportError("no module named 'fcntl' on this platform")
            return None

    _at_saved_fcntl = sys.modules.pop("fcntl", None)
    sys.meta_path.insert(0, _NoFcntlFinder())
    _at_conn = get_conn()
    try:
        try:
            with attempts_svc._projection_file_lock(_at):
                pass
            _at_nofcntl_lock = "no error"
        except OSError as exc:              # ImportError is NOT an OSError
            _at_nofcntl_lock = "oserror" if "fcntl" in str(exc) else "other"
        except ImportError:
            _at_nofcntl_lock = "importerror"
        _at_nofcntl_project = attempts_svc._project_attempt(
            _at_conn, _at, _at_rows()[-1])
        _at_nofcntl_reconcile = attempts_svc.reconcile_projection(_at_conn, _at)
    finally:
        _at_conn.close()
        sys.meta_path[:] = [f for f in sys.meta_path
                            if not isinstance(f, _NoFcntlFinder)]
        if _at_saved_fcntl is not None:
            sys.modules["fcntl"] = _at_saved_fcntl
    assert (
        _at_nofcntl_lock == "oserror"
        and _at_nofcntl_project is False
        and _at_nofcntl_reconcile is False
    ), "no fcntl: projection degrades to pending, never a 500 over a saved attempt"

    # A public reconcile must never publish caller-local uncommitted rows.
    # Rejecting an active transaction leaves the prior projection untouched;
    # after rollback, an ordinary committed snapshot still reconciles.
    _at_before_uncommitted = _at_proj.read_bytes()
    _at_conn = get_conn()
    try:
        _at_conn.execute("BEGIN")
        _at_conn.execute(
            "INSERT INTO lesson_attempts "
            "(attempt_id, event_uuid, lesson_id, lesson_uid, "
            " idempotency_key, page_id, question_id, page_rev, "
            " answer, stale, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(_uuid4()), str(_uuid4()), _at_id, _at["uid"],
                "vera-uncommitted-1", _at_pg, "q_atpredict1", _at_rev,
                "Vera Example: this row rolls back.", 0,
                "2031-01-01T00:00:00.000000+00:00",
            ),
        )
        _at_uncommitted_refused = not attempts_svc.reconcile_projection(
            _at_conn, _at)
        _at_conn.rollback()
        _at_committed_reconcile = attempts_svc.reconcile_projection(
            _at_conn, _at)
    finally:
        _at_conn.close()
    assert (
        _at_uncommitted_refused and _at_committed_reconcile
        and _at_proj.read_bytes() == _at_before_uncommitted
    ), "reconcile refuses an active transaction's uncommitted authority"

    # a short write(2) must complete the line, never report `projected` over
    # a torn tail (PR-57 round 1): force the first os.write to land half
    from unittest import mock as _mock
    _at_real_write = _os.write
    _at_split = {"done": False}

    def _at_short_write(fd, data):
        if not _at_split["done"]:
            _at_split["done"] = True
            return _at_real_write(fd, bytes(data)[: max(1, len(bytes(data)) // 2)])
        return _at_real_write(fd, data)

    with _mock.patch("os.write", side_effect=_at_short_write):
        _at_short = c.post(_at_url, json=dict(
            _at_body, idempotency_key="vera-short-1",
            answer="Vera Example: complete a short append."))
    _at_lines3 = _at_proj.read_text(encoding="utf-8").splitlines()
    _at_last = _at_rows()[-1]
    assert (
        _at_short.json().get("projection") == "projected" and _at_split["done"]
        and len(_at_lines3) == len(_at_rows())
        and json.loads(_at_lines3[-1])["attempt_id"] == _at_last["attempt_id"]
    ), "short write(2) is completed by the append loop, file stays whole"

    # §6.1 order guard (PR-57 round 2): a row that does not sort strictly
    # after the projection tail is never blind-appended — the fast path
    # detects the disorder and rebuilds in authority order instead
    with _mock.patch.object(
            attempts_svc, "_utc_now_iso",
            return_value="2000-01-01T00:00:00.000000+00:00"):
        _at_backdated = c.post(_at_url, json=dict(
            _at_body, idempotency_key="vera-backdated-1",
            answer="Vera Example: clock stepped backwards."))
    _at_lines4 = _at_proj.read_text(encoding="utf-8").splitlines()
    assert (
        _at_backdated.json().get("projection") == "projected"
        and [json.loads(ln)["attempt_id"] for ln in _at_lines4]
        == [r["attempt_id"] for r in _at_rows()]
        and json.loads(_at_lines4[0])["created_at"]
        == "2000-01-01T00:00:00.000000+00:00"
    ), "out-of-order append is caught: projection rebuilt in §6.1 order"

    # a planted DIRECTORY at the projection name is a deterministic §6.1
    # collision (PR-57 round 10): empty dirs are removed, non-empty moved
    # aside under a unique name — the projection heals, never stuck pending
    _at_proj.unlink()
    _at_proj.mkdir()
    _at_dircol1 = c.post(_at_url, json=dict(_at_body, idempotency_key="vera-dir-1"))
    assert (
        _at_dircol1.json().get("projection") == "projected"
        and _at_proj.is_file()
        and len(_at_proj.read_text(encoding="utf-8").splitlines())
        == len(_at_rows())
    ), "empty directory at attempts.jsonl is removed and rebuilt over"
    _at_proj.unlink()
    _at_proj.mkdir()
    (_at_proj / "junk.txt").write_text("agent artifact", encoding="utf-8")
    _at_dircol2 = c.post(_at_url, json=dict(_at_body, idempotency_key="vera-dir-2"))
    _at_aside = list(_at_dir.glob("attempts.jsonl.collision-*"))
    assert (
        _at_dircol2.json().get("projection") == "projected"
        and _at_proj.is_file()
        and len(_at_aside) == 1
        and (_at_aside[0] / "junk.txt").read_text(encoding="utf-8")
        == "agent artifact"
    ), "non-empty directory collision is moved aside, content preserved"
    import shutil as _at_shutil
    _at_shutil.rmtree(_at_aside[0])

    # a hard link planted at the projection name passes O_NOFOLLOW+S_ISREG
    # but must never take the fast path (PR-57 round 11): the rebuild
    # replaces the NAME, so nothing leaks through the link's other name
    _at_conn = get_conn()
    try:
        attempts_svc.reconcile_projection(_at_conn, _at)
    finally:
        _at_conn.close()
    _at_linked = _at_proj.read_bytes()
    _at_link_other = _at_dir / "outside-copy.txt"
    _os.link(_at_proj, _at_link_other)  # projection inode now has 2 names
    _at_hl = c.post(_at_url, json=dict(_at_body, idempotency_key="vera-hl-1"))
    assert (
        _at_hl.json().get("projection") == "projected"
        and _at_link_other.read_bytes() == _at_linked
        and _os.stat(_at_proj).st_nlink == 1
        and len(_at_proj.read_text(encoding="utf-8").splitlines())
        == len(_at_rows())
    ), "hard-linked projection is replaced, append never leaks through"
    _at_link_other.unlink()

    # content-verified fast path (PR-57 round 6): the right line COUNT with
    # wrong earlier content is never blind-appended over — the byte-exact
    # prefix comparison fails and the rebuild restores the authority bytes
    _at_conn = get_conn()
    try:
        attempts_svc.reconcile_projection(_at_conn, _at)
        _at_good = _at_proj.read_text(encoding="utf-8").splitlines(keepends=True)
        _at_forged = json.dumps(
            dict(json.loads(_at_good[0]), answer="FORGED"),
            ensure_ascii=False) + "\n"
        _at_proj.write_text(_at_forged + "".join(_at_good[1:-1]),
                            encoding="utf-8")
        _at_content_ok = attempts_svc._project_attempt(
            _at_conn, _at, _at_rows()[-1])
    finally:
        _at_conn.close()
    assert (
        _at_content_ok
        and _at_proj.read_text(encoding="utf-8") == "".join(_at_good)
    ), "forged earlier line with matching count forces the rebuild"

    # A same-inode rewrite between the append descriptor's final fstat and
    # the name seal must not be blessed by the sidecar. The full seal mismatch
    # forces a rebuild from SQLite.
    attempts_svc._reset_rate_limit()
    _at_real_lstat = _os.lstat
    _at_same_inode = {"mutated": False}

    def _at_mutate_before_name_seal(path, *args, **kwargs):
        if Path(path) == _at_proj and not _at_same_inode["mutated"]:
            with _at_proj.open("r+b") as fh:
                original = fh.read(1)
                fh.seek(0)
                fh.write(b"!" if original != b"!" else b"?")
                fh.flush()
                _os.fsync(fh.fileno())
            _at_same_inode["mutated"] = True
        return _at_real_lstat(path, *args, **kwargs)

    with _mock.patch.object(
            attempts_svc.os, "lstat", _at_mutate_before_name_seal):
        _at_same_inode_response = c.post(_at_url, json=dict(
            _at_body, idempotency_key="vera-same-inode-1",
            answer="Vera Example: detect the same-inode rewrite."))
    _at_same_inode_ids = [
        json.loads(line)["attempt_id"]
        for line in _at_proj.read_text(encoding="utf-8").splitlines()
    ]
    assert (
        _at_same_inode["mutated"]
        and _at_same_inode_response.json().get("projection") == "projected"
        and _at_same_inode_ids == [
            row["attempt_id"] for row in _at_rows()
        ]
    ), "post-append same-inode rewrite cannot be sealed as projected"

    # A rewrite immediately after the append helper returns must also be
    # detected: _write_all returns its immediate descriptor seal, so fsync's
    # later seal cannot advance the cursor over concurrently changed bytes.
    attempts_svc._reset_rate_limit()
    _at_real_write_all2 = attempts_svc._write_all
    _at_append_mutation = {"done": False}

    def _at_write_then_mutate(fd, data):
        written_st = _at_real_write_all2(fd, data)
        if (
            bytes(data).startswith(b'{"kind": "attempt"')
            and not _at_append_mutation["done"]
        ):
            _os.pwrite(fd, b"!", 0)
            _os.fsync(fd)
            _at_append_mutation["done"] = True
        return written_st

    with _mock.patch.object(
            attempts_svc, "_write_all", _at_write_then_mutate):
        _at_append_race = c.post(_at_url, json=dict(
            _at_body, idempotency_key="vera-append-race-1",
            answer="Vera Example: append publication raced."))
    assert (
        _at_append_mutation["done"]
        and _at_append_race.json().get("projection") == "projected"
        and [
            json.loads(line)["attempt_id"]
            for line in _at_proj.read_text(encoding="utf-8").splitlines()
        ] == [row["attempt_id"] for row in _at_rows()]
    ), "append-time same-inode rewrite cannot advance the cursor"

    # close(2) surfacing a delayed write error (PR-57 round 3): target the
    # append descriptor specifically now that the cursor sidecar also opens
    # bounded descriptors. The repair rebuild covers the durable row.
    attempts_svc._reset_rate_limit()
    _at_real_close = _os.close
    _at_real_open3 = _os.open
    _at_real_projection_fd = attempts_svc._projection_fd
    _at_close_state = {
        "raised": False,
        "target_fd": None,
        "target_reopened": False,
        "close_before_reopen": 0,
    }

    def _at_tracked_projection_fd(lesson_, flags):
        fd = _at_real_projection_fd(lesson_, flags)
        if flags & _os.O_APPEND:
            _at_close_state["target_fd"] = fd
        return fd

    def _at_track_reopen(*args, **kwargs):
        fd = _at_real_open3(*args, **kwargs)
        if (
            _at_close_state["raised"]
            and fd == _at_close_state["target_fd"]
        ):
            _at_close_state["target_reopened"] = True
        return fd

    def _at_bad_close(fd):
        if (
            fd == _at_close_state["target_fd"]
            and _at_close_state["raised"]
            and not _at_close_state["target_reopened"]
        ):
            _at_close_state["close_before_reopen"] += 1
        _at_real_close(fd)
        if (
            fd == _at_close_state["target_fd"]
            and not _at_close_state["raised"]
        ):
            _at_close_state["raised"] = True
            raise OSError(28, "No space left on device")

    with _mock.patch.object(
            attempts_svc, "_projection_fd", _at_tracked_projection_fd), \
            _mock.patch("os.open", side_effect=_at_track_reopen), \
            _mock.patch("os.close", side_effect=_at_bad_close):
        _at_close = c.post(_at_url, json=dict(
            _at_body, idempotency_key="vera-close-1",
            answer="Vera Example: delayed close failure."))
    _at_lines5 = _at_proj.read_text(encoding="utf-8").splitlines()
    _at_last3 = _at_rows()[-1]
    assert (
        _at_close.json().get("projection") == "projected"
        and _at_close_state["raised"]
        and _at_close_state["close_before_reopen"] == 0
        and len(_at_lines5) == len(_at_rows())
        and json.loads(_at_lines5[-1])["attempt_id"] == _at_last3["attempt_id"]
    ), (
        "close(2) failure never fails the attempt: rebuild covers the append"
        + "  -- "
        + (
            str({
                "response": _at_close.json(),
                "raised": _at_close_state["raised"],
                "target_reopened": _at_close_state["target_reopened"],
                "close_before_reopen": _at_close_state["close_before_reopen"],
                "projection_lines": len(_at_lines5),
                "authority_rows": len(_at_rows()),
                "projection_last": json.loads(_at_lines5[-1])["attempt_id"],
                "authority_last": _at_last3["attempt_id"],
            })
        )
    )

    # Malformed private state is repair input, never a post-commit 500. A
    # recursively nested document stays under the fixed 4-KiB read cap but
    # exceeds Python's JSON nesting depth.
    _at_state_path, _ = attempts_svc._state_paths(_at)
    _at_state_path.write_bytes(b"[" * 1100 + b"]" * 1100)
    _at_recursive_state = c.post(_at_url, json=dict(
        _at_body, idempotency_key="vera-recursive-state-1",
        answer="Vera Example: malformed cursor heals."))
    assert (
        _at_recursive_state.status_code == 200
        and _at_recursive_state.json().get("projection") == "projected"
        and len(_at_proj.read_text(encoding="utf-8").splitlines())
        == len(_at_rows())
    ), "recursive projection cursor state falls back to rebuild"

    # A slow sibling projector never holds the HTTP request indefinitely:
    # lock contention returns pending after the authority commit, then the
    # next append heals both committed rows under the acquired uid lock.
    attempts_svc._reset_rate_limit()
    with attempts_svc._projection_file_lock(_at):
        _at_busy = c.post(_at_url, json=dict(
            _at_body, idempotency_key="vera-busy-lock-1",
            answer="Vera Example: projection lock is busy."))
    _at_busy_heal = c.post(_at_url, json=dict(
        _at_body, idempotency_key="vera-busy-heal-1",
        answer="Vera Example: projection lock is free."))
    assert (
        _at_busy.status_code == 200
        and _at_busy.json().get("projection") == "pending"
        and _at_busy_heal.json().get("projection") == "projected"
        and len(_at_proj.read_text(encoding="utf-8").splitlines())
        == len(_at_rows())
    ), "busy projection lock returns pending and the next append heals"

    # A database restore can leave the private cursor numerically ahead of
    # SQLite. Both the max-id row identity and the projection sort-tail anchor
    # must still exist in authority before an empty unseen-row query is trusted.
    _at_ahead_state = json.loads(_at_state_path.read_text(encoding="ascii"))
    _at_ahead_state["cursor_id"] += 100000
    _at_ahead_state["cursor_attempt_id"] = str(_uuid4())
    _at_state_path.write_text(
        json.dumps(_at_ahead_state), encoding="ascii")
    attempts_svc._reset_rate_limit()
    _at_ahead = c.post(_at_url, json=dict(
        _at_body, idempotency_key="vera-ahead-cursor-1",
        answer="Vera Example: restored authority wins."))
    _at_repaired_state = json.loads(
        _at_state_path.read_text(encoding="ascii"))
    assert (
        _at_ahead.json().get("projection") == "projected"
        and _at_repaired_state["cursor_id"]
        == max(row["id"] for row in _at_rows())
        and [
            json.loads(line)["attempt_id"]
            for line in _at_proj.read_text(encoding="utf-8").splitlines()
        ] == [row["attempt_id"] for row in _at_rows()]
    ), "cursor ahead of restored SQLite authority forces rebuild"

    # Rebuild keeps the rendered temp descriptor open across replace. A
    # same-inode rewrite immediately after publication changes its post-replace
    # seal relative to the rendered snapshot, returns pending, and is healed by
    # the next append instead of becoming trusted cursor state.
    attempts_svc._reset_rate_limit()
    _at_proj.write_text("force a rebuild\n", encoding="utf-8")
    _at_real_replace2 = _os.replace
    _at_rebuild_mutation = {"done": False}

    def _at_replace_then_mutate(src, dst, *args, **kwargs):
        result = _at_real_replace2(src, dst, *args, **kwargs)
        if Path(dst) == _at_proj and not _at_rebuild_mutation["done"]:
            published = _os.stat(_at_proj)
            with _at_proj.open("r+b") as fh:
                original = fh.read(1)
                fh.seek(0)
                fh.write(b"!" if original != b"!" else b"?")
                fh.flush()
                _os.fsync(fh.fileno())
            _os.utime(
                _at_proj,
                ns=(published.st_atime_ns, published.st_mtime_ns),
            )
            _at_rebuild_mutation["done"] = True
        return result

    with _mock.patch.object(
            attempts_svc.os, "replace", _at_replace_then_mutate):
        _at_rebuild_race = c.post(_at_url, json=dict(
            _at_body, idempotency_key="vera-rebuild-race-1",
            answer="Vera Example: rebuild publication raced."))
    _at_rebuild_heal = c.post(_at_url, json=dict(
        _at_body, idempotency_key="vera-rebuild-heal-1",
        answer="Vera Example: rebuild race healed."))
    assert (
        _at_rebuild_mutation["done"]
        and _at_rebuild_race.json().get("projection") == "pending"
        and _at_rebuild_heal.json().get("projection") == "projected"
        and [
            json.loads(line)["attempt_id"]
            for line in _at_proj.read_text(encoding="utf-8").splitlines()
        ] == [row["attempt_id"] for row in _at_rows()]
    ), "rebuild cannot seal a post-replace same-inode rewrite"

    # §6.3 replay wins over refusals even mid-race (PR-57 round 2): a retry
    # whose original is still in flight sees the key uncommitted at the early
    # check, then hits unknown-question after the question was retired — the
    # refusal path re-checks and returns the committed duplicate
    _at_real_roc = attempts_svc._replay_or_conflict
    _at_roc_calls = {"n": 0}

    def _at_roc_once(conn_, lesson_, sub_):
        _at_roc_calls["n"] += 1
        if _at_roc_calls["n"] == 1:
            return None  # simulate: the original write has not committed yet
        return _at_real_roc(conn_, lesson_, sub_)

    bschema.write_manifest(_at_dir / "lesson.json", dict(_at_raw, questions=[]))
    _at_conn = get_conn()
    try:
        with _mock.patch.object(attempts_svc, "_replay_or_conflict",
                                _at_roc_once):
            _at_race = attempts_svc.record_attempt(_at_conn, _at, dict(_at_body))
    finally:
        _at_conn.close()
        bschema.write_manifest(_at_dir / "lesson.json", _at_raw)  # restore
    assert (
        _at_race["result"] == "duplicate"
        and _at_race["attempt_id"] == _at_row1["attempt_id"]
        and _at_roc_calls["n"] == 2
    ), "racing retry beats a manifest refusal: committed duplicate wins"

    # the same re-check covers the rate limit (PR-57 round 11): an original
    # that committed after the early check wins over an exhausted window
    _at_roc_calls["n"] = 0
    attempts_svc._reset_rate_limit()
    _at_rate_saved = attempts_svc.RATE_MAX_PER_WINDOW
    attempts_svc.RATE_MAX_PER_WINDOW = 1
    with attempts_svc._rate_lock:  # window pre-exhausted by the "original"
        attempts_svc._rate[_at["id"]] = attempts_svc.deque(
            [attempts_svc._monotonic()])
    _at_conn = get_conn()
    try:
        with _mock.patch.object(attempts_svc, "_replay_or_conflict",
                                _at_roc_once):
            _at_race429 = attempts_svc.record_attempt(_at_conn, _at,
                                                      dict(_at_body))
    finally:
        _at_conn.close()
        attempts_svc.RATE_MAX_PER_WINDOW = _at_rate_saved
        attempts_svc._reset_rate_limit()
    assert (
        _at_race429["result"] == "duplicate"
        and _at_race429["attempt_id"] == _at_row1["attempt_id"]
        and _at_roc_calls["n"] == 2
    ), "racing retry beats an exhausted window: committed duplicate wins"

    # a duplicate resolved only at the LOCKED re-check refunds its window
    # slot (PR-57 round 12): retries racing a slow original are not new
    # writes and never starve the next real attempt of budget
    _at_roc_calls["n"] = 0
    attempts_svc._reset_rate_limit()
    _at_rate_saved = attempts_svc.RATE_MAX_PER_WINDOW
    attempts_svc.RATE_MAX_PER_WINDOW = 3
    _at_conn = get_conn()
    try:
        with _mock.patch.object(attempts_svc, "_replay_or_conflict",
                                _at_roc_once):
            _at_refund = attempts_svc.record_attempt(_at_conn, _at,
                                                     dict(_at_body))
        _at_window_after = len(attempts_svc._rate.get(_at["id"], ()))
    finally:
        _at_conn.close()
        attempts_svc.RATE_MAX_PER_WINDOW = _at_rate_saved
        attempts_svc._reset_rate_limit()
    assert (
        _at_refund["result"] == "duplicate"
        and _at_roc_calls["n"] == 2
        and _at_window_after == 0
    ), "late-resolved duplicate refunds its rate-limit slot"

    # rate limit: sliding per-lesson window, distinct code + Retry-After;
    # fresh keys spend budget, replays never do (PR-57 round 9) — a retry of
    # the window-exhausting attempt learns its attempt_id, not a 429
    attempts_svc._reset_rate_limit()
    _at_rate_saved = attempts_svc.RATE_MAX_PER_WINDOW
    attempts_svc.RATE_MAX_PER_WINDOW = 3
    try:
        for _rl_i in range(3):
            _at_rl_ok = c.post(_at_url, json=dict(
                _at_body, idempotency_key=f"vera-rl-{_rl_i}"))
        _at_rl_hit = c.post(_at_url, json=dict(
            _at_body, idempotency_key="vera-rl-fresh"))
        _at_rl_replay = c.post(_at_url, json=dict(
            _at_body, idempotency_key="vera-rl-2"))
    finally:
        attempts_svc.RATE_MAX_PER_WINDOW = _at_rate_saved
        attempts_svc._reset_rate_limit()
    assert (
        _at_rl_ok.status_code == 200 and _at_rl_hit.status_code == 429
        and _at_rl_hit.json()["error"] == "rate-limited"
        and _at_rl_hit.headers.get("retry-after") is not None
    ), "rate limit: 429 rate-limited with Retry-After past the window"
    assert (
        _at_rl_replay.status_code == 200
        and _at_rl_replay.json()["result"] == "duplicate"
    ), "replay bypasses an exhausted window: duplicate, not 429"

    # Issue #58 growth proof: build a separate, invented 200-row authority
    # whose answers reproduce the review's multi-megabyte historical prefix.
    # Reconcile must iterate (a fetchall-capable cursor is deliberately denied),
    # while one following append may read only the fixed-size private state and
    # render only its one new line.
    _at_growth_conn = get_conn()
    try:
        _at_growth_id = lessons_svc.create_lesson(
            _at_growth_conn, "Projection Growth Demo")
        _at_growth = lessons_svc.get_lesson(
            _at_growth_conn, _at_growth_id)
        _at_growth_rows = []
        for _grow_i in range(200):
            _grow_attempt = str(_uuid4())
            _grow_created = (
                f"2030-01-01T00:{_grow_i // 60:02d}:"
                f"{_grow_i % 60:02d}.000000+00:00"
            )
            _at_growth_rows.append((
                _grow_attempt, str(_uuid4()), _at_growth_id,
                _at_growth["uid"], f"growth-{_grow_i}",
                "pg_growth0001", "q_growth0001",
                "sha256:" + "0" * 64, "x" * (32 * 1024), 0,
                _grow_created,
            ))
        with _at_growth_conn:
            _at_growth_conn.executemany(
                "INSERT INTO lesson_attempts "
                "(attempt_id, event_uuid, lesson_id, lesson_uid, "
                " idempotency_key, page_id, question_id, page_rev, "
                " answer, stale, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _at_growth_rows,
            )

        class _AtNoFetchAllCursor:
            def __init__(self, cursor):
                self._cursor = cursor

            def __iter__(self):
                return iter(self._cursor)

            def close(self):
                self._cursor.close()

            def fetchall(self):
                raise AssertionError("reconcile must stream, not fetchall")

        class _AtStreamingConnection:
            def __init__(self, conn_):
                self._conn = conn_

            @property
            def in_transaction(self):
                return self._conn.in_transaction

            def execute(self, *args, **kwargs):
                return _AtNoFetchAllCursor(
                    self._conn.execute(*args, **kwargs))

        _at_growth_streamed = attempts_svc.reconcile_projection(
            _AtStreamingConnection(_at_growth_conn), _at_growth)
        _at_growth_proj = (
            Path(lessons_svc.LESSONS_DIR)
            / _at_growth["slug"] / attempts_svc.PROJECTION_NAME
        )

        def _at_growth_insert(index, created_at):
            attempt_id = str(_uuid4())
            event_uuid = str(_uuid4())
            with _at_growth_conn:
                cursor = _at_growth_conn.execute(
                    "INSERT INTO lesson_attempts "
                    "(attempt_id, event_uuid, lesson_id, lesson_uid, "
                    " idempotency_key, page_id, question_id, page_rev, "
                    " answer, stale, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        attempt_id, event_uuid, _at_growth_id,
                        _at_growth["uid"], f"growth-{index}",
                        "pg_growth0001", "q_growth0001",
                        "sha256:" + "0" * 64,
                        f"Vera Example growth tail {index}", 0, created_at,
                    ),
                )
            return dict(_at_growth_conn.execute(
                "SELECT * FROM lesson_attempts WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone())

        _at_growth_next = _at_growth_insert(
            200, "2030-01-01T00:04:00.000000+00:00")
        _at_real_read = _os.read
        _at_real_pread = _os.pread
        _at_real_projection_line = attempts_svc._projection_line
        _at_growth_cost = {
            "read_bytes": 0, "pread_bytes": 0,
            "render_calls": 0, "render_bytes": 0,
        }

        def _at_count_read(fd, size):
            data = _at_real_read(fd, size)
            _at_growth_cost["read_bytes"] += len(data)
            return data

        def _at_count_pread(fd, size, offset):
            data = _at_real_pread(fd, size, offset)
            _at_growth_cost["pread_bytes"] += len(data)
            return data

        def _at_count_projection_line(row):
            line = _at_real_projection_line(row)
            _at_growth_cost["render_calls"] += 1
            _at_growth_cost["render_bytes"] += len(line.encode("utf-8"))
            return line

        with _mock.patch.object(
                attempts_svc.os, "read", side_effect=_at_count_read), \
                _mock.patch.object(
                    attempts_svc.os, "pread", side_effect=_at_count_pread), \
                _mock.patch.object(
                    attempts_svc, "_projection_line",
                    side_effect=_at_count_projection_line):
            _at_growth_fast = attempts_svc._project_attempt(
                _at_growth_conn, _at_growth, _at_growth_next)
        assert (
            _at_growth_streamed
            and _at_growth_proj.stat().st_size > 6 * 1024 * 1024
        ), "projection reconcile streams authority rows in bounded memory"
        assert (
            _at_growth_fast
            and _at_growth_cost["read_bytes"]
            <= attempts_svc.PROJECTION_STATE_MAX_BYTES
            and _at_growth_cost["pread_bytes"]
            <= attempts_svc.MAX_LINE_BYTES
            and _at_growth_cost["render_calls"] == 1
            and _at_growth_cost["render_bytes"]
            <= attempts_svc.MAX_LINE_BYTES
        ), (
            "one projection append has history-independent reads and rendering"
            + "  -- "
            + (
                str(_at_growth_cost)
            )
        )

        # Hold the per-lesson projection lock at the actual append write. A
        # second SQLite connection must still commit an unrelated row before
        # projection is released; BEGIN IMMEDIATE would make this impossible.
        _at_growth_concurrent = _at_growth_insert(
            201, "2030-01-01T00:04:01.000000+00:00")
        _at_concurrent_line = _at_real_projection_line(
            _at_growth_concurrent).encode("utf-8")
        _at_real_write_all = attempts_svc._write_all
        _at_projection_held = threading.Event()
        _at_projection_release = threading.Event()
        _at_projection_result = {}

        def _at_block_projection_write(fd, data):
            if (
                bytes(data) == _at_concurrent_line
                and not _at_projection_held.is_set()
            ):
                _at_projection_held.set()
                if not _at_projection_release.wait(10):
                    raise OSError("projection writer test timed out")
            return _at_real_write_all(fd, data)

        def _at_project_in_thread():
            conn_ = get_conn()
            try:
                _at_projection_result["ok"] = attempts_svc._project_attempt(
                    conn_, _at_growth, _at_growth_concurrent)
            finally:
                conn_.close()

        _at_unrelated_ok = False
        with _mock.patch.object(
                attempts_svc, "_write_all", _at_block_projection_write):
            _at_projection_thread = threading.Thread(
                target=_at_project_in_thread)
            _at_projection_thread.start()
            _at_held_ok = _at_projection_held.wait(10)
            try:
                _at_unrelated_conn = get_conn()
                try:
                    _at_unrelated_conn.execute("PRAGMA busy_timeout = 200")
                    with _at_unrelated_conn:
                        _at_unrelated_conn.execute(
                            "INSERT INTO routine_items "
                            "(title, group_name, active, sort_order, created_at) "
                            "VALUES (?, ?, 1, 0, ?)",
                            (
                                "Projection Lock Probe",
                                "Invented Verification",
                                "2030-01-01T00:00:00+00:00",
                            ),
                        )
                    _at_unrelated_ok = True
                except sqlite3.OperationalError:
                    _at_unrelated_ok = False
                finally:
                    _at_unrelated_conn.close()
            finally:
                _at_projection_release.set()
            _at_projection_thread.join(10)
        assert (
            _at_held_ok and _at_unrelated_ok
            and not _at_projection_thread.is_alive()
            and _at_projection_result.get("ok") is True
        ), "projection append does not hold SQLite's global writer lock"

        # Reproduce the round-10 interleaving: projector A begins a rebuild
        # snapshot, row B commits while A is paused, then projector B waits on
        # the private uid lock. A may publish its older snapshot, but B can only
        # run afterwards and must advance the durable cursor/file through row B.
        _at_growth_proj.write_text("broken projection\n", encoding="utf-8")
        _at_race_snapshot = threading.Event()
        _at_race_release = threading.Event()
        _at_race_results = {}
        _at_race_once = {"paused": False}

        def _at_pause_rebuild_line(row):
            if not _at_race_once["paused"]:
                _at_race_once["paused"] = True
                _at_race_snapshot.set()
                if not _at_race_release.wait(10):
                    raise OSError("stale-rebuild test timed out")
            return _at_real_projection_line(row)

        def _at_race_project(name, row):
            conn_ = get_conn()
            try:
                _at_race_results[name] = attempts_svc._project_attempt(
                    conn_, _at_growth, row)
            finally:
                conn_.close()

        with _mock.patch.object(
                attempts_svc, "_projection_line", _at_pause_rebuild_line):
            _at_race_a = threading.Thread(
                target=_at_race_project,
                args=("a", _at_growth_concurrent),
            )
            _at_race_a.start()
            _at_snapshot_ok = _at_race_snapshot.wait(10)
            _at_growth_late = _at_growth_insert(
                202, "2030-01-01T00:04:02.000000+00:00")
            _at_race_b = threading.Thread(
                target=_at_race_project, args=("b", _at_growth_late))
            _at_race_b.start()
            _at_race_b.join(5)
            _at_b_returned_pending = (
                not _at_race_b.is_alive()
                and _at_race_results.get("b") is False
            )
            _at_race_release.set()
            _at_race_a.join(10)
        _at_race_project("c", _at_growth_late)
        _at_growth_authority_ids = [
            row_["attempt_id"] for row_ in _at_growth_conn.execute(
                "SELECT attempt_id FROM lesson_attempts WHERE lesson_id = ? "
                "ORDER BY created_at, attempt_id",
                (_at_growth_id,),
            )
        ]
        with _at_growth_proj.open(encoding="utf-8") as _at_growth_fh:
            _at_growth_projection_ids = [
                json.loads(line)["attempt_id"] for line in _at_growth_fh
            ]
        assert (
            _at_snapshot_ok and _at_b_returned_pending
            and not _at_race_a.is_alive() and not _at_race_b.is_alive()
            and _at_race_results == {"b": False, "a": True, "c": True}
            and _at_growth_projection_ids == _at_growth_authority_ids
        ), "private uid lock prevents the round-10 stale-rebuild race"
    finally:
        _at_growth_conn.close()

    suite_state.update({
        name: value for name, value in locals().items()
        if name not in {"client", "suite_state"}
    })
