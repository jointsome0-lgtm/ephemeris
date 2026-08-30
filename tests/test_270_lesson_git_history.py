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


pytestmark = pytest.mark.usefixtures("started_app")


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
    ), "a local identity, because the session may have no .gitconfig and has no remote"


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
    # The link the app places at this name, not a directory: git's own
    # `node_modules/` rule would match only a directory and track the link.
    packages = bundle / "invented-packages"
    packages.mkdir(exist_ok=True)
    (packages / "invented-package.js").write_text("//\n", encoding="utf-8")
    (bundle / "node_modules").symlink_to(packages)
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
        if name == "node_modules" or name.startswith("node_modules/")
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


def test_a_repository_without_the_rules_gets_them_and_keeps_its_own_identity():
    lesson = _lesson("rules missing")
    bundle = _bundle(lesson)
    (bundle / ".git" / "info" / "exclude").unlink()
    _git(bundle, "config", "user.email", "invented-somebody@example.invalid")

    lessons.lesson_file_info(lesson)

    assert (bundle / ".git" / "info" / "exclude").read_text(
        encoding="utf-8"
    ) == lessons.BUNDLE_GIT_EXCLUDE, "app-owned rules are app-owned: rewritten"
    assert _git(bundle, "config", "user.email").stdout.strip() == (
        "invented-somebody@example.invalid"
    ), "a value the repository already carries is never overwritten"


