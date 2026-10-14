# agy-cua

Background computer use for [Google Antigravity](https://antigravity.google)
(the app and the `agy` CLI), powered by the open-source
[Cua Driver](https://github.com/trycua/cua/tree/main/libs/cua-driver).

Antigravity doesn't ship a desktop computer-use tool, and Gemini's
computer-use model is sold through the Gemini API rather than included in
Antigravity plans. agy-cua uses ordinary MCP tool calls, so it runs on the
plan you already pay for. Your agent can open a Mac app, read its window,
click, type, and check the result, all while you keep working: the target app
stays in the background and your cursor doesn't move.

> Unofficial community project. Not affiliated with Google or Cua.

## Status

v0.2.0, macOS only. Qualified across both the **Antigravity App (IDE)** and the **`agy` CLI** (`bin/agy-cua qualify --installed`) with **Gemini 4 Argon (`gemini-4-argon-high`)** and **Jev (`jev-1.13.0`)**:

| Surface / Run (2026-10-14, `cua-driver 0.30.4`) | Model / Selector | Action / Target | Result | Timing | Focus stolen |
| --- | --- | --- | --- | --- | --- |
| Antigravity IDE (`call_mcp_tool` on `agy-cua_agy-cua`) | Gemini 4 Argon | Calculator `73 × 29` (`click` by `snapshot_id`) | PASS (`2,117`) | ~2.5 s/click | never |
| Antigravity IDE (`call_mcp_tool` on `agy-cua_agy-cua-jev`) | Gemini 4 Argon + `jev-1.13.0` (`jev_step`) | Calculator `73 × 29` (6 guarded steps, `s0000025b` → `s00000266`) | PASS (`2,117`, `uncertain_calls=0`) | `provider_ms` 193–304 ms/step | never |
| `bin/agy-cua qualify --installed` (`agy 1.3.5`) | `gemini-4-argon-high` + `jev-1.13.0` | Calculator `24 × 21` (including automatic Delete recovery on step 6) | PASS (`504`, `errors=0`) | `provider_median_ms=258.82`, `dispatch_median_ms=2582.95` | 0 samples |
| Background capability & refusal matrix (`jev_step`) | `jev-1.13.0` + `cua-driver` | `click`, `double_click`, `right_click`, `set_value` (`AXTextField`), `type_text`, `press_key`, `hotkey`, `scroll`, plus `stale_snapshot_id`, `out_of_scope_target`, `foreground`, and `drag` refusals | PASS (`verified=true` / deterministic refusals) | `provider_ms` 217–293 ms | never |

Qualified in background (`delivery_mode="background"`): `launch_app`, `get_window_state`, `click` / `double_click` / `right_click`, `set_value`, `type_text`, `press_key`, `hotkey`, and `scroll`.
Refused in background-only mode: `drag` (`background_drag_unavailable_on_macos`), `delivery_mode="foreground"`, `bring_to_front`, `get_desktop_state`, `clipboard_*`, `kill_app`, `set_config`, `browser_*`, `stale_snapshot_id`, and `out_of_scope_target`.

## How it works

```text
Antigravity (app or agy) --stdio MCP--> bin/agy-cua-mcp -----> cua-driver mcp --> CuaDriver.app daemon --> target app
                         --stdio MCP--> bin/agy-cua-jev-mcp -> Jev (jev-1.13.0) + guarded cua-driver loop
                  ^ skills/cua-computer-use teaches the loop
```

- **Cua Driver** does the real OS work. It sends accessibility actions and input
  to one process without stealing focus, and returns exact refusal codes when
  it can't. The signed `CuaDriver.app` holds the macOS Accessibility and Screen
  Recording grants.
- **`bin/agy-cua-mcp`** is a small stdio pass-through shim. Antigravity shows the model only
  the text part of a tool result, and cua-driver keeps the element snapshot id
  in the structured part, so without help the model can only click by guessing
  pixels ([trycua/cua#4346](https://github.com/trycua/cua/issues/4346)). The
  shim copies `snapshot_id` into the text, which enables exact element clicks.
  Everything else passes through unchanged. It goes away once #4346 ships.
- **`bin/agy-cua-jev-mcp`** is a zero-dependency Python 3 stdlib MCP server (`agy-cua-jev`)
  for bounded candidate selection with pinned **Jev (`jev-1.13.0`)** (`jev_status`,
  `jev_select_action`, `jev_step`). It keeps Jev text-only, enforces zero automatic
  provider retries (`max_attempts=1`), validates `(pid, window_id, snapshot_id)` and
  `delivery_mode="background"` before dispatching to `cua-driver`, and verifies the
  post-action window state with separate `observe_ms`, `provider_ms`, `dispatch_ms`,
  and `verify_ms` timings.
- **`skills/cua-computer-use`** teaches Gemini 4 Argon the loop: launch in the
  background, step with `jev_step` (or snapshot + act by element), and verify from
  the fresh tree. It also sets ground rules: no whole-desktop captures, no clipboard,
  no focus stealing.

## Install

1. Install Cua Driver and grant its permissions (one time):

   ```bash
   curl -fsSL https://cua.ai/driver/install.sh | bash
   cua-driver permissions grant
   ```

2. Clone this repo, store your Jev API key in macOS Keychain (never in files or chat), and check readiness:

   ```bash
   git clone https://github.com/saariuslystoned/agy-cua && cd agy-cua
   bin/agy-cua jev-key
   bin/agy-cua doctor
   ```

3. Install the Antigravity plugin, then restart Antigravity:

   ```bash
   bin/agy-cua install
   ```

   `install` copies only committed files into
   `~/.gemini/config/plugins/agy-cua`. Antigravity exposes the plugin's MCP
   servers as `agy-cua_agy-cua` and `agy-cua_agy-cua-jev`. If you used
   [agy-computer-use](https://github.com/saariuslystoned/agy-computer-use),
   remove its `computer-use` server (`agy mcp remove computer-use`) and its
   skill so they don't compete; `doctor` warns about it.

4. Ask Antigravity something like: *"Use the cua-computer-use skill to open
   Calculator and compute 12 × 34 with jev_step."*

**Per-workspace alternative:** copy `mcp_config.json` to
`<workspace>/.agents/mcp_config.json`, set `"env": {"AGY_CUA_HOME":
"/path/to/agy-cua"}` on the `agy-cua` and `agy-cua-jev` servers, and copy
`skills/cua-computer-use` to `<workspace>/.agents/skills/`.

## Verify on your machine

```bash
bin/agy-cua qualify               # headless agy (Gemini 4 Argon + Jev) run from this checkout
bin/agy-cua qualify --installed   # same check against the installed plugin
python3 -m unittest discover tests
```

`qualify` runs `agy -p --model gemini-4-argon-high --dangerously-skip-permissions --sandbox`
inside a throwaway workspace. It passes only if the display matches the expected
product, Calculator was never frontmost, Jev (`jev-1.13.0`) selected the actions, and
the agent used only the `agy-cua` / `agy-cua-jev` servers.

## Privacy

Proof runs record the frontmost app name and cursor position every 0.5 s, and
Antigravity keeps the tool outputs it was shown in its own conversation
directory. `runs/` is gitignored; review it before sharing.

## Related

- [trycua/cua](https://github.com/trycua/cua): Cua Driver, MIT.
- [agy-computer-use](https://github.com/saariuslystoned/agy-computer-use): the
  earlier Swift host this replaces. Its operator leases and human-intervention
  guard are candidates to propose upstream to Cua.

## License

MIT
