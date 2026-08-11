"""Cross-lesson learner memory — `memory.jsonl` (#114).

The tutor of one lesson may know what the learner demonstrated in the others.
These cases pin the read model down: the fold is the §6.5 fold applied per
lesson, the current lesson is excluded, the order is tree-closeness, and the
file is regenerated whole at terminal open.

Runs late in the cumulative suite: earlier files record assessments of their
own, so every case below asserts about ITS OWN lessons rather than the whole
file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import get_conn
from app.services import assessments, bundle_schema, learner_memory, lessons


@pytest.fixture(scope="module", autouse=True)
def _started_app(client):
    yield


def _lesson(title: str, path: str | None = None, step: int | None = None) -> dict:
    """One lesson with a bundle, optionally addressed in a track."""
    conn = get_conn()
    try:
        lesson_id = lessons.create_lesson(conn, f"#114 {title}")
        lesson = lessons.get_lesson(conn, lesson_id)
    finally:
        conn.close()
    manifest_path = Path(lessons.LESSONS_DIR) / lesson["slug"] / lessons.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if path is not None:
        manifest["path"] = path
        if step is not None:
            manifest["step"] = step
    bundle_schema.write_manifest(manifest_path, manifest)
    return lesson


def _record(lesson: dict, key: str, **payload) -> str:
    payload.setdefault("note", f"Vera Example: invented record {key}.")
    conn = get_conn()
    try:
        result = assessments.record_assessment(
            conn, lesson, {**payload, "idempotency_key": key}
        )
    finally:
        conn.close()
    return result["assessment_id"]


def _memory_path(lesson: dict) -> Path:
    return (
        Path(lessons.LESSONS_DIR) / lesson["slug"] / learner_memory.PROJECTION_NAME
    )


def _project(lesson: dict) -> list[dict]:
    """Regenerate this lesson's memory file and read back its lines."""
    conn = get_conn()
    try:
        assert learner_memory.reconcile_projection(conn, lesson) is True
    finally:
        conn.close()
    return [
        json.loads(line)
        for line in _memory_path(lesson).read_text(encoding="utf-8").splitlines()
    ]


def test_memory_folds_other_lessons_and_sorts_by_closeness():
    here = _lesson("here", "inv-tree/alpha", 1)
    near = _lesson("near sibling", "inv-tree/alpha", 7)
    cousin = _lesson("cousin", "inv-tree/beta", 2)
    far = _lesson("far", "inv-elsewhere", 3)
    loose = _lesson("no address")

    # `here` records too: its own state is `assessments.jsonl`, so none of it
    # may appear in its own memory file.
    _record(here, "114-here", kind="evidence", level="passed", basis="live",
            concepts=["invented-here-concept"])

    # `near` shows the fold: one concept corrected upwards, one retracted.
    weak = _record(near, "114-near-1", kind="evidence", level="weak",
                   basis="attempts", concepts=["invented-channels"])
    _record(near, "114-near-2", kind="evidence", level="passed",
            basis="artifacts", concepts=["invented-channels"], supersedes=weak)
    doomed = _record(near, "114-near-3", kind="evidence", level="developing",
                     basis="live", concepts=["invented-retracted"])
    _record(near, "114-near-4", kind="retraction", supersedes=doomed)
    _record(near, "114-near-5", kind="summary",
            note="Vera Example: traced a send/receive pair unaided.",
            next_action="invented: introduce buffering")

    for lesson, key in ((cousin, "114-cousin"), (far, "114-far"),
                        (loose, "114-loose")):
        _record(lesson, key, kind="evidence", level="seen", basis="live",
                concepts=[f"invented-{key}"])

    lines = _project(here)
    entries = {entry["slug"]: entry for entry in lines[1:]}
    order = [entry["slug"] for entry in lines[1:]]

    assert here["slug"] not in entries, (
        "the current lesson is excluded — its own record is assessments.jsonl"
    )
    assert (
        entries[near["slug"]]["concepts"]
        == [{"concept": "invented-channels", "level": "passed",
             "basis": "artifacts"}]
    ), "the §6.5 fold applies per lesson: superseded and retracted rows are gone"
    assert (
        entries[near["slug"]]["summary"]["next_action"]
        == "invented: introduce buffering"
        and entries[near["slug"]]["path"] == "inv-tree/alpha"
        and entries[near["slug"]]["step"] == 7
        and entries[near["slug"]]["title"] == near["title"]
    ), "an entry carries the latest summary and the address its manifest declares"
    assert entries[loose["slug"]]["path"] is None, (
        "a lesson whose manifest declares no path has no address"
    )
    assert (
        order.index(near["slug"])
        < order.index(cousin["slug"])
        < order.index(far["slug"])
        < order.index(loose["slug"])
    ), "closest track first, then cluster, then the rest, then no address at all"


