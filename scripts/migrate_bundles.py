"""v1 → v2 lesson-bundle migration (learn-bundle-spec.md §10).

Migrates every v1 `lesson.json` under the lessons root to schema v2 using the
normalized v1 read model as the structural input and the raw parsed object for
unknown-field preservation and the §10 stop conditions. The tool never mints
identity: `lesson_uid` is copied from SQLite `lessons.uid` (backfilled in C3),
so a bundle without a DB row is reported and left untouched.

Guarantees (§10 invariants + C4 requirements):
- HTML page bytes are never touched; declared pages are hashed before the
  write and re-hashed after it (post-verification).
- The DB `current_entry` selection is never written; a valid selection absent
  from the v1 page list is folded into the head of `pages` instead.
- Page ids are minted deterministically ("pg_" + sha256(lesson_uid + "\\n" +
  path) first 16 hex) — reproducible across dry-run, run, and re-verification.
- Stop-before-write: a grammar/§4-limit violation, an unknown v1 key colliding
  with a v2-owned key, or an object-form page item carrying `id`/`title`
  leaves the manifest untouched and fails the run visibly.
- Idempotent: a v2 manifest is a no-op, so a rerun reports no changes.
- Atomic replacement (the B1 writer idiom via `bundle_schema.atomic_write_text`)
  and a rollback manifest written before any bundle is mutated.

Usage:
    ACTIVITY_DATA_DIR=... python -m scripts.migrate_bundles --dry-run
    ACTIVITY_DATA_DIR=... python -m scripts.migrate_bundles
    ACTIVITY_DATA_DIR=... python -m scripts.migrate_bundles --slug thank-go-1-2
    ACTIVITY_DATA_DIR=... python -m scripts.migrate_bundles \
        --rollback data/migrations/v1v2-20260719-120000

Rollback restores the recorded pre-migration manifest bytes only while the
on-disk manifest still hashes to the migrated output; a manifest edited since
migration (e.g. by the study agent) is refused, never overwritten.

A run is per-bundle, not all-or-nothing: every migratable bundle is applied
even when others stop, and the exit code still reports the stops — dry-run
first, then rerun after resolving what stopped.

Exit code 0 = nothing stopped or failed; 1 otherwise. Writes land in
data/migrations/ (inside the gitignored data/ area, outside every bundle).
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import sqlite3
import stat as stat_module
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import DATA_DIR, DB_PATH, now_iso, now_stamp  # noqa: E402
from app.services import bundle_schema  # noqa: E402
from app.services.lessons import LESSONS_DIR, MANIFEST_NAME  # noqa: E402

MIGRATIONS_DIR = DATA_DIR / "migrations"
ROLLBACK_MANIFEST = "rollback.json"

# lessons.uid exists from schema v11 on (the C3 step after the v10→v11
# renumbering); an older DB has no identity to copy, so the tool refuses.
MIN_SCHEMA_VERSION = 11

ACTION_MIGRATE = "migrate"
ACTION_NOOP = "already-v2"
ACTION_SKIP = "skip"
ACTION_STOP = "stop"

# The v1 grammar (§9.1): everything else on a v1 manifest is an unknown field.
V1_KNOWN_KEYS = frozenset({
    "schema_version", "slug", "title", "source_url", "entry", "related",
    "updated_by_agent_at",
})
# §10: an unknown v1 key colliding with a v2-owned key has no lossless home.
V2_OWNED_KEYS = frozenset({
    "lesson_uid", "pages", "questions", "blocks", "path", "step", "concepts",
    "runtime", "artifact_roots",
})
# §10: object-form entry/related members that would collide with the v2 page
# object (`path` itself is the consumed member, so it cannot collide).
PAGE_OBJECT_COLLISIONS = ("id", "title")


def deterministic_page_id(lesson_uid: str, path: str) -> str:
    digest = hashlib.sha256(f"{lesson_uid}\n{path}".encode("utf-8")).hexdigest()
    return "pg_" + digest[:16]


@dataclass
class BundlePlan:
    """One bundle's migration decision plus everything apply/verify needs."""

    slug: str
    action: str
    reasons: list[str] = field(default_factory=list)  # why stopped/skipped
    notes: list[str] = field(default_factory=list)    # informational
    old_bytes: bytes | None = None
    new_text: str | None = None
    page_hashes: dict[str, str] = field(default_factory=dict)


