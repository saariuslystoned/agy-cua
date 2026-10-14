# AGENTS.md — `saariuslystoned/agy-cua`

## Repository Scope & Purpose
`agy-cua` packages Cua Driver (`agy-cua`) and bounded Jev action selection (`agy-cua-jev`, pinned to `jev-1.13.0`) as an installable Google Antigravity plugin for background macOS app automation.

## Pull Request Code Review Policy (Repo-Specific)
- **Use GitHub Copilot Code Review exclusively** on `saariuslystoned/agy-cua` pull requests via the workspace [`code-review` skill](file:///.agents/skills/code-review/SKILL.md) and the GitHub Copilot [`.github/skills/code-review/SKILL.md`](file:///.github/skills/code-review/SKILL.md) skill (`copilot-pull-request-reviewer`, GraphQL bot ID `BOT_kgDOCnlnWA`).
- Do **not** run `smoky-openclaw-autoreview` (Spark-2 / OpenClaw / ClawSweeper / EmDashBot) on this repository.
- Keep [`code-review`](file:///.agents/skills/code-review/SKILL.md) under `.agents/skills/` and `.github/skills/` only—never under top-level `skills/`, which is reserved for skills packaged globally by `agy plugin install` (`skills/cua-computer-use`).

## Hard Constraints
- Keep [`bin/agy-cua-mcp`](file:///bin/agy-cua-mcp) as a minimal pass-through shim to `cua-driver mcp`.
- Keep [`bin/agy-cua-jev-mcp`](file:///bin/agy-cua-jev-mcp) dependency-free (Python 3 standard library only).
- Never read, print, log, or commit the `typesafe-jev` Keychain secret or `TYPESAFE_API_KEY`.
