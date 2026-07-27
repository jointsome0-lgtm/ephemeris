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
python verify.py            # both suites create their own throwaway data dir
python verify_restore.py
python scripts/check_public_hygiene.py
```

All of these must pass before a PR, and CI must be green on the PR head.

## Ground rules

- Read [`AGENTS.md`](AGENTS.md) — it is the working contract for this repository,
  including the public-data boundary. Nothing from a real ledger (task titles,
  notes, dates, screenshots, exports) belongs in code, docs, tests, or fixtures;
  invent examples instead.
- Changes to `app/terminal.py` or anything that listens on a port get their own
  security review; see [`docs/reviews/QUEUE.md`](docs/reviews/QUEUE.md).
- Keep the commit history readable: one logical change per commit, present-tense
  subject line.
