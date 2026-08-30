"""Learn routes: the /learn view, preview and bundle files, artifacts, runs,
attempts, assessments, the build step and the lesson CRUD writes."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse,
)
from starlette.concurrency import run_in_threadpool

from ..db import get_conn, get_db, pretty_date
from ..request_body import PayloadTooLarge, read_capped
from .. import runner as runner_core
from ..security import browser_origin_rejection
from ..services import (
    artifacts, assessments, attempts, bundle_schema, lesson_build, lessons, runs,
)
from ..templating import _safe_return, _with_flash, templates

router = APIRouter()


@router.get("/learn")
def get_learn(
    request: Request,
    status: str | None = None,
    archived: int = 0,
    lesson: int | None = None,
    entry: str | None = None,
    flash: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    show_archived = bool(archived)
    try:
        rows = lessons.list_lessons(conn, status=status, archived_only=show_archived)
    except lessons.LessonError:
        status = None
        rows = lessons.list_lessons(conn, archived_only=show_archived)
    counts = lessons.counts(conn)
    selected = None
    selected_entry = None
    if lesson is not None:
        selected = next((row for row in rows if row["id"] == lesson), None)
        if selected is not None:
            selected_entry = entry
    if selected is None and rows:
        selected = rows[0]
    if selected:
        # DB state first, then the single manifest read that bundle metadata,
        # selection persistence and the record all use, so a newly committed
        # attempt is never classified against an older declaration set.
        record_db_state = lessons.record_panel_db_state(conn, selected["id"])
        selected, selected_manifest = lessons.with_bundle_info_read(
            selected, entry=selected_entry
        )
        # A rejected manifest has no selectable entry; a stale v2 selection
        # (§4.2) keeps its stored candidate too, because persisting the
        # fallback would make the next read report `ok` and erase the finding.
        if (selected["entry"]
                and not selected["bundle"]["stale_selection"]):
            lessons.mark_opened(conn, selected["id"], selected["entry"])
        selected["record"] = _record_panel(
            conn, selected, manifest_read=selected_manifest,
            db_state=record_db_state,
        )
    selected_id = selected["id"] if selected else None
    for row in rows:
        row["selected"] = row["id"] == selected_id
        row["href"] = _learn_url(status=status, archived=show_archived, lesson_id=row["id"])
    if selected:
        selected["file_url"] = _lesson_preview_url(selected["id"], selected["entry"])
        selected["preview_url"] = _lesson_preview_url(
            selected["id"],
            selected["entry"],
            exists=selected["file"]["exists"],
        )
        # The metadata poll must carry the ORIGINAL stale candidate when the
        # render fell back (§4.2): polling the fallback entry would answer
        # `ok` and clear the finding the page just surfaced.
        selected["preview_meta_url"] = _lesson_preview_url(
            selected["id"],
            selected["bundle"]["stale_selection"] or selected["entry"],
            meta=True,
        )
        selected["artifacts_url"] = f"/learn/lessons/{selected['id']}/blocks"
        selected["runs_url"] = f"/learn/lessons/{selected['id']}/blocks"
        selected["record_counts_url"] = f"/learn/lessons/{selected['id']}/record-counts"
        # None when no agent CLI is installed: the template then renders no
        # "Review my answers" button rather than one that cannot run.
        selected["tutor_command"] = lessons.tutor_launch_command()
        selected["sandbox"] = _preview_sandbox(selected["file"]["profile"])
        selected["record_snapshot"] = json.dumps(
            _record_snapshot(
                selected["record"], selected["file"]["bridge_page"]
            ),
            separators=(",", ":"),
        )
        for page in selected["pages"]:
            page["href"] = _learn_url(
                status=status,
                archived=show_archived,
                lesson_id=selected["id"],
                entry=page["entry"],
            )
    self_url = _learn_url(
        status=status,
        archived=show_archived,
        lesson_id=selected_id,
        entry=selected["entry"] if selected else None,
    )
    # Track progress counts the whole active list, never the filtered `rows`:
    # "N of M studied" must not move when a status pill is clicked. The
    # next-step link leaves the filter behind for the same reason.
    track_rows = (
        rows if status is None and not show_archived
        else lessons.list_lessons(conn)
    )
    tracks = lessons.track_progress(
        track_rows,
        reads={selected["id"]: selected_manifest} if selected else None,
    )
    for track in tracks:
        if track["next"]:
            track["next"]["href"] = _learn_url(lesson_id=track["next"]["id"])
    groups, ungrouped = _lesson_groups(rows, tracks, selected)
    return templates.TemplateResponse(request, "learn.html", {
        "request": request,
        "rail": "learn",
        "rows": rows,
        "status_filter": status,
        "show_archived": show_archived,
        "counts": counts,
        "groups": groups,
        "ungrouped": ungrouped,
        "status_tabs": [{"key": key, "label": lessons.STATUS_LABELS[key]} for key in lessons.STATUSES],
        "selected": selected,
        "self_url": self_url,
        "flash": flash,
    })


def _lesson_groups(
    rows: list[dict],
    tracks: list[dict],
    selected: dict | None,
) -> tuple[list[dict], list[dict]]:
    """Split the rendered list into a tree of collapsible blocks, plus the rest.

    Presentational only: `rows` is whatever the filter chose, and grouping
    neither adds a row nor removes one. Groups nest by the `path` address
    (§4.5); a branch the filter emptied is dropped. The header keeps the
    whole-track numbers while the rows inside are the filtered ones.
    """
    shown = {row["id"]: row for row in rows}
    selected_id = selected["id"] if selected else None
    grouped: set[int] = set()
    for track in tracks:
        grouped.update(track["ids"])

    def build(node: dict) -> dict | None:
        children = [built for built in map(build, node["children"]) if built]
        members = [
            shown[lesson_id] for lesson_id in node["rows_ids"] if lesson_id in shown
        ]
        if not members and not children:
            return None
        return {
            **node,
            "children": children,
            "rows": members,
            # Rounds toward 0 so a bar only fills when the step is done.
            "pct": node["studied"] * 100 // node["total"],
            # The server's default for "open"; still true after the stored
            # preference overrides it, so a folded group can say it holds the
            # current lesson.
            "selected": selected_id in node["ids"],
        }

    groups = [
        built for built in map(build, lessons.path_tree(tracks)) if built
    ]
    return groups, [row for row in rows if row["id"] not in grouped]


def _learn_url(
    *,
    status: str | None = None,
    archived: bool = False,
    lesson_id: int | None = None,
    entry: str | None = None,
) -> str:
    query: list[tuple[str, str]] = []
    if status:
        query.append(("status", status))
    if archived:
        query.append(("archived", "1"))
    if lesson_id is not None:
        query.append(("lesson", str(lesson_id)))
    if entry:
        query.append(("entry", entry))
    return "/learn" + (f"?{urlencode(query)}" if query else "")


# --- the record panel -------------------------------------------------------
#
# A pure read: the manifest comes from the READONLY reader, so rendering never
# creates bundle state, and the assessment side is the active fold, so a
# retracted or superseded record never reaches the panel. Every agent- and
# learner-authored string reaches the template as plain text Jinja escapes.

# Chip order: the actionable state first. Unknown levels cannot occur
# (CHECK-enforced) but sort last rather than crashing the page.
_EVIDENCE_ORDER = {"weak": 0, "developing": 1, "seen": 2, "passed": 3}


def _focus_label(focus_total: dict) -> str:
    """`_dur_label` spells nothing as "0s"; the counts line is a row of
    minutes, so an empty focus keeps that unit."""
    return focus_total["label"] if focus_total["seconds"] else "0m"


def _record_date(iso: str | None) -> str:
    """Local calendar date of a UTC stamp; an unparseable value (a corrupt
    row, since only the app writes the column) renders as no date."""
    try:
        return pretty_date(datetime.fromisoformat(iso).astimezone().date())
    except (TypeError, ValueError):
        return ""


def _document_question_ids(read) -> set[str] | None:
    """The question ids the manifest DOCUMENT names, valid or not, or None
    when the document cannot answer.

    Retirement is absence from the manifest, a fact about the document
    rather than the validated model, which drops entries the author never
    removed. A missing `questions` key means the author declares none; a
    present value that is not a list (an explicit null included) reads as
    unknown, as do a rejected read, a v1 read and an identity-mismatched read.
    """
    if (read.rejected or read.version != bundle_schema.SCHEMA_V2
            or "identity-mismatch" in read.codes()):
        return None
    raw = read.raw if isinstance(read.raw, dict) else None
    if raw is None:
        return None
    if "questions" not in raw:
        return set()
    if not isinstance(raw["questions"], list):
        return None
    items = raw["questions"]
    return {
        item["id"] for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _record_entry(state: dict, attempt: dict | None, *, label: str,
                  question_id: str, page_id: str | None, retired: bool,
                  unvalidated: bool = False,
                  declared_kind: str | None = None) -> dict:
    """One question row: its latest attempt and the verdict on THAT attempt.

    Only the latest ACTIVE review renders; earlier ones are a count, and any
    later one was retracted. `page_id` is where the manifest declares the
    question NOW; the row shows the page the answer was written on and names
    the current binding beside it. `declared_kind` speaks only for a control
    nobody has used yet, so re-kinding cannot relabel a recorded question.
    """
    review = None
    earlier = 0
    if attempt is not None:
        review = state["reviews_by_attempt"].get(attempt["attempt_id"])
        if review is not None:
            earlier = state["earlier_review_counts"].get(
                attempt["attempt_id"], 0
            )
    recorded_page = attempt["page_id"] if attempt is not None else None
    return {
        "question_id": question_id,
        "label": label,
        "page_id": recorded_page or page_id,
        "moved_to": page_id if (recorded_page and page_id
                                and page_id != recorded_page) else None,
        "retired": retired,
        "unvalidated": unvalidated,
        "ask_tutor": attempts.row_is_question(attempt, declared_kind),
        "attempt": attempt,
        "attempt_date": _record_date(attempt["created_at"]) if attempt else "",
        "review": review,
        "review_date": _record_date(review["created_at"]) if review else "",
        "review_exam": bool(review and review["mode"] == "exam"),
        "earlier_reviews": earlier,
        # Filled by `_attach_successors` once every row exists.
        "successor": None,
    }


def _attach_successors(rows: list[dict], declared: list[dict]) -> None:
    """Point a RETIRED row at the declared question that `replaces` it.

    Only retired rows take the link (§4.3). The reader compares the claim
    against the questions that VALIDATED while retirement is decided here, so
    a claim on a named-but-unvalidated question is refused at this end.
    """
    successors = {
        question["replaces"]: {
            "question_id": question["id"],
            "label": question["label"] or question["id"],
        }
        for question in declared
        if question.get("replaces")
    }
    for row in rows:
        row["successor"] = (
            successors.get(row["question_id"]) if row["retired"] else None
        )


def _record_snapshot(record: dict, bridge_page: dict | None) -> dict:
    """What crosses INTO the lesson page: the record for the questions THIS
    page declares (ABI §2.1 `welcome.record`).

    Projected from the panel rows the same render draws, never a second read,
    so the page is never shown a verdict the badge below still calls unread.
    It carries the identity it was taken FOR, and the runtime compares before
    it attaches. Only questions with something recorded get an entry: absence
    must not read as proof a question was never attempted (§6.1).
    """
    question_ids = set(bridge_page["questions"]) if bridge_page else set()
    questions = []
    for row in record["questions"]:
        if row["question_id"] not in question_ids:
            continue
        attempt = row["attempt"]
        if attempt is None:
            continue
        review = row["review"]
        questions.append({
            "question_id": row["question_id"],
            # Decided by the panel's own reader, not the page's current control
            # kind, which could turn a grading verdict into the tutor's reply.
            "asked": row["ask_tutor"],
            "answer": attempt["answer"],
            "answer_truncated": attempt["answer_truncated"],
            "answered_at": attempt["created_at"],
            "stale": attempt["stale"],
            "verdict": {
                "level": review["level"],
                "note": review["note"],
                "recorded_at": review["created_at"],
            } if review else None,
        })
    return {
        "lesson_uid": bridge_page["lesson_uid"] if bridge_page else None,
        "page_id": bridge_page["page_id"] if bridge_page else None,
        "questions": questions,
    }


def _record_panel(conn, lesson: dict, *, manifest_read=None, db_state=None) -> dict:
    state, attempt_state, focus_total = (
        db_state if db_state is not None
        else lessons.record_panel_db_state(conn, lesson["id"])
    )
    latest = attempt_state["latest_by_question"]
    # `/learn` passes the exact read that built `selected["bundle"]`, so one
    # GET cannot mix two manifest versions.
    read = (
        manifest_read if manifest_read is not None
        else lessons.read_bundle_readonly(lesson)
    )
    # A manifest that yields no declaration list knows nothing about what the
    # author still declares, so nothing is called retired on its word: the
    # attempted questions render under their durable ids, unretired.
    document_ids = _document_question_ids(read)
    declared_known = document_ids is not None
    declared = read.questions if declared_known else []

    questions = [
        _record_entry(
            state, latest.get(q["id"]),
            label=q["label"] or q["id"], question_id=q["id"],
            page_id=q["page"], retired=False, declared_kind=q["kind"],
        )
        for q in declared
    ]
    declared_ids = {q["id"] for q in declared}
    named = document_ids if declared_known else set()
    # Named but not validated is not a retirement: the row stays, unlabelled
    # and marked, on the page its answer was written on.
    questions += [
        _record_entry(
            state, attempt,
            label=question_id, question_id=question_id,
            page_id=None, retired=False, unvalidated=True,
        )
        for question_id, attempt in latest.items()
        if question_id not in declared_ids and question_id in named
    ]
    # Attempts whose question left the manifest. Durable ids are retired
    # forever, so the reviewed history behind them must not vanish with them.
    retired = [
        _record_entry(
            state, attempt,
            label=question_id, question_id=question_id,
            page_id=None, retired=True,
        )
        for question_id, attempt in latest.items()
        if question_id not in declared_ids and question_id not in named
    ]
    if not declared_known:
        questions = [dict(row, retired=False) for row in retired]
        retired = []
    _attach_successors(questions + retired, declared)

    evidence = sorted(
        (
            {
                "concept": concept,
                "level": row["level"],
                "basis": row["basis"],
                "note": row["note"],
                "date": _record_date(row["created_at"]),
                "exam": row["mode"] == "exam",
            }
            for concept, row in state["evidence_by_concept"].items()
        ),
        key=lambda chip: (_EVIDENCE_ORDER.get(chip["level"], 9), chip["concept"]),
    )

    summary = state["summary"]
    return {
        "evidence": evidence,
        "summary": {
            "note": summary["note"],
            "next_action": summary["next_action"],
            "date": _record_date(summary["created_at"]),
            "exam": summary["mode"] == "exam",
        } if summary else None,
        "questions": questions,
        "retired": retired,
        "declared_known": declared_known,
        "verdict_count": len(state["reviews_by_attempt"]),
        # A watermark over both tables, not the newest standing review: a
        # retraction takes a row AWAY and a Check rewrites one, and a cursor
        # derived from the fold would hold still through either.
        "cursor": _record_signal(state, attempt_state),
        "counts": {
            # Answers only: counting a question to the tutor as an attempt
            # would make asking for help look like a failed try.
            "attempts": attempt_state.get("answers", attempt_state["total"]),
            "questions": attempt_state.get("questions", 0),
            "assessments": state["active_count"],
            "focus": _focus_label(focus_total),
            "focus_seconds": focus_total["seconds"],
        },
        "empty": not (
            evidence or summary or questions or retired
            or attempt_state["total"] or focus_total["seconds"]
        ),
    }


# Preview CSP is selected by the manifest's runtime profile
# (learn-bundle-spec.md §5): a registered profile only, never composed or
# widened, and the readers fail-close unknown profiles to legacy-display.
# legacy-display is the historical permissive policy, verbatim, so pre-v2
# bundles keep rendering; it carries no bridge/attempt/run affordances.
_LESSON_PREVIEW_CSP_LEGACY = (
    "sandbox allow-scripts allow-forms allow-popups allow-downloads; "
    "default-src 'self' data: blob: https:; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob: https:; "
    "style-src 'self' 'unsafe-inline' data: https:; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data: https:; "
    "connect-src 'self' data: blob: https:; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
)
# interactive-local-v1: code local, data open (owner decision 2026-08-11).
# Scripts stay 'self' + inline only, so a page cannot LOAD code from the
# network; library code goes through the build step and its release
# quarantine. Everything that is not code may use the network, http: included:
# lesson experiments talk to loopback servers the learner just started.
# Accepted residuals (spec §5): an inline script can fetch remote text and
# inject it, and same-frame navigation is not blocked by any shipped header.
_LESSON_PREVIEW_CSP_INTERACTIVE = (
    "sandbox allow-scripts; "
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' http: https:; "
    "img-src 'self' data: blob: http: https:; "
    "media-src 'self' data: blob: http: https:; "
    "font-src 'self' data: http: https:; "
    "connect-src 'self' data: blob: http: https: ws: wss:; "
    "form-action 'none'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'self'"
)
_LESSON_PREVIEW_CSPS = {
    bundle_schema.PROFILE_LEGACY: _LESSON_PREVIEW_CSP_LEGACY,
    bundle_schema.PROFILE_INTERACTIVE: _LESSON_PREVIEW_CSP_INTERACTIVE,
}
# One owner for the iframe sandbox tokens: learn.html renders them and the
# preview metadata carries them, so the client keeps no second copy.
_LESSON_PREVIEW_SANDBOXES = {
    bundle_schema.PROFILE_LEGACY: "allow-scripts allow-forms allow-popups allow-downloads",
    bundle_schema.PROFILE_INTERACTIVE: "allow-scripts",
}


def _preview_csp(profile: str) -> str:
    # Unreachable via the readers, but an unregistered value defaults to the
    # NARROW policy, never the wide legacy one.
    return _LESSON_PREVIEW_CSPS.get(profile, _LESSON_PREVIEW_CSP_INTERACTIVE)


def _preview_sandbox(profile: str) -> str:
    return _LESSON_PREVIEW_SANDBOXES.get(
        profile, _LESSON_PREVIEW_SANDBOXES[bundle_schema.PROFILE_INTERACTIVE]
    )


def _lesson_preview_url(
    lesson_id: int,
    entry: str | None,
    *,
    exists: bool = True,
    meta: bool = False,
) -> str:
    if not entry:
        entry = lessons.DEFAULT_ENTRY
    if meta:
        return f"/learn/lessons/{lesson_id}/preview-meta?{urlencode([('entry', entry)])}"
    if not exists:
        return f"/learn/lessons/{lesson_id}/preview?{urlencode([('entry', entry)])}"
    return f"/learn/lessons/{lesson_id}/files/{quote(entry, safe='/')}"


def _lesson_or_404(conn, lesson_id: int) -> dict:
    lesson = lessons.get_lesson(conn, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="unknown lesson")
    return lesson


_STALE_SNAPSHOT_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Reloading…</title></head><body>
<p>This page changed while it was loading; fetching the current version…</p>
<script>setTimeout(function () { location.reload(); }, 600);</script>
</body></html>
"""


