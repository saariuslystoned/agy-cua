# AGY-CUA vs. Upstream Google Computer Use API — Benchmark & Hillclimb Report

- **Date:** 2026-10-14 (results re-run 2026-10-15 under parity conditions)
- **Repository:** `saariuslystoned/agy-cua`
- **Branch:** `feat/cua-benchmark-hillclimb`
- **Outer Planner Model:** `gemini-3.8-flash` (`google-genai 2.23.0`)
- **Pinned Action Selector (`agy-cua-jev`):** `jev-1.13.0` (TypeSafe hosted API)
- **Driver Runtime:** `cua-driver 0.35.4` (background window-scoped delivery, `focus_violations = 0`)
- **Results run:** `bench-20261015T152518Z` on harness commit `9f55b10` (raw metrics: [`proof/bench-20261015T152518Z-summary.json`](../proof/bench-20261015T152518Z-summary.json))
- **Benchmark conditions:** every arm uses the same planner `thinking_budget=512`; one bounded provider attempt per call (no retries); `screenshots_sent` counts every image transmitted (history resends included); planner decides task completion in every arm.
- **Run spend:** **`$0.2474`** (`$1.00` cap, `reserved_usd = 0.0`, uncertain provider failures: Gemini `0`, Jev `0`)

> [!NOTE]
> An earlier 2026-10-14 run reported 6.8× speedup / 6.9× lower cost / 25.2× fewer tokens. That run gave the Google arm the model's default thinking budget (AGY arms: 512), ended the `v1` arm on batch verification without a final planner turn, and under-counted resent screenshots. Those numbers are superseded by the parity re-run below.

---

## 1. Executive Summary

We benchmarked four computer-use architectures head-to-head across six deterministic macOS desktop tasks (`T1`–`T5b`, 24 total task-arm evaluations) and implemented three targeted hillclimbs in [`bin/agy-cua-jev-mcp`](../bin/agy-cua-jev-mcp):

| Contender Arm | Description | Task Pass Rate | Total Wall Time (6 Tasks) | Speedup vs. Google CUA | Outer LLM Turns | Images Sent | Gemini Input Tokens | Total Cost (6 Tasks) | Cost Reduction vs. Google CUA | Focus Violations |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`google-cua-bg`** | Upstream Google Computer Use (`ENVIRONMENT_DESKTOP`, window PNG + normalized `0..1000` pixel/key dispatch) | 6 / 6 | 409.67 s | 1.0× | 50 | 94 images | 160,488 | \$0.0940 | 1.0× | 0 |
| **`agy-cua-direct`** | Gemini + `agy-cua` MCP (`get_window_state` AX markdown + `element_token` actions) | 6 / 6 | 139.80 s | 2.9× | 41 | 0 images | 126,322 | \$0.0685 | 1.4× | 0 |
| **`agy-cua-jev-v0`** | Gemini + `agy-cua-jev` baseline (`jev_step` 1-by-1, default `cua-driver` steady cursor session) | 6 / 6 | 178.39 s | 2.3× | 40 | 0 images | 59,342 | \$0.0508 | 1.8× | 0 |
| **`agy-cua-jev-v1`** | **Hillclimbed `agy-cua-jev`** (`jev_sequence` + `post_obs -> pre_obs` chaining + auto `set_value` + fast session) | **6 / 6** | **95.27 s** | **4.3×** | **16** | **0 images** | **25,136** (6.4× fewer) | **\$0.0341** | **2.8×** | **0** |

