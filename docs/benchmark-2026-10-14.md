# AGY-CUA vs. Upstream Google Computer Use API — Benchmark & Hillclimb Report

- **Date:** 2026-10-14
- **Repository:** `saariuslystoned/agy-cua`
- **Branch:** `feat/cua-benchmark-hillclimb`
- **Outer Planner Model:** `gemini-3.8-flash` (`google-genai 2.23.0`)
- **Pinned Action Selector (`agy-cua-jev`):** `jev-1.13.0` (TypeSafe hosted API)
- **Driver Runtime:** `cua-driver 0.35.4` (background window-scoped delivery, `focus_violations = 0`)
- **Total API Spend Across All Calibration & Full Benchmark Runs:** **`$0.2896`** (out of `$5.00` run cap)

> [!WARNING]
> **Benchmark condition disclosure:** the numbers below were measured with the `google-cua-bg` planner on the model's **default** thinking budget, while the three AGY arms used `thinking_budget=512`. That difference affects planner latency, tokens, and cost independently of the controller, so the speedup/cost ratios do not isolate controller gains. The harness now applies one shared `PLANNER_THINKING_BUDGET` (512) to every arm and records it in `summary.json`; these results must be re-run under that parity condition before the ratios are cited.

---

## 1. Executive Summary

We benchmarked four computer-use architectures head-to-head across six deterministic macOS desktop tasks (`T1`–`T5b`, 24 total task-arm evaluations) and implemented three targeted hillclimbs in [`bin/agy-cua-jev-mcp`](../bin/agy-cua-jev-mcp):

| Contender Arm | Description | Task Pass Rate | Total Wall Time (6 Tasks) | Speedup vs. Google CUA | Outer LLM Turns | Screenshots Sent | Gemini Input Tokens | Total Cost (6 Tasks) | Cost Reduction vs. Google CUA | Focus Violations |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`google-cua-bg`** | Upstream Google Computer Use (`ENVIRONMENT_DESKTOP`, window PNG + normalized `0..1000` pixel/key dispatch) | 6 / 6 (100%) | **409.71 s** | 1.0× | 51 | 51 PNGs | 237,164 | **$0.1427** | 1.0× | 0 |
| **`agy-cua-direct`** | Gemini + `agy-cua` MCP (`get_window_state` AX markdown + `element_token` actions) | 6 / 6 (100%) | **109.89 s** | **3.7×** | 41 | 0 PNGs | 128,345 | **$0.0701** | **2.0×** | 0 |
| **`agy-cua-jev-v0`** | Gemini + `agy-cua-jev` baseline (`jev_step` 1-by-1, default `cua-driver` steady cursor session) | 6 / 6 (100%) | **158.58 s** | **2.6×** | 40 | 0 PNGs | 62,656 | **$0.0513** | **2.8×** | 0 |
| **`agy-cua-jev-v1`** | **Hillclimbed `agy-cua-jev`** (`jev_sequence` + `post_obs -> pre_obs` chaining + auto `set_value` + fast session) | **6 / 6 (100%)** | **59.95 s** | **6.8× faster** | **8** | **0 PNGs** | **9,409** (**25.2× fewer**) | **$0.0207** | **6.9× cheaper** | **0** |

### Key Takeaways
1. **6.8× End-to-End Speedup Over Upstream Google Computer Use (`59.95s` vs. `409.71s`):** On complex multi-field workflows (`T5a` macOS Mail Triage & Safe Draft Compose and `T5b` 1Password Vault Item Creation), `agy-cua-jev-v1` completes in **`9.46s`** and **`8.13s`** respectively, whereas upstream Google Computer Use takes **`101.27s`** (`10.7×` slower) and **`92.72s`** (`11.4×` slower) due to per-step PNG screenshot encoding, vision token prefill, and multi-step `click_at -> cmd+a -> type_text` sequences.
2. **25.2× Reduction in Gemini Input Tokens & 6.9× Lower USD Cost:** `agy-cua-jev-v1` uses compact AX state and executes entire guarded subgoal sequences in a single `jev_sequence` call, consuming **9,409 Gemini input tokens** across all 6 tasks combined versus **237,164 tokens** for `google-cua-bg`.
3. **Zero Screenshot Exposure & Controller-Enforced Safety (`T5a` Mail & `T5b` 1Password):**
   - In `T5b_1password_vault_item`, `google-cua-bg` transmits **10 full window PNG screenshots** of the 1Password window over the network. `agy-cua-jev-v1` transmits **0 screenshots**, automatically detects `AXSecureTextField` and masked password values (`\uf79a`), strips their values from observations (`state: "enabled (secure)"`), excludes them from candidate tables, and refuses any explicit targeting (`secure_element_refused`).
   - In `T5a_mail_triage_draft`, `agy-cua-jev-v1` enforces a hard controller-level refusal (`mail_send_forbidden_by_safety_policy`) against Mail `Send` controls and `cmd+shift+d` shortcuts while permitting background folder selection, search filtering, and draft saving.

