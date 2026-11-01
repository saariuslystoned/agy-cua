# `agy-cua` vs. `vercel-labs/agent-desktop` — Head-to-Head Benchmark & `agy-cua-jev-v2` Hillclimb Report

- **Date:** 2026-10-31 / 2026-11-01 UTC
- **Repository:** `saariuslystoned/agy-cua`
- **Branch:** `feat/agent-desktop-bench-jev-v2`
- **Outer Planner Model:** `gemini-3.8-flash` (`thinking_level="high"`, `temperature=0.0`, `max_attempts=1`)
- **Pinned Action Selector (`agy-cua-jev` & `agent-desktop-jev`):** `jev-1.13.0` (TypeSafe hosted API)
- **Driver / Runtime Baselines:** `cua-driver 0.35.4` (`agy-cua` arms) vs. `vercel-labs/agent-desktop v1.0.2` release binary + `scripts/jev/` (`agent-desktop` arms) on macOS 26.5.2
- **Sanitized Raw Proof:** [`proof/bench-20261101T011202Z-agent-desktop-jev-v2-summary.json`](../proof/bench-20261101T011202Z-agent-desktop-jev-v2-summary.json) (`55` completed task-arm rows)
- **Cumulative Session Spend:** **`\$1.9775`** across all runs (`\$1.9519` in the 55-row resumed chain + `\$0.0255` on an intermediate `T8` smoke probe), strictly under the **`\$2.50`** hard ceiling (`gemini_uncertain_failures = 0`, `jev_uncertain_failures = 0`, `canary_leaked = false` across all 55 rows).

---

## 1. Executive Decision: Port into `agy-cua` (`v0.3.0`), Keep `cua-driver` Default

