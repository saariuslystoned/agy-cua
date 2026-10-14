---
name: cua-computer-use
description: Operate native macOS apps in the background through Cua Driver (agy-cua) and Jev bounded action selection (agy-cua-jev) - launch an app without stealing focus, read its window through the accessibility tree and a window screenshot, select and verify actions with Jev, and verify the result. Use when the user asks you to use, test, or read a desktop app.
---

# Computer use through Cua Driver and Jev (`agy-cua` + `agy-cua-jev`)

You drive native macOS apps in the background through two narrow MCP servers:
- `agy-cua` (`agy-cua_agy-cua` when installed as a plugin): [Cua Driver](https://github.com/trycua/cua/tree/main/libs/cua-driver) for background app launch, window perception (`get_window_state`), and window-scoped input.
- `agy-cua-jev` (`agy-cua_agy-cua-jev` when installed as a plugin): bounded action selection with pinned **Jev (`jev-1.13.0`)** (`jev_status`, `jev_select_action`, `jev_step`).

Cua Driver sends input to one target process without moving the user's cursor or bringing that app to the front, so the user can keep working while you act.

## Division of responsibilities

1. **Task planning & visual interpretation (You / Gemini 4 Argon):** Interpret the user's goal, launch the target app, and read window screenshots when visual layout or custom-drawn content matters.
2. **Observation & candidate generation (`agy-cua` / `jev_step`):** Observe the explicit target `(pid, window_id)` to obtain a fresh `snapshot_id` and structured AX controls.
3. **Bounded action selection (`jev-1.13.0` via `agy-cua-jev`):** Jev is text-only and selects one candidate ID from a closed candidate set (or `reobserve` / `abstain`). Never pass screenshots or base64 images to Jev.
4. **Local validation & background dispatch:** Validate `(pid, window_id)` scope, `snapshot_id` freshness, and `delivery_mode: "background"` before dispatching to Cua Driver.
5. **Independent verification:** Confirm the intended effect from a fresh post-action `get_window_state` observation, not solely from a dispatch receipt.

## Ground rules

- Act only on apps the user named. Leave every other window alone.
- Keep the user's frontmost app in front. Start apps with `launch_app`, which launches in the background. Do not call `bring_to_front` or `delivery_mode: "foreground"` unless the user explicitly asks.
- Do not capture the whole desktop (`get_desktop_state`) unless the user asks. Use the window-scoped `get_window_state` instead.
- Do not read or write the clipboard, kill apps, change Cua Driver config, or use the `browser_*` tools unless the user asks.
- Keep credentials out of chat, clipboard, arguments, logs, and screenshots. If `jev_status` reports `credentials-unavailable`, stop and ask the user to run `bin/agy-cua jev-key`.
- Never repeat an uncertain or failed side effect automatically. If a step returns `dispatch_failed`, `halted`, `reobserve`, or `abstain`, inspect a fresh `get_window_state` or report the exact refusal.

## How Antigravity hands you results

- Call these tools through `call_mcp_tool`. In a workspace config the servers are `agy-cua` and `agy-cua-jev`; when installed as a plugin they are `agy-cua_agy-cua` and `agy-cua_agy-cua-jev`.
- Large results, such as window trees and screenshots, are saved to a file in Antigravity's conversation `brain` directory (`.system_generated/steps/<n>/output.txt`), and you get a pointer to it. Open that file with `view_file`.
- You see only the text part of each result (`structuredContent` is omitted by Antigravity, [trycua/cua#4346](https://github.com/trycua/cua/issues/4346)). `bin/agy-cua-mcp` prepends `snapshot_id=<id>` to every `get_window_state` text result, and `jev_step` / `jev_select_action` format all decision fields, `post_static_texts`, and `timings_ms` directly into their text result.

## The loop

1. **Launch.** Call `launch_app` on `agy-cua` with `bundle_id` (for example `com.apple.calculator`). It returns the `pid` and the app's `Windows:` list with `[window_id: N]`.
   - *Cold-start note:* If the app was not already running, the first `launch_app` call may return before the main window finishes opening (so no `Windows:` block appears in text, and `list_windows` only puts `window_id` in `structuredContent`). Simply call `launch_app` a second time with the same `bundle_id` to get the `[window_id: N]` list in text.
2. **Act & verify with `jev_step` (preferred when `agy-cua-jev` is available).**
   - Call `jev_step` on `agy-cua-jev` (`agy-cua_agy-cua-jev`) with `{pid, window_id, goal, expected_postcondition?}`.
   - By default, `jev_step` observes the window's AX tree (`include_screenshot: false`), builds background `click` candidates for all actionable window controls, asks `jev-1.13.0` to pick the single best candidate (or `reobserve` / `abstain`), validates scope and `snapshot_id`, dispatches the click in the background, and takes a fresh `get_window_state` to verify the result (`post_static_texts`, `timings_ms`).
   - For text entry, keys, shortcuts, `set_value`, `scroll`, or `drag`, pass an explicit `candidates` array to `jev_step`, for example:
     - `{"id": "type-note", "description": "Type hello into [3] AXTextArea", "tool": "type_text", "element_index": 3, "text": "hello"}`
     - `{"id": "set-field", "description": "Set [4] AXTextField value to 42", "tool": "set_value", "element_index": 4, "value": "42"}`
     - `{"id": "press-return", "description": "Press return key in window", "tool": "press_key", "key": "return"}`
     - `{"id": "scroll-down", "description": "Scroll [5] AXScrollArea down", "tool": "scroll", "element_index": 5, "direction": "down", "amount": 3}`
3. **Direct `agy-cua` element actions (when inspecting or acting directly).**
   - Call `get_window_state` with `pid`, `window_id`, and `include_screenshot: false` (or `true` when you need visual inspection).
   - Optionally call `jev_select_action` with `{goal, pid, window_id, snapshot_id, elements, candidates}` to get Jev's validated choice before calling `click`, `type_text`, `set_value`, `press_key`, `hotkey`, `scroll`, or `drag`.
   - Always pass `snapshot_id` with `element_index`, and take a fresh `get_window_state` after the action to verify the new value from the tree.

## Supported capabilities and exact refusals

- **Supported in background (`delivery_mode: "background"`):** `launch_app`, `get_window_state`, element-addressed `click` / `double_click` / `right_click`, `set_value`, `type_text` (native Cocoa fields), `press_key`, `hotkey` (window-posted), `scroll` (element or window-pixel wheel), and `drag` (window-pixel path).
- **Refused by `agy-cua-jev` controller:**
  - `stale_snapshot_id`: candidate or decision `snapshot_id` does not match the current window snapshot. Re-observe first.
  - `out_of_scope_target`: candidate `pid` or `window_id` differs from the explicitly targeted window.
  - `unknown_element_index`: `element_index` is not present in the current snapshot.
  - `foreground_or_global_input_forbidden`: `delivery_mode: "foreground"`, `scope: "desktop"`, `bring_to_front`, `get_desktop_state`, `clipboard_*`, `kill_app`, `set_config`, or `browser_*`.
  - `window_id_not_found` / `window_owner_pid_mismatch`: target window closed or belongs to an out-of-process panel; recover by re-identifying the target window with `launch_app` or stopping cleanly.

## Final answer

State the verified application result, the Jev model identity (`returned_model`) and selected candidate IDs when Jev was used, the tools called in order, stage timings (`observe_ms`, `provider_ms`, `dispatch_ms`, `verify_ms`), and every refusal with its exact error text.
