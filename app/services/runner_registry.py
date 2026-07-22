"""Pure registry of fixed, single-file lesson runners.

This module is deliberately a leaf: it imports no Ephemeris modules.  Bundle
readers and the process-owning runner service may therefore share the same
specifications without creating an application-layer dependency cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


SNAPSHOT_PATH = "{snapshot_path}"
MAX_WALL_SECONDS = 120


@dataclass(frozen=True)
class RunnerSpec:
    """One fixed argv template and its bounded runner policy."""

    argv: tuple[str, ...]
    suffixes: tuple[str, ...]
    wall_seconds: int = 30

    def __post_init__(self) -> None:
        if not self.argv or sum(part == SNAPSHOT_PATH for part in self.argv) != 1:
            raise ValueError("runner argv must contain exactly one snapshot placeholder")
        if any(not part or "\x00" in part for part in self.argv):
            raise ValueError("runner argv entries must be non-empty and NUL-free")
        if (
            not self.suffixes
            or any(not suffix.startswith(".") or "/" in suffix for suffix in self.suffixes)
        ):
            raise ValueError("runner suffixes must be non-empty filename suffixes")
        if not 1 <= self.wall_seconds <= MAX_WALL_SECONDS:
            raise ValueError("runner wall limit is outside the global ceiling")

    def command(self, snapshot_path: str) -> tuple[str, ...]:
        if not snapshot_path.startswith("/") or "\x00" in snapshot_path:
            raise ValueError("snapshot path must be absolute and NUL-free")
        return tuple(
            snapshot_path if part == SNAPSHOT_PATH else part for part in self.argv
        )

    def accepts(self, filename: str) -> bool:
        return filename.endswith(self.suffixes)


RUNNER_REGISTRY: Mapping[str, RunnerSpec] = MappingProxyType({
    "python-script-v1": RunnerSpec(
        argv=("/usr/bin/python3", SNAPSHOT_PATH),
        suffixes=(".py",),
        wall_seconds=30,
    ),
    "go-run-v1": RunnerSpec(
        argv=("/usr/local/go/bin/go", "run", SNAPSHOT_PATH),
        suffixes=(".go",),
        wall_seconds=60,
    ),
})
