"""The no-file lesson placeholder paints in the reader's app theme.

A lesson with no HTML file renders a generated placeholder instead, and that
document is app chrome, not lesson content — so it follows the theme rather
than blinding a night reader with paper. The app theme is a tri-state that can
be pinned AGAINST the OS, and the preview is a sandboxed document that cannot
read `data-theme`, so the resolved value travels in the `al-scheme` cookie and
the server picks the palette outright.

Runs after the tree file: it seeds a lesson of its own and asserts nothing
about anyone else's counts.
"""
from __future__ import annotations

from pathlib import Path

NIGHT_BG = "#10131f"  # html[data-theme="dark"] --bg, repeated in the placeholder


def _slug_of(lesson_id: int) -> str:
    from app.db import get_conn

    conn = get_conn()
    try:
        return conn.execute(
            "SELECT slug FROM lessons WHERE id = ?", (lesson_id,)
        ).fetchone()["slug"]
    finally:
        conn.close()


def _file_less_lesson(client, title: str) -> int:
    """A lesson whose bundle exists but holds no entry page."""
    from app.db import get_conn
    from app.services import lessons as lessons_svc

    response = client.post(
        "/learn/lessons", data={"title": title}, follow_redirects=False
    )
    assert response.status_code == 303, "the add form redirects on success"
    conn = get_conn()
    try:
        lesson_id = conn.execute(
            "SELECT id FROM lessons WHERE title = ?", (title,)
        ).fetchone()["id"]
    finally:
        conn.close()
    entry = Path(lessons_svc.LESSONS_DIR) / _slug_of(lesson_id) / "index.html"
    assert not entry.exists(), "a fresh lesson has no entry page yet"
    return lesson_id


def _preview(client, lesson_id: int, scheme: str | None) -> str:
    """GET the preview as a reader whose app theme resolved to `scheme`.

    The cookie is set on the shared client and cleared again: it is the one
    piece of per-reader state here, and leaving it behind would quietly decide
    the palette for every later request in the session.
    """
    if scheme is None:
        client.cookies.pop("al-scheme", None)
    else:
        client.cookies.set("al-scheme", scheme)
    try:
        response = client.get(f"/learn/lessons/{lesson_id}/preview")
    finally:
        client.cookies.pop("al-scheme", None)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["vary"] == "Cookie", (
        "the cookie is part of what selects this body"
    )
    return response.text


def test_the_placeholder_follows_the_app_theme_not_the_os(client):
    lesson_id = _file_less_lesson(client, "Placeholder scheme")

    night = _preview(client, lesson_id, "dark")
    assert "No HTML file yet." in night, "the placeholder is what is served"
    assert "color-scheme: dark" in night and NIGHT_BG in night, (
        "a dark reader gets the night palette outright — not a media query, "
        "which would answer with the OS and so invert the complaint for "
        "anyone whose theme is pinned against it"
    )
    assert "prefers-color-scheme" not in night, (
        "nothing is left for the OS to decide once the app has said"
    )

    day = _preview(client, lesson_id, "light")
    assert "color-scheme: light;" in day and NIGHT_BG not in day, (
        "a light reader gets paper even when the OS is dark"
    )
    assert "prefers-color-scheme" not in day


def test_an_unknown_scheme_falls_back_to_asking_the_os(client):
    lesson_id = _file_less_lesson(client, "Placeholder scheme fallback")

    for cookie in (None, "", "purple", "DARK"):
        html = _preview(client, lesson_id, cookie)
        assert "@media (prefers-color-scheme: dark)" in html, (
            f"{cookie!r} says nothing usable, so the OS answers — which is what "
            "the app's default 'system' theme resolves to anyway"
        )
        assert "color-scheme: light dark" in html, "both schemes stay available"


def test_a_real_bundle_is_served_byte_for_byte(client):
    """A lesson that HAS a file owns its own colours; nothing is injected."""
    from app.services import lessons as lessons_svc

    lesson_id = _file_less_lesson(client, "Placeholder scheme real file")
    body = "<!doctype html><html><body><p>the lesson itself</p></body></html>"
    (Path(lessons_svc.LESSONS_DIR) / _slug_of(lesson_id) / "index.html").write_text(
        body, encoding="utf-8"
    )

    served = _preview(client, lesson_id, "dark")
    assert served == body, "the bundle is returned unchanged, palette and all"
    assert NIGHT_BG not in served
