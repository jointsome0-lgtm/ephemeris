"""Items, habits, tasks, focus, export, calendar, and terminal verification."""

from __future__ import annotations

import hashlib
import importlib.abc as _importlib_abc
import json
import os
import sqlite3
import stat as stat_module
import subprocess
import sys
import tempfile
import threading
from datetime import date as _vdate
from pathlib import Path

from conftest import ROOT, events_of, item_row


def test_core_surfaces(client, suite_state):
    from app.db import SCHEMA_VERSION, get_conn, pretty_date, today_str
    from app.main import app

    c = client
    today = suite_state["today"]
    # --- CREATE ---------------------------------------------------------
    n_before = len(events_of("routine_item_created"))
    r = c.post(
        "/items",
        data={"title": "Stretch", "group_name": "Mobility"},
        follow_redirects=False,
    )
    assert r.status_code == 303, "POST /items 303" + "  -- " + (str(r.status_code))
    created = events_of("routine_item_created")
    assert len(created) == n_before + 1, "create event appended"
    conn = get_conn()
    new = conn.execute("SELECT * FROM routine_items WHERE title = 'Stretch'").fetchone()
    conn.close()
    assert new is not None, "new item persisted"
    assert new is not None and new["active"] == 1, "new item active"
    assert new is not None and new["group_name"] == "Mobility", "new item group"
    assert new is not None and new["sort_order"] == 10, (
        "new item sort_order = 10 (first in group)"
        + "  -- "
        + (str(new["sort_order"] if new else "?"))
    )
    nid = new["id"]

    # second item in same group -> sort_order should advance to 20
    c.post(
        "/items",
        data={"title": "Foam roll", "group_name": "Mobility"},
        follow_redirects=False,
    )
    conn = get_conn()
    second = conn.execute(
        "SELECT * FROM routine_items WHERE title = 'Foam roll'"
    ).fetchone()
    conn.close()
    assert second["sort_order"] == 20, (
        "second item sort_order = 20" + "  -- " + (str(second["sort_order"]))
    )

    # empty title rejected (flash redirect, no row)
    r = c.post(
        "/items", data={"title": "   ", "group_name": "X"}, follow_redirects=False
    )
    assert r.status_code == 303, "empty title -> 303 redirect"
    assert "flash=" in r.headers.get("location", ""), "empty title -> flash"
    conn = get_conn()
    xcount = conn.execute(
        "SELECT COUNT(*) FROM routine_items WHERE group_name = 'X'"
    ).fetchone()[0]
    conn.close()
    assert xcount == 0, "empty title -> no row created"

    # --- EDIT -----------------------------------------------------------
    nu_before = len(events_of("routine_item_updated"))
    r = c.post(
        f"/items/{nid}/edit",
        data={"title": "Stretch & breathe", "group_name": "Mobility"},
        follow_redirects=False,
    )
    assert r.status_code == 303, "POST edit 303"
    row = item_row(nid)
    assert row["title"] == "Stretch & breathe", (
        "title updated" + "  -- " + (row["title"])
    )
    assert row["updated_at"] is not None, "updated_at set"
    assert len(events_of("routine_item_updated")) == nu_before + 1, (
        "update event appended"
    )

    # edit unknown id -> flash, no crash
    r = c.post(
        "/items/999999/edit",
        data={"title": "x", "group_name": "y"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "flash=" in r.headers.get("location", ""), (
        "edit unknown id -> 303 flash"
    )

    # --- DEACTIVATE (soft) ----------------------------------------------
    nd_before = len(events_of("routine_item_deactivated"))
    r = c.post(f"/items/{nid}/deactivate", follow_redirects=False)
    assert r.status_code == 303, "POST deactivate 303"
    row = item_row(nid)
    assert row["active"] == 0, "item now inactive"
    assert row["deactivated_at"] is not None, "deactivated_at set"
    assert len(events_of("routine_item_deactivated")) == nd_before + 1, (
        "deactivate event appended"
    )
    assert row is not None, "row still exists (soft delete)"

    # deactivated item hidden from Today, shown as inactive on Items
    r = c.get("/today")
    assert "Stretch & breathe" not in r.text, "deactivated hidden from Today"
    r = c.get("/items")
    # Title has an "&" -> Jinja autoescapes to "&amp;" in HTML (security: confirms
    # autoescaping is on). DB keeps the raw value (asserted above).
    assert "Stretch &amp; breathe" in r.text, "deactivated shown on Items"
    assert "Deactivated" in r.text, "items shows Deactivated section"
    assert "Stretch & breathe" not in r.text, "autoescape on (no raw & in title)"

    # --- REACTIVATE -----------------------------------------------------
    r = c.post(f"/items/{nid}/reactivate", follow_redirects=False)
    assert r.status_code == 303, "POST reactivate 303"
    row = item_row(nid)
    assert row["active"] == 1, "item active again"
    assert row["deactivated_at"] is None, "deactivated_at cleared"
    r = c.get("/today")
    assert "Stretch &amp; breathe" in r.text, "reactivated visible on Today"

    # --- §16.4 write contract still holds -------------------------------
    # toggle full_done on, then off (toggle-to-clear)
    r = c.post(
        "/checkins",
        data={"date": today, "routine_item_id": nid, "status": "full_done"},
        headers={"X-Partial": "1"},
    )
    assert r.status_code == 200 and r.json()["status"] == "full_done", (
        "checkin full_done JSON ok"
    )
    r = c.post(
        "/checkins",
        data={"date": today, "routine_item_id": nid, "status": "full_done"},
        headers={"X-Partial": "1"},
    )
    assert r.json()["status"] is None, "toggle-to-clear -> status None"

    # future date rejected
    r = c.post(
        "/checkins",
        data={"date": "2999-01-01", "routine_item_id": nid, "status": "full_done"},
        follow_redirects=False,
    )
    assert r.status_code == 400, "future date -> 400" + "  -- " + (str(r.status_code))

    # light_done allowed (the differentiator)
    r = c.post(
        "/checkins",
        data={"date": today, "routine_item_id": nid, "status": "light_done"},
        headers={"X-Partial": "1"},
    )
    assert r.status_code == 200 and r.json()["status"] == "light_done", (
        "light_done accepted"
    )

    # daily note
    r = c.post(
        "/daily-note",
        data={"date": today, "text": "good day"},
        headers={"X-Partial": "1"},
    )
    assert r.status_code == 200 and r.json()["ok"] is True, "daily-note JSON ok"

    # cross-origin POST rejected
    r = c.post(
        "/items",
        data={"title": "Evil", "group_name": "x"},
        headers={"Origin": "http://evil.example", "Host": "testserver"},
        follow_redirects=False,
    )
    assert r.status_code == 403, (
        "cross-origin POST -> 403" + "  -- " + (str(r.status_code))
    )

    # --- central write guard + host perimeter (issue #15 slice) ----------
    # A brand-new route with NO guard code of its own must still be covered:
    # the middleware in app/security.py owns the policy, not the handler.
    @app.post("/verify-only/unguarded")
    def _unguarded_probe():
        return {"ok": True}

    r = c.post(
        "/verify-only/unguarded",
        headers={"Origin": "http://evil.example", "Host": "testserver"},
    )
    assert r.status_code == 403, (
        "guard: unguarded new route still rejects cross-origin"
        + "  -- "
        + (str(r.status_code))
    )
    r = c.post("/verify-only/unguarded", headers={"Origin": "null"})
    assert r.status_code == 403, (
        "guard: opaque origin (Origin: null) -> 403" + "  -- " + (str(r.status_code))
    )
    r = c.post(
        "/verify-only/unguarded",
        headers=[("Origin", "http://testserver"), ("Origin", "http://evil.example")],
    )
    assert r.status_code == 403, (
        "guard: smuggled duplicate Origin -> 403" + "  -- " + (str(r.status_code))
    )
    r = c.post("/verify-only/unguarded", headers={"Origin": "http://testserver"})
    assert r.status_code == 200 and r.json()["ok"] is True, (
        "guard: same-origin Origin accepted" + "  -- " + (str(r.status_code))
    )
    r = c.post("/verify-only/unguarded", headers={"Origin": "https://testserver"})
    assert r.status_code == 403, (
        "guard: scheme mismatch (https origin, http app) -> 403"
        + "  -- "
        + (str(r.status_code))
    )
    r = c.post("/verify-only/unguarded", headers={"Origin": "http://testserver:80"})
    assert r.status_code == 200, (
        "guard: default port normalized to the same origin"
        + "  -- "
        + (str(r.status_code))
    )
    r = c.post("/verify-only/unguarded", headers={"Origin": "http://testserver/x"})
    assert r.status_code == 403, (
        "guard: non-serialized Origin (path) -> 403" + "  -- " + (str(r.status_code))
    )
    r = c.post("/verify-only/unguarded")
    assert r.status_code == 200, (
        "guard: no-Origin non-browser client accepted" + "  -- " + (str(r.status_code))
    )
    r = c.post("/verify-only/unguarded", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403, (
        "guard: absent Origin but Sec-Fetch-Site: cross-site -> 403"
        + "  -- "
        + (str(r.status_code))
    )
    r = c.post("/verify-only/unguarded", headers={"Sec-Fetch-Site": "same-site"})
    assert r.status_code == 403, (
        "guard: Sec-Fetch-Site: same-site (another local port) -> 403"
        + "  -- "
        + (str(r.status_code))
    )
    r = c.post("/verify-only/unguarded", headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200, (
        "guard: Sec-Fetch-Site: same-origin accepted" + "  -- " + (str(r.status_code))
    )

    # Trusted-host allowlist covers every method, GET included (DNS rebinding)
    r = c.get("/today", headers={"Host": "evil.example"})
    assert r.status_code == 400, (
        "perimeter: untrusted Host -> 400" + "  -- " + (str(r.status_code))
    )
    r = c.get("/today", headers={"Host": "[::1]:8765"})
    assert r.status_code == 200, (
        "perimeter: bracketed IPv6 loopback Host accepted"
        + "  -- "
        + (str(r.status_code))
    )
    r = c.get("/today")
    assert (
        r.headers.get("x-content-type-options") == "nosniff"
        and r.headers.get("referrer-policy") == "same-origin"
        and r.headers.get("content-security-policy") == "frame-ancestors 'none'"
    ), (
        "perimeter: security headers on every response"
        + "  -- "
        + (str(dict(r.headers)))
    )
    r = c.get("/static/style.css")
    assert r.headers.get("x-content-type-options") == "nosniff", (
        "perimeter: headers reach mounted static files"
    )

    # --- habit stats: streaks / weekly dots / detail page ---------------
    from datetime import date as _d, timedelta as _td
    from app.services import stats as _stats
    from app.db import get_conn as _gc, today_str as _ts

    c.post(
        "/items",
        data={"title": "Streaky", "group_name": "Mobility"},
        follow_redirects=False,
    )
    conn = _gc()
    sid = conn.execute("SELECT id FROM routine_items WHERE title='Streaky'").fetchone()[
        "id"
    ]
    t0 = _d.fromisoformat(_ts())
    # offset-from-today -> status. light keeps the chain; skip is neutral; fail breaks.
    seed = {
        0: "full_done",
        1: "light_done",
        2: "skipped",
        3: "full_done",
        4: "failed",
        5: "full_done",
    }
    for off, st in seed.items():
        dd = (t0 - _td(days=off)).isoformat()
        conn.execute(
            "INSERT INTO checkins (date, routine_item_id, status, note, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (dd, sid, st, ("kept it light" if off == 1 else None), "x", "x"),
        )
    # The invented history reaches back six days, so the habit has to have
    # existed for them: since #18 a start_date bounds where statistics begin,
    # and create_item defaults it to today (POST /items above).
    conn.execute(
        "UPDATE routine_items SET start_date = ? WHERE id = ?",
        ((t0 - _td(days=max(seed))).isoformat(), sid),
    )
    conn.commit()
    smap = _stats.history(conn, sid)
    cur = _stats.current_streak_from(smap, t0)
    best = _stats.best_streak_from(smap, t0)
    total = _stats.total_checkins(conn, sid)
    conn.close()
    assert len(smap) == 6, "history has 6 entries" + "  -- " + (str(len(smap)))
    assert cur == 3, (
        "current streak = 3 (full,light,skip,full | fail breaks)" + "  -- " + (str(cur))
    )
    assert best == 3, "best streak = 3" + "  -- " + (str(best))
    assert total == 4, "total kept = 4 (full+light only)" + "  -- " + (str(total))

    # detail page renders with numbers + heatmap + log
    r = c.get(f"/habit/{sid}")
    assert r.status_code == 200, "GET /habit 200" + "  -- " + (str(r.status_code))
    assert "Streaky" in r.text, "detail shows title"
    assert (
        "Monthly check-ins" in r.text
        and "Total Check-Ins" in r.text
        and "Monthly check-in rate" in r.text
        and "Current Streak" in r.text
    ), "detail has stat cards (TickTick labels)"
    assert "Habit Log on" in r.text, "detail has Habit Log heading"
    assert "cal-grid" in r.text and "cal-cell" in r.text, "detail has monthly heatmap"
    assert "cal-cell today done" in r.text or ' done"' in r.text, (
        "detail heatmap has a checked-in cell"
    )
    assert "kept it light" in r.text, "detail has habit log note"
    assert "cal-arrow disabled" in r.text, "detail next-month disabled this month"
    r = c.get(f"/habit/{sid}?month=2020-01")
    assert r.status_code == 200 and "?month=2020-02" in r.text, (
        "detail past month 200 + next enabled"
    )
    r = c.get("/habit/999999")
    assert r.status_code == 404, (
        "GET /habit unknown -> 404" + "  -- " + (str(r.status_code))
    )
    r = c.get("/habit/abc")
    assert r.status_code == 422, (
        "GET /habit non-int -> 422" + "  -- " + (str(r.status_code))
    )

    # Habit tab rows: streak + a TickTick-style circular check-in ring
    r = c.get("/habits")
    assert "hl-check" in r.text, "habits row has check-in ring"
    assert "data-streak-cur" in r.text, "habits row has streak stat"
    assert "data-dot" in r.text, "habits row has full_done affordance"

    # check-in JSON now carries recomputed streaks for live update
    r = c.post(
        "/checkins",
        data={"date": _ts(), "routine_item_id": sid, "status": "full_done"},
        headers={"X-Partial": "1"},
    )
    body = r.json()
    assert "current_streak" in body and "best_streak" in body, (
        "checkin JSON carries streaks" + "  -- " + (str(body))
    )
    # toggled today's full_done OFF -> today pending; streak now 2 (light,full kept; fail breaks)
    assert body["current_streak"] == 2, (
        "streak recomputed after clear = 2"
        + "  -- "
        + (str(body.get("current_streak")))
    )

    # --- tasks / lists / smart lists (sec21) ----------------------------
    from app.services import lists as _lists

    r = c.get("/today")
    assert ">Completed<" in r.text, "today has Completed section"
    assert 'action="/tasks"' in r.text, "today quick-add posts to /tasks"
    assert ">Inbox<" in r.text, "list-sidebar shows Inbox"
    assert "Shopping" in r.text, "list-sidebar shows a user list (Shopping)"

    conn = _gc()
    inbox = _lists.inbox_id(conn)
    conn.close()

    # CREATE a task -> row + event in one txn
    nt_before = len(events_of("task_created"))
    r = c.post(
        "/tasks",
        data={"title": "Pay rent", "list_id": inbox, "return_to": "/today"},
        follow_redirects=False,
    )
    assert r.status_code == 303, "POST /tasks 303" + "  -- " + (str(r.status_code))
    assert len(events_of("task_created")) == nt_before + 1, (
        "task_created event appended"
    )
    conn = _gc()
    trow = conn.execute("SELECT * FROM tasks WHERE title = 'Pay rent'").fetchone()
    conn.close()
    assert trow is not None and trow["list_id"] == inbox, "task persisted in Inbox"
    tid = trow["id"]

    # empty title rejected (flash, no row)
    r = c.post(
        "/tasks",
        data={"title": "   ", "list_id": inbox, "return_to": "/today"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "flash=" in r.headers.get("location", ""), (
        "empty task title -> flash redirect"
    )

    # detail pane renders the editor inline (?sel=task-N)
    r = c.get(f"/today?sel=task-{tid}")
    assert 'class="dp-form"' in r.text and "Pay rent" in r.text, (
        "task detail pane renders editor"
    )

    # complete is a reversible toggle (Mode B JSON)
    r = c.post(
        f"/tasks/{tid}/complete",
        data={"return_to": "/today"},
        headers={"X-Partial": "1"},
    )
    assert r.status_code == 200 and r.json()["completed"] is True, (
        "task complete JSON ok"
    )
    r = c.post(
        f"/tasks/{tid}/complete",
        data={"return_to": "/today"},
        headers={"X-Partial": "1"},
    )
    assert r.json()["completed"] is False, "task reopen toggles back"

    # UPDATE: note + due + priority + list
    r = c.post(
        f"/tasks/{tid}/update",
        data={
            "title": "Pay rent",
            "note": "via bank app",
            "due_date": today,
            "priority": "2",
            "list_id": inbox,
            "return_to": "/today",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, "POST task update 303" + "  -- " + (str(r.status_code))
    conn = _gc()
    trow = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    conn.close()
    assert trow["priority"] == 2, (
        "task priority updated" + "  -- " + (str(trow["priority"]))
    )
    assert trow["due_date"] == today, (
        "task due_date updated" + "  -- " + (str(trow["due_date"]))
    )

    # the now-due task surfaces in Today's Tasks section
    r = c.get("/today")
    assert "Pay rent" in r.text, "due-today task shows on Today"

    # smart lists + per-list view render
    for path in ("/next7", "/completed"):
        rr = c.get(path)
        assert rr.status_code == 200, (
            f"GET {path} 200" + "  -- " + (str(rr.status_code))
        )
    conn = _gc()
    lid = conn.execute("SELECT id FROM lists WHERE name = 'Shopping'").fetchone()["id"]
    conn.close()
    r = c.get(f"/list/{lid}")
    assert r.status_code == 200 and "Buy groceries" in r.text, (
        "GET /list 200 + shows its task"
    )
    r = c.get("/list/999999")
    assert r.status_code == 404, (
        "GET /list unknown -> 404" + "  -- " + (str(r.status_code))
    )

    # cross-origin task POST rejected (same guard as items/checkins)
    r = c.post(
        "/tasks",
        data={"title": "Evil", "list_id": inbox},
        headers={"Origin": "http://evil.example", "Host": "testserver"},
        follow_redirects=False,
    )
    assert r.status_code == 403, (
        "cross-origin POST /tasks -> 403" + "  -- " + (str(r.status_code))
    )

    # --- Habit tab: pane + create / edit / archive / delete (sec31) -------
    r = c.get("/habits?sel=habit-1")
    # pane has NO check-in button (TickTick-faithful: the list row's ring is the
    # check-in affordance); the button lives on the standalone full page only.
    assert "Check in for today" not in r.text, "habit pane: no check-in button"
    assert "Check in for today" in c.get("/habit/1").text, (
        "habit full page: has check-in control"
    )
    assert "cal-grid" in r.text and "cal-cell" in r.text, "habit pane: monthly calendar"
    assert "Monthly check-ins" in r.text and "Total Check-Ins" in r.text, (
        "habit pane: TickTick stat cards"
    )
    assert "rowmenu" in r.text and "/habits/1/delete" in r.text, (
        "habit pane: ⋯ menu w/ delete"
    )
    r = c.get("/habits?sel=habit-1&edit=1")
    assert 'class="habit-form"' in r.text and "Start Date" in r.text, (
        "habit pane: edit form"
    )
    assert "Frequency" not in r.text and 'name="reminder"' not in r.text, (
        "habit pane: edit form drops the controls nothing reads (#18)"
    )

    # CREATE with the field set the form still offers (#18)
    nh_before = len(events_of("routine_item_created"))
    r = c.post(
        "/habits",
        data={
            "title": "Meditate",
            "group_name": "Morning",
            "emoji": "🧘",
            "start_date": "2026-06-01",
            "return_to": "/habits",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, (
        "POST /habits create 303" + "  -- " + (str(r.status_code))
    )
    assert len(events_of("routine_item_created")) == nh_before + 1, (
        "habit create event appended"
    )
    conn = _gc()
    hb = conn.execute("SELECT * FROM routine_items WHERE title = 'Meditate'").fetchone()
    conn.close()
    assert (
        hb is not None
        and hb["emoji"] == "🧘"
        and hb["start_date"] == "2026-06-01"
    ), "habit persisted with the fields the form offers"
    # the legacy columns still exist and take their defaults (#18)
    assert (
        hb["frequency"] == "daily" and hb["goal"] == "achieve_all"
        and hb["goal_days"] == "forever" and hb["reminder"] is None
        and hb["constant_reminder"] == 0
    ), "unposted legacy columns fall back to their defaults"
    hid = hb["id"]
    page = c.get("/habits").text
    assert "Meditate" in page and "Morning" in page, "new habit shows in its section"

    # empty title rejected
    r = c.post(
        "/habits",
        data={"title": "   ", "group_name": "Morning"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "flash=" in r.headers.get("location", ""), (
        "empty habit title -> flash redirect"
    )

    # EDIT: only supplied fields change. A pre-#18 row carrying legacy values
    # must keep them — the trimmed form omits those names, and update_item's
    # _UNSET has to preserve rather than reset them.
    conn = _gc()
    with conn:
        conn.execute(
            "UPDATE routine_items SET frequency='weekdays', reminder='07:30', "
            "constant_reminder=1 WHERE id = ?", (hid,)
        )
    conn.close()
    r = c.post(
        f"/habits/{hid}/edit",
        data={
            "title": "Meditate 10m",
            "group_name": "Morning",
            "emoji": "🧘",
            "start_date": "2026-06-02",
            "return_to": "/habits",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, "POST habit edit 303"
    conn = _gc()
    hb = conn.execute(
        "SELECT title, start_date, frequency, reminder, constant_reminder "
        "FROM routine_items WHERE id = ?", (hid,)
    ).fetchone()
    conn.close()
    assert hb["title"] == "Meditate 10m" and hb["start_date"] == "2026-06-02", (
        "habit edited"
    )
    assert (
        hb["frequency"] == "weekdays"
        and hb["reminder"] == "07:30"
        and hb["constant_reminder"] == 1
    ), "editing through the trimmed form does not erase pre-#18 legacy values"

    # pane Today check-in round-trips and reflects in the pane
    r = c.post(
        "/checkins",
        data={
            "date": today,
            "routine_item_id": hid,
            "status": "full_done",
            "return_to": f"/habits?sel=habit-{hid}",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and f"sel=habit-{hid}" in r.headers.get(
        "location", ""
    ), "pane check-in 303 -> stays on pane"
    # the pane reflects the check-in in its monthly calendar (today cell marked done)
    assert "cal-cell today done" in c.get(f"/habits?sel=habit-{hid}").text, (
        "pane reflects checked status (calendar)"
    )

    # ARCHIVE (soft): hidden from the tab, row kept
    r = c.post(
        f"/habits/{hid}/archive", data={"return_to": "/habits"}, follow_redirects=False
    )
    assert r.status_code == 303, "POST habit archive 303"
    conn = _gc()
    arow = conn.execute(
        "SELECT active FROM routine_items WHERE id = ?", (hid,)
    ).fetchone()
    conn.close()
    assert arow is not None and arow["active"] == 0, "archived habit inactive but kept"
    assert "Meditate 10m" not in c.get("/habits").text, "archived habit hidden from tab"

    # DELETE (hard): row + check-ins gone, audit event kept
    c.post(
        "/habits",
        data={"title": "Temp habit", "group_name": "Morning"},
        follow_redirects=False,
    )
    conn = _gc()
    tmp = conn.execute(
        "SELECT id FROM routine_items WHERE title = 'Temp habit'"
    ).fetchone()["id"]
    conn.close()
    c.post(
        "/checkins",
        data={"date": today, "routine_item_id": tmp, "status": "full_done"},
        follow_redirects=False,
    )
    ndel_before = len(events_of("routine_item_deleted"))
    r = c.post(
        f"/habits/{tmp}/delete", data={"return_to": "/habits"}, follow_redirects=False
    )
    assert r.status_code == 303, "POST habit delete 303"
    conn = _gc()
    gone = conn.execute("SELECT id FROM routine_items WHERE id = ?", (tmp,)).fetchone()
    leftover = conn.execute(
        "SELECT COUNT(*) FROM checkins WHERE routine_item_id = ?", (tmp,)
    ).fetchone()[0]
    conn.close()
    assert gone is None, "deleted habit row gone"
    assert leftover == 0, "deleted habit check-ins removed"
    assert len(events_of("routine_item_deleted")) == ndel_before + 1, (
        "delete event appended (audit kept)"
    )

    # cross-origin habit create rejected
    r = c.post(
        "/habits",
        data={"title": "Evil", "group_name": "x"},
        headers={"Origin": "http://evil.example", "Host": "testserver"},
        follow_redirects=False,
    )
    assert r.status_code == 403, (
        "cross-origin POST /habits -> 403" + "  -- " + (str(r.status_code))
    )

    # --- Focus: one server-owned timer, no page (#75, #20) -------------------
    # The drawer rides on every surface, so the shell is what carries it now.
    shell = c.get("/today").text
    assert 'id="timer-drawer"' in shell, "the timer drawer ships with the shell"
    assert c.get("/focus").status_code == 404, "the Focus page is gone, not redirected"

    state = c.get("/focus/timer").json()
    assert state["run"] is None and state["overview"]["today_seconds"] == 0, (
        "no timer running, nothing focused yet"
    )

    nf_before = len(events_of("focus_session_recorded"))
    r = c.post("/focus/timer/start",
               data={"token": "tok-a", "mode": "countdown", "target_seconds": 1500},
               headers={"X-Partial": "1"})
    assert r.status_code == 200 and r.json()["ok"] is True, "timer start JSON ok"
    run = r.json()["run"]
    assert run["mode"] == "countdown" and run["target_seconds"] == 1500, (
        "the run carries the length the user chose" + "  -- " + str(run)
    )
    assert run["elapsed"] < 5, "a fresh run has barely elapsed"

    # the token is the idempotency key: a retried start must not open a second
    # timer, and a DIFFERENT token while one runs is a mistake, not a second run
    same = c.post("/focus/timer/start",
                  data={"token": "tok-a", "mode": "countdown", "target_seconds": 1500},
                  headers={"X-Partial": "1"}).json()
    assert same["run"]["id"] == run["id"], "retrying a start reuses the run"
    second = c.post("/focus/timer/start",
                    data={"token": "tok-b", "mode": "open"},
                    headers={"X-Partial": "1"})
    assert second.status_code == 422 and "already running" in second.json()["error"], (
        "a second concurrent timer is refused"
    )

    # pause/resume is server state too, so it survives the page load that the
    # drawer is designed to outlive
    paused = c.post("/focus/timer/pause", data={"token": "tok-a", "paused": 1},
                    headers={"X-Partial": "1"}).json()
    assert paused["run"]["paused"] is True, "the run reports itself paused"
    resumed = c.post("/focus/timer/pause", data={"token": "tok-a", "paused": 0},
                     headers={"X-Partial": "1"}).json()
    assert resumed["run"]["paused"] is False, "resume clears the pause"

    def _backdate(token, seconds):
        """Move a run's start into the past — the only way to make the SERVER's
        clock produce a duration inside a test, which is the point: nothing in
        the request can set one."""
        from datetime import datetime as _dtm, timedelta as _td

        from app.db import now_iso as _ni
        started = (_dtm.fromisoformat(_ni())
                   - _td(seconds=seconds)).isoformat(timespec="seconds")
        cx = get_conn()
        try:
            with cx:
                cx.execute("UPDATE focus_runs SET started_at = ?, paused_seconds = 0, "
                           "paused_at = NULL WHERE client_token = ?", (started, token))
        finally:
            cx.close()

    # a countdown left running past its length records the length, not the wait
    _backdate("tok-a", 40 * 60)
    done = c.post("/focus/timer/finish", data={"token": "tok-a"},
                  headers={"X-Partial": "1"})
    assert done.status_code == 200, "finish accepted"
    body = done.json()
    assert body["run"] is None, "finishing clears the running timer"
    assert body["recorded"]["seconds"] == 1500, (
        "a 25m countdown records 25m however long the tab stayed open"
        + "  -- " + str(body["recorded"])
    )
    assert body["recorded"]["duration_label"] == "25m", "record row reads 25m"
    assert body["overview"]["today_focus"]["value"] == 25, "today's focus is 25m"
    assert len(events_of("focus_session_recorded")) == nf_before + 1, (
        "focus_session_recorded event appended"
    )

    # ...and it is dated when it ran out, not when the user came back to it: the
    # 25m ran out 15m ago, so ended_at is back then. A laptop reopened after
    # midnight must not move yesterday's session onto today's bar.
    cx = get_conn()
    try:
        ended = cx.execute("SELECT ended_at, created_at, date FROM focus_sessions "
                           "WHERE id = ?", (body["recorded"]["id"],)).fetchone()
    finally:
        cx.close()
    from datetime import datetime as _dtm

    from app.db import now_iso as _ni
    lag = (_dtm.fromisoformat(_ni()) - _dtm.fromisoformat(ended["ended_at"])).total_seconds()
    assert 890 <= lag <= 910, (
        "a capped countdown ends when it ran out, not when it was stopped"
        + "  -- " + str(ended["ended_at"])
    )
    assert ended["date"] == ended["ended_at"][:10], "the day follows the end, not the write"
    assert ended["created_at"] > ended["ended_at"], "written down after it happened"

    # a retried finish returns the same session instead of counting it twice
    again = c.post("/focus/timer/finish", data={"token": "tok-a"},
                   headers={"X-Partial": "1"}).json()
    assert again["recorded"]["id"] == body["recorded"]["id"], "finish is idempotent"
    assert len(events_of("focus_session_recorded")) == nf_before + 1, (
        "the retried finish appends no second event"
    )

    # A spent token cannot open a second timer: it would sit in the single slot
    # forever, since the session it already recorded owns the token a finish
    # would need.
    spent = c.post("/focus/timer/start", data={"token": "tok-a", "mode": "open"},
                   headers={"X-Partial": "1"})
    assert spent.status_code == 422, "a finished token is refused a new run"
    assert c.get("/focus/timer", headers={"X-Partial": "1"}).json()["run"] is None, (
        "and leaves no timer behind"
    )

    # open-ended tracking: no target length, duration is whatever elapsed
    c.post("/focus/timer/start",
           data={"token": "tok-c", "mode": "open", "note": "Invented deep work"},
           headers={"X-Partial": "1"})
    _backdate("tok-c", 35 * 60)
    open_done = c.post("/focus/timer/finish", data={"token": "tok-c"},
                       headers={"X-Partial": "1"}).json()
    assert 2100 <= open_done["recorded"]["seconds"] <= 2105, (
        "open tracking records the elapsed wall time"
        + "  -- " + str(open_done["recorded"]["seconds"])
    )
    assert open_done["recorded"]["note"] == "Invented deep work", (
        "the note comes back with the span — the drawer's list is the only "
        "surface left that can show what a session was about"
    )
    ov = open_done["overview"]
    assert ov["today_focus"]["value"] == 1 and ov["today_focus"]["unit"] == "h", (
        "an hour focused today, spelled in hours" + "  -- " + str(ov["today_focus"])
    )

    # Stop pressed in the same second as Start: nothing to record, but the timer
    # is still the server's — dropping it here would leave the drawer ticking
    # against a run that no longer exists.
    c.post("/focus/timer/start", data={"token": "tok-z", "mode": "open"},
           headers={"X-Partial": "1"})
    # Started a moment from now, so "no time has passed" is a fact rather than a
    # bet on the two requests landing inside the same clock second.
    _backdate("tok-z", -2)
    too_soon = c.post("/focus/timer/finish", data={"token": "tok-z"},
                      headers={"X-Partial": "1"})
    assert too_soon.status_code == 422, "an empty span is refused"
    still = c.get("/focus/timer", headers={"X-Partial": "1"}).json()
    assert still["run"] and still["run"]["token"] == "tok-z", (
        "the run survives a too-early stop" + "  -- " + str(still["run"])
    )
    c.post("/focus/timer/discard", data={"token": "tok-z"}, headers={"X-Partial": "1"})

    # Discard beats a finish that read the run before it vanished: the span the
    # user threw away must not be recorded by the request already in flight.
    c.post("/focus/timer/start", data={"token": "tok-y", "mode": "open"},
           headers={"X-Partial": "1"})
    _backdate("tok-y", 300)
    cy = get_conn()
    try:
        stale = cy.execute("SELECT * FROM focus_runs WHERE client_token = 'tok-y'").fetchone()
        c.post("/focus/timer/discard", data={"token": "tok-y"}, headers={"X-Partial": "1"})
        from app.services import focus as _focus
        try:
            _focus.record_session(cy, "open", 300, token="tok-y",
                                  targets={"lesson_id": None, "habit_id": None,
                                           "task_id": None},
                                  run_id=stale["id"])
            raise AssertionError("a discarded run was still recorded")
        except _focus.FocusError:
            pass
        assert cy.execute(
            "SELECT COUNT(*) FROM focus_sessions WHERE client_token = 'tok-y'"
        ).fetchone()[0] == 0, "and the rolled-back finish left no session"
    finally:
        cy.close()

    # a mis-start is not focused time: discard leaves no session behind
    c.post("/focus/timer/start", data={"token": "tok-d", "mode": "open"},
           headers={"X-Partial": "1"})
    dropped = c.post("/focus/timer/discard", data={"token": "tok-d"},
                     headers={"X-Partial": "1"}).json()
    assert dropped["run"] is None, "discard stops the timer"
    assert dropped["overview"]["today_seconds"] == open_done["overview"]["today_seconds"], (
        "a discarded timer records nothing"
    )
    assert len(events_of("focus_session_recorded")) == nf_before + 2, (
        "exactly two spans were recorded"
    )

    # rejected shapes (Mode B 422)
    assert c.post("/focus/timer/start", data={"token": "tok-e", "mode": "nope"},
                  headers={"X-Partial": "1"}).status_code == 422, "bad mode -> 422"
    assert c.post("/focus/timer/start",
                  data={"token": "tok-e", "mode": "countdown", "target_seconds": 5},
                  headers={"X-Partial": "1"}).status_code == 422, "5s countdown -> 422"
    assert c.post("/focus/timer/finish", data={"token": "tok-unknown"},
                  headers={"X-Partial": "1"}).status_code == 422, (
        "finishing a timer that never ran -> 422"
    )

    # the picker offers live targets only
    targets = c.get("/focus/timer/targets").json()["targets"]
    assert set(targets) == {"lesson", "habit", "task"}, "picker covers all three kinds"
    assert any(t["title"] for t in targets["habit"]), "habits are pickable targets"

    # a habit target lands the time on the habit, where the stats now live
    hid = targets["habit"][0]["id"]
    c.post("/focus/timer/start",
           data={"token": "tok-h", "mode": "open", "habit_id": str(hid)},
           headers={"X-Partial": "1"})
    _backdate("tok-h", 600)
    hrec = c.post("/focus/timer/finish", data={"token": "tok-h"},
                  headers={"X-Partial": "1"}).json()["recorded"]
    assert hrec["target"]["kind"] == "habit" and hrec["target"]["id"] == hid, (
        "the session names the habit it was spent on"
    )
    hpage = c.get(f"/habit/{hid}").text
    assert "focused" in hpage and 'data-timer-target="habit:' in hpage, (
        "the habit page shows its focused time and can start the timer"
    )

    # --- statistics & charts (M2): the recorded sessions feed the charts ------
    from app.services import focus as _focus, stats as _stats  # noqa: E402

    # the fortnight chart moved to Retro with the Focus page's retirement (#75)
    retro = c.get("/retro").text
    assert 'class="ec-bars"' in retro and "Last 14 days" in retro, (
        "/retro renders the 14-day focus chart"
    )
    assert "all time" in retro, "/retro carries the focus totals line"
    cx = get_conn()
    try:
        daily = _focus.daily_totals(cx)
        ym = _stats.year_map(cx, 1)
        pulse = _stats.week_pulse(cx)
    finally:
        cx.close()
    assert len(daily) == 14, "daily_totals spans 14 days" + "  -- " + (str(len(daily)))
    assert daily[-1]["minutes"] == 70, (
        "daily_totals reflects today's sessions (25m + 35m + 10m)"
        + "  -- " + f"{daily[-1]['minutes']}m"
    )
    assert _focus.focus_day_streak(daily) >= 1, "focus_day_streak counts today"
    assert len(ym) == 52 and all(len(col) == 7 for col in ym), (
        "year_map is 52 Sunday-start columns of 7"
    )
    assert sum(1 for col in ym for cell in col if cell["is_today"]) == 1, (
        "year_map marks exactly one 'today'"
    )
    assert len(pulse) == 7 and pulse[-1]["focus_min"] == 70, (
        "week_pulse spans 7 days; today reflects 70m focus"
    )
    # A task is a timer target too, so its focused time is readable on the task
    # — a picker that promises attribution nothing can show back is a lie.
    c.post("/tasks", data={"title": "Invented focus target", "return_to": "/tasks"},
           follow_redirects=False)
    tid = c.get("/focus/timer/targets").json()["targets"]["task"][0]["id"]
    c.post("/focus/timer/start",
           data={"token": "tok-t", "mode": "open", "task_id": str(tid)},
           headers={"X-Partial": "1"})
    _backdate("tok-t", 300)
    trec = c.post("/focus/timer/finish", data={"token": "tok-t"},
                  headers={"X-Partial": "1"}).json()["recorded"]
    assert trec["target"]["kind"] == "task" and trec["target"]["id"] == tid, (
        "the session names the task it was spent on" + "  -- " + str(trec["target"])
    )
    tpane = c.get(f"/board?sel=task-{tid}").text
    assert "5m focused" in tpane and f'data-timer-target="task:{tid}"' in tpane, (
        "the task pane shows its focused time and can start the timer"
    )

    # An open drawer's picker goes stale when the board behind it moves. Naming
    # a target that is no longer live is refused, not quietly dropped: starting
    # anyway would attach the time to nothing while the picker still shows it.
    c.post(f"/tasks/{tid}/complete", data={"return_to": "/today"},
           headers={"X-Partial": "1"})
    stale_t = c.post("/focus/timer/start",
                     data={"token": "tok-s", "mode": "open", "task_id": str(tid)},
                     headers={"X-Partial": "1"})
    assert stale_t.status_code == 422 and "no longer available" in stale_t.json()["error"], (
        "a completed task is refused as a target" + "  -- " + stale_t.text
    )
    assert c.get("/focus/timer", headers={"X-Partial": "1"}).json()["run"] is None, (
        "and no timer starts behind the refusal"
    )
    c.post(f"/tasks/{tid}/complete", data={"return_to": "/today"},
           headers={"X-Partial": "1"})

    # A countdown that already ran out cannot be paused. Pausing it would fold
    # the idle time between "ran out" and "resumed" into paused_seconds, and the
    # recorded span would then be dated after it truly ended — far enough, on an
    # overnight run, to land on the next day's Retro bar.
    c.post("/focus/timer/start",
           data={"token": "tok-p", "mode": "countdown", "target_seconds": "60"},
           headers={"X-Partial": "1"})
    _backdate("tok-p", 900)
    late = c.post("/focus/timer/pause", data={"token": "tok-p", "paused": 1},
                  headers={"X-Partial": "1"})
    assert late.status_code == 422 and "already finished" in late.json()["error"], (
        "pausing a countdown that ran out is refused" + "  -- " + late.text
    )
    lp = c.post("/focus/timer/finish", data={"token": "tok-p"},
                headers={"X-Partial": "1"}).json()["recorded"]
    assert lp["seconds"] == 60, (
        "and it still records the minute it asked for" + "  -- " + str(lp)
    )

    # A timer started on Friday and stopped on Monday is a day of Friday, not a
    # day of Monday: the credit is capped at 24h, and the span is dated where
    # that credit ran out, or Retro shows a full day of focus on the return day.
    c.post("/focus/timer/start", data={"token": "tok-w", "mode": "open"},
           headers={"X-Partial": "1"})
    _backdate("tok-w", 50 * 3600)
    late_open = c.post("/focus/timer/pause", data={"token": "tok-w", "paused": 1},
                       headers={"X-Partial": "1"})
    assert late_open.status_code == 422 and "full day" in late_open.json()["error"], (
        "a run past its cap cannot be paused either — the idle time would move "
        "the span" + "  -- " + late_open.text
    )
    wrec = c.post("/focus/timer/finish", data={"token": "tok-w"},
                  headers={"X-Partial": "1"}).json()["recorded"]
    from datetime import datetime as _dtm3, timedelta as _td3

    from app.db import now_iso as _ni3
    _capped_day = (_dtm3.fromisoformat(_ni3()) - _td3(hours=26)).date().isoformat()
    assert wrec["seconds"] == 24 * 3600, (
        "a forgotten timer credits a day at most" + "  -- " + str(wrec)
    )
    assert wrec["date"] == _capped_day, (
        "and lands on the day the credit ran out, not the day Stop was pressed"
        + "  -- " + str(wrec["date"]) + " vs " + _capped_day
    )

    # Paused last night, stopped this morning: the span belongs to the evening
    # it was worked in. Dating it from the click would credit it to the wrong
    # Retro bar and print a time at which nothing happened.
    from datetime import datetime as _dtm2, timedelta as _td2

    from app.db import now_iso as _ni2
    c.post("/focus/timer/start", data={"token": "tok-n", "mode": "open"},
           headers={"X-Partial": "1"})
    _stop = (_dtm2.fromisoformat(_ni2()) - _td2(hours=9)).isoformat(timespec="seconds")
    _go = (_dtm2.fromisoformat(_ni2()) - _td2(hours=10)).isoformat(timespec="seconds")
    cn = get_conn()
    try:
        with cn:
            cn.execute("UPDATE focus_runs SET started_at = ?, paused_at = ?, "
                       "paused_seconds = 0 WHERE client_token = 'tok-n'", (_go, _stop))
    finally:
        cn.close()
    nrec = c.post("/focus/timer/finish", data={"token": "tok-n"},
                  headers={"X-Partial": "1"}).json()["recorded"]
    assert nrec["seconds"] == 3600, (
        "the pause is not counted as focus" + "  -- " + str(nrec)
    )
    assert nrec["date"] == _stop[:10] and nrec["time_label"] == _stop[11:16], (
        "and the span is dated from the pause, not from the stop"
        + "  -- " + str(nrec) + " vs " + _stop
    )

    # The pre-#75 write still answers, for a Focus tab left open across the
    # restart: its app.js posts here when a Pomodoro completes, and a 404 would
    # drop a span the user really did spend. The old words convert on the way in
    # — and back out again, because that page reads the answer in its own
    # vocabulary and would print `undefined` for anything it does not know.
    legacy = c.post("/focus/session", data={"mode": "pomo", "seconds": 1500},
                    headers={"X-Partial": "1"})
    assert legacy.status_code == 200, "the retired session write still records"
    lrec, lov = legacy.json()["record"], legacy.json()["overview"]
    assert lrec["mode"] == "pomo" and lrec["mode_label"] == "Pomo", (
        "the answer speaks the old page's words" + "  -- " + str(lrec)
    )
    assert lrec["duration_label"] == "25m" and "lesson_title" in lrec, (
        "including the fields it prints on the row it appends"
        + "  -- " + str(lrec)
    )
    assert lov["today_pomo"] >= 1 and lov["total_pomo"] >= lov["today_pomo"], (
        "the counters it headlines come back" + "  -- " + str(lov)
    )
    assert lov["today_pomo"] < lov["today_sessions"], (
        "and count Pomodoros, not every span" + "  -- " + str(lov)
    )
    nonsense = c.post("/focus/session", data={"mode": "nope", "seconds": 600},
                      headers={"X-Partial": "1"})
    assert nonsense.status_code == 422, (
        "a word the old page never spoke is refused, not filed as an open span"
        + "  -- " + nonsense.text
    )
    lc = get_conn()
    try:
        assert lc.execute(
            "SELECT mode FROM focus_sessions WHERE id = ?", (lrec["id"],)
        ).fetchone()[0] == "countdown", "while the row stored is a countdown"
    finally:
        lc.close()

    assert 'class="sky-strip"' in c.get("/today").text, (
        "/today carries the sky-strip constellation"
    )
    hd = c.get("/habits?sel=habit-1").text
    assert 'class="sy-grid"' in hd and "A year of check-ins" in hd, (
        "habit detail shows the year sky"
    )

    # cross-origin focus POST rejected
    r = c.post(
        "/focus/timer/start",
        data={"token": "tok-evil", "mode": "open"},
        headers={"Origin": "http://evil.example", "Host": "testserver"},
        follow_redirects=False,
    )
    assert r.status_code == 403, (
        "cross-origin POST /focus/timer/start -> 403" + "  -- " + (str(r.status_code))
    )

    # --- Export: one-button JSONL backup of the event ledger (M4, sec18.1) -----
    import json as _json
    from app.db import EXPORTS_DIR as _ED

    r = c.get("/export")
    assert r.status_code == 200, "GET /export 200" + "  -- " + (str(r.status_code))
    assert 'action="/export/jsonl"' in r.text and "Export JSONL" in r.text, (
        "export page has button"
    )

    r = c.post("/export/jsonl", follow_redirects=False)
    assert r.status_code == 200, (
        "POST /export/jsonl 200" + "  -- " + (str(r.status_code))
    )
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd and "events-" in cd, (
        "export is a downloadable file" + "  -- " + (cd)
    )
    lines = r.text.splitlines()
    assert len(lines) >= 1, "export has >=1 JSONL line" + "  -- " + (str(len(lines)))
    first = _json.loads(lines[0])
    assert {"timestamp", "type", "payload_version", "payload"}.issubset(
        first.keys()
    ) and isinstance(first["payload"], dict), (
        "export line shape (timestamp/type/payload_version/payload object)"
    )
    types_in_export = {_json.loads(line)["type"] for line in lines}
    assert (
        "task_created" in types_in_export
        and "focus_session_recorded" in types_in_export
    ), (
        "export includes journaled events (task + focus)"
        + "  -- "
        + (str(sorted(types_in_export)))
    )
    assert len(list(_ED.glob("events-*.jsonl"))) >= 1, (
        "export file written under data/exports/"
    )

    r = c.post(
        "/export/jsonl",
        headers={"Origin": "http://evil.example", "Host": "testserver"},
        follow_redirects=False,
    )
    assert r.status_code == 403, (
        "cross-origin POST /export/jsonl -> 403" + "  -- " + (str(r.status_code))
    )

    # --- Event identity: persistent UUIDs + idempotent backfill (#17 B4, v9) ----
    import sqlite3 as _sqlite3
    from uuid import UUID as _UUID
    from app.db import (
        append_event as _append_event,
        backfill_event_uuids as _backfill,
        now_iso as _now_iso,
    )

    uconn = get_conn()
    try:
        # append_event returns the persistent identity it stored
        with uconn:
            probe_uuid = _append_event(uconn, "verify_uuid_probe", {"probe": 1})
        stored = uconn.execute(
            "SELECT uuid FROM events WHERE type = 'verify_uuid_probe'"
        ).fetchone()
        assert stored is not None and stored["uuid"] == probe_uuid, (
            "append_event returns the stored event UUID" + "  -- " + (str(probe_uuid))
        )
        assert str(_UUID(probe_uuid)) == probe_uuid, (
            "event UUID is canonical" + "  -- " + (probe_uuid)
        )

        # every event written during this run carries a distinct UUID
        total, filled, distinct = uconn.execute(
            "SELECT COUNT(*), COUNT(uuid), COUNT(DISTINCT uuid) FROM events"
        ).fetchone()
        assert total == filled, (
            "every event carries a UUID" + "  -- " + (f"{filled}/{total}")
        )
        assert filled == distinct, (
            "event UUIDs are unique" + "  -- " + (f"{distinct}/{filled}")
        )

        # uniqueness is schema-enforced, not convention
        try:
            with uconn:
                uconn.execute(
                    "INSERT INTO events (uuid, timestamp, type, payload_version, payload_json) "
                    "VALUES (?, ?, 'verify_uuid_dup', 1, '{}')",
                    (probe_uuid, _now_iso()),
                )
            assert False, (
                "duplicate event UUID rejected by the schema"
                + "  -- "
                + ("insert succeeded")
            )
        except _sqlite3.IntegrityError:
            assert True, "duplicate event UUID rejected by the schema"

        # backfill: pre-v9 rows (uuid NULL) get stamped; payload/timestamp untouched
        legacy_payload = '{"legacy": true}'
        with uconn:
            for _ in range(2):
                uconn.execute(
                    "INSERT INTO events (timestamp, type, payload_version, payload_json) "
                    "VALUES ('2026-01-01T00:00:00+03:00', 'verify_uuid_legacy', 1, ?)",
                    (legacy_payload,),
                )
        with uconn:
            stamped = _backfill(uconn)
        legacy = uconn.execute(
            "SELECT uuid, timestamp, payload_json FROM events "
            "WHERE type = 'verify_uuid_legacy' ORDER BY id"
        ).fetchall()
        assert stamped == 2 and all(r["uuid"] for r in legacy), (
            "backfill stamps exactly the NULL-uuid rows" + "  -- " + (str(stamped))
        )
        assert all(
            r["payload_json"] == legacy_payload
            and r["timestamp"] == "2026-01-01T00:00:00+03:00"
            for r in legacy
        ), "backfill never rewrites payload/timestamp history"
        first_uuids = [r["uuid"] for r in legacy]
        with uconn:
            restamped = _backfill(uconn)
        legacy2 = uconn.execute(
            "SELECT uuid FROM events WHERE type = 'verify_uuid_legacy' ORDER BY id"
        ).fetchall()
        assert restamped == 0 and [r["uuid"] for r in legacy2] == first_uuids, (
            "backfill rerun is an idempotent no-op" + "  -- " + (str(restamped))
        )
    finally:
        uconn.close()

    # --- Calendar events: recurrence engine + CRUD (M1, sec32 §4/§10) -----------
    from datetime import date as _d
    from app.services import calendar_events as ce

    def _rule(**kw):
        base = {
            "start_date": None,
            "end_date": None,
            "exdates": None,
            "freq": "once",
            "byweekday": None,
            "interval_n": 1,
        }
        base.update(kw)
        return base

    # occurs_on — the pure predicate (no DB needed)
    orbit_r = _rule(start_date="2027-04-07", freq="weekly", byweekday="1010100")  # MWF
    assert ce.occurs_on(orbit_r, _d(2027, 4, 7)), (
        "occurs_on: weekly hits its weekday (Wed 04-07)"
    )
    assert not ce.occurs_on(orbit_r, _d(2027, 4, 8)), (
        "occurs_on: weekly skips off-weekday (Thu 04-08)"
    )
    assert not ce.occurs_on(orbit_r, _d(2027, 4, 5)), (
        "occurs_on: before start_date excluded (Mon 04-05)"
    )

    once_r = _rule(start_date="2027-04-07", freq="once")
    assert ce.occurs_on(once_r, _d(2027, 4, 7)) and not ce.occurs_on(
        once_r, _d(2027, 4, 8)
    ), "occurs_on: 'once' only on its start_date"

    daily2 = _rule(start_date="2027-04-07", freq="daily", interval_n=2)
    assert (
        ce.occurs_on(daily2, _d(2027, 4, 7))
        and not ce.occurs_on(daily2, _d(2027, 4, 8))
        and ce.occurs_on(daily2, _d(2027, 4, 9))
    ), "occurs_on: daily interval=2 (04-07 yes / 04-08 no / 04-09 yes)"

    biwk = _rule(
        start_date="2027-04-07", freq="weekly", byweekday="1010100", interval_n=2
    )
    assert ce.occurs_on(biwk, _d(2027, 4, 9)), (
        "occurs_on: weekly interval=2 in-week (Fri 04-09 yes)"
    )
    assert not ce.occurs_on(biwk, _d(2027, 4, 12)), (
        "occurs_on: weekly interval=2 next week off (Mon 04-12 no)"
    )
    assert ce.occurs_on(biwk, _d(2027, 4, 19)), (
        "occurs_on: weekly interval=2 two weeks on (Mon 04-19 yes)"
    )

    bounded = _rule(
        start_date="2027-04-07",
        end_date="2027-04-14",
        freq="weekly",
        byweekday="1010100",
    )
    assert ce.occurs_on(bounded, _d(2027, 4, 14)), (
        "occurs_on: end_date inclusive (Wed 04-14 yes)"
    )
    assert not ce.occurs_on(bounded, _d(2027, 4, 16)), (
        "occurs_on: past end_date excluded (Fri 04-16 no)"
    )

    exd = _rule(
        start_date="2027-04-07",
        freq="weekly",
        byweekday="1010100",
        exdates='["2027-04-09"]',
    )  # JSON text, exactly as the column stores it
    assert not ce.occurs_on(exd, _d(2027, 4, 9)) and ce.occurs_on(
        exd, _d(2027, 4, 7)
    ), "occurs_on: exdate removes that day only"

    # layout_day — overlap column-packing (§6.1), pure render geometry, no DB
    def _occ(st, et=None, all_day=False):
        return {
            "all_day": all_day,
            "start_time": st,
            "end_time": et,
            "title": st or "all",
            "emoji": None,
            "color": None,
            "event_id": 0,
            "list_id": None,
            "note": None,
            "date": "2027-04-07",
        }

    ov = ce.layout_day([_occ("09:00", "10:00"), _occ("09:30", "10:30")])
    assert all(o["ncols"] == 2 for o in ov), (
        "layout: two overlapping events → 2 columns"
    )
    assert sorted(round(o["left"], 3) for o in ov) == [0.0, 0.5], (
        "layout: overlapping events get distinct lefts"
        + "  -- "
        + (str([o["left"] for o in ov]))
    )
    seq = ce.layout_day([_occ("09:00", "10:00"), _occ("10:00", "11:00")])
    assert all(o["ncols"] == 1 and o["width"] == 1.0 for o in seq), (
        "layout: back-to-back events share one full-width column"
    )
    tri = ce.layout_day(
        [_occ("09:00", "10:00"), _occ("09:30", "10:30"), _occ("10:00", "11:00")]
    )
    by_start = {o["start_time"]: o for o in tri}
    assert all(o["ncols"] == 2 for o in tri), (
        "layout: transitive cluster packs into 2 columns"
    )
    assert by_start["10:00"]["col"] == 0 and by_start["09:30"]["col"] == 1, (
        "layout: a freed column is reused (C takes col 0, B in col 1)"
    )
    nul = ce.layout_day([_occ("09:00"), _occ("09:15", "09:45")])
    assert all(o["ncols"] == 2 for o in nul), (
        "layout: NULL end → 30-min block, still collides"
    )
    mixed = ce.layout_day([_occ(None, None, all_day=True), _occ("09:00", "10:00")])
    assert len(mixed) == 1 and mixed[0]["start_time"] == "09:00", (
        "layout: all-day items are dropped from the timed grid"
    )

    # occurrences_between + CRUD against the throwaway DB (the §2 synthetic demo fixture)
    cconn = get_conn()
    try:

        def _rejects(label, fn):
            try:
                fn()
                assert False, label + "  -- " + ("no error raised")
            except ce.CalendarEventError:
                assert True, label

        assert cconn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION, (
            "schema migrated to current version"
        )
        assert {
            "idx_attempts_lesson_cursor",
            "idx_attempts_lesson_order",
        }.issubset(
            {row["name"] for row in cconn.execute("PRAGMA index_list(lesson_attempts)")}
        ), "v13 installs the bounded attempt projection cursor index"
        oid = ce.create_event(
            cconn,
            "Orbit Drill",
            start_date="2027-04-07",
            freq="weekly",
            byweekday="1010100",
            start_time="09:10",
            end_time="09:55",
        )
        sid = ce.create_event(
            cconn,
            "Signal Lab",
            start_date="2027-04-07",
            freq="weekly",
            byweekday="0101000",
            start_time="09:10",
            end_time="09:55",
        )
        wk1 = [
            (o["date"], o["title"])
            for o in ce.occurrences_between(cconn, "2027-04-04", "2027-04-10")
        ]
        wk2 = [
            (o["date"], o["title"])
            for o in ce.occurrences_between(cconn, "2027-04-11", "2027-04-17")
        ]
        assert wk1 == [
            ("2027-04-07", "Orbit Drill"),
            ("2027-04-08", "Signal Lab"),
            ("2027-04-09", "Orbit Drill"),
        ], (
            "§2 week1 expands exactly (Wed Orbit, Thu Signal, Fri Orbit)"
            + "  -- "
            + (str(wk1))
        )
        assert wk2 == [
            ("2027-04-12", "Orbit Drill"),
            ("2027-04-13", "Signal Lab"),
            ("2027-04-14", "Orbit Drill"),
            ("2027-04-15", "Signal Lab"),
            ("2027-04-16", "Orbit Drill"),
        ], (
            "§2 week2 expands exactly (Orbit/Signal/Orbit/Signal/Orbit Mon-Fri)"
            + "  -- "
            + (str(wk2))
        )
        assert all(
            o["start_time"] == "09:10" for o in ce.occurrences_on(cconn, "2027-04-14")
        ), "occurrences merged + time-sorted within a day"

        boundary = [
            o["date"] for o in ce.occurrences_between(cconn, "2027-04-30", "2027-05-06")
        ]
        assert (
            "2027-04-30" in boundary
            and "2027-05-03" in boundary
            and "2027-05-05" in boundary
        ), (
            "occurrences cross the month boundary (42-day grid)"
            + "  -- "
            + (str(boundary))
        )

        ce.skip_occurrence(cconn, oid, "2027-04-09")
        wk1b = [
            (o["date"], o["title"])
            for o in ce.occurrences_between(cconn, "2027-04-04", "2027-04-10")
        ]
        assert wk1b == [("2027-04-07", "Orbit Drill"), ("2027-04-08", "Signal Lab")], (
            "skip removes exactly that occurrence" + "  -- " + (str(wk1b))
        )
        _rejects(
            "reject skip date that is not an occurrence",
            lambda: ce.skip_occurrence(cconn, oid, "2027-04-10"),
        )
        _rejects(
            "reject malformed unskip date",
            lambda: ce.unskip_occurrence(cconn, oid, "not-a-date"),
        )
        ce.unskip_occurrence(cconn, oid, "2027-04-09")
        assert len(ce.occurrences_between(cconn, "2027-04-04", "2027-04-10")) == 3, (
            "unskip restores the occurrence"
        )

        ce.archive_event(cconn, sid)
        wk2c = [
            o["title"]
            for o in ce.occurrences_between(cconn, "2027-04-11", "2027-04-17")
        ]
        assert wk2c == ["Orbit Drill", "Orbit Drill", "Orbit Drill"], (
            "archive removes the whole series from reads" + "  -- " + (str(wk2c))
        )

        _rejects(
            "reject weekly without weekday mask",
            lambda: ce.create_event(cconn, "X", start_date="2027-04-07", freq="weekly"),
        )
        _rejects(
            "reject malformed start_time",
            lambda: ce.create_event(
                cconn, "X", start_date="2027-04-07", start_time="7:15"
            ),
        )
        _rejects(
            "reject empty title",
            lambda: ce.create_event(
                cconn, "   ", start_date="2027-04-07", all_day=True
            ),
        )
        _rejects(
            "reject end_time before start_time",
            lambda: ce.create_event(
                cconn,
                "X",
                start_date="2027-04-07",
                start_time="09:55",
                end_time="09:10",
            ),
        )
        _rejects(
            "reject end_date before start_date",
            lambda: ce.create_event(
                cconn, "X", start_date="2027-04-07", end_date="2027-04-01", all_day=True
            ),
        )

        caltypes = {
            row["type"]
            for row in cconn.execute(
                "SELECT DISTINCT type FROM events WHERE type LIKE 'calendar_%'"
            ).fetchall()
        }
        assert {
            "calendar_event_created",
            "calendar_occurrence_skipped",
            "calendar_occurrence_unskipped",
            "calendar_event_archived",
        }.issubset(caltypes), (
            "audit events for create/skip/unskip/archive"
            + "  -- "
            + (str(sorted(caltypes)))
        )

        # read-view routes (M2): the live Orbit Drill series surfaces in both grids
        rcal = c.get("/calendar?month=2027-04")
        assert "cm-event ev" in rcal.text and "Orbit Drill" in rcal.text, (
            "GET /calendar merges event chips"
        )
        assert "09:10" in rcal.text, "GET /calendar shows the event's time chip"
        rwk = c.get("/calendar/week?date=2027-04-07")
        assert rwk.status_code == 200 and "cw-body" in rwk.text, (
            "GET /calendar/week 200 + grid"
        )
        assert (
            "cw-block" in rwk.text and "Orbit Drill" in rwk.text and "09:10" in rwk.text
        ), "week view places timed blocks (Orbit Drill 09:10)"
        assert 'href="/calendar"' in rwk.text, "week view switch links back to month"
        assert c.get("/calendar/week?date=not-a-date").status_code == 200, (
            "week view tolerates a bad ?date (falls back to today)"
        )
    finally:
        cconn.close()

    # --- Calendar events: write path — form POSTs + edit modal (M3, sec32 §6/§10)
    base = "/calendar?month=2027-06"
    r = c.get(base)
    assert 'id="new-event"' in r.text and 'href="#new-event"' in r.text, (
        "calendar has create-event modal + header link"
    )
    assert (
        'class="habit-form event-form"' in r.text
        and 'name="freq"' in r.text
        and 'name="wd"' in r.text
        and 'name="all_day"' in r.text
    ), "event form: repeat select + weekday boxes + all-day toggle"

    # create a timed weekly series via the form route (invented demo data)
    r = c.post(
        "/calendar/events",
        data={
            "title": "Vector Sync",
            "start_date": "2027-06-01",
            "freq": "weekly",
            "wd": ["1", "3"],
            "start_time": "18:30",
            "end_time": "19:15",
            "interval_n": "1",
            "return_to": base,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers.get("location") == base, (
        "POST /calendar/events -> 303 back to the view"
        + "  -- "
        + (f"{r.status_code} {r.headers.get('location')}")
    )
    rcal = c.get(base)
    assert "Vector Sync" in rcal.text and "18:30" in rcal.text, (
        "created event renders in the month grid"
    )

    vconn = get_conn()
    try:
        vid = vconn.execute(
            "SELECT id FROM calendar_events WHERE title = 'Vector Sync'"
        ).fetchone()["id"]
        nrows = vconn.execute("SELECT COUNT(*) AS n FROM calendar_events").fetchone()[
            "n"
        ]
    finally:
        vconn.close()

    # invalid form: weekly without any weekday box → flash redirect, no row
    r = c.post(
        "/calendar/events",
        data={
            "title": "Bad Weekly",
            "start_date": "2027-06-01",
            "freq": "weekly",
            "start_time": "08:00",
            "return_to": base,
        },
        follow_redirects=False,
    )
    vconn = get_conn()
    try:
        n_after = vconn.execute("SELECT COUNT(*) AS n FROM calendar_events").fetchone()[
            "n"
        ]
    finally:
        vconn.close()
    assert (
        r.status_code == 303
        and "flash=" in r.headers.get("location", "")
        and n_after == nrows
    ), (
        "weekly-without-days rejected with flash, no row created"
        + "  -- "
        + (r.headers.get("location", ""))
    )

    # chips link to the edit modal; ?ev= opens it prefilled, ?on= offers Skip
    assert f"ev={vid}" in rcal.text and "on=2027-06-01" in rcal.text, (
        "month event chip links to edit (?ev= & ?on=)"
    )
    redit = c.get(f"{base}&ev={vid}&on=2027-06-03")
    assert (
        'id="edit-event"' in redit.text
        and 'value="Vector Sync"' in redit.text
        and f'action="/calendar/events/{vid}"' in redit.text
    ), "edit modal opens prefilled"
    assert (
        f'action="/calendar/events/{vid}/skip"' in redit.text
        and 'value="2027-06-03"' in redit.text
    ), "edit modal offers skip-this-occurrence for the clicked date"
    assert 'id="edit-event"' not in c.get(f"{base}&ev=zzz").text, (
        "garbage ?ev is ignored"
    )

    # update the whole series: rename + drop Thursday from the mask
    r = c.post(
        f"/calendar/events/{vid}",
        data={
            "title": "Vector Sync II",
            "start_date": "2027-06-01",
            "freq": "weekly",
            "wd": ["1"],
            "start_time": "18:30",
            "end_time": "19:15",
            "interval_n": "1",
            "return_to": base,
        },
        follow_redirects=False,
    )
    rcal = c.get(base)
    assert (
        r.status_code == 303
        and "Vector Sync II" in rcal.text
        and "on=2027-06-01" in rcal.text
        and "on=2027-06-03" not in rcal.text
    ), "series update renames + reshapes the rule (Thu occurrences gone)"

    # skip one occurrence via the route; restore it from the edit modal's list
    c.post(
        f"/calendar/events/{vid}/skip",
        data={"date": "2027-06-08", "return_to": base},
        follow_redirects=False,
    )
    rcal = c.get(base)
    assert "on=2027-06-01" in rcal.text and "on=2027-06-08" not in rcal.text, (
        "skip route hides exactly that occurrence"
    )
    redit = c.get(f"{base}&ev={vid}")
    assert (
        f'action="/calendar/events/{vid}/unskip"' in redit.text
        and 'value="2027-06-08"' in redit.text
    ), "edit modal lists the skipped date with a restore button"
    c.post(
        f"/calendar/events/{vid}/unskip",
        data={"date": "2027-06-08", "return_to": base},
        follow_redirects=False,
    )
    assert "on=2027-06-08" in c.get(base).text, "unskip route restores the occurrence"

    # all-day create lands in the week view's all-day row (modal present there too)
    c.post(
        "/calendar/events",
        data={
            "title": "Quiet Block",
            "start_date": "2027-06-02",
            "all_day": "1",
            "return_to": "/calendar/week?date=2027-06-02",
        },
        follow_redirects=False,
    )
    rwk = c.get("/calendar/week?date=2027-06-02")
    assert "Quiet Block" in rwk.text and 'id="new-event"' in rwk.text, (
        "all-day event lands in the week all-day row"
    )
    assert "date=2027-05-30&ev=" in rwk.text, "week timed block links to the edit modal"

    # archive: series vanishes from views; its edit link goes inert
    r = c.post(
        f"/calendar/events/{vid}/archive",
        data={"return_to": base},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "Vector Sync II" not in c.get(base).text, (
        "archive route removes the series from the view"
    )
    assert 'id="edit-event"' not in c.get(f"{base}&ev={vid}").text, (
        "edit link for an archived series is ignored"
    )

    r = c.post(
        "/calendar/events",
        data={"title": "X", "start_date": "2027-06-01", "all_day": "1"},
        headers={"Origin": "http://evil.example", "Host": "testserver"},
        follow_redirects=False,
    )
    assert r.status_code == 403, (
        "cross-origin POST /calendar/events -> 403" + "  -- " + (str(r.status_code))
    )

    # --- Calendar events: M4 polish — slot-create, now-line, series export (sec32 §8/§12)
    wk = "/calendar/week?date=2027-06-02"
    rwk = c.get(wk)
    assert "date=2027-05-30&add=2027-06-02&at=06:00" in rwk.text, (
        "week grid offers empty-slot create links"
    )
    rpre = c.get(f"{wk}&add=2027-06-04&at=14:00")
    assert (
        'class="modal-overlay open" id="new-event"' in rpre.text
        and 'value="2027-06-04"' in rpre.text
        and 'value="14:00"' in rpre.text
    ), "slot link opens the create modal prefilled"
    assert 'class="modal-overlay open"' not in c.get(f"{wk}&add=junk&at=99:99").text, (
        "garbage ?add/?at are ignored"
    )
    vconn = get_conn()
    try:
        oid2 = vconn.execute(
            "SELECT id FROM calendar_events WHERE title = 'Orbit Drill'"
        ).fetchone()["id"]
    finally:
        vconn.close()
    rboth = c.get(f"{wk}&ev={oid2}&add=2027-06-04&at=14:00")
    assert (
        'id="edit-event"' in rboth.text
        and 'class="modal-overlay open" id="new-event"' not in rboth.text
    ), "?ev= wins over ?add= (edit opens, create stays closed)"

    # current-time line: in today's week only, and only while now is in the band
    from app.db import now_iso as _now_iso

    hhmm = _now_iso()[11:16]
    in_band = 6 * 60 <= int(hhmm[:2]) * 60 + int(hhmm[3:]) <= 23 * 60
    assert ("cw-now" in c.get("/calendar/week").text) == in_band, (
        "now-line on today's week iff now is inside the band" + "  -- " + (hhmm)
    )
    assert "cw-now" not in rwk.text, "no now-line on another week"

    # JSONL export now snapshots the series rows (source of truth, incl. archived)
    lines = [_json.loads(line) for line in c.post("/export/jsonl").text.splitlines()]
    series = [entry for entry in lines if entry["type"] == "calendar_event_series"]
    titles = {s["payload"]["title"] for s in series}
    assert {"Orbit Drill", "Vector Sync II", "Quiet Block"} <= titles, (
        "export carries calendar_event_series snapshot lines"
        + "  -- "
        + (str(sorted(titles)))
    )
    assert any(s["payload"]["byweekday"] == "1010100" for s in series) and any(
        s["payload"]["archived_at"] for s in series
    ), "series snapshot keeps the rule + archived flag"
    assert not any(
        "occurrence" in entry["type"]
        and "skipped" not in entry["type"]
        and "unskipped" not in entry["type"]
        for entry in lines
    ), "occurrences are never exported"

    # --- Learn: lesson lifecycle, ledger events, Search + export (sec: Learn module)
    from app.services import lessons as _lessons

    rL = c.post(
        "/learn/lessons",
        data={
            "title": "Sparse Transformers Study",
            "source_url": "https://example.org/sparser-faster",
        },
        follow_redirects=False,
    )
    assert rL.status_code == 303 and "lesson=" in rL.headers.get("location", ""), (
        "POST /learn/lessons creates + redirects to the lesson"
        + "  -- "
        + (str(rL.status_code))
    )
    lconn = get_conn()
    try:
        lrow = lconn.execute(
            "SELECT id, status FROM lessons WHERE title = 'Sparse Transformers Study'"
        ).fetchone()
        assert lrow is not None and lrow["status"] == "backlog", (
            "new lesson starts in backlog"
        )
        lid = lrow["id"]
    finally:
        lconn.close()

    for st in ("studying", "paused", "studied"):
        rS = c.post(
            f"/learn/lessons/{lid}/status", data={"status": st}, follow_redirects=False
        )
        assert rS.status_code == 303, (
            f"lesson status -> {st} accepted" + "  -- " + (str(rS.status_code))
        )
    lconn = get_conn()
    try:
        cur = lconn.execute(
            "SELECT status, started_at, completed_at FROM lessons WHERE id = ?", (lid,)
        ).fetchone()
        assert (
            cur["status"] == "studied" and cur["started_at"] and cur["completed_at"]
        ), "studied lesson stamped started_at + completed_at"
    finally:
        lconn.close()

    rX = c.post(
        f"/learn/lessons/{lid}/status",
        data={"status": "backlog"},
        headers={"Origin": "http://evil.example", "Host": "testserver"},
        follow_redirects=False,
    )
    assert rX.status_code == 403, (
        "cross-origin POST lesson status -> 403" + "  -- " + (str(rX.status_code))
    )

    rP = c.get(f"/learn/lessons/{lid}/preview")
    assert rP.status_code == 200 and "frame-ancestors 'self'" in rP.headers.get(
        "content-security-policy", ""
    ), (
        "lesson preview keeps its own CSP (frame-ancestors 'self' exception)"
        + "  -- "
        + (f"{rP.status_code} {rP.headers.get('content-security-policy', '')}")
    )

    c.post(f"/learn/lessons/{lid}/archive", follow_redirects=False)
    lconn = get_conn()
    try:
        assert lconn.execute(
            "SELECT archived_at FROM lessons WHERE id = ?", (lid,)
        ).fetchone()["archived_at"], "archive stamps archived_at"
    finally:
        lconn.close()
    c.post(f"/learn/lessons/{lid}/restore", follow_redirects=False)
    lconn = get_conn()
    try:
        assert (
            lconn.execute(
                "SELECT archived_at FROM lessons WHERE id = ?", (lid,)
            ).fetchone()["archived_at"]
            is None
        ), "restore clears archived_at"
    finally:
        lconn.close()

    # Search now spans lessons (not a silo) — page + service
    rSearch = c.get("/search", params={"q": "Sparse Transformers"})
    assert (
        "Sparse Transformers Study" in rSearch.text
        and f"/learn?lesson={lid}" in rSearch.text
    ), "search page surfaces the matching lesson"
    assert (
        "Sparse Transformers Study"
        not in c.get("/search", params={"q": "zzz-nomatch"}).text
    ), "search page ignores lessons for a non-matching query"
    lconn = get_conn()
    try:
        hits = _lessons.search(lconn, "sparse transformers")
        assert any(h["id"] == lid for h in hits), (
            "lessons.search matches case-insensitive substring"
        )
        assert _lessons.search(lconn, "") == [], "lessons.search('') returns nothing"
        assert _lessons.search(lconn, "%") == [], (
            "lessons.search escapes LIKE wildcards"
        )
    finally:
        lconn.close()

    # Lesson ledger events reach the JSONL export (integrated, not a silo)
    llines = [_json.loads(x) for x in c.post("/export/jsonl").text.splitlines()]
    ltypes = {x["type"] for x in llines}
    assert {
        "lesson_created",
        "lesson_status_changed",
        "lesson_archived",
        "lesson_restored",
    } <= ltypes, (
        "export carries lesson lifecycle events"
        + "  -- "
        + (str(sorted(t for t in ltypes if t.startswith("lesson"))))
    )

    # --- Focus ↔ Lesson link (schema v8): a session names the lesson studied
    from app.services import focus as _focus

    def _run_span(token, seconds, **target):
        """Start, backdate and finish one timer — the only way to record time
        now that the duration is the server's to compute (#75)."""
        data = {"token": token, "mode": "open"}
        data.update(target)
        started = c.post("/focus/timer/start", data=data, headers={"X-Partial": "1"})
        assert started.status_code == 200, (
            "timer start accepted" + "  -- " + str(started.status_code)
        )
        cx = get_conn()
        try:
            from datetime import datetime as _dtm, timedelta as _td

            from app.db import now_iso as _ni
            with cx:
                cx.execute("UPDATE focus_runs SET started_at = ? WHERE client_token = ?",
                           ((_dtm.fromisoformat(_ni()) - _td(seconds=seconds))
                            .isoformat(timespec="seconds"), token))
        finally:
            cx.close()
        return c.post("/focus/timer/finish", data={"token": token},
                      headers={"X-Partial": "1"}).json()

    lesson_span = _run_span("les-a", 1500, lesson_id=str(lid))
    assert lesson_span["recorded"]["target"] == {
        "kind": "lesson", "id": lid, "title": "Sparse Transformers Study"
    }, "the session names the lesson studied" + "  -- " + str(lesson_span["recorded"])
    fconn = get_conn()
    try:
        stored = fconn.execute(
            "SELECT COUNT(*) AS n FROM focus_sessions WHERE lesson_id = ?", (lid,)
        ).fetchone()["n"]
        assert stored == 1, "focus session stores the lesson_id"
        assert _focus.lesson_total(fconn, lid)["label"] == "25m", (
            "the per-lesson total is where the lesson lives"
        )
    finally:
        fconn.close()

    # A picker value that names nothing real is refused at the start, where the
    # user is still standing in front of the picker — not accepted and silently
    # unattached, which would promise attribution and then drop it.
    for bad_id in ("999999", "junk"):
        bad = c.post("/focus/timer/start",
                     data={"token": "les-" + bad_id, "mode": "open",
                           "lesson_id": bad_id},
                     headers={"X-Partial": "1"})
        assert bad.status_code == 422 and "no longer available" in bad.json()["error"], (
            "a lesson_id of " + bad_id + " is refused" + "  -- " + bad.text
        )
    # …while a span aimed at nothing in particular is ordinary and records.
    _run_span("les-none", 60)
    fconn = get_conn()
    try:
        dangling = fconn.execute(
            "SELECT COUNT(*) AS n FROM focus_sessions WHERE lesson_id = 999999"
        ).fetchone()["n"]
        assert dangling == 0, "and nothing dangling was stored"
        assert _focus.overview(fconn)["today_sessions"] >= 2, (
            "an unattached focus session still records"
        )
    finally:
        fconn.close()

    # two targets at once would double-count the same attention
    two = c.post("/focus/timer/start",
                 data={"token": "les-two", "mode": "open", "lesson_id": str(lid),
                       "habit_id": "1"},
                 headers={"X-Partial": "1"})
    assert two.status_code == 422 and "one target" in two.json()["error"], (
        "a timer attaches to one target at most" + "  -- " + str(two.status_code)
    )

    assert any(
        x["type"] == "focus_session_recorded" and x["payload"].get("lesson_id") == lid
        for x in [_json.loads(y) for y in c.post("/export/jsonl").text.splitlines()]
    ), "focus event payload carries lesson_id"

    rlearn = c.get("/learn?lesson=%d" % lid)
    assert 'data-timer-target="lesson:%d"' % lid in rlearn.text, (
        "the lesson page starts the timer on itself"
    )

    # --- Smart quick-add + command palette (M3) ---------------------------------
    from app.services import quickadd as _qa

    _p1 = _qa.parse("buy milk завтра !1", "2026-07-05")
    assert _p1 == {"title": "buy milk", "due_date": "2026-07-06", "priority": 3}, (
        "quickadd: RU 'завтра' + !1 -> tomorrow, priority 3 (!1 inverts to high)"
        + "  -- "
        + (str(_p1))
    )
    _p2 = _qa.parse("report friday !2", "2026-07-05")
    assert (
        _p2["due_date"] == "2026-07-10"
        and _p2["priority"] == 2
        and _p2["title"] == "report"
    ), "quickadd: EN weekday + !2 -> next Friday, priority 2" + "  -- " + (str(_p2))
    _p3 = _qa.parse("pay rent 15.08", "2026-07-05")
    assert _p3["due_date"] == "2026-08-15" and _p3["priority"] == 0, (
        "quickadd: numeric 15.08 -> 2026-08-15, no priority word" + "  -- " + (str(_p3))
    )
    _p4 = _qa.parse("just a plain title", "2026-07-05")
    assert _p4 == {"title": "just a plain title", "due_date": None, "priority": 0}, (
        "quickadd: plain text keeps title, no date/priority" + "  -- " + (str(_p4))
    )

    rpal = c.get("/palette.json")
    _pj = rpal.json()
    assert rpal.status_code == 200 and all(
        k in _pj for k in ("views", "lists", "habits", "lessons", "actions")
    ), (
        "/palette.json returns 200 with every section"
        + "  -- "
        + (str(rpal.status_code))
    )
    from app.templating import TASKS_HOME

    assert any(v["href"] == TASKS_HOME for v in _pj["views"]), (
        "/palette.json views expose the Tasks destination"
    )
    # Focus left the views list with its page (#75): it is an action now
    assert not any(v["href"] == "/focus" for v in _pj["views"]), (
        "no Focus destination survives in the palette"
    )
    assert any(a["shortcut"] == "f" for a in _pj["actions"]), (
        "the palette can open the timer instead"
    )

    assert 'name="smart" value="1"' in c.get("/today").text, (
        "quick-add form opts into smart parsing (smart=1)"
    )

    rsmart = c.post(
        "/tasks",
        data={
            "title": "ship release послезавтра !1",
            "smart": "1",
            "return_to": "/today",
        },
        follow_redirects=False,
    )
    assert rsmart.status_code == 303 and "flash=" in rsmart.headers.get(
        "location", ""
    ), (
        "POST /tasks smart=1 redirects (303) with a parse-confirm flash"
        + "  -- "
        + (rsmart.headers.get("location", ""))
    )
    sconn = get_conn()
    try:
        srow = sconn.execute(
            "SELECT due_date, priority FROM tasks WHERE title = 'ship release'"
        ).fetchone()
        assert srow is not None, (
            "smart quick-add strips date/flag words from the stored title"
        )
        assert srow is not None and srow["due_date"] is not None, (
            "smart quick-add resolves the relative date word to a due date"
        )
        assert srow is not None and srow["priority"] == 3, (
            "smart quick-add stores the inverted priority (!1 -> 3)"
        )
    finally:
        sconn.close()

    rjson = c.post(
        "/tasks",
        data={"title": "call dentist tomorrow !2", "smart": "1"},
        headers={"X-Partial": "1"},
    )
    assert (
        rjson.status_code == 200
        and rjson.json().get("ok") is True
        and "!2" in rjson.json().get("label", "")
    ), (
        "POST /tasks smart=1 Mode B returns a JSON parse label"
        + "  -- "
        + (rjson.text[:120])
    )

    # --- Drag & drop: calendar event move (M4) ---------------------------------
    from app.services import calendar_events as _ce

    mconn = get_conn()
    try:
        eo = _ce.create_event(
            mconn, "dnd once", start_date="2026-07-10", freq="once", all_day=True
        )
        er = _ce.create_event(
            mconn,
            "dnd weekly",
            start_date="2026-07-10",
            freq="weekly",
            byweekday="0000100",
            all_day=True,
        )
        _ce.move_event(mconn, eo, "2026-07-15")
        assert (
            mconn.execute(
                "SELECT start_date FROM calendar_events WHERE id=?", (eo,)
            ).fetchone()[0]
            == "2026-07-15"
        ), "move_event moves a one-off event's start_date"
        try:
            _ce.move_event(mconn, er, "2026-07-16")
            refused = False
        except _ce.CalendarEventError:
            refused = True
        assert refused, "move_event refuses a recurring series"
    finally:
        mconn.close()

    rrec = c.post(
        f"/calendar/events/{er}/move",
        data={"date": "2026-07-16"},
        headers={"X-Partial": "1"},
    )
    assert rrec.status_code == 422 and rrec.json().get("ok") is False, (
        "POST /calendar/events/{id}/move rejects a recurring series (422)"
        + "  -- "
        + (str(rrec.status_code))
    )
    rone = c.post(
        f"/calendar/events/{eo}/move",
        data={"date": "2026-07-20"},
        headers={"X-Partial": "1"},
    )
    assert rone.status_code == 200 and rone.json().get("date") == "2026-07-20", (
        "POST /calendar/events/{id}/move moves a one-off (JSON ok)"
        + "  -- "
        + (rone.text[:120])
    )
    rcal = c.get("/calendar?month=2026-07")
    assert (
        'data-dropzone="calendar"' in rcal.text and 'data-date="2026-07' in rcal.text
    ), "calendar cells are drop zones carrying ISO dates"
    assert f'data-ev-id="{eo}"' in rcal.text and 'draggable="true"' in rcal.text, (
        "calendar renders the one-off event as a draggable chip"
    )

    # --- UX polish (M5): mobile "More" sheet exposes the rail overflow ----------
    rhome = c.get("/today").text
    assert (
        'id="more-toggle"' in rhome
        and 'class="more-sheet"' in rhome
        and all(f'href="{h}"' in rhome for h in ("/retro", "/export", "/items"))
    ), "mobile More sheet toggles a slide-up with the rail's overflow links"

    # --- Terminal core: trust gate + session ownership (review F1–F4) ----
    import asyncio as _asyncio
    import pty as _pty
    import time as _time
    import types as _types

    from starlette.websockets import WebSocket as _WS, WebSocketDisconnect as _WSDisc

    from app import terminal as _terminal

    # terminal.py imports the PTY stack at the point of use (issue #25), so there
    # is no _terminal.pty global to patch; patch the stdlib module the lazy import
    # resolves to. Reading it here also asserts the stack loads on this platform.
    _terminal_pty = _terminal._pty_stack()[1]

    # a non-loopback peer (TestClient reports "testclient") is closed pre-accept
    gate_rejected = False
    try:
        with c.websocket_connect("/terminal/ws"):
            pass
    except _WSDisc as e:
        gate_rejected = e.code == 1008
    assert gate_rejected, "terminal WS rejects a non-loopback peer pre-accept"

    async def _ws_noop(*a):  # WebSocket() wants receive/send; the gate never calls them
        pass

    def _gate_ws(peer: str, host: str, origins=()):
        headers = [(b"host", host.encode())] + [
            (b"origin", o.encode()) for o in origins
        ]
        scope = {
            "type": "websocket",
            "path": "/terminal/ws",
            "query_string": b"",
            "headers": headers,
            "client": (peer, 55555),
        }
        return _WS(scope, _ws_noop, _ws_noop)

    _T = _terminal._ws_is_trusted
    assert _T(_gate_ws("127.0.0.1", "127.0.0.1:8765", ["http://127.0.0.1:8765"])), (
        "term gate: same-origin loopback accepted"
    )
    assert _T(_gate_ws("::1", "[::1]:8765", ["http://[::1]:8765"])), (
        "term gate: IPv6 loopback same-origin accepted"
    )
    assert _T(_gate_ws("127.0.0.1", "localhost:8765")), (
        "term gate: no-Origin local (non-browser) client accepted"
    )
    assert not _T(_gate_ws("127.0.0.1", "localhost:8765", ["http://localhost:3000"])), (
        "term gate: cross-port loopback origin rejected (F1)"
    )
    assert not _T(_gate_ws("127.0.0.1", "127.0.0.1:8765", ["http://localhost:8765"])), (
        "term gate: loopback-family but different hostname rejected (F1)"
    )
    assert not _T(_gate_ws("127.0.0.1", "127.0.0.1:8765", ["http://127.0.0.1"])), (
        "term gate: portless origin vs ported host rejected (F1)"
    )
    assert not _T(
        _gate_ws("127.0.0.1", "127.0.0.1:8765", ["http://evil.example:8765"])
    ), "term gate: non-loopback origin rejected"
    assert not _T(
        _gate_ws(
            "127.0.0.1",
            "127.0.0.1:8765",
            ["http://127.0.0.1:8765", "http://evil.example:8765"],
        )
    ), "term gate: duplicate-Origin smuggle rejected"
    assert not _T(
        _gate_ws("192.168.1.50", "127.0.0.1:8765", ["http://127.0.0.1:8765"])
    ), "term gate: non-loopback peer rejected"
    assert not _T(
        _gate_ws("127.0.0.1", "attacker.example:8765", ["http://attacker.example:8765"])
    ), "term gate: non-loopback Host rejected (DNS rebind)"
    assert not _T(_gate_ws("127.0.0.1", "localhost:junk", ["http://localhost:8765"])), (
        "term gate: junk Host port rejected, not crashed"
    )

    class _FakeSock:
        """Just enough of a WebSocket for the _read_input/_write_all/close paths."""

        def __init__(self):
            self.frames = []

        async def receive(self):
            if self.frames:
                return self.frames.pop(0)
            return {"type": "websocket.disconnect"}

        async def close(self, code=None):
            pass

    async def _terminal_behavior() -> dict:
        out = {}
        master, slave = _pty.openpty()
        os.set_blocking(master, False)
        sess = _terminal._TermSession(
            "verify-term-sid",
            _types.SimpleNamespace(returncode=0),
            master,
            role="plain",
            workspace=str(ROOT),
            sandbox_profile=None,
        )
        _terminal._SESSIONS[sess.sid] = sess
        owner, stale = _FakeSock(), _FakeSock()
        sess.attach(owner)

        # F2: a socket that lost the session must not write into it
        try:
            await _terminal._write_all(sess, stale, b"nope\n")
            out["stale_write_blocked"] = False
        except OSError:
            out["stale_write_blocked"] = True
        await _terminal._write_all(sess, owner, b"ok\n")
        out["owner_write_lands"] = os.read(slave, 16) == b"ok\n"

        # F3: a booted socket's resize/kill frames are ignored...
        stale.frames = [
            {
                "type": "websocket.receive",
                "text": '{"type":"resize","rows":50,"cols":100}',
            },
            {"type": "websocket.receive", "text": '{"type":"kill"}'},
        ]
        await _terminal._read_input(stale, sess)
        out["stale_ctrl_ignored"] = (sess.rows, sess.cols) == (
            24,
            80,
        ) and not sess.closed
        # ...while the owning socket's resize applies and kill closes the session
        owner.frames = [
            {
                "type": "websocket.receive",
                "text": '{"type":"resize","rows":50,"cols":100}',
            },
            {"type": "websocket.receive", "text": '{"type":"kill"}'},
        ]
        await _terminal._read_input(owner, sess)
        out["owner_ctrl_applies"] = (sess.rows, sess.cols) == (50, 100) and sess.closed
        out["killed_session_deregistered"] = sess.sid not in _terminal._SESSIONS

        # F2, the original interleaving: a writer PARKED on PTY writability is booted
        # by a newer attach mid-wait — it must wake with an error and its remaining
        # bytes must never reach the PTY the new socket now owns.
        import termios as _termios

        master3, slave3 = _pty.openpty()
        os.set_blocking(master3, False)
        attrs = _termios.tcgetattr(slave3)
        attrs[3] &= ~(_termios.ICANON | _termios.ECHO)  # raw-ish: a plain byte queue
        _termios.tcsetattr(slave3, _termios.TCSANOW, attrs)
        sess3 = _terminal._TermSession(
            "verify-term-sid3",
            _types.SimpleNamespace(returncode=0),
            master3,
            role="plain",
            workspace=str(ROOT),
            sandbox_profile=None,
        )
        _terminal._SESSIONS[sess3.sid] = sess3
        old_sock, new_sock = _FakeSock(), _FakeSock()
        sess3.attach(old_sock)
        big = b"A" * (2 * 1024 * 1024)  # far beyond any PTY buffering — must park
        writer_task = _asyncio.ensure_future(_terminal._write_all(sess3, old_sock, big))
        for _ in range(2000):  # wait (bounded) for the writer to park on add_writer
            if sess3._writer_active or writer_task.done():
                break
            await _asyncio.sleep(0.001)
        out["writer_parked"] = sess3._writer_active and not writer_task.done()
        sess3.detach(old_sock)  # the boot path in _serve_ws — wakes the parked writer
        sess3.attach(new_sock)
        woke = await _asyncio.gather(writer_task, return_exceptions=True)
        out["parked_writer_woken_to_bail"] = isinstance(woke[0], OSError)

        os.set_blocking(slave3, False)

        def _drain(fd: int) -> bytes:
            got = b""
            while True:
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    return got
                if not chunk:
                    return got
                got += chunk

        prefix = _drain(slave3)  # bytes legitimately written BEFORE the boot
        out["park_was_mid_write"] = 0 < len(prefix) < len(big)
        await _terminal._write_all(sess3, new_sock, b"B" * 64)
        out["no_stale_tail_after_reattach"] = _drain(slave3) == b"B" * 64
        await sess3.close()
        os.close(slave3)

        # F4: the reaper skips a TTL-stale session whose attach handshake is in flight
        master2, slave2 = _pty.openpty()
        os.set_blocking(master2, False)
        sess2 = _terminal._TermSession(
            "verify-term-sid2",
            _types.SimpleNamespace(returncode=0),
            master2,
            role="plain",
            workspace=str(ROOT),
            sandbox_profile=None,
        )
        _terminal._SESSIONS[sess2.sid] = sess2
        sess2.detached_at = _time.monotonic() - 2 * _terminal._SESSION_TTL
        await sess2._attach_lock.acquire()
        _terminal._reap_idle()
        out["reaper_skips_mid_attach"] = sess2.sid in _terminal._SESSIONS
        sess2._attach_lock.release()
        _terminal._reap_idle()
        out["reaper_reaps_after_attach"] = sess2.sid not in _terminal._SESSIONS
        await _asyncio.sleep(0)  # let the reaper's close() task finish
        os.close(slave)
        os.close(slave2)
        return out

    tb = _asyncio.run(_terminal_behavior())
    assert tb["stale_write_blocked"], (
        "terminal: booted socket cannot write into a re-attached session (F2)"
    )
    assert tb["owner_write_lands"], "terminal: owning socket writes reach the PTY"
    assert tb["writer_parked"] and tb["park_was_mid_write"], (
        "terminal: writer parks mid-write on a full PTY (F2 precondition)"
    )
    assert tb["parked_writer_woken_to_bail"], (
        "terminal: boot wakes the parked writer to bail (F2)"
    )
    assert tb["no_stale_tail_after_reattach"], (
        "terminal: no stale tail bytes reach the re-attached session (F2)"
    )
    assert tb["stale_ctrl_ignored"], (
        "terminal: booted socket's resize/kill are ignored (F3)"
    )
    assert tb["owner_ctrl_applies"], "terminal: owner resize applies and kill closes"
    assert tb["killed_session_deregistered"], (
        "terminal: killed session leaves the registry"
    )
    assert tb["reaper_skips_mid_attach"], (
        "terminal: reaper skips a session mid-attach (F4)"
    )
    assert tb["reaper_reaps_after_attach"], (
        "terminal: reaper reaps it once the attach lock is free"
    )

    # --- #116: the child must own the PTY as its controlling terminal --------
    # Job control is what makes Ctrl-C and SIGWINCH work, and it exists only if
    # the child holds a controlling terminal — so the property asserted here is
    # the observable one: the master end reports the child as its foreground
    # process group. `tcgetpgrp` fails, or answers 0, when nobody claimed it.
    #
    # This MUST run on uvloop, the loop uvicorn selects in production. uvloop
    # redirects the child's stdio AFTER preexec_fn (plain asyncio does it
    # before), so on plain asyncio fd 0 already happens to be the slave and a
    # hook that ioctl'd fd 0 would pass here while still failing in the live
    # app. The spawned program is /bin/sleep, not a shell, precisely because an
    # interactive bash re-acquires the terminal on its own and would mask the
    # defect on this path.
    import uvloop as _uvloop

    def _ctty_probe():
        async def _spawn():
            master_fd, slave_fd = _pty.openpty()
            setup = _terminal._child_setup_for(slave_fd)
            proc = await _asyncio.create_subprocess_exec(
                "/bin/sleep", "30",
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                preexec_fn=setup, env={"PATH": "/usr/bin:/bin"},
            )
            os.close(slave_fd)
            try:
                try:
                    foreground = os.tcgetpgrp(master_fd)
                except OSError:
                    foreground = -1
                return foreground, proc.pid
            finally:
                proc.kill()
                await proc.wait()
                os.close(master_fd)

        loop = _uvloop.new_event_loop()  # scoped: never touches the global policy
        try:
            return loop.run_until_complete(_spawn())
        finally:
            loop.close()

    _fg, _child_pid = _ctty_probe()
    assert _fg == _child_pid, (
        "terminal: the spawned child must hold the PTY as its controlling "
        f"terminal (foreground pgrp {_fg}, child {_child_pid}) — without it "
        "there is no job control, so Ctrl-C and SIGWINCH are dead (#116)"
    )

    suite_state.update(
        {
            name: value
            for name, value in locals().items()
            if name not in {"client", "suite_state"}
        }
    )


def test_the_v19_rebuild_keeps_the_focus_session_id_counter(tmp_path):
    """A JSONL restore advances `focus_sessions` in sqlite_sequence past every
    session_id in the retained audit stream without restoring the rows — the
    counter is the only memory those ids ever existed. Rebuilding the table for
    v19 must not hand id 1 back out, or the ledger stops being unambiguous."""
    from app import db

    path = tmp_path / "pre-v19.sqlite"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(
            "CREATE TABLE lessons (id INTEGER PRIMARY KEY);"
            "CREATE TABLE routine_items (id INTEGER PRIMARY KEY);"
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY);"
            "CREATE TABLE focus_sessions ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  mode TEXT NOT NULL DEFAULT 'pomo' CHECK(mode IN ('pomo','stopwatch')),"
            "  seconds INTEGER NOT NULL CHECK(seconds >= 0), note TEXT,"
            "  date TEXT NOT NULL, started_at TEXT, ended_at TEXT NOT NULL,"
            "  created_at TEXT NOT NULL, lesson_id INTEGER REFERENCES lessons(id));"
            "INSERT INTO focus_sessions (id, mode, seconds, date, ended_at, created_at)"
            " VALUES (7, 'pomo', 1500, '2026-01-01',"
            "         '2026-01-01T10:00:00+03:00', '2026-01-01T10:00:00+03:00');"
            "UPDATE sqlite_sequence SET seq = 500 WHERE name = 'focus_sessions';"
        )
        db._migrate_to_19(conn)
        conn.commit()

        seq = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'focus_sessions'"
        ).fetchone()
        assert seq is not None and seq["seq"] == 500, (
            "the rebuilt table keeps the counter the restore left behind"
            + "  -- " + str(dict(seq) if seq else None)
        )
        row = conn.execute("SELECT id, mode, target_seconds FROM focus_sessions").fetchone()
        assert (row["id"], row["mode"], row["target_seconds"]) == (7, "countdown", 1500), (
            "a pomo was always a 25-minute countdown"
        )
        conn.execute(
            "INSERT INTO focus_sessions (mode, seconds, date, ended_at, created_at)"
            " VALUES ('open', 60, '2026-01-02', 't', 't')"
        )
        assert conn.execute(
            "SELECT MAX(id) FROM focus_sessions"
        ).fetchone()[0] == 501, "the next session lands past the retained ids"
    finally:
        conn.close()


def test_a_countdown_across_a_dst_change_ends_in_the_ledger_zone():
    """A stored stamp keeps the offset it was written with, and adding the
    target length to it keeps that offset too. Spring forward, and the countdown
    would be filed at an hour the clocks skipped — the wrong time on the row,
    and the wrong Retro day for a zone that turns over at midnight."""
    import app.db as _db
    from app.services import focus as _focus
    from app.settings import load as _load

    zoned = _load({"ACTIVITY_DATA_DIR": str(_db.settings.data_dir),
                   "APP_TIMEZONE": "Europe/Berlin"})
    original = _db.settings
    _db.settings = zoned  # rebind, not mutate: Settings is frozen by design
    try:
        row = {"started_at": "2026-03-29T01:30:00+01:00", "paused_at": None,
               "paused_seconds": 0}
        ended = _focus._capped_end(row, 2 * 3600, None)
    finally:
        _db.settings = original
    assert ended == "2026-03-29T04:30:00+02:00", (
        "the end is spelled in the zone the ledger keeps" + "  -- " + str(ended)
    )
