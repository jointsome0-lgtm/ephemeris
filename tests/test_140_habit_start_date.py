"""Habit start_date is honoured, and the trimmed habit form is honest (#18).

Before #18 the Create-Habit form offered six controls no reader consulted. Five
were removed; `start_date` was kept and made real. These cases pin both halves:
the field now bounds which days a habit exists on and where its statistics
begin, and the removed fields are gone from the form without their columns
losing the values pre-#18 rows already hold.

Runs late in the cumulative suite because it creates habits, whose presence the
earlier files' counts would otherwise have to absorb.
"""
from __future__ import annotations

from datetime import date as _date, timedelta

from conftest import item_row


def _mk_habit(title: str, start_date: str | None) -> int:
    """Create a habit through the service, with an explicit start bound."""
    from app.db import get_conn
    from app.services import items

    conn = get_conn()
    try:
        item_id = items.create_item(
            conn, title, "Start Bound", start_date=start_date
        )
        if start_date is None:  # create_item defaults to the creation date
            with conn:
                conn.execute(
                    "UPDATE routine_items SET start_date = NULL WHERE id = ?",
                    (item_id,),
                )
        return item_id
    finally:
        conn.close()


def _check(item_id: int, date: str, status: str = "full_done") -> None:
    from app.db import get_conn
    from app.services import checkins

    conn = get_conn()
    try:
        checkins.upsert_checkin(conn, date, item_id, status=status)
    finally:
        conn.close()


def _backdated_check(item_id: int, date: str, status: str = "full_done") -> None:
    """A row from before the habit's start — only reachable by moving the start
    date forward after the fact, so it goes in under the write contract."""
    from app.db import get_conn

    conn = get_conn()
    try:
        with conn:
            conn.execute(
                "INSERT INTO checkins (date, routine_item_id, status, note, "
                "created_at, updated_at) VALUES (?,?,?,NULL,'x','x')",
                (date, item_id, status),
            )
    finally:
        conn.close()


def _listed_on(date: str) -> set[int]:
    """The ids today_view answers with for `date`."""
    from app.db import get_conn
    from app.services import checkins

    conn = get_conn()
    try:
        return {
            row["id"]
            for _group, rows in checkins.today_view(conn, date)
            for row in rows
        }
    finally:
        conn.close()


# --- the list bound --------------------------------------------------------


def test_habit_is_not_listed_before_its_start_date(client):
    from app.db import today_str

    today = _date.fromisoformat(today_str())
    start = today + timedelta(days=3)
    item_id = _mk_habit("Start Bound Future", start.isoformat())

    assert item_id not in _listed_on(today.isoformat()), (
        "a habit starting in three days is not on today's list"
    )
    assert item_id not in _listed_on((start - timedelta(days=1)).isoformat()), (
        "still absent the day before it starts"
    )
    assert item_id in _listed_on(start.isoformat()), "present on its start day"
    assert item_id in _listed_on((start + timedelta(days=1)).isoformat()), (
        "and on every day after"
    )


def test_null_start_date_keeps_the_pre_18_behaviour(client):
    from app.db import today_str

    today = _date.fromisoformat(today_str())
    item_id = _mk_habit("Start Bound Legacy", None)
    assert item_row(item_id)["start_date"] is None, "row really has a NULL bound"

    for offset in (-400, -1, 0):
        date = (today + timedelta(days=offset)).isoformat()
        assert item_id in _listed_on(date), (
            f"a NULL start_date means no lower bound (day {offset})"
        )


def test_the_habit_tab_and_the_day_view_share_the_bound(client):
    """Both HTTP surfaces read today_view, so neither can drift from the rule."""
    from app.db import today_str

    c = client
    today = _date.fromisoformat(today_str())
    started = (today - timedelta(days=1)).isoformat()
    _mk_habit("Start Bound Yesterday", started)

    assert "Start Bound Yesterday" in c.get("/habits").text, (
        "a habit that started yesterday is on the Habit tab"
    )
    assert "Start Bound Yesterday" in c.get(f"/history?date={started}").text, (
        "and on the day view for its start day"
    )
    before = (today - timedelta(days=2)).isoformat()
    assert "Start Bound Yesterday" not in c.get(f"/history?date={before}").text, (
        "but not on the day view for the day before it started"
    )


# --- the write bound -------------------------------------------------------


def test_a_checkin_before_the_start_is_rejected(client):
    """The detail page is palette-reachable, so the contract has to hold too."""
    from app.db import get_conn, today_str
    from app.services import checkins

    today = _date.fromisoformat(today_str())
    start = (today + timedelta(days=5)).isoformat()
    item_id = _mk_habit("Start Bound Not Yet", start)

    conn = get_conn()
    try:
        try:
            checkins.upsert_checkin(
                conn, today.isoformat(), item_id, status="full_done"
            )
        except checkins.CheckinError as exc:
            assert start in str(exc), f"the refusal names the start date -- {exc}"
        else:
            raise AssertionError("a check-in before the start date was accepted")

        # ...and on the start day it goes through
        checkins.upsert_checkin(conn, start, item_id, status="full_done")
        assert checkins.get_checkin(conn, start, item_id) is not None, (
            "the start day itself accepts a check-in"
        )
    finally:
        conn.close()


