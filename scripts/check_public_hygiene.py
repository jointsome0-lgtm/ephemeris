"""Fail if private runtime/user data is visible to the public Git layer.

This is intentionally conservative. If a future sanitized demo fixture needs a
private-looking extension such as JSONL, add a narrow allowlist here in the same
change that creates the fixture.
"""
from __future__ import annotations

import fnmatch
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_GITIGNORE_PATTERNS = {
    "data/",
    "*.sqlite",
    "*.sqlite3",
    "*.sqlite-shm",
    "*.sqlite-wal",
    "*.db",
    "*.jsonl",
    ".env",
    ".env.*",
    ".playwright-mcp/",
    "playwright/.auth/",
    "docs/reference/screenshots/",
    "docs/reviews/.work/",
    "tt-*.png",
    "ticktick-*.png",
    "my-*.png",
    ".agents/",
    ".claude/",
    ".codex/",
}

PRIVATE_PATH_PATTERNS = (
    "data/**",
    ".env",
    ".env.*",
    ".playwright-mcp/**",
    "playwright/.auth/**",
    "docs/reference/screenshots/**",
    "docs/reviews/.work/**",
    ".agents/**",
    ".claude/**",
    ".codex/**",
    "tt-*.png",
    "tt-*.jpeg",
    "tt-*.jpg",
    "ticktick-*.png",
    "ticktick-*.jpeg",
    "ticktick-*.jpg",
    "my-*.png",
    "my-*.jpeg",
    "*.sqlite",
    "*.sqlite3",
    "*.sqlite-shm",
    "*.sqlite-wal",
    "*.db",
    "*.jsonl",
)


def git_output(*args: str) -> str:
    return subprocess.check_output(("git", *args), cwd=ROOT, text=True)


def candidate_git_files() -> list[str]:
    raw = subprocess.check_output(
        ("git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"),
        cwd=ROOT,
    )
    return [p.decode("utf-8") for p in raw.split(b"\0") if p]


def matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)


def main() -> int:
    errors: list[str] = []

    try:
        inside = git_output("rev-parse", "--is-inside-work-tree").strip()
    except subprocess.CalledProcessError:
        print("FAIL: not inside a Git work tree", file=sys.stderr)
        return 1
    if inside != "true":
        print("FAIL: not inside a Git work tree", file=sys.stderr)
        return 1

    gitignore = ROOT / ".gitignore"
    lines = {
        line.strip()
        for line in gitignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing = sorted(REQUIRED_GITIGNORE_PATTERNS - lines)
    if missing:
        errors.append("missing .gitignore protections: " + ", ".join(missing))

    leaked = sorted(p for p in candidate_git_files() if matches_any(p, PRIVATE_PATH_PATTERNS))
    if leaked:
        errors.append("private-looking files visible to Git: " + ", ".join(leaked))

    if errors:
        for err in errors:
            print(f"FAIL: {err}", file=sys.stderr)
        return 1

    print("PASS: public Git layer has no visible private runtime/user data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
