"""Learn routes (#24 cut 1): the lesson surface extracted from app/main.py.

Moved verbatim — every route path, signature, status code, header and template
context is the one main.py had; only the decorators (`@app.` → `@router.`) and
the imports changed. The router is included with no prefix, in the position the
routes used to occupy, so URLs and registration order are unchanged (the
`by-slug` routes still register before their `{lesson_id}` twins).

The surface: the /learn view + preview/metadata/bundle files, lesson artifacts
(F1), runs (F4, including the SSE stream), attempts (D4), assessments (S-D-S1)
and the lesson CRUD writes. One module, not a package: those seams stay
together until something actually varies across them.
"""
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
        # Lesson agents write the manifest before they can POST an attempt
        # against its question. Capture the DB state first, then take the
        # single FINAL manifest read used by bundle metadata, selection
        # persistence, and the record:
        # a newly committed attempt can therefore never be classified
        # against an older declaration set.
        record_db_state = _record_panel_db_state(conn, selected["id"])
        selected, selected_manifest = lessons.with_bundle_info_read(
            selected, entry=selected_entry
        )
        # A rejected manifest has no selectable entry — show the
        # placeholder without persisting a selection. A stale v2
        # selection (§4.2) keeps its stored/requested candidate too:
        # persisting the fallback would make the very next read report
        # `ok` and erase the finding.
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
        # The metadata poll re-resolves its `entry` param on every request, so
        # it must carry the ORIGINAL stale candidate when the render fell back
        # (§4.2): polling the fallback entry would answer `ok` and silently
        # clear the invalid-entry finding the page just surfaced. The version
        # token still tracks the fallback file — the one actually displayed —
        # because resolution inside the meta route lands on the same fallback.
        selected["preview_meta_url"] = _lesson_preview_url(
            selected["id"],
            selected["bundle"]["stale_selection"] or selected["entry"],
            meta=True,
        )
        # F1 endpoint discovery.  The template guards this field so a running
        # pre-F backend rendering the new working-tree template omits the
        # capability instead of advertising a route it does not have.
        selected["artifacts_url"] = f"/learn/lessons/{selected['id']}/blocks"
        selected["runs_url"] = f"/learn/lessons/{selected['id']}/blocks"
        # #133 endpoint discovery, guarded in the template for the same
        # reason: a live pre-#133 backend rendering this template must render
        # no poll target rather than one that 404s every few seconds.
        selected["record_counts_url"] = f"/learn/lessons/{selected['id']}/record-counts"
        # #136: what the "Review my answers" button types. None when no agent
        # CLI is installed here — the template then renders no button, because
        # a one-click review that ends in `command not found` is worse than the
        # bare terminal it replaces. Guarded in the template as well, for the
        # live pre-#136 backend that renders it without this key.
        selected["tutor_command"] = lessons.tutor_launch_command()
        # D2: the iframe sandbox attribute follows the effective profile
        # (same owner as the header-level directive); the profile is folded
        # into the version token, so a flip reloads the frame and the parent
        # runtime re-applies the tokens the metadata then carries.
        selected["sandbox"] = _preview_sandbox(selected["file"]["profile"])
        # #133 tier 2: the read-back snapshot the bridge hands the loaded
        # document in its `welcome`. An attribute rather than a route, so it
        # is the same reading as the panel this render draws — see
        # `_record_snapshot`. Scoped to the questions this page declares,
        # which the bridge identity already resolved (spec §6.3).
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
    # #81: track progress counts the whole active list, never the filtered
    # `rows` — "N of M studied" that moved when a status pill was clicked would
    # be a different number each time and mean nothing. The next-step link
    # leaves the filter behind for the same reason: it points at a lesson the
    # current filter usually excludes.
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
        "tracks": tracks,
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

    A course seeded as fourteen lessons buries every other lesson in a flat
    list, so a track renders as one group instead of fourteen rows. The split
    is presentational: `rows` is whatever the status/archived filter already
    chose, and grouping neither adds a row to it nor removes one.

    Groups nest by the `path` address (§4.5): `codecrafters/concepts/…` renders
    inside `codecrafters`, and a node keeps its own rows alongside its child
    groups. Whole branches the filter emptied are dropped — an ancestor
    survives on its descendants' rows alone, because folding a group away must
    not be able to hide the only lesson that matched.

    The header keeps the whole-track numbers from `track_progress` (#81: they
    are the unfiltered list, so a status pill cannot make them jump) while the
    rows inside are the filtered ones — which is why a group can read "1 of 14"
    over a single row without either number being wrong. A track no filtered
    row belongs to is dropped entirely rather than left as an empty header.

    Members keep the track's own step order, not the list's recency order: a
    course is read forwards. Membership comes from `track_progress` for the
    same reason its counts do — one owner for the rule (see its docstring).

    Archived lessons reach `rows` only under `?archived=1`, and archiving drops
    a lesson from its track, so they land in the ungrouped tail — the same
    answer the #81 strip already gives.
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
            # Integer percent for the bar's width; the readable count beside it
            # stays "N of M", which reads better than "0%" for an untouched
            # track. Rounds toward 0 so a bar only fills when the step is done.
            "pct": node["studied"] * 100 // node["total"],
            # Whether the lesson on screen is somewhere in this node's subtree.
            # It is the server's default for "open" — a learner who navigated
            # into a track should land with every ancestor of it unfolded — and
            # it stays true after the stored preference overrides that default,
            # so a group deliberately kept folded can still show that the
            # current lesson is inside it.
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


# --- the record panel (#4 phase S, D-S3-1) -----------------------------------
#
# What the tutor concluded, rendered beside the lesson: evidence per concept,
# where the last session left off, and the verdict on each question's latest
# answer. Server-rendered on GET like the rest of the MPA — no JS, and every
# agent- and learner-authored string reaches the template as plain text that
# Jinja escapes (learn.html renders no markdown: the lesson iframe's
# protections do not extend to the parent page).
#
# The whole builder is a pure read. The manifest comes from the READONLY
# reader: D-F1-2 binds phase S too, so rendering a page never creates bundle
# state. The assessment side is the D-S1-2 active fold, so a retracted or
# superseded record never reaches the panel.