def _read_bytes_no_follow(path: Path) -> tuple[bytes | None, str | None]:
    """Manifest bytes with `read_manifest_path` semantics: never through a
    symlink, regular files only, size-capped. (bytes, None) on success,
    (None, None) when genuinely missing, (None, reason) otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        if path.is_symlink():
            return None, "lesson.json is a dangling symlink"
        return None, None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None, "lesson.json is a symlink"
        return None, exc.strerror or "unreadable"
    try:
        st = os.fstat(fd)
        if not stat_module.S_ISREG(st.st_mode):
            return None, "lesson.json is not a regular file"
        if st.st_size > bundle_schema.MAX_MANIFEST_BYTES:
            return None, f"manifest too large ({st.st_size} bytes)"
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            return fh.read(bundle_schema.MAX_MANIFEST_BYTES + 1), None
    except OSError as exc:
        return None, exc.strerror or "unreadable"
    finally:
        if fd >= 0:
            os.close(fd)


def _hash_file_no_follow(path: Path) -> str | None:
    """Streamed sha256 from one no-follow regular-file descriptor: no whole-file
    allocation, no blocking on a planted special file, and no gap between the
    existence check and the read — the descriptor is the only source."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        if not stat_module.S_ISREG(os.fstat(fd).st_mode):
            return None
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)
    except OSError:
        return None
    finally:
        os.close(fd)


def _hash_pages(bundle_dir: Path, paths: list[str], notes: list[str]) -> dict[str, str]:
    """sha256 of every declared page that exists as a plain file. A missing,
    symlinked, or non-regular page is noted, not fatal — the invariant under
    test is that the tool changes no page bytes, not that every page exists."""
    hashes: dict[str, str] = {}
    for rel in paths:
        if bundle_schema.path_has_symlink(bundle_dir, rel):
            notes.append(f"page {rel!r} resolves through a symlink; not hashed")
            continue
        digest = _hash_file_no_follow(bundle_dir / rel)
        if digest is None:
            notes.append(f"page {rel!r} is missing or not a regular file")
            continue
        hashes[rel] = digest
    return hashes


def _bundle_dir_safe(bundle_dir: Path) -> bool:
    """The runtime's containment posture (lessons.py `_bundle_dir_is_safe`):
    the bundle path must be a real directory whose resolved parent is its
    literal parent — never a symlink and never a traversal out of the root."""
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        return False
    try:
        return bundle_dir.resolve(strict=True).parent == bundle_dir.parent.resolve()
    except OSError:
        return False


