"""Retiring the seeded lesson-libs shelf from bundles that still carry it (#161).

The tool deletes files out of real bundles on a live machine, so what is
actually under test is the refusals: it removes only what the app's own stamp
names AND whose bytes still match, and it leaves the whole directory alone the
moment anything there stopped being the app's.

Self-contained — it builds its bundles under `tmp_path` and points the tool at
them, so it seeds nothing and asserts nothing about anyone else's counts.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

from scripts import drop_lesson_libs

FILES = {
    "d3/d3.min.js": b"// d3, pretend\n",
    "katex/katex.min.js": b"// katex, pretend\n",
    "katex/katex.min.css": b"/* katex css, pretend */\n",
    "mermaid/mermaid.min.js": b"// mermaid, pretend\n",
}


def _seed(bundle: Path, files: dict[str, bytes] = FILES) -> Path:
    """Write a shelf the way the retired seeder wrote one, stamp included."""
    shelf = bundle / drop_lesson_libs.SHELF_DIR
    lines = [
        f"{drop_lesson_libs.STAMP_MARKER} — app-managed, regenerated on terminal open\n",
        "# d3 7.9.0\n",
    ]
    for relative, data in sorted(files.items()):
        target = shelf / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {relative}\n")
    (shelf / drop_lesson_libs.STAMP_NAME).write_text("".join(lines), encoding="utf-8")
    return shelf


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    """A lessons root of this test's own, with a pretend shelf pinned.

    `SHELF_CONTENTS` ships the four real digests, whose 4.4 MB of bytes left
    the repository with the shelf. What is under test is the tool's judgement
    about the pinned set, not the set itself — that is
    `test_the_pinned_inventory_is_the_one_the_shelf_published` below.
    """
    lessons = tmp_path / "lessons"
    lessons.mkdir()
    monkeypatch.setattr(drop_lesson_libs, "LESSONS_DIR", str(lessons))
    monkeypatch.setattr(
        drop_lesson_libs, "SHELF_CONTENTS",
        {rel: hashlib.sha256(data).hexdigest() for rel, data in FILES.items()},
    )
    return lessons


def test_the_pinned_inventory_is_the_one_the_shelf_published():
    """The digests this tool deletes by are the seeder's, checked against the
    commit that introduced them rather than against itself.

    `vendor/lesson-libs/SHASUMS256` was added by #146 and removed by #161 with
    no commit in between, so that one blob is the shelf's whole history. Its
    paths carry the version directory the seeder flattened away.
    """
    import subprocess

    try:
        published = subprocess.run(
            ["git", "show", "1a2c26b:vendor/lesson-libs/SHASUMS256"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        pytest.skip(f"git history unavailable here: {exc}")
    if published.returncode != 0:  # pragma: no cover - shallow clone
        pytest.skip("the #146 blob is not in this clone")

    flattened = {}
    for line in published.stdout.splitlines():
        digest, _, shelf_relative = line.partition("  ")
        name, _version, *rest = shelf_relative.split("/")
        flattened["/".join([name, *rest])] = digest
    assert drop_lesson_libs.SHELF_CONTENTS == flattened


def _bundle(root: Path, slug: str) -> Path:
    bundle = root / slug
    (bundle / "assets").mkdir(parents=True)
    return bundle


def test_a_seeded_shelf_is_retired_whole(root, capsys):
    bundle = _bundle(root, "seeded")
    (bundle / "assets/diagram.png").write_bytes(b"authored")
    (bundle / "index.html").write_bytes(b"<h1>lesson</h1>")
    shelf = _seed(bundle)

    assert drop_lesson_libs.main([]) == 0
    # The shelf and every empty directory under it are gone...
    assert not shelf.exists()
    # ...and `assets/` is not, because it was never the app's to remove.
    assert (bundle / "assets").is_dir()
    assert (bundle / "assets/diagram.png").read_bytes() == b"authored"
    assert (bundle / "index.html").read_bytes() == b"<h1>lesson</h1>"
    # 4 libraries plus the stamp.
    assert "removed 5 file(s)" in capsys.readouterr().out


def test_a_dry_run_reports_the_same_and_changes_nothing(root, capsys):
    bundle = _bundle(root, "seeded")
    shelf = _seed(bundle)
    before = sorted(p.name for p in shelf.rglob("*"))

    assert drop_lesson_libs.main(["--dry-run"]) == 0
    assert sorted(p.name for p in shelf.rglob("*")) == before
    out = capsys.readouterr().out
    assert "would remove 5 file(s)" in out and "nothing changed" in out


def test_a_second_run_finds_nothing_to_do(root, capsys):
    bundle = _bundle(root, "seeded")
    _seed(bundle)
    assert drop_lesson_libs.main([]) == 0
    capsys.readouterr()

    assert drop_lesson_libs.main([]) == 0
    assert "0 carried a seeded shelf" in capsys.readouterr().out


def test_an_edited_copy_keeps_itself_and_the_stamp(root, capsys):
    """A file whose bytes no longer match is somebody's work, not the app's.

    Keeping the stamp with it is the point: while one seeded file is still
    there the directory is unfinished business, and a rerun has to be able to
    see that rather than read an absent stamp as "already done".
    """
    bundle = _bundle(root, "edited")
    shelf = _seed(bundle)
    (shelf / "d3/d3.min.js").write_bytes(b"// d3, with a local patch\n")

    assert drop_lesson_libs.main([]) == 1
    assert (shelf / "d3/d3.min.js").read_bytes() == b"// d3, with a local patch\n"
    assert (shelf / drop_lesson_libs.STAMP_NAME).is_file()
    # The matching ones still went; only the changed file was owed nothing.
    assert not (shelf / "katex/katex.min.js").exists()
    out = capsys.readouterr().out
    assert "d3/d3.min.js is not the copy the shelf published" in out


def test_a_stamp_the_session_extended_cannot_widen_what_is_deleted(root, capsys):
    """The stamp lives where a lesson session can write it.

    So it says only "the app owned this directory". A session that appends its
    own file and that file's real digest would, if the stamp were believed,
    have talked this tool into deleting the author's work. The list of what may
    go is in the repository, and an unlisted file is a stray — which hands the
    whole shelf back rather than taking anything.
    """
    bundle = _bundle(root, "extended-stamp")
    shelf = _seed(bundle)
    authored = shelf / "my-chart.js"
    authored.write_bytes(b"// months of work\n")
    stamp = shelf / drop_lesson_libs.STAMP_NAME
    stamp.write_text(
        stamp.read_text(encoding="utf-8")
        + f"{hashlib.sha256(authored.read_bytes()).hexdigest()}  my-chart.js\n",
        encoding="utf-8",
    )

    assert drop_lesson_libs.main([]) == 1
    assert authored.read_bytes() == b"// months of work\n"
    # The four the repository vouches for still go — they are provably the
    # app's copies. What the stray costs is the directory and the stamp, which
    # stay because the space is somebody else's now.
    assert not (shelf / "d3/d3.min.js").exists()
    assert (shelf / drop_lesson_libs.STAMP_NAME).is_file()
    assert "the stamp never named" in capsys.readouterr().out


def test_a_file_the_stamp_never_named_hands_the_shelf_back(root, capsys):
    """Something else lives here now, so the app stops claiming the directory."""
    bundle = _bundle(root, "extended")
    shelf = _seed(bundle)
    (shelf / "chart-helper.js").write_bytes(b"// mine\n")

    assert drop_lesson_libs.main([]) == 1
    assert (shelf / "chart-helper.js").is_file()
    assert (shelf / drop_lesson_libs.STAMP_NAME).is_file()
    assert "the stamp never named" in capsys.readouterr().out


def test_a_directory_that_was_never_the_apps_is_left_alone(root, capsys):
    """No stamp, or a checksum file somebody else wrote at that name."""
    for slug, stamp in (("no-stamp", None), ("foreign", b"# somebody else's list\n")):
        bundle = _bundle(root, slug)
        shelf = bundle / drop_lesson_libs.SHELF_DIR
        shelf.mkdir(parents=True)
        (shelf / "chart.js").write_bytes(b"// authored\n")
        if stamp is not None:
            (shelf / drop_lesson_libs.STAMP_NAME).write_bytes(stamp)

    assert drop_lesson_libs.main([]) == 1
    for slug in ("no-stamp", "foreign"):
        shelf = root / slug / drop_lesson_libs.SHELF_DIR
        assert (shelf / "chart.js").read_bytes() == b"// authored\n"
    out = capsys.readouterr().out
    assert out.count("not the app's stamp") == 2
    assert "0 carried a seeded shelf" in out


def test_a_symlinked_shelf_is_never_followed(root, capsys):
    """The bundle is writable from inside its own session, so `assets/libs`
    could be a link at somebody else's tree — which this tool does not empty."""
    outside = root.parent / "outside"
    (outside / "d3").mkdir(parents=True)
    (outside / "d3/d3.min.js").write_bytes(FILES["d3/d3.min.js"])
    bundle = _bundle(root, "linked")
    (bundle / drop_lesson_libs.SHELF_DIR).symlink_to(outside, target_is_directory=True)

    assert drop_lesson_libs.main([]) == 0
    assert (outside / "d3/d3.min.js").is_file()
    assert "0 carried a seeded shelf" in capsys.readouterr().out


