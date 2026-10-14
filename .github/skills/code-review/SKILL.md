---
name: code-review
description: Pull request code review skill for saariuslystoned/agy-cua (Cua Driver + Jev computer-use plugin for Google Antigravity). Use when reviewing pull requests, diffs, MCP protocol shims, Jev action selection, shell scripts, or skills in this repository.
---

# `saariuslystoned/agy-cua` Pull Request Code Review Skill

Use this skill when reviewing pull requests in `saariuslystoned/agy-cua`. Focus on concrete behavioral defects, protocol mismatches, security/credential boundary violations, and missing regression tests.

## Repository Architecture & Boundaries

1. **[`bin/agy-cua-mcp`](file:///bin/agy-cua-mcp) (Untouched Cua Driver Shim):**
   - Thin pass-through stdio proxy to `cua-driver mcp`.
   - Rewrites `get_window_state` text responses so `snapshot_id=<id>` appears on line 1 (working around `trycua/cua#4346` where Antigravity strips `structuredContent`).
   - Do not add Jev logic or extra dependencies to `bin/agy-cua-mcp`.

2. **[`bin/agy-cua-jev-mcp`](file:///bin/agy-cua-jev-mcp) (Bounded Jev Action Selection MCP Server):**
   - Pure Python 3 standard-library stdio MCP server (`jev_status`, `jev_select_action`, `jev_step`). No third-party pip dependencies are allowed.
   - `stdout` is reserved exclusively for JSON-RPC 2.0 messages (`Content-Length` framed or newline-delimited JSON matching client framing). Never print debug logs or diagnostics to `stdout`.

3. **Cua Driver `0.35.4+` Wire Contract:**
   - `get_window_state` must be called with `tree_format: "both"` when both `structuredContent.elements` and `tree_markdown` are needed (`0.35.4` defaults to `"markdown"`).
   - Element-addressed background actions (`click`, `double_click`, `right_click`, `set_value`, `type_text`, `press_key`, `hotkey`, `scroll`) require `element_token: "<snapshot_id>:<element_index>"` on the Cua Driver wire call.
   - All mutating actions must stay scoped to the explicit `(pid, window_id)` target with `delivery_mode: "background"` (except `set_value`, which is AX-direct and does not accept `delivery_mode`). Foreground delivery, desktop-wide actions (`get_desktop_state`, `bring_to_front`, `drag`), `clipboard_*`, `kill_app`, `set_config`, and `browser_*` are forbidden in `agy-cua-jev-mcp`.

4. **Pinned Jev (`jev-1.13.0`) & Verification Invariants:**
   - Jev is text-only (`jev-1.13.0`). Never send screenshots, base64 images, or unredacted secrets to Jev.
   - Single-shot, zero-retry policy (`max_attempts_per_call = 1`). Any uncertain provider call or failed post-dispatch verification increments `uncertain_calls` / `uncertain_side_effects` and halts the session (`halted: true`).
   - Post-dispatch verification (`check_postcondition`) requires a fresh `snapshot_id` (`post_snapshot_id != pre_snapshot_id`). When `expected_postcondition` is `None`, verification requires an observable AX state change (`state_changed == True`); idempotent actions that may leave the display unchanged (such as clicking `All Clear` when Calculator already shows `0`) must pass an explicit `expected_postcondition` (e.g. `{"static_text_contains": "0"}`).

5. **Credentials & Secret Hygiene:**
   - The TypeSafe API key lives in macOS Keychain (`service: "typesafe-jev"`) or runtime `TYPESAFE_API_KEY` env var.
   - Never log, echo, commit, or include API keys or `Authorization` headers in error strings, traces, or test fixtures.