# Chip order: the actionable state first, then alphabetically so the block is
# stable between renders. Unknown levels cannot occur (closed vocabulary,
# CHECK-enforced) but sort last rather than crashing the page.
_EVIDENCE_ORDER = {"weak": 0, "developing": 1, "seen": 2, "passed": 3}


def _focus_label(focus_total: dict) -> str:
    """The focused magnitude for the counts line. `_dur_label` spells nothing
    as "0s"; the line is a row of magnitudes, so an empty one keeps the minutes
    unit of the rest. One owner, because the poll refreshes the same cell."""
    return focus_total["label"] if focus_total["seconds"] else "0m"


def _record_date(iso: str | None) -> str:
    """Local calendar date of a UTC authority stamp. The column is written
    only by the app, so an unparseable value is a corrupt-row guard rather
    than a contract — it renders as no date instead of failing the page."""
    try:
        return pretty_date(datetime.fromisoformat(iso).astimezone().date())
    except (TypeError, ValueError):
        return ""


def _document_question_ids(read) -> set[str] | None:
    """The question ids the manifest DOCUMENT names, valid or not, or None
    when the document does not answer the question.

    Retirement is absence from the manifest (S-M7), and absence is a fact
    about the document rather than about the typed model. Validation drops
    entries the author never removed — a dangling page reference, an id that
    fails the grammar — neither of which rejects the read, so a question
    missing from `read.questions` may be unreadable rather than retired.
    Presence is therefore read here and everything shown still comes from the
    validated model.

    A missing `questions` key is an answer: the author declares none, and an
    attempted question really has left. Any value PRESENT under that key and
    not a list — an explicit null included — is not an answer: nothing can be
    observed absent from a list that is not there, so it reads as unknown,
    like a rejected manifest. Presence is tested on the key rather than on the
    value, because `raw.get` cannot tell the two documents apart.

    A rejected read cannot answer for the document. A v1 read cannot either:
    that schema has no question declaration at all. Nor can an
    identity-mismatched v2 read, whose declaration belongs to a different
    lesson even though the shared reader keeps that condition DEGRADED so the
    foreign bundle can still render under the legacy profile.
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

    A review names the attempt it judged, so a verdict on a superseded answer
    stays with that answer instead of being re-attached to a newer one. Only
    the latest ACTIVE review renders; the reviews written BEFORE it are a
    count. Reviews written after it are not: if the fold shows this one, any
    later review was retracted, and a retracted verdict is not an earlier
    reading of the answer.

    `page_id` is where the manifest declares the question NOW; the attempt
    carries where it was answered. A question may move pages, and the stored
    `stale` flag was decided at record time, so a move after the answer leaves
    no mark on it — the row therefore shows the page the answer was written on
    and names the current binding beside it rather than silently adopting it.

    `ask_tutor` (#136) is the row's DIRECTION: the learner asked this instead
    of answering it. It follows the same rule as `stale` — what was recorded
    wins, and `declared_kind` speaks only for a control nobody has used yet —
    so neither retiring an ask control nor re-kinding an ordinary question can
    relabel what the learner already wrote. Turning a recorded question back
    into a wrong answer is the exact misreading this issue exists to end.
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
        # Filled by `_attach_successors` once every row exists (#133 tier 1).
        "successor": None,
    }


def _attach_successors(rows: list[dict], declared: list[dict]) -> None:
    """Point a RETIRED row at the declared question that says it `replaces` it.

    Durable ids are never reused, so a rewritten question arrives under a new
    id and everything recorded about the old one keeps standing alone at the
    foot of the panel. The manifest is the only place that can say the two are
    the same question, and the reader has already refused every ambiguous or
    self-referential claim, so this is a plain lookup — a link between two rows
    the panel is showing anyway, never a move of history between them.

    Only retired rows take the link (§4.3: a predecessor is by definition no
    longer declared). The reader can only compare the claim against the
    questions that VALIDATED, while retirement is decided here, against the
    ids the document still names — so a claim on a question the manifest still
    names but could not validate is refused at this end, where that is known.
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
    page declares (#133 tier 2, ABI §2.1 `welcome.record`).

    Projected from the panel rows the same render draws — never a second read
    of the record. That is the whole point of taking it here: a snapshot read
    fresher than the panel would restore an answer or a verdict that the panel
    under the iframe does not show, and tier 1's unread cursor watermarks
    exactly the rows that WERE rendered. One reading feeds both surfaces, by
    construction, so the page can never be shown a verdict the badge below it
    still calls unread — nor the reverse.

    Nothing here is new information: every field is already on the page, in the
    Record panel, for the same learner. The answer is the panel's own excerpt
    (`answer_truncated` says so), not a re-read of the full 32 KiB body — the
    boundary carries what the page already shows and no more.

    It carries the identity it was taken FOR. The parent may reload the frame
    onto another page without a /learn render — a removed or renamed entry
    falls back through `_resolve_entry` — and that successor document must not
    be handed the predecessor's answers just because it completed the next
    handshake (review round 1, P1). The runtime compares before it attaches.

    Only questions with something recorded get an entry. A declared question
    the learner has not answered has nothing to restore, and the page already
    knows its own ids; an entry saying "nothing" would only invite a page to
    read absence as proof it was never attempted, which §6.1 forbids.
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
            # The direction the RECORD travels, decided by the panel's own
            # reader (review round 1, P2): re-kinding a durable id must not
            # let a page read a grading verdict as the tutor's reply, which is
            # exactly what its current control kind would say.
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


_record_panel_db_state = lessons.record_panel_db_state


def _record_panel(conn, lesson: dict, *, manifest_read=None, db_state=None) -> dict:
    state, attempt_state, focus_total = (
        db_state if db_state is not None
        else _record_panel_db_state(conn, lesson["id"])
    )
    latest = attempt_state["latest_by_question"]
    # `/learn` passes the exact read that built `selected["bundle"]`, so one
    # GET cannot show metadata from one manifest version and question
    # retirement/labels from another. Direct helper callers retain the pure
    # read fallback.
    read = (
        manifest_read if manifest_read is not None
        else lessons.read_bundle_readonly(lesson)
    )
    # A manifest that does not yield a declaration list — rejected outright, or
    # carrying a `questions` value nothing can be read as absent from — knows
    # nothing about what the author still declares, so nothing is called
    # retired on its word (S-M7 retires by ABSENCE from the manifest, and
    # absence has to be observed, not assumed). The attempted questions then
    # render under their durable ids and the retired block is omitted entirely.
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
    # An answered question the manifest still names but the reader could not
    # validate keeps its place in the list — unlabelled and marked, because a
    # validation failure is not a retirement. It has no readable current page,
    # so the row falls back to the page its answer was written on.
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
        # Not retired — just unlabelled, because the manifest could not be read.
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
        # Kept beside `counts` rather than inside it: the count line is the
        # panel's own shape, and the record-counts poll below is the second
        # reader of these two.
        "verdict_count": len(state["reviews_by_attempt"]),
        # How far THIS rendering of the panel reads. What the learner
        # acknowledges is the snapshot in front of them, so the cursor travels
        # with the rendered rows rather than being taken from whichever poll
        # happened to answer last. A watermark over both tables, not the newest
        # standing review: a retraction takes a row AWAY and a Check rewrites
        # one, and a cursor derived from the fold would hold still (or move
        # backwards) through either.
        "cursor": _record_signal(state, attempt_state),
        "counts": {
            # Answers only (#136): a question to the tutor is recorded in the
            # same table, but calling it an attempt in the line the learner
            # reads would make asking for help look like a failed try. It is
            # counted beside it instead, and only when there is one.
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
# (learn-bundle-spec.md §5; enforcement is D1's). The manifest can only pick
# a registered profile — never compose or widen policy — and the readers
# fail-close missing/unknown profiles to legacy-display, so the profile
# reaching here is always registered. The iframe sandbox *attribute* in
# learn.html is unchanged here (D2 owns it); the header-level `sandbox`
# directive below also covers a page opened directly, outside the iframe.
#
# legacy-display: the historical permissive policy, verbatim, so pre-v2
# bundles keep rendering — but the profile carries no bridge/attempt/run
# affordances (those flags are computed off the manifest read, not the CSP).
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
# SCRIPTS stay 'self' + inline only — remote script URLs, data:/blob: script
# and 'unsafe-eval' are refused, so the only road for library code is the
# build step with its 30-day release quarantine, and render-time loads
# cannot bypass it. Everything that is not code may use the network:
# fetch/XHR/WebSocket (connect-src), images, media, fonts and stylesheets
# accept remote URLs, http: included — lesson experiments talk to loopback
# servers the learner just started, and the app itself is served over http.
# Forms, popups, downloads, plugins and nested frames stay refused.
#
# Known residual (spec §5): SAME-FRAME NAVIGATION is not blocked — a page can
# still `location.href = remote` or follow a plain link, and the destination
# document is outside this response's CSP. No shipped header can close that
# channel: CSP3's `navigate-to` was removed from the spec (Sep 2022) without
# ever shipping in a browser, and no sandbox token governs self-navigation.
# The layer that can observe the frame leaving the lesson document is the D2
# parent runtime (its bridge port dies with the document); until then this
# stays an accepted, documented residual of the loopback deployment.
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
# The iframe sandbox ATTRIBUTE mirrors the header-level `sandbox` directive
# per profile (D2; the header alone already covers direct opens). One owner:
# learn.html renders these tokens and the preview metadata carries them, so
# the parent runtime re-applies the server's value on profile flips instead
# of keeping a second copy of this policy in the client.
_LESSON_PREVIEW_SANDBOXES = {
    bundle_schema.PROFILE_LEGACY: "allow-scripts allow-forms allow-popups allow-downloads",
    bundle_schema.PROFILE_INTERACTIVE: "allow-scripts",
}


def _preview_csp(profile: str) -> str:
    # Unreachable via the readers (they fail-close to legacy-display), but if
    # an unregistered value ever leaks through, default to the NARROW policy —
    # never the wide legacy one.
    return _LESSON_PREVIEW_CSPS.get(profile, _LESSON_PREVIEW_CSP_INTERACTIVE)


def _preview_sandbox(profile: str) -> str:
    # Same fail-closed default as _preview_csp: unknown ⇒ the narrow tokens.
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
    # NOT Depends(get_db) (#24 cut 5): this route can answer with a
    # FileResponse, which streams the bundle file from disk AFTER the handler
    # returns. A dependency's finally runs only once the response completes, so
    # it would pin this connection for the whole transfer — a slow client would
    # hold it open indefinitely. The database is needed only to resolve the
    # lesson, so the connection is scoped to exactly that, as it was before.
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
        # Serve-time version binding (PR-60 rounds 1-2): the parent
        # navigates with the token it is going to arm; a declared v2 page
        # whose current state no longer produces that token is refused —
        # INCLUDING when no snapshot could be taken (raced replacement,
        # grown past the size bound): the streaming fallback must not
        # serve bytes the requested token does not describe. The transient
        # swap-restore case self-heals on the reload below; a real edit
        # moves the metadata token and the parent re-navigates with it.
        return Response(
            content=_STALE_SNAPSHOT_HTML,
            status_code=409,
            media_type="text/html; charset=utf-8",
            headers=headers,
        )
    if info["content"] is not None:
        # Declared v2 page: byte-bound snapshot (drain D2 L2) — the body IS
        # the bytes the version token's digest describes; FileResponse would
        # re-open the path and could serve a racing replacement instead.
        return Response(
            content=info["content"], media_type=info["media_type"], headers=headers
        )
    return FileResponse(info["path"], media_type=info["media_type"], headers=headers)


def _scheme_hint(request: Request) -> str | None:
    """The colour scheme the app resolved for this reader, if it said.

    `window.alTheme` (base.html) mirrors its resolved `data-theme` into the
    `al-scheme` cookie precisely so this server-rendered page can match a theme
    that is pinned against the OS. A presentation hint and nothing else: it
    selects a palette, authorises nothing, and any value but the two known ones
    is ignored rather than trusted into the response.
    """
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
            # A generated placeholder is rendered in the reader's colour scheme,
            # so the cookie is part of what selects this body.
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
        # Manifest read outcome + findings (learn-bundle-spec.md §9.2):
        # readers must surface findings to the preview metadata.
        "outcome": info["outcome"],
        "findings": info["findings"],
        # Effective runtime profile + bridge eligibility (§5, D1): the
        # metadata is where the app states which policy set governs the
        # rendered page and whether D2 may offer the postMessage port.
        "profile": info["profile"],
        "bridge": info["bridge"],
        # D2: the sandbox tokens the iframe must carry for this profile (the
        # parent runtime re-applies them before reloading on a profile flip)
        # and the per-page identity the handshake hands to the lesson —
        # parent-derived (§6.3), None whenever this page may not get a port.
        "sandbox": _preview_sandbox(info["profile"]),
        "bridge_page": info["bridge_page"],
        "preview_url": _lesson_preview_url(lesson_id, info["entry"], exists=info["exists"]),
        "file_url": _lesson_preview_url(lesson_id, info["entry"]),
    })