@router.get("/learn/lessons/{lesson_id}/files/{resource:path}")
def get_lesson_bundle_file(lesson_id: int, resource: str, v: str | None = None):
    # Not Depends(get_db): a FileResponse streams the file AFTER the handler
    # returns, and a dependency's finally runs only once the response
    # completes, so it would pin the connection for the whole transfer.
    conn = get_conn()
    try:
        lesson = _lesson_or_404(conn, lesson_id)
        try:
            info = lessons.bundle_resource_info(lesson, resource)
        except lessons.LessonError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
    if not info["exists"]:
        raise HTTPException(status_code=404, detail="lesson file not found")
    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if info["active"]:
        headers["Content-Security-Policy"] = _preview_csp(info["profile"])
        headers["X-Lesson-Preview-Version"] = info["version"]
    if v is not None and info["versioned_page"] and v != info["version"]:
        # Serve-time version binding: a declared v2 page whose current state
        # no longer produces the token the parent is going to arm is refused,
        # INCLUDING when no snapshot could be taken: the streaming fallback
        # must not serve bytes the requested token does not describe.
        return Response(
            content=_STALE_SNAPSHOT_HTML,
            status_code=409,
            media_type="text/html; charset=utf-8",
            headers=headers,
        )
    if info["content"] is not None:
        # The body IS the bytes the version token's digest describes;
        # FileResponse could re-open the path onto a racing replacement.
        return Response(
            content=info["content"], media_type=info["media_type"], headers=headers
        )
    return FileResponse(info["path"], media_type=info["media_type"], headers=headers)


