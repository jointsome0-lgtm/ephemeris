"""Pinned lesson-libs shelf and its seeding into lesson bundles (#146)."""
from __future__ import annotations

import hashlib
import os
import stat
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

    # Every listed file lands inside the app-owned area, never above it.
    for _shelf, bundle_rel, _digest in entries:
        assert bundle_rel.startswith("assets/libs/") and ".." not in bundle_rel.split("/")


def test_katex_css_carries_its_fonts_inline():
    """A lesson page runs on an opaque origin (`sandbox allow-scripts`), where a
    `url(fonts/…)` face is a CORS request the app answers without an
    Access-Control header — i.e. blocked, and KaTeX falls back to system
    glyphs. `font-src 'self' data:` is the way through."""
    css = (lessons.LESSON_LIBS_DIR / "katex/0.17.0/katex.min.css").read_text(
        encoding="utf-8"
    )
    assert "url(fonts/" not in css, "KaTeX CSS still fetches its fonts as files"
    assert css.count("url(data:font/woff2;base64,") == css.count("@font-face"), (
        "every KaTeX face must carry its woff2 inline"
    )
    # d3's string-compiling APIs stay unavailable under that CSP; the brief
    # says so rather than the shelf pretending otherwise.
    assert "d3.csvParseRows" in lessons._AGENTS_TEMPLATE


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


def test_first_seed_preserves_a_library_the_agent_vendored_itself():
    """`assets/libs/` was the agent's to use before the shelf existed, so the
    first seed into an older bundle moves what it finds aside instead of
    destroying it. Once the bundle carries the stamp, the app republishes."""
    _lesson, lesson_dir = _new_lesson("Lesson Libs Preserve")
    older = lesson_dir / "assets/libs/d3/d3.min.js"
    older.parent.mkdir(parents=True)
    older.write_bytes(b"// d3 v6, vendored by hand before the shelf existed\n")

    lessons.seed_lesson_libs(lesson_dir)
    aside = list(older.parent.glob("d3.min.js.collision-*"))
    assert len(aside) == 1, "the hand-vendored copy was not preserved"
    assert aside[0].read_bytes().startswith(b"// d3 v6")
    stamp = lesson_dir / "assets/libs" / lessons.LESSON_LIBS_CHECKSUM_FILE
    assert stamp.is_file(), "the seeded area carries no ownership stamp"
    assert older.read_bytes()[:20] == b"// https://d3js.org "

    # Stamped: this is the app's area now, and an edit under it is replaced,
    # exactly as the brief promises — with no second aside copy.
    older.write_bytes(b"tampered")
    lessons.seed_lesson_libs(lesson_dir)
    assert len(list(older.parent.glob("d3.min.js.collision-*"))) == 1
    assert older.read_bytes()[:20] == b"// https://d3js.org "


def test_a_foreign_checksum_file_is_not_mistaken_for_the_stamp():
    """An older bundle could have vendored libraries under `assets/libs/` and
    recorded their hashes in a file at this very name. Only the app's own
    marker may switch off the pass that preserves them."""
    _lesson, lesson_dir = _new_lesson("Lesson Libs Foreign Stamp")
    libs = lesson_dir / "assets/libs"
    (libs / "d3").mkdir(parents=True)
    (libs / "d3/d3.min.js").write_bytes(b"// d3 v6, hand-vendored\n")
    foreign = libs / lessons.LESSON_LIBS_CHECKSUM_FILE
    foreign.write_text("0" * 64 + "  d3/d3.min.js\n", encoding="utf-8")

    lessons.seed_lesson_libs(lesson_dir)
    assert len(list((libs / "d3").glob("d3.min.js.collision-*"))) == 1, (
        "a foreign checksum file passed as the app's stamp and cost the library"
    )
    assert len(list(libs.glob(f"{lessons.LESSON_LIBS_CHECKSUM_FILE}.collision-*"))) == 1
    assert foreign.read_bytes().startswith(b"# ephemeris lesson-libs")


