"""Pinned lesson-libs shelf and its seeding into lesson bundles (#146)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from app.db import get_conn
from app.services import lessons


@pytest.fixture(scope="module", autouse=True)
def _started_app(client):
    yield


def _new_lesson(title: str) -> tuple[dict, Path]:
    conn = get_conn()
    try:
        lesson_id = lessons.create_lesson(conn, title)
        lesson = lessons.get_lesson(conn, lesson_id)
    finally:
        conn.close()
    return lesson, Path(lessons.LESSONS_DIR) / lesson["slug"]


def test_shelf_matches_its_checksums():
    """The repository's own copy is intact — a tampered or truncated checkout
    is caught here rather than seeded into every bundle."""
    entries = lessons.lesson_libs_manifest()
    assert entries, "the shelf inventory is empty"
    for shelf_rel, _bundle_rel, digest in entries:
        path = lessons.LESSON_LIBS_DIR / shelf_rel
        assert path.is_file(), f"{shelf_rel} listed in SHASUMS256 but missing"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, (
            f"{shelf_rel} does not match its recorded sha256"
        )

    # The starting stock the brief names, at the flattened paths pages use.
    bundle_paths = {bundle_rel for _shelf, bundle_rel, _digest in entries}
    for expected in (
        "assets/libs/d3/d3.min.js",
        "assets/libs/katex/katex.min.js",
        "assets/libs/katex/katex.min.css",
        "assets/libs/mermaid/mermaid.min.js",
    ):
        assert expected in bundle_paths, f"{expected} is not on the shelf"
    assert any(
        path.startswith("assets/libs/katex/fonts/") for path in bundle_paths
    ), "KaTeX ships without its fonts; katex.min.css references them relatively"

    # Every listed file lands inside the app-owned area, never above it.
    for _shelf, bundle_rel, _digest in entries:
        assert bundle_rel.startswith("assets/libs/") and ".." not in bundle_rel.split("/")


def test_terminal_open_seeds_the_shelf_into_the_bundle(client):
    lesson, lesson_dir = _new_lesson("Lesson Libs Seeded")
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None

    entries = lessons.lesson_libs_manifest()
    for _shelf_rel, bundle_rel, digest in entries:
        seeded = lesson_dir / bundle_rel
        assert seeded.is_file(), f"{bundle_rel} was not seeded"
        assert hashlib.sha256(seeded.read_bytes()).hexdigest() == digest
        # Copies, never hardlinks: a shared inode would let this bundle's
        # agent rewrite the shelf for every other lesson.
        assert seeded.stat().st_nlink == 1
        assert (
            seeded.stat().st_ino != (lessons.LESSON_LIBS_DIR / _shelf_rel).stat().st_ino
        )

    d3 = lesson_dir / "assets/libs/d3/d3.min.js"
    assert d3.read_bytes()[:64].startswith(b"// https://d3js.org")
    # The seeded area is inside the served preview surface (v2 `assets/`), so a
    # page can actually load what the brief tells it to reference — a shelf the
    # serving route refuses would be a shelf in name only.
    info = lessons.bundle_resource_info(lesson, "assets/libs/d3/d3.min.js")
    assert info["exists"] and info["size"] == d3.stat().st_size
    served = client.get(
        f"/learn/lessons/{lesson['id']}/files/assets/libs/d3/d3.min.js"
    )
    assert served.status_code == 200
    assert served.content == d3.read_bytes()


def test_seeding_is_idempotent_and_self_healing():
    lesson, lesson_dir = _new_lesson("Lesson Libs Repair")
    assert lessons.seed_lesson_libs(lesson_dir) > 0, "first open seeds the shelf"

    # An up-to-date seed is not rewritten: no copies, and the files the
    # agent's tooling may be watching keep their mtime.
    katex = lesson_dir / "assets/libs/katex/katex.min.js"
    before = katex.stat().st_mtime_ns
    assert lessons.seed_lesson_libs(lesson_dir) == 0, "second open re-copied an intact seed"
    assert katex.stat().st_mtime_ns == before

    # A modified or missing copy is restored on the next terminal open —
    # this is the whole reason the seed is checked by hash and not by name.
    katex.write_bytes(b"tampered")
    mermaid = lesson_dir / "assets/libs/mermaid/mermaid.min.js"
    mermaid.unlink()
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    digests = {
        bundle_rel: digest
        for _shelf, bundle_rel, digest in lessons.lesson_libs_manifest()
    }
    assert (
        hashlib.sha256(katex.read_bytes()).hexdigest()
        == digests["assets/libs/katex/katex.min.js"]
    )
    assert (
        hashlib.sha256(mermaid.read_bytes()).hexdigest()
        == digests["assets/libs/mermaid/mermaid.min.js"]
    )


def test_seeding_never_follows_a_planted_link_and_never_refuses_a_terminal():
    lesson, lesson_dir = _new_lesson("Lesson Libs Planted")
    outside = Path(lessons.LESSONS_DIR) / "libs-decoy"
    outside.mkdir(exist_ok=True)
    (lesson_dir / "assets").mkdir(exist_ok=True)
    link = lesson_dir / "assets" / "libs"
    link.symlink_to(outside, target_is_directory=True)

    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    assert not (link.is_symlink()), "the planted link survived the seed"
    assert (lesson_dir / "assets/libs/d3/d3.min.js").is_file()
    assert not any(outside.iterdir()), "the seed wrote through the link"

    # A shelf that cannot be read costs a warning, never the terminal.
    saved = lessons.LESSON_LIBS_DIR
    lessons.LESSON_LIBS_DIR = Path(lessons.LESSONS_DIR) / "no-such-shelf"
    try:
        assert lessons.seed_lesson_libs(lesson_dir) == 0
        assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    finally:
        lessons.LESSON_LIBS_DIR = saved


def test_seeded_copies_are_readable_by_the_learner_surface():
    _lesson, lesson_dir = _new_lesson("Lesson Libs Modes")
    lessons.seed_lesson_libs(lesson_dir)
    mode = (lesson_dir / "assets/libs/d3/d3.min.js").stat().st_mode & 0o777
    assert mode & 0o400, "seeded library is unreadable"
    assert not mode & 0o111, "seeded library should not be executable"
    assert os.access(lesson_dir / "assets/libs/d3", os.R_OK | os.X_OK)


def test_brief_points_the_study_agent_at_the_shelf():
    """Cheap drift guard: the shelf is useless if the brief never names it."""
    template = lessons._AGENTS_TEMPLATE
    assert "assets/libs" in template
    for path in (
        "assets/libs/d3/d3.min.js",
        "assets/libs/katex/katex.min.js",
        "assets/libs/mermaid/mermaid.min.js",
    ):
        assert path in template, f"the brief does not name {path}"
    assert "never edit, move or delete anything under" in template
    # The shelf must not quietly become the default over purpose-built visuals.
    assert "built for the exact point stays the default" in template
    # The CDN ban and the hand-vendoring rule stay in force beside it.
    assert "loading anything from a CDN" in template