def _scheme_hint(request: Request) -> str | None:
    """The colour scheme the app resolved for this reader, if it said: the
    `al-scheme` cookie `window.alTheme` mirrors its resolved `data-theme` into.
    A presentation hint only; any value but the two known ones is ignored."""
    value = request.cookies.get("al-scheme")
    return value if value in lessons.SCHEMES else None


@router.get("/learn/lessons/{lesson_id}/preview")
def get_lesson_preview(request: Request, lesson_id: int, entry: str | None = None,
                       conn: sqlite3.Connection = Depends(get_db)):
    lesson = _lesson_or_404(conn, lesson_id)
    try:
        html, info = lessons.preview_html(lesson, entry,
                                          scheme=_scheme_hint(request))
    except lessons.LessonError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            # The placeholder is rendered in the reader's colour scheme.
            "Vary": "Cookie",
            "Content-Security-Policy": _preview_csp(info["profile"]),
            "X-Content-Type-Options": "nosniff",
            "X-Lesson-Preview-Version": info["version"],
        },
    )


@router.get("/learn/lessons/{lesson_id}/preview-meta")
def get_lesson_preview_meta(lesson_id: int, entry: str | None = None,
                            conn: sqlite3.Connection = Depends(get_db)):
    lesson = _lesson_or_404(conn, lesson_id)
    try:
        info = lessons.lesson_file_info(lesson, entry)
    except lessons.LessonError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({
        "ok": True,
        "exists": info["exists"],
        "version": info["version"],
        "path": info["rel_path"],
        # Readers must surface findings to the metadata (learn-bundle-spec.md §9.2).
        "outcome": info["outcome"],
        "findings": info["findings"],
        # The policy set governing the page (§5) and whether it may get a port.
        "profile": info["profile"],
        "bridge": info["bridge"],
        # The per-page identity the handshake hands to the lesson is
        # parent-derived (§6.3), None whenever this page may not get a port.
        "sandbox": _preview_sandbox(info["profile"]),
        "bridge_page": info["bridge_page"],
        "preview_url": _lesson_preview_url(lesson_id, info["entry"], exists=info["exists"]),
        "file_url": _lesson_preview_url(lesson_id, info["entry"]),
    })