# Longest `since` a caller can hand the record poll. The value is only ever
# compared as a string against cursors this app minted, but an unbounded query
# parameter should not reach a comparison loop at all.
_MAX_SINCE_LEN = 64
# Width the assessment `seq` is padded to so plain string ordering agrees with
# numeric ordering. 20 digits covers the full SQLite rowid range.
_CURSOR_WIDTH = 20


def _record_cursor(seq: int) -> str:
    """The opaque recency cursor for one assessment row.

    Deliberately `seq`, not `created_at`: the rowid is this table's recency
    AUTHORITY (assessments.py) and is unique by construction, while two rows
    can carry the same microsecond stamp and then have no order at all —
    a verdict sharing a stamp with an acknowledged one would never be counted
    unread. Padded so the client can treat it as an opaque string it only ever
    hands back.
    """
    return f"{seq:0{_CURSOR_WIDTH}d}"


def _record_signal(state: dict, attempt_state: dict) -> str:
    """How far a rendering of the panel reads, over BOTH tables behind it.

    The rows are a join: a Check recorded while the page is open replaces an
    answer (or fills in a "Not attempted" row) without touching
    `lesson_assessments`, exactly as a retraction removes a verdict without
    adding a standing one. A signal built on either table alone therefore
    holds still while the counts line beside it moves, and the panel would sit
    on a stale body until a full reload.

    Both fields are fixed width with a constant separator, so comparing two
    signals as plain strings compares recency — and the assessment field keeps
    its own leading position, which is what lets `unread` be counted against
    `signal[:_CURSOR_WIDTH]` without parsing anything a client handed back.
    """
    if not (state["watermark"] or attempt_state["watermark"]):
        return ""
    return (f"{_record_cursor(state['watermark'])}"
            f"-{_record_cursor(attempt_state['watermark'])}")