def _valid_slug(slug: object) -> bool:
    return (
        isinstance(slug, str)
        and len(slug) <= bundle_schema.MAX_SLUG_LEN
        and bundle_schema.SLUG_RE.match(slug) is not None
    )


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_bytes_durable(path: Path, data: bytes) -> None:
    """Rollback material must survive a crash that happens after the manifest
    replacement: 0600 tempfile, file fsync, atomic replace, directory fsync."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".rollback-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


def _check_item_collisions(label: str, item: dict, stops: list[str]) -> None:
    """§10: an object-form page item carrying `id`/`title` has no lossless v2
    mapping. Like the C3 duplicate rules, this is a raw-declaration fact — it
    stops the migration even when the v1 read model drops the item, because a
    rewrite would silently discard the colliding member."""
    for key in PAGE_OBJECT_COLLISIONS:
        if key in item:
            stops.append(
                f"object-form {label} carries {key!r}, colliding with the v2 page object"
            )


def _collect_page_extras(
    raw: dict, entry: str, stops: list[str]
) -> tuple[list[str], dict[str, dict]]:
    """Mirror the normative v1 related-walk (§9.2 `_read_v1`) while keeping the
    association from each surviving path to its raw object-form item, so
    unknown members can be copied verbatim onto the generated page object."""
    extras: dict[str, dict] = {}

    raw_entry = raw.get("entry")
    if isinstance(raw_entry, dict):
        _check_item_collisions("entry", raw_entry, stops)
        # Extras ride only a page the item actually generated (§10): when the
        # object's own path was dropped and entry fell back, nothing is copied.
        cleaned = bundle_schema.clean_v1_ref(raw_entry.get("path"), html_only=True)
        if cleaned == entry:
            entry_extras = {k: v for k, v in raw_entry.items() if k != "path"}
            if entry_extras:
                extras[entry] = entry_extras

    related = raw.get("related")
    if not isinstance(related, list):
        related = []
    seen: list[str] = []
    for item in related:
        candidate = item.get("path") if isinstance(item, dict) else item
        if isinstance(item, dict):
            _check_item_collisions(f"related item {candidate!r}", item, stops)
        ref = bundle_schema.clean_v1_ref(candidate, html_only=True)
        if ref is None or ref == entry or ref in seen:
            continue  # dropped/deduplicated by the v1 read model; surfaced as findings
        seen.append(ref)
        if isinstance(item, dict):
            item_extras = {k: v for k, v in item.items() if k != "path"}
            if item_extras:
                extras[ref] = item_extras
    return seen, extras


def _build_v2(
    raw: dict,
    read: bundle_schema.ManifestRead,
    db_lesson: dict,
    stops: list[str],
    notes: list[str],
) -> dict | None:
    """The §10 mapping table. Returns the v2 object, or None with `stops`
    explaining why the manifest must be left untouched."""
    for key in raw:
        if key not in V1_KNOWN_KEYS and key in V2_OWNED_KEYS:
            stops.append(f"unknown v1 key {key!r} collides with a v2-owned key")

    entry = read.entry
    related, extras = _collect_page_extras(raw, entry, stops)
    normalized = [entry, *related]
    if normalized != read.page_paths():
        # The association walk must reproduce the normative read model exactly;
        # a divergence means this tool is wrong, and writing would be unsafe.
        stops.append("internal error: related-walk diverged from the v1 read model")
        return None

    # §10: a valid DB current_entry absent from the list is folded in at the
    # head — today's v1 display-injection position — with `entry` unchanged.
    current = db_lesson.get("current_entry")
    if current:
        cleaned = bundle_schema.clean_v1_ref(current, html_only=True)
        if cleaned and bundle_schema.valid_v2_path(cleaned, html=True):
            if cleaned not in normalized:
                normalized.insert(0, cleaned)
                notes.append(f"DB current_entry {cleaned!r} folded in at the head")
            if cleaned != current:
                notes.append(
                    f"DB current_entry {current!r} only matches after cleaning; "
                    "the stored selection will read as stale under v2 exact compare"
                )
        else:
            notes.append(f"DB current_entry {current!r} is not a valid v2 page; not folded in")

    if len(normalized) > bundle_schema.MAX_PAGES:
        stops.append(f"{len(normalized)} pages exceed the §4 limit ({bundle_schema.MAX_PAGES})")
    for path in normalized:
        if not bundle_schema.valid_v2_path(path, html=True):
            stops.append(f"normalized page path {path!r} still violates the v2 grammar")

    if stops:
        return None

    uid = db_lesson["uid"]
    out: dict = {"schema_version": bundle_schema.SCHEMA_V2, "lesson_uid": uid}
    for name in ("slug", "title"):
        if name in raw:
            out[name] = raw[name]
    if raw.get("source_url") is not None:
        out["source_url"] = raw["source_url"]  # a null copy is omitted (§10)
    out["entry"] = entry
    out["pages"] = [
        {"id": deterministic_page_id(uid, path), "path": path, **extras.get(path, {})}
        for path in normalized
    ]
    out["runtime"] = {"profile": bundle_schema.PROFILE_LEGACY}
    out["artifact_roots"] = [bundle_schema.DEFAULT_ARTIFACT_ROOT]
    if raw.get("updated_by_agent_at") is not None:
        # preserved verbatim; a malformed value stays and reads as absent (§10)
        out["updated_by_agent_at"] = raw["updated_by_agent_at"]
    for key, value in raw.items():
        if key not in V1_KNOWN_KEYS:
            out[key] = value  # unknown v1 fields, original relative order (§9.3)
    return out


def plan_bundle(bundle_dir: Path, db_lesson: dict) -> BundlePlan:
    """Decide one bundle's migration without touching anything."""
    slug = db_lesson.get("slug") or bundle_dir.name
    plan = BundlePlan(slug=slug, action=ACTION_STOP)

    if not bundle_dir.is_symlink() and not bundle_dir.exists():
        plan.action = ACTION_SKIP
        plan.reasons.append("no bundle directory")
        return plan
    if not _bundle_dir_safe(bundle_dir):
        plan.reasons.append("bundle dir is not a real directory under the lessons root")
        return plan

    data, err = _read_bytes_no_follow(bundle_dir / MANIFEST_NAME)
    if err is not None:
        plan.reasons.append(err)
        return plan
    if data is None:
        plan.action = ACTION_SKIP
        plan.reasons.append("no manifest")
        return plan
    plan.old_bytes = data

    read = bundle_schema.read_manifest_bytes(data, db_lesson=db_lesson)
    if read.rejected:
        plan.reasons.append(
            "manifest rejected: " + ", ".join(sorted(read.codes()))
        )
        return plan
    if read.version == bundle_schema.SCHEMA_V2:
        plan.action = ACTION_NOOP
        return plan

    uid = db_lesson.get("uid")
    if not uid or not bundle_schema.UUID_RE.match(uid):
        plan.reasons.append("no DB lesson uid to copy (the tool never mints identity)")
        return plan

    for finding in read.findings:
        plan.notes.append(f"v1 read: {finding.code} — {finding.detail}")

    stops: list[str] = []
    out = _build_v2(read.raw, read, db_lesson, stops, plan.notes)
    if out is None:
        plan.reasons.extend(stops)
        return plan

    new_text = bundle_schema.canonical_dumps(out)
    # Backstop pre-validation: the migrated manifest must read back clean
    # (informational findings like stale-metadata copies are still `ok`).
    check = bundle_schema.read_manifest_text(new_text, db_lesson=db_lesson)
    if check.outcome != bundle_schema.OK:
        plan.reasons.append(
            f"migrated manifest would read {check.outcome}: "
            + ", ".join(sorted(check.codes()))
        )
        return plan
    if check.entry != read.entry:
        plan.reasons.append("internal error: migration changed the entry selection")
        return plan

    plan.action = ACTION_MIGRATE
    plan.new_text = new_text
    plan.page_hashes = _hash_pages(bundle_dir, [p["path"] for p in out["pages"]], plan.notes)
    return plan


