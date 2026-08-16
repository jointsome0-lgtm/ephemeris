"""The split brief (#195): a small always-on core plus on-demand companions.

The split exists because agent harnesses cap the project brief they
auto-load — Codex reads the first `project_doc_max_bytes` (32 KiB by
default) of AGENTS.md and silently drops the rest, and the old 46 KB
template lost its own tail sections plus the appended STATE in every real
bundle. These tests pin the two halves of the fix: the core (with STATE
injected near the top) must never grow back toward the cap, and the
reference files the core points at must actually land in the bundle.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from app.db import get_conn
from app.services import bundle_schema, lessons

CODEX_PROJECT_DOC_CAP = 32 * 1024


def _lesson(title: str) -> tuple[dict, Path]:
    conn = get_conn()
    try:
        lesson_id = lessons.create_lesson(conn, title)
        lesson = lessons.get_lesson(conn, lesson_id)
    finally:
        conn.close()
    return lesson, Path(lessons.LESSONS_DIR) / lesson["slug"]


def test_core_brief_stays_far_under_the_codex_cap():
    rendered = lessons._source_brief(lessons._AGENTS_TEMPLATE, Path("made"))
    assert rendered.count("%STATE%") == 1, "exactly one STATE slot"
    core = rendered.replace("%STATE%", "")
    # Half the cap, not the cap: STATE shares the file, grows with the
    # lesson (open questions, artifacts), and has no fixed ceiling.
    assert len(core.encode("utf-8")) < CODEX_PROJECT_DOC_CAP // 2, (
        "the always-on core crept back toward the truncation cap — move the "
        "growth into a reference/ companion instead (#195)"
    )
    # The no-source variant may not be larger than the with-source one by
    # more than the slot texts differ; render it too so both stay bounded.
    bare = lessons._source_brief(lessons._AGENTS_TEMPLATE, None).replace("%STATE%", "")
    assert len(bare.encode("utf-8")) < CODEX_PROJECT_DOC_CAP // 2


def test_state_is_capped_so_the_core_tail_always_loads():
    small = "\n## STATE (generated; refreshed on every terminal open)\n\n- x"
    assert lessons._cap_state(small) == small, "an ordinary STATE passes as-is"

    filler = "".join(
        f'  - "attempts/blk_x/{i:04d}-{"f" * 160}.txt": mtime=…\n'
        for i in range(400)
    )
    state = "\n## STATE (generated; refreshed on every terminal open)\n\n" + filler
    assert len(state.encode("utf-8")) > CODEX_PROJECT_DOC_CAP, (
        "the fixture must be worse than the harness cap on its own"
    )
    brief = lessons._render_agents_brief(Path("made"), state)
    assert len(brief.encode("utf-8")) < CODEX_PROJECT_DOC_CAP, (
        "an artifact-heavy STATE must never push the core past what the "
        "harness loads (#197 round 2)"
    )
    assert "## Mission" in brief and "## Bundle map and git" in brief
    cut = brief.index("  - …STATE hit its size budget")
    assert brief[cut - 1] == "\n", "the cap cuts at a whole line"
    assert brief.index("## Data boundary") < brief.index("## STATE (generated")


def test_open_question_excerpts_are_byte_bounded_so_the_cap_never_eats_them():
    import json

    from app.services import attempts

    # json.dumps sextuples non-ASCII via \uXXXX; the upstream char bound
    # alone let 12 CJK questions outgrow the whole STATE budget.
    dumped, cut = lessons._state_json_excerpt("問" * attempts.STATE_QUESTION_CHARS)
    assert cut and len(dumped) <= lessons._STATE_LINE_MAX_BYTES
    json.loads(dumped)

    plain = "e" * attempts.STATE_QUESTION_CHARS
    assert lessons._state_json_excerpt(plain) == (json.dumps(plain), False), (
        "ASCII excerpts inside the byte bound pass through untouched"
    )

    # The full surfaced set plus generous per-line overhead must fit the
    # STATE budget with room to spare — the section the core says to answer
    # FIRST can never be the part the tail cap removes.
    worst_section = attempts.OPEN_QUESTIONS_SHOWN * (
        lessons._STATE_LINE_MAX_BYTES + 200
    )
    assert worst_section < lessons._STATE_MAX_BYTES - 4096


def test_every_reference_pointer_in_the_core_names_a_written_file():
    pointed = set(re.findall(r"`reference/([a-z-]+\.md)`", lessons._AGENTS_TEMPLATE))
    assert pointed == set(lessons._REFERENCE_FILES), (
        "the core's reference/ pointers and the files the app writes must "
        "stay the same set"
    )
    for text in lessons._REFERENCE_FILES.values():
        assert "%STATE%" not in text and "%SOURCE" not in text, (
            "reference companions are constant templates — no slots"
        )


def test_reference_name_is_reserved_and_git_excluded():
    assert "reference" in bundle_schema.RESERVED_NAMES, (
        "spec §2: no page, block file, or artifact root may claim the "
        "companions' directory"
    )
    assert not bundle_schema.valid_v2_path("reference/anything.html")
    assert "/reference/" in lessons.BUNDLE_GIT_EXCLUDE, (
        "app-owned companions stay out of `git add -A` like the briefs do"
    )


def test_prepare_writes_state_up_top_and_the_companions(client):
    lesson, lesson_dir = _lesson("Reference Companion Fixture")
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None

    brief = (lesson_dir / lessons.AGENTS_FILENAME).read_text(encoding="utf-8")
    assert brief.startswith("# Lesson workspace")
    assert "%STATE%" not in brief
    assert (
        brief.index("## Data boundary")
        < brief.index("## STATE (generated")
        < brief.index("## Mission")
    ), (
        "the boundary precedes STATE (learner-controlled excerpts are never "
        "read before the rule that frames them), and STATE still sits near "
        "the top so a truncating harness sees this session's facts"
    )
    assert len(brief.encode("utf-8")) < CODEX_PROJECT_DOC_CAP, (
        "a fresh lesson's whole AGENTS.md must fit what Codex actually reads"
    )

    for name, text in lessons._REFERENCE_FILES.items():
        path = lesson_dir / lessons.REFERENCE_DIR_NAME / name
        assert path.is_file() and not path.is_symlink(), name
        assert path.read_text(encoding="utf-8") == text, name


def test_companion_writes_replace_planted_nodes_and_keep_foreign_bytes(client):
    lesson, lesson_dir = _lesson("Reference Collision Fixture")
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    ref_dir = lesson_dir / lessons.REFERENCE_DIR_NAME

    # A tutor-authored file on one of our names is kept, moved aside.
    foreign = ref_dir / "bridge.md"
    foreign.write_text("authored under the old contract", encoding="utf-8")
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    asides = list(ref_dir.glob("bridge.md.collision-*"))
    assert (
        len(asides) == 1
        and asides[0].read_text(encoding="utf-8")
        == "authored under the old contract"
        and foreign.read_text(encoding="utf-8")
        == lessons._REFERENCE_FILES["bridge.md"]
    ), "foreign bytes survive; the companion is republished"

    # Reopening over our own unchanged output piles up no aside copies.
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    assert len(list(ref_dir.glob("bridge.md.collision-*"))) == 1

    # A link on the directory name is moved aside, never followed.
    target = lesson_dir.parent / f"{lesson['slug']}-decoy"
    target.mkdir(exist_ok=True)
    for child in ref_dir.iterdir():
        child.unlink()
    ref_dir.rmdir()
    os.symlink(target, ref_dir)
    assert lessons.prepare_terminal_workspace(lesson["slug"]) is not None
    assert ref_dir.is_dir() and not ref_dir.is_symlink()
    assert not any(target.iterdir()), "nothing was written through the link"
    assert (ref_dir / "record.md").is_file()
