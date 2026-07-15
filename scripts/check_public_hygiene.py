"""Enforce the public Git-layer boundary for this repository.

The policy is documented at
https://github.com/jointsome0-lgtm/selfos/blob/main/docs/hygiene.md. This check
rejects known private-data paths, requires the matching .gitignore rules, and
requires the Vera Example marker in demo fixtures. If a legitimate public file
matches a private-data pattern, add a narrow allowlist entry in the same change
that adds the file.
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_GITIGNORE_PATTERNS = {
    "atlas/",
    "data/",
    "state/",
    "intake/",
    "graph/",
    "plans/",
    "*.sqlite*",
    "*.db*",
    "*.jsonl",
    ".env",
    ".env.*",
    "engine.pin",
    "copies-manifest",
    "delivery-registry",
    ".claude/",
    ".codex/",
    ".agents/",
    ".playwright-mcp/",
    "playwright/.auth/",
    "docs/reference/screenshots/",
    "docs/reviews/.work/",
    "tt-*.png",
    "tt-*.jpeg",
    "tt-*.jpg",
    "ticktick-*.png",
    "ticktick-*.jpeg",
    "ticktick-*.jpg",
    "my-*.png",
    "my-*.jpeg",
}

# Directory names denied at any depth, matching gitignore's
# slash-free directory semantics ("state/" matches nested state/).
DENIED_DIR_NAMES = frozenset(
    {
        "atlas",
        "data",
        "state",
        "intake",
        "graph",
        "plans",
        ".claude",
        ".codex",
        ".agents",
        ".playwright-mcp",
    }
)

# File patterns denied at any depth, matched against the basename —
# "*.sqlite*" also covers -journal/-shm/-wal sidecars.
DENIED_BASENAME_PATTERNS = (
    "*.sqlite*",
    "*.db*",
    "*.jsonl",
    ".env",
    ".env.*",
    "engine.pin",
    "copies-manifest",
    "delivery-registry",
    "tt-*.png",
    "tt-*.jpeg",
    "tt-*.jpg",
    "ticktick-*.png",
    "ticktick-*.jpeg",
    "ticktick-*.jpg",
    "my-*.png",
    "my-*.jpeg",
)

# Repo-specific private paths whose directory names are not globally denied.
DENIED_PATH_PATTERNS = (
    "playwright/.auth",
    "playwright/.auth/**",
    "docs/reference/screenshots",
    "docs/reference/screenshots/**",
    "docs/reviews/.work",
    "docs/reviews/.work/**",
)

FIXTURE_PATH_PATTERNS = ("fixtures/**",)

DENIED_PATH_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Add only exact paths here, with a comment explaining each exception.
    }
)


def git_paths(*args: str) -> set[str]:
    raw = subprocess.check_output(("git", "ls-files", "-z", *args), cwd=ROOT)
    return {
        path.decode("utf-8", errors="surrogateescape")
        for path in raw.split(b"\0")
        if path
    }


def candidate_git_files() -> tuple[set[str], set[str]]:
    """Return (cached, untracked-unignored) repository paths."""
    return git_paths("--cached"), git_paths("--others", "--exclude-standard")


def published_content(path: str, cached: set[str]) -> bytes:
    """Read what the public Git layer would publish for this path.

    For a cached path that is the staged blob, not the working tree —
    an unstaged edit must not mask what a commit would publish (an
    unmarked staged fixture, a staged .gitignore losing a pattern).
    """
    if path in cached:
        return subprocess.check_output(("git", "show", f":{path}"), cwd=ROOT)
    return (ROOT / path).read_bytes()


def matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def check_gitignore(cached: set[str]) -> list[str]:
    """Validate the published .gitignore against the required patterns.

    Lines are taken verbatim (rstrip only): git treats a leading space
    literally, so an indented " data/" must not count as "data/". A
    negation re-including a required pattern is an error too.
    """
    if ".gitignore" not in cached:
        return [".gitignore must be tracked in the index"]

    text = published_content(".gitignore", cached).decode("utf-8")
    lines = [line.rstrip() for line in text.splitlines()]
    patterns = {line for line in lines if line and not line.startswith("#")}

    errors = [
        f".gitignore missing required pattern: {pattern}"
        for pattern in sorted(REQUIRED_GITIGNORE_PATTERNS - patterns)
    ]
    errors.extend(
        f".gitignore negates required pattern: {line}"
        for line in lines
        if line.startswith("!") and line[1:] in REQUIRED_GITIGNORE_PATTERNS
    )
    return errors


def main() -> int:
    errors: list[str] = []

    try:
        cached, untracked = candidate_git_files()
        candidates = cached | untracked
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = str(exc).replace("\n", " ")
        print(f"FAIL: cannot inspect the public Git layer: {detail}", file=sys.stderr)
        return 1

    try:
        errors.extend(check_gitignore(cached))
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = str(exc).replace("\n", " ")
        print(f"FAIL: cannot read .gitignore: {detail}", file=sys.stderr)
        return 1

    for path in sorted(candidates):
        # Every component counts, the last one included: a gitlink or
        # directory named like a denied root ("atlas") or a denied
        # file pattern (".env/token") is denied too. A legitimate
        # exception goes through the allowlist.
        parts = Path(path).parts
        denied = (
            any(
                part in DENIED_DIR_NAMES
                or matches_any(part, DENIED_BASENAME_PATTERNS)
                for part in parts
            )
            or matches_any(path, DENIED_PATH_PATTERNS)
        )
        if denied and path not in DENIED_PATH_ALLOWLIST:
            errors.append(f"denied path visible to the public Git layer: {path}")

        if matches_any(path, FIXTURE_PATH_PATTERNS):
            try:
                fixture = published_content(path, cached)
            except (OSError, subprocess.CalledProcessError) as exc:
                detail = str(exc).replace("\n", " ")
                errors.append(f"cannot read fixture {path}: {detail}")
            else:
                if b"Vera Example" not in fixture:
                    errors.append(
                        f"fixture lacks required marker 'Vera Example': {path}"
                    )

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print("OK: public Git layer has no denied paths or unmarked demo fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
