# Proof: agy-cua Upgrade for cua-driver 0.60.0 / 0.61.0 (`ui_tree_revision`, `agent_display`, Delivery Semantics & Cancellation)

- **Branch**: `feat/cua-driver-061-revision-display`
- **Date**: 2026-11-24 UTC
- **Host / OS**: macOS `26.5.2`, `agy` CLI `1.5.14`, Jev model `jev-1.13.0` (`key_source=keychain`)
- **Installed Driver**: `cua-driver 0.60.0` (`/Applications/CuaDriver.app`, symlinked at `~/.local/bin/cua-driver`), TCC (`Accessibility`, `Screen Recording`) granted to `com.trycua.driver`. Upstream `trycua/cua` `cua-driver-rs-v0.61.0` release artifact was checked (`gh release view cua-driver-rs-v0.61.0 -R trycua/cua` -> `release not found`), so live checks ran against signed `0.60.0` while `#8611` `ui_tree_revision` forwarding and stale-revision refusal behavior are verified via schema-probing code + hermetic stdio/driver-mock tests against the `0.61.0` contract.

## 1. Driver Capabilities & Retired Shims

- **Breaking flag removal (`trycua/cua#8339`)**: Verified `cua-driver mcp --claude-code-computer-use-compat` and `cua-driver serve --session-policy /tmp/x` exit `64` on `0.60.0`.
- **Retired `#4346` shim in `bin/agy-cua-mcp`**: Live `get_window_state` on Calculator (`cua-driver 0.60.0`) returns `snapshot_id=s00000001` and `element_token` natively in text content. Replaced the JSON-line interceptor in `bin/agy-cua-mcp` with direct `os.execv` pass-through to `cua-driver mcp` (prepending `--direct` only when `AGY_CUA_DRIVER_DIRECT=1`). Verified via `tests/test_agy_cua_mcp.py`.
- **`DriverClient` daemon `PATH` hardening**: Added `/usr/bin:/bin:/usr/sbin:/sbin` to spawned `cua-driver` subprocess `PATH` in `bin/agy-cua-jev-mcp` and `scripts/raw_mcp.py` so `cua-driver serve`'s internal `/usr/sbin/lsof` check for `browser_prepare` succeeds under minimal caller environments.

## 2. Guarded `ui_tree_revision`, `delivery`/`retry`, `notifications/cancelled`, and `agent_display`

Verified in `bin/agy-cua-jev-mcp`, `skills/cua-computer-use/SKILL.md`, and `README.md`:

- **`ui_tree_revision` (`#8611`)**: `extract_window_observation` reads `ui_tree_revision` from `structuredContent` or text header (`ui_tree_revision=<int>`). `DriverClient` probes `click.ui_tree_revision` via `tools/list`; `jev_step` and `jev_batch` accept `expected_ui_tree_revision` and forward `ui_tree_revision` on supported `UI_TREE_REVISION_TOOLS` (excluding `invoke_menu`).
- **Explicit `delivery` / `retry` markers**: Preflight refusals, validation errors, `notifications/cancelled`, and driver pre-delivery refusals (`status="refused"` / `effect="refused"` / `actual_delivery.mode="not_applicable"`) emit `delivery="not_sent"`, `retry="safe"`, `uncertain_side_effect=false` without halting the session; uncertain post-dispatch driver/HTTP failures retain `uncertain_side_effect=true` (`retry="do_not_blindly_retry"`).
- **MCP `notifications/cancelled`**: `bin/agy-cua-jev-mcp` processes incoming `notifications/cancelled` notifications on a reader thread while executing `tools/call` on a dedicated FIFO worker thread, forwards `notifications/cancelled` to `cua-driver`, resets the hung driver transport when in-flight, suppresses the cancelled response, and keeps subsequent `ping` / `jev_status` calls healthy.
- **Guarded `agent_display` (`#8146`)**: Added `jev_agent_display` (`create`, `list`, `remove`, `move_window` with bounds `[640..7680]` and session ownership enforcement, cache invalidation, and closeout teardown) plus `isolate_display` / `display_width` / `display_height` on `jev_browser_prepare`. Also hardened `handle_browser_prepare` to fail closed on `get_browser_state` / `browser_navigate` refusals even when `isError` is unset.