def test_a_backdated_checkin_can_still_be_cleared(client):
    """Moving a start date forward must not strand rows behind it."""
    from app.db import get_conn
    from app.services import checkins

    item_id = _mk_habit("Start Bound Strand", "2026-05-20")
    _backdated_check(item_id, "2026-05-04")

    conn = get_conn()
    try:
        assert checkins.clear_checkin(conn, "2026-05-04", item_id), (
            "a pre-start row is still removable"
        )
        assert checkins.get_checkin(conn, "2026-05-04", item_id) is None, "it is gone"
    finally:
        conn.close()


def test_the_detail_page_offers_no_checkin_before_the_start(client):
    from app.db import today_str

    today = _date.fromisoformat(today_str())
    start = (today + timedelta(days=5)).isoformat()
    item_id = _mk_habit("Start Bound Detail", start)

    page = client.get(f"/habit/{item_id}").text
    assert "Check in for today" not in page, (
        "no dead-end check-in button before the habit starts"
    )
    assert f"Starts {start}" in page, "the page says when it starts instead"

    # a started habit still has its button
    started_id = _mk_habit("Start Bound Detail Started", today.isoformat())
    assert "Check in for today" in client.get(f"/habit/{started_id}").text, (
        "a started habit keeps the check-in button"
    )


# --- the statistics bound --------------------------------------------------


def test_the_day_before_the_start_neither_breaks_nor_extends_a_streak():
    from app.services import stats

    today = _date(2026, 5, 10)
    start = _date(2026, 5, 8)
    # kept on the 8th, 9th and 10th; the 7th is an untouched pre-start day.
    smap = {"2026-05-08": "full_done", "2026-05-09": "full_done",
            "2026-05-10": "full_done"}

    assert stats.current_streak_from(smap, today, start) == 3, (
        "the empty day before the start does not break the run"
    )
    assert stats.current_streak_from(smap, today, None) == 3, (
        "and an unbounded habit sees the same run here"
    )

    # a stray check-in older than the start (the date was edited backwards)
    smap_stray = dict(smap, **{"2026-05-05": "full_done"})
    assert stats.current_streak_from(smap_stray, today, start) == 3, (
        "a pre-start check-in does not extend the current run either"
    )
    assert stats.best_streak_from(smap_stray, today, start) == 3, (
        "nor seed a best run of its own"
    )
    assert stats.best_streak_from(smap_stray, today, None) == 3, (
        "unbounded, the gap at 05-06/07 resets the run anyway"
    )


def test_a_pre_start_gap_does_not_reset_the_best_run():
    from app.services import stats

    today = _date(2026, 5, 10)
    start = _date(2026, 5, 8)
    # the stray day is kept, then two empty pre-start days, then three kept
    smap = {"2026-05-04": "full_done", "2026-05-08": "full_done",
            "2026-05-09": "full_done", "2026-05-10": "full_done"}
    assert stats.best_streak_from(smap, today, start) == 3, (
        "the walk begins at the start date, so nothing before it is scored"
    )


def test_the_monthly_rate_counts_from_the_start_date(client):
    from app.db import get_conn
    from app.services import stats

    # starts mid-month, kept every day from the 20th to the 25th
    item_id = _mk_habit("Start Bound Rate", "2026-05-20")
    for day in range(20, 26):
        _check(item_id, f"2026-05-{day:02d}")

    conn = get_conn()
    try:
        got = stats.month_stats(conn, item_id, 2026, 5, today="2026-05-25")
    finally:
        conn.close()

    assert got["applicable"] == 6, (
        "denominator is the 20th..25th, not the 1st..25th"
        f"  -- {got['applicable']}"
    )
    assert got["kept"] == 6 and got["rate"] == 100, (
        f"six kept days out of six applicable is 100% -- {got}"
    )


def test_a_month_entirely_before_the_start_is_empty_not_zero_percent_of_31(client):
    from app.db import get_conn
    from app.services import stats

    item_id = _mk_habit("Start Bound Later", "2026-05-20")
    conn = get_conn()
    try:
        got = stats.month_stats(conn, item_id, 2026, 4, today="2026-05-25")
    finally:
        conn.close()
    assert got["applicable"] == 0 and got["rate"] == 0, (
        f"April is outside the habit's life, so nothing is applicable -- {got}"
    )


