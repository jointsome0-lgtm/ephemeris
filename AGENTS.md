# Agent Operating Guide

## Public Data Boundary

Treat this repository as public by default.

Ephemeris is a [public engine](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/architecture.md):
it holds code, schemas/specs, docs, and invented demo fixtures. All private
runtime state lives in an explicitly configured [private instance](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/instance.md)
outside the checkout. The ecosystem [deletion contract](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/deletion.md)
defines how data leaves this ecosystem.

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

## Automated Fix Passes (/simplify, review --fix, and similar)

- `app/agent/` is learning code, written by the user by hand. Report findings there as explanations; never auto-edit the files.
- A previous pass's deliberate skip ("skipped, with reason") is a decision, not a suggestion. Do not apply it in a later cleanup pass; it may return only as its own separate change.
- The terminal PTY/WS core (`app/terminal.py`) is delicate (detach/reattach + fd lifecycle): changes to it go through their own dedicated review, never a ride-along cleanup pass.
- Scope passes to a fresh, small target (a path or the latest commit). Do not re-run a pass over the same accumulated unpushed pile.

## Public-Safety Check

Before finishing any change that touches storage, exports, docs, screenshots, tests, fixtures, or agent instructions, run:

```bash
python scripts/check_public_hygiene.py
git status --short --ignored
```

The app has no auth in v0. Keeping the code in public Git is acceptable; exposing a running instance to the public internet is not.

Changes touching `app/terminal.py`, `app/agent/`, or anything about to listen on a port get one entry appended to `docs/reviews/QUEUE.md` when they land, and do not go live while their entries are Pending (that file's header describes the drain protocol).
