"""Each lesson bundle is its own local git repository (#186).

Per-lesson history is what a learner rolls back with and what a later tutor
session reads instead of guessing. The app owns exactly two guarantees — the
repo exists and `.git` never leaves the machine through the serving surface —
so these cases pin the init down as once-only, best-effort, link-refusing, and
unservable. Committing is the agent's job and is asserted only as the brief
line that asks for it.

Seeds lessons of its own and asserts nothing about anyone else's counts.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.db import get_conn
from app.services import bundle_schema, lessons


@pytest.fixture(scope="module", autouse=True)
def _started_app(client):
    yield


def _lesson(title: str) -> dict:
    conn = get_conn()
    try:
        lesson_id = lessons.create_lesson(conn, f"#186 {title}")
        return lessons.get_lesson(conn, lesson_id)
    finally:
        conn.close()


def _bundle(lesson: dict) -> Path:
    return Path(lessons.LESSONS_DIR) / lesson["slug"]


def _counting_run(calls: list):
    real = subprocess.run

    def run(argv, *args, **kwargs):
        calls.append(argv)
        return real(argv, *args, **kwargs)

    return run


def test_bundle_ensure_inits_one_repo_with_a_gitignore():
    lesson = _lesson("init")
    bundle = _bundle(lesson)

    assert (bundle / ".git").is_dir(), (
        "creating a lesson leaves it a git repository"
    )
    assert (bundle / ".gitignore").read_text(encoding="utf-8") == "node_modules/\n", (
        "the mount point is the one path that must never enter history: the "
        "build workspace is bound over <bundle>/node_modules in the sandbox"
    )
    assert subprocess.run(
        ["git", "-C", str(bundle), "rev-parse", "--is-inside-work-tree"],
        capture_output=True, text=True, timeout=10,
    ).stdout.strip() == "true", "git itself agrees the bundle is a work tree"


def test_repeated_ensures_neither_re_init_nor_rewrite(monkeypatch):
    lesson = _lesson("idempotent")
    bundle = _bundle(lesson)
    # An agent's own ignore rules are its business; the app writes the file at
    # init and never again.
    (bundle / ".gitignore").write_text(
        "node_modules/\ninvented-agent-rule/\n", encoding="utf-8"
    )

    calls: list = []
    monkeypatch.setattr(lessons.subprocess, "run", _counting_run(calls))
    for _ in range(3):
        lessons.lesson_file_info(lesson)

    assert calls == [], "the .git check is cheap and the subprocess never reruns"
    assert (
        "invented-agent-rule/" in (bundle / ".gitignore").read_text(encoding="utf-8")
    ), "a bundle's own ignore rules survive every later read"


def test_git_is_reserved_and_never_served(client):
    v2 = _lesson("reserved v2")
    v1 = _lesson("reserved v1")
    # v1 predates artifact roots and serves any non-reserved file, so the
    # reserved list is the whole of its defence.
    manifest_path = _bundle(v1) / lessons.MANIFEST_NAME
    bundle_schema.write_manifest(manifest_path, {
        "schema_version": 1,
        "slug": v1["slug"],
        "title": v1["title"],
        "entry": "index.html",
    })

    assert ".git" in bundle_schema.RESERVED_NAMES, (
        "no page, block file, or artifact root may claim the repository"
    )
    reads = {
        lesson["slug"]: lessons._ensure_bundle_manifest(lesson) for lesson in (v1, v2)
    }
    assert (
        not reads[v1["slug"]].rejected
        and reads[v1["slug"]].version == bundle_schema.SCHEMA_V1
        and not reads[v2["slug"]].rejected
        and reads[v2["slug"]].version == bundle_schema.SCHEMA_V2
    ), "both refusals below are the reserved name, not a rejected manifest"
    for lesson in (v1, v2):
        assert (_bundle(lesson) / ".git" / "config").is_file(), (
            "the file the route must refuse really is on disk"
        )
        for ref in (".git/config", ".git/HEAD"):
            response = client.get(
                f"/learn/lessons/{lesson['id']}/files/{ref}"
            )
            assert response.status_code == 404, (
                f"{ref} is refused on schema v{1 if lesson is v1 else 2}, "
                f"got {response.status_code}"
            )


def test_a_planted_git_link_is_skipped_not_followed(tmp_path):
    lesson = _lesson("planted link")
    bundle = _bundle(lesson)
    shutil.rmtree(bundle / ".git")
    target = tmp_path / "invented-elsewhere"
    target.mkdir()
    (bundle / ".git").symlink_to(target, target_is_directory=True)

    lessons.lesson_file_info(lesson)

    assert (bundle / ".git").is_symlink(), "the link is left exactly as found"
    assert list(target.iterdir()) == [], (
        "nothing is initialized through a link — a repo outside the bundle is "
        "not this app's to create (§2)"
    )


def test_a_missing_git_leaves_the_bundle_readable(monkeypatch):
    def no_git(argv, *args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory: 'git'")

    monkeypatch.setattr(lessons.subprocess, "run", no_git)
    lesson = _lesson("no git binary")
    bundle = _bundle(lesson)

    assert not (bundle / ".git").exists(), "the init could not happen"
    assert not (bundle / ".gitignore").exists(), (
        "and nothing is written that would claim it did"
    )
    info = lessons.lesson_file_info(lesson)
    assert info and (bundle / lessons.MANIFEST_NAME).is_file(), (
        "history is derived, best-effort state: the read path does not care"
    )


def test_the_brief_asks_the_agent_to_commit():
    brief = lessons._AGENTS_TEMPLATE

    assert "`.git/`" in brief and "git log" in brief, (
        "the tutor is told the bundle is a repository and how to read its past"
    )
    assert "Commit at every checkpoint" in brief, (
        "commits are the agent's job — the app writes none (#186 non-goal)"
    )