### Key Takeaways
1. **4.3× End-to-End Speedup Over Upstream Google Computer Use (`95.27s` vs. `409.67s`):** On the multi-field workflows (`T5a` Mail-twin triage & draft, `T5b` 1Password-twin item creation), `agy-cua-jev-v1` completes in **`12.53s`** and **`12.38s`**, versus **`81.92s`** (`6.5×`) and **`80.17s`** (`6.5×`) for upstream Google Computer Use, driven by per-step PNG encoding, vision prefill, and more planner turns.
2. **6.4× Fewer Gemini Input Tokens & 2.8× Lower USD Cost:** `agy-cua-jev-v1` consumes **25,136** Gemini input tokens across all 6 tasks versus **160,488** for `google-cua-bg` (`\$0.0341` vs. `\$0.0940` total, Jev tokens included).
3. **Zero Screenshot Exposure & Controller-Enforced Safety (`T5a` Mail & `T5b` 1Password):**
   - In `T5b_1password_vault_item`, `google-cua-bg` transmits **17 window PNG images** (history resends included) of the 1Password-twin window. `agy-cua-jev-v1` transmits **0 screenshots**, automatically detects `AXSecureTextField` and masked password values (`\uf79a`), strips their values from observations (`state: "enabled (secure)"`), excludes them from candidate tables, and refuses any explicit targeting (`secure_element_refused`).
   - In `T5a_mail_triage_draft`, `agy-cua-jev-v1` enforces a hard controller-level refusal (`mail_send_forbidden_by_safety_policy`) against Mail `Send` controls and `cmd+shift+d` shortcuts while permitting background folder selection, search filtering, and draft saving.

---

## 2. Hillclimbing Optimizations Implemented in [`bin/agy-cua-jev-mcp`](../bin/agy-cua-jev-mcp)

### Hillclimb 1: Fast Owned `cua-driver` Session Initialization (2.0× Lower Dispatch Latency)
- **Bottleneck Diagnosed:** `cua-driver 0.35.4` defaults to `agent_cursor.enabled: true` with `cursor.motion.timing: "steady"`, imposing a `600ms+` animated cursor glide and `80ms` click dwell on every background action (`dispatch_ms ≈ 950–1020ms`).
- **Fix:** When [`DriverClient._ensure_started`](../bin/agy-cua-jev-mcp) connects to `cua-driver`, it configures the owned `session_id` via `start_session(session=self.session_id, cursor_motion={timing: "fixed", glide_duration_ms: 1, dwell_after_click_ms: 0}, cursor_theme={reduced_motion: "on"})` and `set_agent_cursor_enabled(session=self.session_id, enabled=false)`.
- **Measured Impact:** Per-action `dispatch_ms` averaged **`~1039ms`** (`agy-cua-jev-v0`) vs. **`~517ms`** (`agy-cua-jev-v1`) in the parity re-run without altering global `cua-driver` user configuration.

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