@router.get("/learn/lessons/{lesson_id}/record-counts")
def get_lesson_record_counts(lesson_id: int, since: str | None = None,
                             conn: sqlite3.Connection = Depends(get_db)):
    """The Record panel's counts, so a verdict written while the page is open
    reaches the learner (#133 tier 1).

    The panel itself is server-rendered on GET and the live-reload poll only
    watches the lesson FILE, so before this a verdict landed silently and the
    learner had to guess that reloading was worth it. This answers with the
    same numbers the panel folds — no note text, no per-question shape: it is a
    signal that the record moved, not a second rendering of it.

    Read state is the CLIENT's: `since` is the cursor the learner last
    acknowledged, and `unread` counts the standing verdicts recorded after it.
    Nothing is stored server-side, so this stays a pure read and two browsers
    on the same lesson keep their own idea of what they have seen. Absent
    `since` = no baseline yet (a first visit), which is nothing unread rather
    than everything; the client acknowledges an empty record with the zero
    cursor, so the FIRST verdict of a lesson still announces itself.

    `cursor` is the panel's WATERMARK over both tables behind it, which is a
    strictly wider signal than `unread`: a retraction, an evidence/summary
    write, or a Check recorded from the editor changes the rendered rows
    without adding a standing verdict to announce. The watermark still moves
    for all of those, and the client reads a cursor past its baseline with
    nothing unread as "the record changed quietly" — refresh the body, no
    badge. A count that only ever went up would leave a retracted verdict on
    screen under a header already reading `0 verdicts`.
    """
    lesson = _lesson_or_404(conn, lesson_id)
    state, attempt_state, focus_total = _record_panel_db_state(conn, lesson["id"])
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
            # Against the ASSESSMENT field of the baseline only: what counts as
            # unread is a standing verdict recorded after it, and the attempt
            # half of the signal must not make old verdicts look new again.
            # Fixed-width fields make that a slice, not a parse of client input.
            "unread": (
                sum(1 for cursor in cursors if cursor > baseline[:_CURSOR_WIDTH])
                if baseline else 0
            ),
            "cursor": _record_signal(state, attempt_state),
            # The whole counts line, not a subset: a focus session finished in
            # the drawer beside this panel moves it too, and half a line that
            # refreshes is worse than one that plainly does not.
            "focus": _focus_label(focus_total),
        },
        headers={"Cache-Control": "no-store"},
    )


