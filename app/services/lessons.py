"""Learn lesson backlog and status lifecycle.

Lessons are the durable memory for things to study. The generated lesson HTML is
runtime data in data/lessons later; this service owns metadata, status changes,
soft archive, and the matching ledger events.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import mimetypes
import os
import re
import shutil
import sqlite3
import stat as stat_module
import subprocess
import tempfile
import textwrap
from html import escape
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import NamedTuple
from urllib.parse import urlsplit
from uuid import uuid4

from ..db import DATA_DIR, append_event, get_conn, now_iso
from . import bundle_schema, projection

STATUSES = ("backlog", "studying", "paused", "studied")
STATUS_LABELS = {
    "backlog": "Backlog",
    "studying": "Studying",
    "paused": "Paused",
    "studied": "Studied",
}
LESSONS_DIR = DATA_DIR / "lessons"
BUILD_WORKSPACES_DIR = DATA_DIR / "lesson-builds"
BUILD_WORKSPACE_LINK = "node_modules"
GIT_DIR_NAME = ".git"
GIT_INIT_TIMEOUT_SECONDS = 10
# What history is for is authored work: the lesson's pages and the learner's
# files. Everything the app itself writes into a bundle stays out of it, in the
# repository's own exclude file rather than a `.gitignore` — that name belongs
# to the agent, and a bundle that already has one must neither be overwritten
# nor able to bypass these rules.
#   *.jsonl       app-owned projections, read-only for the agent. A checkpoint
#                 that tracked them would let a learner's `git reset --hard`
#                 roll them back too, and `runs.jsonl` output tails exist
#                 nowhere else — rollback must not be able to destroy them.
# Anchored, every one of them: a pattern with no slash matches that NAME at any
# depth, and the artifact roots hold learner-authored files this app has no
# opinion about — an `attempts/parser/runs.jsonl` the learner wrote is exactly
# the work history is for. `node_modules` is the deliberate exception: it is
# unanchored because installed packages belong in no lesson's history at any
# depth, whoever created the directory — and carries no trailing slash, which
# would match only a directory and leave the bundle's link itself untracked.
GIT_EXCLUDE_PATH = ("info", "exclude")
GIT_REQUIRED_DIRS = ("objects", "refs")
GIT_STAGING_DIR = DATA_DIR / "lesson-git-staging"
BUNDLE_GIT_EXCLUDE = """\
# Written by the Learn app when it set this repository up (#186).
# App-owned paths only: rules of your own belong in .gitignore.
node_modules
/attempts.jsonl
/assessments.jsonl
/memory.jsonl
/runs.jsonl
/AGENTS.md
/CLAUDE.md
/.claude/
/reference/
"""
# An honest git config is a few hundred bytes; this is the ceiling on what the
# app will read from a name a lesson session can write.
GIT_CONFIG_MAX_BYTES = 256 * 1024
BUNDLE_GIT_IDENTITY = (
    ("user.name", "Ephemeris Learn"),
    ("user.email", "lesson@ephemeris.invalid"),  # RFC 2606 reserved TLD
)
DEFAULT_ENTRY = bundle_schema.DEFAULT_ENTRY
MANIFEST_NAME = "lesson.json"

_log = logging.getLogger("activity_ledger")


class LessonError(ValueError):
    """A Learn lesson write was rejected."""


def _clean_title(title: str | None) -> str:
    title = (title or "").strip()
    if not title:
        raise LessonError("lesson title can’t be empty")
    if len(title) > 240:
        raise LessonError("lesson title too long")
    return title


def _clean_url(source_url: str | None) -> str | None:
    source_url = (source_url or "").strip()
    if not source_url:
        return None
    if len(source_url) > 1000:
        raise LessonError("source URL too long")
    parsed = urlsplit(source_url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise LessonError("source URL must be http or https")
    return source_url


_SLUG_WORD = re.compile(r"[^a-z0-9]+")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _base_slug(title: str) -> str:
    slug = _SLUG_WORD.sub("-", title.lower()).strip("-")
    return slug[:80].strip("-") or "lesson"


def _unique_slug(conn: sqlite3.Connection, title: str) -> str:
    base = _base_slug(title)
    slug = base
    n = 2
    while conn.execute("SELECT 1 FROM lessons WHERE slug = ?", (slug,)).fetchone():
        suffix = f"-{n}"
        slug = f"{base[:80 - len(suffix)].rstrip('-')}{suffix}"
        n += 1
    return slug


def _lesson_view(row: sqlite3.Row) -> dict:
    status = row["status"]
    return {
        "id": row["id"],
        "uid": row["uid"],
        "title": row["title"],
        "source_url": row["source_url"],
        "slug": row["slug"],
        "status": status,
        "status_label": STATUS_LABELS.get(status, status.title()),
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "archived_at": row["archived_at"],
        "current_entry": row["current_entry"],
        "last_opened_at": row["last_opened_at"],
        "archived": row["archived_at"] is not None,
    }


def _lesson_dir(slug: str) -> Path:
    if not _SLUG_RE.match(slug or ""):
        raise LessonError("invalid lesson slug")
    return LESSONS_DIR / slug


def _legacy_lesson_path(slug: str) -> Path:
    if not _SLUG_RE.match(slug or ""):
        raise LessonError("invalid lesson slug")
    return LESSONS_DIR / f"{slug}.html"


def _clean_bundle_ref(value: str | None, *, html_only: bool = False) -> str:
    if value is not None and not isinstance(value, str):
        raise LessonError("invalid lesson entry")
    value = (value or DEFAULT_ENTRY).strip()
    if not value or "\\" in value or projection.has_control_chars(value):
        raise LessonError("invalid lesson entry")
    # A name neither the filesystem nor a URL can carry is not a name in this
    # bundle. A JSON body may hold a lone surrogate — `"assets/\ud800.js"` —
    # which is a perfectly ordinary `str` here and then raises
    # `UnicodeEncodeError` out of `os.open` or `urllib.parse.quote`, turning a
    # bad request into an unstructured 500 several layers away.
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise LessonError("invalid lesson entry") from None
    ref = PurePosixPath(value)
    if ref.is_absolute() or ".." in ref.parts:
        raise LessonError("invalid lesson entry")
    if html_only and ref.suffix.lower() != ".html":
        raise LessonError("lesson entry must be HTML")
    return ref.as_posix()


def _clean_html_ref(value: str | None) -> str:
    return _clean_bundle_ref(value, html_only=True)


def _bundle_path(slug: str, ref: str) -> Path:
    base = _lesson_dir(slug)
    ref = _clean_bundle_ref(ref)
    try:
        path = (base / Path(ref)).resolve()
        root = base.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise LessonError("invalid lesson entry") from exc
    if not path.is_relative_to(root):
        raise LessonError("invalid lesson entry")
    return path


def _entry_path(slug: str, entry: str) -> Path:
    entry = _clean_html_ref(entry)
    return _bundle_path(slug, entry)


def _entry_label(entry: str) -> str:
    stem = PurePosixPath(entry).stem.replace("-", " ").replace("_", " ").strip()
    return stem.title() or entry


def _default_manifest(lesson: dict) -> dict:
    """The v2 creation skeleton (learn-bundle-spec.md §5)."""
    return bundle_schema.default_manifest_v2(
        lesson_uid=lesson["uid"],
        slug=lesson["slug"],
        title=lesson["title"],
        source_url=lesson.get("source_url"),
    )


def _manifest_path(slug: str) -> Path:
    return _lesson_dir(slug) / MANIFEST_NAME


def _write_manifest(path: Path, data: dict) -> None:
    bundle_schema.write_manifest(path, data)


class _RegularRead(NamedTuple):
    data: bytes
    opened: os.stat_result
    closed: os.stat_result

    @property
    def stable(self) -> bool:
        return _digest_key(self.opened) == _digest_key(self.closed)


def _read_regular_no_follow(
    path: Path | str, limit: int, *, dir_fd: int | None = None
) -> _RegularRead | None:
    """`path` as bytes, only if it is a regular non-symlink file of at most
    `limit` bytes; None otherwise (§2). The regular-file check, the size
    check and the bytes all come from one descriptor, and the read takes one
    byte past `limit` so a file growing underneath is refused, not trusted.
    With `dir_fd`, `path` is a name resolved against that descriptor."""
    try:
        fd = os.open(path, projection.READ_FLAGS, dir_fd=dir_fd)
    except OSError:
        return None
    try:
        opened = os.fstat(fd)
        if not stat_module.S_ISREG(opened.st_mode) or opened.st_size > limit:
            return None
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            data = fh.read(limit + 1)
            closed = os.fstat(fh.fileno())
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > limit:
        return None
    return _RegularRead(data, opened, closed)


# Digest cache for the metadata poll: the client polls every ~1.2s and each
# eligible poll would otherwise stream the whole page through sha256. Keyed by
# the full inode identity INCLUDING ctime_ns — a writer can restore mtime after
# replacing bytes, but any in-place write or utime call moves ctime, and a
# rename swap changes the inode, so an mtime-preserving replacement misses
# this cache and gets re-hashed.
_PAGE_DIGEST_CACHE: dict[str, tuple[tuple, str]] = {}
_PAGE_DIGEST_CACHE_MAX = 64
_PAGE_DIGEST_CACHE_LOCK = Lock()

# Supported page-size bound: a page larger than this carries no bridge
# identity — it is never hashed for `page_rev`, never snapshotted
# into memory by the serving route, and record-time re-hashes of it report the
# revision unknowable (attempts record `stale`). Display still works via the
# streaming file response. Real lesson pages are tens of KiB; the bound is a
# hard stop on unbounded hash/read work, not a target.
PAGE_IDENTITY_MAX_BYTES = 4 * 1024 * 1024


def _cache_page_digest(path: Path, key: tuple, digest: str) -> None:
    cache_key = str(path)
    with _PAGE_DIGEST_CACHE_LOCK:
        if _PAGE_DIGEST_CACHE_MAX <= 0:
            return
        if cache_key not in _PAGE_DIGEST_CACHE:
            while len(_PAGE_DIGEST_CACHE) >= _PAGE_DIGEST_CACHE_MAX:
                try:
                    _PAGE_DIGEST_CACHE.pop(next(iter(_PAGE_DIGEST_CACHE)), None)
                except StopIteration:
                    break
        _PAGE_DIGEST_CACHE[cache_key] = (key, digest)


def _cached_page_digest(path: Path, key: tuple) -> str | None:
    with _PAGE_DIGEST_CACHE_LOCK:
        cached = _PAGE_DIGEST_CACHE.get(str(path))
        if cached is not None and cached[0] == key:
            return cached[1]
    return None


def _digest_key(st: os.stat_result) -> tuple:
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size, st.st_ctime_ns)


def _read_page_snapshot(path: Path) -> tuple[bytes, str, os.stat_result] | None:
    """One-descriptor page snapshot: the bytes, their sha256, and the closing
    stat all describe the SAME open, so hash and token describe one file
    object (§6.3). None when the name is not a regular non-symlink file
    within the supported size bound."""
    read = _read_regular_no_follow(path, PAGE_IDENTITY_MAX_BYTES)
    if read is None:
        return None
    key = _digest_key(read.closed)
    digest = _cached_page_digest(path, key) if read.stable else None
    if digest is None:
        digest = hashlib.sha256(read.data).hexdigest()
        if read.stable:
            _cache_page_digest(path, key, digest)
    return read.data, digest, read.closed


def _mkdir_no_follow(path: Path) -> None:
    if not path.is_symlink() and not path.exists():
        path.mkdir()


def _is_regular_no_follow(path: Path) -> bool:
    try:
        return stat_module.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _write_git_exclude(git_dir: Path) -> None:
    """Put the app-owned rules in `.git/info/exclude`, atomically and through
    no link at all: the bundle is writable from inside the lesson's own
    session while this runs outside it, so every component is opened
    `O_NOFOLLOW` from a descriptor on the one directory already known to be
    real, and the file arrives by rename, so the readiness marker never
    exists half-written."""
    info_name, exclude_name = GIT_EXCLUDE_PATH
    git_fd = os.open(git_dir, projection.DIRECTORY_FLAGS)
    try:
        info_fd = os.open(info_name, projection.DIRECTORY_FLAGS, dir_fd=git_fd)
    finally:
        os.close(git_fd)
    try:
        projection.publish(
            info_fd, exclude_name, BUNDLE_GIT_EXCLUDE.encode("utf-8"),
            prefix=f".{exclude_name}-", owned=False,
        )
    finally:
        os.close(info_fd)


def _bundle_repo_is_ready(git_dir: Path) -> bool:
    """Three stats deciding whether the setup below has anything left to do.

    `objects/` and `refs/` are directories a repository can carry EMPTY, and
    empty directories do not survive an instance backup/restore round trip
    (`scripts/backup_db.py`: the archive is a list of files); git refuses a
    repository missing either. `info/exclude` is this app's completion
    marker, renamed into place last. Its CONTENT is the marker, not its
    existence — `git init` writes a template of its own at that name from the
    first moment. A mismatch reads as unready, so a later change to the rules
    re-applies itself.
    """
    rules = BUNDLE_GIT_EXCLUDE.encode("utf-8")
    marker = _read_regular_no_follow(git_dir.joinpath(*GIT_EXCLUDE_PATH), len(rules))
    return (
        all(git_dir.joinpath(name).is_dir() for name in GIT_REQUIRED_DIRS)
        and marker is not None
        and marker.data == rules
    )


def _install_bundle_repo(git_dir: Path) -> None:
    """Build a whole repository somewhere the session cannot reach, then move
    it in.

    `git init` and `git config` follow a link at every name they write, and
    the bundle is writable from inside the lesson's own session while this
    runs outside it, so git is never pointed at the bundle at all. The
    repository arrives by `rename`, which follows no link and cannot
    overwrite a non-empty directory: what appears under the bundle is a
    finished repository or nothing.
    """
    staged: Path | None = None
    try:
        GIT_STAGING_DIR.mkdir(parents=True, exist_ok=True)
        staged = Path(tempfile.mkdtemp(dir=GIT_STAGING_DIR))
        work = staged / "repo"
        work.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", str(work)],
            check=True, capture_output=True, timeout=GIT_INIT_TIMEOUT_SECONDS,
        )
        for key, value in BUNDLE_GIT_IDENTITY:
            subprocess.run(
                ["git", "-C", str(work), "config", key, value],
                check=True, capture_output=True,
                timeout=GIT_INIT_TIMEOUT_SECONDS,
            )
        built = work / GIT_DIR_NAME
        built.joinpath(*GIT_EXCLUDE_PATH).write_text(
            BUNDLE_GIT_EXCLUDE, encoding="utf-8"
        )
        os.rename(built, git_dir)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("lesson bundle git init skipped for %s: %s", git_dir, exc)
    finally:
        if staged is not None:
            shutil.rmtree(staged, ignore_errors=True)


def _repair_bundle_repo(git_dir: Path) -> None:
    """Finish a repository that exists but is not ready — without git.

    The one case that reaches here is a repository restored from a backup,
    missing whichever of `objects/` and `refs/` was empty. `mkdir` follows no
    link, so this asks nothing of git; running `git init` over an existing
    repository would put the app back to writing through names the session
    controls, and a restored `config` is not this app's to touch. A `.git`
    with no `HEAD` is no repository but an authored directory: hands off.
    """
    if not _is_regular_no_follow(git_dir / "HEAD"):
        _log.warning("lesson bundle git repair skipped for %s: no HEAD, so "
                     "this is not a repository to finish", git_dir)
        return
    try:
        fd = os.open(git_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        _log.warning("lesson bundle git repair skipped for %s: %s", git_dir, exc)
        return
    try:
        for name in (*GIT_REQUIRED_DIRS, GIT_EXCLUDE_PATH[0]):
            try:
                os.mkdir(name, 0o755, dir_fd=fd)
            except FileExistsError:
                pass
            except OSError as exc:
                _log.warning("lesson bundle git repair incomplete for %s: %s",
                             git_dir, exc)
                return
    finally:
        os.close(fd)
    if not _ensure_repo_identity(git_dir):
        return
    try:
        _write_git_exclude(git_dir)
    except OSError as exc:
        _log.warning("lesson bundle git rules not written for %s: %s",
                     git_dir, exc)


def _ensure_repo_identity(git_dir: Path) -> bool:
    """Give a repository the app did not build the identity a commit needs.

    Without a local `user.name`/`user.email` the checkpoint the brief asks
    for dies on "unable to auto-detect email address", and the marker written
    just after would make that permanent. Whatever is configured is kept.
    Git parses a COPY of the config in the app-private staging directory and
    the result is renamed in through ONE descriptor on `.git`, opened before
    the read: a path resolved twice is a directory that can be swapped in
    between.

    Returns whether the repository can be called finished. A config that is
    not a plain readable file is not the app's to solve, but anything that
    went wrong while filling one in is, because the caller is about to write
    the marker that stops it ever looking again.
    """
    try:
        git_fd = os.open(git_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        _log.warning("lesson bundle git identity skipped for %s: %s",
                     git_dir, exc)
        return False
    staged: Path | None = None
    try:
        current = _read_regular_no_follow("config", GIT_CONFIG_MAX_BYTES, dir_fd=git_fd)
        if current is None:
            return True
        GIT_STAGING_DIR.mkdir(parents=True, exist_ok=True)
        staged = Path(tempfile.mkdtemp(dir=GIT_STAGING_DIR))
        copy = staged / "config"
        copy.write_text(current.data.decode("utf-8", errors="replace"), encoding="utf-8")
        filled = False
        for key, value in BUNDLE_GIT_IDENTITY:
            read = subprocess.run(
                ["git", "config", "-f", str(copy), "--get", key],
                capture_output=True, text=True, timeout=GIT_INIT_TIMEOUT_SECONDS,
            )
            if read.returncode == 0 and read.stdout.strip():
                continue
            subprocess.run(
                ["git", "config", "-f", str(copy), key, value],
                check=True, capture_output=True,
                timeout=GIT_INIT_TIMEOUT_SECONDS,
            )
            filled = True
        if filled:
            staged_fd = os.open(staged, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.replace(copy.name, "config",
                           src_dir_fd=staged_fd, dst_dir_fd=git_fd)
            finally:
                os.close(staged_fd)
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("lesson bundle git identity not set for %s: %s",
                     git_dir, exc)
        return False
    finally:
        os.close(git_fd)
        if staged is not None:
            shutil.rmtree(staged, ignore_errors=True)


def _ensure_bundle_repo(lesson_dir: Path) -> None:
    """Give the bundle a usable local git repository.

    The app only guarantees the repository exists and works; committing is
    the tutor agent's job, so setup leaves behind the two things a commit
    from inside the session needs: the app-owned exclude rules and a local
    identity. Both shapes — creating a repository and finishing one that came
    back from a backup incomplete — are best-effort, write through no link,
    and leave a `.git` that is not a plain directory alone.
    """
    git_dir = lesson_dir / GIT_DIR_NAME
    if git_dir.is_symlink():
        return
    if not git_dir.exists():
        _install_bundle_repo(git_dir)
        return
    if git_dir.is_dir() and not _bundle_repo_is_ready(git_dir):
        _repair_bundle_repo(git_dir)


def _ensure_bundle_manifest(lesson: dict) -> bundle_schema.ManifestRead:
    """Dual-read the bundle manifest (v1/v2), creating the standard dirs and —
    for a lesson that has none — the v2 skeleton. Creation, never repair: an
    existing manifest is read as-is, and a corrupt/unsupported/symlinked one
    is a visible reject (§9.1), not a silent default."""
    LESSONS_DIR.mkdir(parents=True, exist_ok=True)
    lesson_dir = _lesson_dir(lesson["slug"])
    if not _bundle_dir_is_safe(lesson_dir):
        return bundle_schema.rejected_read(
            "symlinked-bundle", "lesson bundle dir is not a real directory"
        )
    lesson_dir.mkdir(parents=True, exist_ok=True)
    for name in ("related", "assets"):
        _mkdir_no_follow(lesson_dir / name)
    _ensure_bundle_repo(lesson_dir)

    manifest_path = _manifest_path(lesson["slug"])
    read = bundle_schema.read_manifest_path(manifest_path, db_lesson=lesson)
    if read is None:  # genuinely missing: creation, not migration (§9.1)
        _write_manifest(manifest_path, _default_manifest(lesson))
        read = bundle_schema.read_manifest_path(manifest_path, db_lesson=lesson)
        if read is None:
            return bundle_schema.rejected_read(
                "manifest-unreadable", "manifest vanished after creation"
            )
    if read.version == bundle_schema.SCHEMA_V2 and not read.rejected:
        _mkdir_no_follow(lesson_dir / bundle_schema.DEFAULT_ARTIFACT_ROOT)

    # Non-destructive bridge from the earlier flat-file prototype:
    # data/lessons/<slug>.html -> data/lessons/<slug>/index.html. Neither
    # side may be (or pass through) a symlink (§2).
    index = lesson_dir / DEFAULT_ENTRY
    if not index.exists() and not index.is_symlink():
        legacy = _read_regular_no_follow(
            _legacy_lesson_path(lesson["slug"]), PAGE_IDENTITY_MAX_BYTES
        )
        if legacy is not None:
            index.write_text(legacy.data.decode("utf-8", errors="replace"), encoding="utf-8")

    return read


def _manifest_version(lesson: dict) -> str:
    """Manifest mtime token (lstat — never follows a planted link), folded
    into placeholder versions so the live-reload poller sees
    placeholder-to-placeholder transitions (missing ↔ rejected ↔ fixed)."""
    try:
        return str(os.lstat(_manifest_path(lesson["slug"])).st_mtime_ns)
    except OSError:
        return "0"


def _finding_views(read: bundle_schema.ManifestRead) -> list[dict]:
    """Findings for the preview metadata — readers MUST surface them (§9.2)."""
    return [
        {"code": f.code, "severity": f.severity, "detail": f.detail}
        for f in read.findings
    ]


def _bridge_block_views(
    read: bundle_schema.ManifestRead, page_id: str
) -> list[dict]:
    """Block identities for one armed page, with runner health folded into
    Run. Health is consulted only when at least one block could otherwise
    run, so ordinary preview reads do not start the runner probe."""
    blocks = [block for block in read.blocks if block["page"] == page_id]
    healthy = False
    if any(block["run_enabled"] for block in blocks):
        try:
            from ..runner import runner_health

            healthy = runner_health().available
        except Exception:
            healthy = False
    return [
        {"id": block["id"], "run": bool(block["run_enabled"] and healthy)}
        for block in blocks
    ]


def _resolve_entry(lesson: dict, read: bundle_schema.ManifestRead, entry: str | None) -> str:
    """One owner of the page-selection rule. v2 accepts only declared
    `pages[].path`, compared exactly (§4.1/§4.2); anything else falls back to
    the manifest entry with a visible `invalid-entry` finding. v1 keeps its
    historical tolerance of undeclared well-formed refs; malformed input raises."""
    candidate = entry or lesson.get("current_entry")
    if read.version == bundle_schema.SCHEMA_V2:
        if candidate:
            if candidate in read.page_paths():
                return candidate
            read.add("invalid-entry", f"selection {candidate!r} is not a declared page")
        return read.entry
    return _clean_html_ref(candidate or read.entry)


def selected_page_ref(lesson: dict, entry: str) -> str:
    """The spelling `entry` has to resolve to for the bundle to have accepted
    it: v2 matches declared pages exactly (§4.1) while v1 normalizes, so
    `./index.html` is a fallback under one version and a synonym under the
    other. Raises `LessonError` on a ref no version would take."""
    read = _ensure_bundle_manifest(lesson)
    if read.version != bundle_schema.SCHEMA_V2:
        return _clean_html_ref(entry)
    _clean_bundle_ref(entry)
    return entry


def _file_info(
    lesson: dict,
    read: bundle_schema.ManifestRead,
    entry: str | None,
    *,
    bridge_identity: bool = False,
) -> dict:
    if read.rejected:
        return {
            "entry": None,
            "label": "Manifest",
            "path": str(_manifest_path(lesson["slug"])),
            "rel_path": f"{lesson['slug']}/{MANIFEST_NAME}",
            "exists": False,
            "version": f"rejected:{_manifest_version(lesson)}",
            "size": 0,
            "outcome": read.outcome,
            "findings": _finding_views(read),
            "profile": read.effective_profile,
            "bridge": read.bridge_eligible,
            "bridge_page": None,
        }
    entry = _resolve_entry(lesson, read, entry)
    findings = _finding_views(read)
    outcome = read.outcome
    # §2: a path that resolves through a symlink is missing — checked before
    # any resolve() so the link is never followed.
    if bundle_schema.path_has_symlink(_lesson_dir(lesson["slug"]), entry):
        findings.append({
            "code": "symlinked-path",
            "severity": bundle_schema.DEGRADED,
            "detail": f"{entry} resolves through a symlink",
        })
        if outcome == bundle_schema.OK:
            outcome = bundle_schema.DEGRADED
        path = _lesson_dir(lesson["slug"]) / PurePosixPath(entry)
        exists = False
    else:
        path = _entry_path(lesson["slug"], entry)
        exists = path.is_file()
    # Bridge page identity (§6.3): granted per page, when the manifest is
    # bridge-eligible and the resolved entry is a declared v2 page whose
    # regular file is readable. Computed on request only, not per listing.
    stat = None
    bridge_page = None
    digest = None
    if exists and bridge_identity and read.bridge_eligible and lesson.get("uid"):
        page_id = next((p["id"] for p in read.pages if p["path"] == entry), None)
        try:
            # lstat + S_ISREG: a symlink raced in after the path_has_symlink()
            # check must not have its TARGET sized, and anything non-regular
            # goes to the O_NOFOLLOW open below, which fails closed.
            pre_stat = os.lstat(path) if page_id else None
        except OSError:
            pre_stat = None
        if (
            pre_stat is not None
            and stat_module.S_ISREG(pre_stat.st_mode)
            and pre_stat.st_size > PAGE_IDENTITY_MAX_BYTES
        ):
            findings.append({
                "code": "page-too-large",
                "severity": bundle_schema.DEGRADED,
                "detail": f"{entry} exceeds {PAGE_IDENTITY_MAX_BYTES} bytes; "
                          "no bridge identity",
            })
            if outcome == bundle_schema.OK:
                outcome = bundle_schema.DEGRADED
            stat = pre_stat
        else:
            snapshot = _read_page_snapshot(path) if page_id else None
            if snapshot is None:
                # No snapshot (not a regular file, or oversized past the
                # pre-check): report the page missing rather than serve bytes
                # the token/hash pair does not describe.
                exists = False
            else:
                _, digest, stat = snapshot
                bridge_page = {
                    "lesson_uid": lesson["uid"],
                    "page_id": page_id,
                    "page_rev": f"sha256:{digest}",
                    "questions": [
                        q["id"] for q in read.questions if q["page"] == page_id
                    ],
                    "blocks": _bridge_block_views(read, page_id),
                }
    elif exists:
        stat = path.stat()
    titles = {p["path"]: p["title"] for p in read.pages}
    return {
        "entry": entry,
        "label": titles.get(entry) or _entry_label(entry),
        "path": str(path),
        # Display form: bundle-relative, so templates/APIs never leak the
        # server's absolute filesystem layout (home dir, username) to clients.
        "rel_path": f"{lesson['slug']}/{entry}",
        "exists": exists,
        # The reload token folds the effective profile in, so a manifest-only
        # profile flip reloads the open page under the CSP the metadata now
        # advertises, and for a bridge page the content digest, so an
        # mtime-preserving byte replacement still moves it.
        "version": (
            (f"{stat.st_mtime_ns}:{read.effective_profile}"
             + (f":{digest[:16]}" if digest else ""))
            if stat else f"missing:{_manifest_version(lesson)}"
        ),
        "size": stat.st_size if stat else 0,
        "outcome": outcome,
        "findings": findings,
        # Manifest-level facts: a degraded entry (symlinked/stale selection)
        # does not flip them here.
        "profile": read.effective_profile,
        "bridge": read.bridge_eligible,
        "bridge_page": bridge_page,
    }


def read_bundle(lesson: dict) -> bundle_schema.ManifestRead:
    """Public record-time bundle read for the attempt backend: the same
    dual-read every other consumer uses."""
    return _ensure_bundle_manifest(lesson)


def read_bundle_readonly(lesson: dict) -> bundle_schema.ManifestRead:
    """Pure record-time read: unlike :func:`read_bundle`, never creates the
    lessons root, bundle directory, standard subdirectories, a manifest
    skeleton, an artifact root, or a legacy flat-file copy. Missing state is
    an explicit rejected read, so a GET never turns into a write."""
    try:
        lesson_dir = _lesson_dir(lesson["slug"])
        if not lesson_dir.exists():
            return bundle_schema.rejected_read(
                "manifest-unreadable", "lesson bundle directory is missing"
            )
        if not _bundle_dir_is_safe(lesson_dir):
            return bundle_schema.rejected_read(
                "symlinked-bundle", "lesson bundle dir is not a real directory"
            )
        read = bundle_schema.read_manifest_path(
            _manifest_path(lesson["slug"]), db_lesson=lesson
        )
    except (KeyError, OSError, LessonError):
        return bundle_schema.rejected_read(
            "manifest-unreadable", "lesson bundle cannot be read"
        )
    if read is None:
        return bundle_schema.rejected_read(
            "manifest-unreadable", "lesson manifest is missing"
        )
    return read


def record_panel_db_state(
    conn: sqlite3.Connection, lesson_id: int
) -> tuple[dict, dict, dict]:
    """Read every SQLite-backed Record-panel input from one snapshot.

    A GET and an assessment POST can run concurrently even with one worker,
    so a deferred read transaction keeps every read on one committed version.
    A caller's transaction is reused; otherwise ours is rolled back on exit."""
    from . import assessments, attempts, focus

    own_snapshot = not conn.in_transaction
    if own_snapshot:
        conn.execute("BEGIN")
    try:
        attempt_state = attempts.lesson_attempt_summary(conn, lesson_id)
        review_attempt_ids = {
            attempt["attempt_id"]
            for attempt in attempt_state["latest_by_question"].values()
        }
        state = assessments.panel_state(
            conn, lesson_id, review_attempt_ids=review_attempt_ids
        )
        return state, attempt_state, focus.lesson_total(conn, lesson_id)
    finally:
        if own_snapshot:
            conn.rollback()


