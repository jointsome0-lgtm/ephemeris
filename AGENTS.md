# Agent Operating Guide

## Public Data Boundary

Treat this repository as public by default.

Allowed in Git:

- Application source code under `app/`.
- Public documentation under `docs/`, after checking it contains no real user data.
- Public demo fixtures only when the data is invented from scratch and clearly named as demo data.

Never add to Git:

- Runtime data under `data/`, including `activity.sqlite`, WAL/SHM sidecars, backups, and `data/exports/`.
- Raw JSONL or Markdown exports of the activity ledger.
- `.env` files, credentials, cookies, auth/session state, Playwright storage state, or local browser profiles.
- Raw screenshots or captures from TickTick, local visual QA, or authenticated browser automation.
- Local agent/tool state such as `.agents/`, `.codex/`, `.claude/`, `.playwright-mcp/`, and review work directories.

Do not copy real task titles, habit names, notes, dates, screenshots, export rows, or database rows into docs, tests, prompts, fixtures, review reports, or issue text. If an example is needed, invent it from scratch.

## Public-Safety Check

Before finishing any change that touches storage, exports, docs, screenshots, tests, fixtures, or agent instructions, run:

```bash
python scripts/check_public_hygiene.py
git status --short --ignored
```

The app has no auth in v0. Keeping the code in public Git is acceptable; exposing a running instance to the public internet is not.