def test_a_hardlink_is_never_accepted_as_a_seeded_copy():
    """Matching bytes are not enough when the inode is shared: a later write
    through the bundle path would change the other name too. The seeder
    promises an independent copy, so it makes one."""
    _lesson, lesson_dir = _new_lesson("Lesson Libs Hardlink")
    shelf_d3 = lessons.LESSON_LIBS_DIR / "d3/7.9.0/d3.min.js"
    elsewhere = Path(lessons.LESSONS_DIR) / "shared-d3.min.js"
    elsewhere.write_bytes(shelf_d3.read_bytes())
    target = lesson_dir / "assets/libs/d3/d3.min.js"
    target.parent.mkdir(parents=True)
    os.link(elsewhere, target)
    assert target.stat().st_nlink == 2

    lessons.seed_lesson_libs(lesson_dir)
    assert target.stat().st_nlink == 1
    assert target.stat().st_ino != elsewhere.stat().st_ino
    assert target.read_bytes() == shelf_d3.read_bytes()


def test_a_symlinked_parent_is_replaced_even_when_its_content_matches():
    """The digest of a file reached THROUGH a symlinked parent says nothing:
    the preview route refuses symlinked paths (§2), so a bundle that passed the
    check would still fail to load the library."""
    _lesson, lesson_dir = _new_lesson("Lesson Libs Linked Parent")
    decoy = Path(lessons.LESSONS_DIR) / "libs-decoy-matching"
    (decoy / "d3").mkdir(parents=True, exist_ok=True)
    shelf_d3 = lessons.LESSON_LIBS_DIR / "d3/7.9.0/d3.min.js"
    (decoy / "d3/d3.min.js").write_bytes(shelf_d3.read_bytes())
    (lesson_dir / "assets").mkdir(exist_ok=True)
    (lesson_dir / "assets/libs").symlink_to(decoy, target_is_directory=True)

    lessons.seed_lesson_libs(lesson_dir)
    assert not (lesson_dir / "assets/libs").is_symlink()
    seeded = lesson_dir / "assets/libs/d3/d3.min.js"
    assert seeded.is_file() and seeded.read_bytes() == shelf_d3.read_bytes()


def test_a_fifo_on_a_seeded_name_does_not_park_the_terminal_open():
    """`_seeded_digest` opens non-blocking: a FIFO left on a seeded name would
    otherwise wait for a writer that never comes, on the terminal-open path."""
    lesson, lesson_dir = _new_lesson("Lesson Libs Fifo")
    target = lesson_dir / "assets/libs/d3/d3.min.js"
    target.parent.mkdir(parents=True)
    os.mkfifo(target)

    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    assert target.is_file() and not stat.S_ISFIFO(target.stat().st_mode)


def test_an_undecodable_inventory_costs_a_warning_not_the_terminal(tmp_path):
    """A damaged checkout must not escape the seeder as UnicodeDecodeError —
    `prepare_terminal_workspace()` calls it outside its own guard."""
    lesson, _lesson_dir = _new_lesson("Lesson Libs Damaged Shelf")
    broken = tmp_path / "shelf"
    broken.mkdir()
    (broken / lessons.LESSON_LIBS_CHECKSUM_FILE).write_bytes(b"\xff\xfe not utf-8\n")
    saved = lessons.LESSON_LIBS_DIR
    lessons.LESSON_LIBS_DIR = broken
    try:
        assert lessons.seed_lesson_libs(Path(lessons.LESSONS_DIR) / lesson["slug"]) == 0
        assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
        # A line that is not a checksum is a refusal, not a guessed path.
        (broken / lessons.LESSON_LIBS_CHECKSUM_FILE).write_text("nonsense\n")
        with pytest.raises(lessons.LessonError):
            lessons.lesson_libs_manifest()
        assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    finally:
        lessons.LESSON_LIBS_DIR = saved


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