def apply_plan(
    bundle_dir: Path, plan: BundlePlan, db_lesson: dict, rollback_dir: Path
) -> list[str]:
    """Write one planned migration: rollback record first, then the atomic
    manifest replacement, then hash post-verification. Returns errors."""
    if plan.action != ACTION_MIGRATE or not plan.new_text or plan.old_bytes is None:
        raise ValueError("apply_plan needs a planned migration")
    errors: list[str] = []
    manifest_path = bundle_dir / MANIFEST_NAME

    # The containment boundary holds at write time, not only at plan time:
    # the bundle must still be a real direct child of its literal parent.
    if not _bundle_dir_safe(bundle_dir):
        return ["refused: bundle dir is not a real directory under the lessons root"]

    # Stale-plan guard: run() plans every bundle before the first write, so by
    # the time a later lesson is reached its manifest may have been edited
    # (app or study agent). Refuse rather than overwrite bytes the rollback
    # copy does not contain — the caller re-plans and reruns.
    current, err = _read_bytes_no_follow(manifest_path)
    if current != plan.old_bytes:
        return [
            "refused: manifest changed since planning"
            + (f" ({err})" if err else "")
            + "; re-plan and rerun"
        ]

    # Durable rollback data BEFORE any mutation: the original bytes as an
    # fsynced file, then the ledger entry (rewritten per bundle and fsynced
    # with its directory) — a crash at any later point, power loss included,
    # leaves every already-migrated bundle restorable.
    _write_bytes_durable(rollback_dir / f"{plan.slug}.lesson.json", plan.old_bytes)
    ledger_path = rollback_dir / ROLLBACK_MANIFEST
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["entries"] = [e for e in ledger["entries"] if e["slug"] != plan.slug]
    ledger["entries"].append({
        "slug": plan.slug,
        "file": f"{plan.slug}.lesson.json",
        "old_sha256": hashlib.sha256(plan.old_bytes).hexdigest(),
        "new_sha256": hashlib.sha256(plan.new_text.encode("utf-8")).hexdigest(),
    })
    bundle_schema.atomic_write_text(ledger_path, json.dumps(ledger, indent=2) + "\n")
    _fsync_dir(rollback_dir)

    bundle_schema.atomic_write_text(manifest_path, plan.new_text)
    _fsync_dir(bundle_dir)

    # Post-verification by hashes: the manifest on disk is exactly the planned
    # bytes, it reads back as clean v2 against the DB row, and no declared
    # page's bytes moved.
    data, err = _read_bytes_no_follow(manifest_path)
    if data != plan.new_text.encode("utf-8"):
        errors.append(f"post-verify: manifest bytes differ ({err or 'content mismatch'})")
    else:
        reread = bundle_schema.read_manifest_bytes(data, db_lesson=db_lesson)
        if reread.version != bundle_schema.SCHEMA_V2 or reread.outcome != bundle_schema.OK:
            errors.append(
                f"post-verify: migrated manifest reads {reread.outcome}: "
                + ", ".join(sorted(reread.codes()))
            )
    after_notes: list[str] = []
    after = _hash_pages(bundle_dir, list(plan.page_hashes), after_notes)
    if after != plan.page_hashes:
        errors.append("post-verify: page bytes changed during migration")
    errors.extend(f"post-verify: {n}" for n in after_notes)
    return errors


