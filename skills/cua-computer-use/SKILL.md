---
name: cua-computer-use
description: Operate native macOS apps in the background through Cua Driver (the agy-cua MCP server) - launch an app without stealing focus, read its window through the accessibility tree and a window screenshot, click and type into it, and verify the result. Use when the user asks you to use, test, or read a desktop app.
---

# Computer use through Cua Driver (agy-cua)

You drive native macOS apps through the `agy-cua` MCP server, which runs the
open-source [Cua Driver](https://github.com/trycua/cua/tree/main/libs/cua-driver).
Cua Driver sends input to one target process without moving the user's cursor
or bringing that app to the front, so the user can keep working while you act.

## Ground rules

- Act only on apps the user named. Leave every other window alone.
- Keep the user's frontmost app in front. Start apps with `launch_app`, which
  launches in the background. Do not call `bring_to_front` unless the user asks.
- Do not capture the whole desktop (`get_desktop_state`) unless the user asks.
  It records every window on screen, including private ones. Use the
  window-scoped `get_window_state` instead.
- Do not read or write the clipboard, kill apps, change Cua Driver config, or
  use the `browser_*` tools unless the user asks.
- Never probe a tool with placeholder arguments such as `"test"` or
  `"not_a_number"`. If a call is refused, read the refusal, fix the call as it
  says, and report the exact error if you still cannot proceed.
- If `check_permissions` or `health_report` shows missing Accessibility or
  Screen Recording, stop and tell the user to run `cua-driver permissions grant`.
  Never try to change macOS privacy settings yourself.

## How Antigravity hands you results

- Call these tools through `call_mcp_tool`. The server is `agy-cua` in a
  workspace config and `agy-cua_agy-cua` when installed as a plugin; use
  whichever one your tool list shows.
- Large results, such as window trees and screenshots, are saved to a file in
  Antigravity's conversation `brain` directory (the CLI uses
  `~/.gemini/antigravity-cli/brain/…/.system_generated/steps/<n>/`), and you
  get a pointer to it. Open that file with `view_file`. Reading those saved
  results is expected; do not write files to work around them.
- You see only the text part of each result. Structured fields do not reach
  you ([trycua/cua#4346](https://github.com/trycua/cua/issues/4346)), so
  agy-cua adds a first line to every `get_window_state` result:
  `snapshot_id=<id>`. Use it as described below.

## The loop

1. **Launch.** Call `launch_app` with `bundle_id` (for example
   `com.apple.calculator`). It returns the `pid` and the app's windows. Use the
   `window_id` of the titled main window. If no windows come back, call
   `list_windows` with that `pid`. Never call `list_windows` without a filter.
2. **Look.** Call `get_window_state` with `pid`, `window_id`, and
   `include_screenshot: false` for the element tree. Open the saved result with
   `view_file` if it was saved to a file. Include a screenshot only when you
   need to see the window or click by pixel.
3. **Act.** Prefer elements over pixels: element actions are exact
   accessibility presses and need no screenshot.
   - Click an element with `click` and `{pid, window_id, snapshot_id,
     element_index}`. `snapshot_id` is the first line of the latest
     `get_window_state` result, and `element_index` is the `[N]` on the
     element's line, for example `[13] AXButton (1)`. A `snapshot_id` stays
     valid until your next `get_window_state` for that window. After a click
     that changes the window's layout (a dialog, sheet, new list, or tab),
     take a new snapshot before the next element action.
   - Never pass `element_index` without `snapshot_id` (refused with
     `snapshot_id_required`), and never invent `snapshot_id` or
     `element_token` values.
   - Use pixels only for surfaces with no element in the tree, such as canvases
     or custom-drawn views: `click` with `{pid, window_id, x, y}`, where `x,y`
     are pixels in the latest window screenshot from this connection, origin at
     its top-left corner. The header `size=WxH` gives that screenshot's size.
   - `type_text` (`text`), `press_key` (`key`), `hotkey` (`keys`), and
     `set_value` accept the same `snapshot_id` + `element_index` to aim at a
     field. The agy-cua qualification has not covered them yet, so always
     verify after using them.
4. **Verify.** Call `get_window_state` with `include_screenshot: false` and
   read the value you changed, such as an `AXStaticText` or a field's value.
   Report what the tree shows, not what you expected. Take a fresh snapshot
   with a screenshot before the next pixel click if the layout may have moved.

## Final answer

State the result, the tools you called in order, and every refusal with its
exact error text.