---

## 2. Hillclimbing Optimizations Implemented in [`bin/agy-cua-jev-mcp`](../bin/agy-cua-jev-mcp)

### Hillclimb 1: Fast Owned `cua-driver` Session Initialization (2.5× Lower Dispatch Latency)
- **Bottleneck Diagnosed:** `cua-driver 0.35.4` defaults to `agent_cursor.enabled: true` with `cursor.motion.timing: "steady"`, imposing a `600ms+` animated cursor glide and `80ms` click dwell on every background action (`dispatch_ms ≈ 950–1020ms`).
- **Fix:** When [`DriverClient._ensure_started`](../bin/agy-cua-jev-mcp) connects to `cua-driver`, it configures the owned `session_id` via `start_session(session=self.session_id, cursor_motion={timing: "fixed", glide_duration_ms: 1, dwell_after_click_ms: 0}, cursor_theme={reduced_motion: "on"})` and `set_agent_cursor_enabled(session=self.session_id, enabled=false)`.
- **Measured Impact:** Per-action `dispatch_ms` dropped from **`~960ms`** (`agy-cua-jev-v0`) to **`~405ms`** (`agy-cua-jev-v1`) without altering global `cua-driver` user configuration.

### Hillclimb 2: `jev_batch` Guarded Multi-Step Execution with Verified Observation Chaining (`chain_observations: true`, `observe_ms = 0.0ms` on Steps `2..N`)
- **Bottleneck Diagnosed:** In `agy-cua-jev-v0`, a 7-step task requires 7 outer Gemini LLM turns and 14 `get_window_state` IPC calls (`pre_obs` + `post_obs` on every step), even though Step $k$'s verified `post_obs` is identical to Step $k+1$'s `pre_obs` when `(query, max_depth)` scope matches and no pixel anchor is needed.
- **Fix:** Added optional `chain_observations: true` (and `jev_sequence` alias; honoured only with explicit `allow_interstep_drift: true`, which the controlled benchmark fixture sets) on [`JevMcpServer.handle_batch`](../bin/agy-cua-jev-mcp), which requires a bound `expected_postcondition` on every step (`1..16`), observes fresh on Step 1, and chains each step's `goal_verified` `post_obs` into the next step's `pre_obs` (`observe_ms = 0.0ms` on Steps `2..N`) while binding a fresh `element_token` to Step $k$'s `post_snapshot_id` and halting immediately on the first failed or unverified step.

### Hillclimb 3: First-Class `value` Parameter (`set_value` Auto-Candidates) + `invoke_menu` + Depth Prioritization + Commit & Security Guards
- **Fix:**
  - Expanded [`ACTIONABLE_AX_ROLES`](../bin/agy-cua-jev-mcp) and [`VALUE_SETTABLE_AX_ROLES`](../bin/agy-cua-jev-mcp) to include `AXSearchField`, `AXSecureTextField`, `AXCell`, and `AXLink`, with depth-aware element/candidate prioritization and `omitted_elements_count` / `omitted_candidates_count` reporting when caps (`MAX_ELEMENTS = 64`, `MAX_AUTO_CANDIDATES = 28`) are exceeded.
  - Added optional `value` string parameter on `jev_step` and `jev_batch` steps so [`build_bound_candidates`](../bin/agy-cua-jev-mcp) automatically generates closed `set_value` candidates (`sv.<idx>.<slug>`) across non-secure editable fields (`AXTextField`, `AXSearchField`, `AXTextArea`, `AXComboBox`) without exposing `value` in candidate descriptions sent to Jev.
  - Added background `invoke_menu` (`path: ["File", "Save"]`), `NSSecureTextField` / `\uf79a` redaction (`secure_element_refused`), cross-snapshot label continuity verification (`target_element_label_mismatch`), and consequential commit safety gates (`mail_send_forbidden_by_safety_policy` / `commit_action_forbidden_by_safety_policy`) across `click` / `double_click`, `hotkey` (`cmd+shift+d`, `cmd+delete`), and `invoke_menu` (`["Message", "Send"]`, `["Mailbox", "Erase Deleted Items"]`, `["Finder", "Empty Trash"]`).