def rollback(rollback_dir: Path) -> int:
    """Restore recorded pre-migration manifests. A bundle whose manifest no
    longer matches the migrated bytes is refused, never overwritten. The
    ledger and the copies are data, not authority: every shape is validated,
    every path is derived from the validated slug, and every read goes
    through the no-follow regular-file boundary."""
    ledger = json.loads((rollback_dir / ROLLBACK_MANIFEST).read_text(encoding="utf-8"))
    entries = ledger.get("entries") if isinstance(ledger, dict) else None
    if not isinstance(entries, list):
        raise SystemExit(f"malformed rollback ledger in {rollback_dir}")
    failures = 0
    for entry in entries:
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(k), str)
            for k in ("slug", "old_sha256", "new_sha256")
        ):
            print(f"[refused] malformed ledger entry {entry!r}")
            failures += 1
            continue
        slug = entry["slug"]
        if not _valid_slug(slug):
            print(f"[refused] {slug!r} — ledger slug violates the slug grammar")
            failures += 1
            continue
        bundle_dir = LESSONS_DIR / slug
        if not _bundle_dir_safe(bundle_dir):
            print(f"[refused] {slug} — bundle dir is not a real directory "
                  "under the lessons root")
            failures += 1
            continue
        manifest_path = bundle_dir / MANIFEST_NAME
        # The copy path is derived from the validated slug, like the write
        # path — the ledger's `file` value is a record, never a path input.
        old_bytes, copy_err = _read_bytes_no_follow(
            rollback_dir / f"{slug}.lesson.json")
        if old_bytes is None:
            print(f"[refused] {slug} — rollback copy is "
                  f"{copy_err or 'missing'}")
            failures += 1
            continue
        if hashlib.sha256(old_bytes).hexdigest() != entry["old_sha256"]:
            print(f"[refused] {slug} — rollback copy does not match its recorded hash")
            failures += 1
            continue
        data, err = _read_bytes_no_follow(manifest_path)
        current = hashlib.sha256(data).hexdigest() if data is not None else None
        if current == entry["old_sha256"]:
            print(f"[ok] {slug} — already at the pre-migration bytes")
            continue
        if current != entry["new_sha256"]:
            print(f"[refused] {slug} — manifest changed since migration ({err or 'edited'})")
            failures += 1
            continue
        bundle_schema.atomic_write_text(
            manifest_path, old_bytes.decode("utf-8")
        )
        print(f"[restored] {slug}")
    return 1 if failures else 0