# --- lesson artifacts (phase F1 editor backend) ----------------------------


def _artifact_refusal(exc: artifacts.ArtifactError) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if exc.status == 429:
        headers["Retry-After"] = str(
            int(exc.fields.get("retry_after", artifacts.RATE_WINDOW_SECONDS))
        )
    return JSONResponse(
        {"ok": False, "error": exc.code, "detail": exc.detail, **exc.fields},
        status_code=exc.status,
        headers=headers,
    )


def _artifact_lesson(
    conn, *, lesson_id: int | None = None, slug: str | None = None
) -> dict:
    lesson = (
        lessons.get_lesson_by_slug(conn, slug)
        if slug is not None
        else lessons.get_lesson(conn, lesson_id)
    )
    if lesson is None:
        raise artifacts.ArtifactError("unknown-lesson", 404, "unknown lesson")
    return lesson


def _get_artifact(
    conn, block_id: str, *, lesson_id: int | None = None, slug: str | None = None
) -> JSONResponse:
    try:
        lesson = _artifact_lesson(conn, lesson_id=lesson_id, slug=slug)
        result = artifacts.get_artifact(lesson, block_id)
    except artifacts.ArtifactError as exc:
        return _artifact_refusal(exc)
    return JSONResponse({"ok": True, **result}, headers={"Cache-Control": "no-store"})


async def _save_artifact(
    request: Request,
    block_id: str,
    *,
    lesson_id: int | None = None,
    slug: str | None = None,
) -> JSONResponse:
    content_type = request.headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        return _artifact_refusal(artifacts.ArtifactError(
            "unsupported-media-type", 415, "artifact saves are application/json"
        ))
    try:
        body = await read_capped(request, artifacts.MAX_BODY_BYTES)
    except PayloadTooLarge:
        return _artifact_refusal(artifacts.ArtifactError(
            "payload-too-large", 413, "request body too large"
        ))
    except ValueError:
        return _artifact_refusal(artifacts.ArtifactError(
            "invalid-request", 400, "bad Content-Length"
        ))
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return _artifact_refusal(artifacts.ArtifactError(
            "invalid-json", 400, "body is not valid JSON"
        ))
    if not isinstance(payload, dict):
        return _artifact_refusal(artifacts.ArtifactError(
            "invalid-json", 400, "body must be a JSON object"
        ))

    def work() -> dict:
        # NOT Depends(get_db) (#24 cut 5): this runs in a threadpool worker,
        # and sqlite3 connections are thread-affine (check_same_thread). A
        # dependency-provided connection is opened in whichever worker
        # resolved the dependency, so using it here would raise as soon as
        # the two threads differ. The connection must be born in the thread
        # that uses it.
        conn = get_conn()
        try:
            lesson = _artifact_lesson(conn, lesson_id=lesson_id, slug=slug)
            return artifacts.save_artifact(conn, lesson, block_id, payload)
        finally:
            conn.close()

    try:
        result = await run_in_threadpool(work)
    except artifacts.ArtifactError as exc:
        return _artifact_refusal(exc)
    return JSONResponse({"ok": True, **result}, headers={"Cache-Control": "no-store"})


# Registered before the {lesson_id} routes so "by-slug" is not parsed as id.
@router.get("/learn/lessons/by-slug/{slug}/blocks/{block_id}/file")
def get_lesson_artifact_by_slug(slug: str, block_id: str,
                                conn: sqlite3.Connection = Depends(get_db)):
    return _get_artifact(conn, block_id, slug=slug)


@router.post("/learn/lessons/by-slug/{slug}/blocks/{block_id}/file")
async def post_lesson_artifact_by_slug(request: Request, slug: str, block_id: str):
    return await _save_artifact(request, block_id, slug=slug)


@router.get("/learn/lessons/{lesson_id}/blocks/{block_id}/file")
def get_lesson_artifact(lesson_id: int, block_id: str,
                        conn: sqlite3.Connection = Depends(get_db)):
    return _get_artifact(conn, block_id, lesson_id=lesson_id)


@router.post("/learn/lessons/{lesson_id}/blocks/{block_id}/file")
async def post_lesson_artifact(request: Request, lesson_id: int, block_id: str):
    return await _save_artifact(request, block_id, lesson_id=lesson_id)


# --- lesson runs (phase F4 run API) ---------------------------------------


def _run_refusal(code: str, status: int, detail: str = "", **fields) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if status == 429:
        headers["Retry-After"] = str(int(fields.get("retry_after", 1)))
    return JSONResponse(
        {"ok": False, "error": code, "detail": detail, **fields},
        status_code=status,
        headers=headers,
    )


def _runner_refusal(exc: runner_core.RunnerError) -> JSONResponse:
    if isinstance(exc, runner_core.RateLimitedError):
        return _run_refusal(
            "rate-limited", 429, "run start rate limit exceeded",
            retry_after=int(getattr(exc, "retry_after", runs.RATE_WINDOW_SECONDS)),
        )
    if isinstance(exc, runner_core.JobMissingError):
        return _run_refusal("job-missing", 404, "runner job is no longer retained")
    if isinstance(exc, runner_core.IdempotencyConflictError):
        return _run_refusal(
            "idempotency-conflict", 409,
            "idempotency_key was already used for another block or revision",
        )
    if isinstance(exc, (
        runner_core.LessonCapacityError,
        runner_core.GlobalCapacityError,
        runner_core.ReaderCapacityError,
    )):
        return _run_refusal("busy", 409, "runner capacity is busy")
    if isinstance(exc, runner_core.UnknownRunnerError):
        return _run_refusal("unknown-runner", 422, "runner is not registered")
    if isinstance(exc, runner_core.IncompatibleRunnerError):
        return _run_refusal(
            "incompatible-runner", 422, "artifact file is incompatible with runner"
        )
    if isinstance(exc, runner_core.SnapshotTooLargeError):
        return _run_refusal("file-too-large", 413, "runner snapshot is too large")
    return _run_refusal("runner-unavailable", 409, "runner is unavailable")