---

## 3. Per-Task Head-to-Head Results

| Task ID & Description | Arm | Verdict | Wall Time (s) | Outer Turns | Actions | Median Step (ms) | Observe Total (ms) | Jev Total (ms) | Dispatch Total (ms) | Verify Total (ms) | PNGs Sent | Gemini In Tokens | Jev Tokens | Cost (USD) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`T1_calc_arith`**<br>Calculator `47 × 83 = 3,901` | `google-cua-bg`<br>`agy-cua-direct`<br>`agy-cua-jev-v0`<br>**`agy-cua-jev-v1`** | PASS<br>PASS<br>PASS<br>**PASS** | 63.26s<br>30.38s<br>29.65s<br>**12.46s** | 8<br>8<br>7<br>**2** | 7<br>7<br>6<br>**7** | 303.5<br>490.0<br>1480.5<br>**820.7** | 964.2<br>626.3<br>637.7<br>**128.8** | 0.0<br>0.0<br>1772.9<br>**1687.8** | 1277.6<br>2874.1<br>6428.3<br>**3435.5** | 0.0<br>0.0<br>567.0<br>**635.7** | 8<br>0<br>0<br>**0** | 35,215<br>36,959<br>13,109<br>**2,880** | 0<br>0<br>12,842<br>**15,080** | \$0.0204<br>\$0.0199<br>\$0.0104<br>**\$0.0057** |
| **`T2_calc_recovery`**<br>Perturbed `'99×4' -> 38 × 45 = 1,710` | `google-cua-bg`<br>`agy-cua-direct`<br>`agy-cua-jev-v0`<br>**`agy-cua-jev-v1`** | PASS<br>PASS<br>PASS<br>**PASS** | 54.00s<br>15.49s<br>41.59s<br>**14.94s** | 8<br>8<br>8<br>**2** | 7<br>7<br>7<br>**7** | 344.2<br>503.5<br>1923.3<br>**974.2** | 1091.8<br>634.3<br>744.7<br>**101.8** | 0.0<br>0.0<br>5124.2<br>**2599.2** | 1244.6<br>2946.0<br>6717.0<br>**3454.9** | 0.0<br>0.0<br>559.1<br>**644.1** | 8<br>0<br>0<br>**0** | 35,049<br>28,437<br>13,987<br>**2,349** | 0<br>0<br>14,995<br>**15,085** | \$0.0206<br>\$0.0149<br>\$0.0116<br>**\$0.0045** |
| **`T3_appkit_form`**<br>Multi-Control Provisioning Form | `google-cua-bg`<br>`agy-cua-direct`<br>`agy-cua-jev-v0`<br>**`agy-cua-jev-v1`** | PASS<br>PASS<br>PASS<br>**PASS** | 58.93s<br>12.09s<br>16.69s<br>**6.85s** | 8<br>6<br>6<br>**1** | 7<br>5<br>5<br>**5** | 1078.3<br>565.1<br>1431.2<br>**817.6** | 957.6<br>764.4<br>727.4<br>**110.2** | 0.0<br>0.0<br>2118.2<br>**1683.4** | 5862.6<br>1909.6<br>3890.0<br>**1660.1** | 0.0<br>0.0<br>618.9<br>**629.5** | 8<br>0<br>0<br>**0** | 33,854<br>10,091<br>5,659<br>**953** | 0<br>0<br>3,984<br>**3,984** | \$0.0196<br>\$0.0059<br>\$0.0049<br>**\$0.0020** |
| **`T4_appkit_dense`**<br>Dense 14px Micro-Toolbar (0 Misclicks) | `google-cua-bg`<br>`agy-cua-direct`<br>`agy-cua-jev-v0`<br>**`agy-cua-jev-v1`** | PASS<br>PASS<br>PASS<br>**PASS** | 39.53s<br>18.51s<br>13.82s<br>**8.11s** | 5<br>5<br>5<br>**1** | 4<br>4<br>4<br>**4** | 772.1<br>798.0<br>1590.3<br>**945.5** | 768.2<br>964.6<br>913.6<br>**168.1** | 0.0<br>0.0<br>1027.8<br>**1298.8** | 2331.4<br>2414.4<br>3952.1<br>**1678.5** | 0.0<br>0.0<br>751.4<br>**808.9** | 5<br>0<br>0<br>**0** | 20,585<br>15,560<br>5,254<br>**1,107** | 0<br>0<br>7,235<br>**7,235** | \$0.0135<br>\$0.0089<br>\$0.0049<br>**\$0.0028** |
| **`T5a_mail_triage_draft`**<br>Mail VIP Triage & Safe Draft Compose | `google-cua-bg`<br>`agy-cua-direct`<br>`agy-cua-jev-v0`<br>**`agy-cua-jev-v1`** | PASS<br>PASS<br>PASS<br>**PASS** | 101.27s<br>17.43s<br>37.94s<br>**9.46s** | 12<br>8<br>8<br>**1** | 11<br>7<br>7<br>**7** | 2080.2<br>600.3<br>1466.1<br>**920.0** | 1621.8<br>1194.2<br>1180.8<br>**134.1** | 0.0<br>0.0<br>2203.4<br>**2261.2** | 17758.9<br>2945.5<br>5679.2<br>**2517.8** | 0.0<br>0.0<br>969.2<br>**959.6** | 12<br>0<br>0<br>**0** | 65,948<br>24,609<br>17,913<br>**1,094** | 0<br>0<br>8,448<br>**8,448** | \$0.0379<br>\$0.0134<br>\$0.0139<br>**\$0.0034** |
| **`T5b_1password_vault_item`**<br>1Password Item & Secret Redaction | `google-cua-bg`<br>`agy-cua-direct`<br>`agy-cua-jev-v0`<br>**`agy-cua-jev-v1`** | PASS<br>PASS<br>PASS<br>**PASS** | 92.72s<br>15.99s<br>18.89s<br>**8.13s** | 10<br>6<br>6<br>**1** | 9<br>5<br>5<br>**5** | 2121.7<br>651.0<br>1573.5<br>**1085.0** | 1277.3<br>786.1<br>792.2<br>**121.0** | 0.0<br>0.0<br>2285.9<br>**2847.2** | 14552.0<br>2112.1<br>3955.9<br>**1689.2** | 0.0<br>0.0<br>673.9<br>**635.7** | 10<br>0<br>0<br>**0** | 46,513<br>12,689<br>6,734<br>**1,026** | 0<br>0<br>5,112<br>**5,112** | \$0.0307<br>\$0.0071<br>\$0.0056<br>**\$0.0023** |

