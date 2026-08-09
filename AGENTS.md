# Agent Operating Guide

## Current stage

Ephemeris is an implementation repository. No repository-wide SDD freeze
applies: changes proceed through the normal issue, security, test, review-queue,
and PR protocols.

Integration v1 follows
[selfos#25](https://github.com/jointsome0-lgtm/selfos/issues/25): peer systems
remain separate and are integrated by configured URLs only, with deterministic
cross-system adapters owned by Selfos. The Ephemeris issues that carried it —
[#1](https://github.com/jointsome0-lgtm/ephemeris/issues/1),
[#17](https://github.com/jointsome0-lgtm/ephemeris/issues/17),
[#35](https://github.com/jointsome0-lgtm/ephemeris/issues/35),
[#36](https://github.com/jointsome0-lgtm/ephemeris/issues/36),
[#38](https://github.com/jointsome0-lgtm/ephemeris/issues/38),
[#39](https://github.com/jointsome0-lgtm/ephemeris/issues/39) — are all closed
as of 2026-08-07;
[#2](https://github.com/jointsome0-lgtm/ephemeris/issues/2) (Diary tab) is the
one still open.

The existing special review rules for `app/terminal.py`, `app/agent/`, and
listening surfaces remain binding.

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

## Automated Fix Passes (/simplify, review --fix, and similar)

- `app/agent/` is learning code, written by the user by hand. Report findings there as explanations; never auto-edit the files.
- A previous pass's deliberate skip ("skipped, with reason") is a decision, not a suggestion. Do not apply it in a later cleanup pass; it may return only as its own separate change.
- The terminal PTY/WS core (`app/terminal.py`) is delicate (detach/reattach + fd lifecycle): changes to it go through their own dedicated review, never a ride-along cleanup pass.
- Scope passes to a fresh, small target (a path or the latest commit). Do not re-run a pass over the same accumulated unpushed pile.

## Review Findings: Don't Over-Engineer

Review critics over-produce. Act on findings that name a concrete failure — wrong behaviour, crash, data loss, broken contract — in the app as deployed: single user, loopback-only, no auth, one worker. Reject the rest with the reason written down: hardening for a deployment this project does not have, abstraction for hypothetical futures, style rewrites. Real defects are never what gets waved off.

## Public-Safety Check

Before finishing any change that touches storage, exports, docs, screenshots, tests, fixtures, or agent instructions, run:

```bash
python scripts/check_public_hygiene.py
git status --short --ignored
```

The app has no auth in v0. Keeping the code in public Git is acceptable; exposing a running instance to the public internet is not.

Review-queue entries:

- Changes touching `app/terminal.py`, `app/agent/`, a new bundle file the lesson sandboxes read, or anything about to listen on a port get one entry appended to `docs/reviews/QUEUE.md` when they land.
- Those changes do not go live while their entries are Pending. That file's header describes the drain protocol.
- Nothing else earns an entry: UI, templates, and portability work go straight to the deploy gate.

Git worktrees:

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