1. **Why `agent-desktop` (`v1.0.2`) beat `agy-cua-jev-v1` out of the box on dense hierarchies (`T8`):**
   On our new 1,500+ AX-node 4-region enterprise workspace (`T8_dense_hierarchy_drill`, modeled after `@mdlahfir`'s Apple Maps long-horizon benchmark), `agy-cua-direct` and `agy-cua-jev-v1` both **failed** (`0/2`, burning `194,322` and `555,755` Gemini input tokens / `\$0.1045` and `\$0.3016`) because a 336-row middle telemetry grid starved downstream `Incident Routing Inspector` (`[365]..[369]`) and `Deployment Commit Footer` (`[370]..[371]`) controls under `v1`'s flat `64`-element / `28`-candidate caps. Both `agent-desktop-direct` (`\$0.0776`) and `agent-desktop-jev` (`\$0.0890`) **passed** `T8` via `--skeleton` + `--root @ref` subtree drilling.
2. **Why `agent-desktop` should NOT replace `cua-driver` as default:**
   Under our strict background-only contract (`focus_violations == 0`, zero cursor movement, zero clipboard touch, deterministic secret/commit refusals), headless `agent-desktop` **failed `T6b_visual_canvas`** (`0/2`, no SkyLight background coordinate synthesis without `--headed` focus steal) and **failed `T7b_1password_browser`** (`0/2`, `portal_signed_in=False`, no built-in isolated loopback CDP workflow), while consuming **3.9×–6.1× more Gemini input tokens** and **2.8× more outer LLM turns** (`75`–`76` vs. `27`) than `agy-cua-jev-v2` across the 10 desktop tasks because `agent-desktop` lacks 1-turn `jev_batch` postcondition-verified chaining.
3. **Empirical Result of Porting Takeaways 1–3 into `bin/agy-cua-jev-mcp` (`agy-cua-jev-v2`, `v0.3.0`):**
   - **10 / 10 (`100%`) Desktop Task Pass Rate (`T1`–`T7a` + `T8`) with `0` Focus Violations** — the **only** arm among all five contenders to pass all 10 desktop tasks.
   - **Solved `T8_dense_hierarchy_drill` in `5` outer turns at `\$0.0390`** (`36,585` Gemini input tokens, `0` decoy clicks) — **15.2× fewer input tokens & 7.7× cheaper than `agy-cua-jev-v1`**, and **2.0×–2.3× cheaper in `2.2×–2.8×` fewer turns than `agent-desktop`**.
   - **Lowest Total Cost & Lowest Token Footprint Across All 11 Tasks:** **`\$0.1919`** total (`136,182` Gemini input tokens, `33` outer turns) — **2.31× cheaper than `agy-cua-jev-v1`** (`\$0.4442`), **2.23× cheaper than `agent-desktop-direct`** (`\$0.4270`), and **1.77× cheaper than `agent-desktop-jev`** (`\$0.3400`).

---

## 2. Aggregate 5-Arm Head-to-Head Summary

### 2.1 Desktop Suite (`10` Tasks: `T1`–`T7a` + `T8`, Native AX + Custom Canvas + Cross-Window + 1,500-Node Hierarchy)

| Contender Arm | Pass Rate | Total Wall Time | Outer LLM Turns | Images Sent | Gemini Input Tokens | Jev Tokens | Total Cost | Focus Violations | Canary Leaks |
| :--- | :---: | ---: | ---: | :---: | ---: | ---: | ---: | :---: | :---: |
| `agy-cua-direct` | 9 / 10 (`90%`) | 283.44 s | 54 | 2 | 413,186 | 0 | \$0.2401 | 0 | 0 |
| `agy-cua-jev-v1` | 9 / 10 (`90%`) | 413.78 s | 36 | 2 | 628,275 | 74,525 | \$0.4288 | 0 | 0 |
| `agent-desktop-direct` (`v1.0.2`) | 9 / 10 (`90%`) | 405.21 s | 76 | 7 | 698,757 | 0 | \$0.4016 | 0 | 0 |
| `agent-desktop-jev` (`v1.0.2`) | 9 / 10 (`90%`) | 428.40 s | 75 | 0 | 448,535 | 171,018 | \$0.3173 | 0 | 0 |
| **`agy-cua-jev-v2` (`v0.3.0`)** | **10 / 10 (`100%`)** | **383.17 s** | **27** | **2** | **113,693** | **83,552** | **\$0.1749** | **0** | **0** |

### 2.2 Full 11-Task Suite (Including `T7b_1password_browser` Two-Surface Loopback Browser Handoff)

| Contender Arm | Strict Pass (`focus_violations == 0`) | Functional Task Completion | Total Wall Time | Outer LLM Turns | Actions | Gemini Input Tokens | Jev Tokens | Total Cost | Focus Violations (`T7b` only) |
| :--- | :---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| `agy-cua-direct` | 9 / 11 | 9 / 11 | 305.00 s | 54 | 41 | 421,792 | 0 | \$0.2471 | 0 |
| `agy-cua-jev-v1` | 9 / 11 | 10 / 11 | 448.95 s | 42 | 43 | 649,035 | 78,639 | \$0.4442 | 70 |
| `agent-desktop-direct` (`v1.0.2`) | 9 / 11 | 9 / 11 | 458.30 s | 80 | 49 | 718,985 | 0 | \$0.4270 | 0 |
| `agent-desktop-jev` (`v1.0.2`) | 9 / 11 | 9 / 11 | 477.68 s | 81 | 45 | 471,680 | 173,313 | \$0.3400 | 0 |
| **`agy-cua-jev-v2` (`v0.3.0`)** | **10 / 11** | **11 / 11 (`100%`)** | **419.38 s** | **33** | **48** | **136,182** | **87,679** | **\$0.1919** | **74** |

> [!NOTE]
> **Transparent `T7b_1password_browser` Focus Accounting:** Both `agy-cua-jev-v1` and `agy-cua-jev-v2` completed 100% of the `T7b` application workflow (`open_and_fill=True`, `secret_unchanged=True`, `portal_signed_in=True`, `user='ro_analyst'`, `ticket='OP-AUTOFILL-771'`, `pw_touched=False`, `leaked=False`), whereas `agent-desktop-direct` and `agent-desktop-jev` failed `T7b` functionally (`portal_signed_in=False`) and `agy-cua-direct` aborted on `unknown_browser_ref`. On macOS 26.5.2, upstream `cua-driver 0.35.4`'s `browser_prepare(mode="isolated_new")` spawns a new Chromium process that macOS WindowServer activates on launch (`focus_violations=70` / `74`). `FocusMonitor` is kept strict (`passed = false` whenever `focus_violations > 0`), and both strict and functional metrics are reported above.

---

## 3. Architectural Upgrades Ported in [`bin/agy-cua-jev-mcp`](../bin/agy-cua-jev-mcp) (`v0.3.0`)

1. **Takeaway 1 — Container-Scoped Progressive Disclosure (`skeleton`, `drill_regions`, `region`, `root_element_index`):**
   - Added `skeleton: bool` to `jev_observe`, `jev_step`, and `jev_batch` steps: parses hierarchy containers from `tree_markdown` + `parent_index` chains, emits `drill_regions` (`region`, `actionable_count`, `subregions`, `sample_labels`, plus `element_index` when assigned by `cua-driver`), preserves top-level window status `static_texts`, and strips deep telemetry/table text noise.
   - Added `region: str` and `root_element_index: int` subtree scoping across `jev_observe`, `jev_step`, and `jev_batch`: auto-expands `get_window_state` walk budget (`max_elements=2000`, `timeout_ms=5000` without passing unsupported schema keys to `cua-driver`), restricts elements and auto-candidates strictly to the selected container's descendants, and chains verified `post_obs -> pre_obs` across consecutive same-region batch steps while forcing a fresh read on cross-region transitions.
2. **Takeaway 2 — `240`-Candidate Jev Budget + Container-Path/State Descriptions + `<0.70`-Confidence Top-5 Rerank:**
   - Raised `v2` caps from `MAX_ELEMENTS = 64` / `MAX_AUTO_CANDIDATES = 28` to `V2_MAX_ELEMENTS = 240` / `V2_MAX_AUTO_CANDIDATES = 240` / `V2_MAX_CANDIDATES = 250` (`V2_MAX_JSON_BYTES = 131072`), with `AGY_CUA_JEV_V1_COMPAT=1` preserving exact `v1` limits.
   - Enriched `v2` candidate descriptions with `in <container_path>` and discrete `current=<val>` (`0`/`1`/short state) without leaking text-field secrets.
   - Added automatic Pass-2 Jev shortlist reranking (`reranked: true`) in `handle_step` and `handle_browser_step` whenever Pass-1 confidence is `< 0.70` across `> 5` candidates.
3. **Takeaway 3 — Bounded Postcondition Settle Polling (`wait_timeout_ms`) + Occluded macOS App Nap Wake:**
   - Added `wait_timeout_ms` (`0..10000`, default `1200` ms in `v2` when `expected_postcondition` is supplied, `0` in `v1_compat`) in `handle_step` and `handle_browser_step` with `_is_retryable_postcondition_reason` (re-polling transient unsettled UI text/values while failing closed immediately on structural `target_element_role_mismatch` / `target_element_label_mismatch`) and `verify_polls` telemetry.
   - Added `wake_macos_app_nap(pid)` (`SIGCONT` on macOS target PIDs) before live observations to prevent occluded background AppKit windows (such as `Calculator.app` under heavy WindowServer occlusion) from stalling AX IPC reads.

---

## 4. Per-Task 5-Arm Breakdown (`55` Rows)

| Task | `agy-cua-direct` | `agy-cua-jev-v1` | `agent-desktop-direct` | `agent-desktop-jev` | **`agy-cua-jev-v2` (`v0.3.0`)** |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`T1_calc_arith`**<br>`47 × 83 = 3,901` | PASS · 27.94s · 8t · `39,227` tok · \$0.0216 | PASS · 45.16s · 2t · `8,002` tok · \$0.0225 | PASS · 42.96s · 10t · `127,052` tok · \$0.0667 | PASS · 44.32s · 7t · `41,935` tok · \$0.0298 | **PASS** · 81.20s · **2t** · `11,481` tok · \$0.0348 |
| **`T2_calc_recovery`**<br>`'99×4' -> 38 × 45 = 1,710` | PASS · 32.22s · 9t · `48,030` tok · \$0.0260 | PASS · 64.67s · 2t · `10,916` tok · \$0.0335 | PASS · 43.20s · 12t · `146,591` tok · \$0.0777 | PASS · 53.44s · 11t · `83,026` tok · \$0.0510 | **PASS** · 61.88s · **2t** · **`9,939` tok** · **\$0.0289** |
| **`T3_appkit_form`**<br>Provisioning Form | PASS · 26.73s · 6t · `17,568` tok · \$0.0115 | PASS · 19.92s · 2t · `3,479` tok · \$0.0060 | PASS · 34.23s · 6t · `33,611` tok · \$0.0210 | PASS · 21.79s · 3t · `7,858` tok · \$0.0103 | **PASS** · 22.84s · **2t** · `4,753` tok · \$0.0098 |
| **`T4_appkit_dense`**<br>14px Micro-Toolbar | PASS · 22.51s · 5t · `18,358` tok · \$0.0114 | PASS · 26.18s · 2t · `4,987` tok · \$0.0110 | PASS · 25.58s · 5t · `38,035` tok · \$0.0226 | PASS · 26.37s · 6t · `20,269` tok · \$0.0145 | **PASS** · **22.21s** · **2t** · **`4,446` tok** · **\$0.0093** |
| **`T5a_mail_triage_draft`**<br>Mail VIP Triage & Safe Draft | PASS · 40.13s · 8t · `40,594` tok · \$0.0257 | PASS · 30.30s · 2t · `5,306` tok · \$0.0125 | PASS · 41.21s · 8t · `67,779` tok · \$0.0390 | PASS · 56.28s · 10t · `62,397` tok · \$0.0436 | **PASS** · **29.04s** · **2t** · `5,418` tok · **\$0.0125** |
| **`T5b_1password_vault_item`**<br>1Password Item & Redaction | PASS · 27.31s · 6t · `24,133` tok · \$0.0156 | PASS · 20.59s · 2t · `4,392` tok · \$0.0084 | PASS · 35.31s · 6t · `42,497` tok · \$0.0262 | PASS · 39.53s · 8t · `35,702` tok · \$0.0247 | **PASS** · **19.64s** · **2t** · **`4,282` tok** · **\$0.0080** |
| **`T6a_ax_incomplete`**<br>Truncated AX Query Recovery | PASS · 12.01s · 3t · `7,714` tok · \$0.0051 | PASS · 19.87s · 3t · `12,885` tok · \$0.0105 | PASS · 20.18s · 3t · `26,967` tok · \$0.0171 | PASS · 28.54s · 4t · `23,142` tok · \$0.0163 | **PASS** · 20.85s · 3t · `14,492` tok · \$0.0111 |
| **`T6b_visual_canvas`**<br>Zero-AX Canvas Click | PASS · 20.63s · 3t · `7,837` tok · \$0.0073 | PASS · 27.26s · 3t · `10,426` tok · \$0.0109 | **FAIL** · 67.20s · 8t · `45,859` tok · \$0.0362 | **FAIL** · 48.48s · 5t · `16,391` tok · \$0.0208 | **PASS** · 27.25s · 3t · `10,652` tok · \$0.0110 |
| **`T7a_cross_window`**<br>Cross-Window Sheet Handoff | PASS · 26.11s · 6t · `15,403` tok · \$0.0112 | PASS · 28.08s · 4t · `12,127` tok · \$0.0120 | PASS · 38.47s · 7t · `28,663` tok · \$0.0174 | PASS · 34.78s · 7t · `23,804` tok · \$0.0173 | **PASS** · **23.97s** · **4t** · **`11,645` tok** · **\$0.0105** |
| **`T7b_1password_browser`**<br>1Password + Loopback Browser | **FAIL** (`unknown_browser_ref`) · \$0.0071 | **FAIL** (`focus_viol=70`, signed-in OK) · \$0.0154 | **FAIL** (`portal_signed_in=False`) · \$0.0254 | **FAIL** (`portal_signed_in=False`) · \$0.0228 | **FAIL** (`focus_viol=74`, **signed-in OK**) · \$0.0170 |
| **`T8_dense_hierarchy_drill`**<br>1,500-Node 4-Region Workspace | **FAIL** (`element_token` out of range) · `194,322` tok · \$0.1045 | **FAIL** (starved at `[64]`) · 14t · `555,755` tok · \$0.3016 | **PASS** · 56.87s · 11t · `141,703` tok · \$0.0776 | **PASS** · 74.87s · 14t · `134,011` tok · \$0.0890 | **PASS** · 74.29s · **5t** · **`36,585` tok** · **\$0.0390** |

---

## 5. Follow-Up Upstream `trycua/cua` Candidates (`cua-driver`)

1. **Native `root_element_token` Subtree Walk & Optional `include_menubar: false` (`platform-macos/src/ax/tree.rs`):** Today `agy-cua-jev-mcp` requests `max_elements=2000` and scopes containers post-walk; adding native `root_element_token` to `cua-driver` will skip walking the 336-row sibling grid over AX IPC altogether and cut `T8` `observe_ms` from `~650ms` to `<50ms`.
2. **Background Launch Policy for `browser_prepare(mode="isolated_new")` on macOS 26.5+:** Launching isolated Chromium via `NSWorkspaceOpenConfiguration` (`activates = false`) or `--no-startup-window` + headless/background tab creation will eliminate the `focus_violations` spike observed on `T7b_1password_browser`.