def hash_bundle_page(lesson: dict, ref: str) -> str | None:
    """sha256 hex of a bundle page's current raw bytes, or None when the path
    is missing, symlinked (§2), or not a regular file. Used by the attempt
    backend to derive `stale` server-side at record time (§6.3/§6.4)."""
    try:
        ref = _clean_bundle_ref(ref)
        if bundle_schema.path_has_symlink(_lesson_dir(lesson["slug"]), ref):
            return None
        snapshot = _read_page_snapshot(_bundle_path(lesson["slug"], ref))
    except LessonError:
        return None
    return snapshot[1] if snapshot else None


def lesson_file_info(lesson: dict, entry: str | None = None) -> dict:
    """Runtime HTML artifact metadata for one bundle entry, including the
    bridge page identity when the page qualifies."""
    read = _ensure_bundle_manifest(lesson)
    return _file_info(lesson, read, entry, bridge_identity=True)


def bundle_resource_info(lesson: dict, ref: str) -> dict:
    """Runtime metadata for a bundle-relative file, including assets."""
    read = _ensure_bundle_manifest(lesson)
    ref = _clean_bundle_ref(ref)
    # This route serves the preview surface only: for v2 the declared pages
    # plus the `assets/` area, minus learner work under artifact roots (§7);
    # v1 keeps its historical tolerance behind a denylist of the same
    # exclusions. Nothing under a rejected manifest, no reserved names, no
    # symlinked paths. A declared page (and for v2 `assets/`) wins over an
    # overlapping artifact root, so a manifest claiming `assets` as a root
    # cannot 404 content the read model reports as renderable.
    declared_page = ref in read.page_paths()
    if read.version == bundle_schema.SCHEMA_V2:
        preview_surface = declared_page or ref.startswith("assets/")
        allowed = preview_surface
        in_artifact_root = not preview_surface and any(
            ref == root or ref.startswith(root + "/") for root in read.artifact_roots
        )
    else:
        allowed = ref.split("/", 1)[0] not in bundle_schema.RESERVED_NAMES
        in_artifact_root = False
    blocked = (
        read.rejected
        or not allowed
        or in_artifact_root
        or bundle_schema.path_has_symlink(_lesson_dir(lesson["slug"]), ref)
    )
    if blocked:
        path = _lesson_dir(lesson["slug"]) / PurePosixPath(ref)
        exists = False
    else:
        path = _bundle_path(lesson["slug"], ref)
        exists = path.is_file()
    stat = path.stat() if exists else None
    media_type, _encoding = mimetypes.guess_type(path.name)
    media_type = media_type or "application/octet-stream"
    suffix = path.suffix.lower()
    html = media_type in ("text/html", "application/xhtml+xml") or suffix in (".html", ".htm")
    active = html or media_type == "image/svg+xml" or suffix == ".svg"
    # A declared v2 page is served from bytes read on ONE descriptor, and the
    # version token carries the digest of exactly those bytes, in the same
    # mtime:profile[:digest16] formula `_file_info` renders, so the route's
    # `?v` comparison never 409s a page the metadata advertises.
    content = None
    version = str(stat.st_mtime_ns) if stat else "0"
    versioned_page = (
        exists and active and read.version == bundle_schema.SCHEMA_V2 and declared_page
    )
    if versioned_page:
        version = f"{stat.st_mtime_ns}:{read.effective_profile}"
        snapshot = _read_page_snapshot(path)
        if snapshot is not None:
            content, snap_digest, stat = snapshot
            version = f"{stat.st_mtime_ns}:{read.effective_profile}"
            if read.bridge_eligible and lesson.get("uid"):
                version += f":{snap_digest[:16]}"
    return {
        "entry": ref,
        "path": str(path),
        "exists": exists,
        "version": version,
        "size": stat.st_size if stat else 0,
        "media_type": media_type,
        "html": html,
        "active": active,
        "profile": read.effective_profile,
        "content": content,
        # True for a declared v2 page: the serving route enforces the `?v`
        # binding even when no snapshot could be taken (fail closed).
        "versioned_page": versioned_page,
    }


