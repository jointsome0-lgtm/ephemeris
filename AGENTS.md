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

## Automated Fix Passes (/simplify, review --fix, and similar)

- `app/agent/` is learning code, written by the user by hand. Report findings there as explanations; never auto-edit the files.
- A previous pass's deliberate skip ("skipped, with reason") is a decision, not a suggestion. Do not apply it in a later cleanup pass; it may return only as its own separate change.
- The terminal PTY/WS core (`app/terminal.py`) is delicate (detach/reattach + fd lifecycle): changes to it go through their own dedicated review, never a ride-along cleanup pass.
- Scope passes to a fresh, small target (a path or the latest commit). Do not re-run a pass over the same accumulated unpushed pile.

## Security Reviews Go To Codex

Adversarial security reviews of the sensitive surfaces — the terminal PTY/WS core (`app/terminal.py`), the future `app/agent/`, and anything about to be exposed on a live port — are **delegated to Codex**, not run by Claude in the first person.

- Do not open, narrate, or carry out a red-team / adversarial security pass yourself. Hand that framing to Codex from the start (`codex:rescue` or the codex plugin) and let it drive the attack-surface analysis.
- Claude's role is the **correctness half** (does the code do what it should, race/lifecycle/fd bugs, plan alignment) and **converging** Codex's findings with its own — see the `route-security-reviews-to-codex` and `use-codex-as-second-reviewer` memories.
- Reason, so nobody "fixes" this later: routing avoids a real workflow failure. Fable's safeguards false-positive on security-review framing and interrupt mid-task (switching models, dropping the thread); Codex is unaffected and gives a genuinely independent adversarial view.
- This is a routing rule, **not** a license to ignore security. The Public Data Boundary above and the Public-Safety Check below still apply to every change, and a security concern noticed in passing still gets surfaced plainly — it just gets handed to Codex to review rather than adversarially probed by Claude.

## Public-Safety Check

Before finishing any change that touches storage, exports, docs, screenshots, tests, fixtures, or agent instructions, run:

```bash
python scripts/check_public_hygiene.py
git status --short --ignored
```

The app has no auth in v0. Keeping the code in public Git is acceptable; exposing a running instance to the public internet is not.
