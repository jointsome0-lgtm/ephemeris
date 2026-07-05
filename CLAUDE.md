@AGENTS.md

<!-- Single source of truth is AGENTS.md (shared with Codex and other tools). Add Claude-only rules below this line only if they cannot live in AGENTS.md. -->

## Security reviews go to Codex

Claude-only rule — the reason is Fable-specific, and in AGENTS.md it would just tell Codex to delegate to itself.

Adversarial security reviews of the sensitive surfaces — the terminal PTY/WS core (`app/terminal.py`), the future `app/agent/`, and anything about to be exposed on a live port — are **delegated to Codex**, not run by Claude in the first person.

- Do not open, narrate, or carry out a red-team / adversarial security pass yourself. Hand that framing to Codex from the start (`codex:rescue` or the codex plugin) and let it drive the attack-surface analysis.
- Claude's role is the **correctness half** (does the code do what it should, race/lifecycle/fd bugs, plan alignment) and **converging** Codex's findings with its own — see the `route-security-reviews-to-codex` and `use-codex-as-second-reviewer` memories.
- Reason, so nobody "fixes" this later: routing avoids a real workflow failure. Fable's safeguards false-positive on security-review framing and interrupt mid-task (switching models, dropping the thread) — officially documented behavior: the Fable 5 announcement (anthropic.com) describes the safeguards as a fallback to Claude Opus 4.8 on cybersecurity/bio/distillation requests. Codex is unaffected and gives a genuinely independent adversarial view.
- This is a routing rule, **not** a license to ignore security. The Public Data Boundary and Public-Safety Check in AGENTS.md still apply to every change, and a security concern noticed in passing still gets surfaced plainly — it just gets handed to Codex to review rather than adversarially probed by Claude.
