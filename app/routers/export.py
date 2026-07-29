"""Export routes (#24 cut 4): the event-stream + calendar-series JSONL backup
(sec15.4 / sec18.1).

A pure move out of app/main.py. The block was contiguous there, so it is one
router included with no prefix at the position it occupied. URLs and
registration order are unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..db import get_conn
from ..services import export
from ..templating import templates

router = APIRouter()  # GET /export, POST /export/jsonl


@router.get("/export")
def get_export(request: Request):
    """One-button export page: shows the event count + recent export files."""
    conn = get_conn()
    try:
        count = export.event_count(conn)
    finally:
        conn.close()
    return templates.TemplateResponse(request,
        "export.html",
        {"request": request, "rail": "export",
         "event_count": count, "recent": export.recent_exports()},
    )


@router.post("/export/jsonl")
def post_export_jsonl(request: Request):
    """Write data/exports/events-<stamp>.jsonl AND stream it back as a download."""
    conn = get_conn()
    try:
        path, text, _count = export.export_events(conn)
    finally:
        conn.close()
    return Response(
        content=text,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )
