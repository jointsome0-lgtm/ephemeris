# Standing brief: draining the security review queue

Audience: the reviewer this brief is handed to (Codex via a direct `codex exec`, or
an independent review subagent). It is passed by file reference; apply it as
written. Written once so that queue entries and drain requests stay neutral.

For each Pending entry in `docs/reviews/QUEUE.md`:

1. Diff the listed commits and read the touched code in full, plus what it
   calls into. Run your own adversarial security review of that surface — you
   drive the attack-surface analysis; nothing here pre-scopes it.
2. Check earlier reports in `docs/reviews/` for the same surface and confirm
   their confirmed findings have not regressed.
3. Assume the posture documented in AGENTS.md: no auth in v0, live instance
   bound to loopback only. Flag anything that changes that posture, or that
   would matter the day the port is exposed more widely.
4. Write a severity-ranked report to `docs/reviews/<yyyy-mm-dd>-<slug>.md`.
   Public Data Boundary applies: invent every example from scratch — no real
   ledger data, task titles, or screenshots.
5. Move the entry to Done with a pointer to the report.

Report findings; do not auto-fix. Fixes land as their own dedicated change
(terminal-core changes never ride along with a cleanup pass — AGENTS.md).