def test_a_symlinked_directory_on_the_way_is_not_walked_through(root, capsys):
    """`O_NOFOLLOW` guards only a path's last component.

    So `assets/libs/d3` — a name a lesson session can replace — pointing at a
    directory elsewhere would otherwise be walked through to hash and then
    UNLINK a file outside the bundle, and the digest would match, because the
    bytes are the ones this tool was sent to delete.
    """
    outside = root.parent / "elsewhere"
    outside.mkdir()
    (outside / "d3.min.js").write_bytes(FILES["d3/d3.min.js"])
    bundle = _bundle(root, "linked-component")
    shelf = _seed(bundle)
    shutil.rmtree(shelf / "d3")
    (shelf / "d3").symlink_to(outside, target_is_directory=True)

    assert drop_lesson_libs.main([]) == 1
    assert (outside / "d3.min.js").read_bytes() == FILES["d3/d3.min.js"]
    assert (shelf / drop_lesson_libs.STAMP_NAME).is_file()
    assert "d3/d3.min.js" in capsys.readouterr().out


def test_a_fifo_at_a_seeded_name_does_not_park_the_run(root, capsys):
    """Without `O_NONBLOCK` the open waits for a writer that never comes, and
    the `fstat` that would reject it as non-regular never runs."""
    bundle = _bundle(root, "fifo")
    shelf = _seed(bundle)
    (shelf / "d3/d3.min.js").unlink()
    os.mkfifo(shelf / "d3/d3.min.js")

    # The assertion is that this returns at all.
    assert drop_lesson_libs.main([]) == 1
    assert (shelf / drop_lesson_libs.STAMP_NAME).is_file()
    assert "d3/d3.min.js" in capsys.readouterr().out


