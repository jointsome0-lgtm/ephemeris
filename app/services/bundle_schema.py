"""Lesson bundle manifest schema — the runtime half of docs/learn-bundle-spec.md.

Single owner of the v1/v2 manifest read models, the §9.2 finding codes, the
canonical serialization (§9.3), the v2 creation skeleton (§5), and atomic
manifest writes. `lessons.py` consumes this module for every bundle read and
write; verify.py drives it directly against fixtures/lesson-manifests/.

Pure by design: nothing here opens a DB connection. Callers that have the DB
row pass it in as `db_lesson` so the reader can report `identity-mismatch`
and `stale-metadata`.
"""
from __future__ import annotations

import errno
import json
import math
import os
import re
import stat as stat_module
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from uuid import uuid4

SCHEMA_V1 = 1
SCHEMA_V2 = 2

DEFAULT_ENTRY = "index.html"
DEFAULT_ARTIFACT_ROOT = "attempts"
RESERVED_NAMES = ("lesson.json", "attempts.jsonl", "AGENTS.md", "CLAUDE.md")

MAX_MANIFEST_BYTES = 256 * 1024
MAX_PAGES = 200
MAX_QUESTIONS = 200
MAX_BLOCKS = 100
MAX_CONCEPTS = 64
MAX_ARTIFACT_ROOTS = 8
MAX_PATH_LEN = 200
MAX_TITLE_LEN = 240
MAX_LABEL_LEN = 200
MAX_SLUG_LEN = 80
MAX_URL_LEN = 1000
MAX_REF_LEN = 200
MIN_STEP, MAX_STEP = 1, 10000

# Bounded finding materialization: a hostile manifest must not turn one
# preview-metadata poll into megabytes of findings JSON or unbounded work.
MAX_FINDINGS = 100
MAX_FINDING_DETAIL = 200

PROFILE_LEGACY = "legacy-display"
PROFILE_INTERACTIVE = "interactive-local-v1"
PROFILES = (PROFILE_LEGACY, PROFILE_INTERACTIVE)

# Runner registry (F3) is not built yet: no runner_id is recognized, so every
# declared runner degrades to `unknown-runner` (Run disabled, editor kept).
RUNNER_REGISTRY: frozenset[str] = frozenset()

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PAGE_ID_RE = re.compile(r"^pg_[a-z0-9]{4,32}$")
QUESTION_ID_RE = re.compile(r"^q_[a-z0-9]{4,32}$")
BLOCK_ID_RE = re.compile(r"^blk_[a-z0-9]{4,32}$")
KIND_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
LANGUAGE_RE = re.compile(r"^[a-z0-9+.-]{1,40}$")
RUNNER_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

QUESTION_KINDS = ("prediction", "free_text", "self_check")
DEFAULT_QUESTION_KIND = "free_text"

# --- findings (§9.2) ---------------------------------------------------------

REJECTED = "rejected"
DEGRADED = "degraded"
INFO = "info"
OK = "ok"

_SEVERITY = {
    "manifest-unreadable": REJECTED,
    "manifest-too-large": REJECTED,
    "unsupported-version": REJECTED,
    "missing-identity": REJECTED,
    "duplicate-id": REJECTED,
    "duplicate-path": REJECTED,
    "limit-exceeded": REJECTED,
    "no-pages": REJECTED,
    "symlinked-bundle": REJECTED,
    "invalid-entry": DEGRADED,
    "invalid-path": DEGRADED,
    "invalid-id": DEGRADED,
    "outside-root": DEGRADED,
    "dangling-ref": DEGRADED,
    "unknown-profile": DEGRADED,
    "unknown-runner": DEGRADED,
    "unknown-kind": DEGRADED,
    "overlapping-roots": DEGRADED,
    "invalid-ref": DEGRADED,
    "identity-mismatch": DEGRADED,
    "symlinked-path": DEGRADED,
    "type-mismatch": DEGRADED,
    "invalid-value": INFO,
    "missing-attempts-root": INFO,
    "stale-metadata": INFO,
    "duplicate-concept": INFO,
}


@dataclass(frozen=True)
class Finding:
    code: str
    detail: str = ""
    # v1 SHOULD-findings surface conditions without changing v1 render
    # behavior (§9.2), so a v1 duplicate-path is degraded, not rejected.
    severity: str = ""

    def __post_init__(self) -> None:
        if not self.severity:
            object.__setattr__(self, "severity", _SEVERITY[self.code])


