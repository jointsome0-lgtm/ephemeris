"""End-to-end verification via TestClient on a throwaway DB.

Run: PYTHONPATH=/home/aina/projects/tick-like ACTIVITY_DATA_DIR=/tmp/al-verify python verify.py
Exercises the new Manage Items CRUD + events and re-checks the §16.4 write
contract still holds. Prints PASS/FAIL per assertion; exits non-zero on any fail.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Isolated DB before importing the app.
os.environ["ACTIVITY_DATA_DIR"] = tempfile.mkdtemp(prefix="al-verify-")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import get_conn, today_str  # noqa: E402
from app.main import app  # noqa: E402

PASS = 0
FAIL = 0
ROOT = Path(__file__).resolve().parent


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

    # --- Ephemeris design system (M1) -----------------------------------
    css = c.get("/static/style.css")
    check("style.css served 200", css.status_code == 200, str(css.status_code))
    check("tokens: --font-display + --astral defined",
          "--font-display" in css.text and "--astral" in css.text)
    check("motion gated behind prefers-reduced-motion", "prefers-reduced-motion" in css.text)
    check(":focus-visible is gold (--astral)",
          ":focus-visible" in css.text and "outline: 2px solid var(--astral)" in css.text)
    check("@font-face vendors Cormorant Garamond + JetBrains Mono",
          "Cormorant Garamond" in css.text and "JetBrains Mono" in css.text)
    dfont = c.get("/static/fonts/cormorant-garamond-400-latin.woff2")
    check("vendored display font served 200 (woff2 magic)",
          dfont.status_code == 200 and dfont.content[:4] == b"wOF2",
          f"{dfont.status_code} {dfont.content[:4]!r}")
    mfont = c.get("/static/fonts/jetbrains-mono-400-latin.woff2")
    check("vendored mono font served 200 (woff2 magic)",
          mfont.status_code == 200 and mfont.content[:4] == b"wOF2", str(mfont.status_code))
    vendor_dir = ROOT / "app" / "static" / "vendor"
    xterm_js = (vendor_dir / "xterm.min.js").read_text(encoding="utf-8", errors="replace")
    xterm_css = (vendor_dir / "xterm.min.css").read_text(encoding="utf-8", errors="replace")
    fit_js = (vendor_dir / "xterm-addon-fit.min.js").read_text(encoding="utf-8", errors="replace")
    webgl_js = (vendor_dir / "xterm-addon-webgl.min.js").read_text(encoding="utf-8", errors="replace")
    web_links_js = (vendor_dir / "xterm-addon-web-links.min.js").read_text(encoding="utf-8", errors="replace")
    unicode11_js = (vendor_dir / "xterm-addon-unicode11.min.js").read_text(encoding="utf-8", errors="replace")
    search_js = (vendor_dir / "xterm-addon-search.min.js").read_text(encoding="utf-8", errors="replace")
    check("vendored xterm JS is @xterm/xterm 5.5.0",
          "/npm/@xterm/xterm@5.5.0/lib/xterm.js" in xterm_js[:500])
    check("vendored xterm CSS is @xterm/xterm 5.5.0",
          "/npm/@xterm/xterm@5.5.0/css/xterm.css" in xterm_css[:500])
    check("vendored addon-fit JS is @xterm/addon-fit 0.10.0",
          "/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.js" in fit_js[:500])
    check("vendored addon-webgl JS is @xterm/addon-webgl 0.18.0",
          "/npm/@xterm/addon-webgl@0.18.0/lib/addon-webgl.js" in webgl_js[:500])
    check("vendored addon-web-links JS is @xterm/addon-web-links 0.11.0",
          "/npm/@xterm/addon-web-links@0.11.0/lib/addon-web-links.js" in web_links_js[:500])
    check("vendored addon-unicode11 JS is @xterm/addon-unicode11 0.8.0",
          "/npm/@xterm/addon-unicode11@0.8.0/lib/addon-unicode11.js" in unicode11_js[:500])
    check("vendored addon-search JS is @xterm/addon-search 0.15.0",
          "/npm/@xterm/addon-search@0.15.0/lib/addon-search.js" in search_js[:500])
    base_html = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    check("base.html stamps terminal vendor attrs via static_url",
          "data-xterm-css=\"{{ static_url('vendor/xterm.min.css') }}\"" in base_html
          and "data-xterm-js=\"{{ static_url('vendor/xterm.min.js') }}\"" in base_html
          and "data-fit-js=\"{{ static_url('vendor/xterm-addon-fit.min.js') }}\"" in base_html
          and "data-webgl-js=\"{{ static_url('vendor/xterm-addon-webgl.min.js') }}\"" in base_html
          and "data-web-links-js=\"{{ static_url('vendor/xterm-addon-web-links.min.js') }}\"" in base_html
          and "data-unicode11-js=\"{{ static_url('vendor/xterm-addon-unicode11.min.js') }}\"" in base_html
          and "data-search-js=\"{{ static_url('vendor/xterm-addon-search.min.js') }}\"" in base_html)
    terminal_js = (ROOT / "app" / "static" / "terminal.js").read_text(encoding="utf-8")
    check("terminal.js lazy-loads the official xterm addons",
          "drawer.dataset.webglJs" in terminal_js
          and "drawer.dataset.webLinksJs" in terminal_js
          and "drawer.dataset.unicode11Js" in terminal_js
          and "drawer.dataset.searchJs" in terminal_js
          and "var scripts = [XJS, FJS, WLJS, U11JS, SJS, WGLJS]" in terminal_js)
    check("terminal.js wires xterm addon behavior",
          "new WebglAddon.WebglAddon()" in terminal_js
          and ".onContextLoss" in terminal_js
          and "new WebLinksAddon.WebLinksAddon(openTerminalLink)" in terminal_js
          and "term.unicode.activeVersion = '11'" in terminal_js
          and "new SearchAddon.SearchAddon()" in terminal_js)
    check("terminal drawer has a minimal find bar",
          'id="term-find"' in base_html
          and 'id="term-find-input"' in base_html
          and 'id="term-find-prev"' in base_html
          and 'id="term-find-next"' in base_html
          and 'id="term-find-close"' in base_html)
    tday = c.get("/today").text
    check("Today title carries the Ephemeris identity", "· Ephemeris" in tday)
    check("base metas rebranded to Ephemeris",
          'application-name" content="Ephemeris"' in tday)
    focus = c.get("/focus").text
    check("focus ring is a progress-driven astrolabe SVG",
          'class="astrolabe"' in focus and "astro-progress" in focus and 'id="focus-ring"' in focus)
    check("astrolabe keeps the timer ids", 'id="focus-time"' in focus and 'id="focus-start"' in focus)
    check("empty quadrant shows a constellation", "es-constellation" in c.get("/matrix").text)

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

    # --- statistics & charts (M2): the recorded sessions feed the charts ------
    from app.services import focus as _focus, stats as _stats  # noqa: E402
    check("/focus renders the 14-day bar chart", 'class="ec-bars"' in r.text and "Last 14 days" in r.text)
    cx = get_conn()
    try:
        daily = _focus.daily_totals(cx)
        ym = _stats.year_map(cx, 1)
        pulse = _stats.week_pulse(cx)
    finally:
        cx.close()
    check("daily_totals spans 14 days", len(daily) == 14, str(len(daily)))
    check("daily_totals reflects today's sessions (60m / 2 pomo)",
          daily[-1]["minutes"] == 60 and daily[-1]["pomos"] == 2,
          f'{daily[-1]["minutes"]}m/{daily[-1]["pomos"]}p')
    check("focus_day_streak counts today", _focus.focus_day_streak(daily) >= 1)
    check("year_map is 52 Sunday-start columns of 7",
          len(ym) == 52 and all(len(col) == 7 for col in ym))
    check("year_map marks exactly one 'today'",
          sum(1 for col in ym for cell in col if cell["is_today"]) == 1)
    check("week_pulse spans 7 days; today reflects 60m focus",
          len(pulse) == 7 and pulse[-1]["focus_min"] == 60)
    check("/today carries the sky-strip constellation", 'class="sky-strip"' in c.get("/today").text)
    hd = c.get("/habits?sel=habit-1").text
    check("habit detail shows the year sky", 'class="sy-grid"' in hd and "A year of check-ins" in hd)

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

    # --- Calendar events: recurrence engine + CRUD (M1, sec32 §4/§10) -----------
    from datetime import date as _d
    from app.services import calendar_events as ce

    def _rule(**kw):
        base = {"start_date": None, "end_date": None, "exdates": None,
                "freq": "once", "byweekday": None, "interval_n": 1}
        base.update(kw)
        return base

    # occurs_on — the pure predicate (no DB needed)
    orbit_r = _rule(start_date="2027-04-07", freq="weekly", byweekday="1010100")  # MWF
    check("occurs_on: weekly hits its weekday (Wed 04-07)", ce.occurs_on(orbit_r, _d(2027, 4, 7)))
    check("occurs_on: weekly skips off-weekday (Thu 04-08)", not ce.occurs_on(orbit_r, _d(2027, 4, 8)))
    check("occurs_on: before start_date excluded (Mon 04-05)", not ce.occurs_on(orbit_r, _d(2027, 4, 5)))

    once_r = _rule(start_date="2027-04-07", freq="once")
    check("occurs_on: 'once' only on its start_date",
          ce.occurs_on(once_r, _d(2027, 4, 7)) and not ce.occurs_on(once_r, _d(2027, 4, 8)))

    daily2 = _rule(start_date="2027-04-07", freq="daily", interval_n=2)
    check("occurs_on: daily interval=2 (04-07 yes / 04-08 no / 04-09 yes)",
          ce.occurs_on(daily2, _d(2027, 4, 7)) and not ce.occurs_on(daily2, _d(2027, 4, 8))
          and ce.occurs_on(daily2, _d(2027, 4, 9)))

    biwk = _rule(start_date="2027-04-07", freq="weekly", byweekday="1010100", interval_n=2)
    check("occurs_on: weekly interval=2 in-week (Fri 04-09 yes)", ce.occurs_on(biwk, _d(2027, 4, 9)))
    check("occurs_on: weekly interval=2 next week off (Mon 04-12 no)", not ce.occurs_on(biwk, _d(2027, 4, 12)))
    check("occurs_on: weekly interval=2 two weeks on (Mon 04-19 yes)", ce.occurs_on(biwk, _d(2027, 4, 19)))

    bounded = _rule(start_date="2027-04-07", end_date="2027-04-14", freq="weekly", byweekday="1010100")
    check("occurs_on: end_date inclusive (Wed 04-14 yes)", ce.occurs_on(bounded, _d(2027, 4, 14)))
    check("occurs_on: past end_date excluded (Fri 04-16 no)", not ce.occurs_on(bounded, _d(2027, 4, 16)))

    exd = _rule(start_date="2027-04-07", freq="weekly", byweekday="1010100",
                exdates='["2027-04-09"]')  # JSON text, exactly as the column stores it
    check("occurs_on: exdate removes that day only",
          not ce.occurs_on(exd, _d(2027, 4, 9)) and ce.occurs_on(exd, _d(2027, 4, 7)))

    # layout_day — overlap column-packing (§6.1), pure render geometry, no DB
    def _occ(st, et=None, all_day=False):
        return {"all_day": all_day, "start_time": st, "end_time": et, "title": st or "all",
                "emoji": None, "color": None, "event_id": 0, "list_id": None, "note": None,
                "date": "2027-04-07"}

    ov = ce.layout_day([_occ("09:00", "10:00"), _occ("09:30", "10:30")])
    check("layout: two overlapping events → 2 columns", all(o["ncols"] == 2 for o in ov))
    check("layout: overlapping events get distinct lefts",
          sorted(round(o["left"], 3) for o in ov) == [0.0, 0.5], str([o["left"] for o in ov]))
    seq = ce.layout_day([_occ("09:00", "10:00"), _occ("10:00", "11:00")])
    check("layout: back-to-back events share one full-width column",
          all(o["ncols"] == 1 and o["width"] == 1.0 for o in seq))
    tri = ce.layout_day([_occ("09:00", "10:00"), _occ("09:30", "10:30"), _occ("10:00", "11:00")])
    by_start = {o["start_time"]: o for o in tri}
    check("layout: transitive cluster packs into 2 columns", all(o["ncols"] == 2 for o in tri))
    check("layout: a freed column is reused (C takes col 0, B in col 1)",
          by_start["10:00"]["col"] == 0 and by_start["09:30"]["col"] == 1)
    nul = ce.layout_day([_occ("09:00"), _occ("09:15", "09:45")])
    check("layout: NULL end → 30-min block, still collides", all(o["ncols"] == 2 for o in nul))
    mixed = ce.layout_day([_occ(None, None, all_day=True), _occ("09:00", "10:00")])
    check("layout: all-day items are dropped from the timed grid",
          len(mixed) == 1 and mixed[0]["start_time"] == "09:00")

    # occurrences_between + CRUD against the throwaway DB (the §2 synthetic demo fixture)
    cconn = get_conn()
    try:
        def _rejects(label, fn):
            try:
                fn()
                check(label, False, "no error raised")
            except ce.CalendarEventError:
                check(label, True)

        check("schema migrated to v8", cconn.execute("PRAGMA user_version").fetchone()[0] == 8)
        oid = ce.create_event(cconn, "Orbit Drill", start_date="2027-04-07", freq="weekly",
                              byweekday="1010100", start_time="09:10", end_time="09:55")
        sid = ce.create_event(cconn, "Signal Lab", start_date="2027-04-07", freq="weekly",
                              byweekday="0101000", start_time="09:10", end_time="09:55")
        wk1 = [(o["date"], o["title"]) for o in ce.occurrences_between(cconn, "2027-04-04", "2027-04-10")]
        wk2 = [(o["date"], o["title"]) for o in ce.occurrences_between(cconn, "2027-04-11", "2027-04-17")]
        check("§2 week1 expands exactly (Wed Orbit, Thu Signal, Fri Orbit)",
              wk1 == [("2027-04-07", "Orbit Drill"), ("2027-04-08", "Signal Lab"),
                      ("2027-04-09", "Orbit Drill")], str(wk1))
        check("§2 week2 expands exactly (Orbit/Signal/Orbit/Signal/Orbit Mon-Fri)",
              wk2 == [("2027-04-12", "Orbit Drill"), ("2027-04-13", "Signal Lab"),
                      ("2027-04-14", "Orbit Drill"), ("2027-04-15", "Signal Lab"),
                      ("2027-04-16", "Orbit Drill")], str(wk2))
        check("occurrences merged + time-sorted within a day",
              all(o["start_time"] == "09:10" for o in ce.occurrences_on(cconn, "2027-04-14")))

        boundary = [o["date"] for o in ce.occurrences_between(cconn, "2027-04-30", "2027-05-06")]
        check("occurrences cross the month boundary (42-day grid)",
              "2027-04-30" in boundary and "2027-05-03" in boundary and "2027-05-05" in boundary,
              str(boundary))

        ce.skip_occurrence(cconn, oid, "2027-04-09")
        wk1b = [(o["date"], o["title"]) for o in ce.occurrences_between(cconn, "2027-04-04", "2027-04-10")]
        check("skip removes exactly that occurrence",
              wk1b == [("2027-04-07", "Orbit Drill"), ("2027-04-08", "Signal Lab")], str(wk1b))
        _rejects("reject skip date that is not an occurrence",
                 lambda: ce.skip_occurrence(cconn, oid, "2027-04-10"))
        _rejects("reject malformed unskip date",
                 lambda: ce.unskip_occurrence(cconn, oid, "not-a-date"))
        ce.unskip_occurrence(cconn, oid, "2027-04-09")
        check("unskip restores the occurrence",
              len(ce.occurrences_between(cconn, "2027-04-04", "2027-04-10")) == 3)

        ce.archive_event(cconn, sid)
        wk2c = [o["title"] for o in ce.occurrences_between(cconn, "2027-04-11", "2027-04-17")]
        check("archive removes the whole series from reads",
              wk2c == ["Orbit Drill", "Orbit Drill", "Orbit Drill"], str(wk2c))

        _rejects("reject weekly without weekday mask",
                 lambda: ce.create_event(cconn, "X", start_date="2027-04-07", freq="weekly"))
        _rejects("reject malformed start_time",
                 lambda: ce.create_event(cconn, "X", start_date="2027-04-07", start_time="7:15"))
        _rejects("reject empty title",
                 lambda: ce.create_event(cconn, "   ", start_date="2027-04-07", all_day=True))
        _rejects("reject end_time before start_time",
                 lambda: ce.create_event(cconn, "X", start_date="2027-04-07",
                                         start_time="09:55", end_time="09:10"))
        _rejects("reject end_date before start_date",
                 lambda: ce.create_event(cconn, "X", start_date="2027-04-07",
                                         end_date="2027-04-01", all_day=True))

        caltypes = {row["type"] for row in cconn.execute(
            "SELECT DISTINCT type FROM events WHERE type LIKE 'calendar_%'").fetchall()}
        check("audit events for create/skip/unskip/archive",
              {"calendar_event_created", "calendar_occurrence_skipped",
               "calendar_occurrence_unskipped", "calendar_event_archived"}.issubset(caltypes),
              str(sorted(caltypes)))

        # read-view routes (M2): the live Orbit Drill series surfaces in both grids
        rcal = c.get("/calendar?month=2027-04")
        check("GET /calendar merges event chips", "cm-event ev" in rcal.text and "Orbit Drill" in rcal.text)
        check("GET /calendar shows the event's time chip", "09:10" in rcal.text)
        rwk = c.get("/calendar/week?date=2027-04-07")
        check("GET /calendar/week 200 + grid", rwk.status_code == 200 and "cw-body" in rwk.text)
        check("week view places timed blocks (Orbit Drill 09:10)",
              "cw-block" in rwk.text and "Orbit Drill" in rwk.text and "09:10" in rwk.text)
        check("week view switch links back to month", 'href="/calendar"' in rwk.text)
        check("week view tolerates a bad ?date (falls back to today)",
              c.get("/calendar/week?date=not-a-date").status_code == 200)
    finally:
        cconn.close()

    # --- Calendar events: write path — form POSTs + edit modal (M3, sec32 §6/§10)
    base = "/calendar?month=2027-06"
    r = c.get(base)
    check("calendar has create-event modal + header link",
          'id="new-event"' in r.text and 'href="#new-event"' in r.text)
    check("event form: repeat select + weekday boxes + all-day toggle",
          'class="habit-form event-form"' in r.text and 'name="freq"' in r.text
          and 'name="wd"' in r.text and 'name="all_day"' in r.text)

    # create a timed weekly series via the form route (invented demo data)
    r = c.post("/calendar/events", data={
        "title": "Vector Sync", "start_date": "2027-06-01", "freq": "weekly",
        "wd": ["1", "3"], "start_time": "18:30", "end_time": "19:15",
        "interval_n": "1", "return_to": base}, follow_redirects=False)
    check("POST /calendar/events -> 303 back to the view",
          r.status_code == 303 and r.headers.get("location") == base,
          f"{r.status_code} {r.headers.get('location')}")
    rcal = c.get(base)
    check("created event renders in the month grid",
          "Vector Sync" in rcal.text and "18:30" in rcal.text)

    vconn = get_conn()
    try:
        vid = vconn.execute(
            "SELECT id FROM calendar_events WHERE title = 'Vector Sync'").fetchone()["id"]
        nrows = vconn.execute("SELECT COUNT(*) AS n FROM calendar_events").fetchone()["n"]
    finally:
        vconn.close()

    # invalid form: weekly without any weekday box → flash redirect, no row
    r = c.post("/calendar/events", data={
        "title": "Bad Weekly", "start_date": "2027-06-01", "freq": "weekly",
        "start_time": "08:00", "return_to": base}, follow_redirects=False)
    vconn = get_conn()
    try:
        n_after = vconn.execute("SELECT COUNT(*) AS n FROM calendar_events").fetchone()["n"]
    finally:
        vconn.close()
    check("weekly-without-days rejected with flash, no row created",
          r.status_code == 303 and "flash=" in r.headers.get("location", "")
          and n_after == nrows, r.headers.get("location", ""))

    # chips link to the edit modal; ?ev= opens it prefilled, ?on= offers Skip
    check("month event chip links to edit (?ev= & ?on=)",
          f"ev={vid}" in rcal.text and "on=2027-06-01" in rcal.text)
    redit = c.get(f"{base}&ev={vid}&on=2027-06-03")
    check("edit modal opens prefilled",
          'id="edit-event"' in redit.text and 'value="Vector Sync"' in redit.text
          and f'action="/calendar/events/{vid}"' in redit.text)
    check("edit modal offers skip-this-occurrence for the clicked date",
          f'action="/calendar/events/{vid}/skip"' in redit.text
          and 'value="2027-06-03"' in redit.text)
    check("garbage ?ev is ignored", 'id="edit-event"' not in c.get(f"{base}&ev=zzz").text)

    # update the whole series: rename + drop Thursday from the mask
    r = c.post(f"/calendar/events/{vid}", data={
        "title": "Vector Sync II", "start_date": "2027-06-01", "freq": "weekly",
        "wd": ["1"], "start_time": "18:30", "end_time": "19:15",
        "interval_n": "1", "return_to": base}, follow_redirects=False)
    rcal = c.get(base)
    check("series update renames + reshapes the rule (Thu occurrences gone)",
          r.status_code == 303 and "Vector Sync II" in rcal.text
          and "on=2027-06-01" in rcal.text and "on=2027-06-03" not in rcal.text)

    # skip one occurrence via the route; restore it from the edit modal's list
    c.post(f"/calendar/events/{vid}/skip", data={"date": "2027-06-08", "return_to": base},
           follow_redirects=False)
    rcal = c.get(base)
    check("skip route hides exactly that occurrence",
          "on=2027-06-01" in rcal.text and "on=2027-06-08" not in rcal.text)
    redit = c.get(f"{base}&ev={vid}")
    check("edit modal lists the skipped date with a restore button",
          f'action="/calendar/events/{vid}/unskip"' in redit.text
          and 'value="2027-06-08"' in redit.text)
    c.post(f"/calendar/events/{vid}/unskip", data={"date": "2027-06-08", "return_to": base},
           follow_redirects=False)
    check("unskip route restores the occurrence", "on=2027-06-08" in c.get(base).text)

    # all-day create lands in the week view's all-day row (modal present there too)
    c.post("/calendar/events", data={
        "title": "Quiet Block", "start_date": "2027-06-02", "all_day": "1",
        "return_to": "/calendar/week?date=2027-06-02"}, follow_redirects=False)
    rwk = c.get("/calendar/week?date=2027-06-02")
    check("all-day event lands in the week all-day row",
          "Quiet Block" in rwk.text and 'id="new-event"' in rwk.text)
    check("week timed block links to the edit modal",
          "date=2027-05-30&ev=" in rwk.text)

    # archive: series vanishes from views; its edit link goes inert
    r = c.post(f"/calendar/events/{vid}/archive", data={"return_to": base},
               follow_redirects=False)
    check("archive route removes the series from the view",
          r.status_code == 303 and "Vector Sync II" not in c.get(base).text)
    check("edit link for an archived series is ignored",
          'id="edit-event"' not in c.get(f"{base}&ev={vid}").text)

    r = c.post("/calendar/events", data={"title": "X", "start_date": "2027-06-01", "all_day": "1"},
               headers={"Origin": "http://evil.example", "Host": "testserver"},
               follow_redirects=False)
    check("cross-origin POST /calendar/events -> 403", r.status_code == 403, str(r.status_code))

    # --- Calendar events: M4 polish — slot-create, now-line, series export (sec32 §8/§12)
    wk = "/calendar/week?date=2027-06-02"
    rwk = c.get(wk)
    check("week grid offers empty-slot create links",
          "date=2027-05-30&add=2027-06-02&at=06:00" in rwk.text)
    rpre = c.get(f"{wk}&add=2027-06-04&at=14:00")
    check("slot link opens the create modal prefilled",
          'class="modal-overlay open" id="new-event"' in rpre.text
          and 'value="2027-06-04"' in rpre.text and 'value="14:00"' in rpre.text)
    check("garbage ?add/?at are ignored",
          'class="modal-overlay open"' not in c.get(f"{wk}&add=junk&at=99:99").text)
    vconn = get_conn()
    try:
        oid2 = vconn.execute(
            "SELECT id FROM calendar_events WHERE title = 'Orbit Drill'").fetchone()["id"]
    finally:
        vconn.close()
    rboth = c.get(f"{wk}&ev={oid2}&add=2027-06-04&at=14:00")
    check("?ev= wins over ?add= (edit opens, create stays closed)",
          'id="edit-event"' in rboth.text
          and 'class="modal-overlay open" id="new-event"' not in rboth.text)

    # current-time line: in today's week only, and only while now is in the band
    from app.db import now_iso as _now_iso
    hhmm = _now_iso()[11:16]
    in_band = 6 * 60 <= int(hhmm[:2]) * 60 + int(hhmm[3:]) <= 23 * 60
    check("now-line on today's week iff now is inside the band",
          ("cw-now" in c.get("/calendar/week").text) == in_band, hhmm)
    check("no now-line on another week", "cw-now" not in rwk.text)

    # JSONL export now snapshots the series rows (source of truth, incl. archived)
    lines = [_json.loads(line) for line in c.post("/export/jsonl").text.splitlines()]
    series = [l for l in lines if l["type"] == "calendar_event_series"]
    titles = {s["payload"]["title"] for s in series}
    check("export carries calendar_event_series snapshot lines",
          {"Orbit Drill", "Vector Sync II", "Quiet Block"} <= titles, str(sorted(titles)))
    check("series snapshot keeps the rule + archived flag",
          any(s["payload"]["byweekday"] == "1010100" for s in series)
          and any(s["payload"]["archived_at"] for s in series))
    check("occurrences are never exported",
          not any("occurrence" in l["type"] and "skipped" not in l["type"]
                  and "unskipped" not in l["type"] for l in lines))

    # --- Learn: lesson lifecycle, ledger events, Search + export (sec: Learn module)
    from app.services import lessons as _lessons

    rL = c.post("/learn/lessons",
                data={"title": "Sparse Transformers Study",
                      "source_url": "https://example.org/sparser-faster"},
                follow_redirects=False)
    check("POST /learn/lessons creates + redirects to the lesson",
          rL.status_code == 303 and "lesson=" in rL.headers.get("location", ""),
          str(rL.status_code))
    lconn = get_conn()
    try:
        lrow = lconn.execute(
            "SELECT id, status FROM lessons WHERE title = 'Sparse Transformers Study'"
        ).fetchone()
        check("new lesson starts in backlog", lrow is not None and lrow["status"] == "backlog")
        lid = lrow["id"]
    finally:
        lconn.close()

    for st in ("studying", "paused", "studied"):
        rS = c.post(f"/learn/lessons/{lid}/status", data={"status": st},
                    follow_redirects=False)
        check(f"lesson status -> {st} accepted", rS.status_code == 303, str(rS.status_code))
    lconn = get_conn()
    try:
        cur = lconn.execute(
            "SELECT status, started_at, completed_at FROM lessons WHERE id = ?", (lid,)
        ).fetchone()
        check("studied lesson stamped started_at + completed_at",
              cur["status"] == "studied" and cur["started_at"] and cur["completed_at"])
    finally:
        lconn.close()

    rX = c.post(f"/learn/lessons/{lid}/status", data={"status": "backlog"},
                headers={"Origin": "http://evil.example", "Host": "testserver"},
                follow_redirects=False)
    check("cross-origin POST lesson status -> 403", rX.status_code == 403, str(rX.status_code))

    c.post(f"/learn/lessons/{lid}/archive", follow_redirects=False)
    lconn = get_conn()
    try:
        check("archive stamps archived_at", lconn.execute(
            "SELECT archived_at FROM lessons WHERE id = ?", (lid,)).fetchone()["archived_at"])
    finally:
        lconn.close()
    c.post(f"/learn/lessons/{lid}/restore", follow_redirects=False)
    lconn = get_conn()
    try:
        check("restore clears archived_at", lconn.execute(
            "SELECT archived_at FROM lessons WHERE id = ?", (lid,)).fetchone()["archived_at"] is None)
    finally:
        lconn.close()

    # Search now spans lessons (not a silo) — page + service
    rSearch = c.get("/search", params={"q": "Sparse Transformers"})
    check("search page surfaces the matching lesson",
          "Sparse Transformers Study" in rSearch.text and f"/learn?lesson={lid}" in rSearch.text)
    check("search page ignores lessons for a non-matching query",
          "Sparse Transformers Study" not in c.get("/search", params={"q": "zzz-nomatch"}).text)
    lconn = get_conn()
    try:
        hits = _lessons.search(lconn, "sparse transformers")
        check("lessons.search matches case-insensitive substring", any(h["id"] == lid for h in hits))
        check("lessons.search('') returns nothing", _lessons.search(lconn, "") == [])
        check("lessons.search escapes LIKE wildcards", _lessons.search(lconn, "%") == [])
    finally:
        lconn.close()

    # Lesson ledger events reach the JSONL export (integrated, not a silo)
    llines = [_json.loads(x) for x in c.post("/export/jsonl").text.splitlines()]
    ltypes = {x["type"] for x in llines}
    check("export carries lesson lifecycle events",
          {"lesson_created", "lesson_status_changed", "lesson_archived",
           "lesson_restored"} <= ltypes,
          str(sorted(t for t in ltypes if t.startswith("lesson"))))

    # --- Focus ↔ Lesson link (schema v8): a session names the lesson studied
    from app.services import focus as _focus

    rF = c.post("/focus/session",
                data={"mode": "pomo", "seconds": "1500", "lesson_id": str(lid)},
                follow_redirects=False)
    check("focus session with a lesson is accepted", rF.status_code == 303, str(rF.status_code))
    fconn = get_conn()
    try:
        stored = fconn.execute(
            "SELECT COUNT(*) AS n FROM focus_sessions WHERE lesson_id = ?", (lid,)).fetchone()["n"]
        check("focus session stores the lesson_id", stored == 1)
        recs = _focus.recent_sessions(fconn)
        check("focus record surfaces the linked lesson title",
              any(r["lesson_title"] == "Sparse Transformers Study" for r in recs))
    finally:
        fconn.close()

    c.post("/focus/session", data={"mode": "pomo", "seconds": "60", "lesson_id": "999999"},
           follow_redirects=False)
    c.post("/focus/session", data={"mode": "pomo", "seconds": "60", "lesson_id": "junk"},
           follow_redirects=False)
    fconn = get_conn()
    try:
        bad = fconn.execute(
            "SELECT COUNT(*) AS n FROM focus_sessions WHERE lesson_id = 999999").fetchone()["n"]
        check("nonexistent lesson_id is nulled, not stored", bad == 0)
        check("a plain focus session (no lesson) still records",
              _focus.overview(fconn)["total_pomo"] >= 3)
    finally:
        fconn.close()

    check("focus event payload carries lesson_id",
          any(x["type"] == "focus_session_recorded" and x["payload"].get("lesson_id") == lid
              for x in [_json.loads(y) for y in c.post("/export/jsonl").text.splitlines()]))

    rfocus = c.get("/focus")
    check("focus page renders the lesson picker",
          'id="focus-lesson"' in rfocus.text and "Sparse Transformers Study" in rfocus.text)

    # --- Smart quick-add + command palette (M3) ---------------------------------
    from app.services import quickadd as _qa

    _p1 = _qa.parse("buy milk завтра !1", "2026-07-05")
    check("quickadd: RU 'завтра' + !1 -> tomorrow, priority 3 (!1 inverts to high)",
          _p1 == {"title": "buy milk", "due_date": "2026-07-06", "priority": 3}, str(_p1))
    _p2 = _qa.parse("report friday !2", "2026-07-05")
    check("quickadd: EN weekday + !2 -> next Friday, priority 2",
          _p2["due_date"] == "2026-07-10" and _p2["priority"] == 2 and _p2["title"] == "report", str(_p2))
    _p3 = _qa.parse("pay rent 15.08", "2026-07-05")
    check("quickadd: numeric 15.08 -> 2026-08-15, no priority word",
          _p3["due_date"] == "2026-08-15" and _p3["priority"] == 0, str(_p3))
    _p4 = _qa.parse("just a plain title", "2026-07-05")
    check("quickadd: plain text keeps title, no date/priority",
          _p4 == {"title": "just a plain title", "due_date": None, "priority": 0}, str(_p4))

    rpal = c.get("/palette.json")
    _pj = rpal.json()
    check("/palette.json returns 200 with every section",
          rpal.status_code == 200 and all(k in _pj for k in
              ("views", "lists", "habits", "lessons", "actions")), str(rpal.status_code))
    check("/palette.json views expose Tasks + Focus destinations",
          any(v["href"] == "/today" for v in _pj["views"]) and
          any(v["href"] == "/focus" for v in _pj["views"]))

    check("quick-add form opts into smart parsing (smart=1)",
          'name="smart" value="1"' in c.get("/today").text)

    rsmart = c.post("/tasks", data={"title": "ship release послезавтра !1",
                                    "smart": "1", "return_to": "/today"},
                    follow_redirects=False)
    check("POST /tasks smart=1 redirects (303) with a parse-confirm flash",
          rsmart.status_code == 303 and "flash=" in rsmart.headers.get("location", ""),
          rsmart.headers.get("location", ""))
    sconn = get_conn()
    try:
        srow = sconn.execute(
            "SELECT due_date, priority FROM tasks WHERE title = 'ship release'").fetchone()
        check("smart quick-add strips date/flag words from the stored title", srow is not None)
        check("smart quick-add resolves the relative date word to a due date",
              srow is not None and srow["due_date"] is not None)
        check("smart quick-add stores the inverted priority (!1 -> 3)",
              srow is not None and srow["priority"] == 3)
    finally:
        sconn.close()

    rjson = c.post("/tasks", data={"title": "call dentist tomorrow !2", "smart": "1"},
                   headers={"X-Partial": "1"})
    check("POST /tasks smart=1 Mode B returns a JSON parse label",
          rjson.status_code == 200 and rjson.json().get("ok") is True
          and "!2" in rjson.json().get("label", ""), rjson.text[:120])

    # --- Drag & drop: matrix reorder/reprioritise + calendar event move (M4) ----
    from app.services import calendar_events as _ce, tasks as _tasks

    mconn = get_conn()
    try:
        ta = _tasks.create_task(mconn, "dnd A", priority=1)
        tb = _tasks.create_task(mconn, "dnd B", priority=1)
        tc = _tasks.create_task(mconn, "dnd C", priority=1)
        _tasks.move_task(mconn, tc, after_id=ta, before_id=tb)   # reorder: A < C < B
        oa, ob, oc = (mconn.execute("SELECT sort_order FROM tasks WHERE id=?", (i,)).fetchone()[0]
                      for i in (ta, tb, tc))
        check("move_task reorders within a quadrant (A < C < B by sort_order)",
              oa < oc < ob, f"{oa},{oc},{ob}")
        res = _tasks.move_task(mconn, tc, priority=3)             # cross-quadrant
        check("move_task reprioritises across quadrants (C -> priority 3)",
              res["priority"] == 3 and
              mconn.execute("SELECT priority FROM tasks WHERE id=?", (tc,)).fetchone()[0] == 3)
        mconn.execute("UPDATE tasks SET sort_order=5 WHERE id=?", (ta,))   # zero-gap neighbours
        mconn.execute("UPDATE tasks SET sort_order=6 WHERE id=?", (tb,))
        mconn.commit()
        _tasks.move_task(mconn, tc, priority=1, after_id=ta, before_id=tb)
        na, nb, nc = (mconn.execute("SELECT sort_order FROM tasks WHERE id=?", (i,)).fetchone()[0]
                      for i in (ta, tb, tc))
        check("move_task respaces when neighbours have no gap (A < C < B holds)",
              na < nc < nb, f"{na},{nc},{nb}")

        eo = _ce.create_event(mconn, "dnd once", start_date="2026-07-10",
                              freq="once", all_day=True)
        er = _ce.create_event(mconn, "dnd weekly", start_date="2026-07-10",
                              freq="weekly", byweekday="0000100", all_day=True)
        _ce.move_event(mconn, eo, "2026-07-15")
        check("move_event moves a one-off event's start_date",
              mconn.execute("SELECT start_date FROM calendar_events WHERE id=?",
                            (eo,)).fetchone()[0] == "2026-07-15")
        try:
            _ce.move_event(mconn, er, "2026-07-16")
            refused = False
        except _ce.CalendarEventError:
            refused = True
        check("move_event refuses a recurring series", refused)
    finally:
        mconn.close()

    rmv = c.post(f"/tasks/{ta}/move", data={"priority": "0", "return_to": "/matrix"},
                 headers={"X-Partial": "1"})
    check("POST /tasks/{id}/move returns JSON with the new priority",
          rmv.status_code == 200 and rmv.json().get("ok") is True
          and rmv.json().get("priority") == 0, rmv.text[:120])
    check("POST /tasks/{id}/move rejects an unknown task id (422)",
          c.post("/tasks/999999/move", data={"priority": "1"},
                 headers={"X-Partial": "1"}).status_code == 422)

    check("matrix rows are draggable inside quadrant drop zones",
          all(s in c.get("/matrix").text for s in
              ('draggable="true"', 'data-dropzone="matrix"', 'data-priority=')))

    rrec = c.post(f"/calendar/events/{er}/move", data={"date": "2026-07-16"},
                  headers={"X-Partial": "1"})
    check("POST /calendar/events/{id}/move rejects a recurring series (422)",
          rrec.status_code == 422 and rrec.json().get("ok") is False, str(rrec.status_code))
    rone = c.post(f"/calendar/events/{eo}/move", data={"date": "2026-07-20"},
                  headers={"X-Partial": "1"})
    check("POST /calendar/events/{id}/move moves a one-off (JSON ok)",
          rone.status_code == 200 and rone.json().get("date") == "2026-07-20", rone.text[:120])
    rcal = c.get("/calendar?month=2026-07")
    check("calendar cells are drop zones carrying ISO dates",
          'data-dropzone="calendar"' in rcal.text and 'data-date="2026-07' in rcal.text)
    check("calendar renders the one-off event as a draggable chip",
          f'data-ev-id="{eo}"' in rcal.text and 'draggable="true"' in rcal.text)

    # --- UX polish (M5): mobile "More" sheet exposes the rail overflow ----------
    rhome = c.get("/today").text
    check("mobile More sheet toggles a slide-up with the rail's overflow links",
          'id="more-toggle"' in rhome and 'class="more-sheet"' in rhome
          and all(f'href="{h}"' in rhome for h in ("/countdown", "/learn", "/export", "/items")))

    # --- Terminal core: trust gate + session ownership (review F1–F4) ----
    import asyncio as _asyncio
    import pty as _pty
    import time as _time
    import types as _types

    from starlette.websockets import WebSocket as _WS, WebSocketDisconnect as _WSDisc

    from app import terminal as _terminal

    # a non-loopback peer (TestClient reports "testclient") is closed pre-accept
    gate_rejected = False
    try:
        with c.websocket_connect("/terminal/ws"):
            pass
    except _WSDisc as e:
        gate_rejected = e.code == 1008
    check("terminal WS rejects a non-loopback peer pre-accept", gate_rejected)

    async def _ws_noop(*a):  # WebSocket() wants receive/send; the gate never calls them
        pass

    def _gate_ws(peer: str, host: str, origins=()):
        headers = [(b"host", host.encode())] + [(b"origin", o.encode()) for o in origins]
        scope = {"type": "websocket", "path": "/terminal/ws", "query_string": b"",
                 "headers": headers, "client": (peer, 55555)}
        return _WS(scope, _ws_noop, _ws_noop)

    _T = _terminal._ws_is_trusted
    check("term gate: same-origin loopback accepted",
          _T(_gate_ws("127.0.0.1", "127.0.0.1:8765", ["http://127.0.0.1:8765"])))
    check("term gate: IPv6 loopback same-origin accepted",
          _T(_gate_ws("::1", "[::1]:8765", ["http://[::1]:8765"])))
    check("term gate: no-Origin local (non-browser) client accepted",
          _T(_gate_ws("127.0.0.1", "localhost:8765")))
    check("term gate: cross-port loopback origin rejected (F1)",
          not _T(_gate_ws("127.0.0.1", "localhost:8765", ["http://localhost:3000"])))
    check("term gate: loopback-family but different hostname rejected (F1)",
          not _T(_gate_ws("127.0.0.1", "127.0.0.1:8765", ["http://localhost:8765"])))
    check("term gate: portless origin vs ported host rejected (F1)",
          not _T(_gate_ws("127.0.0.1", "127.0.0.1:8765", ["http://127.0.0.1"])))
    check("term gate: non-loopback origin rejected",
          not _T(_gate_ws("127.0.0.1", "127.0.0.1:8765", ["http://evil.example:8765"])))
    check("term gate: duplicate-Origin smuggle rejected",
          not _T(_gate_ws("127.0.0.1", "127.0.0.1:8765",
                          ["http://127.0.0.1:8765", "http://evil.example:8765"])))
    check("term gate: non-loopback peer rejected",
          not _T(_gate_ws("192.168.1.50", "127.0.0.1:8765", ["http://127.0.0.1:8765"])))
    check("term gate: non-loopback Host rejected (DNS rebind)",
          not _T(_gate_ws("127.0.0.1", "attacker.example:8765",
                          ["http://attacker.example:8765"])))
    check("term gate: junk Host port rejected, not crashed",
          not _T(_gate_ws("127.0.0.1", "localhost:junk", ["http://localhost:8765"])))

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
            "verify-term-sid", _types.SimpleNamespace(returncode=0), master)
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
            {"type": "websocket.receive", "text": '{"type":"resize","rows":50,"cols":100}'},
            {"type": "websocket.receive", "text": '{"type":"kill"}'},
        ]
        await _terminal._read_input(stale, sess)
        out["stale_ctrl_ignored"] = (sess.rows, sess.cols) == (24, 80) and not sess.closed
        # ...while the owning socket's resize applies and kill closes the session
        owner.frames = [
            {"type": "websocket.receive", "text": '{"type":"resize","rows":50,"cols":100}'},
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
            "verify-term-sid3", _types.SimpleNamespace(returncode=0), master3)
        _terminal._SESSIONS[sess3.sid] = sess3
        old_sock, new_sock = _FakeSock(), _FakeSock()
        sess3.attach(old_sock)
        big = b"A" * (2 * 1024 * 1024)  # far beyond any PTY buffering — must park
        writer_task = _asyncio.ensure_future(
            _terminal._write_all(sess3, old_sock, big))
        for _ in range(2000):  # wait (bounded) for the writer to park on add_writer
            if sess3._writer_active or writer_task.done():
                break
            await _asyncio.sleep(0.001)
        out["writer_parked"] = sess3._writer_active and not writer_task.done()
        sess3.detach(old_sock)   # the boot path in _serve_ws — wakes the parked writer
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
            "verify-term-sid2", _types.SimpleNamespace(returncode=0), master2)
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
    check("terminal: booted socket cannot write into a re-attached session (F2)",
          tb["stale_write_blocked"])
    check("terminal: owning socket writes reach the PTY", tb["owner_write_lands"])
    check("terminal: writer parks mid-write on a full PTY (F2 precondition)",
          tb["writer_parked"] and tb["park_was_mid_write"])
    check("terminal: boot wakes the parked writer to bail (F2)",
          tb["parked_writer_woken_to_bail"])
    check("terminal: no stale tail bytes reach the re-attached session (F2)",
          tb["no_stale_tail_after_reattach"])
    check("terminal: booted socket's resize/kill are ignored (F3)", tb["stale_ctrl_ignored"])
    check("terminal: owner resize applies and kill closes", tb["owner_ctrl_applies"])
    check("terminal: killed session leaves the registry", tb["killed_session_deregistered"])
    check("terminal: reaper skips a session mid-attach (F4)", tb["reaper_skips_mid_attach"])
    check("terminal: reaper reaps it once the attach lock is free", tb["reaper_reaps_after_attach"])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
