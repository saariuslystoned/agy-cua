---
name: cua-computer-use
description: Operate native macOS apps in the background through Cua Driver (agy-cua) and Jev bounded action selection (agy-cua-jev) - launch an app without stealing focus, read its window through the accessibility tree and a window screenshot, select and verify actions with Jev, and verify the result. Use when the user asks you to use, test, or read a desktop app.
---

# Computer use through Cua Driver and Jev (`agy-cua` + `agy-cua-jev`)

You drive native macOS apps in the background through two narrow MCP servers:
- `agy-cua` (`agy-cua_agy-cua` when installed as a plugin): [Cua Driver](https://github.com/trycua/cua/tree/main/libs/cua-driver) for background app launch, window perception (`get_window_state`), and window-scoped input.
- `agy-cua-jev` (`agy-cua_agy-cua-jev` when installed as a plugin): bounded action selection with pinned **Jev (`jev-1.13.0`)** (`jev_status`, `jev_select_action`, `jev_step`, `jev_batch`).

Cua Driver sends input to one target process without moving the user's cursor or bringing that app to the front, so the user can keep working while you act.

## Division of responsibilities

1. **Task planning & visual interpretation (You / Gemini 4 Argon):** Interpret the user's goal, launch the target app, and read window screenshots when visual layout or custom-drawn content matters.
2. **Observation & candidate generation (`agy-cua` / `jev_step` / `jev_batch`):** Observe the explicit target `(pid, window_id)` to obtain a fresh `snapshot_id` and structured AX controls.
3. **Bounded action selection (`jev-1.13.0` via `agy-cua-jev`):** Jev is text-only and selects one candidate ID from a closed candidate set (or `reobserve` / `abstain`). Never pass screenshots or base64 images to Jev.
4. **Local validation & background dispatch:** Validate `(pid, window_id)` scope, `snapshot_id` freshness, and `delivery_mode: "background"` before dispatching to Cua Driver.
5. **Independent verification:** Confirm the intended effect from a fresh post-action `get_window_state` observation, not solely from a dispatch receipt.

## Ground rules

- Act only on apps the user named. Leave every other window alone. For a named app with a known bundle ID, discover its process and windows with `launch_app` first, even when it is already running; it does not activate the app. Do not use `list_apps` or an unfiltered `list_windows` as a shortcut. If you do not know the bundle ID, ask for the target or use an explicitly authorized scoped lookup; do not inventory unrelated apps or windows.
- Keep the user's frontmost app in front. Start apps with `launch_app`, which launches in the background. Do not call `bring_to_front` or `delivery_mode: "foreground"` unless the user explicitly asks.
- Do not capture the whole desktop (`get_desktop_state`) unless the user asks. Use the window-scoped `get_window_state` instead.
- Do not read or write the clipboard, kill apps, change Cua Driver config, or use the `browser_*` tools unless the user asks.
- Keep credentials out of chat, clipboard, arguments, logs, and screenshots. If `jev_status` reports `credentials-unavailable`, stop and ask the user to run `bin/agy-cua jev-key`.
- Never repeat an uncertain or failed side effect automatically. If `jev_step` or `jev_batch` returns `dispatch_failed`, `verification_failed`, `outcome_unknown`, `postcondition_unmet`, `stale_snapshot`, `target_conflict`, `halted`, `reobserve`, or `abstain`, inspect `completed_steps` (for batches) and a fresh `get_window_state` before taking any further action. Never replay a batch from step 0 after a partial side effect.

## How Antigravity hands you results

- Call these tools through `call_mcp_tool`. In a workspace config the servers are `agy-cua` and `agy-cua-jev`; when installed as a plugin they are `agy-cua_agy-cua` and `agy-cua_agy-cua-jev`.
- Large results, such as window trees and screenshots, are saved to a file in Antigravity's conversation `brain` directory (`.system_generated/steps/<n>/output.txt`), and you get a pointer to it. Open that file with `view_file`.
- You see only the text part of each result (`structuredContent` is omitted by Antigravity, [trycua/cua#4346](https://github.com/trycua/cua/issues/4346)). `bin/agy-cua-mcp` prepends `snapshot_id=<id>` to every `get_window_state` text result, and `jev_step` / `jev_batch` / `jev_select_action` format all decision fields, `post_static_texts`, and `timings_ms` directly into their text result.
- Within one conversation, reuse tool schemas and skill instructions you have already read. Open a saved result only when its contents are needed; do not repeatedly read the same unchanged schema. A resumed conversation still needs a fresh window snapshot: old element handles are not durable locators.

## The loop

1. **Launch.** Call `launch_app` on `agy-cua` with `bundle_id` (for example `com.apple.calculator`). It returns the `pid` and the app's `Windows:` list with `[window_id: N]`.
   - *Cold-start note:* If the app was not already running, the first `launch_app` call may return before the main window finishes opening (so no `Windows:` block appears in text, and `list_windows` only puts `window_id` in `structuredContent`). Simply call `launch_app` a second time with the same `bundle_id` to get the `[window_id: N]` list in text.
2. **Act & verify with `jev_step` or `jev_batch` (preferred when `agy-cua-jev` is available).**
   - Call `jev_step` on `agy-cua-jev` (`agy-cua_agy-cua-jev`) with `{pid, window_id, goal, value?, expected_postcondition}` for a single guarded step, or call `jev_batch` with `{pid, window_id, chain_observations?, steps: [{goal, value?, expected_postcondition, mode?, query?, max_depth?, candidates?}, ...]}` for a short guarded multi-step sequence (`1..16` steps). Do not use raw `cua-driver` `run_steps` or `run_script` as a shortcut around the `agy-cua-jev` controller.
   - Always pass a bound `expected_postcondition` to verify the step's goal (`status: "verified"`, `verification_outcome: "goal_verified"`). In `jev_batch`, every step **must** include a bound `expected_postcondition` so dependent steps only advance after `verification_outcome == "goal_verified"`. Use `{"static_text_equals": "0"}` when clicking `All Clear` / `Clear` on Calculator, and exact `static_text_equals` (with optional `static_text_index: -1` for Calculator's main display) for digit and result steps. Reserve `static_text_contains` only for explicit substring checks. For element value/label checks (`element_value_equals`, `element_label_equals`, `element_label_contains`), `jev_step` / `jev_batch` binds to `expected_postcondition.element_index` or automatically to the dispatched candidate's `element_index` and verifies role and label continuity (`target_element_label_mismatch`).
   - In `jev_batch`, each step runs the full protected controller loop (fresh `get_window_state`, or reusing the prior step's verified `post_obs` when `chain_observations: true` and `(query, max_depth)` scope matches without requiring a pixel anchor; fresh `snapshot_id` and `element_token: "<snapshot_id>:<element_index>"` binding; background-only delivery validation; and fresh post-action verification). Steps default to `mode: "jev"`, or may set `mode: "deterministic"` when supplying a single explicit candidate in `candidates`. If any step fails its postcondition, encounters external interstep state drift (`stale_snapshot`), or times out (`outcome_unknown`), `jev_batch` stops immediately before the next step and returns `completed_steps`, `failed_step`, and `remaining_steps`. Never replay `completed_steps`; inspect `failed_step` and a fresh `get_window_state` first.
   - Without `expected_postcondition` (on single `jev_step` only), `jev_step` never reports `verified: true` (`goal_verified`); it classifies the outcome as `state_changed` (when observable AX state changed) or `dispatched` (when no AX change occurred), or `outcome_unknown` (`uncertain_side_effect: true`, session halted) if dispatch or post-dispatch observation fails.
   - By default, `jev_step` observes the window's AX tree (`include_screenshot: false`), prioritizes `AXToolbar` and shallow controls ahead of deep outline/table rows before applying element (`64`) and auto-candidate (`28`) caps, discloses any omissions (`total_actionable_elements`, `omitted_elements_count`, `omitted_candidates_count`, `observation_truncated`), asks `jev-1.13.0` to pick the single best candidate (or `reobserve` / `abstain`), validates scope and `snapshot_id`, dispatches the action in the background, and takes a fresh `get_window_state` to verify the result (`post_static_texts`, `timings_ms`).
   - When setting a text field value (`AXTextField`, `AXSearchField`, `AXTextArea`, `AXComboBox`), pass `value: "<string>"` on `jev_step` or a `jev_batch` step to auto-generate bounded `set_value` candidates (`sv.<idx>.<slug>`) across non-secure editable fields without exposing the string value in candidate descriptions sent to Jev.
   - When `observation_truncated: true` or `omitted_candidates_count > 0` indicates controls were capped, or when targeting a known subtree, pass optional `query` (non-empty substring) and/or `max_depth` (`1..12`) to `jev_step` (or a `jev_batch` step). `jev_step` forwards both parameters to Stage 1 (`pre_obs`) and Stage 5 (`post_obs`) `get_window_state` calls so element/role continuity checks in `expected_postcondition` compare matching tree scopes.
   - For explicit text entry, keys, shortcuts, `set_value`, `invoke_menu`, or `scroll`, pass an explicit `candidates` array to `jev_step` or `jev_batch`, for example:
     - `{"id": "type-note", "description": "Type hello into [3] AXTextArea", "tool": "type_text", "element_index": 3, "text": "hello"}`
     - `{"id": "set-field", "description": "Set [4] AXTextField value", "tool": "set_value", "element_index": 4, "value": "42"}`
     - `{"id": "save-menu", "description": "Invoke File > Save menu item", "tool": "invoke_menu", "path": ["File", "Save"]}`
     - `{"id": "press-return", "description": "Press return key in window", "tool": "press_key", "key": "return"}`
     - `{"id": "scroll-down", "description": "Scroll [5] AXScrollArea down", "tool": "scroll", "element_index": 5, "direction": "down", "amount": 3}`
3. **Direct `agy-cua` element actions (when inspecting or acting directly).**
   - Call `get_window_state` with `pid`, `window_id`, `tree_format: "both"`, and `include_screenshot: false` (or `true` when you need visual inspection).
   - When the task's relevant role or a previously observed label is known, add `query` to return matching lines and their ancestors. It is a case-insensitive substring filter, not a selector language; element indices keep their original values. If the query omits needed context, broaden it or omit it. Never query for the expected answer as a substitute for reading the actual value.
   - Optionally call `jev_select_action` with `{goal, pid, window_id, snapshot_id, elements, candidates}` to get Jev's validated choice before calling `click`, `type_text`, `set_value`, `press_key`, `hotkey`, `invoke_menu`, or `scroll`.
   - Always pass `element_token: "<snapshot_id>:<element_index>"` on direct `agy-cua` element actions, and take a fresh `get_window_state` after the action to verify the new value from the tree.

## Multi-pane apps & macOS Mail (`com.apple.mail`) guidance

- **Bounded `max_depth: 2` workaround for upstream traversal starvation:** On large multi-pane windows such as `com.apple.mail`, `cua-driver` walks `AXSplitGroup` -> `AXOutline` / `AXTable` depth-first and applies `query` filtering post-walk. A large mailbox or message table can exhaust the default walk budget before reaching sibling `AXToolbar` controls (`New Message`, `Search`), causing `query: "Search"` alone at default depth to return zero matches. Pass `max_depth: 2` (optionally combined with `query`) on `get_window_state` or `jev_step` as a bounded downstream workaround to surface top-level `AXToolbar` controls quickly.
- **New same-PID windows/sheets are discovery, not verification:** Actions in Mail such as clicking `New Message`, pressing `cmd+n` / `cmd+r` (Reply), or double-clicking a message row open a new same-PID window or sheet. Spawning a new window is **window discovery**, not automatic goal verification (the new window could be an error alert or an unrelated dialog). Re-call `launch_app` with `bundle_id: "com.apple.mail"` to discover the new `[window_id: N]`, observe that `window_id` with `get_window_state`, and verify its expected purpose and fields with bound predicates.
- **Hard `WAITING_FOR_HUMAN` boundary on `Send`, `Delete`, and `Empty Trash`:** Never click `Send` (`AXButton "Send"`), trigger send/delete shortcuts (`cmd+shift+d`, `cmd+delete`), or invoke commit menu items (`["Message", "Send"]`, `["Mailbox", "Erase Deleted Items"]`, `["Finder", "Empty Trash"]`) without explicit user confirmation (`WAITING_FOR_HUMAN`) bound to the current recipient, subject, and body. Default compose/reply workflows to saving drafts (`["File", "Save"]` via `invoke_menu` or `cmd+s`) and verifying draft state.

## Supported capabilities and exact refusals

- **Supported in background (`delivery_mode: "background"`):** `launch_app`, `get_window_state`, element-addressed `click` / `double_click` / `right_click`, `set_value`, `type_text` (native Cocoa fields), `press_key`, `hotkey` (window-posted), `invoke_menu` (window-scoped menu bar path array), and `scroll` (element or window-pixel wheel).
- **Refused by `agy-cua-jev` controller:**
  - `stale_snapshot_id`: candidate or decision `snapshot_id` does not match the current window snapshot. Re-observe first.
  - `out_of_scope_target`: candidate `pid` or `window_id` differs from the explicitly targeted window.
  - `unknown_element_index`: `element_index` is not present in the current snapshot.
  - `secure_element_refused`: target element is a secure/password field (`AXSecureTextField` or `\uf79a`-masked value); values are stripped (`state: "enabled (secure)"`), excluded from auto-candidates, and refused for automated interaction.
  - `mail_send_forbidden_by_safety_policy` / `commit_action_forbidden_by_safety_policy`: consequential commit actions (`Send`, `Delete`, `Empty Trash`, `cmd+shift+d`, `cmd+delete`, `["Message", "Send"]`) are refused at the controller boundary (`WAITING_FOR_HUMAN`).
  - `incompatible_tool_for_role` / `unsupported_element_action`: candidate tool (e.g. `set_value` or `type_text` on a non-input role, or `click` on a non-pressable `AXCell`) is not supported by the target element.
  - `background_drag_unavailable_on_macos`: `cua-driver` on macOS requires `delivery_mode: "foreground"` for `drag`, which violates background-only execution.
  - `foreground_or_global_input_forbidden`: `delivery_mode: "foreground"`, `scope: "desktop"`, `bring_to_front`, `get_desktop_state`, `clipboard_*`, `kill_app`, `set_config`, or `browser_*`.
  - `window_id_not_found` / `window_owner_pid_mismatch`: target window closed or belongs to an out-of-process panel; recover by re-identifying the target window with `launch_app` or stopping cleanly.

## Final answer

State the verified application result, the Jev model identity (`returned_model`) and selected candidate IDs when Jev was used, the tools called in order, stage timings (`observe_ms`, `provider_ms`, `dispatch_ms`, `verify_ms`), and every refusal with its exact error text.