# Longest `since` the record poll accepts; an unbounded query parameter should
# not reach a comparison loop at all.
_MAX_SINCE_LEN = 64
# `seq` padded to the full SQLite rowid width, so string order is numeric order.
_CURSOR_WIDTH = 20


def _record_cursor(seq: int) -> str:
    """The opaque recency cursor for one assessment row: `seq`, not
    `created_at`, because the rowid is the table's recency authority and
    unique, while a verdict sharing a microsecond stamp with an acknowledged
    one would never count as unread."""
    return f"{seq:0{_CURSOR_WIDTH}d}"


def _record_signal(state: dict, attempt_state: dict) -> str:
    """How far a rendering of the panel reads, over BOTH tables behind it: a
    Check replaces an answer without touching `lesson_assessments` and a
    retraction removes a verdict without adding one, so a signal on either
    table alone holds still while the counts line moves. Fixed-width fields:
    string comparison is recency, and `unread` slices `signal[:_CURSOR_WIDTH]`
    without parsing client input."""
    if not (state["watermark"] or attempt_state["watermark"]):
        return ""
    return (f"{_record_cursor(state['watermark'])}"
            f"-{_record_cursor(attempt_state['watermark'])}")


@router.get("/learn/lessons/{lesson_id}/record-counts")
def get_lesson_record_counts(lesson_id: int, since: str | None = None,
                             conn: sqlite3.Connection = Depends(get_db)):
    """The Record panel's counts: a signal that the record moved, not a second
    rendering of it.

    Read state is the CLIENT's: `since` is the cursor the learner last
    acknowledged and `unread` counts the standing verdicts recorded after it.
    Absent `since` is nothing unread rather than everything; the client
    acknowledges an empty record with the zero cursor, so the FIRST verdict
    still announces itself. `cursor` is strictly wider than `unread`: a
    retraction or a Check moves it without a verdict to announce, and the
    client reads that as "refresh the body, no badge".
    """
    lesson = _lesson_or_404(conn, lesson_id)
    state, attempt_state, focus_total = lessons.record_panel_db_state(conn, lesson["id"])
    reviews = list(state["reviews_by_attempt"].values())
    cursors = sorted(_record_cursor(review["seq"]) for review in reviews)
    baseline = since[:_MAX_SINCE_LEN] if since else None
    return JSONResponse(
        {
            "ok": True,
            "attempts": attempt_state.get("answers", attempt_state["total"]),
            "questions": attempt_state.get("questions", 0),
            "assessments": state["active_count"],
            "verdicts": len(reviews),
            # Against the ASSESSMENT field of the baseline only, so the
            # attempt half of the signal cannot make old verdicts look new.
            "unread": (
                sum(1 for cursor in cursors if cursor > baseline[:_CURSOR_WIDTH])
                if baseline else 0
            ),
            "cursor": _record_signal(state, attempt_state),
            # The whole counts line: a focus session in the drawer moves it too.
            "focus": _focus_label(focus_total),
        },
        headers={"Cache-Control": "no-store"},
    )