def test_an_unreadable_copy_keeps_the_stamp_rather_than_being_read_as_absent(
    root, capsys
):
    """A mode this process cannot read is not the same as nothing being there.

    Conflating the two would drop the stamp, exit clean, and leave the seeded
    bytes with no marker for a later run to find them by.
    """
    bundle = _bundle(root, "unreadable")
    shelf = _seed(bundle)
    locked = shelf / "d3/d3.min.js"
    locked.chmod(0o000)
    try:
        if _readable_despite_mode(locked):
            pytest.skip("running as a user that ignores the mode bits")
        assert drop_lesson_libs.main([]) == 1
        assert (shelf / drop_lesson_libs.STAMP_NAME).is_file()
        assert locked.exists()
        assert "d3/d3.min.js" in capsys.readouterr().out
    finally:
        locked.chmod(0o644)


def _readable_despite_mode(path: Path) -> bool:
    try:
        path.read_bytes()
    except OSError:
        return False
    return True


def test_slug_limits_the_run(root, capsys):
    for slug in ("one", "two"):
        _seed(_bundle(root, slug))

    assert drop_lesson_libs.main(["--slug", "one"]) == 0
    assert not (root / "one" / drop_lesson_libs.SHELF_DIR).exists()
    assert (root / "two" / drop_lesson_libs.SHELF_DIR).is_dir()
    assert "1 bundle(s) examined" in capsys.readouterr().out

    assert drop_lesson_libs.main(["--slug", "nope"]) == 1