| Task ID & Description | Arm | Verdict | Wall Time (s) | Outer Turns | Actions | Median Step (ms) | Observe Total (ms) | Jev Total (ms) | Dispatch Total (ms) | Verify Total (ms) | Images Sent | Gemini In Tokens | Jev Tokens | Cost (USD) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`T1_calc_arith`**<br>Calculator `47 × 83 = 3,901` | `google-cua-bg`<br>`agy-cua-direct`<br>`agy-cua-jev-v0`<br>**`agy-cua-jev-v1`** | PASS<br>PASS<br>PASS<br>**PASS** | 57.95s<br>21.04s<br>34.21s<br>**24.19s** | 8<br>8<br>7<br>**4** | 7<br>7<br>6<br>**6** | 1622.1<br>1115.3<br>1504.0<br>**1166.8** | 1564.6<br>1081.3<br>938.1<br>**917.3** | 0.0<br>0.0<br>1869.9<br>**1615.7** | 8962.6<br>6333.9<br>6143.0<br>**3145.4** | 0.0<br>0.0<br>696.5<br>**1076.8** | 15<br>0<br>0<br>**0** | 29,248<br>29,022<br>12,963<br>**8,076** | 0<br>0<br>14,557<br>**14,557** | \$0.0171<br>\$0.0152<br>\$0.0112<br>**\$0.0093** |
| **`T2_calc_recovery`**<br>Perturbed `'99×4' -> 38 × 45 = 1,710` | `google-cua-bg`<br>`agy-cua-direct`<br>`agy-cua-jev-v0`<br>**`agy-cua-jev-v1`** | PASS<br>PASS<br>PASS<br>**PASS** | 66.08s<br>37.35s<br>40.44s<br>**22.44s** | 8<br>8<br>8<br>**3** | 7<br>7<br>7<br>**7** | 1728.1<br>1110.4<br>1572.1<br>**882.0** | 1994.9<br>1035.3<br>1153.7<br>**518.9** | 0.0<br>0.0<br>2635.9<br>**1704.5** | 10024.6<br>6425.6<br>6915.6<br>**3334.3** | 0.0<br>0.0<br>996.8<br>**1050.2** | 15<br>0<br>0<br>**0** | 27,795<br>37,101<br>15,057<br>**5,965** | 0<br>0<br>17,061<br>**17,076** | \$0.0162<br>\$0.0201<br>\$0.0126<br>**\$0.0090** |
| **`T3_appkit_form`**<br>Multi-Control Provisioning Form | `google-cua-bg`<br>`agy-cua-direct`<br>`agy-cua-jev-v0`<br>**`agy-cua-jev-v1`** | PASS<br>PASS<br>PASS<br>**PASS** | 73.87s<br>19.72s<br>26.81s<br>**12.43s** | 8<br>6<br>6<br>**2** | 7<br>5<br>5<br>**5** | 1899.8<br>1389.5<br>1699.8<br>**1166.1** | 3552.3<br>1483.0<br>1322.5<br>**494.1** | 0.0<br>0.0<br>2627.5<br>**1199.3** | 10067.9<br>5537.5<br>5482.5<br>**2862.0** | 0.0<br>0.0<br>741.7<br>**1103.1** | 15<br>0<br>0<br>**0** | 25,688<br>10,144<br>5,661<br>**2,199** | 0<br>0<br>5,888<br>**5,836** | \$0.0151<br>\$0.0059<br>\$0.0050<br>**\$0.0031** |
| **`T4_appkit_dense`**<br>Dense 14px Micro-Toolbar (0 Misclicks) | `google-cua-bg`<br>`agy-cua-direct`<br>`agy-cua-jev-v0`<br>**`agy-cua-jev-v1`** | PASS<br>PASS<br>PASS<br>**PASS** | 49.68s<br>16.66s<br>28.10s<br>**11.30s** | 5<br>5<br>5<br>**3** | 4<br>4<br>4<br>**4** | 2300.0<br>1235.7<br>1904.2<br>**1066.2** | 1817.9<br>1004.2<br>799.6<br>**524.8** | 0.0<br>0.0<br>2233.4<br>**1075.2** | 7341.6<br>4383.2<br>4391.7<br>**2261.5** | 0.0<br>0.0<br>410.9<br>**534.8** | 9<br>0<br>0<br>**0** | 15,793<br>12,953<br>8,590<br>**3,909** | 0<br>0<br>9,524<br>**9,524** | \$0.0107<br>\$0.0070<br>\$0.0076<br>**\$0.0047** |
| **`T5a_mail_triage_draft`**<br>Mail-twin VIP Triage & Safe Draft Compose | `google-cua-bg`<br>`agy-cua-direct`<br>`agy-cua-jev-v0`<br>**`agy-cua-jev-v1`** | PASS<br>PASS<br>PASS<br>**PASS** | 81.92s<br>24.51s<br>28.41s<br>**12.53s** | 12<br>8<br>8<br>**2** | 11<br>7<br>7<br>**7** | 1925.9<br>1065.3<br>1623.3<br>**795.3** | 7071.4<br>857.4<br>1387.4<br>**318.0** | 0.0<br>0.0<br>2295.8<br>**1457.3** | 14216.9<br>7046.2<br>7366.7<br>**3483.3** | 0.0<br>0.0<br>750.9<br>**1028.1** | 23<br>0<br>0<br>**0** | 30,071<br>24,609<br>10,409<br>**2,606** | 0<br>0<br>11,422<br>**11,379** | \$0.0164<br>\$0.0134<br>\$0.0089<br>**\$0.0048** |
| **`T5b_1password_vault_item`**<br>1Password-twin Item & Secret Redaction | `google-cua-bg`<br>`agy-cua-direct`<br>`agy-cua-jev-v0`<br>**`agy-cua-jev-v1`** | PASS<br>PASS<br>PASS<br>**PASS** | 80.17s<br>20.52s<br>20.42s<br>**12.38s** | 9<br>6<br>6<br>**2** | 8<br>5<br>5<br>**5** | 1673.5<br>1148.9<br>1422.6<br>**1133.5** | 4440.2<br>732.5<br>685.1<br>**268.7** | 0.0<br>0.0<br>1040.2<br>**2551.2** | 9917.3<br>5046.1<br>5031.0<br>**2491.0** | 0.0<br>0.0<br>533.9<br>**526.9** | 17<br>0<br>0<br>**0** | 31,893<br>12,493<br>6,662<br>**2,381** | 0<br>0<br>6,822<br>**6,817** | \$0.0185<br>\$0.0070<br>\$0.0056<br>**\$0.0033** |

---

## 4. Verification & Reproducibility

1. **Unit & Controller Boundary Tests (`48 / 48 PASS`):**
   ```bash
   python3 -m unittest discover -s tests -v
   ```
   Covers `jev_batch` guarded multi-step execution and `chain_observations=true` observation chaining (`observe_ms == 0.0` on steps `2..N`), immediate halt on failed `expected_postcondition` or timeout without replay, automatic `set_value` candidate generation via `value`, depth-aware candidate prioritization, cross-snapshot label continuity (`target_element_label_mismatch`), `NSSecureTextField` / `\uf79a` redaction and `secure_element_refused` guard, background `invoke_menu`, and consequential commit safety refusals (`mail_send_forbidden_by_safety_policy` / `commit_action_forbidden_by_safety_policy`).
2. **Benchmark Harness & Deterministic Native Fixture:**
   - AppKit multi-mode fixture: [`scripts/bench_fixture.swift`](../scripts/bench_fixture.swift) (`--mode form | dense | mail-twin | 1password-twin --state-file <path>`)
   - Head-to-head runner: [`scripts/benchmark_cua.py`](../scripts/benchmark_cua.py) (`--arms google-cua-bg,agy-cua-direct,agy-cua-jev-v0,agy-cua-jev-v1 --tasks all --max-budget-usd 1.0`; run with `google-genai 2.23.0`)

## 5. Codex Computer Use — original fixture cannot be addressed (2026-10-15)

**No benchmark task has run (0/24).** Bobby approved native app access and
resolved the configurations to **GPT-6.1 Sol Fast** and **GPT-6 Astra Ultrafast**.
The native plugin can access Calculator, but cannot address the exact original
unbundled benchmark fixture. The current authority is the
[authorized admission evidence](../proof/codex-native-20261015-authorized-admission.json);
the earlier [capability probe](../proof/codex-native-20261015-probe.json) remains
historical evidence, including the initial permission failure.

| Final requested arm | Configured model | Speed/service tier | Completed / required runs | Pass rate | Wall time | Model turns | Images sent | Input/output tokens | Cost | Focus violations / probe errors |
| :--- | :--- | :--- | ---: | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `6.1 sol fast` | `gpt-6.1-sol` | Fast / `priority` | 0 / 12 | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |
| `6 astra ultrafast` | `gpt-6-astra` | Ultrafast / `ultrafast` | 0 / 12 | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |

Bobby's final choice supersedes an earlier Sol Ultrafast reply and resolves the
dispatch label “6.1 sol astra ultrafast.” Both final combinations are advertised
by the live catalog. Separate reasoning effort `low` was explicitly configured
and echoed for both admission threads. Neither thread requested inference;
these are configuration identities, not upstream per-response identity receipts.
All 24 original task/repetition slots remain `not_run` with null metrics in the
committed evidence. No per-repetition result, variance, or speed comparison can
be computed, and no timing difference can be attributed to batching or screenshots.

### Native access result and reproducibility

