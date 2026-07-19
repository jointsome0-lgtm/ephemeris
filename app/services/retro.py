"""Retro entries — owner-typed retrospectives over approximate periods (issue #49).

The capture half of the future exp2res feed (docs/retro-spec.md): the owner
describes what a period was about ("Q1 2026", "2026-05-01/2026-06-15") in free
text; a selfos adapter later converts the journaled snapshots for exp2res.
The period grammar — precision / confidence vocabularies, accepted period
formats, range shape rules, rejection of DST-ambiguous naive local times — is
a verbatim stdlib port of exp2res services/time_input.py, so anything accepted
here imports there cleanly. Do not diverge from that module; loosening or
tightening a rule here silently breaks the downstream contract.

period_raw stays the owner-typed truth the adapter ships; period_start /
period_end are ephemeris-local derivations for ordering and display only
(exp2res re-resolves the raw value in its own workspace timezone). Entries are
soft-archived, never hard-deleted, and every write appends a full-snapshot
event in the same transaction — including the entry's uuid, because the JSONL
export carries payloads only (the adapter's dedup key must ride the payload).
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import date as _date, datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..db import app_tz, append_event, now_iso

# Vocabularies mirror exp2res domain/enums.py (TemporalPrecision / TemporalConfidence).
PRECISIONS = ("exact_datetime", "exact_day", "week", "month", "quarter", "year",
              "date_range", "approximate_range", "unknown")
CONFIDENCES = ("low", "medium", "high", "unknown")

_RANGE_PRECISIONS = {"date_range", "approximate_range"}

# Byte budget mirrors exp2res RAW_TEXT_LIMIT; C0 controls except \t \n \r plus
# the C1 block (U+0080–U+009F incl. DEL) are rejected there at capture, so
# reject them here too — in free text AND in structural labels like project.
_TEXT_LIMIT = 1_048_576
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


class RetroError(ValueError):
    """A retro write was rejected (bad period/precision shape, empty text, …)."""


# --- period grammar (port of exp2res services/time_input.py) ----------------


def _zone():
    """The ledger zone as a real IANA zone where possible: APP_TIMEZONE, else
    the zone /etc/localtime points at. The bare-offset fallback
    (datetime.now().astimezone().tzinfo) is last resort only — a fixed offset
    resolves cross-DST anchors with today's offset and can't detect ambiguous
    or nonexistent wall-clock times, which exp2res would then reject."""
    tz = app_tz()
    if tz is not None:
        return tz
    try:
        parts = Path("/etc/localtime").resolve().parts
        if "zoneinfo" in parts:
            return ZoneInfo("/".join(parts[parts.index("zoneinfo") + 1:]))
    except (OSError, ValueError, KeyError):
        pass
    return datetime.now().astimezone().tzinfo


def _resolve_local(value: datetime, zone) -> datetime:
    """Attach the ledger zone to a naive datetime; aware values pass through.
    A naive wall-clock that is ambiguous or nonexistent in the zone (DST fold /
    gap) is refused rather than silently picked — same rule as exp2res."""
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(timezone.utc).astimezone(zone)
        if (round_trip.replace(tzinfo=None) == value
                and candidate.utcoffset() == round_trip.utcoffset()):
            candidates.append(candidate)
    offsets = {candidate.utcoffset() for candidate in candidates}
    if not candidates or len(offsets) != 1:
        raise RetroError(
            "that local time is ambiguous or nonexistent (DST); add an explicit offset")
    return candidates[0]


def _parse_datetime(value: str, zone) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise RetroError(f"invalid time value: {value!r}")
    return _resolve_local(parsed, zone)


def _day_start(day: _date, zone) -> datetime:
    return _resolve_local(datetime.combine(day, datetime.min.time()), zone)


def _named_anchor(value: str, precision: str, zone) -> datetime:
    """'2026' / '2026-05' / 'Q1 2026' / '2026-W23' → the period's first instant.
    A value that misses its precision's named form falls through to full ISO
    parsing — deliberately, because exp2res does the same (acceptance-set
    equality is the contract, not just the canonical spellings)."""
    try:
        if precision == "year" and re.fullmatch(r"\d{4}", value):
            return _day_start(_date(int(value), 1, 1), zone)
        if precision == "month" and re.fullmatch(r"\d{4}-\d{2}", value):
            year, month = (int(part) for part in value.split("-"))
            return _day_start(_date(year, month, 1), zone)
        if precision == "quarter":
            match = re.fullmatch(r"(?:Q([1-4])\s+(\d{4})|(\d{4})-Q([1-4]))", value)
            if match:
                quarter = int(match.group(1) or match.group(4))
                year = int(match.group(2) or match.group(3))
                return _day_start(_date(year, (quarter - 1) * 3 + 1, 1), zone)
        if precision == "week":
            match = re.fullmatch(r"(\d{4})-W(\d{2})", value)
            if match:
                return _day_start(
                    _date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1),
                    zone)
    except ValueError:
        # Out-of-range calendar anchors: month 13, week 99, year 0000.
        raise RetroError(f"invalid time value: {value!r}")
    return _parse_datetime(value, zone)


def parse_period(period: str | None, precision: str) -> tuple[str | None, str | None]:
    """Owner-typed (period, precision) → (start_iso, end_iso) in the ledger zone.
    Raises RetroError on anything exp2res would refuse."""
    if precision not in PRECISIONS:
        raise RetroError(f"unknown precision: {precision!r}")
    period = (period or "").strip()
    if precision == "unknown":
        # Stricter than exp2res (which ignores the value): a typed period with
        # precision 'unknown' is almost certainly a mis-picked dropdown.
        if period:
            raise RetroError("precision 'unknown' takes no period — clear the field")
        return None, None
    if not period:
        raise RetroError("period is required for this precision")
    zone = _zone()
    if precision in _RANGE_PRECISIONS:
        # Endpoints are NOT trimmed: exp2res parses them verbatim, so a space
        # around '/' must fail here too, not ship downstream inside period_raw.
        parts = period.split("/", 1)
        if len(parts) != 2:
            raise RetroError("a range needs start/end separated by '/'")
        start = _parse_datetime(parts[0], zone)
        end = _parse_datetime(parts[1], zone)
        if end <= start:
            raise RetroError("range end must be after its start")
        return start.isoformat(), end.isoformat()
    return _named_anchor(period, precision, zone).isoformat(), None


# --- field hygiene (mirrors exp2res text/label rules) -----------------------


def _clean_text(text: str | None) -> str:
    text = text or ""
    if not text.strip():
        raise RetroError("retro text can’t be empty")
    if len(text.encode("utf-8")) > _TEXT_LIMIT:
        raise RetroError("retro text is too long (1 MB max)")
    if _CONTROL_RE.search(text):
        raise RetroError("retro text contains control characters")
    return text


def _clean_project(project: str | None) -> str | None:
    if project is None or not project.strip():
        return None  # an empty form field means "no project", not an error
    project = project.strip()
    if _CONTROL_RE.search(project):
        raise RetroError("project label contains control characters")
    if not unicodedata.normalize("NFC", project).casefold().strip():
        raise RetroError("project label is blank")
    if len(project.encode("utf-8")) > 16_384:
        raise RetroError("project label is too long")
    return project


def _clean(*, period: str | None, precision: str, confidence: str,
           project: str | None, text: str | None) -> dict:
    if confidence not in CONFIDENCES:
        raise RetroError(f"unknown confidence: {confidence!r}")
    start, end = parse_period(period, precision)
    return {
        "period_raw": (period or "").strip(),
        "precision": precision,
        "confidence": confidence,
        "period_start": start,
        "period_end": end,
        "project": _clean_project(project),
        "text": _clean_text(text),
    }


# --- writes ----------------------------------------------------------------

_COLS = ("period_raw", "precision", "confidence", "period_start", "period_end",
         "project", "text")


def _snapshot(row: sqlite3.Row) -> dict:
    """The full-entry event payload. Everything the future adapter needs rides
    here — the export serializes payloads only, so this is the wire format."""
    return {
        "retro_uuid": row["uuid"],
        "retro_id": row["id"],
        "period_raw": row["period_raw"],
        "precision": row["precision"],
        "confidence": row["confidence"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "project": row["project"],
        "text": row["text"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "archived_at": row["archived_at"],
    }


def create_entry(conn: sqlite3.Connection, *, period: str | None, precision: str,
                 confidence: str, text: str, project: str | None = None) -> sqlite3.Row:
    c = _clean(period=period, precision=precision, confidence=confidence,
               project=project, text=text)
    entry_uuid = str(uuid4())
    ts = now_iso()
    with conn:
        cur = conn.execute(
            f"INSERT INTO retro_entries (uuid, {', '.join(_COLS)}, created_at) "
            f"VALUES ({', '.join('?' * (len(_COLS) + 2))})",
            [entry_uuid, *(c[k] for k in _COLS), ts],
        )
        row = get_entry(conn, cur.lastrowid)
        append_event(conn, "retro_entry_created", _snapshot(row))
    return row


def update_entry(conn: sqlite3.Connection, entry_id: int, *, period: str | None,
                 precision: str, confidence: str, text: str,
                 project: str | None = None) -> sqlite3.Row:
    """Full-form re-submit: every editable field is validated and written; the
    uuid never changes, so downstream latest-wins consumers follow the edit."""
    if get_entry(conn, entry_id) is None:
        raise RetroError("unknown retro entry")
    c = _clean(period=period, precision=precision, confidence=confidence,
               project=project, text=text)
    ts = now_iso()
    with conn:
        conn.execute(
            f"UPDATE retro_entries SET {', '.join(k + '=?' for k in _COLS)}, "
            "updated_at=? WHERE id=?",
            [*(c[k] for k in _COLS), ts, entry_id],
        )
        row = get_entry(conn, entry_id)
        append_event(conn, "retro_entry_updated", _snapshot(row))
    return row


def archive_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    """Soft-delete (sets archived_at); the entry leaves the default list but
    stays joinable to its events and restorable via unarchive. The state check
    rides the UPDATE itself so a concurrent double-archive stays an idempotent
    no-op instead of stamping twice and journaling a duplicate event."""
    if get_entry(conn, entry_id) is None:
        raise RetroError("unknown retro entry")
    ts = now_iso()
    with conn:
        cur = conn.execute(
            "UPDATE retro_entries SET archived_at = ? "
            "WHERE id = ? AND archived_at IS NULL", (ts, entry_id))
        if cur.rowcount == 0:
            return  # already archived — idempotent, no event
        append_event(conn, "retro_entry_archived", _snapshot(get_entry(conn, entry_id)))


def unarchive_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    if get_entry(conn, entry_id) is None:
        raise RetroError("unknown retro entry")
    with conn:
        cur = conn.execute(
            "UPDATE retro_entries SET archived_at = NULL "
            "WHERE id = ? AND archived_at IS NOT NULL", (entry_id,))
        if cur.rowcount == 0:
            return  # not archived — idempotent, no event
        append_event(conn, "retro_entry_unarchived", _snapshot(get_entry(conn, entry_id)))


def get_entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM retro_entries WHERE id = ?", (entry_id,)).fetchone()


def list_entries(conn: sqlite3.Connection, include_archived: bool = False) -> list[sqlite3.Row]:
    """Newest period first; entries with an unknown period sink to the end.
    datetime() normalizes the stored offsets to UTC instants, so an entry whose
    range was typed with an explicit non-local offset still sorts by real time,
    not by string order."""
    q = "SELECT * FROM retro_entries"
    if not include_archived:
        q += " WHERE archived_at IS NULL"
    q += (" ORDER BY (period_start IS NULL), datetime(period_start) DESC,"
          " created_at DESC, id DESC")
    return conn.execute(q).fetchall()