# --- lesson artifacts -------------------------------------------------------


def _refusal(exc) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if isinstance(exc, attempts.AttemptError) and exc.status == 429:
        headers["Retry-After"] = str(int(attempts.RATE_WINDOW_SECONDS))
    # An unknown-field detail can carry a lone surrogate from an object key,
    # and Starlette renders JSON with ensure_ascii=False, which cannot encode
    # one; escape only the unencodable code points so the 400 path stays total.
    detail = exc.detail.encode("utf-8", "backslashreplace").decode("utf-8")
    return JSONResponse(
        {"ok": False, "error": exc.code, "detail": detail, **getattr(exc, "fields", {})},
        status_code=exc.status,
        headers=headers,
    )


async def _admit_json(
    request: Request, error_cls: type, limit: int, *, what: str,
    require_length: bool = False,
) -> dict:
    if require_length and request.headers.get("content-length") is None:
        raise error_cls("length-required", 411, "Content-Length is required")
    content_type = request.headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise error_cls("unsupported-media-type", 415, f"{what} are application/json")
    try:
        body = await read_capped(request, limit)
    except PayloadTooLarge:
        raise error_cls("payload-too-large", 413, "request body too large")
    except ValueError:
        raise error_cls("invalid-request", 400, "bad Content-Length")
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        # RecursionError: deeply nested input under the byte cap is still a
        # malformed body, never a 500.
        raise error_cls("invalid-json", 400, "body is not valid JSON")
    if not isinstance(payload, dict):
        raise error_cls("invalid-json", 400, "body must be a JSON object")
    return payload