def test_a_repository_missing_an_identity_is_given_one(monkeypatch):
    """The repair also reaches repositories the app did not build — one an
    agent made itself in a session, say. The session supplies no identity, so
    without this the checkpoint the brief asks for dies on "unable to
    auto-detect email address", and the marker written right after would make
    that permanent."""
    lesson = _lesson("identity missing")
    bundle = _bundle(lesson)
    _git(bundle, "config", "--unset", "user.email")
    _git(bundle, "config", "user.name", "Invented Somebody")
    (bundle / ".git" / "info" / "exclude").unlink()

    seen: list = []
    monkeypatch.setattr(lessons.subprocess, "run", _counting_run(seen))
    lessons.lesson_file_info(lesson)
    during_setup = list(seen)  # before this test's own git calls join them
    monkeypatch.undo()

    assert _git(bundle, "config", "user.email").stdout.strip() == (
        "lesson@ephemeris.invalid"
    ), "the missing half is filled"
    assert _git(bundle, "config", "user.name").stdout.strip() == (
        "Invented Somebody"
    ), "and only the missing half"
    assert during_setup and not any(
        str(bundle) in " ".join(argv) for argv in during_setup
    ), (
        "git parsed a COPY in the app's own staging area; it was never "
        f"pointed at the bundle — ran: {during_setup}"
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


def test_the_app_writes_the_rules_through_no_link(tmp_path):
    """The session that owns this bundle can write in it; the setup runs
    outside that session, as the app. A name the session controls must never
    decide where the app's bytes land."""
    lesson = _lesson("planted marker")
    bundle = _bundle(lesson)
    outside = tmp_path / "invented-host-file"
    outside.write_text("untouched\n", encoding="utf-8")
    exclude = bundle / ".git" / "info" / "exclude"
    exclude.unlink()
    exclude.symlink_to(outside)
    shutil.rmtree(bundle / ".git" / "objects")  # so the setup runs again

    lessons.lesson_file_info(lesson)

    assert outside.read_text(encoding="utf-8") == "untouched\n", (
        "nothing was written through the planted name"
    )
    assert not exclude.is_symlink() and exclude.read_text(
        encoding="utf-8"
    ) == lessons.BUNDLE_GIT_EXCLUDE, (
        "the rename replaced the link rather than following it"
    )


def test_an_identity_that_could_not_be_set_leaves_the_repo_unfinished(monkeypatch):
    """The marker is what stops the app ever looking again, so it must not be
    written over a repository whose commits still cannot name an author."""
    lesson = _lesson("identity failed")
    bundle = _bundle(lesson)
    _git(bundle, "config", "--unset", "user.email")
    exclude = bundle / ".git" / "info" / "exclude"
    exclude.unlink()

    def no_git(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory: 'git'")

    monkeypatch.setattr(lessons.subprocess, "run", no_git)
    lessons.lesson_file_info(lesson)
    monkeypatch.undo()

    assert not exclude.exists(), "no marker while the identity is missing"
    assert not lessons._bundle_repo_is_ready(bundle / ".git"), (
        "so the next read tries again instead of calling this finished"
    )

    lessons.lesson_file_info(lesson)

    assert lessons._bundle_repo_is_ready(bundle / ".git") and _git(
        bundle, "config", "user.email"
    ).stdout.strip() == "lesson@ephemeris.invalid", (
        "and the read that can finish the job does both halves"
    )


def test_an_authored_git_directory_is_not_mistaken_for_a_repository():
    """`.git` was servable and unreserved until this change, so a bundle may
    hold an ordinary directory under it. Finishing THAT would leave something
    the app calls ready and git calls "not a git repository", permanently."""
    lesson = _lesson("authored git dir")
    bundle = _bundle(lesson)
    shutil.rmtree(bundle / ".git")
    (bundle / ".git").mkdir()
    (bundle / ".git" / "invented-notes.txt").write_text("mine\n", encoding="utf-8")

    lessons.lesson_file_info(lesson)

    assert list((bundle / ".git").iterdir()) == [
        bundle / ".git" / "invented-notes.txt"
    ], "nothing was created inside a directory that is not a repository"
    assert not lessons._bundle_repo_is_ready(bundle / ".git"), (
        "and it is not recorded as finished either"
    )


def test_a_staging_area_that_cannot_be_used_is_not_a_broken_bundle(monkeypatch):
    """Repository setup is best-effort all the way out: the read path has no
    handler for an OSError from here, so one would be a 500 on a bundle whose
    only problem is that it has no history yet."""
    lesson = _lesson("staging blocked")
    bundle = _bundle(lesson)
    shutil.rmtree(bundle / ".git")

    def no_staging(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(lessons.tempfile, "mkdtemp", no_staging)

    assert lessons.lesson_file_info(lesson), "the bundle reads regardless"
    assert not (bundle / ".git").exists(), "it simply has no repository"


def test_an_outsized_marker_is_answered_without_reading_it():
    """The name is the session's to fill, and this is asked on a read path: a
    sparse file of any size must cost a stat, not a load."""
    lesson = _lesson("outsized marker")
    exclude = _bundle(lesson) / ".git" / "info" / "exclude"
    exclude.unlink()
    with open(exclude, "wb") as handle:
        handle.truncate(8 * 1024 * 1024 * 1024)

    assert not lessons._bundle_repo_is_ready(_bundle(lesson) / ".git"), (
        "8 GiB is not the marker, and finding that out reads nothing"
    )


def test_git_is_never_pointed_at_an_existing_bundle_repository(tmp_path,
                                                               monkeypatch):
    """`git init` and `git config` write `config`, `HEAD` and the template
    files, and follow a link at any of those names. The session runs
    concurrently and can plant one at any moment, so checking first would only
    narrow the window: git is simply never run against a bundle that already
    holds a repository.
    """
    lesson = _lesson("planted config")
    bundle = _bundle(lesson)
    outside = tmp_path / "invented-host-gitconfig"
    outside.write_text("[user]\n\tname = untouched\n", encoding="utf-8")
    (bundle / ".git" / "config").unlink()
    (bundle / ".git" / "config").symlink_to(outside)
    shutil.rmtree(bundle / ".git" / "objects")  # so the repair runs

    calls: list = []
    monkeypatch.setattr(lessons.subprocess, "run", _counting_run(calls))
    lessons.lesson_file_info(lesson)

    assert calls == [], (
        "the repair recreates a directory and rewrites the app's own rules; "
        "it asks git for nothing"
    )
    assert outside.read_text(encoding="utf-8") == "[user]\n\tname = untouched\n", (
        "so no link the session planted decided where the app's bytes landed"
    )
    assert (bundle / ".git" / "objects").is_dir(), "and the repair happened"


def test_a_repository_arrives_whole_or_not_at_all():
    """It is built outside the bundle and renamed in, so a session watching
    the directory never sees a repository without the rules or the identity —
    and a failed build leaves nothing behind, in the bundle or the staging
    area."""
    lesson = _lesson("staged install")
    bundle = _bundle(lesson)
    shutil.rmtree(bundle / ".git")
    assert not list(lessons.GIT_STAGING_DIR.iterdir()), (
        "the staging area is cleaned up after every build"
    )

    lessons.lesson_file_info(lesson)

    assert lessons._bundle_repo_is_ready(bundle / ".git"), (
        "what appears under the bundle is a finished repository"
    )
    assert not list(lessons.GIT_STAGING_DIR.iterdir()), (
        "and the staging area is empty again"
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