The genuine `unified-computer-use` plugin (`26.1007.71326`, `cua_repl/js`) was
called through Codex app-server `0.161.0`. After forwarding Bobby's explicit
Calculator approval through the documented MCP elicitation response, a read-only
Calculator probe succeeded. No permission or attestation response was invented.
No macOS permission setting changed. This establishes a programmatic native-plugin
path for supported apps, rather than a Cua Driver actor labeled as Codex.

The unchanged `launch_target_for_task()` launched the original form fixture,
created its state file, and exposed a real window through the independent harness.
Its screenshot was visually inspected: the original empty provisioning form,
Standard Tier, and Pending status. The fresh form correctly failed the final
submission oracle. The native plugin then returned:

- Exact original executable path: **Invalid app**.
- Process display name `bench_fixture`: **Invalid app**.
- Actual fixture window ID: **macOS getApp requires an app name, path, or bundle ID.**
- Native app inventory filtered to that exact window: **zero matching app entries**.
  A separate name/path inventory filter also returned no fixture app entries.

Thus, native control of the required fixture is unavailable through these
supported target forms in this session. The same targeting problem blocks a
native-chat fallback. No fixture wrapping, bundling, alternate launch principal,
or easier reconstruction was used; no setup failure was counted as a completed
task. The next return condition is native support for this original unbundled
fixture, or an explicit benchmark-contract change permitting a different setup.

The reproducible admission adapter starts **zero model turns**:

```bash
runs/benchmark-codex-runs/20261015-native-probe/venv/bin/python \
  scripts/benchmark_codex_native.py --approved-native-apps --max-budget-usd 5
```

Use a local venv with `google-genai==2.23.0`, `python-dotenv`, and Pillow for the
shared harness imports. The adapter disables the optional dotenv bootstrap and
never reads credentials. The app flag forwards an already obtained human grant
only for Calculator and this fixture; other approval requests fail closed.
Exit 2 records the blocker. Saved summaries contain selected metadata and
classifications, never arbitrary UI state or raw error text. Screenshot pixels
and state files stay in ignored private evidence.

### Authority, cost and conditions

Frozen benchmark base: PR #11 OPEN at
`154edf54e6223b4c61543d78206247535b3e2b89`. Executed adapter source:
`1c1ff24ea4b1df572e0c2eb62f353fbfdc33f29a`, branch
`codex/issue-12-codex-cu`, worktree
`/Users/bobbybones/Developer/side-quests/agy-cua-worktrees/issue-12-codex-cu`.
The evidence binds the source, fixture binary and fixture source hashes.
`TaskSpec`, task goals, setup, verifiers, and focus contracts remain identical;
executed AST comparisons prove this. The summary serialization statement was
extracted unchanged into `write_benchmark_summary()` for reuse. The original
fixture, focus probe and historical baseline summary remain byte-identical.
Production adapters were not tuned. Local validation: **68 tests passed**,
ShellCheck passed, diff checks passed.

This resumed admission work made no model request and no paid API request. The
one earlier subscription capability probe reported 52,830 input tokens (23,296
cached), 100 output tokens, and unavailable marginal dollar cost/credit debit.
Those numbers are probe-only, never benchmark results. The total issue cap is
$5; no paid API/key-backed run is admitted. Native subscription marginal cost
remains unavailable, not zero. The
[official Codex pricing](https://learn.chatgpt.com/docs/pricing) and
[speed documentation](https://learn.chatgpt.com/docs/agent-configuration/speed)
distinguish included subscription usage from credit/API billing; API prices were
not applied to subscription usage. The
[app-server protocol](https://learn.chatgpt.com/docs/app-server) documents the
native MCP call and human elicitation response used here.

A competing benchmark fixture/worker was observed and left untouched. No
exclusive desktop interval, task timer or focus monitor was started, so focus
metrics remain unavailable. A prior source read exposed the fixture sentinel in
this implementation chat; it cannot serve as a measured solver. Future runs
require fresh solver contexts without source/verifier evidence, a noncompeting
desktop interval, the original focus-based pass rule, and every failure retained.
