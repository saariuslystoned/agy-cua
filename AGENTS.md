# Repository agent guidance

agy-cua is a thin Antigravity integration over Cua Driver. Keep it thin: the
skill, the MCP config, the stdio shim, and proof tooling. Put driver behavior
changes upstream in trycua/cua, not here.

- Behavior claims need proof from `bin/agy-cua qualify` (result.json verdict,
  model, versions), not from an agent's own answer.
- Before committing, run `python3 -m unittest discover tests` and
  `shellcheck bin/agy-cua scripts/*.sh`.
- `bin/agy-cua-mcp` stays a pass-through except for the documented
  `snapshot_id` line. Remove it once trycua/cua#4346 ships in a released
  cua-driver, and update the skill in the same change.
- Never commit `runs/`, screenshots of real desktops, device identifiers,
  or Antigravity conversation files.
- `install`, `qualify`, and anything that changes Antigravity or macOS
  settings are user-run actions. Agents must ask first.
