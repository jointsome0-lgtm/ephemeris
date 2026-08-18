"""Typed configuration (#24 cut 5): the one place that reads the environment.

Every `os.environ` lookup that configures the *app* used to sit inline in
app/db.py, which meant the configuration contract was something you learned by
grepping. It is now a frozen dataclass built once at import, so the full set of
knobs — their names, their defaults, and which one is required — is readable in
one screen and cannot be mutated after startup.

Stdlib only, on purpose: a settings library would be a new dependency for three
variables.

Deliberately NOT here: the terminal's `EPHEMERIS_TERM_*` / `EPHEMERIS_ENABLE_*`
variables. They belong to app/terminal.py, which owns that surface and reads
them itself; centralising them would move part of a sensitive surface's
contract into a module that has no other business with it.

Two things stay lazy on purpose, because collapsing them would change a
failure mode rather than just tidy one:

* `timezone` is kept as the raw string, not a `ZoneInfo`. Building the zone
  here would turn a bad `APP_TIMEZONE` into an import-time crash of the whole
  process; today it raises from `app.db.app_tz()` at first use, and that is
  the behaviour callers are written against.

  What DID change, deliberately: the variable is read once, at import, so
  mutating `os.environ["APP_TIMEZONE"]` afterwards no longer affects
  `app_tz()`. That is the point of resolving configuration once — the ledger
  clock cannot be repointed at a different zone by a later `os.environ`
  write, which would silently change what "today" means mid-process. Nothing
  in the app or the tests mutates it after import; a test that needs another
  zone should build its own `Settings` via `load({...})`.
* `data_dir` is not created here. `app.db.get_conn()` / `init_db()` still mkdir
  on demand, so importing this module has no filesystem side effects.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_MISSING_DATA_DIR = (
    "ACTIVITY_DATA_DIR is required: the destination must be an explicitly "
    "configured private path outside the public checkout (for example, "
    "~/.local/share/ephemeris); see "
    "https://github.com/jointsome0-lgtm/selfos/blob/main/docs/instance.md"
)


@dataclass(frozen=True)
class Settings:
    """Resolved app configuration. Frozen: startup decides, nothing re-decides."""

    data_dir: Path
    """`ACTIVITY_DATA_DIR` — required, private, outside the public checkout."""

    db_path: Path
    """`ACTIVITY_DB`, else `<data_dir>/activity.sqlite`."""

    exports_dir: Path
    """`<data_dir>/exports` — where the JSONL backups land (sec18.1)."""

    backups_dir: Path
    """`<data_dir>/backups` — where full backup sets land (issue #17).

    The app never writes here; `scripts/backup_db.py` does. It is resolved here
    anyway so the name has one owner: the script used to compute it itself, and
    the /export status panel reads it, which is exactly the arrangement where
    two spellings drift apart.
    """

    timezone: str | None
    """`APP_TIMEZONE` as typed, or None to mean 'host local zone' (sec13.3)."""

    exp2res_url: str | None
    """`SELFOS_EXP2RES_URL` — the exp2res gap-questions view the Diary tab
    embeds by URL (sec35), or None to render no strip. Config-only coupling:
    the value is rendered, never fetched or parsed, and the only supported
    topology is same-machine loopback (exp2res §30 refuses anything else)."""

    exp2res_mirror_url: str | None
    """`SELFOS_EXP2RES_MIRROR_URL` — the exp2res global Mirror view the
    /mirror surface embeds by URL (#128), or None for no surface at all.
    Same config-only coupling and loopback-only topology as `exp2res_url`."""


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _loopback_embed_url(raw: str | None, var: str) -> str | None:
    """The configured embed URL, or None when it is unset or not loopback.

    The only supported topology is same-machine loopback (exp2res §30), so a
    URL whose host is anything else is treated as unset — one warning line,
    then the surface simply doesn't render — rather than handed to an iframe.
    """
    url = (raw or "").strip()
    if not url:
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        parts = None
        host = None
    if parts is None or parts.scheme not in ("http", "https") or host not in _LOOPBACK_HOSTS:
        print(f"{var} ignored: not a loopback http(s) URL: {url!r}", file=sys.stderr)
        return None
    return url


def load(env: dict[str, str] | None = None) -> Settings:
    """Resolve settings from `env` (default: the real environment).

    Raises RuntimeError if ACTIVITY_DATA_DIR is unset or empty — the app must
    never guess where private data lives.
    """
    env = os.environ if env is None else env
    data_dir = env.get("ACTIVITY_DATA_DIR")
    if not data_dir:
        raise RuntimeError(_MISSING_DATA_DIR)
    root = Path(data_dir)
    return Settings(
        data_dir=root,
        db_path=Path(env.get("ACTIVITY_DB", root / "activity.sqlite")),
        exports_dir=root / "exports",
        backups_dir=root / "backups",
        timezone=env.get("APP_TIMEZONE"),
        exp2res_url=_loopback_embed_url(
            env.get("SELFOS_EXP2RES_URL"), "SELFOS_EXP2RES_URL"),
        exp2res_mirror_url=_loopback_embed_url(
            env.get("SELFOS_EXP2RES_MIRROR_URL"), "SELFOS_EXP2RES_MIRROR_URL"),
    )


settings = load()
