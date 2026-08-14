"""Diary tab verification (issue #2, docs/diary-spec.md)."""
from __future__ import annotations

import dataclasses
import json

from conftest import events_of


def test_diary(client, suite_state, monkeypatch):
    from app.db import SCHEMA_VERSION, get_conn, today_str

    c = client
    assert SCHEMA_VERSION >= 20, "diary_entries landed in schema v20"
    conn = get_conn()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION, (
            "database migrated to the current schema"
        )
    finally:
        conn.close()

    def diary_row(entry_id: int):
        conn = get_conn()
        try:
            return conn.execute(
                "SELECT * FROM diary_entries WHERE id = ?", (entry_id,)
            ).fetchone()
        finally:
            conn.close()

    # --- create: Mode A, empty date defaults to today -----------------------
    n_created = len(events_of("diary_entry_created"))
    r = c.post("/diary", data={"text": "Wired the diary capture slice."},
               follow_redirects=False)
    assert r.status_code == 303, f"POST /diary (Mode A) -> 303 -- {r.status_code}"
    conn = get_conn()
    row_a = conn.execute(
        "SELECT * FROM diary_entries ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row_a is not None and bool(row_a["uuid"]), "diary row exists with a uuid"
    assert row_a["entry_date"] == today_str(), "empty date defaults to today"
    assert row_a["private"] == 0 and row_a["tags_json"] == "[]", (
        "defaults: non-private, no tags"
    )

    # --- create: Mode B with the structured fields --------------------------
    r = c.post("/diary", data={
        "entry_date": "2026-08-13",
        "text": "Read about B-trees; I think I understood splitting. #дневник",
        "tags": " atlas , deep-work, atlas, дневник ",
        "atlas_ref": "btrees-node-split",
        "private": "",
    }, headers={"x-partial": "1"})
    assert (
        r.status_code == 200 and r.json().get("ok") is True
        and "id" in r.json() and "uuid" in r.json()
    ), f"POST /diary (Mode B) ok + id + uuid -- {r.text}"
    rid_b = r.json()["id"]
    row_b = diary_row(rid_b)
    assert json.loads(row_b["tags_json"]) == ["atlas", "deep-work", "дневник"], (
        "tags trimmed, deduped keeping order, stored as an opaque list"
        f" -- {row_b['tags_json']}"
    )
    assert row_b["atlas_ref"] == "btrees-node-split" and row_b["private"] == 0

    created = events_of("diary_entry_created")
    assert len(created) == n_created + 2, (
        f"diary_entry_created events appended -- {len(created)}"
    )
    payload_b = json.loads(created[-1]["payload_json"])
    assert payload_b == {
        "diary_uuid": row_b["uuid"], "diary_id": rid_b,
        "entry_date": "2026-08-13",
        "text": row_b["text"],
        "tags": ["atlas", "deep-work", "дневник"],
        "private": False,
        "atlas_ref": "btrees-node-split",
        "created_at": row_b["created_at"], "updated_at": None,
        "archived_at": None,
    }, f"created payload is the full snapshot carrying diary_uuid -- {payload_b}"

    # --- private entry ------------------------------------------------------
    r = c.post("/diary", data={
        "text": "Something for my eyes only.", "private": "on",
    }, headers={"x-partial": "1"})
    assert r.status_code == 200, f"private create -- {r.text}"
    rid_p = r.json()["id"]
    assert diary_row(rid_p)["private"] == 1, "private stored"
    assert json.loads(events_of("diary_entry_created")[-1]["payload_json"])[
        "private"] is True, "private rides the payload as a bool"

    # --- rejections ---------------------------------------------------------
    def diary_reject(label: str, data: dict) -> None:
        rr = c.post("/diary", data=data, headers={"x-partial": "1"})
        assert (
            rr.status_code == 422 and rr.json().get("ok") is False
        ), f"diary reject: {label} -- {rr.status_code} {rr.text}"

    diary_reject("empty text", {"text": "   "})
    diary_reject("bad date", {"text": "x", "entry_date": "2026-13-40"})
    diary_reject("future date", {"text": "x", "entry_date": "2999-01-01"})
    diary_reject("control chars in text", {"text": "a\x00b"})
    diary_reject("C1 control in text", {"text": "a\x85b"})
    diary_reject("control char in tag", {"text": "x", "tags": "a\x85b"})
    diary_reject("control char in atlas_ref", {"text": "x", "atlas_ref": "a\x07b"})
    diary_reject("oversized text", {"text": "x" * 20_001})
    diary_reject("oversized tag", {"text": "x", "tags": "t" * 101})
    diary_reject("too many tags",
                 {"text": "x", "tags": ",".join(f"t{i}" for i in range(21))})
    diary_reject("oversized atlas_ref", {"text": "x", "atlas_ref": "r" * 501})

    # --- edit: append-only ledger, stable uuid ------------------------------
    r = c.post(f"/diary/{rid_b}/edit", data={
        "entry_date": "2026-08-13",
        "text": "Read about B-trees; splitting clicked after drawing it.",
        "tags": "atlas",
        "atlas_ref": "btrees-node-split",
    }, headers={"x-partial": "1"})
    assert r.status_code == 200 and r.json().get("ok") is True, f"edit -- {r.text}"
    row_b2 = diary_row(rid_b)
    assert (
        row_b2["uuid"] == row_b["uuid"] and row_b2["updated_at"] is not None
        and json.loads(row_b2["tags_json"]) == ["atlas"]
    ), "edit rewrites fields, keeps uuid, stamps updated_at"
    upd = events_of("diary_entry_updated")
    upd_payload = json.loads(upd[-1]["payload_json"]) if upd else {}
    assert len(upd) >= 1 and upd_payload == {
        "diary_uuid": row_b2["uuid"], "diary_id": rid_b,
        "entry_date": row_b2["entry_date"], "text": row_b2["text"],
        "tags": ["atlas"], "private": False, "atlas_ref": row_b2["atlas_ref"],
        "created_at": row_b2["created_at"], "updated_at": row_b2["updated_at"],
        "archived_at": None,
    }, f"updated payload is the complete post-write row -- {upd_payload}"

    # --- private is set-only ------------------------------------------------
    r = c.post(f"/diary/{rid_p}/edit", data={
        "text": "Still for my eyes only — tried to clear the flag.",
        "private": "",
    }, headers={"x-partial": "1"})
    assert r.status_code == 200, f"edit of private entry -- {r.text}"
    assert diary_row(rid_p)["private"] == 1, (
        "private survives an edit that omits it — one-way latch"
    )
    assert json.loads(events_of("diary_entry_updated")[-1]["payload_json"])[
        "private"] is True, "latched private rides the update payload"
    r = c.post(f"/diary/{rid_b}/edit", data={
        "entry_date": "2026-08-13",
        "text": "Read about B-trees; splitting clicked after drawing it.",
        "tags": "atlas", "atlas_ref": "btrees-node-split", "private": "on",
    }, headers={"x-partial": "1"})
    assert r.status_code == 200 and diary_row(rid_b)["private"] == 1, (
        "private can be added on an existing entry"
    )

    # The latch must ride the UPDATE itself, not a pre-read row: an edit that
    # raced a concurrent privatization (its existence check read private=0,
    # then the other request committed private=1) must still land private=1.
    from app.services import diary as diary_svc
    conn = get_conn()
    try:
        conn.execute("UPDATE diary_entries SET private = 0 WHERE id = ?", (rid_p,))
        conn.commit()
        stale = diary_svc.get_entry(conn, rid_p)
        assert stale["private"] == 0
        conn.execute("UPDATE diary_entries SET private = 1 WHERE id = ?", (rid_p,))
        conn.commit()
        real_get = diary_svc.get_entry
        calls = {"n": 0}

        def stale_first(inner_conn, entry_id):
            calls["n"] += 1
            return stale if calls["n"] == 1 else real_get(inner_conn, entry_id)

        monkeypatch.setattr(diary_svc, "get_entry", stale_first)
        diary_svc.update_entry(conn, rid_p, text="Race probe — still private.")
        monkeypatch.setattr(diary_svc, "get_entry", real_get)
        assert diary_svc.get_entry(conn, rid_p)["private"] == 1, (
            "stale-read edit cannot clear a concurrently set private flag"
        )
        assert json.loads(events_of("diary_entry_updated")[-1]["payload_json"])[
            "private"] is True, "the raced update journals private=true"
    finally:
        conn.close()

    # --- archive / unarchive ------------------------------------------------
    n_arch = len(events_of("diary_entry_archived"))
    r = c.post(f"/diary/{rid_b}/archive", follow_redirects=False)
    assert r.status_code == 303, f"archive -> 303 -- {r.status_code}"
    assert (
        diary_row(rid_b)["archived_at"] is not None
        and len(events_of("diary_entry_archived")) == n_arch + 1
        and json.loads(events_of("diary_entry_archived")[-1]["payload_json"])[
            "archived_at"] is not None
    ), "archive sets archived_at + appends snapshot"
    r = c.post(f"/diary/{rid_b}/archive", follow_redirects=False)
    assert (
        r.status_code == 303
        and len(events_of("diary_entry_archived")) == n_arch + 1
    ), "second archive is an idempotent no-op (no duplicate event)"

    r = c.get("/diary")
    assert r.status_code == 200, f"GET /diary -- {r.status_code}"
    assert "splitting clicked" not in r.text, "default list hides archived entry"
    assert "Wired the diary capture slice." in r.text, "active entry listed"
    assert 'data-rail="diary"' in r.text, "diary page marks the rail active"
    assert "Today" in r.text, "entries grouped under day headings"
    r = c.get("/diary?archived=1")
    assert "splitting clicked" in r.text, "archived view shows the archived entry"
    r = c.post(f"/diary/{rid_b}/unarchive", follow_redirects=False)
    assert (
        r.status_code == 303 and diary_row(rid_b)["archived_at"] is None
        and len(events_of("diary_entry_unarchived")) == 1
    ), "unarchive -> 303, clears archived_at, appends event"

    # --- nav + palette surface the tab --------------------------------------
    r = c.get("/retro")
    assert 'title="Diary"' in r.text, "rail carries the Diary destination"
    r = c.get("/palette.json", headers={"x-partial": "1"})
    assert {"label": "Diary", "href": "/diary", "icon": "diary"} in r.json()["views"], (
        "command palette indexes the Diary view"
    )

    # --- export: payloads ride unchanged ------------------------------------
    lines = [json.loads(line) for line in c.post("/export/jsonl").text.splitlines()]
    exported = [ln for ln in lines if ln["type"] == "diary_entry_created"
                and ln["payload"].get("diary_uuid") == row_b["uuid"]]
    assert len(exported) == 1 and exported[0]["payload"] == payload_b, (
        "export carries the created snapshot verbatim — tags, private and"
        f" atlas_ref unfiltered -- {exported}"
    )
    assert any(
        ln["type"] == "diary_entry_created" and ln["payload"].get("private") is True
        for ln in lines
    ), "private entries are NOT filtered from the export (full ledger replay)"

    # --- gap-questions strip: config-gated ----------------------------------
    import app.settings as app_settings
    assert app_settings.settings.exp2res_url is None, (
        "test env must not set SELFOS_EXP2RES_URL"
    )
    r = c.get("/diary")
    assert "Questions of the day" not in r.text and "<iframe" not in r.text, (
        "strip absent when SELFOS_EXP2RES_URL is unset"
    )
    strip_url = "http://127.0.0.1:8123/questions?scope=global"
    monkeypatch.setattr(
        app_settings, "settings",
        dataclasses.replace(app_settings.settings, exp2res_url=strip_url))
    r = c.get("/diary")
    assert "Questions of the day" in r.text and strip_url in r.text, (
        "strip renders the configured URL"
    )
    assert "<iframe" in r.text and "sandbox" in r.text, "strip iframe is sandboxed"
    assert "the diary works fine without it" in r.text, (
        "honest unavailable state: the caption says what an empty frame means"
    )

    suite_state.update({
        name: value for name, value in locals().items()
        if name not in {"client", "suite_state", "monkeypatch"}
    })