async def _start_run(
    request: Request,
    block_id: str,
    *,
    lesson_id: int | None = None,
    slug: str | None = None,
) -> JSONResponse:
    content_type = request.headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        return _run_refusal(
            "unsupported-media-type", 415, "run starts are application/json"
        )
    try:
        body = await read_capped(request, runs.MAX_BODY_BYTES)
    except PayloadTooLarge:
        return _run_refusal("payload-too-large", 413, "request body too large")
    except ValueError:
        return _run_refusal("invalid-request", 400, "bad Content-Length")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return _run_refusal("invalid-json", 400, "body is not valid JSON")
    if not isinstance(payload, dict):
        return _run_refusal("invalid-json", 400, "body must be a JSON object")

    def load_lesson() -> dict | None:
        # NOT Depends(get_db) (#24 cut 5): this runs in a threadpool worker,
        # and sqlite3 connections are thread-affine (check_same_thread). A
        # dependency-provided connection is opened in whichever worker
        # resolved the dependency, so using it here would raise as soon as
        # the two threads differ. The connection must be born in the thread
        # that uses it.
        conn = get_conn()
        try:
            return (
                lessons.get_lesson_by_slug(conn, slug)
                if slug is not None else lessons.get_lesson(conn, lesson_id)
            )
        finally:
            conn.close()

    lesson = await run_in_threadpool(load_lesson)
    if lesson is None:
        return _run_refusal("unknown-lesson", 404, "unknown lesson")
    service = request.app.state.runner_service
    try:
        admission = await runs.start(service, lesson, block_id, payload)
    except artifacts.ArtifactError as exc:
        return _run_refusal(exc.code, exc.status, exc.detail, **exc.fields)
    except runs.RunRequestError as exc:
        return _run_refusal(exc.code, exc.status, exc.detail)
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


@router.post("/learn/lessons/by-slug/{slug}/blocks/{block_id}/runs")
async def start_lesson_run_by_slug(request: Request, slug: str, block_id: str):
    return await _start_run(request, block_id, slug=slug)


@router.post("/learn/lessons/{lesson_id}/blocks/{block_id}/runs")
async def start_lesson_run(request: Request, lesson_id: int, block_id: str):
    return await _start_run(request, block_id, lesson_id=lesson_id)


