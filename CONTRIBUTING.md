# Contributing

Ephemeris is a solo portfolio project, developed in the open. Issues are
welcome — bug reports, questions about the design, and rough edges you hit while
running it. For anything larger than a typo, please open an issue and agree on
the shape before writing a pull request; unsolicited large PRs are likely to be
declined simply because they do not match where the project is going.

Security problems go through [`SECURITY.md`](SECURITY.md), not a public issue.

## Local gate

```bash
uv sync
uv run ruff check .
uv run pytest
uv run pytest tests/test_010_platform_ui.py tests/test_020_bundle_attempts.py  # ordered prefix
uv run python verify_restore.py
uv run python scripts/check_public_hygiene.py
```

All of these must pass before a PR, and CI must be green on the PR head.

CI adds three things the local gate does not: the suite runs on Python 3.10, 3.12
and 3.13 rather than only the deployed 3.10; `pip-audit` checks the locked
runtime dependencies against the advisory database and blocks on a hit; and
`gitleaks` scans both the history and the checked-out tree for credentials.
Ruff's rule set is deliberately narrow (`E`, `F`, `W`) and there is no
formatter — see the `TODO(#21)` comments in `pyproject.toml` for what is
knowingly switched off and why.

## Ground rules

- Read [`AGENTS.md`](AGENTS.md) — it is the working contract for this repository,
  including the public-data boundary. Nothing from a real ledger (task titles,
  notes, dates, screenshots, exports) belongs in code, docs, tests, or fixtures;
  invent examples instead.
- Changes to `app/terminal.py` or anything that listens on a port get their own
  security review; see [`docs/reviews/QUEUE.md`](docs/reviews/QUEUE.md).
- Keep the commit history readable: one logical change per commit, present-tense
  subject line.