def _bundle_info(
    lesson: dict,
    read: bundle_schema.ManifestRead,
    entry: str | None = None,
) -> dict:
    """Render bundle metadata from one already-established manifest read."""
    base = {
        "manifest": read.raw,
        "manifest_path": str(_manifest_path(lesson["slug"])),
        "schema_version": read.version,
        "profile": read.effective_profile,
        "bridge": read.bridge_eligible,
    }
    if read.rejected:
        return {
            **base,
            "outcome": read.outcome,
            "findings": _finding_views(read),
            "entry": None,
            "stale_selection": None,
            "file": _file_info(lesson, read, None),
            "pages": [],
        }
    candidate = entry or lesson.get("current_entry")
    try:
        current = _resolve_entry(lesson, read, entry)
    except LessonError:
        current = read.entry
    # §4.2: a v2 selection that fell back is reported, not silently repaired;
    # `stale_selection` carries the rejected candidate.
    stale_selection = (
        candidate
        if read.version == bundle_schema.SCHEMA_V2 and candidate and candidate != current
        else None
    )
    # The current entry takes the identity path so the version token the Learn
    # page renders is the same content-bound token the metadata poll answers
    # with; the per-page listing below stays cheap.
    file = _file_info(lesson, read, current, bridge_identity=True)
    info = {
        **base,
        "outcome": file["outcome"],
        "findings": list(file["findings"]),
    }
    pages = read.page_paths()
    if read.version != bundle_schema.SCHEMA_V2 and current not in pages:
        pages.insert(0, current)  # v1 display tolerance; v2 never injects (§4.2)
    return {
        **info,
        "entry": current,
        "stale_selection": stale_selection,
        "file": file,
        "pages": [
            {**_file_info(lesson, read, page), "current": page == current}
            for page in pages
        ],
    }


