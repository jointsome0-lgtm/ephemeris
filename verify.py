"""End-to-end verification via TestClient on a throwaway DB.

Run: PYTHONPATH=/home/aina/projects/tick-like ACTIVITY_DATA_DIR=/tmp/al-verify python verify.py
Exercises the new Manage Items CRUD + events and re-checks the §16.4 write
contract still holds. Prints PASS/FAIL per assertion; exits non-zero on any fail.
"""
from __future__ import annotations

import os
import sys
import tempfile

# Isolated DB before importing the app.
os.environ["ACTIVITY_DATA_DIR"] = tempfile.mkdtemp(prefix="al-verify-")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import get_conn, today_str  # noqa: E402
from app.main import app  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    mark = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{mark}] {label}" + (f"  -- {extra}" if extra and not cond else ""))


def events_of(type_: str) -> list:
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT payload_json FROM events WHERE type = ? ORDER BY id", (type_,)
        ).fetchall()
    finally:
        conn.close()


def item_row(item_id: int):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM routine_items WHERE id = ?", (item_id,)
        ).fetchone()
    finally:
        conn.close()


with TestClient(app) as c:
    today = today_str()

    # --- pages render (desktop chrome present) ---------------------------
    # /today is now the TickTick-style task view (sec21): list-sidebar + sections.
    r = c.get("/today")
    check("GET /today 200", r.status_code == 200, str(r.status_code))
    check("today is tasks view (list-sidebar)", 'class="listbar"' in r.text)
    check("today has icon rail", 'class="rail"' in r.text)
    check("today has bottom-nav", 'class="bottom-nav"' in r.text)
    check("today has quick-add", 'class="quick-add"' in r.text)
    check("today has Habit section", ">Habit<" in r.text)

    # /habits is the TickTick-style Habit tab (sec31): list + create + pane.
    r = c.get("/habits")
    check("GET /habits 200", r.status_code == 200, str(r.status_code))
    check("habits is Habit tab (list rows)", 'class="habit-row' in r.text)
    check("habits has create button + modal", 'href="#new-habit"' in r.text and 'id="new-habit"' in r.text)
    # Create-Habit modal mirrors TickTick (two-column rows, no priority, P0 gone)
    check("create modal: TickTick rows", 'class="habit-form"' in r.text and "Frequency" in r.text
          and "Goal Days" in r.text and "Constant Reminder" in r.text)
    check("create modal: reminder '+' + toggle", 'class="hf-reminder"' in r.text and 'class="hf-switch"' in r.text)
    check("create modal: habits have NO priority field", 'name="priority"' not in r.text)
    check("habit section is P0-free", "P0 Core Routine" not in r.text and "Core Routine" in r.text)
    # the rich day-review view now lives at /history (week strip + day sections)
    check("history has week strip", 'class="week-strip"' in c.get("/history").text)

    # --- premium views: calendar / matrix / focus / countdown / search / trash
    r = c.get("/calendar")
    check("GET /calendar 200", r.status_code == 200, str(r.status_code))
    check("calendar has month grid", "cal-month" in r.text)
    r = c.get("/matrix")
    check("GET /matrix 200", r.status_code == 200, str(r.status_code))
    check("matrix has 4 quadrants", r.text.count('class="quad ') == 4, str(r.text.count('class="quad ')))
    r = c.get("/focus")
    check("GET /focus 200", r.status_code == 200, str(r.status_code))
    check("focus has timer", 'id="focus-time"' in r.text and 'id="focus-start"' in r.text)
    r = c.get("/countdown")
    check("GET /countdown 200", r.status_code == 200, str(r.status_code))
    check("countdown shows seeded event (Weekend)", "Weekend" in r.text)
    r = c.get("/search?q=groceries")
    check("GET /search 200 + finds task", r.status_code == 200 and "Buy groceries" in r.text)
    r = c.get("/search")
    check("GET /search (no query) 200", r.status_code == 200)
    r = c.get("/trash")
    check("GET /trash 200", r.status_code == 200, str(r.status_code))

    r = c.get("/items")
    check("GET /items 200", r.status_code == 200, str(r.status_code))
    check("items has Add form", 'action="/items"' in r.text)
    check("items seeded rows shown", "Sleep" in r.text or "Food" in r.text)

    # --- CREATE ---------------------------------------------------------
    n_before = len(events_of("routine_item_created"))
    r = c.post("/items", data={"title": "Stretch", "group_name": "Mobility"},
               follow_redirects=False)
    check("POST /items 303", r.status_code == 303, str(r.status_code))
    created = events_of("routine_item_created")
    check("create event appended", len(created) == n_before + 1)
    conn = get_conn()
    new = conn.execute(
        "SELECT * FROM routine_items WHERE title = 'Stretch'"
    ).fetchone()
    conn.close()
    check("new item persisted", new is not None)
    check("new item active", new is not None and new["active"] == 1)
    check("new item group", new is not None and new["group_name"] == "Mobility")
    check("new item sort_order = 10 (first in group)",
          new is not None and new["sort_order"] == 10, str(new["sort_order"] if new else "?"))
    nid = new["id"]

    # second item in same group -> sort_order should advance to 20
    c.post("/items", data={"title": "Foam roll", "group_name": "Mobility"},
           follow_redirects=False)
    conn = get_conn()
    second = conn.execute("SELECT * FROM routine_items WHERE title = 'Foam roll'").fetchone()
    conn.close()
    check("second item sort_order = 20", second["sort_order"] == 20, str(second["sort_order"]))

    # empty title rejected (flash redirect, no row)
    r = c.post("/items", data={"title": "   ", "group_name": "X"},
               follow_redirects=False)
    check("empty title -> 303 redirect", r.status_code == 303)
    check("empty title -> flash", "flash=" in r.headers.get("location", ""))
    conn = get_conn()
    xcount = conn.execute("SELECT COUNT(*) FROM routine_items WHERE group_name = 'X'").fetchone()[0]
    conn.close()
    check("empty title -> no row created", xcount == 0)

    # --- EDIT -----------------------------------------------------------
    nu_before = len(events_of("routine_item_updated"))
    r = c.post(f"/items/{nid}/edit",
               data={"title": "Stretch & breathe", "group_name": "Mobility"},
               follow_redirects=False)
    check("POST edit 303", r.status_code == 303)
    row = item_row(nid)
    check("title updated", row["title"] == "Stretch & breathe", row["title"])
    check("updated_at set", row["updated_at"] is not None)
    check("update event appended", len(events_of("routine_item_updated")) == nu_before + 1)

    # edit unknown id -> flash, no crash
    r = c.post("/items/999999/edit", data={"title": "x", "group_name": "y"},
               follow_redirects=False)
    check("edit unknown id -> 303 flash", r.status_code == 303 and "flash=" in r.headers.get("location", ""))

    # --- DEACTIVATE (soft) ----------------------------------------------
    nd_before = len(events_of("routine_item_deactivated"))
    r = c.post(f"/items/{nid}/deactivate", follow_redirects=False)
    check("POST deactivate 303", r.status_code == 303)
    row = item_row(nid)
    check("item now inactive", row["active"] == 0)
    check("deactivated_at set", row["deactivated_at"] is not None)
    check("deactivate event appended", len(events_of("routine_item_deactivated")) == nd_before + 1)
    check("row still exists (soft delete)", row is not None)

    # deactivated item hidden from Today, shown as inactive on Items
    r = c.get("/today")
    check("deactivated hidden from Today", "Stretch & breathe" not in r.text)
    r = c.get("/items")
    # Title has an "&" -> Jinja autoescapes to "&amp;" in HTML (security: confirms
    # autoescaping is on). DB keeps the raw value (asserted above).
    check("deactivated shown on Items", "Stretch &amp; breathe" in r.text)
    check("items shows Deactivated section", "Deactivated" in r.text)
    check("autoescape on (no raw & in title)", "Stretch & breathe" not in r.text)

    # --- REACTIVATE -----------------------------------------------------
    r = c.post(f"/items/{nid}/reactivate", follow_redirects=False)
    check("POST reactivate 303", r.status_code == 303)
    row = item_row(nid)
    check("item active again", row["active"] == 1)
    check("deactivated_at cleared", row["deactivated_at"] is None)
    r = c.get("/today")
    check("reactivated visible on Today", "Stretch &amp; breathe" in r.text)

    # --- §16.4 write contract still holds -------------------------------
    # toggle full_done on, then off (toggle-to-clear)
    r = c.post("/checkins",
               data={"date": today, "routine_item_id": nid, "status": "full_done"},
               headers={"X-Partial": "1"})
    check("checkin full_done JSON ok", r.status_code == 200 and r.json()["status"] == "full_done")
    r = c.post("/checkins",
               data={"date": today, "routine_item_id": nid, "status": "full_done"},
               headers={"X-Partial": "1"})
    check("toggle-to-clear -> status None", r.json()["status"] is None)

    # future date rejected
    r = c.post("/checkins",
               data={"date": "2999-01-01", "routine_item_id": nid, "status": "full_done"},
               follow_redirects=False)
    check("future date -> 400", r.status_code == 400, str(r.status_code))

    # light_done allowed (the differentiator)
    r = c.post("/checkins",
               data={"date": today, "routine_item_id": nid, "status": "light_done"},
               headers={"X-Partial": "1"})
    check("light_done accepted", r.status_code == 200 and r.json()["status"] == "light_done")

    # daily note
    r = c.post("/daily-note", data={"date": today, "text": "good day"},
               headers={"X-Partial": "1"})
    check("daily-note JSON ok", r.status_code == 200 and r.json()["ok"] is True)

    # cross-origin POST rejected
    r = c.post("/items", data={"title": "Evil", "group_name": "x"},
               headers={"Origin": "http://evil.example", "Host": "testserver"},
               follow_redirects=False)
    check("cross-origin POST -> 403", r.status_code == 403, str(r.status_code))

    # --- habit stats: streaks / weekly dots / detail page ---------------
    from datetime import date as _d, timedelta as _td
    from app.services import stats as _stats
    from app.db import get_conn as _gc, today_str as _ts

    c.post("/items", data={"title": "Streaky", "group_name": "Mobility"}, follow_redirects=False)
    conn = _gc()
    sid = conn.execute("SELECT id FROM routine_items WHERE title='Streaky'").fetchone()["id"]
    t0 = _d.fromisoformat(_ts())
    # offset-from-today -> status. light keeps the chain; skip is neutral; fail breaks.
    seed = {0: "full_done", 1: "light_done", 2: "skipped", 3: "full_done", 4: "failed", 5: "full_done"}
    for off, st in seed.items():
        dd = (t0 - _td(days=off)).isoformat()
        conn.execute(
            "INSERT INTO checkins (date, routine_item_id, status, note, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (dd, sid, st, ("kept it light" if off == 1 else None), "x", "x"),
        )
    conn.commit()
    smap = _stats.history(conn, sid)
    cur = _stats.current_streak_from(smap, t0)
    best = _stats.best_streak_from(smap, t0)
    total = _stats.total_checkins(conn, sid)
    conn.close()
    check("history has 6 entries", len(smap) == 6, str(len(smap)))
    check("current streak = 3 (full,light,skip,full | fail breaks)", cur == 3, str(cur))
    check("best streak = 3", best == 3, str(best))
    check("total kept = 4 (full+light only)", total == 4, str(total))

    # detail page renders with numbers + heatmap + log
    r = c.get(f"/habit/{sid}")
    check("GET /habit 200", r.status_code == 200, str(r.status_code))
    check("detail shows title", "Streaky" in r.text)
    check("detail has stat cards (TickTick labels)",
          "Monthly check-ins" in r.text and "Total Check-Ins" in r.text
          and "Monthly check-in rate" in r.text and "Current Streak" in r.text)
    check("detail has Habit Log heading", "Habit Log on" in r.text)
    check("detail has monthly heatmap", "cal-grid" in r.text and "cal-cell" in r.text)
    check("detail heatmap has a checked-in cell", "cal-cell today done" in r.text or " done\"" in r.text)
    check("detail has habit log note", "kept it light" in r.text)
    check("detail next-month disabled this month", "cal-arrow disabled" in r.text)
    r = c.get(f"/habit/{sid}?month=2020-01")
    check("detail past month 200 + next enabled", r.status_code == 200 and "?month=2020-02" in r.text)
    r = c.get("/habit/999999")
    check("GET /habit unknown -> 404", r.status_code == 404, str(r.status_code))
    r = c.get("/habit/abc")
    check("GET /habit non-int -> 422", r.status_code == 422, str(r.status_code))

    # Habit tab rows: streak + a TickTick-style circular check-in ring
    r = c.get("/habits")
    check("habits row has check-in ring", "hl-check" in r.text)
    check("habits row has streak stat", "data-streak-cur" in r.text)
    check("habits row has full_done affordance", "data-dot" in r.text)

    # check-in JSON now carries recomputed streaks for live update
    r = c.post("/checkins", data={"date": _ts(), "routine_item_id": sid, "status": "full_done"},
               headers={"X-Partial": "1"})
    body = r.json()
    check("checkin JSON carries streaks", "current_streak" in body and "best_streak" in body, str(body))
    # toggled today's full_done OFF -> today pending; streak now 2 (light,full kept; fail breaks)
    check("streak recomputed after clear = 2", body["current_streak"] == 2, str(body.get("current_streak")))

    # --- tasks / lists / smart lists (sec21) ----------------------------
    from app.services import lists as _lists

    r = c.get("/today")
    check("today has Countdown section", ">Countdown<" in r.text)
    check("today has Completed section", ">Completed<" in r.text)
    check("today quick-add posts to /tasks", 'action="/tasks"' in r.text)
    check("list-sidebar shows Inbox", ">Inbox<" in r.text)
    check("list-sidebar shows a user list (Shopping)", "Shopping" in r.text)
    check("today shows seeded countdown (Weekend)", "Weekend" in r.text)

    conn = _gc()
    inbox = _lists.inbox_id(conn)
    conn.close()

    # CREATE a task -> row + event in one txn
    nt_before = len(events_of("task_created"))
    r = c.post("/tasks", data={"title": "Pay rent", "list_id": inbox, "return_to": "/today"},
               follow_redirects=False)
    check("POST /tasks 303", r.status_code == 303, str(r.status_code))
    check("task_created event appended", len(events_of("task_created")) == nt_before + 1)
    conn = _gc()
    trow = conn.execute("SELECT * FROM tasks WHERE title = 'Pay rent'").fetchone()
    conn.close()
    check("task persisted in Inbox", trow is not None and trow["list_id"] == inbox)
    tid = trow["id"]

    # empty title rejected (flash, no row)
    r = c.post("/tasks", data={"title": "   ", "list_id": inbox, "return_to": "/today"},
               follow_redirects=False)
    check("empty task title -> flash redirect",
          r.status_code == 303 and "flash=" in r.headers.get("location", ""))

    # detail pane renders the editor inline (?sel=task-N)
    r = c.get(f"/today?sel=task-{tid}")
    check("task detail pane renders editor", 'class="dp-form"' in r.text and "Pay rent" in r.text)

    # complete is a reversible toggle (Mode B JSON)
    r = c.post(f"/tasks/{tid}/complete", data={"return_to": "/today"}, headers={"X-Partial": "1"})
    check("task complete JSON ok", r.status_code == 200 and r.json()["completed"] is True)
    r = c.post(f"/tasks/{tid}/complete", data={"return_to": "/today"}, headers={"X-Partial": "1"})
    check("task reopen toggles back", r.json()["completed"] is False)

    # UPDATE: note + due + priority + list
    r = c.post(f"/tasks/{tid}/update",
               data={"title": "Pay rent", "note": "via bank app", "due_date": today,
                     "priority": "2", "list_id": inbox, "return_to": "/today"},
               follow_redirects=False)
    check("POST task update 303", r.status_code == 303, str(r.status_code))
    conn = _gc()
    trow = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    conn.close()
    check("task priority updated", trow["priority"] == 2, str(trow["priority"]))
    check("task due_date updated", trow["due_date"] == today, str(trow["due_date"]))

    # the now-due task surfaces in Today's Tasks section
    r = c.get("/today")
    check("due-today task shows on Today", "Pay rent" in r.text)

    # smart lists + per-list view render
    for path in ("/next7", "/completed"):
        rr = c.get(path)
        check(f"GET {path} 200", rr.status_code == 200, str(rr.status_code))
    conn = _gc()
    lid = conn.execute("SELECT id FROM lists WHERE name = 'Shopping'").fetchone()["id"]
    conn.close()
    r = c.get(f"/list/{lid}")
    check("GET /list 200 + shows its task", r.status_code == 200 and "Buy groceries" in r.text)
    r = c.get("/list/999999")
    check("GET /list unknown -> 404", r.status_code == 404, str(r.status_code))

    # cross-origin task POST rejected (same guard as items/checkins)
    r = c.post("/tasks", data={"title": "Evil", "list_id": inbox},
               headers={"Origin": "http://evil.example", "Host": "testserver"},
               follow_redirects=False)
    check("cross-origin POST /tasks -> 403", r.status_code == 403, str(r.status_code))

    # --- Habit tab: pane + create / edit / archive / delete (sec31) -------
    r = c.get("/habits?sel=habit-1")
    # pane has NO check-in button (TickTick-faithful: the list row's ring is the
    # check-in affordance); the button lives on the standalone full page only.
    check("habit pane: no check-in button", "Check in for today" not in r.text)
    check("habit full page: has check-in control", "Check in for today" in c.get("/habit/1").text)
    check("habit pane: monthly calendar", "cal-grid" in r.text and "cal-cell" in r.text)
    check("habit pane: TickTick stat cards", "Monthly check-ins" in r.text and "Total Check-Ins" in r.text)
    check("habit pane: ⋯ menu w/ delete", "rowmenu" in r.text and "/habits/1/delete" in r.text)
    r = c.get("/habits?sel=habit-1&edit=1")
    check("habit pane: edit form", 'class="habit-form"' in r.text and "Frequency" in r.text)

    # CREATE with the full Create-Habit field set
    nh_before = len(events_of("routine_item_created"))
    r = c.post("/habits", data={"title": "Meditate", "group_name": "Morning", "emoji": "🧘",
        "frequency": "weekdays", "goal": "achieve_all", "goal_days": "66",
        "start_date": "2026-06-01", "reminder": "07:30", "constant_reminder": "1",
        "return_to": "/habits"}, follow_redirects=False)
    check("POST /habits create 303", r.status_code == 303, str(r.status_code))
    check("habit create event appended", len(events_of("routine_item_created")) == nh_before + 1)
    conn = _gc()
    hb = conn.execute("SELECT * FROM routine_items WHERE title = 'Meditate'").fetchone()
    conn.close()
    check("habit persisted with all fields",
          hb is not None and hb["emoji"] == "🧘" and hb["frequency"] == "weekdays"
          and hb["goal_days"] == "66" and hb["reminder"] == "07:30" and hb["constant_reminder"] == 1)
    hid = hb["id"]
    page = c.get("/habits").text
    check("new habit shows in its section", "Meditate" in page and "Morning" in page)

    # empty title rejected
    r = c.post("/habits", data={"title": "   ", "group_name": "Morning"}, follow_redirects=False)
    check("empty habit title -> flash redirect",
          r.status_code == 303 and "flash=" in r.headers.get("location", ""))

    # EDIT (and only-supplied fields change; reminder cleared)
    r = c.post(f"/habits/{hid}/edit", data={"title": "Meditate 10m", "group_name": "Morning",
        "emoji": "🧘", "frequency": "daily", "goal": "achieve_all", "goal_days": "forever",
        "start_date": "2026-06-01", "reminder": "", "return_to": "/habits"}, follow_redirects=False)
    check("POST habit edit 303", r.status_code == 303)
    conn = _gc()
    hb = conn.execute("SELECT title, frequency, reminder FROM routine_items WHERE id = ?", (hid,)).fetchone()
    conn.close()
    check("habit edited", hb["title"] == "Meditate 10m" and hb["frequency"] == "daily" and hb["reminder"] is None)

    # pane Today check-in round-trips and reflects in the pane
    r = c.post("/checkins", data={"date": today, "routine_item_id": hid, "status": "full_done",
        "return_to": f"/habits?sel=habit-{hid}"}, follow_redirects=False)
    check("pane check-in 303 -> stays on pane",
          r.status_code == 303 and f"sel=habit-{hid}" in r.headers.get("location", ""))
    # the pane reflects the check-in in its monthly calendar (today cell marked done)
    check("pane reflects checked status (calendar)", "cal-cell today done" in c.get(f"/habits?sel=habit-{hid}").text)

    # ARCHIVE (soft): hidden from the tab, row kept
    r = c.post(f"/habits/{hid}/archive", data={"return_to": "/habits"}, follow_redirects=False)
    check("POST habit archive 303", r.status_code == 303)
    conn = _gc()
    arow = conn.execute("SELECT active FROM routine_items WHERE id = ?", (hid,)).fetchone()
    conn.close()
    check("archived habit inactive but kept", arow is not None and arow["active"] == 0)
    check("archived habit hidden from tab", "Meditate 10m" not in c.get("/habits").text)

    # DELETE (hard): row + check-ins gone, audit event kept
    c.post("/habits", data={"title": "Temp habit", "group_name": "Morning"}, follow_redirects=False)
    conn = _gc()
    tmp = conn.execute("SELECT id FROM routine_items WHERE title = 'Temp habit'").fetchone()["id"]
    conn.close()
    c.post("/checkins", data={"date": today, "routine_item_id": tmp, "status": "full_done"},
           follow_redirects=False)
    ndel_before = len(events_of("routine_item_deleted"))
    r = c.post(f"/habits/{tmp}/delete", data={"return_to": "/habits"}, follow_redirects=False)
    check("POST habit delete 303", r.status_code == 303)
    conn = _gc()
    gone = conn.execute("SELECT id FROM routine_items WHERE id = ?", (tmp,)).fetchone()
    leftover = conn.execute("SELECT COUNT(*) FROM checkins WHERE routine_item_id = ?", (tmp,)).fetchone()[0]
    conn.close()
    check("deleted habit row gone", gone is None)
    check("deleted habit check-ins removed", leftover == 0)
    check("delete event appended (audit kept)", len(events_of("routine_item_deleted")) == ndel_before + 1)

    # cross-origin habit create rejected
    r = c.post("/habits", data={"title": "Evil", "group_name": "x"},
               headers={"Origin": "http://evil.example", "Host": "testserver"}, follow_redirects=False)
    check("cross-origin POST /habits -> 403", r.status_code == 403, str(r.status_code))

    # --- Focus sessions: persisted Pomodoro / Stopwatch stats (M5) ------------
    r = c.get("/focus")
    check("focus starts at zero stats", 'id="st-today-pomo">0<' in r.text)
    check("focus shows empty record state", "No focus record yet" in r.text)

    nf_before = len(events_of("focus_session_recorded"))
    r = c.post("/focus/session", data={"mode": "pomo", "seconds": 1500}, headers={"X-Partial": "1"})
    check("focus session JSON ok", r.status_code == 200 and r.json()["ok"] is True)
    body = r.json()
    check("focus overview today_pomo=1", body["overview"]["today_pomo"] == 1, str(body["overview"]))
    check("focus overview today_focus 25m",
          body["overview"]["today_focus"]["value"] == 25 and body["overview"]["today_focus"]["unit"] == "m")
    check("focus overview total_pomo=1", body["overview"]["total_pomo"] == 1)
    check("focus record returned (25m pomo)",
          body["record"]["mode"] == "pomo" and body["record"]["duration_label"] == "25m")
    check("focus_session_recorded event appended", len(events_of("focus_session_recorded")) == nf_before + 1)

    # stopwatch adds focus duration but NOT a pomo
    r = c.post("/focus/session", data={"mode": "stopwatch", "seconds": 600}, headers={"X-Partial": "1"})
    ov = r.json()["overview"]
    check("stopwatch adds focus, not pomo", ov["total_pomo"] == 1 and ov["today_focus"]["value"] == 35)

    # invalid durations / modes rejected (Mode B 422)
    r = c.post("/focus/session", data={"mode": "pomo", "seconds": 0}, headers={"X-Partial": "1"})
    check("focus zero duration -> 422", r.status_code == 422 and r.json()["ok"] is False)
    r = c.post("/focus/session", data={"mode": "nope", "seconds": 60}, headers={"X-Partial": "1"})
    check("focus bad mode -> 422", r.status_code == 422)

    # Mode A (no-JS) records the session and 303-redirects back to /focus
    r = c.post("/focus/session", data={"mode": "pomo", "seconds": 1500, "return_to": "/focus"},
               follow_redirects=False)
    check("focus Mode A 303 -> /focus", r.status_code == 303 and r.headers.get("location") == "/focus")

    # persisted rows now surface on the page (record row + updated total)
    r = c.get("/focus")
    check("focus page shows a record row", 'class="focus-rec-row"' in r.text and "25m" in r.text)
    check("focus page total pomo = 2", 'id="st-total-pomo">2<' in r.text)

    # cross-origin focus POST rejected
    r = c.post("/focus/session", data={"mode": "pomo", "seconds": 60},
               headers={"Origin": "http://evil.example", "Host": "testserver"}, follow_redirects=False)
    check("cross-origin POST /focus/session -> 403", r.status_code == 403, str(r.status_code))

    # --- Export: one-button JSONL backup of the event ledger (M4, sec18.1) -----
    import json as _json
    from app.db import EXPORTS_DIR as _ED

    r = c.get("/export")
    check("GET /export 200", r.status_code == 200, str(r.status_code))
    check("export page has button", 'action="/export/jsonl"' in r.text and "Export JSONL" in r.text)

    r = c.post("/export/jsonl", follow_redirects=False)
    check("POST /export/jsonl 200", r.status_code == 200, str(r.status_code))
    cd = r.headers.get("content-disposition", "")
    check("export is a downloadable file", "attachment" in cd and "events-" in cd, cd)
    lines = r.text.splitlines()
    check("export has >=1 JSONL line", len(lines) >= 1, str(len(lines)))
    first = _json.loads(lines[0])
    check("export line shape (timestamp/type/payload_version/payload object)",
          {"timestamp", "type", "payload_version", "payload"}.issubset(first.keys())
          and isinstance(first["payload"], dict))
    types_in_export = {_json.loads(line)["type"] for line in lines}
    check("export includes journaled events (task + focus)",
          "task_created" in types_in_export and "focus_session_recorded" in types_in_export,
          str(sorted(types_in_export)))
    check("export file written under data/exports/", len(list(_ED.glob("events-*.jsonl"))) >= 1)

    r = c.post("/export/jsonl", headers={"Origin": "http://evil.example", "Host": "testserver"},
               follow_redirects=False)
    check("cross-origin POST /export/jsonl -> 403", r.status_code == 403, str(r.status_code))

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