@router.get("/learn/runs/{job_id}")
async def get_lesson_run(request: Request, job_id: str):
    service = request.app.state.runner_service
    job = await service.get(job_id)
    if job is None:
        return _run_refusal("job-missing", 404, "runner job is no longer retained")
    if job.state == runner_core.FINISHED and not job.event_attempted.is_set():
        await job.event_attempted.wait()
    status = runs.status_view(job)
    if (
        status["state"] == runner_core.FINISHED
        and not job.event_attempted.is_set()
    ):
        await job.event_attempted.wait()
        status = runs.status_view(job)
    return JSONResponse(
        {"ok": True, **status},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/learn/runs/{job_id}/cancel")
async def cancel_lesson_run(request: Request, job_id: str):
    service = request.app.state.runner_service
    job = await service.get(job_id)
    if job is None:
        return _run_refusal("job-missing", 404, "runner job is no longer retained")
    if job.state != runner_core.FINISHED:
        await service.cancel(job_id)
    if job.state == runner_core.FINISHED and not job.event_attempted.is_set():
        await job.event_attempted.wait()
    status = runs.status_view(job)
    if (
        status["state"] == runner_core.FINISHED
        and not job.event_attempted.is_set()
    ):
        await job.event_attempted.wait()
        status = runs.status_view(job)
    return JSONResponse(
        {"ok": True, **status},
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
        return _run_refusal("forbidden", 403, origin_rejection)
    raw_cursor = after
    if raw_cursor is None:
        raw_cursor = request.headers.get("last-event-id")
    if raw_cursor in (None, ""):
        cursor = 0
    else:
        try:
            cursor = int(raw_cursor)
        except ValueError:
            return _run_refusal("invalid-cursor", 400, "after must be an integer")
        if cursor < 0:
            return _run_refusal("invalid-cursor", 400, "after must be non-negative")
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


# --- lesson attempts (D4, learn-bundle-spec.md §6 + docs/lesson-attempts-api.md)

# The submission envelope is small by contract (§6.2: answer ≤ 32 KiB, whole
# projection line ≤ 64 KiB); the body cap only has to admit the worst-case
# JSON escaping of a valid answer plus the fixed fields.
_ATTEMPT_MAX_BODY = 256 * 1024


def _attempt_refusal(code: str, status: int, detail: str = "") -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if status == 429:
        headers["Retry-After"] = str(int(attempts.RATE_WINDOW_SECONDS))
    return JSONResponse(
        {"ok": False, "error": code, "detail": detail},
        status_code=status,
        headers=headers,
    )


async def _record_attempt_request(
    request: Request, *, lesson_id: int | None = None, slug: str | None = None
) -> JSONResponse:
    """Shared handler for the id and slug-alias attempt routes. The async
    layer owns body admission (bounded, JSON only); the blocking service
    work runs in the threadpool like every sync route. The B2 perimeter
    middleware already applied its unsafe-method origin policy before this
    runs — same-origin fetch (the D5 bridge parent) and origin-less
    non-browser clients pass; the sandboxed iframe's own `Origin: null`
    can never reach here."""
    length = request.headers.get("content-length")
    if length is None:
        return _attempt_refusal("length-required", 411, "Content-Length is required")
    try:
        expected_len = int(length)
    except ValueError:
        return _attempt_refusal("invalid-request", 400, "bad Content-Length")
    if expected_len < 0:
        return _attempt_refusal("invalid-request", 400, "bad Content-Length")
    if expected_len > _ATTEMPT_MAX_BODY:
        return _attempt_refusal("payload-too-large", 413, "request body too large")
    content_type = request.headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        return _attempt_refusal(
            "unsupported-media-type", 415, "submissions are application/json"
        )
    try:
        body = await read_capped(request, _ATTEMPT_MAX_BODY)
    except PayloadTooLarge:
        return _attempt_refusal("payload-too-large", 413, "request body too large")
    except ValueError:
        return _attempt_refusal("invalid-request", 400, "bad Content-Length")
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        # RecursionError: json.loads on deeply nested input (well under the
        # byte cap) — still just a malformed body, never a 500. The parser
        # unwinds fully before this handler runs.
        return _attempt_refusal("invalid-json", 400, "body is not valid JSON")
    if not isinstance(payload, dict):
        return _attempt_refusal("invalid-json", 400, "body must be a JSON object")

    def work() -> dict:
        # NOT Depends(get_db) (#24 cut 5): this runs in a threadpool worker,
        # and sqlite3 connections are thread-affine (check_same_thread). A
        # dependency-provided connection is opened in whichever worker
        # resolved the dependency, so using it here would raise as soon as
        # the two threads differ. The connection must be born in the thread
        # that uses it.
        conn = get_conn()
        try:
            if slug is not None:
                lesson = lessons.get_lesson_by_slug(conn, slug)
            else:
                lesson = lessons.get_lesson(conn, lesson_id)
            if lesson is None:
                raise attempts.AttemptError("unknown-lesson", 404, "unknown lesson")
            return attempts.record_attempt(conn, lesson, payload)
        finally:
            conn.close()

    try:
        result = await run_in_threadpool(work)
    except attempts.AttemptError as exc:
        return _attempt_refusal(exc.code, exc.status, exc.detail)
    return JSONResponse(
        {"ok": True, **result}, headers={"Cache-Control": "no-store"}
    )


# Registered before the {lesson_id} route so "by-slug" is never parsed as an id.
@router.post("/learn/lessons/by-slug/{slug}/attempts")
async def post_lesson_attempt_by_slug(request: Request, slug: str):
    return await _record_attempt_request(request, slug=slug)


@router.post("/learn/lessons/{lesson_id}/attempts")
async def post_lesson_attempt(request: Request, lesson_id: int):
    return await _record_attempt_request(request, lesson_id=lesson_id)


# --- lesson assessments (S-DESIGN D-S1, docs/lesson-assessments-api.md) ------

# The submission is small by contract (note ≤ 8 KiB, next_action ≤ 512 B); the
# body cap only has to admit the worst-case JSON escaping of a valid note plus
# the fixed fields. It is a quarter of the attempt cap because assessments
# diagnose BY REFERENCE — nothing here ever carries an attempt body.
_ASSESSMENT_MAX_BODY = 64 * 1024


def _assessment_refusal(code: str, status: int, detail: str = "") -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if status == 429:
        headers["Retry-After"] = str(int(assessments.RATE_WINDOW_SECONDS))
    # JSON accepts escaped lone surrogates in object keys. The strict
    # unknown-field error names those keys, but Starlette deliberately renders
    # JSON with ensure_ascii=False and cannot UTF-8 encode a surrogate. Keep the
    # controlled 400 path total: ordinary Unicode stays unchanged while only
    # unencodable code points become their explicit backslash escape.
    detail = detail.encode("utf-8", "backslashreplace").decode("utf-8")
    return JSONResponse(
        {"ok": False, "error": code, "detail": detail},
        status_code=status,
        headers=headers,
    )


async def _record_assessment_request(
    request: Request, *, lesson_id: int | None = None, slug: str | None = None
) -> JSONResponse:
    """Shared handler for the id and slug-alias assessment routes — the D4
    admission shape, deliberately (the attempt handler owns its own copy and
    stays untouched). The async layer owns body admission (bounded, JSON only);
    the blocking service work runs in the threadpool like every sync route. The
    B2 perimeter middleware already applied its unsafe-method origin policy:
    the lesson-agent shell's origin-less request passes, the sandboxed lesson
    iframe's `Origin: null` never reaches here — the tutor writes verdicts, the
    lesson page must not."""
    # The session write capability (s3), if the caller has one. It is read here
    # and never from the body: the sitting and the lesson it names are the
    # server's own facts about the caller.
    capability_token = request.headers.get(assessments.CAPABILITY_HEADER)
    length = request.headers.get("content-length")
    if length is None:
        return _assessment_refusal("length-required", 411, "Content-Length is required")
    try:
        expected_len = int(length)
    except ValueError:
        return _assessment_refusal("invalid-request", 400, "bad Content-Length")
    if expected_len < 0:
        return _assessment_refusal("invalid-request", 400, "bad Content-Length")
    if expected_len > _ASSESSMENT_MAX_BODY:
        return _assessment_refusal("payload-too-large", 413, "request body too large")
    content_type = request.headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        return _assessment_refusal(
            "unsupported-media-type", 415, "submissions are application/json"
        )
    try:
        body = await read_capped(request, _ASSESSMENT_MAX_BODY)
    except PayloadTooLarge:
        return _assessment_refusal("payload-too-large", 413, "request body too large")
    except ValueError:
        return _assessment_refusal("invalid-request", 400, "bad Content-Length")
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        # RecursionError: json.loads on deeply nested input (well under the
        # byte cap) — still just a malformed body, never a 500.
        return _assessment_refusal("invalid-json", 400, "body is not valid JSON")
    if not isinstance(payload, dict):
        return _assessment_refusal("invalid-json", 400, "body must be a JSON object")

    def work() -> dict:
        # NOT Depends(get_db) (#24 cut 5): this runs in a threadpool worker,
        # and sqlite3 connections are thread-affine (check_same_thread). A
        # dependency-provided connection is opened in whichever worker
        # resolved the dependency, so using it here would raise as soon as
        # the two threads differ. The connection must be born in the thread
        # that uses it.
        conn = get_conn()
        try:
            if slug is not None:
                lesson = lessons.get_lesson_by_slug(conn, slug)
            else:
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
        result = await run_in_threadpool(work)
    except assessments.AssessmentError as exc:
        return _assessment_refusal(exc.code, exc.status, exc.detail)
    return JSONResponse(
        {"ok": True, **result}, headers={"Cache-Control": "no-store"}
    )


# Registered before the {lesson_id} route so "by-slug" is never parsed as an id.
@router.post("/learn/lessons/by-slug/{slug}/assessments")
async def post_lesson_assessment_by_slug(request: Request, slug: str):
    return await _record_assessment_request(request, slug=slug)


@router.post("/learn/lessons/{lesson_id}/assessments")
async def post_lesson_assessment(request: Request, lesson_id: int):
    return await _record_assessment_request(request, lesson_id=lesson_id)


# --- the lesson build step (#161) --------------------------------------------
#
# The agent authors the source and names the packages; this route runs the
# package manager and the bundler, because the rules that make either safe —
# the 30-day release quarantine and the copying cache backend — live on a
# command line only the app writes (`app/services/lesson_build.py`).
#
# No capability token, unlike the assessment write beside it: that one needs a
# server-derived answer to "which sitting is this", while a build names its
# lesson in the path and asserts nothing about who asked. What guards it is the
# B2 perimeter policy every unsafe method already passes — the agent shell's
# origin-less request is allowed, a cross-origin page is refused, and the
# sandboxed lesson iframe's `Origin: null` never gets here, so a lesson page
# cannot rebuild its own lesson.
_BUILD_MAX_BODY = 16 * 1024


def _build_refusal(code: str, status: int, detail: str = "", **fields) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": code, "detail": detail, **fields},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _self_origin(request: Request) -> str | None:
    """This app's own origin, from the accepted socket and never from Host.

    The render gate points a browser at the URL built here. A client-supplied
    Host header would therefore choose which server gets loaded and reported
    on, so the address comes from the ASGI scope's `server` — the local end of
    the connection, filled in by the server from the transport. Same rule, and
    the same wildcard fallback, as the terminal's capability URL.
    """
    server = request.scope.get("server")
    if not server or len(server) < 2:
        return None
    host, port = server[0], server[1]
    if not host or not port:
        return None
    if host in {"0.0.0.0", "::", ""}:
        host = "127.0.0.1"
    if ":" in host:  # bare IPv6 literal needs brackets in a URL authority
        host = f"[{host}]"
    scheme = "https" if request.scope.get("scheme") in {"wss", "https"} else "http"
    return f"{scheme}://{host}:{port}"


@router.post("/learn/lessons/{lesson_id}/build")
async def post_lesson_build(request: Request, lesson_id: int):
    length = request.headers.get("content-length")
    if length is None:
        return _build_refusal("length-required", 411, "Content-Length is required")
    try:
        if int(length) > _BUILD_MAX_BODY:
            return _build_refusal("payload-too-large", 413, "request body too large")
    except ValueError:
        return _build_refusal("invalid-request", 400, "bad Content-Length")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() \
            != "application/json":
        return _build_refusal(
            "unsupported-media-type", 415, "build requests are application/json"
        )
    try:
        body = await read_capped(request, _BUILD_MAX_BODY)
    except PayloadTooLarge:
        return _build_refusal("payload-too-large", 413, "request body too large")
    except ValueError:
        return _build_refusal("invalid-request", 400, "bad Content-Length")
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        return _build_refusal("invalid-json", 400, "body is not valid JSON")
    if not isinstance(payload, dict):
        return _build_refusal("invalid-json", 400, "body must be a JSON object")

    origin = _self_origin(request)
    if origin is None:
        return _build_refusal(
            "no-origin", 503,
            "this app cannot name its own address, so the render gate has "
            "nothing to load",
        )

    def resolve() -> tuple[dict, str, str]:
        # Same threadpool rule as every sync route here: the connection is
        # born in the thread that uses it (#24 cut 5).
        conn = get_conn()
        try:
            lesson = lessons.get_lesson(conn, lesson_id)
            if lesson is None:
                raise lesson_build.BuildError("unknown-lesson", 404, "unknown lesson")
            page = payload.get("page")
            # Absent means "the lesson's current page". A number or an object
            # means the caller tried to choose one and this route could not
            # read it — treating that as absent would render somewhere else and
            # report the result as evidence about the selection.
            if page is not None and not isinstance(page, str):
                raise lesson_build.BuildError(
                    "invalid-request", 400,
                    "`page` must be a string naming a page in this bundle",
                )
            asked = page.strip() if isinstance(page, str) and page.strip() else None
            try:
                # An undeclared page is a fallback `lesson_file_info` handles
                # quietly, but a malformed one such as `../outside.html`
                # raises, and that is a bad request rather than an internal
                # fault. `selected_page_ref` raises on the same input and
                # otherwise says which spelling this bundle's version would
                # have had to resolve to.
                wanted = (
                    lessons.selected_page_ref(lesson, asked)
                    if asked is not None else None
                )
                info = lessons.lesson_file_info(lesson, asked)
            except lessons.LessonError as exc:
                raise lesson_build.BuildError("invalid-request", 400, str(exc)) from exc
            # `lesson_file_info` falls back to the manifest's entry for a name
            # it cannot resolve, which is right for a preview and wrong here:
            # silently rendering a different page than the one asked for would
            # let a build pass on evidence about somewhere else.
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
            # The same route the page's own relative reference resolves to, so
            # the gate can tell "this page loaded the artifact" from "this page
            # loaded cleanly and never mentioned it".
            artifact_url=f"{origin}{_lesson_preview_url(lesson_id, out)}",
        )
    except lesson_build.BuildError as exc:
        return _build_refusal(exc.code, exc.status, exc.detail, **exc.fields)
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
