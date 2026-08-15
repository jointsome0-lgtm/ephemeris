from __future__ import annotations

import stat


def _use_agent_root(monkeypatch, lessons, tmp_path):
    agent_root = tmp_path / "agent-homes"
    host_config = tmp_path / "owner" / ".codex" / "config.toml"
    monkeypatch.setattr(lessons, "AGENT_HOMES_DIR", agent_root)
    monkeypatch.setattr(lessons, "HOST_CODEX_CONFIG", host_config)
    return agent_root, host_config


def test_agent_home_seeds_codex_config_once(monkeypatch, tmp_path):
    from app.services import lessons

    _, host_config = _use_agent_root(monkeypatch, lessons, tmp_path)
    host_config.parent.mkdir(parents=True)
    host_config.write_text('model = "invented-owner-default"\n', encoding="utf-8")

    home = lessons._ensure_agent_home("invented-lesson")
    lesson_config = home / "codex" / "config.toml"

    assert lesson_config.read_text(encoding="utf-8") == host_config.read_text(
        encoding="utf-8"
    )
    assert stat.S_IMODE(lesson_config.stat().st_mode) == 0o600

    lesson_config.write_text(
        '[projects."/invented/lesson"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )
    host_config.write_text('model = "changed-owner-default"\n', encoding="utf-8")

    assert lessons._ensure_agent_home("invented-lesson") == home
    assert lesson_config.read_text(encoding="utf-8") == (
        '[projects."/invented/lesson"]\ntrust_level = "trusted"\n'
    )


def test_agent_home_replaces_legacy_read_only_config_placeholder(
    monkeypatch, tmp_path
):
    from app.services import lessons

    agent_root, host_config = _use_agent_root(monkeypatch, lessons, tmp_path)
    host_config.parent.mkdir(parents=True)
    host_config.write_text('model = "invented-owner-default"\n', encoding="utf-8")
    codex_home = agent_root / "invented-lesson" / "codex"
    codex_home.mkdir(parents=True)
    (agent_root / "invented-lesson" / "claude").mkdir()
    placeholder = codex_home / "config.toml"
    placeholder.touch(mode=0o444)

    home = lessons._ensure_agent_home("invented-lesson")

    assert home == agent_root / "invented-lesson"
    assert placeholder.read_text(encoding="utf-8") == host_config.read_text(
        encoding="utf-8"
    )
    assert stat.S_IMODE(placeholder.stat().st_mode) == 0o600


def test_agent_home_without_host_config_leaves_writable_first_run(
    monkeypatch, tmp_path
):
    from app.services import lessons

    agent_root, _ = _use_agent_root(monkeypatch, lessons, tmp_path)
    codex_home = agent_root / "invented-lesson" / "codex"
    codex_home.mkdir(parents=True)
    (agent_root / "invented-lesson" / "claude").mkdir()
    placeholder = codex_home / "config.toml"
    placeholder.touch(mode=0o444)

    home = lessons._ensure_agent_home("invented-lesson")

    assert home == agent_root / "invented-lesson"
    assert not placeholder.exists()

    new_home = lessons._ensure_agent_home("invented-new-lesson")
    assert not (new_home / "codex" / "config.toml").exists()