def test_meta_line_versions_the_whole_file():
    here = _lesson("meta reader", "inv-meta/root", 4)
    studied = _lesson("meta neighbour", "inv-meta/root", 5)
    _record(studied, "114-meta", kind="evidence", level="developing",
            basis="mixed", concepts=["invented-meta-concept"])

    lines = _project(here)
    meta = lines[0]

    assert (
        meta["kind"] == "learner_memory_meta"
        and meta["format"] == 1
        and meta["scope"] == "all-studied-lessons"
        and meta["lesson_uid"] == here["uid"]
        and meta["slug"] == here["slug"]
        and meta["path"] == "inv-meta/root"
        and meta["step"] == 4
        and meta["entries"] == len(lines) - 1
        and meta["generated_at"].endswith("+00:00")
    ), "the meta line names the format, the scope, and the lesson it was cut for"
    assert all(entry["kind"] == "lesson_memory" for entry in lines[1:]), (
        "every other line is one studied lesson"
    )


def test_memory_jsonl_is_a_reserved_bundle_name():
    assert learner_memory.PROJECTION_NAME in bundle_schema.RESERVED_NAMES, (
        "an agent could otherwise shadow the file the app publishes"
    )
    assert (
        bundle_schema.valid_v2_path("memory.jsonl") is False
        and bundle_schema.valid_v2_path("memory.jsonl/notes.txt") is False
    ), "nothing in a manifest may claim the name, or nest under it"

    lesson = _lesson("reserved name claim")
    manifest_path = Path(lessons.LESSONS_DIR) / lesson["slug"] / lessons.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_roots"] = ["memory.jsonl", "invented-work"]
    bundle_schema.write_manifest(manifest_path, manifest)

    read = lessons.read_bundle_readonly(lesson)
    assert (
        "memory.jsonl" not in read.artifact_roots
        and "invented-work" in read.artifact_roots
        and any(finding.code == "invalid-path" for finding in read.findings)
    ), "a manifest claiming the projection name is refused, visibly"


def test_terminal_open_regenerates_the_file():
    here = _lesson("terminal reader", "inv-open/root", 1)
    studied = _lesson("terminal neighbour", "inv-open/root", 2)
    _record(studied, "114-open", kind="evidence", level="weak", basis="runs",
            concepts=["invented-open-concept"])

    published = _project(here)
    _memory_path(here).write_text("planted by the agent\n", encoding="utf-8")

    assert lessons.prepare_terminal_workspace(here["slug"]) is not None
    lines = [
        json.loads(line)
        for line in _memory_path(here).read_text(encoding="utf-8").splitlines()
    ]

    assert (
        [entry["slug"] for entry in lines[1:]]
        == [entry["slug"] for entry in published[1:]]
    ), "a lesson-agent terminal open republishes the file over whatever it finds"

    # ...and the newest state is what it republishes: evidence recorded
    # elsewhere after the last render arrives at THIS lesson's next open.
    _record(studied, "114-open-2", kind="evidence", level="passed",
            basis="artifacts", concepts=["invented-open-later"])
    assert lessons.prepare_terminal_workspace(here["slug"]) is not None
    refreshed = [
        json.loads(line)
        for line in _memory_path(here).read_text(encoding="utf-8").splitlines()
    ]
    entry = next(e for e in refreshed[1:] if e["slug"] == studied["slug"])
    assert [c["concept"] for c in entry["concepts"]] == [
        "invented-open-concept", "invented-open-later",
    ], "the next open carries what the other lesson recorded meanwhile"