def test_an_unbounded_habit_still_rates_the_whole_elapsed_month(client):
    from app.db import get_conn
    from app.services import stats

    item_id = _mk_habit("Start Bound Rate Legacy", None)
    for day in range(20, 26):
        _check(item_id, f"2026-05-{day:02d}")

    conn = get_conn()
    try:
        got = stats.month_stats(conn, item_id, 2026, 5, today="2026-05-25")
    finally:
        conn.close()
    assert got["applicable"] == 25 and got["kept"] == 6, (
        f"a NULL bound keeps the pre-#18 denominator (the 1st..25th) -- {got}"
    )


def test_pre_start_checkins_stay_visible_in_the_detail_heatmap(client):
    """They stop counting, but they are not deleted and not hidden (#18)."""
    from app.db import get_conn
    from app.services import stats

    item_id = _mk_habit("Start Bound Backdated", "2026-05-20")
    _backdated_check(item_id, "2026-05-04")

    conn = get_conn()
    try:
        weeks = stats.month_calendar(conn, item_id, 2026, 5, today="2026-05-25")
        history = stats.history(conn, item_id)
    finally:
        conn.close()

    assert history.get("2026-05-04") == "full_done", "the row is still in the table"
    cell = next(
        c for week in weeks for c in week if c["date"] == "2026-05-04"
    )
    assert cell["status"] == "full_done", (
        "and the detail heatmap still shows it"
    )


# --- the trimmed form ------------------------------------------------------


def test_the_form_posts_without_the_removed_fields(client):
    """Create and edit round-trip on emoji / title / start date / section alone."""
    r = client.post(
        "/habits",
        data={
            "title": "Start Bound Form",
            "group_name": "Start Bound",
            "emoji": "🌒",
            "start_date": "2026-05-20",
            "return_to": "/habits",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, f"create 303 -- {r.status_code}"

    item_id = item_row_by_title("Start Bound Form")["id"]
    r = client.post(
        f"/habits/{item_id}/edit",
        data={
            "title": "Start Bound Form Revised",
            "group_name": "Start Bound",
            "emoji": "🌓",
            "start_date": "2026-05-21",
            "return_to": "/habits",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, f"edit 303 -- {r.status_code}"
    row = item_row(item_id)
    assert (
        row["title"] == "Start Bound Form Revised"
        and row["emoji"] == "🌓"
        and row["start_date"] == "2026-05-21"
    ), "the four remaining fields all round-trip"


def test_editing_a_legacy_habit_does_not_invent_a_start_date(client):
    """A NULL bound must survive a rename: the field renders empty, not today."""
    item_id = _mk_habit("Start Bound Unbounded", None)

    # The page carries the create modal too, so scope to the edit form's action.
    page = client.get(f"/habits?sel=habit-{item_id}&edit=1").text
    form = page.split(f'action="/habits/{item_id}/edit"', 1)[1].split("</form>", 1)[0]
    field = form.split('name="start_date"', 1)[1].split(">", 1)[0]
    assert 'value=""' in field, (
        f"an item with no stored bound renders an empty date field -- {field!r}"
    )

    r = client.post(
        f"/habits/{item_id}/edit",
        data={
            "title": "Start Bound Unbounded Renamed",
            "group_name": "Start Bound",
            "emoji": "",
            "start_date": "",
            "return_to": "/habits",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, f"edit 303 -- {r.status_code}"
    row = item_row(item_id)
    assert row["title"] == "Start Bound Unbounded Renamed", "the rename landed"
    assert row["start_date"] is None, (
        "renaming did not stamp a bound that would hide the habit's past"
    )


def test_editing_does_not_erase_the_legacy_columns(client):
    """A pre-#18 row keeps frequency / reminder the trimmed form cannot send."""
    from app.db import get_conn

    item_id = _mk_habit("Start Bound Legacy Values", "2026-05-20")
    conn = get_conn()
    try:
        with conn:
            conn.execute(
                "UPDATE routine_items SET frequency = 'weekly', goal = 'custom', "
                "goal_days = '66', reminder = '06:45', constant_reminder = 1 "
                "WHERE id = ?",
                (item_id,),
            )
    finally:
        conn.close()

    r = client.post(
        f"/habits/{item_id}/edit",
        data={
            "title": "Start Bound Legacy Values",
            "group_name": "Start Bound",
            "emoji": "",
            "start_date": "2026-05-22",
            "return_to": "/habits",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, f"edit 303 -- {r.status_code}"

    row = item_row(item_id)
    assert row["start_date"] == "2026-05-22", "the edit did land"
    assert (
        row["frequency"] == "weekly"
        and row["goal"] == "custom"
        and row["goal_days"] == "66"
        and row["reminder"] == "06:45"
        and row["constant_reminder"] == 1
    ), "the columns the form no longer posts keep their stored values"


def item_row_by_title(title: str):
    from app.db import get_conn

    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM routine_items WHERE title = ?", (title,)
        ).fetchone()
    finally:
        conn.close()
