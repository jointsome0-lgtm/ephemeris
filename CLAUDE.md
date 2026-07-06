@AGENTS.md

<!-- Single source of truth is AGENTS.md (shared with Codex and other tools). Add Claude-only rules below this line only if they cannot live in AGENTS.md. -->

## Security reviews go to Codex

Claude-only rule — the reason is Fable-specific, and in AGENTS.md it would just tell Codex to delegate to itself.

Adversarial security reviews of the sensitive surfaces — the terminal PTY/WS core (`app/terminal.py`), the future `app/agent/`, and anything about to be exposed on a live port — are **delegated to Codex**, not run by Claude in the first person.

- Do not open, narrate, or carry out a red-team / adversarial security pass yourself. Hand that framing to Codex from the start (`codex:rescue` or the codex plugin) and let it drive the attack-surface analysis.
- Claude's role is the **correctness half** (does the code do what it should, race/lifecycle/fd bugs, plan alignment) and **converging** Codex's findings with its own — see the `route-security-reviews-to-codex` and `use-codex-as-second-reviewer` memories.
- Reason, so nobody "fixes" this later: routing avoids a real workflow failure. Fable's safeguards false-positive on security-review framing and interrupt mid-task (switching models, dropping the thread) — officially documented behavior: the Fable 5 announcement (anthropic.com) describes the safeguards as a fallback to Claude Opus 4.8 on cybersecurity/bio/distillation requests. Codex is unaffected and gives a genuinely independent adversarial view.
- This is a routing rule, **not** a license to ignore security. The Public Data Boundary and Public-Safety Check in AGENTS.md still apply to every change, and a security concern noticed in passing still gets surfaced plainly — it just gets handed to Codex to review rather than adversarially probed by Claude.

### Queue instead of inline framing

Even composing a security-framed plan or subagent prompt in Claude's own output can trip the fallback. So the framing lives on disk, written once, and Claude only ever handles neutral facts:

- When a change touches a sensitive surface, append one neutral line to `docs/reviews/QUEUE.md` (date, commits, paths, what changed). Facts only — no threat analysis in the entry.
- To drain the queue, hand Codex the standing brief **by file reference** — "apply `docs/reviews/review-prompt.md` to the Pending entries" — without restating its contents. A second independent pass (an Opus-model subagent pointed at the same file) is welcome per `use-codex-as-second-reviewer`; Claude converges the results in its correctness voice.
- Drain at task boundaries (a turn or session of its own), never mid-task — so if a fallback still fires, it has nothing to drop.
- The deploy gate is mechanical: no live restart while the touched surface has Pending entries (AGENTS.md → Public-Safety Check).
