"""Assessment, artifact, bridge, run membrane, and migration verification."""
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



def test_assessment_artifact_migration(client, suite_state):
    from app.db import SCHEMA_VERSION, get_conn, pretty_date, today_str
    from app.main import app

    c = client
    _at = suite_state["_at"]
    _at_asyncio = suite_state["_at_asyncio"]
    _at_body = suite_state["_at_body"]
    _at_dir = suite_state["_at_dir"]
    _at_direct_asgi = suite_state["_at_direct_asgi"]
    _at_id = suite_state["_at_id"]
    _at_j1 = suite_state["_at_j1"]
    _at_raw = suite_state["_at_raw"]
    _at_url = suite_state["_at_url"]
    _d2_js = suite_state["_d2_js"]
    _d2_ts = suite_state["_d2_ts"]
    _fx_cases = suite_state["_fx_cases"]
    _fx_dir = suite_state["_fx_dir"]
    _mock = suite_state["_mock"]
    _os = suite_state["_os"]
    _rej_id = suite_state["_rej_id"]
    _shutil = suite_state["_shutil"]
    _v1 = suite_state["_v1"]
    _v1_dir = suite_state["_v1_dir"]
    _v1_id = suite_state["_v1_id"]
    _v2_dir = suite_state["_v2_dir"]
    _v2_id = suite_state["_v2_id"]
    attempts_svc = suite_state["attempts_svc"]
    bschema = suite_state["bschema"]
    db_mod = suite_state["db_mod"]
    lessons_svc = suite_state["lessons_svc"]
    # ---- S1: lesson assessments — the tutor-memory authority layer ---------
    # (S-DESIGN D-S1-1..D-S1-4, docs/lesson-assessments-api.md)
    from uuid import uuid4 as _as_uuid4
    from app.services import assessments as assess_svc

    _as_conn = get_conn()
    try:
        _as_id = lessons_svc.create_lesson(_as_conn, "Assessment Authority Demo")
        _as = lessons_svc.get_lesson(_as_conn, _as_id)
        _as_fold_id = lessons_svc.create_lesson(_as_conn, "Assessment Fold Demo")
        _as_fold = lessons_svc.get_lesson(_as_conn, _as_fold_id)
        _as_probe_id = lessons_svc.create_lesson(_as_conn, "Assessment Check Demo")
        _as_probe = lessons_svc.get_lesson(_as_conn, _as_probe_id)
        _as_arch_id = lessons_svc.create_lesson(_as_conn, "Assessment Archive Demo")
        _as_fp_id = lessons_svc.create_lesson(_as_conn, "Assessment Fingerprint Demo")
    finally:
        _as_conn.close()

    # schema v14: the table, its index, and the columns the memo binds
    _as_schema_conn = get_conn()
    try:
        _as_cols = {r["name"] for r in _as_schema_conn.execute(
            "PRAGMA table_info(lesson_assessments)")}
        _as_indexes = {r["name"] for r in _as_schema_conn.execute(
            "PRAGMA index_list(lesson_assessments)")}
        _as_user_version = _as_schema_conn.execute(
            "PRAGMA user_version").fetchone()[0]
    finally:
        _as_schema_conn.close()
    check("schema v14 adds lesson_assessments with the D-S1-1 columns",
          _as_user_version == SCHEMA_VERSION >= 14
          and _as_cols == {
              "id", "assessment_id", "event_uuid", "lesson_id", "lesson_uid",
              "sitting_id", "mode", "idempotency_key", "fingerprint", "kind",
              "level", "basis", "attempt_id", "question_id", "concepts_json",
              "note", "next_action", "supersedes", "created_at"}
          and {"idx_assessments_lesson_kind",
               "idx_assessments_lesson_seq"} <= _as_indexes)

    # The per-kind CHECKs are schema-level self-enforcement (S-M1): the typed
    # authority must stay structurally valid under restore tooling or a future
    # second writer, not only under the endpoint's validation. Probe them with
    # direct INSERTs on a lesson the endpoint checks never touch.
    def _as_raw_insert(**over):
        row = {
            "assessment_id": str(_as_uuid4()), "event_uuid": str(_as_uuid4()),
            "lesson_id": _as_probe_id, "lesson_uid": _as_probe["uid"],
            "sitting_id": None, "mode": "tutoring",
            "idempotency_key": "raw-" + _as_uuid4().hex,
            "fingerprint": "sha256:" + "0" * 64, "kind": "summary",
            "level": None, "basis": None, "attempt_id": None,
            "question_id": None, "concepts_json": None,
            "note": "Vera Example: raw CHECK probe.", "next_action": None,
            "supersedes": None,
            "created_at": "2030-01-01T00:00:00.000000+00:00",
        }
        row.update(over)
        conn_ = get_conn()
        try:
            with conn_:
                conn_.execute(
                    "INSERT INTO lesson_assessments ("
                    + ", ".join(row) + ") VALUES ("
                    + ", ".join("?" * len(row)) + ")",
                    tuple(row.values()))
            return "accepted"
        except sqlite3.IntegrityError:
            return "rejected"
        finally:
            conn_.close()

    _as_valid_seed = _as_raw_insert()
    _as_check_probes = {
        "review without attempt_id": _as_raw_insert(
            kind="review", level="correct"),
        "review with an evidence level": _as_raw_insert(
            kind="review", level="passed", attempt_id=str(_as_uuid4())),
        "evidence without concepts": _as_raw_insert(
            kind="evidence", level="weak", basis="attempts"),
        # NULL, not just wrong: SQLite's `NULL IN (...)` is NULL and a CHECK
        # passes on NULL, so the constraints spell the IS NOT NULL out
        "evidence without basis": _as_raw_insert(
            kind="evidence", level="weak", concepts_json='["c"]'),
        "evidence without level": _as_raw_insert(
            kind="evidence", basis="live", concepts_json='["c"]'),
        "review without level": _as_raw_insert(
            kind="review", attempt_id=str(_as_uuid4())),
        "evidence with a review level": _as_raw_insert(
            kind="evidence", level="correct", basis="live",
            concepts_json='["c"]'),
        "evidence with an unknown basis": _as_raw_insert(
            kind="evidence", level="weak", basis="rumour",
            concepts_json='["c"]'),
        "summary carrying a level": _as_raw_insert(kind="summary", level="seen"),
        "summary carrying an attempt": _as_raw_insert(
            kind="summary", attempt_id=str(_as_uuid4())),
        "retraction without supersedes": _as_raw_insert(kind="retraction"),
        "retraction carrying concepts": _as_raw_insert(
            kind="retraction", supersedes=str(_as_uuid4()),
            concepts_json='["c"]'),
        "next_action outside a summary": _as_raw_insert(
            kind="review", level="correct", attempt_id=str(_as_uuid4()),
            next_action="do the thing"),
        "basis outside evidence": _as_raw_insert(kind="summary", basis="live"),
        "unknown kind": _as_raw_insert(kind="verdict"),
        "unknown mode": _as_raw_insert(mode="grading"),
        "empty note": _as_raw_insert(note=""),
        # a retraction that DOES satisfy its supersedes conjunct must still be
        # rejected for the fields it may not carry — otherwise the probes above
        # would pass on the supersedes clause alone
        "retraction carrying a level": _as_raw_insert(
            kind="retraction", supersedes=str(_as_uuid4()), level="correct"),
        "retraction carrying an attempt": _as_raw_insert(
            kind="retraction", supersedes=str(_as_uuid4()),
            attempt_id=str(_as_uuid4())),
    }
    check("per-kind CHECK constraints reject rows violating any stated conjunct",
          _as_valid_seed == "accepted"
          and all(outcome == "rejected"
                  for outcome in _as_check_probes.values()),
          f"{_as_valid_seed} / "
          + ", ".join(f"{k}={v}" for k, v in _as_check_probes.items()
                      if v != "rejected"))
    _as_taken_uuid = str(_as_uuid4())
    _as_taken_ref = str(_as_uuid4())
    check("UNIQUE(lesson_id, idempotency_key), event_uuid and assessment_id hold",
          _as_raw_insert(idempotency_key="raw-dup") == "accepted"
          and _as_raw_insert(idempotency_key="raw-dup") == "rejected"
          and _as_raw_insert(event_uuid=_as_taken_uuid) == "accepted"
          and _as_raw_insert(event_uuid=_as_taken_uuid) == "rejected"
          and _as_raw_insert(assessment_id=_as_taken_ref) == "accepted"
          and _as_raw_insert(assessment_id=_as_taken_ref) == "rejected")

    # one recorded attempt to diagnose: the lesson needs a declared question
    _as_dir = Path(lessons_svc.LESSONS_DIR) / _as["slug"]
    _as_raw_manifest = json.loads(
        (_as_dir / "lesson.json").read_text(encoding="utf-8"))
    _as_pg = _as_raw_manifest["pages"][0]["id"]
    _as_raw_manifest["questions"] = [
        {"id": "q_asfirst001", "page": _as_pg, "kind": "prediction"}]
    bschema.write_manifest(_as_dir / "lesson.json", _as_raw_manifest)
    (_as_dir / "index.html").write_text(
        "<html>Vera Example assessment page</html>", encoding="utf-8")
    _as_rev = "sha256:" + hashlib.sha256(
        (_as_dir / "index.html").read_bytes()).hexdigest()
    attempts_svc._reset_rate_limit()
    _as_attempt = c.post(f"/learn/lessons/{_as_id}/attempts", json={
        "question_id": "q_asfirst001", "page_id": _as_pg, "page_rev": _as_rev,
        "answer": "Vera Example: the loop runs three times.",
        "idempotency_key": "vera-as-att-1"}).json()
    _as_attempt_id = _as_attempt["attempt_id"]
    _as_url = f"/learn/lessons/{_as_id}/assessments"
    assess_svc._reset_rate_limit()

    def _as_rows(lesson_id=None):
        conn_ = get_conn()
        try:
            return [dict(r) for r in conn_.execute(
                "SELECT * FROM lesson_assessments WHERE lesson_id = ? ORDER BY id",
                (lesson_id if lesson_id is not None else _as_id,)).fetchall()]
        finally:
            conn_.close()

    def _as_events():
        conn_ = get_conn()
        try:
            return conn_.execute(
                "SELECT uuid, payload_json FROM events "
                "WHERE type = 'lesson_assessment' ORDER BY id").fetchall()
        finally:
            conn_.close()

    _as_review_body = {
        "kind": "review", "level": "partial", "attempt_id": _as_attempt_id,
        "note": "Vera Example: counts the loop edges, not the iterations.",
        "idempotency_key": "vera-as-1"}
    _as_r1 = c.post(_as_url, json=_as_review_body)
    _as_j1 = _as_r1.json()
    _as_row1 = _as_rows()[0]
    check("review recorded: durable row, seq is the rowid, state projected",
          _as_r1.status_code == 200 and _as_j1["result"] == "recorded"
          and _as_j1["assessment_id"] == _as_row1["assessment_id"]
          and _as_j1["seq"] == _as_row1["id"]
          and _as_j1["projection"] == "projected"
          and _as_row1["fingerprint"].startswith("sha256:")
          and _as_row1["mode"] == "tutoring"
          and _as_row1["created_at"].endswith("+00:00")
          and len(_as_row1["created_at"].split(".")[-1]) == len("000000+00:00"))
    check("question_id is copied from the attempt row; sitting_id stays NULL",
          _as_row1["question_id"] == "q_asfirst001"
          and _as_row1["attempt_id"] == _as_attempt_id
          and _as_row1["sitting_id"] is None
          and _as_row1["lesson_uid"] == _as["uid"])
    _as_ev = _as_events()
    _as_ev1 = json.loads(_as_ev[-1]["payload_json"])
    check("row and lesson_assessment event share one txn + event uuid",
          len(_as_ev) == 1 and _as_ev[-1]["uuid"] == _as_row1["event_uuid"])
    # ...and the transaction is real in both directions: a failing ledger append
    # must leave no orphan authority row (a matching uuid alone would also hold
    # if the row were committed first)
    def _as_failing_append(*args, **kwargs):
        raise sqlite3.OperationalError("ledger append refused (probe)")

    with _mock.patch.object(assess_svc, "append_event", _as_failing_append):
        try:
            _as_atomic_raised = None
            c.post(_as_url, json={
                "kind": "summary", "note": "Vera Example: event will fail.",
                "idempotency_key": "vera-as-atomic-1"})
        except sqlite3.OperationalError as exc:
            _as_atomic_raised = exc
    check("a failing ledger append rolls the assessment row back",
          all(r["idempotency_key"] != "vera-as-atomic-1" for r in _as_rows())
          and len(_as_rows()) == 1 and len(_as_events()) == 1)
    check("lesson_assessment payload echoes the full D-S1-1 field list",
          list(_as_ev1.keys()) == [
              "lesson_uid", "lesson_id", "slug", "assessment_id", "seq",
              "kind", "mode", "sitting_id", "level", "basis", "attempt_id",
              "question_id", "concepts", "note", "next_action", "supersedes",
              "created_at"]
          and _as_ev1["lesson_uid"] == _as["uid"] and _as_ev1["lesson_id"] == _as_id
          and _as_ev1["slug"] == _as["slug"] and _as_ev1["seq"] == _as_row1["id"]
          and _as_ev1["question_id"] == "q_asfirst001"
          and _as_ev1["sitting_id"] is None and _as_ev1["mode"] == "tutoring"
          and _as_ev1["note"] == _as_review_body["note"]
          and "title" not in _as_ev1 and "fingerprint" not in _as_ev1)

    # idempotency (D-S1-3): the fingerprint is the whole validated submission
    _as_replay = c.post(_as_url, json=_as_review_body)
    _as_null_replay = c.post(_as_url, json=dict(
        _as_review_body, basis=None, next_action=None, supersedes=None))
    check("replay returns the original assessment_id/seq and writes nothing",
          _as_replay.status_code == 200
          and _as_replay.json() == dict(_as_j1, result="duplicate")
          # an explicit null reads as absent, so it fingerprints identically
          and _as_null_replay.json() == dict(_as_j1, result="duplicate")
          and len(_as_rows()) == 1 and len(_as_events()) == 1)
    # every validated field is part of what the key identifies: a regression
    # that dropped one from the canonical form would silently coalesce a
    # DIFFERENT judgment into the original row
    _as_conflicts = {
        "note": dict(_as_review_body,
                     note="Vera Example: a different diagnosis entirely."),
        "level": dict(_as_review_body, level="incorrect"),
        "mode": dict(_as_review_body, mode="exam"),
        "attempt_id": dict(_as_review_body, attempt_id=str(_as_uuid4())),
        "concepts": dict(_as_review_body, concepts=["loops"]),
        "kind": {"kind": "summary", "note": _as_review_body["note"],
                 "idempotency_key": _as_review_body["idempotency_key"]},
    }
    _as_conflict_out = {name: c.post(_as_url, json=body)
                        for name, body in _as_conflicts.items()}
    _as_ev_conflicts = {
        "basis": {"kind": "evidence", "level": "weak", "basis": "runs",
                  "concepts": ["loops"], "note": "Vera Example: evidence.",
                  "idempotency_key": "vera-as-ev-basis"},
        "next_action": {"kind": "summary", "note": "Vera Example: summary.",
                        "next_action": "Read section two.",
                        "idempotency_key": "vera-as-na"},
        "supersedes": {"kind": "summary", "note": "Vera Example: summary two.",
                       "idempotency_key": "vera-as-sup"},
    }
    # on their own lesson: these three need an original row per key, and the
    # row-count assertions below belong to the main lesson
    _as_fp_url = f"/learn/lessons/{_as_fp_id}/assessments"
    for _as_name, _as_body in _as_ev_conflicts.items():
        c.post(_as_fp_url, json=_as_body)  # the original write for this key
    _as_ev_conflict_out = {
        "basis": c.post(_as_fp_url, json=dict(
            _as_ev_conflicts["basis"], basis="live")),
        "next_action": c.post(_as_fp_url, json=dict(
            _as_ev_conflicts["next_action"], next_action="Read section three.")),
        # a known key with a changed submission conflicts BEFORE the
        # mutable-state refusals: this supersedes names another lesson's row
        "supersedes": c.post(_as_fp_url, json=dict(
            _as_ev_conflicts["supersedes"], supersedes=_as_j1["assessment_id"])),
    }
    check("every validated field is in the fingerprint: any change conflicts",
          all(resp.status_code == 409
              and resp.json()["error"] == "idempotency-conflict"
              for resp in list(_as_conflict_out.values())
              + list(_as_ev_conflict_out.values())),
          ", ".join(f"{name}={resp.status_code}/{resp.json().get('error')}"
                    for name, resp in list(_as_conflict_out.items())
                    + list(_as_ev_conflict_out.items())
                    if resp.json().get("error") != "idempotency-conflict"))

    # closed vocabularies and per-kind shape: distinct code each, nothing coerced
    def _as_error(body):
        return c.post(_as_url, json=dict(
            {"idempotency_key": "vera-as-" + _as_uuid4().hex[:8]}, **body)).json()

    _as_note = "Vera Example: a note."
    _as_refusals = {
        "unknown-field": _as_error({
            "kind": "summary", "note": _as_note, "question_id": "q_asfirst001"}),
        "sitting_id is client-supplied": _as_error({
            "kind": "summary", "note": _as_note, "sitting_id": "sid-1"}),
        "invalid-kind": _as_error({"kind": "verdict", "note": _as_note}),
        "invalid-mode": _as_error({
            "kind": "summary", "mode": "grading", "note": _as_note}),
        "invalid-level (cross vocabulary)": _as_error({
            "kind": "review", "level": "passed", "attempt_id": _as_attempt_id,
            "note": _as_note}),
        "invalid-level (on a summary)": _as_error({
            "kind": "summary", "level": "seen", "note": _as_note}),
        "invalid-basis (unknown)": _as_error({
            "kind": "evidence", "level": "weak", "basis": "rumour",
            "concepts": ["loops"], "note": _as_note}),
        "invalid-basis (outside evidence)": _as_error({
            "kind": "review", "level": "correct", "attempt_id": _as_attempt_id,
            "basis": "live", "note": _as_note}),
        "invalid-attempt-id (missing on review)": _as_error({
            "kind": "review", "level": "correct", "note": _as_note}),
        "invalid-attempt-id (on a summary)": _as_error({
            "kind": "summary", "attempt_id": _as_attempt_id, "note": _as_note}),
        "invalid-concepts (missing on evidence)": _as_error({
            "kind": "evidence", "level": "weak", "basis": "live",
            "note": _as_note}),
        "invalid-concepts (too many)": _as_error({
            "kind": "evidence", "level": "weak", "basis": "live",
            "concepts": [f"c{i}" for i in range(9)], "note": _as_note}),
        "invalid-concepts (control character)": _as_error({
            "kind": "evidence", "level": "weak", "basis": "live",
            "concepts": ["loops\u0001"], "note": _as_note}),
        "invalid-concepts (empty list)": _as_error({
            "kind": "evidence", "level": "weak", "basis": "live",
            "concepts": [], "note": _as_note}),
        "invalid-concepts (on a retraction)": _as_error({
            "kind": "retraction", "supersedes": _as_j1["assessment_id"],
            "concepts": ["loops"], "note": _as_note}),
        "invalid-note (blank)": _as_error({"kind": "summary", "note": "   "}),
        "invalid-next-action (outside a summary)": _as_error({
            "kind": "review", "level": "correct", "attempt_id": _as_attempt_id,
            "note": _as_note, "next_action": "re-run the exercise"}),
        "invalid-supersedes (missing on retraction)": _as_error({
            "kind": "retraction", "note": _as_note}),
        "invalid-supersedes (not a uuid)": _as_error({
            "kind": "retraction", "supersedes": "nope", "note": _as_note}),
        "invalid-idempotency-key": c.post(_as_url, json={
            "kind": "summary", "note": _as_note,
            "idempotency_key": "ctrl\x01char"}).json(),
    }
    _as_expected = {
        "unknown-field": "unknown-field",
        "sitting_id is client-supplied": "unknown-field",
        "invalid-kind": "invalid-kind",
        "invalid-mode": "invalid-mode",
        "invalid-level (cross vocabulary)": "invalid-level",
        "invalid-level (on a summary)": "invalid-level",
        "invalid-basis (unknown)": "invalid-basis",
        "invalid-basis (outside evidence)": "invalid-basis",
        "invalid-attempt-id (missing on review)": "invalid-attempt-id",
        "invalid-attempt-id (on a summary)": "invalid-attempt-id",
        "invalid-concepts (missing on evidence)": "invalid-concepts",
        "invalid-concepts (too many)": "invalid-concepts",
        "invalid-concepts (control character)": "invalid-concepts",
        "invalid-concepts (empty list)": "invalid-concepts",
        "invalid-concepts (on a retraction)": "invalid-concepts",
        "invalid-note (blank)": "invalid-note",
        "invalid-next-action (outside a summary)": "invalid-next-action",
        "invalid-supersedes (missing on retraction)": "invalid-supersedes",
        "invalid-supersedes (not a uuid)": "invalid-supersedes",
        "invalid-idempotency-key": "invalid-idempotency-key",
    }
    check("every vocabulary/shape violation gets its own distinct code",
          all(_as_refusals[label].get("error") == code
              for label, code in _as_expected.items())
          and len(_as_rows()) == 1,
          ", ".join(f"{label}={_as_refusals[label].get('error')}"
                    for label, code in _as_expected.items()
                    if _as_refusals[label].get("error") != code))
    # Drain L1: JSON accepts an escaped lone surrogate in an object key. It is
    # still just an unknown field, and the refusal detail must remain encodable
    # rather than turning JSONResponse construction into an application error.
    _as_surrogate_body = (
        b'{"kind":"summary","note":"Vera Example: a note.",'
        b'"idempotency_key":"vera-as-surrogate","\\ud800":1}'
    )
    _as_surrogate_id = c.post(
        _as_url, content=_as_surrogate_body,
        headers={"content-type": "application/json"})
    _as_surrogate_slug = c.post(
        f"/learn/lessons/by-slug/{_as['slug']}/assessments",
        content=_as_surrogate_body,
        headers={"content-type": "application/json"})
    check("assessment aliases return a UTF-8-safe unknown-field refusal",
          _as_surrogate_id.status_code == 400
          and _as_surrogate_slug.status_code == 400
          and _as_surrogate_id.json() == _as_surrogate_slug.json()
          and _as_surrogate_id.json()["error"] == "unknown-field"
          and _as_surrogate_id.json()["detail"] == r"unknown fields: \ud800"
          and len(_as_rows()) == 1)
    check("note over 8 KiB and next_action over 512 B have their own codes",
          _as_error({"kind": "summary",
                     "note": "x" * (assess_svc.MAX_NOTE_BYTES + 1)}
                    ).get("error") == "note-too-large"
          and _as_error({
              "kind": "summary", "note": _as_note,
              "next_action": "x" * (assess_svc.MAX_NEXT_ACTION_BYTES + 1)}
          ).get("error") == "next-action-too-large")

    # references are validated against THIS lesson: an attempt or an assessment
    # of another lesson is unknown here, never a cross-lesson write
    _as_foreign = _as_error({
        "kind": "review", "level": "correct", "attempt_id": _at_j1["attempt_id"],
        "note": _as_note})
    _as_unknown_att = _as_error({
        "kind": "review", "level": "correct", "attempt_id": str(_as_uuid4()),
        "note": _as_note})
    _as_unknown_sup = _as_error({
        "kind": "retraction", "supersedes": str(_as_uuid4()), "note": _as_note})
    check("attempt/supersedes references are scoped to the lesson (422)",
          _as_foreign.get("error") == "unknown-attempt"
          and _as_unknown_att.get("error") == "unknown-attempt"
          and _as_unknown_sup.get("error") == "unknown-supersedes"
          and len(_as_rows()) == 1)

    # evidence: server-side concept dedup, basis recorded, level from its own
    # vocabulary; `live` grounds any level including `passed` (owner decision 2)
    _as_ev_resp = c.post(_as_url, json={
        "kind": "evidence", "level": "passed", "basis": "live",
        "concepts": ["loops", "loops", "off-by-one"],
        "note": "Vera Example: explained the boundary out loud, unprompted.",
        "idempotency_key": "vera-as-ev-1"})
    _as_ev_row = _as_rows()[-1]
    check("evidence records basis and deduplicates concepts server-side",
          _as_ev_resp.status_code == 200
          and json.loads(_as_ev_row["concepts_json"]) == ["loops", "off-by-one"]
          and _as_ev_row["basis"] == "live" and _as_ev_row["level"] == "passed"
          and _as_ev_row["id"] > _as_row1["id"])
    _as_exam = c.post(_as_url, json={
        "kind": "summary", "mode": "exam",
        "concepts": ["loops"],
        "note": "Vera Example: check-up covered iteration bounds.",
        "next_action": "Move on to slices.",
        "idempotency_key": "vera-as-sum-1"})
    check("summary carries mode=exam and an optional next_action",
          _as_exam.status_code == 200
          and _as_rows()[-1]["mode"] == "exam"
          and _as_rows()[-1]["next_action"] == "Move on to slices."
          and _as_rows()[-1]["level"] is None)

    # NOT bridge-gated (D-S1-4): the legacy and rejected-manifest bundles that
    # refuse ATTEMPT writes must still accept the tutor's memory
    _as_legacy = c.post(f"/learn/lessons/{_v1_id}/assessments", json={
        "kind": "summary", "note": "Vera Example: legacy bundle, spoken answers.",
        "idempotency_key": "vera-as-legacy-1"})
    _as_rejected = c.post(f"/learn/lessons/{_rej_id}/assessments", json={
        "kind": "evidence", "level": "seen", "basis": "live",
        "concepts": ["goroutines"],
        "note": "Vera Example: covered in the terminal, manifest is broken.",
        "idempotency_key": "vera-as-rejected-1"})
    check("legacy and rejected-manifest lessons still record assessments",
          _as_legacy.status_code == 200
          and _as_legacy.json()["result"] == "recorded"
          and _as_rejected.status_code == 200
          and _as_rejected.json()["result"] == "recorded")
    check("unknown lesson id and slug both 404",
          c.post("/learn/lessons/999999/assessments", json={
              "kind": "summary", "note": _as_note,
              "idempotency_key": "vera-as-404"}).status_code == 404
          and c.post("/learn/lessons/by-slug/no-such-lesson/assessments", json={
              "kind": "summary", "note": _as_note,
              "idempotency_key": "vera-as-404"}).status_code == 404)

    # archived lessons refuse (D-S1-4) — but a replay of an already-durable
    # write still returns its outcome: replay precedes mutable-state refusals
    _as_arch_url = f"/learn/lessons/{_as_arch_id}/assessments"
    _as_arch_body = {"kind": "summary",
                     "note": "Vera Example: recorded before archiving.",
                     "idempotency_key": "vera-as-arch-1"}
    _as_arch_first = c.post(_as_arch_url, json=_as_arch_body)
    _as_arch_conn = get_conn()
    try:
        lessons_svc.archive_lesson(_as_arch_conn, _as_arch_id)
        _as_arch_view = lessons_svc.get_lesson(_as_arch_conn, _as_arch_id)
    finally:
        _as_arch_conn.close()
    _as_arch_new = c.post(_as_arch_url, json=dict(
        _as_arch_body, idempotency_key="vera-as-arch-2"))
    _as_arch_replay = c.post(_as_arch_url, json=_as_arch_body)
    check("archived lesson refuses new writes but still answers replays",
          _as_arch_first.status_code == 200 and _as_arch_view["archived"]
          and _as_arch_new.status_code == 409
          and _as_arch_new.json()["error"] == "lesson-archived"
          and _as_arch_replay.status_code == 200
          and _as_arch_replay.json() == dict(
              _as_arch_first.json(), result="duplicate")
          and len(_as_rows(_as_arch_id)) == 1)

    # PR #85 round 1: the caller's lesson view is a snapshot. The archive can
    # commit between the handler's read and the insert (the owner archives in
    # the browser while the tutor's write is in the threadpool), so the binding
    # refusal reads the committed archive state inside the write transaction.
    _as_stale_conn = get_conn()
    try:
        _as_stale_id = lessons_svc.create_lesson(_as_stale_conn, "Assessment Race Demo")
        _as_stale_view = lessons_svc.get_lesson(_as_stale_conn, _as_stale_id)
        lessons_svc.archive_lesson(_as_stale_conn, _as_stale_id)
        _as_stale_refusal = None
        try:
            assess_svc.record_assessment(
                _as_stale_conn, _as_stale_view, {
                    "kind": "summary",
                    "note": "Vera Example: raced the archive.",
                    "idempotency_key": "vera-as-race-1"})
        except assess_svc.AssessmentError as exc:
            _as_stale_refusal = exc.code
    finally:
        _as_stale_conn.close()
    check("a lesson archived after the handler's read still refuses the write",
          _as_stale_view["archived"] is False
          and _as_stale_refusal == "lesson-archived"
          and len(_as_rows(_as_stale_id)) == 0)

    # PR #85 round 1: the fold's deactivation lookup is correlated — without an
    # index on (lesson_id, supersedes) it rescans the lesson's whole history
    # once per row, making every active_state() consumer quadratic
    _as_plan_conn = get_conn()
    try:
        _as_plan = [r["detail"] for r in _as_plan_conn.execute(
            "EXPLAIN QUERY PLAN " + assess_svc.ACTIVE_ROWS_SQL, (_as_fold_id,))]
    finally:
        _as_plan_conn.close()
    check("the active fold's supersedes lookup is index-bounded",
          any("idx_assessments_lesson_supersedes" in detail
              for detail in _as_plan),
          " | ".join(_as_plan))

    # slug alias shares the handler
    _as_slug = c.post(f"/learn/lessons/by-slug/{_as['slug']}/assessments", json={
        "kind": "summary", "note": "Vera Example: recorded through the alias.",
        "idempotency_key": "vera-as-slug-1"})
    check("slug-alias route records against the same lesson",
          _as_slug.status_code == 200
          and _as_slug.json()["result"] == "recorded"
          and _as_rows()[-1]["lesson_uid"] == _as["uid"])

    # active-state fold (D-S1-2) on its own lesson: latest active evidence per
    # concept, latest active review per attempt, latest active summary
    _as_fold_url = f"/learn/lessons/{_as_fold_id}/assessments"

    def _as_fold_post(body, key):
        return c.post(_as_fold_url, json=dict(body, idempotency_key=key)).json()

    _as_f_ev1 = _as_fold_post({
        "kind": "evidence", "level": "weak", "basis": "attempts",
        "concepts": ["slices", "capacity"],
        "note": "Vera Example: confuses length with capacity."}, "fold-1")
    _as_f_ev2 = _as_fold_post({
        "kind": "evidence", "level": "developing", "basis": "live",
        "concepts": ["capacity"],
        "note": "Vera Example: predicted the growth step correctly."}, "fold-2")
    _as_f_sum1 = _as_fold_post({
        "kind": "summary", "note": "Vera Example: first sitting."}, "fold-3")
    _as_f_sum2 = _as_fold_post({
        "kind": "summary", "note": "Vera Example: second sitting.",
        "supersedes": _as_f_sum1["assessment_id"]}, "fold-4")
    _as_f_retract = _as_fold_post({
        "kind": "retraction", "supersedes": _as_f_ev2["assessment_id"],
        "note": "Vera Example: that was a different learner's answer."},
        "fold-5")
    # a SECOND active evidence for one concept: "slices" now has two live
    # candidates, so latest-wins is exercised rather than last-one-standing
    _as_f_ev3 = _as_fold_post({
        "kind": "evidence", "level": "passed", "basis": "artifacts",
        "concepts": ["slices"],
        "note": "Vera Example: reslice exercise came out right."}, "fold-6")
    _as_fold_conn = get_conn()
    try:
        _as_state = assess_svc.active_state(_as_fold_conn, _as_fold_id)
        _as_active = assess_svc.active_rows(_as_fold_conn, _as_fold_id)
    finally:
        _as_fold_conn.close()
    check("active fold: retracted/superseded rows drop out, latest wins by seq",
          [row["assessment_id"] for row in _as_active] == [
              _as_f_ev1["assessment_id"], _as_f_sum2["assessment_id"],
              _as_f_retract["assessment_id"], _as_f_ev3["assessment_id"]]
          and set(_as_state["evidence_by_concept"]) == {"slices", "capacity"}
          # slices: the later of two ACTIVE rows; capacity: falls back to the
          # earlier one because the later was retracted
          and _as_state["evidence_by_concept"]["slices"]["assessment_id"]
          == _as_f_ev3["assessment_id"]
          and _as_state["evidence_by_concept"]["capacity"]["assessment_id"]
          == _as_f_ev1["assessment_id"]
          and _as_state["summary"]["assessment_id"] == _as_f_sum2["assessment_id"]
          and _as_state["reviews_by_attempt"] == {})

    # latest ACTIVE review per attempt, on the lesson that has a real attempt
    _as_rev2 = c.post(_as_url, json={
        "kind": "review", "level": "unclear", "attempt_id": _as_attempt_id,
        "note": "Vera Example: second look, still ambiguous.",
        "idempotency_key": "vera-as-rev-2"}).json()
    _as_rev3 = c.post(_as_url, json={
        "kind": "review", "level": "correct", "attempt_id": _as_attempt_id,
        "note": "Vera Example: third look; the earlier read was wrong.",
        "supersedes": _as_rev2["assessment_id"],
        "idempotency_key": "vera-as-rev-3"}).json()
    _as_rev_conn = get_conn()
    try:
        _as_rev_state = assess_svc.active_state(_as_rev_conn, _as_id)
    finally:
        _as_rev_conn.close()
    check("latest ACTIVE review per attempt wins over earlier and superseded",
          set(_as_rev_state["reviews_by_attempt"]) == {_as_attempt_id}
          and _as_rev_state["reviews_by_attempt"][_as_attempt_id][
              "assessment_id"] == _as_rev3["assessment_id"]
          # neither the first review nor the superseded second may win
          and _as_rev3["assessment_id"] not in (
              _as_j1["assessment_id"], _as_rev2["assessment_id"]))
    check("supersedes is validated against the same lesson (422 across lessons)",
          c.post(_as_fold_url, json={
              "kind": "retraction", "supersedes": _as_j1["assessment_id"],
              "note": "Vera Example: wrong lesson.",
              "idempotency_key": "fold-cross"}).json().get(
                  "error") == "unknown-supersedes")

    # rate limit + refund table (D-S1-3): replays are not new writes
    _as_rate_url = f"/learn/lessons/{_as_fold_id}/assessments"
    assess_svc._reset_rate_limit()
    _as_rate_first = c.post(_as_rate_url, json={
        "kind": "summary", "note": "Vera Example: rate window seed.",
        "idempotency_key": "rate-seed"})
    for _as_i in range(assess_svc.RATE_MAX_PER_WINDOW - 2):
        c.post(_as_rate_url, json={
            "kind": "summary", "note": f"Vera Example: filler {_as_i}.",
            "idempotency_key": f"rate-fill-{_as_i}"})
    _as_rate_replay = c.post(_as_rate_url, json={
        "kind": "summary", "note": "Vera Example: rate window seed.",
        "idempotency_key": "rate-seed"})
    _as_rate_last = c.post(_as_rate_url, json={
        "kind": "summary", "note": "Vera Example: the refunded slot.",
        "idempotency_key": "rate-last"})
    _as_rate_over = c.post(_as_rate_url, json={
        "kind": "summary", "note": "Vera Example: over the window.",
        "idempotency_key": "rate-over"})
    _as_rate_replay_over = c.post(_as_rate_url, json={
        "kind": "summary", "note": "Vera Example: rate window seed.",
        "idempotency_key": "rate-seed"})
    check("rate limit is 30/60s per lesson; replays cost no window budget",
          _as_rate_first.status_code == 200
          and _as_rate_replay.json()["result"] == "duplicate"
          # without the refund this write would have been the 31st charge
          and _as_rate_last.status_code == 200
          and _as_rate_last.json()["result"] == "recorded"
          and _as_rate_over.status_code == 429
          and _as_rate_over.json()["error"] == "rate-limited"
          and _as_rate_over.headers.get("Retry-After") == "60"
          # replay precedes the rate limit: a retry never loses its outcome
          and _as_rate_replay_over.status_code == 200
          and _as_rate_replay_over.json()["result"] == "duplicate")
    assess_svc._reset_rate_limit()

    # The refund table proper: the checks above only prove that a replay found
    # BEFORE the rate charge costs nothing. A retry racing its own original
    # sees the key uncommitted at that early check, gets charged, and only
    # discovers the duplicate under the write lock — that slot must come back,
    # or retries against a slow original starve real writes. Staged by making
    # the first replay lookup of the request miss.
    _as_real_replay = assess_svc._replay_or_conflict
    _as_replay_calls = {"n": 0}

    def _as_late_replay(*args, **kwargs):
        _as_replay_calls["n"] += 1
        if _as_replay_calls["n"] == 1:
            return None  # the racing retry cannot see its original yet
        return _as_real_replay(*args, **kwargs)

    _as_refund_body = {
        "kind": "summary", "note": "Vera Example: the racing retry.",
        "idempotency_key": "vera-as-refund-1"}
    assess_svc._reset_rate_limit()
    _as_refund_first = c.post(_as_url, json=_as_refund_body)
    _as_window_before = len(assess_svc._rate.get(_as_id, ()))
    with _mock.patch.object(
            assess_svc, "_replay_or_conflict", _as_late_replay):
        _as_refund_late = c.post(_as_url, json=_as_refund_body)
    _as_window_after = len(assess_svc._rate.get(_as_id, ()))
    check("a replay that only surfaces under the write lock refunds its slot",
          _as_refund_first.json()["result"] == "recorded"
          and _as_replay_calls["n"] >= 2
          and _as_refund_late.status_code == 200
          and _as_refund_late.json() == dict(
              _as_refund_first.json(), result="duplicate")
          and _as_window_before == 1 and _as_window_after == 1,
          f"calls={_as_replay_calls['n']} "
          f"window {_as_window_before}->{_as_window_after}")
    assess_svc._reset_rate_limit()

    # body admission (64 KiB) and the B2 write guard
    check("assessment route sits behind the B2 write guard (Origin null / cross)",
          c.post(_as_url, json=_as_review_body,
                 headers={"Origin": "null"}).status_code == 403
          and c.post(_as_url, json=_as_review_body,
                     headers={"Origin": "http://evil.example"}).status_code == 403
          and c.post(_as_url, json=_as_review_body,
                     headers={"Origin": "http://testserver"}).status_code == 200)
    check("body admission: 415 / 413 / invalid-json / non-object",
          c.post(_as_url, content=b"kind=summary",
                 headers={"content-type": "application/x-www-form-urlencoded"}
                 ).status_code == 415
          and c.post(_as_url, content=b"{" + b" " * (80 * 1024),
                     headers={"content-type": "application/json"}
                     ).status_code == 413
          and c.post(_as_url, content=b"not json {",
                     headers={"content-type": "application/json"}
                     ).json()["error"] == "invalid-json"
          and c.post(_as_url, json=[1, 2, 3]).json()["error"] == "invalid-json"
          and c.post(_as_url, content=b"[" * 20000 + b"]" * 20000,
                     headers={"content-type": "application/json"}
                     ).json()["error"] == "invalid-json")
    _as_stream = _at_asyncio.run(_at_direct_asgi(
        _as_url, 1, [b"x" * (16 * 1024) for _ in range(8)]))
    _as_stream_slug = _at_asyncio.run(_at_direct_asgi(
        f"/learn/lessons/by-slug/{_as['slug']}/assessments", 1,
        [b"x" * (16 * 1024) for _ in range(8)]))
    _as_negative = _at_asyncio.run(_at_direct_asgi(_as_url, -1, [b"{}"]))
    check("assessment aliases abort dishonest bodies mid-stream, reject "
          "negative Content-Length",
          _as_stream == (413, 5) and _as_stream_slug == (413, 5)
          and _as_negative == (400, 0))

    # recovery posture (S-M6): the export is an audit feed, not recovery —
    # restore keeps `lesson_assessment` as audit and never rebuilds the table
    _as_restore_src = (
        ROOT / "scripts" / "restore_from_export.py").read_text(encoding="utf-8")
    check("restore tooling does not reconstruct lesson_assessments",
          "lesson_assessment" not in _as_restore_src
          and "lesson_assessments" not in _as_restore_src)

    # ---- S3: the write capability at the endpoint (D-S1-3 / D-S2-2) ---------
    # The registry itself is the terminal module's (its session lifecycle is
    # covered with the rest of the terminal contract); here the seam is what the
    # endpoint does with a token: derive the sitting, or refuse visibly.
    from app import terminal as _s3_term

    _s3_conn = get_conn()
    try:
        _s3_les_id = lessons_svc.create_lesson(_s3_conn, "Assessment Sitting Demo")
        _s3_les = lessons_svc.get_lesson(_s3_conn, _s3_les_id)
        _s3_other_id = lessons_svc.create_lesson(_s3_conn, "Assessment Sitting Other")
        _s3_other = lessons_svc.get_lesson(_s3_conn, _s3_other_id)
    finally:
        _s3_conn.close()
    _s3_les_url = f"/learn/lessons/{_s3_les_id}/assessments"
    assess_svc._reset_rate_limit()

    def _s3_open_sitting(sid, lesson_id, lesson_uid):
        """Register a capability exactly as a lesson-agent session mints one."""
        capability = _s3_term._mint_assessment_capability(
            sid, lesson_id, lesson_uid, "http://127.0.0.1:8765")
        _s3_term._ASSESS_CAPABILITIES[capability["token"]] = capability
        return capability

    def _s3_post(body, token=None, url=None):
        headers = {} if token is None else {
            assess_svc.CAPABILITY_HEADER: token}
        return c.post(url or _s3_les_url, json=body, headers=headers)

    def _s3_row(assessment_id):
        conn_ = get_conn()
        try:
            row = conn_.execute(
                "SELECT * FROM lesson_assessments WHERE assessment_id = ?",
                (assessment_id,)).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn_.close()

    _s3_cap = _s3_open_sitting("verify-sitting-a", _s3_les_id, _s3_les["uid"])
    _s3_foreign = _s3_open_sitting(
        "verify-sitting-foreign", _s3_other_id, _s3_other["uid"])
    _s3_recorded = _s3_post({
        "kind": "evidence", "level": "developing", "basis": "live",
        "concepts": ["goroutines"],
        "note": "Vera Example: explained the scheduler unprompted.",
        "idempotency_key": "vera-s3-ev-1"}, _s3_cap["token"]).json()
    _s3_stamped = _s3_row(_s3_recorded["assessment_id"])
    _s3_event = None
    _s3_ev_conn = get_conn()
    try:
        _s3_event = json.loads(_s3_ev_conn.execute(
            "SELECT payload_json FROM events WHERE type = 'lesson_assessment' "
            "ORDER BY id DESC LIMIT 1").fetchone()["payload_json"])
    finally:
        _s3_ev_conn.close()
    check("a live capability stamps the sitting server-side, row and event",
          _s3_recorded["result"] == "recorded"
          and _s3_stamped["sitting_id"] == "verify-sitting-a"
          and _s3_event["sitting_id"] == "verify-sitting-a"
          and _s3_event["assessment_id"] == _s3_recorded["assessment_id"])
    _s3_body_claim = _s3_post({
        "kind": "summary", "sitting_id": "claimed-by-the-body",
        "note": "Vera Example: body-supplied sitting.",
        "idempotency_key": "vera-s3-claim-1"}, _s3_cap["token"])
    check("the body can never claim a sitting (unknown field, strict)",
          _s3_body_claim.status_code == 400
          and _s3_body_claim.json()["error"] == "unknown-field")
    _s3_mismatch = _s3_post({
        "kind": "summary", "note": "Vera Example: wrong lesson.",
        "idempotency_key": "vera-s3-mismatch-1"}, _s3_foreign["token"])
    _s3_unknown = _s3_post({
        "kind": "summary", "note": "Vera Example: invented token.",
        "idempotency_key": "vera-s3-unknown-1"}, "not-a-minted-token")
    _s3_empty = _s3_post({
        "kind": "summary", "note": "Vera Example: empty header.",
        "idempotency_key": "vera-s3-empty-1"}, "   ")
    check("a foreign, unknown, or empty capability refuses with its own code",
          _s3_mismatch.status_code == 409
          and _s3_mismatch.json()["error"] == "capability-lesson-mismatch"
          and _s3_unknown.status_code == 403
          and _s3_unknown.json()["error"] == "invalid-capability"
          and _s3_empty.status_code == 403
          and _s3_empty.json()["error"] == "invalid-capability"
          and all(k not in [r["idempotency_key"] for r in _as_rows(_s3_les_id)]
                  for k in ("vera-s3-mismatch-1", "vera-s3-unknown-1",
                            "vera-s3-empty-1")))
    # The by-slug alias resolves the same capability against the same lesson.
    _s3_slug = _s3_post({
        "kind": "summary", "note": "Vera Example: alias sitting.",
        "idempotency_key": "vera-s3-alias-1"}, _s3_cap["token"],
        url=f"/learn/lessons/by-slug/{_s3_les['slug']}/assessments").json()
    check("the by-slug alias derives the same sitting",
          _s3_slug["result"] == "recorded"
          and _s3_row(_s3_slug["assessment_id"])["sitting_id"]
          == "verify-sitting-a")
    # One ACTIVE summary per sitting: a second must supersede the first.
    _s3_second = _s3_post({
        "kind": "summary", "note": "Vera Example: a second synthesis.",
        "idempotency_key": "vera-s3-sum-2"}, _s3_cap["token"])
    _s3_superseding = _s3_post({
        "kind": "summary", "note": "Vera Example: corrected synthesis.",
        "next_action": "Re-run the channel exercise.",
        "supersedes": _s3_slug["assessment_id"],
        "idempotency_key": "vera-s3-sum-3"}, _s3_cap["token"])
    check("one active summary per sitting; the refusal names what to supersede",
          _s3_second.status_code == 409
          and _s3_second.json()["error"] == "summary-exists"
          and _s3_slug["assessment_id"] in _s3_second.json()["detail"]
          and _s3_superseding.status_code == 200
          and _s3_superseding.json()["result"] == "recorded")
    # A different sitting is a different session's synthesis, and a tokenless
    # write has no sitting for the rule to scope to.
    _s3_cap_b = _s3_open_sitting("verify-sitting-b", _s3_les_id, _s3_les["uid"])
    _s3_other_sitting = _s3_post({
        "kind": "summary", "note": "Vera Example: a later session's synthesis.",
        "idempotency_key": "vera-s3-sum-4"}, _s3_cap_b["token"])
    _s3_owner_1 = _s3_post({
        "kind": "summary", "note": "Vera Example: owner note one.",
        "idempotency_key": "vera-s3-owner-1"})
    _s3_owner_2 = _s3_post({
        "kind": "summary", "note": "Vera Example: owner note two.",
        "idempotency_key": "vera-s3-owner-2"})
    check("the summary rule is scoped to a sitting, not to the lesson",
          _s3_other_sitting.status_code == 200
          and _s3_owner_1.status_code == 200 and _s3_owner_2.status_code == 200
          and _s3_row(_s3_owner_2.json()["assessment_id"])["sitting_id"] is None)
    # A replay carries the same capability; a dead one is never a quiet
    # duplicate — the agent has to learn its provenance is gone.
    _s3_replay = _s3_post({
        "kind": "evidence", "level": "developing", "basis": "live",
        "concepts": ["goroutines"],
        "note": "Vera Example: explained the scheduler unprompted.",
        "idempotency_key": "vera-s3-ev-1"}, _s3_cap["token"])
    _s3_term._ASSESS_CAPABILITIES.pop(_s3_cap["token"], None)  # session closed
    _s3_dead_replay = _s3_post({
        "kind": "evidence", "level": "developing", "basis": "live",
        "concepts": ["goroutines"],
        "note": "Vera Example: explained the scheduler unprompted.",
        "idempotency_key": "vera-s3-ev-1"}, _s3_cap["token"])
    _s3_after_death = _s3_post({
        "kind": "summary", "note": "Vera Example: after the session ended.",
        "idempotency_key": "vera-s3-dead-1"}, _s3_cap["token"])
    check("a dead capability refuses even a replay, and never falls back",
          _s3_replay.json()["result"] == "duplicate"
          and _s3_replay.json()["assessment_id"]
          == _s3_recorded["assessment_id"]
          and _s3_dead_replay.status_code == 403
          and _s3_after_death.status_code == 403
          and all(r["idempotency_key"] != "vera-s3-dead-1"
                  for r in _as_rows(_s3_les_id)))
    _s3_term._ASSESS_CAPABILITIES.pop(_s3_foreign["token"], None)
    _s3_term._ASSESS_CAPABILITIES.pop(_s3_cap_b["token"], None)
    # The header is read by name: the same live token under any other header is
    # not a capability, and the write lands as the anonymous owner path.
    _s3_cap_c = _s3_open_sitting("verify-sitting-c", _s3_les_id, _s3_les["uid"])
    _s3_wrong_header = c.post(_s3_les_url, json={
        "kind": "summary", "note": "Vera Example: token under another header.",
        "idempotency_key": "vera-s3-hdr-1"},
        headers={"Authorization": f"Bearer {_s3_cap_c['token']}",
                 "X-Assess-Token": _s3_cap_c["token"]})
    _s3_term._ASSESS_CAPABILITIES.pop(_s3_cap_c["token"], None)
    check("the endpoint reads the capability from its own header, not the body",
          assess_svc.CAPABILITY_HEADER == "X-Ephemeris-Assess-Token"
          and _s3_wrong_header.status_code == 200
          and _s3_row(_s3_wrong_header.json()["assessment_id"])["sitting_id"]
          is None
          and "sitting_id" not in assess_svc._FIELDS)

    # ---- S2: assessments.jsonl — the active-state projection ----------------
    # (S-DESIGN D-S1-5/D-S1-6, docs/learn-bundle-spec.md §6.5)
    _s2_conn = get_conn()
    try:
        _s2_id = lessons_svc.create_lesson(_s2_conn, "Assessment Projection Demo")
        _s2 = lessons_svc.get_lesson(_s2_conn, _s2_id)
        _s2_idm_id = lessons_svc.create_lesson(_s2_conn, "Assessment Identity Demo")
        _s2_idm = lessons_svc.get_lesson(_s2_conn, _s2_idm_id)
        _s2_scale_id = lessons_svc.create_lesson(_s2_conn, "Assessment Scale Demo")
        _s2_scale = lessons_svc.get_lesson(_s2_conn, _s2_scale_id)
        _s2_quiet_id = lessons_svc.create_lesson(_s2_conn, "Assessment Quiet Demo")
        _s2_quiet = lessons_svc.get_lesson(_s2_conn, _s2_quiet_id)
        _s2_rep_id = lessons_svc.create_lesson(_s2_conn, "Assessment Replay Demo")
        _s2_rep = lessons_svc.get_lesson(_s2_conn, _s2_rep_id)
    finally:
        _s2_conn.close()
    assess_svc._reset_rate_limit()

    def _s2_path(lesson_view):
        return (Path(lessons_svc.LESSONS_DIR) / lesson_view["slug"]
                / assess_svc.PROJECTION_NAME)

    def _s2_lines(lesson_view):
        text = _s2_path(lesson_view).read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line]

    def _s2_post(lesson_id, body, key):
        return c.post(f"/learn/lessons/{lesson_id}/assessments",
                      json=dict(body, idempotency_key=key)).json()

    def _s2_rows(lesson_id):
        conn_ = get_conn()
        try:
            return [assess_svc.row_view(r) for r in conn_.execute(
                "SELECT * FROM lesson_assessments WHERE lesson_id = ? ORDER BY id",
                (lesson_id,)).fetchall()]
        finally:
            conn_.close()

    # one evidence row covering TWO concepts: the fold indexes it per concept,
    # the file must carry it exactly once
    _s2_ev1 = _s2_post(_s2_id, {
        "kind": "evidence", "level": "weak", "basis": "attempts",
        "concepts": ["closures", "captures"],
        "note": "Vera Example: rebinds the loop variable each iteration."},
        "s2-1")
    _s2_first_lines = _s2_lines(_s2)
    check("first write publishes the projection: meta line + one record",
          _s2_ev1["projection"] == "projected"
          and len(_s2_first_lines) == 2
          and _s2_first_lines[0] == {
              "kind": assess_svc.META_KIND, "v": assess_svc.META_VERSION,
              "lesson_uid": _s2["uid"], "as_of_seq": _s2_ev1["seq"],
              "generated_at": _s2_first_lines[0]["generated_at"]}
          and _s2_first_lines[0]["generated_at"].endswith("+00:00")
          and _s2_first_lines[1]["assessment_id"] == _s2_ev1["assessment_id"],
          str(_s2_first_lines))

    _s2_sum1 = _s2_post(_s2_id, {
        "kind": "summary",
        "note": "Vera Example: covered closures, ran out of time."}, "s2-2")
    _s2_ev2 = _s2_post(_s2_id, {
        "kind": "evidence", "level": "passed", "basis": "live",
        "concepts": ["closures"],
        "note": "Vera Example: predicted the capture correctly, unprompted."},
        "s2-3")
    _s2_retract = _s2_post(_s2_id, {
        "kind": "retraction", "supersedes": _s2_sum1["assessment_id"],
        "note": "Vera Example: that summary described the wrong sitting."},
        "s2-4")
    _s2_sum2 = _s2_post(_s2_id, {
        "kind": "summary", "note": "Vera Example: closures land; slices next.",
        "next_action": "Open the slices page and predict the growth step."},
        "s2-5")
    _s2_state_lines = _s2_lines(_s2)
    _s2_state_ids = [line["assessment_id"] for line in _s2_state_lines[1:]]
    check("the file is the ACTIVE fold: superseded, retracted and retraction "
          "rows never appear, ascending seq",
          _s2_state_ids == [
              _s2_ev1["assessment_id"], _s2_ev2["assessment_id"],
              _s2_sum2["assessment_id"]]
          and [line["seq"] for line in _s2_state_lines[1:]]
          == sorted(line["seq"] for line in _s2_state_lines[1:])
          # as_of_seq is the authority watermark, not the newest LINE: the
          # retraction advanced history without leaving a record behind
          and _s2_state_lines[0]["as_of_seq"] == _s2_sum2["seq"]
          and _s2_sum1["assessment_id"] not in _s2_state_ids
          and _s2_retract["assessment_id"] not in _s2_state_ids,
          str(_s2_state_ids))
    _s2_authority = {row["seq"]: row for row in _s2_rows(_s2_id)}
    check("each line is the full authority record echo, verbatim",
          all(line == _s2_authority[line["seq"]]
              for line in _s2_state_lines[1:])
          and _s2_state_lines[-1]["next_action"]
          == "Open the slices page and predict the growth step."
          and _s2_state_lines[1]["concepts"] == ["closures", "captures"]
          and _s2_state_lines[1]["basis"] == "attempts")

    # commit-then-project (S-H1): a transaction that rolls back leaves the
    # published file byte-identical — no half-written state, no phantom record
    _s2_before_fail = _s2_path(_s2).read_bytes()
    with _mock.patch.object(assess_svc, "append_event", _as_failing_append):
        try:
            c.post(f"/learn/lessons/{_s2_id}/assessments", json={
                "kind": "summary", "note": "Vera Example: event will fail.",
                "idempotency_key": "s2-atomic"})
        except sqlite3.OperationalError:
            pass
    check("a rolled-back write leaves the projection untouched",
          _s2_path(_s2).read_bytes() == _s2_before_fail
          and len(_s2_lines(_s2)) == 4
          and all(row["idempotency_key"] != "s2-atomic"
                  for row in _as_rows(_s2_id)))

    # the projection entry point refuses an in-transaction connection: no
    # filesystem work may run inside the write transaction
    _s2_txn_conn = get_conn()
    _s2_txn_dir = Path(lessons_svc.LESSONS_DIR) / _s2["slug"]
    _s2_before_txn = sorted(p.name for p in _s2_txn_dir.iterdir())
    try:
        _s2_txn_conn.execute("BEGIN IMMEDIATE")
        _s2_in_txn = assess_svc.reconcile_projection(_s2_txn_conn, _s2)
        # observed BEFORE the rollback: a refusal that still staged, cleared or
        # published would leave a trace here that the second, legitimate call
        # would then paper over
        _s2_during_txn = (sorted(p.name for p in _s2_txn_dir.iterdir()),
                          _s2_path(_s2).read_bytes())
        _s2_txn_conn.rollback()
        # forced: the second call must actually rewrite, and this process has
        # already published this watermark
        _s2_after_txn = assess_svc.reconcile_projection(
            _s2_txn_conn, _s2, force=True)
    finally:
        _s2_txn_conn.close()
    check("reconcile refuses an active transaction and works once committed",
          _s2_in_txn is False and _s2_after_txn is True
          and _s2_during_txn == (_s2_before_txn, _s2_before_fail)
          and _s2_path(_s2).read_bytes() != _s2_before_fail  # rewritten fresh
          and len(_s2_lines(_s2)) == 4)

    # identity gate (S-H7): a manifest claiming a DIFFERENT lesson blocks
    # publication — the row still commits, the projection stays pending
    _s2_idm_first = _s2_post(_s2_idm_id, {
        "kind": "summary", "note": "Vera Example: recorded before the swap."},
        "s2-idm-1")
    _s2_idm_published = _s2_path(_s2_idm).read_bytes()
    _s2_idm_manifest_path = Path(lessons_svc.LESSONS_DIR) / _s2_idm["slug"] / "lesson.json"
    _s2_idm_manifest = json.loads(_s2_idm_manifest_path.read_text(encoding="utf-8"))
    bschema.write_manifest(_s2_idm_manifest_path, dict(
        _s2_idm_manifest, lesson_uid="0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60"))
    _s2_idm_blocked = _s2_post(_s2_idm_id, {
        "kind": "summary", "note": "Vera Example: written under a foreign uid."},
        "s2-idm-2")
    _s2_idm_still = _s2_path(_s2_idm).read_bytes()
    bschema.write_manifest(_s2_idm_manifest_path, _s2_idm_manifest)
    _s2_idm_conn = get_conn()
    try:
        _s2_idm_healed = assess_svc.reconcile_projection(_s2_idm_conn, _s2_idm)
    finally:
        _s2_idm_conn.close()
    check("a manifest naming another lesson blocks publication, then heals",
          _s2_idm_first["projection"] == "projected"
          and _s2_idm_blocked["projection"] == "pending"
          # the row is durable regardless: only the file waits
          and len(_s2_rows(_s2_idm_id)) == 2
          and _s2_idm_still == _s2_idm_published
          and _s2_idm_healed is True
          and [line["assessment_id"] for line in _s2_lines(_s2_idm)[1:]]
          == [_s2_idm_blocked["assessment_id"]])
    # PR #88 round 1: a REJECTED read carries no trusted identity. The reader
    # assigns lesson_uid before a later finding rejects the manifest, so a
    # foreign uid on a broken manifest must not gate — that would block the
    # projection permanently on exactly the bundles that must publish.
    bschema.write_manifest(_s2_idm_manifest_path, dict(
        _s2_idm_manifest, lesson_uid="0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60",
        pages=[]))
    _s2_idm_broken_read = lessons_svc.read_bundle_readonly(_s2_idm)
    _s2_idm_broken = _s2_post(_s2_idm_id, {
        "kind": "summary", "note": "Vera Example: foreign uid, broken manifest."},
        "s2-idm-3")
    bschema.write_manifest(_s2_idm_manifest_path, _s2_idm_manifest)
    check("a rejected manifest never gates the projection, foreign uid or not",
          _s2_idm_broken_read.rejected
          and _s2_idm_broken_read.lesson_uid
          == "0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60"
          and _s2_idm_broken["projection"] == "projected"
          and [line["assessment_id"] for line in _s2_lines(_s2_idm)[1:]]
          == [_s2_idm_broken["assessment_id"]],
          str(_s2_idm_broken_read.codes()))

    # PR #88 round 1: the fold and the watermark come from ONE snapshot. A
    # sibling process committing between two autocommit reads would otherwise
    # publish a file advertising an `as_of_seq` whose row it does not carry.
    _s2_snap_real = assess_svc.active_state
    _s2_snap_injected = {}

    def _s2_snap_state(conn_, lesson_id_):
        state = _s2_snap_real(conn_, lesson_id_)
        if not _s2_snap_injected:
            sibling = get_conn()
            try:
                with sibling:
                    cur = sibling.execute(
                        "INSERT INTO lesson_assessments ("
                        "assessment_id, event_uuid, lesson_id, lesson_uid, mode,"
                        " idempotency_key, fingerprint, kind, level, basis,"
                        " concepts_json, note, created_at) VALUES "
                        "(?, ?, ?, ?, 'tutoring', 's2-sibling', ?, 'evidence',"
                        " 'seen', 'live', ?, ?, ?)",
                        (str(_as_uuid4()), str(_as_uuid4()), _s2_idm_id, _s2_idm["uid"],
                         "sha256:" + "0" * 64, json.dumps(["sibling-concept"]),
                         "Vera Example: committed by a sibling process.",
                         "2030-02-02T00:00:00.000000+00:00"))
                    _s2_snap_injected["seq"] = cur.lastrowid
            finally:
                sibling.close()
        return state

    _s2_snap_conn = get_conn()
    try:
        with _mock.patch.object(assess_svc, "active_state", _s2_snap_state):
            _s2_snap_ok = assess_svc.reconcile_projection(
                _s2_snap_conn, _s2_idm, force=True)
    finally:
        _s2_snap_conn.close()
    _s2_snap_lines = _s2_lines(_s2_idm)
    check("the fold and its watermark are read from one snapshot",
          _s2_snap_ok is True and "seq" in _s2_snap_injected
          # the sibling row landed after the snapshot opened: it is in neither
          # the lines nor the watermark, so the file stays self-consistent
          and _s2_snap_lines[0]["as_of_seq"] < _s2_snap_injected["seq"]
          and _s2_snap_lines[0]["as_of_seq"]
          == max(line["seq"] for line in _s2_snap_lines[1:])
          and all(line["seq"] != _s2_snap_injected["seq"]
                  for line in _s2_snap_lines[1:]),
          f"as_of={_s2_snap_lines[0]['as_of_seq']} "
          f"sibling={_s2_snap_injected.get('seq')}")

    # missing / legacy / rejected manifests publish (D-S1-4): the tutor's
    # memory must work on exactly the bundles that can never record attempts
    _s2_legacy_conn = get_conn()
    try:
        _s2_v1 = lessons_svc.get_lesson(_s2_legacy_conn, _v1_id)
        _s2_rej = lessons_svc.get_lesson(_s2_legacy_conn, _rej_id)
    finally:
        _s2_legacy_conn.close()
    check("legacy and rejected-manifest bundles are projected, not gated",
          _s2_path(_s2_v1).exists() and _s2_path(_s2_rej).exists()
          and _s2_lines(_s2_v1)[0]["lesson_uid"] == _s2_v1["uid"]
          and len(_s2_lines(_s2_rej)) == 2)

    # reconcile trigger (a): the lesson-agent terminal open, where the next
    # reader appears
    _s2_path(_s2).unlink()
    _s2_prepared = lessons_svc.prepare_terminal_workspace(_s2["slug"])
    check("lesson-agent terminal open reconciles the projection",
          _s2_prepared is not None
          and [line["assessment_id"] for line in _s2_lines(_s2)[1:]]
          == _s2_state_ids)
    # ...and a lesson that never recorded anything gets no file at all
    _s2_quiet_prepared = lessons_svc.prepare_terminal_workspace(_s2_quiet["slug"])
    _s2_quiet_conn = get_conn()
    try:
        _s2_quiet_ok = assess_svc.reconcile_projection(_s2_quiet_conn, _s2_quiet)
    finally:
        _s2_quiet_conn.close()
    check("a lesson with no assessments is left without a projection file",
          _s2_quiet_prepared is not None and _s2_quiet_ok is True
          and not _s2_path(_s2_quiet).exists())

    # reconcile trigger (b): an idempotent replay heals a pending projection —
    # a lost response must not leave the closing summary invisible
    _s2_path(_s2).unlink()
    _s2_replay = _s2_post(_s2_id, {
        "kind": "summary", "note": "Vera Example: closures land; slices next.",
        "next_action": "Open the slices page and predict the growth step."},
        "s2-5")
    check("an idempotent replay reprojects the whole active state",
          _s2_replay["result"] == "duplicate"
          and _s2_replay["projection"] == "projected"
          and _s2_replay["assessment_id"] == _s2_sum2["assessment_id"]
          and [line["assessment_id"] for line in _s2_lines(_s2)[1:]]
          == _s2_state_ids)
    # ...but a replay that would republish identical bytes does no work at all.
    # Replays are outside the rate budget by design (D-S1-3), so an unlimited
    # loop of one duplicate key must not drive unlimited full rewrites. On its
    # own lesson: this is about work done, not about what lands in the file.
    _s2_rep_body = {"kind": "summary",
                    "note": "Vera Example: the replay changes nothing."}
    _s2_post(_s2_rep_id, _s2_rep_body, "s2-rep-1")
    _s2_publishes = []
    _s2_publish_real = assess_svc._publish

    def _s2_counting_publish(lesson_view, data):
        _s2_publishes.append(len(data))
        return _s2_publish_real(lesson_view, data)

    with _mock.patch.object(assess_svc, "_publish", _s2_counting_publish):
        _s2_rep_a = _s2_post(_s2_rep_id, _s2_rep_body, "s2-rep-1")
        _s2_rep_b = _s2_post(_s2_rep_id, _s2_rep_body, "s2-rep-1")
        # a real write moves the watermark, so it must publish again
        _s2_rep_new = _s2_post(_s2_rep_id, {
            "kind": "evidence", "level": "passed", "basis": "mixed",
            "concepts": ["ranges"],
            "note": "Vera Example: ranges are clear now."}, "s2-rep-2")
    check("repeated replays publish nothing; the next real write does",
          _s2_rep_a["result"] == "duplicate" and _s2_rep_b["result"] == "duplicate"
          and _s2_rep_a["projection"] == "projected"
          and _s2_rep_b["projection"] == "projected"
          and _s2_rep_new["projection"] == "projected"
          and len(_s2_publishes) == 1
          and len(_s2_lines(_s2_rep)) == 3,
          f"publishes={_s2_publishes}")
    # The cached identity is a full metadata seal, not only inode/size/mtime.
    # A bundle writer can edit in place and restore mtime, but ctime still
    # changes; a replay must detect that and heal instead of falsely reporting
    # the altered bytes as projected.
    _s2_rep_canonical = _s2_path(_s2_rep).read_bytes()
    _s2_rep_before_tamper = _s2_path(_s2_rep).stat()
    _s2_rep_tampered = _s2_rep_canonical.replace(
        b"Vera Example", b"Fera Example", 1)
    _s2_path(_s2_rep).write_bytes(_s2_rep_tampered)
    os.utime(
        _s2_path(_s2_rep),
        ns=(_s2_rep_before_tamper.st_atime_ns,
            _s2_rep_before_tamper.st_mtime_ns),
    )
    _s2_rep_after_tamper = _s2_path(_s2_rep).stat()
    _s2_publishes.clear()
    with _mock.patch.object(assess_svc, "_publish", _s2_counting_publish):
        _s2_rep_repaired = _s2_post(
            _s2_rep_id, _s2_rep_body, "s2-rep-1")
    check("same-inode same-size restored-mtime mutation is reprojected",
          _s2_rep_tampered != _s2_rep_canonical
          and _s2_rep_before_tamper.st_ino == _s2_rep_after_tamper.st_ino
          and _s2_rep_before_tamper.st_size == _s2_rep_after_tamper.st_size
          and _s2_rep_before_tamper.st_mtime_ns
          == _s2_rep_after_tamper.st_mtime_ns
          and _s2_rep_before_tamper.st_ctime_ns
          != _s2_rep_after_tamper.st_ctime_ns
          and _s2_rep_repaired["result"] == "duplicate"
          and _s2_rep_repaired["projection"] == "projected"
          and len(_s2_publishes) == 1
          and _s2_path(_s2_rep).read_bytes() != _s2_rep_tampered
          and len(_s2_lines(_s2_rep)) == 3,
          f"publishes={_s2_publishes}")
    # Terminal creation is another unmetered reconcile trigger. The ordinary
    # seal already detects a missing or changed file, so repeated opens over
    # an intact projection must neither fold nor publish.
    _s2_terminal_publishes = []
    _s2_terminal_folds = []
    _s2_terminal_state_real = assess_svc.active_state

    def _s2_terminal_publish(lesson_view, data):
        _s2_terminal_publishes.append(len(data))
        return _s2_publish_real(lesson_view, data)

    def _s2_terminal_state(conn_, lesson_id_):
        _s2_terminal_folds.append(lesson_id_)
        return _s2_terminal_state_real(conn_, lesson_id_)

    with _mock.patch.object(
            assess_svc, "_publish", _s2_terminal_publish), \
         _mock.patch.object(
            assess_svc, "active_state", _s2_terminal_state):
        _s2_rep_open_a = lessons_svc.prepare_terminal_workspace(_s2_rep["slug"])
        _s2_rep_open_b = lessons_svc.prepare_terminal_workspace(_s2_rep["slug"])
    check("repeated terminal opens skip an intact projection",
          _s2_rep_open_a is not None and _s2_rep_open_b is not None
          and _s2_terminal_publishes == [] and _s2_terminal_folds == []
          and len(_s2_lines(_s2_rep)) == 3,
          f"publishes={_s2_terminal_publishes} folds={_s2_terminal_folds}")
    # the skip is keyed on what THIS process published, so a projection it
    # never wrote is still rewritten — the heal triggers keep working
    _s2_path(_s2_rep).unlink()
    assess_svc._reset_sweep_state()
    _s2_rep_heal_conn = get_conn()
    try:
        _s2_rep_healed = assess_svc.reconcile_projection(
            _s2_rep_heal_conn, _s2_rep)
    finally:
        _s2_rep_heal_conn.close()
    check("a projection this process never published is not skipped",
          _s2_rep_healed is True and len(_s2_lines(_s2_rep)) == 3)
    # ...and the terminal open heals a deleted file at an unchanged watermark:
    # the ordinary metadata seal sees the missing name, no force bypass needed
    _s2_path(_s2_rep).unlink()
    _s2_rep_terminal_healed = lessons_svc.prepare_terminal_workspace(
        _s2_rep["slug"])
    check("the terminal open heals a missing file at an unchanged watermark",
          _s2_rep_terminal_healed is not None and _s2_path(_s2_rep).exists()
          and len(_s2_lines(_s2_rep)) == 3)
    # the identity gate governs the skip too: at an unchanged watermark, a
    # manifest that now names another lesson still answers pending (S-H7)
    _s2_rep_mpath = Path(lessons_svc.LESSONS_DIR) / _s2_rep["slug"] / "lesson.json"
    _s2_rep_manifest = json.loads(_s2_rep_mpath.read_text(encoding="utf-8"))
    _s2_rep_before_swap = _s2_path(_s2_rep).read_bytes()
    bschema.write_manifest(_s2_rep_mpath, dict(
        _s2_rep_manifest, lesson_uid="0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60"))
    # ...and it refuses WITHOUT folding: these replays are unmetered, so the
    # refusal must not be linear in the lesson's active state either
    _s2_rep_folds = []
    _s2_fold_real = assess_svc.active_state

    def _s2_counting_state(conn_, lesson_id_):
        _s2_rep_folds.append(lesson_id_)
        return _s2_fold_real(conn_, lesson_id_)

    with _mock.patch.object(assess_svc, "active_state", _s2_counting_state):
        _s2_rep_foreign = _s2_post(_s2_rep_id, _s2_rep_body, "s2-rep-1")
    bschema.write_manifest(_s2_rep_mpath, _s2_rep_manifest)
    _s2_rep_restored = _s2_post(_s2_rep_id, _s2_rep_body, "s2-rep-1")
    check("a skip is refused while the manifest names another lesson",
          _s2_rep_foreign["result"] == "duplicate"
          and _s2_rep_foreign["projection"] == "pending"
          and _s2_rep_folds == []
          and _s2_rep_restored["projection"] == "projected"
          and _s2_path(_s2_rep).read_bytes() == _s2_rep_before_swap,
          f"folds={_s2_rep_folds}")
    # ...and the watermark query itself seeks instead of walking the lesson's
    # history: replays are unmetered, so it must not be linear in lifetime rows
    _s2_plan_conn = get_conn()
    try:
        _s2_plan = [str(tuple(r)) for r in _s2_plan_conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT MAX(id) FROM lesson_assessments WHERE lesson_id = ?",
            (_s2_rep_id,)).fetchall()]
    finally:
        _s2_plan_conn.close()
    check("the projection watermark is served by (lesson_id, id)",
          any("idx_assessments_lesson_seq" in row for row in _s2_plan),
          str(_s2_plan))

    # reconcile trigger (c): the first write per lesson per process sweeps even
    # when that write is REFUSED — a restart mid-pending heals on next contact
    _s2_path(_s2).unlink()
    assess_svc._reset_sweep_state()
    _s2_refused = c.post(f"/learn/lessons/{_s2_id}/assessments", json={
        "kind": "retraction", "supersedes": str(_as_uuid4()),
        "note": "Vera Example: retracts a record that does not exist.",
        "idempotency_key": "s2-sweep"})
    check("the first-write sweep heals a pending projection even on a refusal",
          _s2_refused.status_code == 422
          and _s2_refused.json()["error"] == "unknown-supersedes"
          and [line["assessment_id"] for line in _s2_lines(_s2)[1:]]
          == _s2_state_ids)

    # collision (D-S1-5): a foreign object on the name is moved aside, never
    # adopted and never written through
    _s2_col_dir = _s2_path(_s2)
    _s2_col_dir.unlink()
    _s2_col_dir.mkdir()
    (_s2_col_dir / "planted.txt").write_text("Vera Example: planted", encoding="utf-8")
    _s2_col_conn = get_conn()
    try:
        _s2_col_dir_ok = assess_svc.reconcile_projection(_s2_col_conn, _s2)
    finally:
        _s2_col_conn.close()
    _s2_bundle = Path(lessons_svc.LESSONS_DIR) / _s2["slug"]
    _s2_aside = sorted(_s2_bundle.glob("assessments.jsonl.collision-*"))
    check("a directory on the projection name is moved aside with its content",
          _s2_col_dir_ok is True and _s2_path(_s2).is_file()
          and len(_s2_aside) == 1 and _s2_aside[0].is_dir()
          and (_s2_aside[0] / "planted.txt").read_text(encoding="utf-8")
          == "Vera Example: planted"
          and [line["assessment_id"] for line in _s2_lines(_s2)[1:]]
          == _s2_state_ids)
    # a symlink is moved aside, and its target keeps its own bytes
    _s2_outside = _s2_bundle / "not-the-projection.txt"
    _s2_outside.write_text("Vera Example: unrelated file\n", encoding="utf-8")
    _s2_path(_s2).unlink()
    _s2_path(_s2).symlink_to(_s2_outside)
    _s2_sym_conn = get_conn()
    try:
        _s2_sym_ok = assess_svc.reconcile_projection(_s2_sym_conn, _s2)
    finally:
        _s2_sym_conn.close()
    check("a symlink on the projection name is replaced, target untouched",
          _s2_sym_ok is True
          and not _s2_path(_s2).is_symlink() and _s2_path(_s2).is_file()
          and _s2_outside.read_text(encoding="utf-8")
          == "Vera Example: unrelated file\n"
          and len(_s2_lines(_s2)) == 4)
    # a hard link never receives the new bytes through its other name
    _s2_other_name = _s2_bundle / "assessments-hardlink.jsonl"
    _os.link(_s2_path(_s2), _s2_other_name)
    _s2_before_link = _s2_other_name.read_bytes()
    _s2_link_conn = get_conn()
    try:
        _s2_link_ok = assess_svc.reconcile_projection(_s2_link_conn, _s2)
    finally:
        _s2_link_conn.close()
    check("a multi-link projection name is replaced, the other name is frozen",
          _s2_link_ok is True
          and _s2_other_name.read_bytes() == _s2_before_link
          and _s2_path(_s2).stat().st_nlink == 1
          and len(_s2_lines(_s2)) == 4)
    # ...and the same holds for a link planted on the STAGED temp: publishing it
    # would hand the bundle a second, writable name for the live projection
    _s2_stage_real = assess_svc._stage_temp

    def _s2_linking_stage(dir_fd, data):
        name, fd = _s2_stage_real(dir_fd, data)
        _os.link(name, name + ".alias", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        return name, fd

    _s2_staged_before = _s2_path(_s2).read_bytes()
    with _mock.patch.object(assess_svc, "_stage_temp", _s2_linking_stage):
        _s2_staged = _s2_post(_s2_id, {
            "kind": "summary",
            "note": "Vera Example: staged while a link was planted."},
            "s2-stage-link")
    check("a link planted on the staged temp keeps it from being published",
          _s2_staged["result"] == "recorded"
          and _s2_staged["projection"] == "pending"
          and _s2_path(_s2).read_bytes() == _s2_staged_before
          and _s2_path(_s2).stat().st_nlink == 1
          and len(sorted(_s2_bundle.glob(".assessments-*.tmp.alias"))) == 1
          and not sorted(_s2_bundle.glob(".assessments-*.tmp")))

    # a busy cross-process lock is an honest pending, and the next write heals
    with assess_svc._projection_file_lock(_s2):
        _s2_busy = _s2_post(_s2_id, {
            "kind": "evidence", "level": "developing", "basis": "mixed",
            "concepts": ["slices"],
            "note": "Vera Example: written while the lock is held."}, "s2-busy")
    _s2_busy_heal = _s2_post(_s2_id, {
        "kind": "summary", "note": "Vera Example: the lock is free again."},
        "s2-busy-2")
    check("a busy projection lock returns pending; the next write heals",
          _s2_busy["projection"] == "pending"
          and _s2_busy_heal["projection"] == "projected"
          and _s2_busy["assessment_id"] in [
              line["assessment_id"] for line in _s2_lines(_s2)[1:]])

    # the rewrite is bounded by CURRENT state, so it must stay correct at the
    # few hundred active rows a long-running lesson accumulates
    _s2_scale_conn = get_conn()
    try:
        with _s2_scale_conn:
            for _s2_i in range(300):
                _s2_scale_conn.execute(
                    "INSERT INTO lesson_assessments ("
                    "assessment_id, event_uuid, lesson_id, lesson_uid, mode, "
                    "idempotency_key, fingerprint, kind, level, basis, "
                    "concepts_json, note, created_at) VALUES "
                    "(?, ?, ?, ?, 'tutoring', ?, ?, 'evidence', 'seen', "
                    " 'live', ?, ?, ?)",
                    (str(_as_uuid4()), str(_as_uuid4()), _s2_scale_id,
                     _s2_scale["uid"], f"s2-scale-{_s2_i}",
                     "sha256:" + "0" * 64, json.dumps([f"concept-{_s2_i}"]),
                     f"Vera Example: scale row {_s2_i}.",
                     f"2030-01-01T00:00:{_s2_i % 60:02d}.000000+00:00"))
        _s2_scale_ok = assess_svc.reconcile_projection(_s2_scale_conn, _s2_scale)
    finally:
        _s2_scale_conn.close()
    _s2_scale_lines = _s2_lines(_s2_scale)
    check("a few hundred active rows project in one bounded rewrite",
          _s2_scale_ok is True and len(_s2_scale_lines) == 301
          and _s2_scale_lines[0]["kind"] == assess_svc.META_KIND
          and [line["seq"] for line in _s2_scale_lines[1:]]
          == sorted(line["seq"] for line in _s2_scale_lines[1:])
          and len({line["assessment_id"] for line in _s2_scale_lines[1:]}) == 300,
          f"lines={len(_s2_scale_lines)}")

    # D-S1-6: the reserved name is enforced by the §4.1 path grammar, so no
    # page, block file, or artifact root can claim the projection
    _s2_reserved_read = bschema.read_manifest_text(json.dumps({
        "schema_version": 2,
        "lesson_uid": "0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60",
        "entry": "index.html",
        "pages": [
            {"id": "pg_s2reserved1", "path": "index.html"},
            {"id": "pg_s2reserved2", "path": "assessments.jsonl"},
        ],
        "artifact_roots": ["attempts", "assessments.jsonl"],
    }))
    check("assessments.jsonl is a reserved bundle name (§2/§4.1)",
          "assessments.jsonl" in bschema.RESERVED_NAMES
          and not bschema.valid_v2_path("assessments.jsonl")
          and not bschema.valid_v2_path("assessments.jsonl/notes.html")
          and "invalid-path" in _s2_reserved_read.codes()
          and "assessments.jsonl" not in _s2_reserved_read.page_paths()
          and "assessments.jsonl" not in _s2_reserved_read.artifact_roots)
    _s2_spec = (ROOT / "docs" / "learn-bundle-spec.md").read_text(encoding="utf-8")
    check("the bundle spec records the assessments projection (§2/§6.5/§12)",
          "assessments.jsonl   app-owned projection" in _s2_spec
          and "### 6.5 Assessments" in _s2_spec
          and "| `assessments.jsonl` | owns (projection + reconcile)"
          in _s2_spec)

    # ---- F1: pure artifact reads + conflict-safe editor backend ------------
    from app.services import artifacts as artifacts_svc
    import types as _f1_types

    _f1_conn = get_conn()
    try:
        _f1_id = lessons_svc.create_lesson(_f1_conn, "Artifact Editor Demo")
        _f1 = lessons_svc.get_lesson(_f1_conn, _f1_id)
    finally:
        _f1_conn.close()
    _f1_dir = Path(lessons_svc.LESSONS_DIR) / _f1["slug"]
    _f1_raw = json.loads((_f1_dir / "lesson.json").read_text(encoding="utf-8"))
    _f1_page = _f1_raw["pages"][0]["id"]
    _f1_raw["blocks"] = [
        {"id": "blk_editor01", "page": _f1_page, "kind": "editor",
         "file": "attempts/blk_editor01/main.py",
         "runner_id": "python-script-v1"},
        {"id": "blk_safe0001", "page": _f1_page, "kind": "editor",
         "file": "attempts/blk_safe0001/main.py",
         "runner_id": "python-script-v1"},
        {"id": "blk_link0001", "page": _f1_page, "kind": "editor",
         "file": "attempts/blk_link0001/main.py",
         "runner_id": "python-script-v1"},
        {"id": "blk_race0001", "page": _f1_page, "kind": "editor",
         "file": "attempts/blk_race0001/main.py",
         "runner_id": "python-script-v1"},
        {"id": "blk_deep0001", "page": _f1_page, "kind": "editor",
         "file": "attempts/a/b/c/d/e.py",
         "runner_id": "python-script-v1"},
    ]
    bschema.write_manifest(_f1_dir / "lesson.json", _f1_raw)
    (_f1_dir / "index.html").write_text(
        "<html>Vera Example editor page</html>", encoding="utf-8")
    _f1_url = f"/learn/lessons/{_f1_id}/blocks/blk_editor01/file"
    artifacts_svc._reset_rate_limit()

    # The phase-F reader treats missing bundle state as a refusal and does not
    # recreate any of the preview path's historical skeleton/directories.
    _f1_pure_conn = get_conn()
    try:
        _f1_pure_id = lessons_svc.create_lesson(
            _f1_pure_conn, "Artifact Pure Read Demo")
        _f1_pure = lessons_svc.get_lesson(_f1_pure_conn, _f1_pure_id)
    finally:
        _f1_pure_conn.close()
    _f1_pure_dir = Path(lessons_svc.LESSONS_DIR) / _f1_pure["slug"]
    _shutil.rmtree(_f1_pure_dir)
    _f1_pure_get = c.get(
        f"/learn/lessons/{_f1_pure_id}/blocks/blk_never0001/file")
    check("F1 pure GET does not create a missing bundle or manifest",
          _f1_pure_get.status_code == 409
          and _f1_pure_get.json()["error"] == "manifest-rejected"
          and not _f1_pure_dir.exists())

    _f1_missing = c.get(_f1_url)
    check("F1 missing artifact GET is side-effect-free",
          _f1_missing.status_code == 200
          and _f1_missing.json() == {
              "ok": True, "exists": False, "content": "", "size": 0,
          }
          and not (_f1_dir / "attempts" / "blk_editor01").exists())
    check("F1 artifact POST stays behind the B2 write guard",
          c.post(_f1_url, json={"content": "x", "base_rev": "absent"},
                 headers={"Origin": "null"}).status_code == 403
          and not (_f1_dir / "attempts" / "blk_editor01").exists())

    _f1_body1 = "print('Vera Example')\n"
    _f1_save1 = c.post(
        _f1_url, json={"content": _f1_body1, "base_rev": "absent"})
    _f1_saved1 = _f1_save1.json()
    _f1_file = _f1_dir / "attempts" / "blk_editor01" / "main.py"
    check("F1 first save publishes mode-safe bytes and records telemetry",
          _f1_save1.status_code == 200
          and _f1_saved1["result"] == "saved"
          and _f1_saved1["event_recorded"] is True
          and _f1_file.read_text(encoding="utf-8") == _f1_body1
          and stat_module.S_IMODE(_f1_file.stat().st_mode) == 0o600
          and _f1_file.stat().st_nlink == 1)
    _f1_get1 = c.get(_f1_url)
    check("F1 GET returns strict bytes, size, and their sha256 revision",
          _f1_get1.status_code == 200
          and _f1_get1.json()["content"] == _f1_body1
          and _f1_get1.json()["file_rev"] == _f1_saved1["file_rev"]
          and _f1_get1.json()["size"] == len(_f1_body1.encode("utf-8")))
    _f1_alias = c.get(
        f"/learn/lessons/by-slug/{_f1['slug']}/blocks/blk_editor01/file")
    check("F1 by-slug artifact alias is the same descriptor-bound read",
          _f1_alias.status_code == 200
          and _f1_alias.json()["file_rev"] == _f1_saved1["file_rev"])

    _f1_event_count = len(events_of("lesson_artifact_saved"))
    _f1_conflict = c.post(
        _f1_url, json={"content": "print('changed')\n", "base_rev": "absent"})
    _f1_retry = c.post(
        _f1_url, json={"content": _f1_body1, "base_rev": "absent"})
    check("F1 conflict returns the current revision without overwriting",
          _f1_conflict.status_code == 409
          and _f1_conflict.json()["error"] == "file-conflict"
          and _f1_conflict.json()["file_rev"] == _f1_saved1["file_rev"]
          and _f1_file.read_text(encoding="utf-8") == _f1_body1)
    check("F1 content-equal retry is unchanged with no write or event",
          _f1_retry.status_code == 200
          and _f1_retry.json()["result"] == "unchanged"
          and _f1_retry.json()["event_recorded"] is False
          and len(events_of("lesson_artifact_saved")) == _f1_event_count)

    _f1_body2 = "print('saved revision two')\n"
    _f1_save2 = c.post(
        _f1_url,
        json={"content": _f1_body2, "base_rev": _f1_saved1["file_rev"]},
    )
    _f1_events = events_of("lesson_artifact_saved")
    _f1_event2 = json.loads(_f1_events[-1]["payload_json"])
    check("F1 save event carries identity and metadata but never content",
          _f1_save2.status_code == 200
          and _f1_event2["lesson_uid"] == _f1["uid"]
          and _f1_event2["block_id"] == "blk_editor01"
          and _f1_event2["file"] == "attempts/blk_editor01/main.py"
          and _f1_event2["created"] is False
          and "content" not in _f1_event2
          and "title" not in _f1_event2)

    # Event failure is observable but cannot roll back the already-durable file.
    _f1_body3 = "print('telemetry unavailable')\n"
    with _mock.patch.object(
            artifacts_svc, "append_event",
            side_effect=sqlite3.OperationalError("invented event outage")):
        _f1_event_down = c.post(
            _f1_url,
            json={"content": _f1_body3,
                  "base_rev": _f1_save2.json()["file_rev"]},
        )
    check("F1 file-first event failure is visible and leaves saved bytes durable",
          _f1_event_down.status_code == 200
          and _f1_event_down.json()["result"] == "saved"
          and _f1_event_down.json()["event_recorded"] is False
          and _f1_file.read_text(encoding="utf-8") == _f1_body3)

    # Stored bytes are descriptor-bound: over-limit, invalid UTF-8, and a
    # multi-link regular file each have their distinct fail-closed outcomes.
    _f1_file.write_bytes(b"x" * (artifacts_svc.MAX_FILE_BYTES + 1))
    check("F1 GET refuses an oversized stored artifact before buffering past cap",
          c.get(_f1_url).status_code == 413
          and c.get(_f1_url).json()["error"] == "file-too-large")
    _f1_file.write_bytes(b"\xff")
    _f1_invalid = c.get(_f1_url)
    check("F1 GET refuses invalid UTF-8 without replacement characters",
          _f1_invalid.status_code == 422
          and _f1_invalid.json()["error"] == "invalid-encoding"
          and "content" not in _f1_invalid.json())
    _f1_bad_rev = "sha256:" + hashlib.sha256(b"\xff").hexdigest()
    _f1_repair = c.post(
        _f1_url, json={"content": _f1_body3, "base_rev": _f1_bad_rev})
    _f1_other = _f1_dir / "attempts" / "linked-copy.py"
    _os.link(_f1_file, _f1_other)
    _f1_linked = c.get(_f1_url)
    check("F1 safe-file rule refuses a multi-link regular descriptor",
          _f1_repair.status_code == 200
          and _f1_linked.status_code == 409
          and _f1_linked.json()["error"] == "unsafe-file")
    _f1_other.unlink()

    # A same-inode direct writer can otherwise change the file between read
    # chunks while leaving the reader with a made-up mixed snapshot and hash.
    # The opening/closing descriptor identity must agree before any content is
    # returned or used as the save comparison authority.
    _f1_stable_dir = _f1_dir / "attempts" / "blk_safe0001"
    _f1_stable_dir.mkdir(exist_ok=True)
    _f1_stable_file = _f1_stable_dir / "main.py"
    _f1_stable_file.write_bytes(b"A" * artifacts_svc.MAX_FILE_BYTES)
    _f1_real_read = artifacts_svc.os.read
    _f1_mid_read_changed = [False]

    def _f1_mutate_during_read(fd, amount):
        chunk = _f1_real_read(fd, amount)
        if chunk and not _f1_mid_read_changed[0]:
            _f1_mid_read_changed[0] = True
            # Change size as well as bytes so the regression is deterministic
            # even on a filesystem with coarse timestamp resolution.
            _f1_stable_file.write_bytes(
                b"Z" * (artifacts_svc.MAX_FILE_BYTES - 1)
            )
        return chunk

    with _mock.patch.object(
            artifacts_svc.os, "read", _f1_mutate_during_read):
        try:
            artifacts_svc.get_artifact(_f1, "blk_safe0001")
            _f1_mid_read_code = None
        except artifacts_svc.ArtifactError as exc:
            _f1_mid_read_code = exc.code
    check("F1 descriptor read refuses a same-inode mid-read mutation",
          _f1_mid_read_changed[0] and _f1_mid_read_code == "unsafe-file")

    _f1_outside = _f1_dir.parent / "invented-artifact-outside"
    _f1_outside.mkdir()
    _f1_link_parent = _f1_dir / "attempts" / "blk_link0001"
    _f1_link_parent.symlink_to(_f1_outside, target_is_directory=True)
    _f1_link_url = f"/learn/lessons/{_f1_id}/blocks/blk_link0001/file"
    _f1_link_get = c.get(_f1_link_url)
    _f1_link_post = c.post(
        _f1_link_url, json={"content": "outside?", "base_rev": "absent"})
    check("F1 no-follow traversal refuses a symlinked parent on GET and save",
          _f1_link_get.status_code == 409
          and _f1_link_post.status_code == 409
          and not (_f1_outside / "main.py").exists())
    _f1_link_parent.unlink()

    _f1_deep_url = f"/learn/lessons/{_f1_id}/blocks/blk_deep0001/file"
    _f1_deep = c.post(
        _f1_deep_url, json={"content": "deep", "base_rev": "absent"})
    check("F1 depth guard rejects undiscoverable writes without making parents",
          _f1_deep.status_code == 422
          and _f1_deep.json()["error"] == "undiscoverable-path"
          and not (_f1_dir / "attempts" / "a").exists())

    # Deterministic parent-swap harness: move the checked destination parent,
    # plant a symlink at its former path immediately before replace, and prove
    # the dirfd-relative publication stays on the pinned inode.
    _f1_race_dir = _f1_dir / "attempts" / "blk_race0001"
    _f1_race_dir.mkdir()
    _f1_race_held = _f1_dir / "attempts" / "blk_race0001-held"
    _f1_real_replace = artifacts_svc.os.replace
    _f1_swapped = {"done": False}

    def _f1_swap_replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        if dst == "main.py" and dst_dir_fd is not None:
            _os.rename(_f1_race_dir, _f1_race_held)
            _f1_race_dir.symlink_to(_f1_outside, target_is_directory=True)
            try:
                result = _f1_real_replace(
                    src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
                _f1_swapped["done"] = True
                return result
            finally:
                _f1_race_dir.unlink()
                _os.rename(_f1_race_held, _f1_race_dir)
        return _f1_real_replace(
            src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    with _mock.patch.object(artifacts_svc.os, "replace", _f1_swap_replace):
        _f1_race = c.post(
            f"/learn/lessons/{_f1_id}/blocks/blk_race0001/file",
            json={"content": "print('pinned')\n", "base_rev": "absent"},
        )
    check("F1 mutation harness never publishes through a raced parent swap",
          _f1_race.status_code == 200 and _f1_swapped["done"]
          and (_f1_race_dir / "main.py").read_text(encoding="utf-8")
              == "print('pinned')\n"
          and not (_f1_outside / "main.py").exists())

    # A direct writer changing the current descriptor after compare but before
    # publication is caught by the final identity check; its bytes win.
    _f1_current = c.get(_f1_url).json()["file_rev"]
    _f1_real_stage = artifacts_svc._stage_temp

    def _f1_stage_then_mutate(parent_fd, data):
        name = _f1_real_stage(parent_fd, data)
        _f1_file.write_text("direct writer wins\n", encoding="utf-8")
        return name

    with _mock.patch.object(
            artifacts_svc, "_stage_temp", _f1_stage_then_mutate):
        _f1_identity_conflict = c.post(
            _f1_url,
            json={"content": "api writer loses\n", "base_rev": _f1_current},
        )
    check("F1 final descriptor identity re-check catches a pre-replace mutation",
          _f1_identity_conflict.status_code == 409
          and _f1_identity_conflict.json()["error"] == "file-conflict"
          and _f1_file.read_text(encoding="utf-8") == "direct writer wins\n"
          and not list(_f1_file.parent.glob(".artifact-*.tmp")))

    # Admission and semantic caps are separate: one is a streaming body cap,
    # the other is the 64-KiB raw file contract.
    _f1_huge_body = c.post(
        _f1_url,
        content=json.dumps({
            "content": "x" * (artifacts_svc.MAX_BODY_BYTES + 1),
            "base_rev": "absent",
        }).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    _f1_huge_file = c.post(
        _f1_url,
        json={"content": "x" * (artifacts_svc.MAX_FILE_BYTES + 1),
              "base_rev": "absent"},
    )
    check("F1 capped-stream admission refuses a body over 512 KiB",
          _f1_huge_body.status_code == 413
          and _f1_huge_body.json()["error"] == "payload-too-large")
    check("F1 save refuses content over 64 KiB by raw UTF-8 bytes",
          _f1_huge_file.status_code == 413
          and _f1_huge_file.json()["error"] == "file-too-large")

    artifacts_svc._reset_rate_limit()
    _f1_rate_max = artifacts_svc.RATE_MAX_PER_WINDOW
    artifacts_svc.RATE_MAX_PER_WINDOW = 1
    try:
        _f1_same1 = c.post(
            _f1_url,
            json={"content": "direct writer wins\n", "base_rev": "absent"},
        )
        _f1_same2 = c.post(
            _f1_url,
            json={"content": "direct writer wins\n", "base_rev": "absent"},
        )
        _f1_charge = c.post(
            _f1_url, json={"content": "new bytes", "base_rev": "absent"})
        _f1_rate_hit = c.post(
            _f1_url, json={"content": "more bytes", "base_rev": "absent"})
    finally:
        artifacts_svc.RATE_MAX_PER_WINDOW = _f1_rate_max
        artifacts_svc._reset_rate_limit()
    check("F1 unchanged saves refund the per-lesson rate slot",
          _f1_same1.json().get("result") == "unchanged"
          and _f1_same2.json().get("result") == "unchanged")
    check("F1 conflicts stay charged and rate-limited itself is uncharged",
          _f1_charge.status_code == 409
          and _f1_rate_hit.status_code == 429
          and _f1_rate_hit.json()["error"] == "rate-limited"
          and _f1_rate_hit.headers.get("retry-after") is not None)

    with _mock.patch(
            "app.runner.runner_health",
            return_value=_f1_types.SimpleNamespace(available=True)):
        _f1_meta = c.get(
            f"/learn/lessons/{_f1_id}/preview-meta",
            params={"entry": "index.html"},
        ).json()
        _f1_learn = c.get(f"/learn?lesson={_f1_id}").text
    with _mock.patch(
            "app.runner.runner_health",
            return_value=_f1_types.SimpleNamespace(available=False)):
        _f1_unhealthy_meta = c.get(
            f"/learn/lessons/{_f1_id}/preview-meta",
            params={"entry": "index.html"},
        ).json()
    check("F1 armed metadata exposes block ids and health-gated run flags only",
          _f1_meta["bridge_page"]["blocks"][0]
              == {"id": "blk_editor01", "run": True}
          and all("file" not in block for block in _f1_meta["bridge_page"]["blocks"])
          and not any(
              block["run"]
              for block in _f1_unhealthy_meta["bridge_page"]["blocks"]
          ))
    check("F1 Learn template advertises the guarded artifact route prefix",
          f'data-artifacts-url="/learn/lessons/{_f1_id}/blocks"' in _f1_learn
          and "{% if selected.artifacts_url is defined %}"
              in (ROOT / "app/templates/learn.html").read_text(encoding="utf-8"))

    # ---- D5: Check through the bridge — parent derivation surface, byte-
    # bound page serving, attempt operation (lesson-bridge-abi.md §3.1) ----
    # per-page declared questions ride the bridge identity, so the parent
    # can refuse undeclared ids before spending a server write
    _d5_meta = c.get(f"/learn/lessons/{_at_id}/preview-meta",
                     params={"entry": "index.html"}).json()
    check("preview-meta lists the questions declared for the armed page",
          _d5_meta["bridge"] is True
          and _d5_meta["bridge_page"]["questions"] == ["q_atpredict1"])
    # single served-content snapshot (drain D2 L2): a declared v2 page's
    # response body is byte-identical to the digest its version token
    # carries, and the file route's version equals the metadata poll's
    _d5_file = c.get(f"/learn/lessons/{_at_id}/files/index.html")
    _d5_digest = hashlib.sha256(_d5_file.content).hexdigest()
    check("served page bytes match the content-bound version token",
          _d5_file.status_code == 200
          and _d5_file.headers["x-lesson-preview-version"] == _d5_meta["version"]
          and _d5_meta["version"].endswith(":" + _d5_digest[:16])
          and _d5_file.content == (_at_dir / "index.html").read_bytes())
    _d5_info = lessons_svc.bundle_resource_info(_at, "index.html")
    check("bundle_resource_info returns the one-descriptor snapshot for v2 pages",
          _d5_info["content"] == _d5_file.content
          and _d5_info["version"] == _d5_meta["version"])
    # serve-time version binding (PR-60 round 1): the parent navigates with
    # ?v=<token>; matching bytes serve, a mismatched token is refused with
    # the self-reloading 409 instead of showing bytes the armed page_rev
    # does not describe
    _d5_vok = c.get(f"/learn/lessons/{_at_id}/files/index.html",
                    params={"v": _d5_meta["version"]})
    _d5_vbad = c.get(f"/learn/lessons/{_at_id}/files/index.html",
                     params={"v": "1:interactive-local-v1:deadbeefdeadbeef"})
    check("?v binding: matching token serves, mismatched token is a 409 reload",
          _d5_vok.status_code == 200 and _d5_vok.content == _d5_file.content
          and _d5_vbad.status_code == 409
          and "location.reload" in _d5_vbad.text
          and _d5_vbad.headers.get("x-lesson-preview-version") == _d5_meta["version"])
    from urllib.parse import quote as _d5_quote
    check("learn.html initial iframe src carries the ?v binding",
          f'?v={_d5_quote(_d5_meta["version"], safe="")}'
          in c.get(f"/learn?lesson={_at_id}").text.replace("&amp;", "&"))
    # an asset (undeclared as a page) streams as before: no snapshot, no
    # content-bound token
    (_at_dir / "assets").mkdir(exist_ok=True)
    (_at_dir / "assets" / "probe.css").write_text("body{}", encoding="utf-8")
    _d5_asset = lessons_svc.bundle_resource_info(_at, "assets/probe.css")
    check("assets are not snapshotted and keep the plain mtime version",
          _d5_asset["content"] is None and ":" not in _d5_asset["version"])
    # supported page-size bound (drain L3/D5): an oversized declared page
    # renders but carries NO bridge identity — visible finding, never silent
    _d5_orig = (_at_dir / "index.html").read_bytes()
    (_at_dir / "index.html").write_bytes(
        b"<html>" + b"x" * lessons_svc.PAGE_IDENTITY_MAX_BYTES + b"</html>")
    _d5_big_meta = c.get(f"/learn/lessons/{_at_id}/preview-meta",
                         params={"entry": "index.html"}).json()
    _d5_big_file = c.get(f"/learn/lessons/{_at_id}/files/index.html")
    check("oversized page: renders, no bridge identity, page-too-large finding",
          _d5_big_meta["exists"] is True
          and _d5_big_meta["bridge_page"] is None
          and _d5_big_meta["outcome"] == "degraded"
          and any(f["code"] == "page-too-large" for f in _d5_big_meta["findings"])
          and _d5_big_file.status_code == 200)
    check("oversized page: attempts refuse on the server too (stale revision)",
          c.post(_at_url, json=dict(_at_body, idempotency_key="vera-big-page-1")
                 ).json().get("stale") is True)
    # round 2 fail-closed: a declared page that cannot be snapshotted (here:
    # grown past the bound) refuses a versioned request instead of letting
    # the streaming fallback serve bytes the requested token doesn't describe
    _d5_gone = c.get(f"/learn/lessons/{_at_id}/files/index.html",
                     params={"v": _d5_meta["version"]})
    check("unsnapshottable declared page fails closed on a versioned request",
          _d5_gone.status_code == 409 and "location.reload" in _d5_gone.text)
    (_at_dir / "index.html").write_bytes(_d5_orig)  # restore
    # round 2 parity: a non-bridge v2 page (legacy-display profile) uses the
    # same mtime:profile token in the metadata and the file route — ?v never
    # 409s a page the metadata advertises
    bschema.write_manifest(_at_dir / "lesson.json",
                           dict(_at_raw, runtime={"profile": "legacy-display"}))
    _d5_leg_meta = c.get(f"/learn/lessons/{_at_id}/preview-meta",
                         params={"entry": "index.html"}).json()
    _d5_leg_file = c.get(f"/learn/lessons/{_at_id}/files/index.html",
                         params={"v": _d5_leg_meta["version"]})
    check("legacy v2 page: meta and route tokens agree, ?v serves 200",
          _d5_leg_meta["bridge"] is False
          and _d5_leg_meta["version"].endswith(":legacy-display")
          and _d5_leg_file.status_code == 200)
    bschema.write_manifest(_at_dir / "lesson.json", _at_raw)  # restore
    # rounds 3+5: a page vanishing between is_file() and the lstat size
    # pre-check must fall through to the descriptor-bound hash open — never
    # a 500 out of the metadata poll. The file is REALLY gone here; only
    # is_file() reports the stale pre-race truth, so the pre-check's
    # os.lstat raises exactly as in the race.
    from unittest import mock as _d5_mock
    _van_real_isfile = Path.is_file

    def _van_isfile(self):
        if str(self).endswith(f"{_at['slug']}/index.html"):
            return True  # the stale answer the race saw
        return _van_real_isfile(self)

    _van_orig = (_at_dir / "index.html").read_bytes()
    (_at_dir / "index.html").unlink()
    with _d5_mock.patch.object(Path, "is_file", _van_isfile):
        _van_info = lessons_svc.lesson_file_info(_at, "index.html")
    (_at_dir / "index.html").write_bytes(_van_orig)  # restore
    check("vanish race in the lstat pre-check fails closed, never a 500",
          _van_info["exists"] is False and _van_info["bridge_page"] is None)
    # round 4: a symlink raced in AFTER the path_has_symlink() check (mocked
    # away here) must not have its target sized by the pre-check — lstat +
    # S_ISREG routes it to the O_NOFOLLOW open, which fails closed (§2)
    _r4_target = _at_dir / "oversized-decoy.html"
    _r4_target.write_bytes(b"z" * (lessons_svc.PAGE_IDENTITY_MAX_BYTES + 1))
    _r4_orig = (_at_dir / "index.html").read_bytes()
    (_at_dir / "index.html").unlink()
    _os.symlink(_r4_target, _at_dir / "index.html")
    # freeze the raced state: the guard and the resolve() ran on the clean
    # pre-swap path (mocked), the swapped-in symlink is what lstat/open see
    with _d5_mock.patch.object(lessons_svc.bundle_schema, "path_has_symlink",
                               return_value=False), \
            _d5_mock.patch.object(lessons_svc, "_entry_path",
                                  lambda slug, entry: _at_dir / entry):
        _r4_info = lessons_svc.lesson_file_info(_at, "index.html")
    (_at_dir / "index.html").unlink()
    (_at_dir / "index.html").write_bytes(_r4_orig)  # restore
    _r4_target.unlink()
    check("raced-in symlink to an oversized target fails closed, no identity",
          _r4_info["exists"] is False and _r4_info["bridge_page"] is None
          and not any(f["code"] == "page-too-large"
                      for f in _r4_info["findings"]))
    # the digest cache evicts one entry when full, never the whole set
    check("page digest cache evicts oldest, not clear-all",
          "_PAGE_DIGEST_CACHE.clear()" not in
          (ROOT / "app" / "services" / "lessons.py").read_text(encoding="utf-8"))
    # Drain C1: cache admission must stay at its configured bound when many
    # distinct cold misses arrive together. The custom len() makes the old
    # unsynchronized implementation deterministically observe the same
    # pre-insert size in every worker; the locked implementation times out
    # the first rendezvous and serializes all later checks.
    import threading as _d5_threading

    class _D5ConcurrentLenDict(dict):
        def __init__(self, initial, parties):
            super().__init__(initial)
            self._len_barrier = _d5_threading.Barrier(parties)

        def __len__(self):
            observed = dict.__len__(self)
            try:
                self._len_barrier.wait(timeout=0.25)
            except _d5_threading.BrokenBarrierError:
                pass
            return observed

    _d5_cache_workers = 12
    _d5_cache_max = 64
    _d5_cache_probe = _D5ConcurrentLenDict({
        f"/invented/preloaded-{i}.html": ((i,), f"{i:064x}")
        for i in range(_d5_cache_max - 1)
    }, _d5_cache_workers)
    _d5_cache_start = _d5_threading.Barrier(_d5_cache_workers + 1)
    _d5_cache_errors = []

    def _d5_cache_miss(i):
        try:
            _d5_cache_start.wait()
            lessons_svc._cache_page_digest(
                Path(f"/invented/cold-{i}.html"), (i,), f"{i + 1000:064x}")
        except BaseException as exc:  # keep worker failures visible to check()
            _d5_cache_errors.append(exc)

    _d5_saved_cache = lessons_svc._PAGE_DIGEST_CACHE
    _d5_saved_cache_max = lessons_svc._PAGE_DIGEST_CACHE_MAX
    try:
        lessons_svc._PAGE_DIGEST_CACHE = _d5_cache_probe
        lessons_svc._PAGE_DIGEST_CACHE_MAX = _d5_cache_max
        _d5_cache_threads = [
            _d5_threading.Thread(target=_d5_cache_miss, args=(i,))
            for i in range(_d5_cache_workers)
        ]
        for _d5_cache_thread in _d5_cache_threads:
            _d5_cache_thread.start()
        _d5_cache_start.wait()
        for _d5_cache_thread in _d5_cache_threads:
            _d5_cache_thread.join(timeout=2)
        _d5_cache_alive = any(t.is_alive() for t in _d5_cache_threads)
        _d5_cache_actual = dict.__len__(_d5_cache_probe)
    finally:
        lessons_svc._PAGE_DIGEST_CACHE = _d5_saved_cache
        lessons_svc._PAGE_DIGEST_CACHE_MAX = _d5_saved_cache_max
    check("page digest cache stays bounded under concurrent cold misses",
          not _d5_cache_alive and not _d5_cache_errors
          and _d5_cache_actual == _d5_cache_max,
          f"entries={_d5_cache_actual}, errors={_d5_cache_errors!r}")
    # the Learn page hands the parent runtime the attempt endpoint
    check("learn.html carries data-attempts-url for the parent runtime",
          f'data-attempts-url="/learn/lessons/{_at_id}/attempts"'
          in c.get(f"/learn?lesson={_at_id}").text)
    # structural anchors for the attempt operation in the parent runtime —
    # source .ts and committed emit alike (#42): capability negotiation,
    # parent-derived submission, per-op re-validation, toast, in-flight cap
    for _d5_name, _d5_text in (("learn-bridge.ts", _d2_ts), ("learn-bridge.js", _d2_js)):
        check(f"{_d5_name}: attempt operation anchors",
              "ATTEMPT_OP_VERSION = 1" in _d5_text
              and 'want.includes("attempts")' in _d5_text
              and "idempotency_key: requestId" in _d5_text
              and "page_id: armed.page_id" in _d5_text
              and "page_rev: armed.page_rev" in _d5_text
              and '"stale-page"' in _d5_text
              and '"capability-not-granted"' in _d5_text
              and "MAX_ATTEMPTS_INFLIGHT" in _d5_text
              and "ATTEMPT_SETTLE_MS" in _d5_text
              and "MAX_ANSWER_BYTES = 32 * 1024" in _d5_text
              and "contentByteLength(answer) > MAX_ANSWER_BYTES" in _d5_text
              and "attempt #" in _d5_text)
    check("parent runtime re-validates per operation against fresh metadata",
          "metaQuestions" in _d2_ts
          and "await fetchMeta()" in _d2_ts.split("postAttempt")[1])

    # ---- phase F2 frontend: editor capability and artifact membrane ----
    # Source and emitted runtime must carry the same block-specific, fresh-meta
    # guards. This is deliberately the editor-only first commit; run anchors
    # arrive in the next commit so the review history preserves D-FE-1.
    for _fe_name, _fe_text in (("learn-bridge.ts", _d2_ts), ("learn-bridge.js", _d2_js)):
        check(f"{_fe_name}: editor membrane anchors",
              'frame.dataset["artifactsUrl"]' in _fe_text
              and 'want.includes("editor")' in _fe_text
              and 'capabilities.push("editor")' in _fe_text
              and 'msg["op"] === "artifact.get"' in _fe_text
              and 'msg["op"] === "artifact.save"' in _fe_text
              and "freshBlock" in _fe_text
              and "metaBlocks" in _fe_text
              and "MAX_BRIDGE_BLOCKS = 100" in _fe_text
              and "EDITOR_SETTLE_MS" in _fe_text
              and "MAX_EDITOR_INFLIGHT" in _fe_text
              and "contentByteLength(content) > MAX_CONTENT_BYTES" in _fe_text
              and 'body: JSON.stringify({ content, base_rev: baseRev })' in _fe_text)
    _fe_template = (ROOT / "app" / "templates" / "learn.html").read_text(encoding="utf-8")
    check("Learn template feature-detects the artifact endpoint",
          "selected.artifacts_url is defined" in _fe_template
          and 'data-artifacts-url="{{ selected.artifacts_url }}"' in _fe_template)
    check("editor operations revalidate the fresh page block before HTTP",
          "const freshBlock = async" in _d2_ts
          and "const meta = await fetchMeta()" in _d2_ts.split("const freshBlock = async", 1)[1]
          and "blocks.find((candidate) => candidate.id === blockId)" in _d2_ts
          and _d2_ts[_d2_ts.index("const saveArtifact"):
                     _d2_ts.index("const runStartEndpoint")].count("await freshBlock") == 2
          and _d2_ts.rindex("await freshBlock", _d2_ts.index("const saveArtifact"),
                            _d2_ts.index("const runStartEndpoint"))
          < _d2_ts.index("method: \"POST\"", _d2_ts.index("const saveArtifact")))
    _fe_get = _d2_ts[_d2_ts.index("const getArtifact"):
                     _d2_ts.index("const saveArtifact")]
    check("artifact reads revalidate the page block after GET before disclosure",
          _fe_get.count("await freshBlock") == 3
          and _fe_get.index("const rec = await readEndpointJson")
          < _fe_get.rindex("await freshBlock")
          < _fe_get.index("boundPort.postMessage(reply)"))
    check("private artifact reads require sticky parent consent before GET",
          "let artifactReadConsent: boolean | null = null" in _d2_ts
          and "artifactReadConsent = null" in _d2_ts
          and "window.confirm(" in _d2_ts
          and 'answerError(boundPort, "artifact-read-denied", requestId)' in _fe_get
          and _fe_get.index("allowArtifactRead()")
          < _fe_get.index("const rec = await readEndpointJson")
          and _fe_get.index("allowArtifactRead()")
          < _fe_get.index("await freshBlock", _fe_get.index("allowArtifactRead()")))
    check("editor grant refreshes current block metadata at handshake time",
          "const handleReady = async" in _d2_ts
          and "const meta = await fetchMeta()" in _d2_ts.split("const handleReady = async", 1)[1]
          and "armedBlocks = metaBlocks(meta) ?? []" in
          _d2_ts.split("const handleReady = async", 1)[1]
          and "grantToken !== token" in _d2_ts)

    # Byte accounting probes the two expansion classes behind the derived
    # 512 KiB membrane cap: ASCII controls that become six-byte JSON escapes,
    # and multibyte Unicode at the raw 64 KiB semantic limit.
    _fe_hostile = "\x00" * (64 * 1024)
    _fe_multibyte = "🪐" * ((64 * 1024) // len("🪐".encode("utf-8")))
    _fe_hostile_wire = json.dumps(
        {"op": "artifact.save", "content": _fe_hostile},
        ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    _fe_multibyte_wire = json.dumps(
        {"op": "artifact.save", "content": _fe_multibyte},
        ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    check("editor byte bounds admit hostile escaping inside the derived cap",
          len(_fe_hostile.encode("utf-8")) == 64 * 1024
          and 384 * 1024 <= len(_fe_hostile_wire) < 512 * 1024)
    check("editor byte bounds measure multibyte raw and serialized UTF-8",
          len(_fe_multibyte.encode("utf-8")) == 64 * 1024
          and len(_fe_multibyte_wire) < 512 * 1024
          and "UTF8.encode(text).byteLength" in _d2_ts)

    _fe_fixture = (ROOT / "fixtures" / "lesson-bridge"
                   / "editor-run-conventions.html").read_text(encoding="utf-8")
    check("editor conventions fixture exercises get/save as text-only data",
          'want: ["editor", "run"]' in _fe_fixture
          and 'op: "artifact.get"' in _fe_fixture
          and 'op: "artifact.save"' in _fe_fixture
          and "status.textContent = text" in _fe_fixture
          and "innerHTML" not in _fe_fixture)
    check("editor conventions authenticate and consume one handshake result",
          "answered || event.source !== window.parent" in _fe_fixture
          and "event.origin !== appOrigin" in _fe_fixture
          and 'message.abi !== 1' in _fe_fixture
          and "event.ports.length !== 1" in _fe_fixture
          and "event.ports.length !== 0" in _fe_fixture
          and "answered = true" in _fe_fixture)
    check("editor conventions mint lesson-wide ids and fail closed without entropy",
          "crypto.getRandomValues(words)" in _fe_fixture
          and "requestNonce" in _fe_fixture
          and "secure request ids unavailable" in _fe_fixture
          and "fixture-${kind}-${requestNonce}-${++sequence}" in _fe_fixture
          and "retry keeps runRequestId" in _fe_fixture)
    check("editor degradation: no bridge stays useful and read-only",
          "Read-only preview. Connecting" in _fe_fixture
          and "bridge unavailable" in _fe_fixture
          and '<textarea id="source"' in _fe_fixture
          and " readonly>" in _fe_fixture)
    check("editor degradation: welcome without grant stays read-only",
          'message.capabilities.includes("editor")' in _fe_fixture
          and 'readOnly("editor capability not granted")' in _fe_fixture)
    check("editor degradation: old backend attrs grant no capability",
          "selected.artifacts_url is defined" in _fe_template
          and 'const artifactsUrl = frame.dataset["artifactsUrl"] || null' in _d2_ts
          and 'artifactsUrl !== null && armedBlocks.length > 0' in _d2_ts)
    check("editor degradation: direct-open fixture stays read-only",
          "window.parent === window" in _fe_fixture
          and 'readOnly("direct open: no parent bridge")' in _fe_fixture)

    # ---- phase F5 frontend: composite save/run, owned SSE relay, cancel ----
    for _fr_name, _fr_text in (("learn-bridge.ts", _d2_ts), ("learn-bridge.js", _d2_js)):
        check(f"{_fr_name}: run membrane anchors",
              'frame.dataset["runsUrl"]' in _fr_text
              and 'want.includes("run")' in _fr_text
              and 'capabilities.push("run")' in _fr_text
              and 'msg["op"] === "artifact.save_run"' in _fr_text
              and 'msg["op"] === "run.cancel"' in _fr_text
              and 'op: "run.output"' in _fr_text
              and 'op: "run.exit"' in _fr_text
              and "MAX_OUTPUT_BYTES = 32 * 1024" in _fr_text
              and "MAX_OWNED_RUNS = 16" in _fr_text
              and "ownedRuns" in _fr_text
              and "activeRelay" in _fr_text
              and "RUN_SETTLE_MS" in _fr_text)
    check("Learn template feature-detects the run endpoint independently",
          "selected.runs_url is defined" in _fe_template
          and 'data-runs-url="{{ selected.runs_url }}"' in _fe_template)
    _fr_save_run = _d2_ts[_d2_ts.index("const saveAndRun"):
                          _d2_ts.index("const cancelRun")]
    check("save_run saves successfully before starting the returned revision",
          _fr_save_run.index("artifactEndpoint(blockId)")
          < _fr_save_run.index("runStartEndpoint(blockId)")
          and 'saveResult !== "saved" && saveResult !== "unchanged"' in _fr_save_run
          and "file_rev: fileRev, idempotency_key: idempotencyKey" in _fr_save_run
          and _fr_save_run.count("await freshBlock") == 4)
    check("save_run revalidates page/block Run authority after start before relay",
          _fr_save_run.index("const started = await readEndpointJson")
          < _fr_save_run.index("const afterStart = await freshBlock")
          < _fr_save_run.index("rememberOwnedRun")
          and 'if (!afterStart.run)' in _fr_save_run)
    check("save_run derives parameter-bound idempotency before artifact mutation",
          "export const sha256Hex" in _d2_ts
          and "window.crypto" not in _d2_ts
          and '"ephemeris:lesson-run:v1", requestId, blockId, content' in _d2_ts
          and _fr_save_run.index("deriveRunIdempotencyKey")
          < _fr_save_run.index("artifactEndpoint(blockId)"))
    check("run ownership gates relay and cancel while navigation only aborts relay",
          "rememberOwnedRun(runId, { generation: gen, block_id: blockId })" in _d2_ts
          and "const owner = ownedRuns.get(runId)" in _d2_ts
          and "owner?.generation === gen && owner.block_id === blockId" in _d2_ts
          and "if (activeRelay) activeRelay.controller.abort()" in _d2_ts
          and "ownedRuns = new Map()" in _d2_ts
          and "service.cancel" not in _d2_ts)
    _fr_cancel = _d2_ts[_d2_ts.index("const cancelRun"):
                        _d2_ts.index("const postAttempt")]
    check("owned run cancel survives block removal but keeps fresh page checks",
          _fr_cancel.count("await freshBlocks") == 2
          and "await freshBlock(" not in _fr_cancel
          and _fr_cancel.index("const owner = ownedRuns.get(runId)")
          < _fr_cancel.index("await freshBlocks"))
    _fr_port = _d2_ts[_d2_ts.index("const onPortMessage"):
                      _d2_ts.index("const finishReady")]
    check("save_run rejects backend-invalid idempotency keys before mutation",
          'answerError(port, "invalid-idempotency-key", requestId)' in _fr_port
          and "requestId.charCodeAt" in _fr_port
          and _fr_port.index("invalid-idempotency-key")
          < _fr_port.index("void saveAndRun"))
    check("one document-wide stream refuses a second save_run before HTTP",
          "activeRelay !== null || runStartToken !== null" in _fr_port
          and _fr_port.index("activeRelay !== null || runStartToken !== null")
          < _fr_port.index("void saveAndRun")
          and 'answerError(port, "busy", requestId)' in _fr_port)
    check("SSE relay validates sequence, stream, UTF-8 size, and terminal cause",
          'new TextDecoder("utf-8", { fatal: true })' in _d2_ts
          and 'payload["seq"] !== seq' in _d2_ts
          and 'stream !== "stdout" && stream !== "stderr"' in _d2_ts
          and "contentByteLength(text) > MAX_OUTPUT_BYTES" in _d2_ts
          and "RUN_CAUSES.has(cause)" in _d2_ts
          and 'op: "run.error"' in _d2_ts)
    _fr_relay_loop = _d2_ts[_d2_ts.index("while (ownsRelay())"):
                            _d2_ts.index("const saveAndRun")]
    check("SSE relay drains complete coalesced frames before partial-frame cap",
          _fr_relay_loop.index('let boundary = buffer.indexOf("\\n\\n")')
          < _fr_relay_loop.index("UTF8.encode(buffer).byteLength > MAX_PORT_BYTES"))
    _fr_output_multibyte = "🪐" * ((32 * 1024) // len("🪐".encode("utf-8")))
    _fr_output_wire = json.dumps(
        {"op": "run.output", "text": _fr_output_multibyte},
        ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    check("run output keeps its 32 KiB raw limit inside the serialized cap",
          len(_fr_output_multibyte.encode("utf-8")) == 32 * 1024
          and len(_fr_output_wire) < 512 * 1024)
    check("run conventions fixture exercises save_run, cursor relay, and cancel as text",
          'op: "artifact.save_run"' in _fe_fixture
          and 'op: "run.cancel"' in _fe_fixture
          and 'reply.op === "run.output"' in _fe_fixture
          and 'reply.op === "run.exit"' in _fe_fixture
          and "after: cursor" in _fe_fixture
          and "output.textContent += reply.text" in _fe_fixture
          and "innerHTML" not in _fe_fixture)
    check("conventions fixture is test-only, not shipped by the Learn template",
          "editor-run-conventions" not in _fe_template)
    # frozen docs: the ABI carries the attempt op; the lesson brief teaches
    # the child side of it (child sends ONLY v/op/request_id/question_id/answer)
    _d5_abi = (ROOT / "docs" / "lesson-bridge-abi.md").read_text(encoding="utf-8")
    check("ABI §3.1 freezes the attempt operation",
          "### 3.1" in _d5_abi
          and '"op": "attempt", "v": 1' in _d5_abi
          and "capability-not-granted" in _d5_abi
          and "32 KiB of raw UTF-8" in _d5_abi)
    check("ABI §3.2 freezes editor ops and derived byte accounting",
          "### 3.2" in _d5_abi
          and '"op": "artifact.get", "v": 1' in _d5_abi
          and '"op": "artifact.save", "v": 1' in _d5_abi
          and "512 KiB" in _d5_abi
          and "6 bytes per input byte" in _d5_abi
          and "64 KiB raw UTF-8 bytes" in _d5_abi)
    check("ABI pins authenticated child handshake and fresh logical request ids",
          "event.origin" in _d5_abi
          and "exactly one `MessagePort`" in _d5_abi
          and "first valid result is final" in _d5_abi
          and "fresh opaque `request_id`" in _d5_abi
          and "even across reloads and tabs" in _d5_abi)
    check("ABI §3.3 freezes composite run, relay ownership, and reconnect",
          "### 3.3" in _d5_abi
          and '"op": "artifact.save_run", "v": 1' in _d5_abi
          and '"op": "run.cancel", "v": 1' in _d5_abi
          and '"op": "run.output"' in _d5_abi
          and '"op": "run.exit"' in _d5_abi
          and "There is no bare child-facing run-start operation" in _d5_abi
          and "**not** call cancel" in _d5_abi)
    check("lesson brief teaches the frozen attempt call",
          '{"op": "attempt", "v": 1' in lessons_svc._AGENTS_TEMPLATE
          and "retry an unanswered submission with the SAME id"
          in lessons_svc._AGENTS_TEMPLATE)

    # §2 symlink policy: a page that resolves through a symlink is missing
    _symp_conn = get_conn()
    try:
        _symp_id = lessons_svc.create_lesson(_symp_conn, "Symlink Page Demo")
        _symp = lessons_svc.get_lesson(_symp_conn, _symp_id)
    finally:
        _symp_conn.close()
    _symp_dir = Path(lessons_svc.LESSONS_DIR) / _symp["slug"]
    _symp_target = Path(lessons_svc.LESSONS_DIR) / "decoy-page.html"
    _symp_target.write_text("<html>outside the bundle</html>", encoding="utf-8")
    _os.symlink(_symp_target, _symp_dir / "index.html")
    _symp_info = lessons_svc.lesson_file_info(_symp)
    _symp_file = c.get(f"/learn/lessons/{_symp_id}/files/index.html")
    check("symlinked page is treated as missing (§2)",
          _symp_info["exists"] is False and _symp_file.status_code == 404)
    check("symlinked page never carries bridge identity (D2)",
          _symp_info["bridge_page"] is None)
    check("symlinked page degrades the reported outcome (§9.2)",
          _symp_info["outcome"] == "degraded"
          and any(f["code"] == "symlinked-path" for f in _symp_info["findings"]))
    _symp_bundle = lessons_svc.bundle_info(_symp)
    check("symlinked current page degrades the TOP-LEVEL bundle_info outcome",
          _symp_bundle["outcome"] == "degraded"
          and any(f["code"] == "symlinked-path" for f in _symp_bundle["findings"]))
    _symp_manifest = _symp_dir / "lesson.json"
    _symp_manifest.unlink()
    _os.symlink(_symp_target, _symp_manifest)
    _symp_meta = c.get(f"/learn/lessons/{_symp_id}/preview-meta").json()
    check("symlinked lesson.json rejects as symlinked-bundle, no skeleton overwrite",
          _symp_meta["outcome"] == "rejected"
          and any(f["code"] == "symlinked-bundle" for f in _symp_meta["findings"])
          and _symp_manifest.is_symlink())

    # a DANGLING symlink at the bundle dir rejects visibly, never a 500
    _dang_conn = get_conn()
    try:
        _dang_id = lessons_svc.create_lesson(_dang_conn, "Dangling Bundle Demo")
        _dang = lessons_svc.get_lesson(_dang_conn, _dang_id)
    finally:
        _dang_conn.close()
    _dang_dir = Path(lessons_svc.LESSONS_DIR) / _dang["slug"]
    import shutil as _shutil
    _shutil.rmtree(_dang_dir)
    _os.symlink(Path(lessons_svc.LESSONS_DIR) / "no-such-target-dir", _dang_dir)
    _dang_resp = c.get(f"/learn/lessons/{_dang_id}/preview-meta")
    check("dangling bundle-dir symlink rejects as symlinked-bundle, not a 500",
          _dang_resp.status_code == 200
          and _dang_resp.json()["outcome"] == "rejected"
          and any(f["code"] == "symlinked-bundle" for f in _dang_resp.json()["findings"])
          and _dang_dir.is_symlink())

    # a non-regular node at lesson.json rejects visibly — never a 500 — and
    # finding details never leak the absolute runtime path
    _dirm_conn = get_conn()
    try:
        _dirm_id = lessons_svc.create_lesson(_dirm_conn, "Directory Manifest Demo")
        _dirm = lessons_svc.get_lesson(_dirm_conn, _dirm_id)
    finally:
        _dirm_conn.close()
    _dirm_path = Path(lessons_svc.LESSONS_DIR) / _dirm["slug"] / "lesson.json"
    _dirm_path.unlink()
    _dirm_path.mkdir()
    _dirm_resp = c.get(f"/learn/lessons/{_dirm_id}/preview-meta")
    _dirm_meta = _dirm_resp.json()
    check("directory at lesson.json rejects as manifest-unreadable, not a 500",
          _dirm_resp.status_code == 200
          and _dirm_meta["outcome"] == "rejected"
          and any(f["code"] == "manifest-unreadable" for f in _dirm_meta["findings"]))
    check("finding details never leak the absolute runtime path",
          str(lessons_svc.LESSONS_DIR) not in _dirm_resp.text)

    # the preview file route serves the preview surface only
    check("reserved bundle names are not served through /files/",
          c.get(f"/learn/lessons/{_v2_id}/files/lesson.json").status_code == 404)
    _v2_note = _v2_dir / "attempts" / "note.txt"
    _v2_note.parent.mkdir(exist_ok=True)
    _v2_note.write_text("Vera Example learner note", encoding="utf-8")
    check("artifact-root files are not served through /files/",
          c.get(f"/learn/lessons/{_v2_id}/files/attempts/note.txt").status_code == 404)
    # v2 serving is a positive allowlist: declared pages + assets/ only
    (_v2_dir / "undeclared-private.html").write_text(
        "<html>Vera Example private draft</html>", encoding="utf-8")
    (_v2_dir / "assets").mkdir(exist_ok=True)
    (_v2_dir / "assets" / "diagram.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    check("v2 /files/ serves declared pages + assets only",
          c.get(f"/learn/lessons/{_v2_id}/files/undeclared-private.html").status_code == 404
          and c.get(f"/learn/lessons/{_v2_id}/files/assets/diagram.svg").status_code == 200
          and c.get(f"/learn/lessons/{_v2_id}/files/related/01-stage.html").status_code == 200)
    # a declared page stays servable even when a root claims its directory
    _v2_roots_raw = json.loads((_v2_dir / "lesson.json").read_text(encoding="utf-8"))
    _v2_roots_raw["artifact_roots"] = ["related", "attempts"]
    bschema.write_manifest(_v2_dir / "lesson.json", _v2_roots_raw)
    check("declared page wins over an overlapping artifact root",
          c.get(f"/learn/lessons/{_v2_id}/files/related/01-stage.html").status_code == 200
          and c.get(f"/learn/lessons/{_v2_id}/files/attempts/note.txt").status_code == 404)
    # ...and so does the assets/ preview area when a root claims it
    _v2_roots_raw["artifact_roots"] = ["assets", "attempts"]
    bschema.write_manifest(_v2_dir / "lesson.json", _v2_roots_raw)
    check("preview assets win over an overlapping artifact root",
          c.get(f"/learn/lessons/{_v2_id}/files/assets/diagram.svg").status_code == 200
          and c.get(f"/learn/lessons/{_v2_id}/files/attempts/note.txt").status_code == 404)
    _v2_roots_raw["artifact_roots"] = ["attempts"]
    bschema.write_manifest(_v2_dir / "lesson.json", _v2_roots_raw)
    # the injected mandatory root joins the overlap pass: a nested root
    # declared without "attempts" is dropped, the final set stays disjoint
    _inj = bschema.read_manifest_text(json.dumps({
        "schema_version": 2,
        "lesson_uid": "0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60",
        "entry": "index.html",
        "pages": [{"id": "pg_inject001", "path": "index.html"}],
        "artifact_roots": ["attempts/deep"],
    }))
    check("injected attempts root keeps the root set disjoint",
          _inj.artifact_roots == ["attempts"]
          and {"overlapping-roots", "missing-attempts-root"} <= _inj.codes()
          and _inj.outcome == "degraded")
    # ...and a root intruding into the assets preview area is dropped visibly
    _assets_root = bschema.read_manifest_text(json.dumps({
        "schema_version": 2,
        "lesson_uid": "0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60",
        "entry": "index.html",
        "pages": [{"id": "pg_assets001", "path": "index.html"}],
        "artifact_roots": ["assets/work", "attempts"],
    }))
    check("asset-nested artifact root is dropped with overlapping-roots",
          _assets_root.artifact_roots == ["attempts"]
          and "overlapping-roots" in _assets_root.codes()
          and _assets_root.outcome == "degraded")

    # v1 keeps its historical surface: an undeclared page under attempts/
    # (v1 tolerance allows selecting it) still serves
    (_v1_dir / "attempts").mkdir(exist_ok=True)
    (_v1_dir / "attempts" / "extra.html").write_text(
        "<html>Vera Example v1 undeclared page</html>", encoding="utf-8")
    check("v1 undeclared page under attempts/ stays servable",
          c.get(f"/learn/lessons/{_v1_id}/files/attempts/extra.html").status_code == 200)

    # the legacy flat-file bridge refuses a symlinked source (§2)
    _leg_conn = get_conn()
    try:
        _leg_id = lessons_svc.create_lesson(_leg_conn, "Legacy Symlink Demo")
        _leg = lessons_svc.get_lesson(_leg_conn, _leg_id)
    finally:
        _leg_conn.close()
    _leg_dir = Path(lessons_svc.LESSONS_DIR) / _leg["slug"]
    (_leg_dir / "index.html").unlink(missing_ok=True)
    _os.symlink(_symp_target, Path(lessons_svc.LESSONS_DIR) / f"{_leg['slug']}.html")
    lessons_svc.lesson_file_info(_leg)  # runs the ensure/bridge path
    check("legacy flat-file bridge refuses a symlinked source (§2)",
          not (_leg_dir / "index.html").exists())
    # ...while a regular legacy source still bridges (fd-bound read)
    _leg_flat = Path(lessons_svc.LESSONS_DIR) / f"{_leg['slug']}.html"
    _leg_flat.unlink()
    _leg_flat.write_text("<html>Vera Example legacy body</html>", encoding="utf-8")
    lessons_svc.lesson_file_info(_leg)
    check("legacy flat-file bridge still copies a regular source",
          (_leg_dir / "index.html").is_file()
          and "Vera Example legacy body" in (_leg_dir / "index.html").read_text(encoding="utf-8"))

    # hostile manifests stay bounded: finding count, deep JSON, malformed URL
    _flood = bschema.read_manifest_text(json.dumps({
        "schema_version": 2,
        "lesson_uid": "0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60",
        "entry": "index.html",
        "pages": [{"id": "pg_flood0001", "path": "index.html"}] + [7] * 5000,
    }))
    check("hostile manifest findings stay bounded",
          _flood.outcome == "rejected"
          and len(_flood.findings) <= bschema.MAX_FINDINGS + 5)
    _deep = bschema.read_manifest_text('{"x":' * 5000 + "1" + "}" * 5000)
    check("pathologically deep JSON is manifest-unreadable, not a crash",
          _deep.outcome == "rejected" and "manifest-unreadable" in _deep.codes())
    _badurl = bschema.read_manifest_text(json.dumps({
        "schema_version": 2,
        "lesson_uid": "0d3f2b9a-6e4c-4f7d-8a1b-5c9e7d2f4a60",
        "slug": "vera-example", "title": "Vera Example",
        "source_url": "http://[::1",
        "entry": "index.html",
        "pages": [{"id": "pg_badurl001", "path": "index.html"}],
    }))
    check("malformed source_url copy degrades to stale-metadata, not a crash",
          _badurl.outcome == "ok" and "stale-metadata" in _badurl.codes())
    _nan = bschema.read_manifest_text('{"schema_version": 2, "x_weight": NaN}')
    check("non-standard JSON constants are manifest-unreadable",
          _nan.outcome == "rejected" and "manifest-unreadable" in _nan.codes())
    _bigint = bschema.read_manifest_text(
        '{"schema_version": 2, "x_big": ' + "9" * 5000 + "}")
    check("huge integer token is manifest-unreadable, not a crash",
          _bigint.outcome == "rejected" and "manifest-unreadable" in _bigint.codes())
    _inf = bschema.read_manifest_text('{"schema_version": 2, "x_big": 1e10000}')
    check("overflowing float token is manifest-unreadable (writer stays JSON)",
          _inf.outcome == "rejected" and "manifest-unreadable" in _inf.codes())

    # v2 selections compare exactly (§4.1): a normalizable variant is not repaired
    _norm_meta = c.get(f"/learn/lessons/{_v2_id}/preview-meta",
                       params={"entry": "./index.html"}).json()
    check("normalizable v2 selection degrades instead of silent repair (§4.1)",
          _norm_meta["outcome"] == "degraded"
          and any(f["code"] == "invalid-entry" for f in _norm_meta["findings"])
          and _norm_meta["path"].endswith("/index.html"))
    _norm_conn = get_conn()
    try:
        _norm_refused = False
        try:
            lessons_svc.set_current_entry(_norm_conn, _v2_id, "./related/01-stage.html")
        except lessons_svc.LessonError:
            _norm_refused = True
        _norm_after = lessons_svc.get_lesson(_norm_conn, _v2_id)
    finally:
        _norm_conn.close()
    check("set_current_entry refuses a normalizable v2 variant, stores exact paths",
          _norm_refused and _norm_after["current_entry"] == "related/01-stage.html")

    # --- C4: v1→v2 migration tool (learn-bundle-spec.md §10) -----------------
    import contextlib as _contextlib
    import io as _io

    from scripts import migrate_bundles as mig

    _mig_case = next(
        _c for _c in _fx_cases["cases"] if _c["file"] == "v1-migrated.json")
    _mig_uid = _mig_case["context"]["lesson_uid"]
    _mig_dir = Path(lessons_svc.LESSONS_DIR) / "vera-example-tides"
    _mig_dir.mkdir(exist_ok=True)
    _mig_v1_text = (_fx_dir / "v1-valid.json").read_text(encoding="utf-8")
    (_mig_dir / "lesson.json").write_text(_mig_v1_text, encoding="utf-8")
    (_mig_dir / "index.html").write_text(
        "<html>Vera Example tides page</html>", encoding="utf-8")
    _mig_db = {
        "uid": _mig_uid,
        "slug": "vera-example-tides",
        "title": "Vera Example: Why Tides Happen",
        "source_url": "https://learning.example/tides-101",
        "current_entry": _mig_case["context"]["db_current_entry"],
    }
    _mig_plan = mig.plan_bundle(_mig_dir, _mig_db)
    _mig_expected = (_fx_dir / "v1-migrated.json").read_text(encoding="utf-8")
    check("migration output matches the fixture pair byte-exactly (§10/§11)",
          _mig_plan.action == mig.ACTION_MIGRATE
          and _mig_plan.new_text == _mig_expected,
          f"action={_mig_plan.action} reasons={_mig_plan.reasons}")
    check("migration plan is deterministic across reruns",
          mig.plan_bundle(_mig_dir, _mig_db).new_text == _mig_plan.new_text)
    _mig_page_hash = hashlib.sha256(
        (_mig_dir / "index.html").read_bytes()).hexdigest()

    _mig_rb1 = mig.MIGRATIONS_DIR / "v1v2-test-apply"
    _mig_rb1.mkdir(parents=True)
    (_mig_rb1 / "rollback.json").write_text(
        json.dumps({"created_at": "test", "entries": []}) + "\n", encoding="utf-8")
    _mig_errors = mig.apply_plan(_mig_dir, _mig_plan, _mig_db, _mig_rb1)
    check("apply writes the planned bytes atomically and post-verifies clean",
          _mig_errors == []
          and (_mig_dir / "lesson.json").read_text(encoding="utf-8") == _mig_expected,
          "; ".join(_mig_errors))
    check("HTML page bytes are untouched by migration (§10)",
          hashlib.sha256((_mig_dir / "index.html").read_bytes()).hexdigest()
          == _mig_page_hash)
    check("migration is idempotent: a v2 manifest replans as a no-op",
          mig.plan_bundle(_mig_dir, _mig_db).action == mig.ACTION_NOOP)

    _mig_ledger = json.loads(
        (_mig_rb1 / "rollback.json").read_text(encoding="utf-8"))
    check("rollback ledger records the old/new manifest hashes",
          [e["slug"] for e in _mig_ledger["entries"]] == ["vera-example-tides"]
          and _mig_ledger["entries"][0]["old_sha256"]
          == hashlib.sha256(_mig_v1_text.encode()).hexdigest()
          and (_mig_rb1 / "vera-example-tides.lesson.json").read_text(encoding="utf-8")
          == _mig_v1_text)
    with _contextlib.redirect_stdout(_io.StringIO()) as _mig_out:
        _mig_rb_code = mig.rollback(_mig_rb1)
    check("rollback restores the pre-migration manifest byte-exactly",
          _mig_rb_code == 0
          and (_mig_dir / "lesson.json").read_text(encoding="utf-8") == _mig_v1_text)
    # a manifest edited after migration is refused, never overwritten
    _mig_errors2 = mig.apply_plan(_mig_dir, _mig_plan, _mig_db, _mig_rb1)
    _mig_edited = _mig_expected.replace(
        '"schema_version": 2', '"schema_version": 2, "x_agent_edit": true')
    (_mig_dir / "lesson.json").write_text(_mig_edited, encoding="utf-8")
    with _contextlib.redirect_stdout(_io.StringIO()):
        _mig_rb_code2 = mig.rollback(_mig_rb1)
    check("rollback refuses a manifest edited since migration",
          _mig_errors2 == [] and _mig_rb_code2 == 1
          and (_mig_dir / "lesson.json").read_text(encoding="utf-8") == _mig_edited)

    # §10: a valid DB current_entry absent from the v1 list folds in at the
    # head with entry unchanged; null source_url/updated_by_agent_at are
    # omitted; a malformed updated_by_agent_at is preserved verbatim
    _mig_head_dir = Path(lessons_svc.LESSONS_DIR) / "vera-example-head"
    _mig_head_dir.mkdir(exist_ok=True)
    (_mig_head_dir / "lesson.json").write_text(json.dumps({
        "schema_version": 1,
        "entry": "index.html",
        "related": ["related/01-extra.html"],
        "source_url": None,
        "updated_by_agent_at": None,
    }) + "\n", encoding="utf-8")
    _mig_head_db = {"uid": "2c8f0d0f-5b6e-4a1b-8d2e-3b9c8e4f2a15",
                    "slug": "vera-example-head",
                    "title": "Vera Example Head",
                    "current_entry": "related/09-note.html"}
    _mig_head = mig.plan_bundle(_mig_head_dir, _mig_head_db)
    _mig_head_obj = json.loads(_mig_head.new_text)
    check("valid DB current_entry folds in at the head, entry unchanged (§10)",
          _mig_head.action == mig.ACTION_MIGRATE
          and _mig_head_obj["entry"] == "index.html"
          and [p["path"] for p in _mig_head_obj["pages"]]
          == ["related/09-note.html", "index.html", "related/01-extra.html"]
          and _mig_head_obj["pages"][0]["id"]
          == mig.deterministic_page_id(_mig_head_db["uid"], "related/09-note.html"))
    check("null source_url and updated_by_agent_at copies are omitted (§10)",
          "source_url" not in _mig_head_obj
          and "updated_by_agent_at" not in _mig_head_obj)
    check("missing v1 slug/title copies are filled from the DB row (§12)",
          _mig_head_obj["slug"] == "vera-example-head"
          and _mig_head_obj["title"] == "Vera Example Head"
          and sum("filled from the DB row" in n for n in _mig_head.notes) == 2)
    _mig_nometa = mig.plan_bundle(
        _mig_head_dir, {"uid": _mig_head_db["uid"], "slug": "vera-example-head"})
    check("no usable title anywhere stops the migration",
          _mig_nometa.action == mig.ACTION_STOP
          and any("no usable title" in r for r in _mig_nometa.reasons))
    # an invalid source_url copy is never emitted: DB value wins, else omitted
    (_mig_head_dir / "lesson.json").write_text(json.dumps({
        "schema_version": 1, "entry": "index.html",
        "source_url": "not a url",
    }) + "\n", encoding="utf-8")
    _mig_badsrc = mig.plan_bundle(_mig_head_dir, dict(
        _mig_head_db, current_entry=None,
        source_url="https://learning.example/vera-head"))
    _mig_badsrc2 = mig.plan_bundle(_mig_head_dir, dict(_mig_head_db, current_entry=None))
    check("invalid source_url copy: DB fallback, else omitted (§4)",
          _mig_badsrc.action == mig.ACTION_MIGRATE
          and json.loads(_mig_badsrc.new_text)["source_url"]
          == "https://learning.example/vera-head"
          and _mig_badsrc2.action == mig.ACTION_MIGRATE
          and "source_url" not in json.loads(_mig_badsrc2.new_text)
          and any("omitted" in n for n in _mig_badsrc2.notes))

    # the §4 bound is on the emitted value's length, not its stripped form
    (_mig_head_dir / "lesson.json").write_text(json.dumps({
        "schema_version": 1, "entry": "index.html",
        "title": " " + "x" * 240 + " ",
    }) + "\n", encoding="utf-8")
    _mig_longtitle = mig.plan_bundle(
        _mig_head_dir, dict(_mig_head_db, current_entry=None))
    check("over-long title copy falls back to the DB row, never emitted (§4)",
          _mig_longtitle.action == mig.ACTION_MIGRATE
          and json.loads(_mig_longtitle.new_text)["title"] == "Vera Example Head")
    (_mig_head_dir / "lesson.json").write_text(json.dumps({
        "schema_version": 1,
        "entry": "index.html",
        "updated_by_agent_at": "soon-ish",
    }) + "\n", encoding="utf-8")
    _mig_soon = mig.plan_bundle(_mig_head_dir, dict(_mig_head_db, current_entry=None))
    check("malformed updated_by_agent_at is preserved verbatim (§10)",
          _mig_soon.action == mig.ACTION_MIGRATE
          and json.loads(_mig_soon.new_text)["updated_by_agent_at"] == "soon-ish")

    # §10 positive path: unknown members of an object-form related item are
    # copied verbatim onto the generated page object, in canonical key order
    (_mig_head_dir / "lesson.json").write_text(json.dumps({
        "schema_version": 1,
        "entry": "index.html",
        "related": [{"path": "related/01-extra.html",
                     "x_meta": "Vera Example extra member"}],
    }) + "\n", encoding="utf-8")
    _mig_extras = mig.plan_bundle(_mig_head_dir, dict(_mig_head_db, current_entry=None))
    _mig_extras_page = json.loads(_mig_extras.new_text)["pages"][1]
    check("object-form item extras ride the generated page object (§10)",
          _mig_extras.action == mig.ACTION_MIGRATE
          and list(_mig_extras_page) == ["id", "path", "x_meta"]
          and _mig_extras_page["x_meta"] == "Vera Example extra member"
          and _mig_extras_page["id"]
          == mig.deterministic_page_id(_mig_head_db["uid"], "related/01-extra.html"))

    # a manifest edited between plan and apply is refused, never overwritten
    _mig_race = mig.plan_bundle(_mig_head_dir, dict(_mig_head_db, current_entry=None))
    _mig_race_edit = json.dumps({
        "schema_version": 1, "entry": "index.html",
        "x_note": "Vera Example concurrent edit",
    }) + "\n"
    (_mig_head_dir / "lesson.json").write_text(_mig_race_edit, encoding="utf-8")
    _mig_race_errors = mig.apply_plan(_mig_head_dir, _mig_race, _mig_head_db, _mig_rb1)
    check("apply refuses a manifest edited since planning",
          _mig_race.action == mig.ACTION_MIGRATE
          and any("changed since planning" in e for e in _mig_race_errors)
          and (_mig_head_dir / "lesson.json").read_text(encoding="utf-8")
          == _mig_race_edit)

    # §10 stop-before-write conditions leave the manifest untouched
    def _mig_stop_case(label: str, manifest: dict, needle: str) -> None:
        _stop_dir = Path(lessons_svc.LESSONS_DIR) / "vera-example-stop"
        _stop_dir.mkdir(exist_ok=True)
        _stop_text = json.dumps(manifest) + "\n"
        (_stop_dir / "lesson.json").write_text(_stop_text, encoding="utf-8")
        _stop_plan = mig.plan_bundle(
            _stop_dir, {"uid": "3d9a1e1a-6c7f-4b2c-9e3f-4c0d9f5a3b26",
                        "slug": "vera-example-stop"})
        check(f"stop-before-write: {label}",
              _stop_plan.action == mig.ACTION_STOP
              and any(needle in r for r in _stop_plan.reasons)
              and (_stop_dir / "lesson.json").read_text(encoding="utf-8") == _stop_text,
              f"action={_stop_plan.action} reasons={_stop_plan.reasons}")

    _mig_stop_case(
        "unknown v1 key colliding with a v2-owned key",
        {"schema_version": 1, "entry": "index.html",
         "runtime": {"x": "Vera Example collision"}},
        "collides with a v2-owned key")
    _mig_stop_case(
        "object-form related item carrying a v2 page-object member",
        {"schema_version": 1, "entry": "index.html",
         "related": [{"path": "related/01-x.html", "id": "boom"}]},
        "colliding with the v2 page object")
    _mig_stop_case(
        "colliding member on a DROPPED (duplicate) item still stops",
        {"schema_version": 1, "entry": "index.html",
         "related": ["related/01-x.html",
                     {"path": "related/01-x.html", "id": "legacy"}]},
        "colliding with the v2 page object")
    _mig_stop_case(
        "normalized page path violating the v2 grammar",
        {"schema_version": 1, "entry": "index.html",
         "related": ["related/" + "n" * 250 + ".html"]},
        "violates the v2 grammar")
    (_mig_dir / "lesson.json").write_text(_mig_v1_text, encoding="utf-8")
    check("the tool never mints identity: no DB uid stops the migration",
          mig.plan_bundle(_mig_dir, {"slug": "vera-example-tides"}).action
          == mig.ACTION_STOP)

    # containment: a traversal DB slug stops before any filesystem join
    _esc_conn = get_conn()
    try:
        with _esc_conn:
            _esc_conn.execute(
                "INSERT INTO lessons (uid, title, slug, status, created_at) "
                "VALUES ('4e0b2f2b-7d8a-4c3d-af4e-5d1e0a6b4c37', "
                "'Vera Example Escape', '../../vera-escape', 'backlog', ?)",
                (db_mod.now_iso(),))
    finally:
        _esc_conn.close()
    with _contextlib.redirect_stdout(_io.StringIO()) as _esc_out:
        _esc_code = mig.run(dry_run=False, slugs=["../../vera-escape"])
    check("traversal DB slug stops before any filesystem join",
          _esc_code == 1 and "violates the slug grammar" in _esc_out.getvalue())
    _esc_conn = get_conn()
    try:
        with _esc_conn:
            _esc_conn.execute("DELETE FROM lessons WHERE slug='../../vera-escape'")
    finally:
        _esc_conn.close()

    # rollback trusts nothing: a symlinked bundle dir and a symlinked
    # rollback copy both refuse before any read or write through the link
    _rbh = mig.MIGRATIONS_DIR / "v1v2-test-hardening"
    _rbh.mkdir(parents=True)
    (_rbh / "rollback.json").write_text(json.dumps({"created_at": "test", "entries": [
        {"slug": "vera-example-rbsym", "file": "vera-example-rbsym.lesson.json",
         "old_sha256": hashlib.sha256(b"Vera Example old").hexdigest(),
         "new_sha256": hashlib.sha256(b"Vera Example new").hexdigest()}]}) + "\n",
        encoding="utf-8")
    _rbh_target = Path(lessons_svc.LESSONS_DIR) / "vera-rbsym-target"
    _rbh_target.mkdir(exist_ok=True)
    _os.symlink(_rbh_target, Path(lessons_svc.LESSONS_DIR) / "vera-example-rbsym")
    with _contextlib.redirect_stdout(_io.StringIO()) as _rbh_out:
        _rbh_code = mig.rollback(_rbh)
    check("rollback refuses a symlinked bundle dir",
          _rbh_code == 1 and "not a real directory" in _rbh_out.getvalue())
    (Path(lessons_svc.LESSONS_DIR) / "vera-example-rbsym").unlink()
    _rbh_dir = Path(lessons_svc.LESSONS_DIR) / "vera-example-rbsym"
    _rbh_dir.mkdir(exist_ok=True)
    (_rbh_dir / "lesson.json").write_bytes(b"Vera Example new")
    _os.symlink(_rbh / "rollback.json", _rbh / "vera-example-rbsym.lesson.json")
    with _contextlib.redirect_stdout(_io.StringIO()) as _rbh_out2:
        _rbh_code2 = mig.rollback(_rbh)
    check("rollback refuses a symlinked rollback copy",
          _rbh_code2 == 1 and "rollback copy is" in _rbh_out2.getvalue()
          and (_rbh_dir / "lesson.json").read_bytes() == b"Vera Example new")

    # a declared page that is a FIFO is noted, never opened blocking
    (_mig_head_dir / "related").mkdir(exist_ok=True)
    (_mig_head_dir / "lesson.json").write_text(json.dumps({
        "schema_version": 1, "entry": "index.html",
        "related": ["related/02-fifo.html"]}) + "\n", encoding="utf-8")
    _os.mkfifo(_mig_head_dir / "related" / "02-fifo.html")
    _mig_fifo = mig.plan_bundle(_mig_head_dir, dict(_mig_head_db, current_entry=None))
    check("declared FIFO page is noted and skipped, not opened blocking",
          _mig_fifo.action == mig.ACTION_MIGRATE
          and "related/02-fifo.html" not in _mig_fifo.page_hashes
          and any("not a regular file" in n for n in _mig_fifo.notes))

    # end-to-end run over the DB enumeration: dry-run writes nothing, the run
    # migrates, a rerun reports the no-op
    _mig_run_before = (_v1_dir / "lesson.json").read_text(encoding="utf-8")
    with _contextlib.redirect_stdout(_io.StringIO()) as _mig_dry_out:
        _mig_dry_code = mig.run(dry_run=True, slugs=[_v1["slug"]])
    check("dry-run plans the migration and writes nothing",
          _mig_dry_code == 0
          and "[migrate]" in _mig_dry_out.getvalue()
          and (_v1_dir / "lesson.json").read_text(encoding="utf-8") == _mig_run_before)
    with _contextlib.redirect_stdout(_io.StringIO()):
        _mig_run_code = mig.run(dry_run=False, slugs=[_v1["slug"]])
    _mig_run_read = bschema.read_manifest_text(
        (_v1_dir / "lesson.json").read_text(encoding="utf-8"))
    check("run migrates the enumerated bundle to clean v2",
          _mig_run_code == 0
          and _mig_run_read.version == 2
          and _mig_run_read.outcome == "ok"
          and _mig_run_read.lesson_uid == _v1["uid"])
    with _contextlib.redirect_stdout(_io.StringIO()) as _mig_rerun_out:
        _mig_rerun_code = mig.run(dry_run=True, slugs=[_v1["slug"]])
    check("rerun dry-run reports already-v2, no changes",
          _mig_rerun_code == 0 and "already-v2=1" in _mig_rerun_out.getvalue())

    tday = c.get("/today").text
    check("Today title carries the Ephemeris identity", "· Ephemeris" in tday)
    check("base metas rebranded to Ephemeris",
          'application-name" content="Ephemeris"' in tday)
    focus = c.get("/focus").text
    check("focus ring is a progress-driven astrolabe SVG",
          'class="astrolabe"' in focus and "astro-progress" in focus and 'id="focus-ring"' in focus)
    check("astrolabe keeps the timer ids", 'id="focus-time"' in focus and 'id="focus-start"' in focus)

    r = c.get("/items")
    check("GET /items 200", r.status_code == 200, str(r.status_code))
    check("items has Add form", 'action="/items"' in r.text)
    check("items seeded rows shown", "Sleep" in r.text or "Food" in r.text)

    suite_state.update({
        name: value for name, value in locals().items()
        if name not in {"client", "suite_state"}
    })
