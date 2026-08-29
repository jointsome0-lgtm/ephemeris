# CLAUDE.md

Read [AGENTS.md](AGENTS.md) first: it is the shared agent contract for this
repository and applies to Claude Code in full.

## Codex

- Run `codex exec` / `codex review` directly via Bash with a self-contained
  prompt you wrote. Always pass `-c model_reasoning_effort=<level>`; do not
  rely on the config default.
- `-s read-only` for pure reading or analysis; `-s workspace-write` when it
  must edit files or run tests/builds (test runs write caches, so read-only
  makes them fail or stall).
- Treat its claims (file:line, "tests are green", "done") as unverified until
  checked against artifacts.
- Never use Haiku for subagents.
