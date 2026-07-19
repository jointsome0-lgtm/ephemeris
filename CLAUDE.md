# CLAUDE.md

Read [AGENTS.md](AGENTS.md) first: it is the shared agent contract for this
repository (the public-data boundary, rules for automated fix passes, the
public-safety check) and applies to Claude Code in full. The security-review
queue and its drain protocol live in
[docs/reviews/QUEUE.md](docs/reviews/QUEUE.md).

## Security reviews go to Codex

Claude-only rule — the reason is Fable-specific, and in AGENTS.md it would just tell Codex to delegate to itself.

Adversarial security reviews of the sensitive surfaces — the terminal PTY/WS core (`app/terminal.py`), the future `app/agent/`, and anything about to be exposed on a live port — are **delegated to Codex**, not run by Claude in the first person.

- Do not open, narrate, or carry out a red-team / adversarial security pass yourself. Hand that framing to Codex from the start (a direct `codex exec` with a self-contained prompt) and let it drive the attack-surface analysis.
- Claude's role is the **correctness half** (does the code do what it should, race/lifecycle/fd bugs, plan alignment) and **converging** Codex's findings with its own — see the `route-security-reviews-to-codex` and `use-codex-as-second-reviewer` memories.
- Reason, so nobody "fixes" this later: routing avoids a real workflow failure. Fable's safeguards false-positive on security-review framing and interrupt mid-task (switching models, dropping the thread) — officially documented behavior: the Fable 5 announcement (anthropic.com) describes the safeguards as a fallback to Claude Opus 4.8 on cybersecurity/bio/distillation requests. Codex is unaffected and gives a genuinely independent adversarial view.
- This is a routing rule, **not** a license to ignore security. The Public Data Boundary and Public-Safety Check in AGENTS.md still apply to every change, and a security concern noticed in passing still gets surfaced plainly — it just gets handed to Codex to review rather than adversarially probed by Claude.

### Queue instead of inline framing

Even composing a security-framed plan or subagent prompt in Claude's own output can trip the fallback. So the framing lives on disk, written once, and Claude only ever handles neutral facts:

- When a change touches a sensitive surface, append one neutral line to `docs/reviews/QUEUE.md` (date, commits, paths, what changed). Facts only — no threat analysis in the entry.
- To drain the queue, hand Codex the standing brief **by file reference** — "apply `docs/reviews/review-prompt.md` to the Pending entries" — without restating its contents. A second independent pass (an Opus-model subagent pointed at the same file) is welcome per `use-codex-as-second-reviewer`; Claude converges the results in its correctness voice.
- Drain at task boundaries (a turn or session of its own), never mid-task — so if a fallback still fires, it has nothing to drop.
- The deploy gate is mechanical: no live restart while the touched surface has Pending entries (AGENTS.md → Public-Safety Check).

## Picking the right models for workflows and subagents

Rankings, higher = better. Cost reflects what I actually pay (OpenAI has really
generous limits), not list price. Intelligence is how hard a problem you can
hand the model unsupervised. Taste covers UI/UX, code quality, API design, and
copy.

| model    | cost | intelligence | taste |
|----------|------|--------------|-------|
| gpt-5.6  | 9    | 8.9          | 7     |
| sonnet-5 | 5    | 5            | 7     |
| opus-4.8 | 4    | 7            | 8     |
| fable-5  | 2    | 9            | 9     |

How to apply:

- These are defaults, not limits. You have standing permission to override
  them: if a cheaper model's output doesn't meet the bar, rerun or redo the
  work with a smarter model without asking. Judge the output, not the price
  tag. Escalating costs less than shipping mediocre work.
- Cost is a tie-breaker only; when axes conflict for anything that ships,
  intelligence > taste > cost.
- The top two split by shape, not rank: fable-5 is stronger on architecture
  and interconnections; gpt-5.6 on driving a goal to completion and finding
  defects. Pick by task shape, not the raw intelligence number.
- Bulk/mechanical work (clear-spec implementation, data analysis, migrations):
  gpt-5.6 — it's effectively free.
- Anything user-facing (UI, copy, API design) needs taste ≥ 7.
- Reviews of plans/implementations: fable-5 or opus-4.8, optionally gpt-5.6 as
  an extra independent perspective.
- Never use Haiku.
- Mechanics: gpt-5.6 is only reachable through the Codex CLI — `codex exec` /
  `codex review` (my `~/.codex/config.toml` defaults to `gpt-5.6-sol` at xhigh
  effort). Always run `codex exec` directly via Bash
  with a self-contained prompt you wrote: `-s read-only` for pure
  reading/analysis; `-s workspace-write` when it must edit files OR run
  tests/builds — test runs write caches and temp state, so read-only makes
  them fail or stall (this produced a false "verify.py hangs" finding once).
  Health check: `codex --version` plus a trivial exec.
- Effort sizing (2026-07-19): the xhigh config default is for full
  adversarial/design passes only — open-ended search where a missed
  defect costs more than the hours. Scoped real work — implementing
  from a clear spec, diagnosing a named bug, reviewing a medium diff,
  prep/measurement tasks — gets `-c model_reasoning_effort=high`.
  Routine bounded checks — verifying a small diff, fidelity/gate
  checks, health checks — medium; trivial/relay: low (atlas
  2026-07-19: xhigh on a 42-line verify diff burned ~10x wall-time
  for no extra findings).
- Parallel codex execs are fragile (atlas 2026-07-16: an exec hung
  ~35 min behind parallel sessions) — prefer one lighter run over a
  fan-out; whole-diff consistency doesn't decompose per-finding.
- Claude models (sonnet-5, opus-4.8, fable-5) run via the Agent/Workflow model
  parameter.

Using gpt-5.6 inside workflows and subagents (the model parameter only takes
Claude models, so use a wrapper):

- Spawn a thin Claude wrapper agent with `model: 'sonnet', effort: 'low'` whose
  only job is to run `codex exec` via Bash and return the raw output verbatim.
- Write the full self-contained codex prompt yourself and pass it to the
  wrapper word-for-word. Wrappers never formulate or rewrite codex prompts —
  sonnet/opus don't write them well, and intermediary layers are banned — and
  you digest the result yourself.
- Treat codex claims (file:line, "tests are green", "done") as unverified until
  checked against artifacts. Codex is goal-driven and loves finding defects —
  excellent as a critic, so on long solo work call it at checkpoints
  (draft/diff → its findings → improve), not only at the end.