def _artifact_lesson(conn, lesson_id: int) -> dict:
    lesson = lessons.get_lesson(conn, lesson_id)
    if lesson is None:
        raise artifacts.ArtifactError("unknown-lesson", 404, "unknown lesson")
    return lesson


@router.get("/learn/lessons/{lesson_id}/blocks/{block_id}/file")
def get_lesson_artifact(lesson_id: int, block_id: str,
                        conn: sqlite3.Connection = Depends(get_db)):
    try:
        lesson = _artifact_lesson(conn, lesson_id)
        result = artifacts.get_artifact(lesson, block_id)
    except artifacts.ArtifactError as exc:
        return _refusal(exc)
    return JSONResponse({"ok": True, **result}, headers={"Cache-Control": "no-store"})


@router.post("/learn/lessons/{lesson_id}/blocks/{block_id}/file")
async def post_lesson_artifact(request: Request, lesson_id: int, block_id: str):
    def work(payload: dict) -> dict:
        # Not Depends(get_db): this runs in a threadpool worker and sqlite3
        # connections are thread-affine, so the connection must be born in
        # the thread that uses it.
        conn = get_conn()
        try:
            lesson = _artifact_lesson(conn, lesson_id)
            return artifacts.save_artifact(conn, lesson, block_id, payload)
        finally:
            conn.close()

    try:
        payload = await _admit_json(
            request, artifacts.ArtifactError, artifacts.MAX_BODY_BYTES,
            what="artifact saves",
        )
        result = await run_in_threadpool(work, payload)
    except artifacts.ArtifactError as exc:
        return _refusal(exc)
    return JSONResponse({"ok": True, **result}, headers={"Cache-Control": "no-store"})


# --- lesson runs ------------------------------------------------------------


def _runner_refusal(exc: runner_core.RunnerError) -> JSONResponse:
    if isinstance(exc, runner_core.JobMissingError):
        error = runs.RunRequestError(
            "job-missing", 404, "runner job is no longer retained"
        )
    elif isinstance(exc, runner_core.IdempotencyConflictError):
        error = runs.RunRequestError(
            "idempotency-conflict", 409,
            "idempotency_key was already used for another block or revision",
        )
    elif isinstance(exc, (
        runner_core.LessonCapacityError,
        runner_core.GlobalCapacityError,
    )):
        error = runs.RunRequestError("busy", 409, "runner capacity is busy")
    else:
        error = runs.RunRequestError("runner-unavailable", 409, "runner is unavailable")
    return _refusal(error)


@router.post("/learn/lessons/{lesson_id}/blocks/{block_id}/runs")
async def start_lesson_run(request: Request, lesson_id: int, block_id: str):
    def load_lesson() -> dict:
        conn = get_conn()
        try:
            lesson = lessons.get_lesson(conn, lesson_id)
        finally:
            conn.close()
        if lesson is None:
            raise runs.RunRequestError("unknown-lesson", 404, "unknown lesson")
        return lesson

    service = request.app.state.runner_service
    try:
        payload = await _admit_json(
            request, runs.RunRequestError, runs.MAX_BODY_BYTES, what="run starts"
        )
        lesson = await run_in_threadpool(load_lesson)
        admission = await runs.start(service, lesson, block_id, payload)
    except (artifacts.ArtifactError, runs.RunRequestError) as exc:
        return _refusal(exc)
    except runner_core.RunnerError as exc:
        return _runner_refusal(exc)
    return JSONResponse(
        {
            "ok": True,
            "job_id": admission.job.job_id,
            "state": admission.job.state,
            "replayed": admission.replayed,
        },
        headers={"Cache-Control": "no-store"},
    )


async def _run_status(job) -> dict:
    if job.state == runner_core.FINISHED and not job.event_attempted.is_set():
        await job.event_attempted.wait()
    status = runs.status_view(job)
    if (
        status["state"] == runner_core.FINISHED
        and not job.event_attempted.is_set()
    ):
        await job.event_attempted.wait()
        status = runs.status_view(job)
    return status


