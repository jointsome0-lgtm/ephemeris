"""Export routes (#24 cut 4): the event-stream + calendar-series JSONL backup
(sec15.4 / sec18.1).

A pure move out of app/main.py. The block was contiguous there, so it is one
router included with no prefix at the position it occupied. URLs and
registration order are unchanged.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from ..db import get_db
from ..services import export
from ..templating import templates

router = APIRouter()  # GET /export, POST /export/jsonl


@router.get("/export")
def get_export(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """One-button export page: shows the event count + recent export files."""
    count = export.event_count(conn)
    return templates.TemplateResponse(request,
        "export.html",
        {"request": request, "rail": "export",
         "event_count": count, "recent": export.recent_exports()},
    )


@router.post("/export/jsonl")
def post_export_jsonl(request: Request,
                      conn: sqlite3.Connection = Depends(get_db)):
    """Write data/exports/events-<stamp>.jsonl AND stream it back as a download."""
    path, text, _count = export.export_events(conn)
    return Response(
        content=text,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )
