# Agent Operating Guide

Ephemeris is the public engine of the
[selfos](https://github.com/jointsome0-lgtm/selfos) ecosystem. Peer systems
stay separate and are integrated by configured URLs only
([selfos#25](https://github.com/jointsome0-lgtm/selfos/issues/25)).

## Stack

Python (backend, via `uv`) and TypeScript (browser, `app/static/src/*.ts` →
`bun run build` → committed JS). Anything else needs a reason in the PR.

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

Diary content (sec35) stays out of agent context by default (selfos AGENTS.md
→ "Cloud-context data boundary"); `private` entries are an absolute deny.

## Automated Fix Passes (/simplify, review --fix, and similar)

- A previous pass's deliberate skip ("skipped, with reason") is a decision, not a suggestion. Do not apply it in a later cleanup pass; it may return only as its own separate change.
- Scope passes to a fresh, small target (a path or the latest commit). Do not re-run a pass over the same accumulated unpushed pile.

## Review Findings: Don't Over-Engineer

Review critics over-produce. Act on findings that name a concrete failure — wrong behaviour, crash, data loss, broken contract — in the app as deployed: single user, loopback-only, no auth, one worker. Reject the rest with the reason written down: hardening for a deployment this project does not have, abstraction for hypothetical futures, style rewrites. Real defects are never what gets waved off.

## Git worktrees

- Create them only in `.worktrees/<name>` inside the repo (globally gitignored via `~/.config/git/ignore`), never as sibling directories.
- Any work that will open a PR branches and builds in such a worktree, never in the primary checkout — the primary checkout stays on a clean `main` so parallel sessions don't fight for its index.
- Trivial read-only work and single-file doc edits on a clean main need no worktree.
- Remove the worktree and delete its local branch once its PR merges.

## Style

- Avoid code comments unless explicitly asked to add comments.
- Deliver what was asked, at the scope asked — no extra features,
  refactoring, or abstractions beyond the task.
- In prose (PR text, docs, summaries): lead with the outcome, cut
  anything that doesn't change what the reader does next.