@dataclass
class ManifestRead:
    """Typed read model of one manifest, plus everything the reader found.

    `raw` is the parsed JSON object exactly as stored (unknown fields and all)
    — writers preserve through it; the typed fields are the validated model
    consumers act on. On a rejected read the model fields stay at their
    defaults and `pages` is empty.
    """

    version: int | None
    raw: dict | None
    findings: list[Finding] = field(default_factory=list)
    lesson_uid: str | None = None
    entry: str | None = None
    pages: list[dict] = field(default_factory=list)      # {"id","path","title"}
    questions: list[dict] = field(default_factory=list)  # {"id","page","kind","label"}
    blocks: list[dict] = field(default_factory=list)     # {"id","page","kind","language","file","runner_id","run_enabled"}
    path_ref: str | None = None
    step: int | None = None
    concepts: list[str] = field(default_factory=list)
    profile: str = PROFILE_LEGACY
    artifact_roots: list[str] = field(default_factory=lambda: [DEFAULT_ARTIFACT_ROOT])
    updated_by_agent_at: str | None = None

    @property
    def outcome(self) -> str:
        worst = OK
        for f in self.findings:
            if f.severity == REJECTED:
                return REJECTED
            if f.severity == DEGRADED:
                worst = DEGRADED
        return worst

    @property
    def rejected(self) -> bool:
        return self.outcome == REJECTED

    @property
    def bridge_eligible(self) -> bool:
        """§5 (enforced in D1): the postMessage bridge may only be offered for
        a manifest that parsed as v2, was not rejected, and carries the
        interactive profile. v1, rejected reads, and every fail-closed-to-
        legacy path (missing/unknown profile) never get the bridge; degraded
        v2 findings keep it — identity stays valid, D2 gates per page."""
        return (
            self.version == SCHEMA_V2
            and not self.rejected
            and self.profile == PROFILE_INTERACTIVE
        )

    def codes(self) -> set[str]:
        return {f.code for f in self.findings}

    def page_paths(self) -> list[str]:
        return [p["path"] for p in self.pages]

    def add(self, code: str, detail: str = "", severity: str = "") -> None:
        # Bounded: past the cap, only a not-yet-seen code is still recorded,
        # so every applicable code surfaces (§9.2) at a bounded total size.
        if len(self.findings) >= MAX_FINDINGS and any(f.code == code for f in self.findings):
            return
        self.findings.append(Finding(code, detail[:MAX_FINDING_DETAIL], severity))


def rejected_read(code: str, detail: str = "") -> ManifestRead:
    read = ManifestRead(version=None, raw=None)
    read.add(code, detail)
    return read


# --- path and value grammar (§4.1, §4.5) -------------------------------------


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def valid_v2_path(value: object, *, html: bool = False) -> bool:
    """§4.1 grammar for `entry`, `pages[].path`, `blocks[].file`,
    `artifact_roots[]`. Exact comparison, no normalization: a path that needs
    cleaning is invalid rather than repaired."""
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_PATH_LEN:
        return False
    if "\\" in value or _has_control_chars(value):
        return False
    if value != value.strip():
        # §4.1: the app's request-cleaning layer strips edge whitespace, so
        # such a path could never be resolved or served verbatim — invalid,
        # not repaired (C3 round 17).
        return False
    if value.startswith("/"):
        return False
    segments = value.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        return False
    if segments[0] in RESERVED_NAMES:  # equal to, or nested under, a reserved name
        return False
    if html and not value.endswith(".html"):
        return False
    return True


def valid_opaque_ref(value: object) -> bool:
    """§4.5 Atlas-facing refs: an atom of 1–200 chars, no control characters."""
    return (
        isinstance(value, str)
        and 1 <= len(value) <= MAX_REF_LEN
        and not _has_control_chars(value)
    )