def with_bundle_info_read(
    lesson: dict | None,
    entry: str | None = None,
) -> tuple[dict | None, bundle_schema.ManifestRead | None]:
    """Add bundle metadata and return the exact manifest read behind it."""
    if lesson is None:
        return None, None
    lesson = dict(lesson)
    read = _ensure_bundle_manifest(lesson)
    lesson["bundle"] = _bundle_info(lesson, read, entry)
    lesson["entry"] = lesson["bundle"]["entry"]
    lesson["file"] = lesson["bundle"]["file"]
    lesson["pages"] = lesson["bundle"]["pages"]
    return lesson, read


def _require_lesson(conn: sqlite3.Connection, lesson_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    if row is None:
        raise LessonError("unknown lesson")
    return row


def get_lesson(conn: sqlite3.Connection, lesson_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    return _lesson_view(row) if row else None


def get_lesson_by_slug(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = conn.execute("SELECT * FROM lessons WHERE slug = ?", (slug,)).fetchone()
    return _lesson_view(row) if row else None


# --- lesson terminal workspace (agent-facing) --------------------------------

AGENTS_FILENAME = "AGENTS.md"
CLAUDE_FILENAME = "CLAUDE.md"
CLAUDE_DIR_NAME = ".claude"
SOURCE_DIR_NAME = "source"
SETTINGS_FILENAME = "settings.json"
REFERENCE_DIR_NAME = "reference"

_STATE_FILE_MAX_BYTES = 64 * 1024


def _stage_written(lesson: dict, ref: str) -> bool:
    """A declared page counts as written only when its file is a regular file
    reached through no symlink (§2) and within the bridge identity cap: a
    placeholder records nothing, and neither does a page too large to carry
    a `page_rev` (the `page-too-large` finding in `_file_info`)."""
    try:
        ref = _clean_bundle_ref(ref)
        if bundle_schema.path_has_symlink(_lesson_dir(lesson["slug"]), ref):
            return False
        st = _bundle_path(lesson["slug"], ref).stat()
    except (LessonError, OSError):
        return False
    return stat_module.S_ISREG(st.st_mode) and st.st_size <= PAGE_IDENTITY_MAX_BYTES


def _changed_editor_files(lesson_dir: Path, files: list[str]) -> int | None:
    """How many of `files` the bundle's repository tracks and now holds
    different bytes from their first commit; None when there is no repository
    to ask. History is read through git, the working tree through the
    module's own reader, and a file no commit has seen yet is not counted."""
    git_dir = lesson_dir / GIT_DIR_NAME
    if git_dir.is_symlink() or not git_dir.is_dir():
        return None
    git = [
        "git", f"--git-dir={git_dir}", f"--work-tree={lesson_dir}",
        "--literal-pathspecs",
    ]

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [*git, *args], capture_output=True, timeout=GIT_INIT_TIMEOUT_SECONDS
        )

    try:
        if run("rev-parse", "--git-dir").returncode != 0:
            return None
        changed = 0
        for rel in files:
            if bundle_schema.path_has_symlink(lesson_dir, rel):
                continue
            current = _read_regular_no_follow(lesson_dir / rel, _STATE_FILE_MAX_BYTES)
            if current is None:
                continue
            history = run("rev-list", "--reverse", "HEAD", "--", rel)
            first = history.stdout.split(b"\n", 1)[0].decode("ascii", errors="replace")
            if history.returncode != 0 or not first:
                continue
            starter = run("cat-file", "blob", f"{first}:{rel}")
            if starter.returncode == 0 and starter.stdout != current.data:
                changed += 1
        return changed
    except (OSError, subprocess.SubprocessError):
        return None


def _agent_path_dirs() -> list[Path]:
    """Where an agent shell will look for an executable, in its own order."""
    home = os.path.expanduser("~")
    path = f"{home}/.local/bin:/usr/local/bin:" + os.environ.get(
        "PATH", "/usr/bin:/bin"
    )
    return [Path(directory) for directory in path.split(os.pathsep)]


def _on_agent_path(program: str) -> bool:
    return any(
        os.access(directory / program, os.X_OK)
        for directory in _agent_path_dirs()
    )


def _go_on_agent_path() -> bool:
    return _on_agent_path("go")


# The tutor CLIs this app is used with (app/terminal.py: Claude Code, codex,
# aider), each with how it takes an opening prompt. Preference order, not
# ranking: the first one actually installed is the one the learner has.
TUTOR_CLIS = (
    ("claude", 'claude "{prompt}"'),
    ("codex", 'codex "{prompt}"'),
    ("aider", 'aider --message "{prompt}"'),
)
TUTOR_PROMPT = "Read my record and review my answers."


def tutor_launch_command() -> str | None:
    """The command the "Review my answers" button types, or None when no
    tutor CLI is on the agent shell's PATH: the button is rendered from what
    is installed rather than promising a `claude` that is not there."""
    for program, template in TUTOR_CLIS:
        if _on_agent_path(program):
            return template.format(prompt=TUTOR_PROMPT)
    return None


# Per-excerpt byte bound for STATE's learner-text lines, on the DUMPED form:
# json.dumps sextuples non-ASCII via \uXXXX, so a char-based cut alone would
# let a dozen CJK questions outgrow _STATE_MAX_BYTES.
_STATE_LINE_MAX_BYTES = 700


def _state_json_excerpt(text: str) -> tuple[str, bool]:
    """json.dumps bounded by output BYTES (== len: ensure_ascii is ASCII)."""
    dumped = json.dumps(text)
    if len(dumped) <= _STATE_LINE_MAX_BYTES:
        return dumped, False
    keep = text
    while len(dumped) > _STATE_LINE_MAX_BYTES:
        keep = keep[: max(1, (len(keep) * _STATE_LINE_MAX_BYTES) // len(dumped))]
        dumped = json.dumps(keep)
    return dumped, True


def _render_lesson_state(
    conn: sqlite3.Connection,
    lesson: dict,
    read: bundle_schema.ManifestRead,
) -> str:
    """Serialize the current lesson record for the regenerated agent brief."""
    from . import attempts as attempts_service

    # Open questions come from the same committed version as the rest of the
    # panel state, derived from the RECORDED kind so retiring a control cannot
    # retire the debt, and counted per ATTEMPT rather than per question.
    own_snapshot = not conn.in_transaction
    if own_snapshot:
        conn.execute("BEGIN")
    try:
        state, attempt_state, _focus_total = record_panel_db_state(
            conn, lesson["id"]
        )
        open_questions, open_total = attempts_service.open_questions(
            conn, lesson["id"], state["reviewed_attempt_ids"]
        )
    finally:
        if own_snapshot:
            conn.rollback()
    latest = attempt_state["latest_by_question"]

    def reviewed(attempt: dict | None) -> dict | None:
        return (
            state["reviews_by_attempt"].get(attempt["attempt_id"])
            if attempt else None
        )
    try:
        current = None if read.rejected else _resolve_entry(lesson, read, None)
    except LessonError:
        current = read.entry
    title, title_cut = _state_json_excerpt(lesson["title"])
    page_ref, page_cut = _state_json_excerpt(current or "unavailable")
    lines = [
        "\n## STATE (generated; refreshed on every terminal open)\n",
        f"- Lesson title (data): {title}"
        + (" (cut here; the full title is in `lesson.json`)" if title_cut else ""),
        f"- Lesson slug: `{lesson['slug']}`",
        f"- Current page (data): {page_ref}"
        + (" (cut here; the full path is in `lesson.json`)" if page_cut else ""),
    ]
    if open_questions:
        lines.append(
            f"- FIRST: the learner asked you {open_total} question"
            f"{'' if open_total == 1 else 's'} nobody has answered. Answer "
            "each one before teaching anything new, then record a `review` "
            "naming that attempt — the review IS the answer, and it is what "
            "marks the question closed:"
        )
        for row in open_questions:
            asked, byte_cut = _state_json_excerpt(row["asked"])
            if row["asked_truncated"] or byte_cut:
                asked += " (cut here; the whole text is the record with this "
                asked += 'attempt id in `attempts.jsonl`)'
            lines.append(
                f"  - `{row['question_id']}`, attempt `{row['attempt_id']}` "
                f"({row['created_at']}): {asked}"
            )
        if open_total > len(open_questions):
            lines.append(
                f"  - …and {open_total - len(open_questions)} more, oldest "
                "first in `attempts.jsonl`"
            )
    declared_stages = [page for page in read.pages if page["path"] != read.entry]
    written = [page for page in declared_stages if _stage_written(lesson, page["path"])]
    missing = [page for page in declared_stages if page not in written]
    if written:
        last = written[-1]
        declared = [
            q for q in read.questions
            if q["page"] == last["id"] and q["kind"] != bundle_schema.ASK_TUTOR_KIND
        ]
        answered = sum(
            1 for q in declared
            if latest.get(q["id"]) is not None
            and not attempts_service.row_is_question(latest[q["id"]], q["kind"])
        )
        editors = [
            block["file"] for block in read.blocks if block["page"] == last["id"]
        ]
        changed = (
            _changed_editor_files(_lesson_dir(lesson["slug"]), editors)
            if editors else 0
        )
        last_ref, last_cut = _state_json_excerpt(last["path"])
        lines.append(
            f"- Stages written: {len(written)} of {len(declared_stages)} declared; "
            f"last written stage (data): {last_ref}"
            + (" (cut here; the full path is in `lesson.json`)" if last_cut else "")
            + f"; on it {answered} of {len(declared)} questions answered and "
            + (
                f"{changed} of {len(editors)} editor files changed from their starter"
                if changed is not None else
                "editor files changed from their starter: count unavailable "
                "(the bundle has no git repository)"
            )
        )
    else:
        lines.append(f"- Stages written: 0 of {len(declared_stages)} declared")
    if missing:
        refs = [_state_json_excerpt(page["path"])[0] for page in missing[:5]]
        lines.append(
            "- Declared stages with no page file that can record work (missing, "
            "symlinked, or over the bridge size cap; write or repair them): "
            + ", ".join(refs)
            + (f", and {len(missing) - 5} more" if len(missing) > 5 else "")
        )
    lines.append("- Questions:")
    for question in read.questions:
        attempt = latest.get(question["id"])
        review = reviewed(attempt)
        verdict = review["level"] if review else "none"
        if attempts_service.row_is_question(attempt, question["kind"]):
            lines.append(
                f"  - `{question['id']}` (the learner asks YOU): "
                f"{'asked' if attempt else 'nothing asked'}; "
                f"answered_by_you={'yes' if review else 'no'}"
            )
            continue
        lines.append(
            f"  - `{question['id']}`: "
            f"{'answered' if attempt else 'unanswered'}; verdict={verdict}"
        )
    if not read.questions:
        lines.append("  - none declared")
    lines.extend([
        "- Run history: `runs.jsonl` is the app-owned finished-run log; each "
        "line binds a run and block to its file revision, start/finish timestamps, "
        "and the newest 8192 UTF-8 bytes of combined stdout/stderr. It may be "
        "absent or lag behind; never write it.",
        f"- Summary exists: {'yes' if state['summary'] else 'no'}",
        "- Assessment env names: `EPHEMERIS_ASSESS_URL`, "
        "`EPHEMERIS_ASSESS_TOKEN` (never print the token value)",
        "- Build env name: `EPHEMERIS_BUILD_URL` (no token; the lesson is in the path)",
        "- Environment: Go on PATH (this agent shell)="
        f"{'yes' if _go_on_agent_path() else 'no'}",
        "",
    ])
    return "\n".join(lines)


_BRIEFS_DIR = Path(__file__).resolve().parent / "briefs"


def _brief_text(name: str) -> str:
    return (_BRIEFS_DIR / name).read_text(encoding="utf-8")


_AGENTS_TEMPLATE = _brief_text("agents.md")
_REF_RECORD = _brief_text("record.md")
_REF_BRIDGE = _brief_text("bridge.md")
_REF_PACKAGES = _brief_text("packages.md")
_REF_MANIFEST = _brief_text("manifest.md")


# The reference companions written into every bundle beside the brief,
# regenerated on every terminal open; constant templates, no lesson data.
_REFERENCE_FILES: dict[str, str] = {
    "record.md": _REF_RECORD,
    "bridge.md": _REF_BRIDGE,
    "packages.md": _REF_PACKAGES,
    "manifest.md": _REF_MANIFEST,
}


# Claude Code loads CLAUDE.md (following @-includes); Codex and most other
# agent CLIs read AGENTS.md directly. One brief, two entry points.
_CLAUDE_TEMPLATE = """\
@AGENTS.md

<!-- Generated by the Learn app together with AGENTS.md every time a lesson
     terminal opens; edits here are overwritten. The brief lives in AGENTS.md —
     this file only makes Claude Code load it. -->
"""


# Claude Code resolves `.claude/settings.json` from the directory the session
# starts in when that directory sits outside any repository, as a bundle does.
# A constant: no lesson metadata is interpolated into a file an agent harness
# reads as configuration. Strict JSON, no comment.
_SETTINGS_TEMPLATE = """\
{
  "outputStyle": "Learning"
}
"""

_SETTINGS_BYTES = _SETTINGS_TEMPLATE.encode("utf-8")


def _bundle_dir_is_safe(lesson_dir: Path) -> bool:
    """Refuse a lesson dir reached through a symlink, so a pre-planted link at
    data/lessons/<slug> can't redirect the manifest/AGENTS.md write or the
    shell cwd outside the bundle tree. A not-yet-created dir is fine."""
    if lesson_dir.is_symlink():
        return False  # incl. a dangling link: exists() follows and says False
    if not lesson_dir.exists():
        return True
    if not lesson_dir.is_dir():
        return False
    try:
        return lesson_dir.resolve(strict=True).parent == LESSONS_DIR.resolve()
    except OSError:
        return False


def _write_brief(path: Path, text: str) -> None:
    """Atomically replace a generated agent-facing file at mode 0600: a
    pre-planted link or special file on the name is replaced, never followed."""
    projection.replace_file(path, text.encode("utf-8"), prefix=".brief-")


def _preserve_foreign(path: Path, expected: bytes | None = None) -> None:
    """Move whatever sits at `path` and did not come from this writer aside,
    keeping its bytes under `<name>.collision-<hex>`.

    A node that is not an ordinary single-link file is moved aside unread. An
    ordinary file matching `expected` is this writer's own output and is left
    alone, so a reopen does not pile up aside copies; `expected=None` matches
    nothing, which is what the directory names want.
    """
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    ours = (
        expected is not None
        and stat_module.S_ISREG(st.st_mode)
        and st.st_nlink == 1
        and st.st_size == len(expected)
    )
    if ours:
        try:
            ours = path.read_bytes() == expected
        except OSError:
            ours = False
    if ours:
        return
    os.rename(path, path.with_name(f"{path.name}.collision-{uuid4().hex[:8]}"))


def _ensure_settings_dir(lesson_dir: Path) -> Path:
    """Return the bundle's `.claude/` directory, creating it if needed. A
    link or special file on the name is moved aside rather than followed; a
    real directory is kept, since the app owns only `settings.json` in it."""
    path = lesson_dir / CLAUDE_DIR_NAME
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        _preserve_foreign(path)  # incl. a dangling link: exists() follows, says False
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        if path.is_symlink() or not path.is_dir():
            raise NotADirectoryError(f"{CLAUDE_DIR_NAME} is not a directory")
    return path


def _ensure_reference_dir(lesson_dir: Path) -> Path:
    """Return the bundle's `reference/` directory, creating it if needed;
    same posture as :func:`_ensure_settings_dir`."""
    path = lesson_dir / REFERENCE_DIR_NAME
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        _preserve_foreign(path)  # incl. a dangling link: exists() follows, says False
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        if path.is_symlink() or not path.is_dir():
            raise NotADirectoryError(f"{REFERENCE_DIR_NAME} is not a directory")
    return path


def _write_reference_files(lesson_dir: Path) -> None:
    """Regenerate the reference companions beside the brief, under the same
    replace-don't-follow contract as the briefs."""
    reference_dir = _ensure_reference_dir(lesson_dir)
    for name, text in _REFERENCE_FILES.items():
        path = reference_dir / name
        _preserve_foreign(path, text.encode("utf-8"))
        _write_brief(path, text)


_SOURCE_STORE_YES = """\
- `source/` in this bundle is where raw input lives. Read it before you
  fetch anything: what is already there is what a previous session, or the
  learner, brought in."""

_SOURCE_STORE_NO = """\
- This bundle has NO `source/` directory: the name is already taken by
  something else here, and the app will not move another lesson's content
  aside to claim it. Do not create one and do not write to that name."""

_SOURCE_KEEP_YES = """\
- Save what you pull into `source/`, one file per page, and write a
  `source/_fetched.json` entry beside it recording `url`, the UTC date, and
  the sha256 of the saved bytes. Later sessions — and the learner — must be
  able to see where a claim came from without asking you."""

_SOURCE_KEEP_NO = """\
- With nowhere to save it, fetched material lives only in this session, so
  name its url and date in your own notes as you use it. Do not claim on a
  page that the bundle holds a source file it does not."""


def _source_brief(template: str, source_dir: Path | None) -> str:
    """Fill the brief's source-material slots for the bundle as it stands:
    the instructions follow the directory, not the intention, so the tutor is
    never sent to a `source/` that prep could not create."""
    made = source_dir is not None
    return (
        template
        .replace("%SOURCE_STORE%", _SOURCE_STORE_YES if made else _SOURCE_STORE_NO)
        .replace("%SOURCE_KEEP%", _SOURCE_KEEP_YES if made else _SOURCE_KEEP_NO)
    )


# Half the 32 KiB harness cap each: tests pin the template core under one
# half, this budget bounds STATE to the other, so the whole rendered brief
# always loads however many artifacts or questions a lesson accumulates.
_STATE_MAX_BYTES = 16 * 1024

_STATE_TRUNCATION_NOTE = (
    "  - …STATE hit its size budget and was cut here; the omitted lines\n"
    "    mirror data you can read directly — `lesson.json`, `attempts/`,\n"
    "    and the record files are the primary sources."
)


def _cap_state(state: str) -> str:
    data = state.encode("utf-8")
    if len(data) <= _STATE_MAX_BYTES:
        return state
    note = _STATE_TRUNCATION_NOTE.encode("utf-8")
    keep = data[: _STATE_MAX_BYTES - len(note)]
    keep = keep[: keep.rfind(b"\n") + 1]
    return (keep + note).decode("utf-8")


def _render_agents_brief(source_dir: Path | None, state: str) -> str:
    """The one brief a session gets: source slots filled, STATE injected near
    the top, because agent harnesses cap how much project brief they load
    (Codex reads the first 32 KiB by default) and STATE is the one part that
    changes every session. The template scan happens before the insertion,
    so token-shaped learner data inside STATE is never itself substituted."""
    return _source_brief(_AGENTS_TEMPLATE, source_dir).replace(
        "%STATE%", _cap_state(state), 1
    )


def _ensure_source_dir(lesson_dir: Path) -> Path | None:
    """Return the bundle's `source/` directory, creating it if the name is
    free. The app writes nothing here — the tutor does — so a name already
    taken is left as it is rather than moved aside, and a link is not
    followed: the directory is simply not offered."""
    path = lesson_dir / SOURCE_DIR_NAME
    if path.is_symlink():
        return None
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        return path if path.is_dir() and not path.is_symlink() else None
    return path


def _ensure_build_workspace(slug: str, lesson_dir: Path) -> Path:
    """Return this lesson's persistent build workspace, creating it if needed.

    `<bundle>/node_modules` is a symlink to the workspace's `node_modules`, so
    the agent and the bundler work in an ordinary project layout while the
    bundle on disk never carries a byte of it. On the bundle side anything
    that is not this link is moved aside, a populated directory too: a link
    placed over a directory that already held something would hide it from
    the agent while leaving it on disk. An OSError here becomes "no
    workspace", so the caller refuses to open a shell rather than one whose
    installs land in the bundle.

    This does NOT isolate one lesson's packages from another's: a package
    manager that hardlinks out of one shared cache hands every lesson the
    same inode.
    """
    if not _SLUG_RE.match(slug or ""):
        raise LessonError("invalid lesson slug")
    BUILD_WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    workspace = (BUILD_WORKSPACES_DIR / slug).absolute()
    # Absolute: a link's text resolves against the link's own directory, not
    # against the process cwd a relative data directory was spelled from.
    target = (workspace / BUILD_WORKSPACE_LINK).absolute()
    for path in (workspace, target):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            _preserve_foreign(path)
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            if path.is_symlink() or not path.is_dir():
                raise NotADirectoryError(f"{path.name} is not a directory")
    link = lesson_dir / BUILD_WORKSPACE_LINK
    if link.is_symlink() and os.readlink(link) == str(target):
        return workspace
    if link.is_symlink() or link.exists():
        if not link.is_symlink() and link.is_dir() and not any(link.iterdir()):
            os.rmdir(link)
        else:
            if not link.is_symlink() and link.is_dir():
                _log.warning(
                    "moved a populated %s aside in %s; reinstall to restore it",
                    link.name, lesson_dir,
                )
            _preserve_foreign(link)
    try:
        os.symlink(target, link)
    except FileExistsError:
        # A terminal open and a build may prepare the same lesson at once;
        # the loser is fine as long as the winner placed the same link.
        if not (link.is_symlink() and os.readlink(link) == str(target)):
            raise
    return workspace


# The seams the build step (`lesson_build.py`) uses.

def lesson_bundle_dir(slug: str) -> Path:
    """This lesson's bundle directory (not created here)."""
    return _lesson_dir(slug)


def ensure_build_workspace(slug: str) -> Path:
    """This lesson's build workspace, linked from its bundle."""
    return _ensure_build_workspace(slug, _lesson_dir(slug))


def clean_bundle_ref(value: str | None) -> str:
    """Normalize a bundle-relative reference or raise :class:`LessonError`."""
    return _clean_bundle_ref(value)


def bundle_ref_path(slug: str, ref: str) -> Path:
    """Resolve a bundle-relative reference inside the bundle, or raise."""
    return _bundle_path(slug, ref)


def _resolve_terminal_lesson(
    slug: str | None,
) -> tuple[str, dict, Path] | None:
    """Resolve the DB row and safety-checked bundle path shared by PTY roles."""
    slug = (slug or "").strip()
    if len(slug) > 80 or not _SLUG_RE.match(slug):
        return None
    conn = get_conn()
    try:
        lesson = get_lesson_by_slug(conn, slug)
    finally:
        conn.close()
    if lesson is None:
        return None
    lesson_dir = _lesson_dir(slug)
    if not _bundle_dir_is_safe(lesson_dir):
        return None
    return slug, lesson, lesson_dir


def _workspace_view(
    slug: str,
    lesson: dict,
    lesson_dir: Path,
    build_workspace: Path | None = None,
) -> dict:
    """What a PTY role learns about the lesson it opens on. `id` and `uid`
    are the DB's own identity: the terminal binds the lesson-agent session's
    assessment capability to them. `build_workspace` is None for every role
    but lesson-agent, the only one that installs packages."""
    return {
        "slug": slug,
        "title": lesson["title"],
        "dir": str(lesson_dir),
        "id": lesson["id"],
        "uid": lesson["uid"],
        "build_workspace": (
            str(build_workspace) if build_workspace is not None else None
        ),
    }


def resolve_terminal_workspace(slug: str | None) -> dict | None:
    """Resolve an existing lesson bundle for a no-regeneration PTY role: the
    same checks as :func:`prepare_terminal_workspace`, but no manifest or
    brief writes. A missing bundle is a refusal."""
    try:
        resolved = _resolve_terminal_lesson(slug)
        if resolved is None:
            return None
        slug, lesson, lesson_dir = resolved
        if not lesson_dir.exists():
            return None
    except (OSError, sqlite3.Error, LessonError):
        return None
    return _workspace_view(slug, lesson, lesson_dir)


_RECONCILE_BEFORE_BRIEF = (
    ("attempts", lambda svc, conn, lesson: svc.reconcile_projection_if_stale(conn, lesson)),
)
_RECONCILE_AFTER_BRIEF = (
    ("assessments", lambda svc, conn, lesson: svc.reconcile_projection(conn, lesson)),
    ("learner_memory", lambda svc, conn, lesson: svc.reconcile_projection(conn, lesson)),
    ("runs", lambda svc, conn, lesson: svc.retire_foreign_projection(lesson)),
)


def _reconcile_projections(lesson: dict, steps) -> None:
    """Run the terminal-open projection triggers, best effort: a projection
    the app cannot repair must not keep the terminal from opening. Each
    service imports this module, so it is imported here on demand."""
    for module_name, step in steps:
        try:
            service = importlib.import_module(f".{module_name}", __package__)
            conn = get_conn()
            try:
                step(service, conn, lesson)
            finally:
                conn.close()
        except (OSError, sqlite3.Error, ImportError, LessonError):
            pass


def prepare_terminal_workspace(slug: str | None) -> dict | None:
    """Resolve a Learn slug and regenerate its agent-facing terminal briefs.

    Runs in a worker thread off the websocket accept path. Total by design —
    returns None (meaning "REFUSE") for an unknown/invalid slug, a
    symlink-redirected bundle dir, or any DB/filesystem error. The build
    workspace is prepared after `_ensure_bundle_manifest`, which is what
    recreates a bundle directory that went missing.
    """
    try:
        resolved = _resolve_terminal_lesson(slug)
        if resolved is None:
            return None
        slug, lesson, lesson_dir = resolved
        read = _ensure_bundle_manifest(lesson)
        build_workspace = _ensure_build_workspace(slug, lesson_dir)
        source_dir = _ensure_source_dir(lesson_dir)
        # Before the brief: STATE sends the tutor to `attempts.jsonl` for the
        # rest of a long question, so the file heals before that pointer is
        # written. Best effort, like its siblings.
        _reconcile_projections(lesson, _RECONCILE_BEFORE_BRIEF)
        conn = get_conn()
        try:
            state = _render_lesson_state(conn, lesson, read)
        finally:
            conn.close()
        _write_brief(
            lesson_dir / AGENTS_FILENAME,
            _render_agents_brief(source_dir, state),
        )
        _write_brief(lesson_dir / CLAUDE_FILENAME, _CLAUDE_TEMPLATE)
        settings_path = _ensure_settings_dir(lesson_dir) / SETTINGS_FILENAME
        _preserve_foreign(settings_path, _SETTINGS_BYTES)
        _write_brief(settings_path, _SETTINGS_TEMPLATE)
        _write_reference_files(lesson_dir)
    except (OSError, sqlite3.Error, LessonError):
        return None
    # After the briefs: the workspace is ready either way, and a projection
    # hiccup may not cost the agent its regenerated contract.
    _reconcile_projections(lesson, _RECONCILE_AFTER_BRIEF)
    return _workspace_view(slug, lesson, lesson_dir, build_workspace)


def create_lesson(conn: sqlite3.Connection, title: str, source_url: str | None = None) -> int:
    """Create one backlog lesson and append its ledger event in the same txn.
    The lesson uid is minted here, exactly once (learn-bundle-spec.md §3);
    the manifest written right after only carries an echo."""
    title = _clean_title(title)
    source_url = _clean_url(source_url)
    slug = _unique_slug(conn, title)
    uid = str(uuid4())
    ts = now_iso()
    with conn:
        cur = conn.execute(
            "INSERT INTO lessons (uid, title, source_url, slug, status, created_at) "
            "VALUES (?, ?, ?, ?, 'backlog', ?)",
            (uid, title, source_url, slug, ts),
        )
        lesson_id = cur.lastrowid
        # No title echo (learn-bundle-spec.md §8): adapters resolve current
        # metadata by lesson_uid; the DB row and manifest own the title.
        append_event(conn, "lesson_created", {
            "lesson_id": lesson_id,
            "lesson_uid": uid,
            "source_url": source_url,
            "slug": slug,
            "status": "backlog",
        })
    # v2 skeleton at creation (§5). Best-effort: a filesystem hiccup must not
    # undo the committed lesson — the read path recreates a missing manifest.
    try:
        _ensure_bundle_manifest(get_lesson(conn, lesson_id))
    except OSError:
        pass
    return lesson_id


def mark_opened(conn: sqlite3.Connection, lesson_id: int, entry: str) -> None:
    """Persist lightweight UI state without a ledger event. Callers pass an
    entry already resolved against the bundle read model."""
    entry = _clean_html_ref(entry)
    _require_lesson(conn, lesson_id)
    ts = now_iso()
    with conn:
        conn.execute(
            "UPDATE lessons SET current_entry=?, last_opened_at=? WHERE id=?",
            (entry, ts, lesson_id),
        )


def set_current_entry(conn: sqlite3.Connection, lesson_id: int, entry: str) -> None:
    """Explicitly set the lesson entry. For a v2 bundle only declared
    `pages[].path` values are accepted, compared exactly (§4.1/§4.2); a
    rejected manifest refuses the write."""
    row = _require_lesson(conn, lesson_id)
    read = _ensure_bundle_manifest(_lesson_view(row))
    if read.rejected:
        raise LessonError("lesson manifest is rejected; fix lesson.json first")
    if read.version == bundle_schema.SCHEMA_V2:
        if entry not in read.page_paths():
            raise LessonError("entry is not a declared lesson page")
    else:
        entry = _clean_html_ref(entry)
    ts = now_iso()
    with conn:
        conn.execute(
            "UPDATE lessons SET current_entry=?, updated_at=? WHERE id=?",
            (entry, ts, lesson_id),
        )
        append_event(conn, "lesson_entry_changed", {
            "lesson_id": lesson_id,
            "lesson_uid": row["uid"],
            "from_entry": row["current_entry"],
            "to_entry": entry,
        })


def set_status(conn: sqlite3.Connection, lesson_id: int, status: str) -> None:
    """Move an active lesson through backlog/studying/paused/studied."""
    if status not in STATUSES:
        raise LessonError("unknown lesson status")
    row = _require_lesson(conn, lesson_id)
    if row["archived_at"] is not None:
        raise LessonError("lesson is archived")
    ts = now_iso()
    started_at = row["started_at"]
    completed_at = row["completed_at"]
    if status == "backlog":
        started_at = None
        completed_at = None
    elif status in ("studying", "paused") and not started_at:
        started_at = ts
        completed_at = None
    elif status in ("studying", "paused"):
        completed_at = None
    elif status == "studied":
        started_at = started_at or ts
        completed_at = ts
    with conn:
        conn.execute(
            "UPDATE lessons SET status=?, updated_at=?, started_at=?, completed_at=? "
            "WHERE id=?",
            (status, ts, started_at, completed_at, lesson_id),
        )
        append_event(conn, "lesson_status_changed", {
            "lesson_id": lesson_id,
            "lesson_uid": row["uid"],
            "from_status": row["status"],
            "to_status": status,
        })


def archive_lesson(conn: sqlite3.Connection, lesson_id: int) -> None:
    row = _require_lesson(conn, lesson_id)
    if row["archived_at"] is not None:
        return
    ts = now_iso()
    with conn:
        conn.execute(
            "UPDATE lessons SET archived_at=?, updated_at=? WHERE id=?",
            (ts, ts, lesson_id),
        )
        append_event(conn, "lesson_archived", {
            "lesson_id": lesson_id,
            "lesson_uid": row["uid"],
            "status": row["status"],
        })


def restore_lesson(conn: sqlite3.Connection, lesson_id: int) -> None:
    row = _require_lesson(conn, lesson_id)
    if row["archived_at"] is None:
        return
    ts = now_iso()
    with conn:
        conn.execute(
            "UPDATE lessons SET archived_at=NULL, updated_at=? WHERE id=?",
            (ts, lesson_id),
        )
        append_event(conn, "lesson_restored", {
            "lesson_id": lesson_id,
            "lesson_uid": row["uid"],
            "status": row["status"],
        })


# Active first, then studying → paused → backlog → studied, freshest within each.
# Shared by the Learn list and Search so both rank lessons identically.
_LESSON_ORDER = (
    " ORDER BY "
    "CASE WHEN archived_at IS NULL THEN 0 ELSE 1 END, "
    "CASE status "
    "WHEN 'studying' THEN 0 WHEN 'paused' THEN 1 "
    "WHEN 'backlog' THEN 2 WHEN 'studied' THEN 3 ELSE 4 END, "
    "COALESCE(updated_at, created_at) DESC, id DESC"
)


def list_lessons(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    include_archived: bool = False,
    archived_only: bool = False,
) -> list[dict]:
    """Lessons for the Learn list, active by default."""
    params: list[object] = []
    where = []
    if status:
        if status not in STATUSES:
            raise LessonError("unknown lesson status")
        where.append("status = ?")
        params.append(status)
    if archived_only:
        where.append("archived_at IS NOT NULL")
    elif not include_archived:
        where.append("archived_at IS NULL")
    sql = "SELECT * FROM lessons"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += _LESSON_ORDER
    return [_lesson_view(row) for row in conn.execute(sql, params).fetchall()]


def search(conn: sqlite3.Connection, query: str, limit: int = 50) -> list[dict]:
    """Lessons whose title, notes, or source URL contain `query` (case-insensitive
    substring). Spans archived lessons too — the Search view marks them. Empty
    query returns nothing, mirroring tasks.search."""
    q = (query or "").strip()
    if not q:
        return []
    # escape LIKE metacharacters so a literal % or _ isn't treated as a wildcard
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{esc}%"
    rows = conn.execute(
        "SELECT * FROM lessons "
        "WHERE title LIKE ? ESCAPE '\\' OR COALESCE(notes,'') LIKE ? ESCAPE '\\' "
        "OR COALESCE(source_url,'') LIKE ? ESCAPE '\\'"
        + _LESSON_ORDER + " LIMIT ?",
        (like, like, like, limit),
    ).fetchall()
    return [_lesson_view(row) for row in rows]


# Human text for the §9.2 short-circuit/reject codes the preview can hit.
_REJECT_MESSAGES = {
    "unsupported-version": "Unsupported manifest version.",
    "manifest-unreadable": "lesson.json is not readable JSON.",
    "manifest-too-large": "lesson.json exceeds the manifest size limit.",
    "symlinked-bundle": "The lesson bundle resolves through a symlink.",
    "missing-identity": "The manifest is missing its lesson identity.",
    "duplicate-id": "The manifest repeats an id.",
    "duplicate-path": "The manifest claims one path twice.",
    "limit-exceeded": "A manifest list exceeds its size limit.",
    "no-pages": "The manifest declares no valid pages.",
}


SCHEMES = ("light", "dark")
"""The resolved app themes a caller may ask the placeholder to paint in."""

# The night palette, repeating html[data-theme="dark"] in style.css. This
# document is sandboxed and cannot see the app's tokens, so the two copies are
# kept in step by hand; `--bg` and `--card-bg` are the ones that matter.
_PLACEHOLDER_NIGHT = """\
    body { color: #e6e4da; background: #10131f; }
    main {
      border-color: #262c42; background: #1e2338;
      box-shadow: 0 12px 36px rgba(0,0,0,.34);
    }
    code { background: #171b2c; }"""


def _placeholder_scheme_css(scheme: str | None) -> str:
    """The colour-scheme half of the placeholder's stylesheet. `scheme` is
    the theme the app resolved, which is NOT always what the OS reports: the
    app theme is a tri-state and can be pinned against it. `None` means
    nobody said, and only then does the OS answer."""
    if scheme == "dark":
        return f"    :root {{ color-scheme: dark; }}\n{_PLACEHOLDER_NIGHT}"
    if scheme == "light":
        return "    :root { color-scheme: light; }"
    night = textwrap.indent(_PLACEHOLDER_NIGHT, "  ")
    return ("    :root { color-scheme: light dark; }\n"
            "    @media (prefers-color-scheme: dark) {\n"
            f"{night}\n"
            "    }")


def _placeholder_html(title: str, message: str, code_line: str,
                      scheme: str | None = None) -> str:
    title = escape(title)
    message = escape(message)
    code_line = escape(code_line)
    scheme_css = _placeholder_scheme_css(scheme)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      font: 14px/1.5 system-ui, -apple-system, Segoe UI, sans-serif;
      color: #2d3035; background: #f6f7f9;
    }}
    main {{
      width: min(680px, calc(100vw - 48px)); padding: 32px;
      border: 1px solid #e3e6ea; border-radius: 8px; background: white;
      box-shadow: 0 12px 36px rgba(0,0,0,.08);
    }}
    h1 {{ margin: 0 0 10px; font-size: 22px; line-height: 1.2; }}
    code {{
      display: block; margin-top: 16px; padding: 12px;
      border-radius: 7px; background: #f1f3f5; overflow-wrap: anywhere;
      font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
{scheme_css}
  </style>
</head>
<body>
  <main>
    <h1>{title}</h1>
    <p>{message}</p>
    <code>{code_line}</code>
  </main>
</body>
</html>
"""


def preview_html(lesson: dict, entry: str | None = None, *,
                 scheme: str | None = None) -> tuple[str, dict]:
    """Return the current lesson HTML, or a small generated placeholder —
    including the explicit rejected-manifest placeholder (§9.1). `scheme` is
    the reader's resolved app theme, used only for a placeholder; real lesson
    HTML is returned byte for byte."""
    info = lesson_file_info(lesson, entry)
    if info["exists"]:
        return Path(info["path"]).read_text(encoding="utf-8", errors="replace"), info
    if info["outcome"] == bundle_schema.REJECTED:
        codes = sorted({f["code"] for f in info["findings"]
                        if f["severity"] == bundle_schema.REJECTED})
        message = " ".join(_REJECT_MESSAGES.get(code, "The lesson manifest was rejected.")
                           for code in codes)
        html = _placeholder_html(lesson["title"], message,
                                 f"{info['rel_path']}: {', '.join(codes)}", scheme)
    else:
        html = _placeholder_html(lesson["title"], "No HTML file yet.",
                                 info["rel_path"], scheme)
    return html, info


# --- track progress ----------------------------------------------------------
#
# Track membership lives ONLY in `lesson.json` (`path`/`step`), agent-written
# and app-read-only, so it is derived per render rather than mirrored into the
# `lessons` table. The read is the readonly one: rendering /learn must not
# create bundle state.

def track_progress(
    rows: list[dict],
    *,
    reads: dict[int, bundle_schema.ManifestRead] | None = None,
) -> list[dict]:
    """Per-track "N of M studied" plus the first unstudied step, from manifests.

    A lesson contributes to a track when its manifest declares a `path`; one
    whose manifest is missing or unreadable belongs to no track. `reads`
    supplies manifests the caller already read, by lesson id, so the /learn
    render's one read stays the single authority for its bundle. Members
    order by `step`, then slug; tracks by `path`. `ids` carries the member
    order out, so a caller grouping rows uses the same membership rule that
    produced the counts.
    """
    by_path: dict[str, list[dict]] = {}
    for row in rows:
        read = (reads or {}).get(row["id"]) or read_bundle_readonly(row)
        # A rejected read's model fields mean nothing, and an `identity-mismatch`
        # means the bundle on disk belongs to a DIFFERENT lesson, whose
        # declaration must not enrol this row in a track.
        if read.rejected or read.lesson_uid != row["uid"]:
            continue
        # Normalized (§4.5): a stray doubled or trailing slash cannot file a
        # lesson under an address that renders like its neighbour's.
        segments = bundle_schema.split_path_ref(read.path_ref)
        if not segments:
            continue
        by_path.setdefault("/".join(segments), []).append(
            {"row": row, "step": read.step}
        )
    tracks = []
    for path in sorted(by_path):
        # An absent step sorts last: a declared member never displaces one that
        # positioned itself.
        members = sorted(
            by_path[path],
            key=lambda m: (m["step"] is None, m["step"] or 0, m["row"]["slug"]),
        )
        nxt = next((m["row"] for m in members if m["row"]["status"] != "studied"), None)
        tracks.append({
            "path": path,
            "studied": sum(1 for m in members if m["row"]["status"] == "studied"),
            "total": len(members),
            "next": {"id": nxt["id"], "title": nxt["title"]} if nxt else None,
            "ids": [m["row"]["id"] for m in members],
        })
    return tracks


def path_tree(tracks: list[dict]) -> list[dict]:
    """`track_progress` output nested by address segment, counts rolled up.

    One node per address: `codecrafters/concepts/network-protocols` yields
    three, and an ancestor may be a track in its own right, so a node can
    have both `rows_ids` and `children`. `studied`/`total`/`ids`/`next` are
    the whole SUBTREE's, so a folded ancestor's "2 of 9" counts what
    unfolding it would reveal; nothing here re-derives membership. Traversal
    is own rows first, then children by address, and `next` follows it.
    """
    by_address = {track["path"]: track for track in tracks}
    nodes: dict[str, dict] = {}

    def ensure(address: str) -> dict:
        node = nodes.get(address)
        if node is not None:
            return node
        segments = address.split("/")
        node = {
            "path": address,
            "name": segments[-1],
            "depth": len(segments) - 1,
            "children": [],
        }
        nodes[address] = node
        if len(segments) > 1:
            ensure("/".join(segments[:-1]))["children"].append(node)
        return node

    for track in tracks:
        ensure(track["path"])

    def finish(node: dict) -> None:
        node["children"].sort(key=lambda child: child["path"])
        own = by_address.get(node["path"])
        node["rows_ids"] = list(own["ids"]) if own else []
        node["studied"] = own["studied"] if own else 0
        node["total"] = own["total"] if own else 0
        node["ids"] = list(node["rows_ids"])
        node["next"] = own["next"] if own else None
        for child in node["children"]:
            finish(child)
            node["studied"] += child["studied"]
            node["total"] += child["total"]
            node["ids"] += child["ids"]
            node["next"] = node["next"] or child["next"]

    roots = [node for address, node in nodes.items() if "/" not in address]
    for node in roots:
        finish(node)
    return sorted(roots, key=lambda node: node["path"])


def counts(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM lessons "
        "WHERE archived_at IS NULL GROUP BY status"
    ).fetchall()
    by_status = {status: 0 for status in STATUSES}
    for row in rows:
        by_status[row["status"]] = row["n"]
    archived = conn.execute(
        "SELECT COUNT(*) AS n FROM lessons WHERE archived_at IS NOT NULL"
    ).fetchone()["n"]
    by_status["all"] = sum(by_status.values())
    by_status["archived"] = archived
    return by_status