def _load_lessons(slugs: list[str] | None) -> list[dict]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < MIN_SCHEMA_VERSION:
            raise SystemExit(
                f"DB schema v{version} predates lessons.uid (v{MIN_SCHEMA_VERSION}); "
                "start the app once to migrate the DB first"
            )
        rows = [dict(r) for r in conn.execute("SELECT * FROM lessons ORDER BY id")]
    if slugs:
        by_slug = {r["slug"]: r for r in rows}
        missing = [s for s in slugs if s not in by_slug]
        if missing:
            raise SystemExit(f"unknown lesson slug(s): {', '.join(missing)}")
        rows = [by_slug[s] for s in slugs]
    return rows


def run(*, dry_run: bool, slugs: list[str] | None) -> int:
    if not DB_PATH.exists():
        raise SystemExit(f"no database at {DB_PATH}")
    lessons = _load_lessons(slugs)

    plans: list[tuple[dict, BundlePlan]] = []
    for lesson in lessons:
        # A DB slug is data: the runtime's slug grammar gates every join, so
        # a corrupt/imported row can never turn into a filesystem path.
        if not _valid_slug(lesson["slug"]):
            bad = BundlePlan(slug=repr(lesson["slug"]), action=ACTION_STOP)
            bad.reasons.append("DB slug violates the slug grammar")
            plans.append((lesson, bad))
            continue
        bundle_dir = LESSONS_DIR / lesson["slug"]
        plans.append((lesson, plan_bundle(bundle_dir, lesson)))

    known_slugs = {lesson["slug"] for lesson in lessons}
    unmanaged = sorted(
        p.name for p in LESSONS_DIR.iterdir()
        if p.is_dir() and not p.is_symlink() and p.name not in known_slugs
    ) if LESSONS_DIR.is_dir() and slugs is None else []

    rollback_dir: Path | None = None
    to_apply = [pair for pair in plans if pair[1].action == ACTION_MIGRATE]
    if to_apply and not dry_run:
        base = MIGRATIONS_DIR / f"v1v2-{now_stamp()}"
        rollback_dir = base
        suffix = 2
        while True:  # now_stamp is second-granular; two runs may share it
            try:
                rollback_dir.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                rollback_dir = base.with_name(f"{base.name}-{suffix}")
                suffix += 1
        bundle_schema.atomic_write_text(
            rollback_dir / ROLLBACK_MANIFEST,
            json.dumps({"created_at": now_iso(), "entries": []}, indent=2) + "\n",
        )

    failed = 0
    for lesson, plan in plans:
        label = f"[{plan.action}] {plan.slug}"
        detail = "; ".join(plan.reasons) if plan.reasons else ""
        if plan.action == ACTION_MIGRATE:
            pages = len(json.loads(plan.new_text)["pages"])
            detail = f"{pages} pages, {len(plan.page_hashes)} page files hashed"
        print(label + (f" — {detail}" if detail else ""))
        for note in plan.notes:
            print(f"    note: {note}")
        if plan.action == ACTION_STOP:
            failed += 1
            continue
        if plan.action == ACTION_MIGRATE and not dry_run:
            errors = apply_plan(LESSONS_DIR / plan.slug, plan, lesson, rollback_dir)
            for error in errors:
                print(f"    ERROR: {error}")
            if errors:
                failed += 1

    for name in unmanaged:
        print(f"[skip] {name} — bundle dir without a DB lesson row (no identity to copy)")

    counts: dict[str, int] = {}
    for _, plan in plans:
        counts[plan.action] = counts.get(plan.action, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    mode = "dry-run" if dry_run else "run"
    print(f"{mode}: {summary or 'no lessons'}"
          + (f"; rollback dir {rollback_dir}" if rollback_dir else ""))
    return 1 if failed else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Migrate v1 lesson bundles to schema v2 (§10).")
    ap.add_argument("--dry-run", action="store_true", help="plan and report; write nothing")
    ap.add_argument("--slug", action="append", dest="slugs", metavar="SLUG",
                    help="migrate only this lesson (repeatable)")
    ap.add_argument("--rollback", type=Path, metavar="DIR",
                    help="restore manifests recorded in DIR instead of migrating")
    args = ap.parse_args()
    if args.rollback:
        if args.dry_run or args.slugs:
            ap.error("--rollback cannot be combined with --dry-run/--slug")
        raise SystemExit(rollback(args.rollback))
    raise SystemExit(run(dry_run=args.dry_run, slugs=args.slugs))


if __name__ == "__main__":
    main()
