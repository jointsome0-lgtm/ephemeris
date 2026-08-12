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


def test_learner_files_named_like_a_projection_are_still_history():
    """The exclude rules are anchored on purpose: a pattern with no slash
    matches that name at any depth, and `attempts/…/runs.jsonl` is the
    learner's own file, not the app's projection of the same name."""
    lesson = _lesson("anchored rules")
    bundle = _bundle(lesson)
    nested = bundle / "attempts" / "invented-parser"
    nested.mkdir(parents=True, exist_ok=True)
    for name in ("runs.jsonl", "memory.jsonl", "CLAUDE.md"):
        (nested / name).write_text("{}\n", encoding="utf-8")

    ignored = _git(
        bundle, "check-ignore", *[f"attempts/invented-parser/{n}"
                                 for n in ("runs.jsonl", "memory.jsonl",
                                           "CLAUDE.md")]
    )
    assert ignored.returncode == 1 and ignored.stdout == "", (
        "nothing under an artifact root is excluded by an app-owned rule, "
        f"but git ignored: {ignored.stdout!r}"
    )
    assert _git(bundle, "check-ignore", "runs.jsonl").returncode == 0, (
        "while the app's own projection at the bundle root still is"
    )


def test_a_repo_restored_without_its_empty_dirs_is_completed_in_place():
    """An instance backup is a list of FILES, so a bundle backed up before its
    first commit comes back with a `.git` git calls "not a git repository".
    The gate is readiness, not existence, so the next read finishes the job."""
    for missing in ("objects", "refs"):
        # Two different repositories: a never-committed one restores without
        # `objects/`, a packed one keeps its packs and `packed-refs` but loses
        # an emptied `refs/`. Git refuses either.
        lesson = _lesson(f"restored repo {missing}")
        bundle = _bundle(lesson)
        shutil.rmtree(bundle / ".git" / missing)
        assert _git(bundle, "status", "--porcelain").returncode != 0, (
            f"a restore missing {missing}/ really does leave git unable to "
            "read the repository"
        )

        lessons.lesson_file_info(lesson)

        assert _git(bundle, "status", "--porcelain").returncode == 0, (
            f"a read repairs the {missing}/ the restore could not carry"
        )


def test_a_half_finished_setup_is_retried_not_frozen():
    lesson = _lesson("partial setup")
    bundle = _bundle(lesson)
    # What a transient failure between `git init` and the exclude write leaves
    # behind: a repository with none of this app's rules.
    (bundle / ".git" / "info" / "exclude").unlink()
    _git(bundle, "config", "--unset", "user.email")

    lessons.lesson_file_info(lesson)

    assert (bundle / ".git" / "info" / "exclude").read_text(
        encoding="utf-8"
    ) == lessons.BUNDLE_GIT_EXCLUDE, "the rules are written on the retry"
    assert _git(bundle, "config", "user.email").stdout.strip() == (
        "lesson@ephemeris.invalid"
    ), "and so is the identity the sandbox cannot supply"


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


def test_the_build_still_sees_every_link_the_repo_can_hold():
    """The build refuses a bundle holding any symlink, because the bundler
    resolves one into the artifact and `_graph_sources` compares paths lexically
    on the strength of that. Adding `.git` adds nowhere for a link to hide: an
    entry can `import "./.git/hooks/helper.js"` like any other relative path.
    """
    from app.services import lesson_build

    lesson = _lesson("repo internals")
    bundle = _bundle(lesson)
    (bundle / ".git" / "hooks" / "invented-hook.js").symlink_to("/etc/hostname")

    assert lesson_build._linked_paths(bundle) == [
        ".git/hooks/invented-hook.js"
    ], "a link inside the repository is a link inside the bundle"


def test_a_planted_link_costs_the_bundle_its_history_not_the_host(tmp_path):
    """The session that owns this bundle can write in it; the setup runs
    outside that sandbox, as the app. A name the session controls must never
    decide where the app's bytes land — so a repository holding a link where
    git writes is left alone, and the lesson simply goes without history.
    """
    lesson = _lesson("planted marker")
    bundle = _bundle(lesson)
    outside = tmp_path / "invented-host-file"
    outside.write_text("untouched\n", encoding="utf-8")
    exclude = bundle / ".git" / "info" / "exclude"
    exclude.unlink()
    exclude.symlink_to(outside)
    shutil.rmtree(bundle / ".git" / "objects")  # so the setup would run again

    assert lessons.lesson_file_info(lesson), "and the bundle still reads"

    assert outside.read_text(encoding="utf-8") == "untouched\n", (
        "nothing was written through the planted name"
    )
    assert exclude.is_symlink(), (
        "nor was the session's own file replaced — the app's answer to a "
        "sabotaged repository is to stop, not to start deleting inside a bundle"
    )


def test_the_app_points_git_at_no_repository_holding_a_link(tmp_path):
    """`git init` and `git config` write `config`, `HEAD` and the template
    files, and they follow a link at any of those names. The session can plant
    one; the app runs outside its sandbox, so it must not be the hand that
    writes there."""
    lesson = _lesson("planted config")
    bundle = _bundle(lesson)
    outside = tmp_path / "invented-host-gitconfig"
    outside.write_text("[user]\n\tname = untouched\n", encoding="utf-8")
    (bundle / ".git" / "config").unlink()
    (bundle / ".git" / "config").symlink_to(outside)
    shutil.rmtree(bundle / ".git" / "objects")  # so the setup would run again

    lessons.lesson_file_info(lesson)

    assert outside.read_text(encoding="utf-8") == "[user]\n\tname = untouched\n", (
        "neither git init nor git config was pointed at a repository whose "
        "metadata the session had replaced with a link"
    )


def test_a_marker_git_wrote_itself_is_not_a_finished_setup():
    """`git init` creates a template `info/exclude` from the first moment, so
    presence proves nothing: a setup that died before the rules were written —
    or a repository older than the rules — must still be finished."""
    lesson = _lesson("template marker")
    git_dir = _bundle(lesson) / ".git"
    exclude = git_dir.joinpath(*lessons.GIT_EXCLUDE_PATH)

    exclude.write_text("# git's own template, not the app's rules\n",
                       encoding="utf-8")
    assert not lessons._bundle_repo_is_ready(git_dir), (
        "the marker is the content, not the name"
    )

    lessons.lesson_file_info(lesson)

    assert lessons._bundle_repo_is_ready(git_dir), "the read finishes the job"

    exclude.unlink()
    exclude.symlink_to(_bundle(lesson) / lessons.MANIFEST_NAME)
    assert not lessons._bundle_repo_is_ready(git_dir), (
        "and a link at the name satisfies nothing, whatever it points at"
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
