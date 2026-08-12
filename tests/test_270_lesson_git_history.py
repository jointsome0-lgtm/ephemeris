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


def _git(bundle: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(bundle), *args],
        capture_output=True, text=True, timeout=30,
    )


def test_bundle_ensure_inits_one_usable_repo():
    lesson = _lesson("init")
    bundle = _bundle(lesson)

    assert (bundle / ".git").is_dir(), (
        "creating a lesson leaves it a git repository"
    )
    assert _git(bundle, "rev-parse", "--is-inside-work-tree").stdout.strip() == (
        "true"
    ), "git itself agrees the bundle is a work tree"
    assert not (bundle / ".gitignore").exists(), (
        "`.gitignore` is the agent's to own — the app's rules live in the "
        "repository's own exclude file"
    )
    assert _git(bundle, "config", "user.email").stdout.strip().endswith(
        "@ephemeris.invalid"
    ), "a local identity, because the sandbox has no .gitconfig and no remote"


def test_a_commit_from_a_bare_environment_holds_authored_work_only():
    """The whole point of the identity and the exclude rules: an agent doing
    exactly what the brief asks — `git add -A` in a session whose `$HOME` has
    no git configuration at all — must produce a commit, and that commit must
    not carry app-owned state a later `git reset --hard` would then destroy.
    """
    lesson = _lesson("bare commit")
    bundle = _bundle(lesson)
    (bundle / "attempts").mkdir(exist_ok=True)
    (bundle / "attempts" / "invented-work.go").write_text("// learner\n",
                                                          encoding="utf-8")
    (bundle / "runs.jsonl").write_text('{"invented": "app-owned"}\n',
                                       encoding="utf-8")
    (bundle / "node_modules").mkdir(exist_ok=True)
    (bundle / "node_modules" / "invented-package.js").write_text("//\n",
                                                                 encoding="utf-8")
    bare = {"PATH": "/usr/bin:/bin", "HOME": str(bundle / "invented-empty-home")}

    add = subprocess.run(["git", "-C", str(bundle), "add", "-A"],
                         capture_output=True, text=True, timeout=30, env=bare)
    commit = subprocess.run(
        ["git", "-C", str(bundle), "commit", "-q", "-m", "invented checkpoint"],
        capture_output=True, text=True, timeout=30, env=bare,
    )
    assert add.returncode == 0 and commit.returncode == 0, (
        "an unconfigured session commits: " + (commit.stderr or add.stderr)
    )
    assert subprocess.run(
        ["git", "-C", str(bundle), "log", "-1", "--format=%ae"],
        capture_output=True, text=True, timeout=30, env=bare,
    ).stdout.strip() == "lesson@ephemeris.invalid", (
        "and it commits as the repository's own identity, not a stray "
        "system-level one that happens to be configured on this machine"
    )
    tracked = set(
        subprocess.run(["git", "-C", str(bundle), "ls-files"],
                       capture_output=True, text=True, timeout=30,
                       env=bare).stdout.split()
    )
    assert "attempts/invented-work.go" in tracked, "authored work is history"
    assert not {
        name for name in tracked
        if name.startswith("node_modules/")
        or name.endswith(".jsonl")
        or name in ("AGENTS.md", "CLAUDE.md")
    }, (
        "app-owned state stays out: rollback must not be able to rewrite a "
        "projection whose contents exist nowhere else"
    )


def test_repeated_ensures_never_re_init(monkeypatch):
    lesson = _lesson("idempotent")
    bundle = _bundle(lesson)
    marker = "invented-agent-rule/\n"
    # An agent's own ignore rules are its business, and the app never touches
    # the name — before or after this read.
    (bundle / ".gitignore").write_text(marker, encoding="utf-8")

    calls: list = []
    monkeypatch.setattr(lessons.subprocess, "run", _counting_run(calls))
    for _ in range(3):
        lessons.lesson_file_info(lesson)

    assert calls == [], "the .git check is cheap and the subprocess never reruns"
    assert (bundle / ".gitignore").read_text(encoding="utf-8") == marker, (
        "a bundle's own ignore rules survive every later read"
    )


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
    assert "`git add -A` stages authored work only" in brief, (
        "and the agent is told the app's own files are already excluded"
    )
