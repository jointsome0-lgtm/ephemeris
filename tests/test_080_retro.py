"""Retro capture verification."""
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

from conftest import ROOT, check, events_of, item_row



def test_retro(client, suite_state):
    from app.db import SCHEMA_VERSION, get_conn, pretty_date, today_str
    from app.main import app

    c = client
    import json as _json
    # --- Retro capture (docs/retro-spec.md, issue #49) ----------------------
    # The period grammar mirrors exp2res services/time_input.py; the journaled
    # full-snapshot payload (incl. retro_uuid) is the future adapter's wire format.

    def retro_row(entry_id: int):
        conn = get_conn()
        try:
            return conn.execute(
                "SELECT * FROM retro_entries WHERE id = ?", (entry_id,)
            ).fetchone()
        finally:
            conn.close()

    nrc_before = len(events_of("retro_entry_created"))
    r = c.post("/retro", data={
        "period": "Q1 2026", "precision": "quarter", "confidence": "medium",
        "project": "ephemeris", "text": "Built the retro capture slice.",
    }, follow_redirects=False)
    check("POST /retro (Mode A) -> 303", r.status_code == 303, str(r.status_code))
    conn = get_conn()
    row_a = conn.execute("SELECT * FROM retro_entries ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    check("retro row exists with a uuid", row_a is not None and bool(row_a["uuid"]))
    check("quarter anchor resolves to the quarter's first instant",
          row_a["period_start"].startswith("2026-01-01T00:00:00")
          and row_a["period_end"] is None, str(row_a["period_start"]))

    r = c.post("/retro", data={
        "period": "2026-05-01/2026-06-15", "precision": "approximate_range",
        "confidence": "low", "project": "   ", "text": "Fuzzy: mostly exp2res spec work.",
    }, headers={"x-partial": "1"})
    check("POST /retro (Mode B) ok + id + uuid",
          r.status_code == 200 and r.json().get("ok") is True
          and "id" in r.json() and "uuid" in r.json(), r.text)
    rid_b = r.json()["id"]
    row_b = retro_row(rid_b)
    check("approximate range keeps both bounds",
          row_b["period_start"].startswith("2026-05-01")
          and row_b["period_end"].startswith("2026-06-15"))
    check("whitespace-only project stored as NULL", row_b["project"] is None)

    created = events_of("retro_entry_created")
    check("retro_entry_created events appended", len(created) == nrc_before + 2,
          str(len(created)))
    payload = _json.loads(created[-1]["payload_json"])
    check("created payload is a full snapshot carrying retro_uuid",
          payload.get("retro_uuid") == row_b["uuid"]
          and payload.get("retro_id") == rid_b
          and payload.get("period_raw") == "2026-05-01/2026-06-15"
          and payload.get("precision") == "approximate_range"
          and payload.get("confidence") == "low"
          and payload.get("text") == "Fuzzy: mostly exp2res spec work."
          and payload.get("archived_at") is None, str(payload))

    def retro_reject(label: str, data: dict) -> None:
        rr = c.post("/retro", data=data, headers={"x-partial": "1"})
        check(f"retro reject: {label}",
              rr.status_code == 422 and rr.json().get("ok") is False, rr.text)

    retro_reject("month 13", {"period": "2026-13", "precision": "month",
                              "confidence": "medium", "text": "x"})
    retro_reject("quarter Q5", {"period": "Q5 2026", "precision": "quarter",
                                "confidence": "medium", "text": "x"})
    retro_reject("week 99", {"period": "2026-W99", "precision": "week",
                             "confidence": "low", "text": "x"})
    retro_reject("reversed range", {"period": "2026-06-15/2026-05-01",
                                    "precision": "date_range", "confidence": "high", "text": "x"})
    retro_reject("range without '/'", {"period": "2026-05-01", "precision": "date_range",
                                       "confidence": "high", "text": "x"})
    retro_reject("space around range separator (exp2res parses endpoints verbatim)",
                 {"period": "2026-05-01/ 2026-06-15", "precision": "date_range",
                  "confidence": "high", "text": "x"})
    retro_reject("unknown precision with a typed period",
                 {"period": "2026-05", "precision": "unknown", "confidence": "low", "text": "x"})
    retro_reject("empty text", {"period": "2026-05", "precision": "month",
                                "confidence": "medium", "text": "   "})
    retro_reject("control chars in text", {"period": "2026-05", "precision": "month",
                                           "confidence": "medium", "text": "a\x00b"})
    retro_reject("C1 control in text", {"period": "2026-05", "precision": "month",
                                        "confidence": "medium", "text": "a\x85b"})
    retro_reject("control char in project", {"period": "2026-05", "precision": "month",
                                             "confidence": "medium", "project": "a\x85b",
                                             "text": "x"})
    retro_reject("bogus precision", {"period": "2026-05", "precision": "sometime",
                                     "confidence": "medium", "text": "x"})
    retro_reject("bogus confidence", {"period": "2026-05", "precision": "month",
                                      "confidence": "sure", "text": "x"})

    r = c.post(f"/retro/{rid_b}/edit", data={
        "period": "2026-05", "precision": "month", "confidence": "medium",
        "text": "Refined memory of the exp2res spec push.",
    }, headers={"x-partial": "1"})
    check("edit (Mode B) ok", r.status_code == 200 and r.json().get("ok") is True, r.text)
    row_b2 = retro_row(rid_b)
    check("edit rewrites fields, keeps uuid, stamps updated_at",
          row_b2["uuid"] == row_b["uuid"] and row_b2["period_raw"] == "2026-05"
          and row_b2["precision"] == "month" and row_b2["updated_at"] is not None)
    upd = events_of("retro_entry_updated")
    upd_payload = _json.loads(upd[-1]["payload_json"]) if upd else {}
    check("retro_entry_updated payload is the complete post-write row",
          len(upd) >= 1 and upd_payload == {
              "retro_uuid": row_b2["uuid"], "retro_id": rid_b,
              "period_raw": row_b2["period_raw"], "precision": row_b2["precision"],
              "confidence": row_b2["confidence"],
              "period_start": row_b2["period_start"], "period_end": row_b2["period_end"],
              "project": row_b2["project"], "text": row_b2["text"],
              "created_at": row_b2["created_at"], "updated_at": row_b2["updated_at"],
              "archived_at": row_b2["archived_at"],
          }, str(upd_payload))

    r = c.post(f"/retro/{rid_b}/archive", follow_redirects=False)
    check("archive -> 303", r.status_code == 303, str(r.status_code))
    arch = events_of("retro_entry_archived")
    check("archive sets archived_at + appends snapshot with archived_at",
          retro_row(rid_b)["archived_at"] is not None and len(arch) == 1
          and _json.loads(arch[-1]["payload_json"])["archived_at"] is not None)
    r = c.post(f"/retro/{rid_b}/archive", follow_redirects=False)
    check("second archive is an idempotent no-op (no duplicate event)",
          r.status_code == 303 and len(events_of("retro_entry_archived")) == 1)
    r = c.get("/retro")
    check("GET /retro 200, active list hides archived entry",
          r.status_code == 200 and "Refined memory" not in r.text
          and "Built the retro capture slice." in r.text, str(r.status_code))
    check("retro page marks the rail active", 'data-rail="retro"' in r.text)
    r = c.get("/retro?archived=1")
    check("archived view shows the archived entry", "Refined memory" in r.text)
    r = c.post(f"/retro/{rid_b}/unarchive", follow_redirects=False)
    check("unarchive -> 303, clears archived_at, appends event",
          r.status_code == 303 and retro_row(rid_b)["archived_at"] is None
          and len(events_of("retro_entry_unarchived")) == 1)

    lines = [_json.loads(line) for line in c.post("/export/jsonl").text.splitlines()]
    retro_lines = [ln for ln in lines if ln["type"] == "retro_entry_created"]
    check("export carries retro_entry_created full-snapshot payloads",
          any(ln["payload"].get("retro_uuid") == row_b["uuid"] for ln in retro_lines),
          str(len(retro_lines)))
    suite_state.update({
        name: value for name, value in locals().items()
        if name not in {"client", "suite_state"}
    })
