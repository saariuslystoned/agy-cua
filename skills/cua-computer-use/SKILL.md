---
name: cua-computer-use
description: Operate native macOS apps in the background through Cua Driver (agy-cua) and Jev bounded action selection (agy-cua-jev) - launch an app without stealing focus, read its window through the accessibility tree and a window screenshot, select and verify actions with Jev, and verify the result. Use when the user asks you to use, test, or read a desktop app.
---

# Computer use through Cua Driver and Jev (`agy-cua` + `agy-cua-jev`)

You drive native macOS apps in the background through two narrow MCP servers:
- `agy-cua` (`agy-cua_agy-cua` when installed as a plugin): [Cua Driver](https://github.com/trycua/cua/tree/main/libs/cua-driver) for background app launch, window perception (`get_window_state`), and window-scoped input.
- `agy-cua-jev` (`agy-cua_agy-cua-jev` when installed as a plugin): bounded action selection and guarded controller loops (`v0.4.0`) with pinned **Jev (`jev-1.13.0`)** (`jev_status`, `jev_select_action`, `jev_observe`, `jev_step`, `jev_batch`, `jev_browser_prepare`, `jev_browser_step`).

Cua Driver sends input to one target process without moving the user's cursor or bringing that app to the front, so the user can keep working while you act.

## Division of responsibilities

1. **Task planning & visual interpretation (You):** Interpret the user's goal, launch the target app, and inspect window screenshots when visual layout or custom-drawn content matters (`visual_fallback_recommended: true`).
2. **Observation & candidate generation (`jev_observe` / `jev_step` / `jev_batch`):** Observe explicit `(pid, window_id)` scope to obtain a fresh `snapshot_id`, structured AX controls, semantic regions/surfaces (`drill_regions`, `active_surfaces`), query completeness (`query_status`, `query_incomplete`, `search_complete`), and visual fallback metadata (`visual_fallback_recommended`, `window_bounds`).
3. **Bounded action selection (`jev-1.13.0`):** Jev is text-only and selects one candidate ID from a closed candidate set (or `reobserve` / `abstain`). Never pass screenshots or base64 images to Jev.
4. **Local validation & background dispatch:** Validate `(pid, window_id)` or `(target_id, tab_id)` scope, `snapshot_id` / `source_snapshot_id` freshness, window coordinate bounds (`0 .. window_bounds.width` × `0 .. window_bounds.height`), idempotent checkbox state (`check` / `uncheck`), and `delivery_mode: "background"` before dispatching.
5. **Independent verification:** Confirm the intended effect from a fresh post-action `get_window_state` (or `get_browser_state`) observation, never solely from a dispatch receipt.

## Ground rules

- Act only on apps the user named. Leave every other window alone. For a named app with a known bundle ID, discover its process and windows with `launch_app` first, even when already running; it does not activate the app. Do not use `list_apps` or an unfiltered `list_windows` as a shortcut. If you do not know the bundle ID, ask for the target or use an explicitly authorized scoped lookup; do not inventory unrelated apps or windows.
- Keep the user's frontmost app in front. Start apps with `launch_app` (background). Do not call `bring_to_front` or `delivery_mode: "foreground"` unless the user explicitly asks.
- Do not capture the whole desktop (`get_desktop_state`) unless the user asks. Use window-scoped `get_window_state` / `jev_observe`.
- Do not read or write the clipboard, kill apps, or change Cua Driver config unless the user asks. Do not use `browser_*` tools except through the isolated loopback-only `jev_browser_prepare` / `jev_browser_step` lane when explicitly requested.
- Keep credentials out of chat, clipboard, arguments, logs, and screenshots. If `jev_status` reports `credentials-unavailable`, stop and ask the user to run `bin/agy-cua jev-key`.
- Never repeat an uncertain or failed side effect automatically. If `jev_step`, `jev_batch`, or `jev_browser_step` returns `dispatch_failed`, `verification_failed`, `outcome_unknown`, `postcondition_unmet`, `stale_snapshot`, `target_conflict`, `halted`, `reobserve`, or `abstain`, inspect `completed_steps` (for batches) and a fresh observation before acting further. Never replay a batch from step 0 after a partial side effect.

## How Antigravity hands you results

- Call these tools through `call_mcp_tool` (`agy-cua` / `agy-cua-jev` in workspace configs; `agy-cua_agy-cua` / `agy-cua_agy-cua-jev` as an installed plugin).
- Large results are saved to `.system_generated/steps/<n>/output.txt`; open them only when needed. Reuse tool schemas and skill instructions within a conversation, but always take a fresh window snapshot in a resumed session.
- Antigravity displays only the text part of tool results ([trycua/cua#4346](https://github.com/trycua/cua/issues/4346)). `bin/agy-cua-mcp` prepends `snapshot_id=<id>` to `get_window_state` text, and `agy-cua-jev` renders all decision fields, `post_static_texts`, and `timings_ms` directly into text.

## The loop

1. **Launch.** Call `launch_app` on `agy-cua` with `bundle_id` (e.g. `com.apple.calculator`) to obtain `pid` and `Windows:` (`[window_id: N]`). On a cold start where the first `launch_app` returns before the window opens, call `launch_app` once more with the same `bundle_id`.
2. **Act & verify with `jev_step` or `jev_batch` (preferred when `agy-cua-jev` is available).**
   - Call `jev_step` with `{pid, window_id, goal, value?, query?, skeleton?, region?, root_element_index?, max_depth?, max_elements?, walk_timeout_ms?, wait_timeout_ms?, include_diff?, discover_windows?, source_snapshot_id?, candidates?, expected_postcondition}`, or `jev_batch` with `{pid, window_id, chain_observations?, steps: [{goal, value?, expected_postcondition, mode?, query?, skeleton?, region?, root_element_index?, max_depth?, max_elements?, walk_timeout_ms?, wait_timeout_ms?, candidates?}, ...]}` (`1..16` steps). Do not use raw `run_steps` or `run_script` around the controller.
   - Always pass a bound `expected_postcondition` (`status: "verified"`, `verification_outcome: "goal_verified"`). Every `jev_batch` step requires `expected_postcondition`. When supplied, the controller wakes occluded App Nap targets and polls transient UI settling up to `wait_timeout_ms` (`1200` ms default, `0..10000`, `verify_polls` reported). Use `{"static_text_equals": "0"}` for Calculator `All Clear` / `Clear`, and exact `static_text_equals` (with `static_text_index: -1` for Calculator's display) for digits/results. Reserve `static_text_contains` for substring checks. Element checks (`element_value_equals`, `element_label_equals`, `element_label_contains`) bind to `expected_postcondition.element_index` or the dispatched candidate's `element_index` and verify role/label continuity (`target_element_label_mismatch`).
   - In `jev_batch`, each step takes a fresh `get_window_state` (or reuses the prior verified `post_obs` only when `chain_observations: true`, `allow_interstep_drift: true`, and scope parameters match without needing a pixel anchor). Steps default to `mode: "jev"`, or `mode: "deterministic"` for a single explicit candidate. On postcondition failure, interstep drift (`stale_snapshot`), or timeout (`outcome_unknown`), `jev_batch` halts immediately and returns `completed_steps`, `failed_step`, and `remaining_steps`.
   - Without `expected_postcondition` (single `jev_step` only), `jev_step` never claims `goal_verified`; it reports `state_changed`, `dispatched`, or `outcome_unknown` (`uncertain_side_effect: true`, session halted).
   - **Candidate ranking, idempotent checkboxes & Pass-2 rerank (`v0.4.0`):** `jev_step` observes with `include_screenshot: false` (and `include_menubar: false` + native `root_element_token` subtree reads when supported by `cua-driver`), prioritizes `AXToolbar` and shallow controls ahead of deep table/outline rows (`240` elements / `240` auto-candidates; `64`/`28` under `AGY_CUA_JEV_V1_COMPAT=1`), enriches descriptions with `container_path` and `current=<val>`, emits idempotent `chk.<idx>.<slug>` (`check`) and `unchk.<idx>.<slug>` (`uncheck`) candidates alongside `ax.<idx>.<slug>` on `AXCheckBox` controls with unambiguous boolean state (`0`/`1`, skipping `AXPress` when already in the requested state so checkboxes never double-toggle), and runs a Pass-2 top-5 Jev shortlist rerank (`reranked: true`) whenever Pass-1 confidence is `< 0.70` across `> 5` candidates.
   - **Text fields (`value`):** Pass `value: "<string>"` on `jev_step` or a `jev_batch` step to auto-generate `set_value` candidates (`sv.<idx>.<slug>`) across non-secure editable fields (`AXTextField`, `AXSearchField`, `AXTextArea`, `AXComboBox`) without sending the string value to Jev.
   - **Progressive container disclosure (`skeleton: true` -> `drill_regions` -> `region` / `root_element_index`) & automatic single-hop drill:** On dense `1,000+` node multi-pane windows where middle tables starve downstream panes, call `jev_observe(pid, window_id, skeleton: true)` to inspect top-level controls and `drill_regions` (`[{region, element_index?, region_token?, actionable_count, subregions, sample_labels, ambiguous_region?}]`), then pass `region: "<region>"` (or `root_element_index: <int>`, required when `ambiguous_region: true`) on `jev_observe`, `jev_step`, or `jev_batch` steps. In `v0.4.0`, when an unscoped `jev_step` observes a truncated or skeleton window with `drill_regions`, it also offers `drill.<idx>.<slug>` container candidates to Jev and automatically executes at most **one** internal subtree drill (`auto_drilled_region` reported) + target selection + dispatch + postcondition verification inside the same `jev_step` call. Scope parameters (`query`, `max_depth` `1..12`, `max_elements` `1..1000`, `walk_timeout_ms` `100..10000`, `region`, `root_element_index`) are applied consistently to both `pre_obs` and `post_obs`.
   - **Zero-AX visual coordinate fallback (`visual_fallback_recommended: true`):** When a window exposes zero actionable AX controls, call `jev_observe(pid, window_id, include_screenshot: true)` to inspect the PNG, then call `jev_step` with `source_snapshot_id` set to that `snapshot_id`, explicit window-scoped coordinate candidate(s) in `candidates` (`{"id": "canvas.approve", "description": "Click green APPROVE RELEASE button", "tool": "click", "x": 525, "y": 370}` within `0 .. window_bounds.width` × `0 .. window_bounds.height`), and `expected_postcondition`. Retina screenshot pixels are converted to logical window points automatically when needed.
   - **Explicit `candidates` examples** (for `type_text`, `set_value`, `invoke_menu`, `press_key`, `hotkey`, `scroll`, or canvas `click`):
     - `{"id": "type-note", "description": "Type hello into [3] AXTextArea", "tool": "type_text", "element_index": 3, "text": "hello"}`
     - `{"id": "set-field", "description": "Set [4] AXTextField value", "tool": "set_value", "element_index": 4, "value": "42"}`
     - `{"id": "save-menu", "description": "Invoke File > Save", "tool": "invoke_menu", "path": ["File", "Save"]}`
     - `{"id": "press-return", "description": "Press return key", "tool": "press_key", "key": "return"}`
     - `{"id": "scroll-down", "description": "Scroll [5] AXScrollArea down", "tool": "scroll", "element_index": 5, "direction": "down", "amount": 3}`
3. **Direct `agy-cua` element actions (fallback inspection/action).**
   - Call `get_window_state` (`pid`, `window_id`, `tree_format: "both"`, `include_screenshot: false` unless inspecting pixels; optional case-insensitive substring `query`, never querying for the expected answer instead of reading actual state).
   - Always pass `element_token: "<snapshot_id>:<element_index>"` on direct actions and verify with a fresh `get_window_state`.

## Multi-pane apps, cross-window discovery & macOS Mail (`com.apple.mail`) guidance

- **Bounded `max_depth: 2` toolbar discovery:** Large `AXSplitGroup` -> `AXOutline` / `AXTable` hierarchies in apps like `com.apple.mail` can exhaust default walk budgets before reaching sibling `AXToolbar` items (`New Message`, `Search`). Pass `max_depth: 2` (optionally with `query`, `max_elements`, and `walk_timeout_ms`) to surface top-level toolbar controls immediately.
- **Same-PID window/sheet discovery (`discover_windows: true`):** Pass `discover_windows: true` to `jev_step` or `jev_observe` when an action opens a new window or sheet (`discovered_windows`, `newly_opened_windows: [{window_id, title}]`). Opening a window is discovery, not `goal_verified`; observe the new `window_id` and verify its postconditions explicitly.
- **Hard `WAITING_FOR_HUMAN` gate on `Send`, `Delete`, and `Empty Trash`:** Never click `Send` (`AXButton "Send"`), press send/delete shortcuts (`cmd+shift+d`, `cmd+delete`), or invoke commit menus (`["Message", "Send"]`, `["Mailbox", "Erase Deleted Items"]`, `["Finder", "Empty Trash"]`) without explicit user confirmation (`WAITING_FOR_HUMAN`) bound to the current recipient, subject, and body. Save Mail drafts (`["File", "Save"]` or `cmd+s`) and verify draft state instead.

## 1Password 8 (`com.1password.1password`) & isolated loopback browser guidance

- **Locked vault & Touch ID handoff:** On `"Lock Screen — 1Password"`, never target Master Password (`AXTextField = "Enter your password"`) or `"Reveal Password"` (`secure_element_refused`). Trigger `"Unlock using Touch ID"` in `delivery_mode: "background"` and ask the user (`WAITING_FOR_HUMAN`) to scan their fingerprint before verifying the unlocked vault. Lock when requested via `invoke_menu` `["1Password", "Lock 1Password"]` (`mode: "deterministic"`).
- **Electron `AXWebArea` search commit & item rows:** After `set_value` on the toolbar `Search` field, dispatch `press_key` (`key: "Return"`) to commit results to `AXList (Item list)` and dismiss the `AXList (Search results)` popover (whose `AXMenuItem` rows ignore background `AXPress`, unlike `AXList (Item list)` rows). Pass `max_depth: 5` and `max_elements: 500` when reading full item details.
- **Opaque autofill (`Open and Fill`) & secret redaction:** Prefer `Open and Fill` so credentials never enter chat or clipboard: (1) promoted `Open and Fill (<url>)` inline control (`ax.<idx>.open-and-fill-...`), (2) `AXPopUpButton "website. More Actions"` -> `AXMenuItem "Open and Fill"`, (3) `invoke_menu` `["Item", "Open and Fill"]` / `["Edit", "Open and Fill"]`, or (4) isolated loopback browser handoff (`jev_browser_prepare` + `jev_browser_step` on `http://127.0.0.1`, `http://localhost`, `http://[::1]`, `about:blank` with `mode: "isolated" | "isolated_named"`, `delivery_mode: "background"`, `input_route: "dom_event"`, and `semantic_v2` verification). Concealed spans (`\uf79a` / `\u2022{4,}` masks and `credential`, `password`, `api_key`, `secret`, `private_key`, `totp`, `one-time password` fields) are stripped from `static_texts` and marked `state: "enabled (secure)"` (`secure_element_refused`). Non-secret `Copy` buttons are labeled `Copy <field>`.

## Supported capabilities and exact refusals

- **Supported in background (`delivery_mode: "background"`):** `launch_app`, `get_window_state`, `jev_observe`, element-addressed or bounded window-coordinate `click` / `double_click` / `right_click`, idempotent `check` / `uncheck`, `set_value`, `type_text` (native Cocoa fields), `press_key`, `hotkey`, `invoke_menu`, `scroll`, and loopback-only `jev_browser_prepare` / `jev_browser_step`.
- **Refused by `agy-cua-jev` controller:**
  - `stale_snapshot_id`: candidate, decision, or `source_snapshot_id` mismatches the current snapshot. Re-observe first.
  - `out_of_scope_target` / `coordinate_out_of_bounds`: target `(pid, window_id)` / `(target_id, tab_id)` mismatch, or `(x, y)` outside `0 .. window_bounds.width` × `0 .. window_bounds.height` (or missing `source_snapshot_id` / `window_bounds`).
  - `unknown_element_index` / `unknown_browser_ref`: `element_index` or DOM `ref` absent from current snapshot.
  - `secure_element_refused`: secure/password inputs (`AXSecureTextField`, DOM `password`, bullet-masked values, lock-screen Master Password / Reveal Password, or secret-field `Copy` / `Reveal` / `More Actions`).
  - `mail_send_forbidden_by_safety_policy` / `secret_exfiltration_forbidden_by_safety_policy` / `commit_action_forbidden_by_safety_policy`: consequential commit or secret-exfiltration actions (`Send`, `Delete`, `Empty Trash`, `Archive Item`, `Reveal Secure Fields`, `Copy Password`, `Copy One-Time Password`, `Export`, `Share`, `cmd+shift+d`, `cmd+delete`, `cmd+option+c`, `cmd+option+r`, `cmd+shift+c`, `["Message", "Send"]`) require `WAITING_FOR_HUMAN`.
  - `existing_browser_profile_forbidden` / `external_browser_url_forbidden` / `browser_tool_forbidden`: `existing_profile` attachment, non-loopback or `file://` / `data:` URLs, `browser_download`, and `browser_set_input_files`.
  - `incompatible_tool_for_role` / `unsupported_element_action`: tool/action unsupported by target element role.
  - `background_drag_unavailable_on_macos`: `drag` requires foreground delivery on macOS.
  - `foreground_or_global_input_forbidden`: `delivery_mode: "foreground"`, `scope: "desktop"`, `bring_to_front`, `get_desktop_state`, `clipboard_*`, `kill_app`, or `set_config`.
  - `window_id_not_found` / `window_owner_pid_mismatch`: target window closed or hosted out-of-process; re-identify with `launch_app` or stop cleanly.

## Final answer

State the verified application result, Jev model identity (`returned_model`) and selected candidate IDs when used, tools called in order, stage timings (`observe_ms`, `provider_ms`, `dispatch_ms`, `verify_ms`), and every refusal with its exact error text.