@router.get("/learn/runs/{job_id}")
async def get_lesson_run(request: Request, job_id: str):
    service = request.app.state.runner_service
    job = await service.get(job_id)
    if job is None:
        return _runner_refusal(runner_core.JobMissingError())
    return JSONResponse(
        {"ok": True, **await _run_status(job)},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/learn/runs/{job_id}/cancel")
async def cancel_lesson_run(request: Request, job_id: str):
    service = request.app.state.runner_service
    job = await service.get(job_id)
    if job is None:
        return _runner_refusal(runner_core.JobMissingError())
    if job.state != runner_core.FINISHED:
        await service.cancel(job_id)
    return JSONResponse(
        {"ok": True, **await _run_status(job)},
        headers={"Cache-Control": "no-store"},
    )


def _sse_event(event: dict) -> str:
    payload = {key: value for key, value in event.items() if key != "event"}
    return (
        f"id: {event['seq']}\n"
        f"event: {event['event']}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


class _ReaderStreamingResponse(StreamingResponse):
    """Release a reader lease even when response setup/send is cancelled."""

    def __init__(self, *args, release_reader, **kwargs):
        super().__init__(*args, **kwargs)
        self._release_reader = release_reader

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._release_reader()


@router.get("/learn/runs/{job_id}/stream")
async def stream_lesson_run(request: Request, job_id: str, after: str | None = None):
    origin_rejection = browser_origin_rejection(
        request.headers, request.scope.get("scheme", "http")
    )
    if origin_rejection is not None:
        return _refusal(runs.RunRequestError("forbidden", 403, origin_rejection))
    raw_cursor = after
    if raw_cursor is None:
        raw_cursor = request.headers.get("last-event-id")
    if raw_cursor in (None, ""):
        cursor = 0
    else:
        try:
            cursor = int(raw_cursor)
        except ValueError:
            return _refusal(runs.RunRequestError(
                "invalid-cursor", 400, "after must be an integer"
            ))
        if cursor < 0:
            return _refusal(runs.RunRequestError(
                "invalid-cursor", 400, "after must be non-negative"
            ))
    service = request.app.state.runner_service
    try:
        attached = await service.attach_reader(job_id)
    except runner_core.RunnerError as exc:
        return _runner_refusal(exc)

    async def release_reader() -> None:
        await service.detach_reader(attached)

    async def events():
        current = cursor
        try:
            while True:
                _job, batch, snapshot_state = await service.events_after(
                    job_id, current
                )
                for event in batch:
                    current = int(event["seq"])
                    yield _sse_event(event)
                    if event["event"] == "exit":
                        return
                if snapshot_state == runner_core.FINISHED:
                    return
                try:
                    await asyncio.wait_for(
                        service.wait_for_update(job_id, current), timeout=15.0
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            await release_reader()

    return _ReaderStreamingResponse(
        events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        release_reader=release_reader,
    )


# --- lesson attempts (learn-bundle-spec.md §6, docs/lesson-attempts-api.md) --

# The submission is small by contract (§6.2: answer ≤ 32 KiB); the cap only
# has to admit the worst-case JSON escaping of a valid answer.
_ATTEMPT_MAX_BODY = 256 * 1024


@router.post("/learn/lessons/{lesson_id}/attempts")
async def post_lesson_attempt(request: Request, lesson_id: int):
    """The perimeter middleware already applied its unsafe-method origin
    policy, so the sandboxed iframe's own `Origin: null` never reaches here."""
    def work(payload: dict) -> dict:
        conn = get_conn()
        try:
            lesson = lessons.get_lesson(conn, lesson_id)
            if lesson is None:
                raise attempts.AttemptError("unknown-lesson", 404, "unknown lesson")
            return attempts.record_attempt(conn, lesson, payload)
        finally:
            conn.close()

    try:
        payload = await _admit_json(
            request, attempts.AttemptError, _ATTEMPT_MAX_BODY,
            what="submissions", require_length=True,
        )
        result = await run_in_threadpool(work, payload)
    except attempts.AttemptError as exc:
        return _refusal(exc)
    return JSONResponse(
        {"ok": True, **result}, headers={"Cache-Control": "no-store"}
    )


# --- lesson assessments (docs/lesson-assessments-api.md) --------------------

# The submission is small by contract (note ≤ 8 KiB, next_action ≤ 512 B); a
# quarter of the attempt cap because assessments diagnose BY REFERENCE and
# never carry an attempt body.
_ASSESSMENT_MAX_BODY = 64 * 1024


@router.post("/learn/lessons/{lesson_id}/assessments")
async def post_lesson_assessment(request: Request, lesson_id: int):
    """The perimeter middleware already applied its unsafe-method origin
    policy: the lesson-agent shell's origin-less request passes, the sandboxed
    lesson iframe's `Origin: null` never reaches here. The tutor writes
    verdicts; the lesson page must not."""
    # The session write capability is read from the header and never from the
    # body: the sitting it names is the server's own fact about the caller.
    capability_token = request.headers.get(assessments.CAPABILITY_HEADER)

    def work(payload: dict) -> dict:
        conn = get_conn()
        try:
            lesson = lessons.get_lesson(conn, lesson_id)
            if lesson is None:
                raise assessments.AssessmentError(
                    "unknown-lesson", 404, "unknown lesson"
                )
            return assessments.record_assessment(
                conn, lesson, payload, capability_token
            )
        finally:
            conn.close()

    try:
        payload = await _admit_json(
            request, assessments.AssessmentError, _ASSESSMENT_MAX_BODY,
            what="submissions", require_length=True,
        )
        result = await run_in_threadpool(work, payload)
    except assessments.AssessmentError as exc:
        return _refusal(exc)
    return JSONResponse(
        {"ok": True, **result}, headers={"Cache-Control": "no-store"}
    )


# --- the lesson build step --------------------------------------------------
#
# This route runs the package manager and the bundler, because the rules that
# make either safe live on a command line only the app writes
# (`app/services/lesson_build.py`). No capability token, unlike the assessment
# write: a build names its lesson in the path and asserts nothing about who
# asked; the perimeter policy every unsafe method passes guards it.
_BUILD_MAX_BODY = 16 * 1024


def _self_origin(request: Request) -> str | None:
    """This app's own origin, from the ASGI scope's `server` and never from
    Host: a client-supplied Host would choose which server the render gate
    loads and reports on. The same rule as the terminal's capability URL."""
    server = request.scope.get("server")
    if not server or len(server) < 2:
        return None
    host, port = server[0], server[1]
    if not host or not port:
        return None
    if ":" in host:  # bare IPv6 literal needs brackets in a URL authority
        host = f"[{host}]"
    scheme = "https" if request.scope.get("scheme") in {"wss", "https"} else "http"
    return f"{scheme}://{host}:{port}"


@router.post("/learn/lessons/{lesson_id}/build")
async def post_lesson_build(request: Request, lesson_id: int):
    try:
        payload = await _admit_json(
            request, lesson_build.BuildError, _BUILD_MAX_BODY,
            what="build requests", require_length=True,
        )
    except lesson_build.BuildError as exc:
        return _refusal(exc)

    origin = _self_origin(request)
    if origin is None:
        return _refusal(lesson_build.BuildError(
            "no-origin", 503,
            "this app cannot name its own address, so the render gate has "
            "nothing to load",
        ))

    def resolve() -> tuple[dict, str, str]:
        conn = get_conn()
        try:
            lesson = lessons.get_lesson(conn, lesson_id)
            if lesson is None:
                raise lesson_build.BuildError("unknown-lesson", 404, "unknown lesson")
            page = payload.get("page")
            # Absent means "the lesson's current page"; a non-string means the
            # caller tried to choose one and this route could not read it, and
            # treating that as absent would report evidence about another page.
            if page is not None and not isinstance(page, str):
                raise lesson_build.BuildError(
                    "invalid-request", 400,
                    "`page` must be a string naming a page in this bundle",
                )
            asked = page.strip() if isinstance(page, str) and page.strip() else None
            try:
                # A malformed page such as `../outside.html` raises and is a
                # bad request rather than an internal fault.
                wanted = (
                    lessons.selected_page_ref(lesson, asked)
                    if asked is not None else None
                )
                info = lessons.lesson_file_info(lesson, asked)
            except lessons.LessonError as exc:
                raise lesson_build.BuildError("invalid-request", 400, str(exc)) from exc
            # `lesson_file_info` falls back to the manifest's entry for a name
            # it cannot resolve, which is right for a preview and wrong here.
            if not info.get("exists") or (
                wanted is not None and info.get("entry") != wanted
            ):
                raise lesson_build.BuildError(
                    "no-page", 404,
                    "the page to render after the build is not in this bundle",
                )
            return lesson, info["entry"], _lesson_preview_url(lesson_id, info["entry"])
        finally:
            conn.close()

    try:
        lesson, page, path = await run_in_threadpool(resolve)
        add = lesson_build.clean_packages(payload.get("add"))
        entry, out = lesson_build.clean_build_refs(payload)
        result = await lesson_build.build_lesson(
            lesson, add=add, entry=entry, out=out,
            page=page, page_url=f"{origin}{path}",
            # The route the page's own relative reference resolves to, so the
            # gate can tell "loaded the artifact" from "never mentioned it".
            artifact_url=f"{origin}{_lesson_preview_url(lesson_id, out)}",
        )
    except lesson_build.BuildError as exc:
        return _refusal(exc)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/learn/lessons")
def post_lesson_create(
    request: Request,
    title: str = Form(...),
    source_url: str = Form(""),
    return_to: str = Form("/learn"),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        lesson_id = lessons.create_lesson(conn, title, source_url)
    except lessons.LessonError as exc:
        return RedirectResponse(
            _with_flash(_safe_return(return_to, "/learn"), str(exc)), status_code=303
        )
    return RedirectResponse(f"/learn?lesson={lesson_id}", status_code=303)


@router.post("/learn/lessons/{lesson_id}/entry")
def post_lesson_entry(
    request: Request,
    lesson_id: int,
    entry: str = Form(...),
    return_to: str = Form("/learn"),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        lessons.set_current_entry(conn, lesson_id, entry)
    except lessons.LessonError as exc:
        return RedirectResponse(
            _with_flash(_safe_return(return_to, f"/learn?lesson={lesson_id}"), str(exc)),
            status_code=303,
        )
    return RedirectResponse(_safe_return(return_to, f"/learn?lesson={lesson_id}"), status_code=303)


@router.post("/learn/lessons/{lesson_id}/status")
def post_lesson_status(
    request: Request,
    lesson_id: int,
    status: str = Form(...),
    return_to: str = Form("/learn"),
    conn: sqlite3.Connection = Depends(get_db),
):
    try:
        lessons.set_status(conn, lesson_id, status)
    except lessons.LessonError as exc:
        return RedirectResponse(
            _with_flash(_safe_return(return_to, "/learn"), str(exc)), status_code=303
        )
    return RedirectResponse(_safe_return(return_to, "/learn"), status_code=303)


@router.post("/learn/lessons/{lesson_id}/archive")
def post_lesson_archive(request: Request, lesson_id: int, return_to: str = Form("/learn"),
                        conn: sqlite3.Connection = Depends(get_db)):
    try:
        lessons.archive_lesson(conn, lesson_id)
    except lessons.LessonError as exc:
        return RedirectResponse(
            _with_flash(_safe_return(return_to, "/learn"), str(exc)), status_code=303
        )
    return RedirectResponse(_safe_return(return_to, "/learn"), status_code=303)


@router.post("/learn/lessons/{lesson_id}/restore")
def post_lesson_restore(request: Request, lesson_id: int, return_to: str = Form("/learn"),
                        conn: sqlite3.Connection = Depends(get_db)):
    try:
        lessons.restore_lesson(conn, lesson_id)
    except lessons.LessonError as exc:
        return RedirectResponse(
            _with_flash(_safe_return(return_to, "/learn"), str(exc)), status_code=303
        )
    return RedirectResponse(_safe_return(return_to, "/learn"), status_code=303)