Live probes on `cua-driver 0.60.0`:
- `handle_agent_display` (`create` `1280x800` -> `display_id=9`, `list` -> `[9]`, `move_window` invalid pid -> `status=refused`, `delivery=not_sent`, `retry=safe`, `remove` -> `ok`) passed.
- `handle_browser_prepare({"mode": "isolated", "url": "about:blank", "isolate_display": True})` created virtual display `8` (`1920x1080` at `x=1728`), attached `target_id` / `tab_id`, moved the isolated browser window (`window_moved=true`), and cleaned up `display_id=8` on `close()`.

## 3. Automated Checks

- `bin/agy-cua doctor`: all 6 checks `ok` (`cua-driver 0.60.0`, TCC grants to `com.trycua.driver`, `jev-1.13.0` Keychain route ready, plugin installed).
- `shellcheck bin/agy-cua scripts/*.sh`: exit `0`.
- `python3 -m unittest discover -s tests -v`: **`Ran 111 tests in 19.987s` — `OK`**.

## 4. 11-Task Benchmark (`agy-cua-jev-v3`, `gemini-3.8-flash` `high`)

Full 11-task benchmark artifact: [`proof/bench-20261124T004432Z-cua-driver-060-summary.json`](bench-20261124T004432Z-cua-driver-060-summary.json) (resumed `T1`–`T7b` from `runs/bench-20261124T003238Z/summary.json` after fixing `T8` initial observation retry in `scripts/benchmark_cua.py`; total spend `$0.1825`, `run_status="completed"`).

| Task | Result | Key Verified Metrics | Focus Violations | Cost |
| --- | --- | --- | --- | --- |
| `T1_calc_arith` | PASS | `3,901` (`6` actions, `0` PNGs) | `0` | `$0.0159` |
| `T2_calc_recovery` | PASS | `1,710` (`8` actions, `0` PNGs) | `0` | `$0.0388` |
| `T3_appkit_form` | PASS | `submitted=True` (`5` actions, `0` PNGs) | `0` | `$0.0071` |
| `T4_appkit_dense` | PASS | `B2,B5,B8`, `misclicks=0`, `committed=True` | `0` | `$0.0086` |
| `T5a_mail_triage_draft` | PASS | `draft_saved=True`, `send_violation=False` | `0` | `$0.0102` |
| `T5b_1password_vault_item` | PASS | `saved=True`, `leaked=False` | `0` | `$0.0102` |
| `T6a_ax_incomplete` | PASS | `target_activated=True`, `decoy_clicks=0` | `0` | `$0.0158` |
| `T6b_visual_canvas` | PASS | `quad=3`, `misclicks=0`, `pngs=2` | `0` | `$0.0112` |
| `T7a_cross_window` | PASS | `code='REL-704'`, `confirmed=True` | `0` | `$0.0183` |
| `T7b_1password_browser` | Functional PASS / Focus note | `open_and_fill=True`, `portal_signed_in=True`, `ticket='OP-AUTOFILL-771'`, `leaked=False` | `122` (Chrome launch activation) | `$0.0172` |
| `T8_dense_hierarchy_drill` | PASS | `cluster='Production Cluster'`, `INC-4821`, `P1`, `applied=True`, `decoys=0` | `0` | `$0.0293` |

Result: **10 / 10 desktop PASS (`0` focus violations)**, **11 / 11 functional PASS** (`T7b` portal sign-in verified; stock `cua-driver 0.60.0` Chrome process spawn activates macOS frontmost app during `browser_prepare`, matching the prior `2026-11-01` stock `v3` baseline).