---

## 4. Verification & Reproducibility

1. **Unit & Controller Boundary Tests (`46 / 46 PASS`):**
   ```bash
   python3 -m unittest discover -s tests -v
   ```
   Covers `jev_batch` guarded multi-step execution and `chain_observations=true` observation chaining (`observe_ms == 0.0` on steps `2..N`), immediate halt on failed `expected_postcondition` or timeout without replay, automatic `set_value` candidate generation via `value`, depth-aware candidate prioritization, cross-snapshot label continuity (`target_element_label_mismatch`), `NSSecureTextField` / `\uf79a` redaction and `secure_element_refused` guard, background `invoke_menu`, and consequential commit safety refusals (`mail_send_forbidden_by_safety_policy` / `commit_action_forbidden_by_safety_policy`).
2. **Benchmark Harness & Deterministic Native Fixture:**
   - AppKit multi-mode fixture: [`scripts/bench_fixture.swift`](../scripts/bench_fixture.swift) (`--mode form | dense | mail-twin | 1password-twin --state-file <path>`)
   - Head-to-head runner: [`scripts/benchmark_cua.py`](../scripts/benchmark_cua.py) (`--arms google-cua-bg,agy-cua-direct,agy-cua-jev-v0,agy-cua-jev-v1 --tasks all --max-budget-usd 5.0`)
