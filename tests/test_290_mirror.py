"""#128: the exp2res Mirror embed — a configured peer URL as its own surface.

Three states, settings and route level: unset (no nav entry, no route), a
loopback URL (sandboxed iframe + a deliberate frame-src CSP), and a
non-loopback URL (treated as unset with one warning). The Diary strip rides
along: the page that already embeds now states its frame-src too.
"""
from __future__ import annotations

import dataclasses

import app.settings as app_settings
from app.security import embed_frame_csp
from app.settings import load

MIRROR_URL = "http://127.0.0.1:8731/mirror?scope=global"
MIRROR_CSP = "frame-ancestors 'none'; frame-src http://127.0.0.1:8731"
DEFAULT_CSP = "frame-ancestors 'none'"


def _load(tmp_path, **extra):
    env = {"ACTIVITY_DATA_DIR": str(tmp_path)}
    env.update(extra)
    return load(env)


def test_settings_three_states(tmp_path, capsys):
    assert _load(tmp_path).exp2res_mirror_url is None, "unset stays None"

    for good in (MIRROR_URL,
                 "http://[::1]:8731/mirror?scope=global",
                 "http://localhost:8731/mirror?scope=global"):
        assert _load(tmp_path, SELFOS_EXP2RES_MIRROR_URL=good).exp2res_mirror_url == good
    assert capsys.readouterr().err == "", "loopback URLs load without a warning"

    for bad in ("http://192.168.0.7:8731/mirror?scope=global",
                "http://mirror.example/mirror?scope=global",
                "https://127.0.0.2:8731/mirror?scope=global",
                "javascript:alert(1)",
                "not a url"):
        assert _load(tmp_path, SELFOS_EXP2RES_MIRROR_URL=bad).exp2res_mirror_url is None, bad
        err = capsys.readouterr().err
        assert err.count("\n") == 1 and "SELFOS_EXP2RES_MIRROR_URL" in err, (
            f"exactly one warning line for {bad!r} -- {err!r}")

    # Decision 4 retrofit: the diary strip's variable gets the same check.
    assert _load(
        tmp_path, SELFOS_EXP2RES_URL="http://10.0.0.5:8731/questions?scope=global"
    ).exp2res_url is None
    assert "SELFOS_EXP2RES_URL" in capsys.readouterr().err


def test_embed_frame_csp_origins():
    assert embed_frame_csp(MIRROR_URL) == MIRROR_CSP
    assert embed_frame_csp("http://[::1]:8731/mirror?scope=global") == (
        "frame-ancestors 'none'; frame-src http://[::1]:8731")
    assert embed_frame_csp("http://localhost/questions") == (
        "frame-ancestors 'none'; frame-src http://localhost")


def test_mirror_unset_no_surface(client):
    assert app_settings.settings.exp2res_mirror_url is None, (
        "test env must not set SELFOS_EXP2RES_MIRROR_URL"
    )
    assert client.get("/mirror").status_code == 404, "no configured URL, no page"
    r = client.get("/retro")
    assert 'href="/mirror"' not in r.text, "no nav entry when unset"
    assert r.headers["content-security-policy"] == DEFAULT_CSP
    views = client.get("/palette.json").json()["views"]
    assert all(v["label"] != "Mirror" for v in views), "palette gated too"


def test_mirror_configured(client, monkeypatch):
    monkeypatch.setattr(
        app_settings, "settings",
        dataclasses.replace(app_settings.settings, exp2res_mirror_url=MIRROR_URL))
    r = client.get("/mirror")
    assert r.status_code == 200
    assert MIRROR_URL in r.text, "the configured URL is what the iframe loads"
    assert "<iframe" in r.text and "sandbox" in r.text, "embed iframe is sandboxed"
    assert "works fine without it" in r.text, (
        "honest unavailable state: the caption says what an empty frame means"
    )
    assert r.headers["content-security-policy"] == MIRROR_CSP, (
        "the embedding page carries the deliberate frame-src"
    )
    assert 'href="/mirror"' in r.text, "rail/More entries appear when configured"
    views = client.get("/palette.json").json()["views"]
    assert {"label": "Mirror", "href": "/mirror", "icon": "mirror"} in views
    labels = [v["label"] for v in views]
    assert labels.index("Mirror") == labels.index("Diary") + 1, "rail order"
    assert client.get("/retro").headers["content-security-policy"] == DEFAULT_CSP, (
        "frame-src is stated only on pages that embed"
    )


def test_diary_csp_follows_its_strip(client, monkeypatch):
    assert client.get("/diary").headers["content-security-policy"] == DEFAULT_CSP
    strip_url = "http://127.0.0.1:8731/questions?scope=global"
    monkeypatch.setattr(
        app_settings, "settings",
        dataclasses.replace(app_settings.settings, exp2res_url=strip_url))
    assert client.get("/diary").headers["content-security-policy"] == MIRROR_CSP
