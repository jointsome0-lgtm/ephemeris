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
* `data_dir` is not created here. `app.db.get_conn()` / `init_db()` still mkdir
  on demand, so importing this module has no filesystem side effects.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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

    timezone: str | None
    """`APP_TIMEZONE` as typed, or None to mean 'host local zone' (sec13.3)."""


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
        timezone=env.get("APP_TIMEZONE"),
    )


settings = load()
