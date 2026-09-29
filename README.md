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

v0.1.0, macOS only. The CLI qualification (`bin/agy-cua qualify`) has the
agent compute a random product in Calculator, then reads the display back over
a separate connection:

| Run (2026-09-29, agy 1.2.13, `gemini-3.8-flash-high`, cua-driver 0.30.4) | Result | Time | Tool calls | Calculator frontmost |
| --- | --- | --- | --- | --- |
| 96 × 13, pixel clicks only (before the shim) | FAIL, missed buttons, timed out | 364 s | 63 | never |
| 81 × 63 | PASS, 5,103 | 87 s | 15 | never |
| 17 × 17 | PASS, 289 | 117 s | 18 | never |
| 86 × 37 | PASS, 3,182 | 110 s | 18 | never |
| 42 × 35, installed plugin (`qualify --installed`) | PASS, 1,470 | 80 s | 16 | never |

Not qualified yet: the Antigravity app (IDE) surface, `type_text` /
`press_key` / `hotkey`, apps other than Calculator, Windows and Linux.

## How it works

```text
Antigravity (app or agy) --stdio MCP--> bin/agy-cua-mcp --> cua-driver mcp --> CuaDriver.app daemon --> target app
                 ^ skills/cua-computer-use teaches the loop
```

- **Cua Driver** does the real work. It sends accessibility actions and input
  to one process without stealing focus, and returns exact refusal codes when
  it can't. The signed `CuaDriver.app` holds the macOS Accessibility and Screen
  Recording grants.
- **`bin/agy-cua-mcp`** is a small stdio shim. Antigravity shows the model only
  the text part of a tool result, and cua-driver keeps the element snapshot id
  in the structured part, so without help the model can only click by guessing
  pixels ([trycua/cua#4346](https://github.com/trycua/cua/issues/4346)). The
  shim copies `snapshot_id` into the text, which enables exact element clicks.
  Everything else passes through unchanged. It goes away once #4346 ships.
- **`skills/cua-computer-use`** teaches Gemini the loop: launch in the
  background, snapshot, act by element, verify from the tree. It also sets
  ground rules: no whole-desktop captures, no clipboard, no focus stealing.

## Install

1. Install Cua Driver and grant its permissions (one time):

   ```bash
   curl -fsSL https://cua.ai/driver/install.sh | bash
   cua-driver permissions grant
   ```

2. Clone this repo and check readiness:

   ```bash
   git clone https://github.com/saariuslystoned/agy-cua && cd agy-cua
   bin/agy-cua doctor
   ```

3. Install the Antigravity plugin, then restart Antigravity:

   ```bash
   bin/agy-cua install
   ```

   `install` copies only committed files into
   `~/.gemini/config/plugins/agy-cua`. Antigravity exposes the plugin's MCP
   server as `agy-cua_agy-cua`. If you used
   [agy-computer-use](https://github.com/saariuslystoned/agy-computer-use),
   remove its `computer-use` server (`agy mcp remove computer-use`) and its
   skill so they don't compete; `doctor` warns about it.

4. Ask Antigravity something like: *"Use the cua-computer-use skill to open
   Calculator and compute 12 × 34."*

**Per-workspace alternative:** copy `mcp_config.json` to
`<workspace>/.agents/mcp_config.json`, set `"env": {"AGY_CUA_HOME":
"/path/to/agy-cua"}` on the `agy-cua` server, and copy
`skills/cua-computer-use` to `<workspace>/.agents/skills/`.

## Verify on your machine

```bash
bin/agy-cua qualify            # headless agy run from this checkout; proof in runs/qualify-<UTC>/
bin/agy-cua qualify --installed   # same check against the installed plugin
python3 -m unittest discover tests
```

`qualify` runs `agy -p --dangerously-skip-permissions --sandbox` inside a
throwaway workspace. It passes only if the display matches the expected
product and the agent used only the `agy-cua` server.

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