def clean_v1_ref(value: object, *, html_only: bool = False) -> str | None:
    """The normative v1 cleaning (§9.2): PurePosixPath normalization collapses
    `./`, doubled and trailing slashes; backslash, control characters, absolute
    paths, and `..` are rejected. Returns the cleaned path or None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or "\\" in value or _has_control_chars(value):
        return None
    ref = PurePosixPath(value)
    if ref.is_absolute() or ".." in ref.parts:
        return None
    if html_only and ref.suffix.lower() != ".html":
        return None
    return ref.as_posix()


def _valid_source_url(value: str) -> bool:
    if len(value) > MAX_URL_LEN:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:  # e.g. malformed bracketed IPv6 authority
        return False
    return parsed.scheme.lower() in ("http", "https") and bool(parsed.netloc)


def _is_int(value: object) -> bool:
    # JSON booleans are ints to Python; the schema says integer, not boolean.
    return isinstance(value, int) and not isinstance(value, bool)


# --- readers (§9) -------------------------------------------------------------


def _reject_nonstandard(const: str) -> None:
    """NaN/Infinity/-Infinity are Python extensions, not JSON: a manifest
    carrying them is not valid JSON and must read as manifest-unreadable —
    the canonical writer must never re-emit a non-JSON token."""
    raise json.JSONDecodeError(f"non-standard JSON constant {const}", "", 0)


def _finite_float(token: str) -> float:
    """A standard number token that overflows to inf (e.g. 1e10000) must not
    be accepted either: json.dumps would re-emit it as the non-JSON token
    `Infinity`, poisoning the manifest for the next read."""
    value = float(token)
    if not math.isfinite(value):
        raise json.JSONDecodeError(f"non-finite number {token}", "", 0)
    return value


def read_manifest_text(
    text: str,
    *,
    db_lesson: dict | None = None,
    runner_registry: frozenset[str] = RUNNER_REGISTRY,
) -> ManifestRead:
    return read_manifest_bytes(
        text.encode("utf-8"), db_lesson=db_lesson, runner_registry=runner_registry
    )


def read_manifest_bytes(
    data: bytes,
    *,
    db_lesson: dict | None = None,
    runner_registry: frozenset[str] = RUNNER_REGISTRY,
) -> ManifestRead:
    """Dual-read dispatch (§9.1). Short-circuits only on manifest-too-large,
    manifest-unreadable, and unsupported-version; every other finding
    accumulates across the whole manifest."""
    if len(data) > MAX_MANIFEST_BYTES:
        return rejected_read("manifest-too-large", f"{len(data)} bytes")
    try:
        raw = json.loads(
            data.decode("utf-8"),
            parse_constant=_reject_nonstandard,
            parse_float=_finite_float,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        # ValueError covers JSONDecodeError AND the decoder's other parse-time
        # failures (e.g. the int-conversion digit limit on a huge number
        # token): the parse boundary is total — unreadable, never a crash.
        return rejected_read("manifest-unreadable", str(exc)[:MAX_FINDING_DETAIL])
    except RecursionError:  # pathologically deep JSON is unreadable, not a crash
        return rejected_read("manifest-unreadable", "manifest nesting too deep")
    if not isinstance(raw, dict):
        return rejected_read("manifest-unreadable", "manifest is not a JSON object")

    version = raw.get("schema_version", SCHEMA_V1)  # missing ⇒ v1 (§9.1)
    if not _is_int(version) or version not in (SCHEMA_V1, SCHEMA_V2):
        read = rejected_read("unsupported-version", f"schema_version {version!r}")
        read.raw = raw
        return read
    if version == SCHEMA_V1:
        return _read_v1(raw)
    return _read_v2(raw, db_lesson=db_lesson, runner_registry=runner_registry)


def read_manifest_path(
    path: Path,
    *,
    db_lesson: dict | None = None,
    runner_registry: frozenset[str] = RUNNER_REGISTRY,
) -> ManifestRead | None:
    """Read a manifest file without ever following a symlink at the manifest
    path itself (§2: a symlinked lesson.json is `symlinked-bundle`, never
    "missing" — a default skeleton there would mask a planted link).
    Returns None when the file genuinely does not exist (creation is the
    caller's decision)."""
    try:
        # O_NONBLOCK so a planted FIFO cannot park the reader on open.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        if path.is_symlink():  # dangling symlink: present, planted, rejected
            return rejected_read("symlinked-bundle", "lesson.json is a symlink")
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return rejected_read("symlinked-bundle", "lesson.json is a symlink")
        # strerror only: the finding detail reaches clients, the path must not
        return rejected_read("manifest-unreadable", exc.strerror or "unreadable")
    try:
        st = os.fstat(fd)
        if not stat_module.S_ISREG(st.st_mode):
            return rejected_read("manifest-unreadable", "lesson.json is not a regular file")
        if st.st_size > MAX_MANIFEST_BYTES:
            return rejected_read("manifest-too-large", f"{st.st_size} bytes")
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            data = fh.read(MAX_MANIFEST_BYTES + 1)
    except OSError as exc:
        return rejected_read("manifest-unreadable", exc.strerror or "unreadable")
    finally:
        if fd >= 0:
            os.close(fd)
    return read_manifest_bytes(
        data, db_lesson=db_lesson, runner_registry=runner_registry
    )


def _read_v1(raw: dict) -> ManifestRead:
    """The normative v1 read model — exactly today's `_normalise_manifest`
    behavior, with the §9.2 SHOULD-findings surfaced instead of silent drops.
    v1 render behavior is unchanged, so these findings never reject."""
    read = ManifestRead(version=SCHEMA_V1, raw=raw)

    raw_entry = raw.get("entry")
    if isinstance(raw_entry, dict):  # object form is unwrapped
        raw_entry = raw_entry.get("path")
    entry = clean_v1_ref(raw_entry, html_only=True) if raw_entry is not None else None
    if entry is None:
        if raw_entry is not None or "entry" in raw:
            read.add("invalid-entry", "entry fell back to index.html", DEGRADED)
        entry = DEFAULT_ENTRY
    read.entry = entry
    read.pages.append({"id": None, "path": entry, "title": None})

    raw_related = raw.get("related")
    if not isinstance(raw_related, list):
        raw_related = []
    seen: list[str] = []
    for item in raw_related:
        candidate = item.get("path") if isinstance(item, dict) else item
        ref = clean_v1_ref(candidate, html_only=True)
        if ref is None:
            read.add("invalid-path", f"related item {candidate!r} dropped", DEGRADED)
            continue
        if ref == entry or ref in seen:
            read.add("duplicate-path", f"related item {ref!r} deduplicated", DEGRADED)
            continue
        seen.append(ref)
        read.pages.append({"id": None, "path": ref, "title": None})

    updated = raw.get("updated_by_agent_at")
    read.updated_by_agent_at = updated if isinstance(updated, str) else None
    read.profile = PROFILE_LEGACY  # v1 can never opt into interactivity (§5)
    return read


def _check_metadata_copies(read: ManifestRead, raw: dict, db_lesson: dict | None) -> None:
    """slug/title/source_url are non-authoritative copies (§4): a grammar
    violation or a DB mismatch is informational and the DB value wins."""
    checks = (
        ("slug", lambda v: len(v) <= MAX_SLUG_LEN and SLUG_RE.match(v)),
        ("title", lambda v: 0 < len(v.strip()) <= MAX_TITLE_LEN),
        ("source_url", _valid_source_url),
    )
    for name, valid in checks:
        value = raw.get(name)
        if value is None:
            if name == "source_url":  # null is accepted as absent (§9.3)
                if db_lesson is not None and db_lesson.get(name):
                    read.add("stale-metadata", f"{name} copy differs from the DB")
            elif name in raw:
                read.add("type-mismatch", f"{name} is null")
            else:
                read.add("stale-metadata", f"{name} copy is missing")
            continue
        if not isinstance(value, str):
            read.add("type-mismatch", f"{name} is not a string")
            continue
        if not valid(value):
            read.add("stale-metadata", f"{name} copy violates its grammar")
            continue
        if db_lesson is not None and db_lesson.get(name) != value:
            read.add("stale-metadata", f"{name} copy differs from the DB")


def _read_v2(
    raw: dict,
    *,
    db_lesson: dict | None,
    runner_registry: frozenset[str],
) -> ManifestRead:
    read = ManifestRead(version=SCHEMA_V2, raw=raw)

    # identity (§3): missing-identity accumulates — the rest is still checked.
    uid = raw.get("lesson_uid")
    if isinstance(uid, str) and UUID_RE.match(uid):
        read.lesson_uid = uid
        if db_lesson is not None and db_lesson.get("uid") and uid != db_lesson["uid"]:
            read.add("identity-mismatch", "manifest lesson_uid differs from the DB uid")
    else:
        read.add("missing-identity", f"lesson_uid {uid!r}")

    _check_metadata_copies(read, raw, db_lesson)

    # artifact roots (§7) come before blocks: blocks[].file is checked against them.
    read.artifact_roots = _read_artifact_roots(read, raw)

    read.pages = _read_pages(read, raw)
    page_ids = {p["id"] for p in read.pages}
    page_paths = read.page_paths()

    entry = raw.get("entry")
    if not isinstance(entry, str):
        read.add("type-mismatch", "entry is not a string")
        read.add("invalid-entry", "entry treated as absent")
        entry = None
    elif not valid_v2_path(entry, html=True):
        read.add("invalid-entry", f"entry {entry!r} violates the path grammar")
        entry = None
    elif entry not in page_paths:
        read.add("invalid-entry", f"entry {entry!r} is not a declared page")
        entry = None
    read.entry = entry if entry is not None else (page_paths[0] if page_paths else None)

    read.questions = _read_questions(read, raw, page_ids)
    read.blocks = _read_blocks(read, raw, page_ids, runner_registry)
    _read_opaque_refs(read, raw)

    # runtime profile (§5): fail-closed to legacy-display.
    runtime = raw.get("runtime")
    if runtime is not None and not isinstance(runtime, dict):
        read.add("type-mismatch", "runtime is not an object")
        runtime = None
    if runtime is None:
        read.profile = PROFILE_LEGACY
    else:
        profile = runtime.get("profile")
        if isinstance(profile, str) and profile in PROFILES:
            read.profile = profile
        else:
            read.add("unknown-profile", f"profile {profile!r}")
            read.profile = PROFILE_LEGACY
    if "identity-mismatch" in read.codes():
        read.profile = PROFILE_LEGACY  # render as legacy until resolved (§9.2)

    updated = raw.get("updated_by_agent_at")
    if updated is not None:
        if not isinstance(updated, str):
            read.add("type-mismatch", "updated_by_agent_at is not a string")
        else:
            try:
                datetime.fromisoformat(updated.replace("Z", "+00:00"))
                read.updated_by_agent_at = updated
            except ValueError:
                read.add("invalid-value", f"updated_by_agent_at {updated!r}")
    return read


def _read_pages(read: ManifestRead, raw: dict) -> list[dict]:
    items = raw.get("pages")
    if items is not None and not isinstance(items, list):
        read.add("type-mismatch", "pages is not a list")
        items = None
    if not items:
        read.add("no-pages", "v2 manifest declares no pages")
        return []
    if len(items) > MAX_PAGES:
        read.add("limit-exceeded", f"{len(items)} pages (max {MAX_PAGES})")
        items = items[:MAX_PAGES]  # already rejected; bound the walk
    pages: list[dict] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            read.add("type-mismatch", "pages[] item is not an object")
            continue
        pid, path = item.get("id"), item.get("path")
        # id and path are validated independently: a duplicate is a duplicate
        # in the raw declaration (§9.2), even when the other component would
        # drop the item anyway.
        pid_ok = False
        if not isinstance(pid, str):
            read.add("type-mismatch", f"page id {pid!r} is not a string")
        elif not PAGE_ID_RE.match(pid):
            read.add("invalid-id", f"page id {pid!r}")
        elif pid in seen_ids:
            read.add("duplicate-id", f"page id {pid}")
        else:
            seen_ids.add(pid)
            pid_ok = True
        path_ok = False
        if not isinstance(path, str):
            read.add("type-mismatch", f"page path {path!r} is not a string")
        elif not valid_v2_path(path, html=True):
            read.add("invalid-path", f"page path {path!r}")
        elif path in seen_paths:
            read.add("duplicate-path", f"page path {path}")
        else:
            seen_paths.add(path)
            path_ok = True
        if not (pid_ok and path_ok):
            continue
        title = item.get("title")
        if title is not None and not (isinstance(title, str) and len(title) <= MAX_TITLE_LEN):
            read.add("invalid-value", f"page {pid} title dropped")
            title = None
        pages.append({"id": pid, "path": path, "title": title})
    if not pages and "no-pages" not in read.codes():
        read.add("no-pages", "no valid page survived validation")
    return pages


def _read_questions(read: ManifestRead, raw: dict, page_ids: set[str]) -> list[dict]:
    items = raw.get("questions")
    if items is not None and not isinstance(items, list):
        read.add("type-mismatch", "questions is not a list")
        items = None
    if not items:
        return []
    if len(items) > MAX_QUESTIONS:
        read.add("limit-exceeded", f"{len(items)} questions (max {MAX_QUESTIONS})")
        items = items[:MAX_QUESTIONS]  # already rejected; bound the walk
    questions: list[dict] = []
    seen_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            read.add("type-mismatch", "questions[] item is not an object")
            continue
        qid, page = item.get("id"), item.get("page")
        if not isinstance(qid, str):
            read.add("type-mismatch", f"question id {qid!r} is not a string")
            continue
        if not QUESTION_ID_RE.match(qid):
            read.add("invalid-id", f"question id {qid!r}")
            continue
        if qid in seen_ids:
            read.add("duplicate-id", f"question id {qid}")
            continue
        seen_ids.add(qid)
        if not isinstance(page, str):
            read.add("type-mismatch", f"question {qid} page is not a string")
            continue
        if page not in page_ids:
            read.add("dangling-ref", f"question {qid} references page {page!r}")
            continue
        kind = item.get("kind")
        if kind is not None:
            if not isinstance(kind, str) or not KIND_RE.match(kind):
                read.add("invalid-value", f"question {qid} kind dropped")
                kind = None
            elif kind not in QUESTION_KINDS:  # unknown but grammar-valid (§4.3)
                kind = DEFAULT_QUESTION_KIND
        kind = kind or DEFAULT_QUESTION_KIND
        label = item.get("label")
        if label is not None and not (isinstance(label, str) and len(label) <= MAX_LABEL_LEN):
            read.add("invalid-value", f"question {qid} label dropped")
            label = None
        questions.append({"id": qid, "page": page, "kind": kind, "label": label})
    return questions


def _under_root(file: str, roots: list[str]) -> bool:
    return any(file.startswith(root + "/") for root in roots)


def _read_blocks(
    read: ManifestRead, raw: dict, page_ids: set[str], runner_registry: frozenset[str]
) -> list[dict]:
    items = raw.get("blocks")
    if items is not None and not isinstance(items, list):
        read.add("type-mismatch", "blocks is not a list")
        items = None
    if not items:
        return []
    if len(items) > MAX_BLOCKS:
        read.add("limit-exceeded", f"{len(items)} blocks (max {MAX_BLOCKS})")
        items = items[:MAX_BLOCKS]  # already rejected; bound the walk
    blocks: list[dict] = []
    seen_ids: set[str] = set()
    seen_files: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            read.add("type-mismatch", "blocks[] item is not an object")
            continue
        bid = item.get("id")
        # id and file duplicates are raw-declaration facts (§9.2): they are
        # recorded even when another component drops the block.
        bid_ok = False
        if not isinstance(bid, str):
            read.add("type-mismatch", f"block id {bid!r} is not a string")
        elif not BLOCK_ID_RE.match(bid):
            read.add("invalid-id", f"block id {bid!r}")
        elif bid in seen_ids:
            read.add("duplicate-id", f"block id {bid}")
        else:
            seen_ids.add(bid)
            bid_ok = True
        file = item.get("file")
        file_ok = False
        if not isinstance(file, str):
            read.add("type-mismatch", f"block file {file!r} is not a string")
        elif not valid_v2_path(file):
            read.add("invalid-path", f"block file {file!r}")
        elif file in seen_files:
            read.add("duplicate-path", f"block file {file}")
        else:
            seen_files.add(file)
            file_ok = True
        if not (bid_ok and file_ok):
            continue
        page = item.get("page")
        page_ok = True
        if not isinstance(page, str):
            read.add("type-mismatch", f"block {bid} page is not a string")
            page_ok = False
        elif page not in page_ids:
            read.add("dangling-ref", f"block {bid} references page {page!r}")
            page_ok = False
        kind = item.get("kind")
        kind_ok = isinstance(kind, str) and kind == "editor"  # v2 defines only editor (§4.4)
        if not kind_ok:
            read.add("unknown-kind", f"block {bid} kind {kind!r}")
        # page, kind, and file-vs-roots containment are independent checks
        # (§9.2 aggregation): every violation of the block's declaration is
        # recorded before the drop, so the agent repairs it in one pass.
        in_root = _under_root(file, read.artifact_roots)
        if not in_root:
            read.add("outside-root", f"block {bid} file {file!r} is outside every artifact root")
        if not (page_ok and kind_ok and in_root):
            continue
        language = item.get("language")
        if language is not None and not (isinstance(language, str) and LANGUAGE_RE.match(language)):
            read.add("invalid-value", f"block {bid} language dropped")
            language = None
        runner_id = item.get("runner_id")
        run_enabled = False
        if runner_id is not None:
            if not isinstance(runner_id, str) or not RUNNER_ID_RE.match(runner_id):
                # grammar violation: dropped field, same save-only editor as absent
                read.add("invalid-value", f"block {bid} runner_id dropped")
                runner_id = None
            elif runner_id not in runner_registry:
                read.add("unknown-runner", f"block {bid} runner_id {runner_id!r}")
            else:
                run_enabled = True
        blocks.append({
            "id": bid,
            "page": page,
            "kind": kind,
            "language": language,
            "file": file,
            "runner_id": runner_id,
            "run_enabled": run_enabled,
        })
    return blocks


def _read_artifact_roots(read: ManifestRead, raw: dict) -> list[str]:
    items = raw.get("artifact_roots")
    if items is not None and not isinstance(items, list):
        read.add("type-mismatch", "artifact_roots is not a list")
        items = None
    if items is None:
        return [DEFAULT_ARTIFACT_ROOT]  # absent ⇒ default, no finding (§7)
    if len(items) > MAX_ARTIFACT_ROOTS:
        read.add("limit-exceeded", f"{len(items)} artifact roots (max {MAX_ARTIFACT_ROOTS})")
        items = items[:MAX_ARTIFACT_ROOTS]  # already rejected; bound the walk
    valid: list[str] = []
    for item in items:
        if not isinstance(item, str):
            read.add("type-mismatch", "artifact_roots[] item is not a string")
            continue
        if not valid_v2_path(item):
            read.add("invalid-path", f"artifact root {item!r}")
            continue
        if item == "assets" or item.startswith("assets/"):
            # learner-work roots may not intrude into the presentation area
            # pages reference (§2/§7) — otherwise the preview file surface
            # and artifact discovery would claim the same files
            read.add("overlapping-roots",
                     f"artifact root {item!r} intrudes into the assets preview area")
            continue
        valid.append(item)
    # `attempts` is injected BEFORE the overlap pass, so a declared root
    # nested under it (e.g. "attempts/deep" without "attempts") is dropped
    # as overlapping rather than surviving next to the injected mandatory
    # root — the final set is always disjoint.
    if DEFAULT_ARTIFACT_ROOT not in valid:
        read.add("missing-attempts-root", "attempts injected into the read model")
        valid.append(DEFAULT_ARTIFACT_ROOT)
    roots: list[str] = []
    for index, root in enumerate(valid):
        others = valid[:index] + valid[index + 1:]
        if root in roots:  # exact duplicate: first occurrence already kept
            read.add("overlapping-roots", f"artifact root {root!r} repeated")
            continue
        if any(root.startswith(other + "/") for other in others):
            # segment-wise nested under another root: drop the nested one
            read.add("overlapping-roots", f"artifact root {root!r} nests under another root")
            continue
        roots.append(root)
    return roots


def _read_opaque_refs(read: ManifestRead, raw: dict) -> None:
    path_ref = raw.get("path")
    if path_ref is not None:
        if not isinstance(path_ref, str):
            read.add("type-mismatch", "path is not a string")
            path_ref = None
        elif not valid_opaque_ref(path_ref):
            read.add("invalid-ref", "path ref dropped")
            path_ref = None
    read.path_ref = path_ref

    step = raw.get("step")
    if step is not None:
        if read.path_ref is None:
            read.add("invalid-ref", "step without a path is dropped")
        elif not _is_int(step) or not MIN_STEP <= step <= MAX_STEP:
            read.add("invalid-ref", f"step {step!r} dropped")
        else:
            read.step = step

    concepts = raw.get("concepts")
    if concepts is not None and not isinstance(concepts, list):
        read.add("type-mismatch", "concepts is not a list")
        concepts = None
    if concepts:
        if len(concepts) > MAX_CONCEPTS:
            read.add("limit-exceeded", f"{len(concepts)} concepts (max {MAX_CONCEPTS})")
            concepts = concepts[:MAX_CONCEPTS]  # already rejected; bound the walk
        for concept in concepts:
            if not valid_opaque_ref(concept):
                read.add("invalid-ref", f"concept {concept!r} dropped")
            elif concept in read.concepts:
                read.add("duplicate-concept", f"concept {concept!r} deduplicated")
            else:
                read.concepts.append(concept)


# --- canonical serialization (§9.3) ------------------------------------------

_TOP_LEVEL_ORDER = (
    "schema_version",
    "lesson_uid",
    "slug",
    "title",
    "source_url",
    "entry",
    "pages",
    "questions",
    "blocks",
    "path",
    "step",
    "concepts",
    "runtime",
    "artifact_roots",
    "updated_by_agent_at",
)
_ITEM_ORDER = {
    "pages": ("id", "path", "title"),
    "questions": ("id", "page", "kind", "label"),
    "blocks": ("id", "page", "kind", "language", "file", "runner_id"),
    "runtime": ("profile",),
}


def _ordered(obj: dict, known: tuple[str, ...]) -> dict:
    """Known keys first in schema order, then unknown keys in their original
    relative order (§9.3) — values untouched."""
    out = {key: obj[key] for key in known if key in obj}
    out.update((key, value) for key, value in obj.items() if key not in known)
    return out


def canonical_manifest(raw: dict) -> dict:
    """Reordered copy of a v2 manifest object in canonical key order.
    Unknown fields are preserved semantically; item lists keep their order."""
    out = _ordered(raw, _TOP_LEVEL_ORDER)
    for name, item_order in _ITEM_ORDER.items():
        value = out.get(name)
        if name == "runtime":
            if isinstance(value, dict):
                out[name] = _ordered(value, item_order)
        elif isinstance(value, list):
            out[name] = [
                _ordered(item, item_order) if isinstance(item, dict) else item
                for item in value
            ]
    return out


def canonical_dumps(raw: dict, version: int = SCHEMA_V2) -> str:
    """Exactly the §9.3 canonical serialization. v1 manifests are never
    reordered (they are never rewritten at all — this form exists only so
    verify can assert fixtures round-trip byte-identically)."""
    obj = canonical_manifest(raw) if version != SCHEMA_V1 else raw
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


# --- creation skeleton (§5) and atomic writes --------------------------------


def mint_page_id() -> str:
    return "pg_" + uuid4().hex[:16]


def default_manifest_v2(
    *, lesson_uid: str, slug: str, title: str, source_url: str | None = None
) -> dict:
    """The v2 creation skeleton: absent optionals are omitted, never null."""
    manifest: dict = {
        "schema_version": SCHEMA_V2,
        "lesson_uid": lesson_uid,
        "slug": slug,
        "title": title,
    }
    if source_url:
        manifest["source_url"] = source_url
    manifest.update({
        "entry": DEFAULT_ENTRY,
        "pages": [{"id": mint_page_id(), "path": DEFAULT_ENTRY}],
        "runtime": {"profile": PROFILE_INTERACTIVE},
        "artifact_roots": [DEFAULT_ARTIFACT_ROOT],
    })
    return manifest


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace a generated file (the B1 brief-writer idiom): write
    and fsync a mode-0600 temporary file in the destination directory, then
    replace the destination entry without ever opening it — a pre-planted
    link or special file is replaced, not followed."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".manifest-")
    try:
        try:
            fh = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        with fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_manifest(path: Path, raw: dict, version: int = SCHEMA_V2) -> None:
    atomic_write_text(path, canonical_dumps(raw, version))


# --- filesystem symlink policy (§2) -------------------------------------------


def path_has_symlink(base: Path, rel: str) -> bool:
    """Per-segment no-follow check: True when base itself or any component of
    base/rel is a symlink (lstat per component — never resolves). A missing
    component is not a symlink; the path is then simply missing."""
    ref = PurePosixPath(rel)
    if ref.is_absolute() or ".." in ref.parts:
        return True  # backstop: callers pre-clean; never walk outside base
    current = base
    try:
        if current.is_symlink():
            return True
        for segment in ref.parts:
            current = current / segment
            if current.is_symlink():
                return True
    except OSError:
        return True  # unreadable component: treat as unsafe, i.e. missing
    return False
