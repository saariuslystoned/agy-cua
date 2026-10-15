#!/bin/bash
# Headless end-to-end check: Antigravity CLI (Gemini 4 Argon) + agy-cua + agy-cua-jev
# drive Calculator in the background, then a separate MCP connection reads the
# display back. Writes runs/qualify-<UTC>/ (gitignored). Exit 0 only on PASS.
#
#   scripts/qualify-calculator.sh [--installed] [--model MODEL] [--timeout 6m] [--no-jev] [--batch]
#
# By default the skill and MCP config come from this checkout via a workspace
# .agents/ dir. --installed uses an empty workspace, so only the installed
# plugin (~/.gemini/config/plugins/agy-cua) can provide them.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="gemini-4-argon-high"
TIMEOUT="6m"
INSTALLED=0
REQUIRE_JEV=1
USE_BATCH=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --installed) INSTALLED=1; shift ;;
    --model) MODEL="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --no-jev) REQUIRE_JEV=0; shift ;;
    --batch) USE_BATCH=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
AGY="$(command -v agy || echo "$HOME/.local/bin/agy")"
[[ -x "$AGY" ]] || { echo "agy (Antigravity CLI) not found" >&2; exit 2; }

OUT="$ROOT/runs/qualify-$(date -u +%Y%m%dT%H%M%SZ)"
WS="$OUT/workspace"
JEV_TRACE="$OUT/jev-trace.jsonl"
mkdir -p "$WS"
: > "$JEV_TRACE"
if [[ "$INSTALLED" == 1 ]]; then
  [[ -d "$HOME/.gemini/config/plugins/agy-cua" ]] || { echo "agy-cua plugin not installed" >&2; exit 2; }
else
  mkdir -p "$WS/.agents/skills"
  jq --arg home "$ROOT" --arg trace "$JEV_TRACE" '
    .mcpServers["agy-cua"].env = {AGY_CUA_HOME: $home} |
    if .mcpServers["agy-cua-jev"] then
      .mcpServers["agy-cua-jev"].env = {AGY_CUA_HOME: $home, AGY_CUA_JEV_TRACE_FILE: $trace}
    else . end
  ' "$ROOT/mcp_config.json" > "$WS/.agents/mcp_config.json"
  cp -R "$ROOT/skills/cua-computer-use" "$WS/.agents/skills/"
fi

A=$((RANDOM % 87 + 12)); B=$((RANDOM % 87 + 12)); EXPECTED=$((A * B))
if [[ "$REQUIRE_JEV" == 1 && "$USE_BATCH" == 1 ]]; then
  cat > "$OUT/prompt.txt" <<EOF
Use the cua-computer-use skill, the agy-cua MCP server, and the agy-cua-jev MCP server (jev_batch). In Calculator (bundle id com.apple.calculator), compute $A × $B in the background using jev_batch with expected_postcondition for each step (All Clear with static_text_equals "0", digits of $A with static_text_equals, Multiply with static_text_contains "×", digits of $B with static_text_equals, Equals with static_text_equals "$EXPECTED"), leaving my current frontmost app in front, then report the verified value on Calculator's display, the Jev model used, and the batch timings. Do not use the terminal, the browser, or subagents, and do not write files.
EOF
elif [[ "$REQUIRE_JEV" == 1 ]]; then
  cat > "$OUT/prompt.txt" <<EOF
Use the cua-computer-use skill, the agy-cua MCP server, and the agy-cua-jev MCP server (jev_step). In Calculator (bundle id com.apple.calculator), compute $A × $B in the background using jev_step with expected_postcondition for each button press (All Clear with static_text_equals "0", digits of $A with static_text_equals, Multiply with static_text_contains "×", digits of $B with static_text_equals, Equals with static_text_equals "$EXPECTED"), leaving my current frontmost app in front, then report the verified value on Calculator's display, the Jev model used, and the step timings. Do not use the terminal, the browser, or subagents, and do not write files.
EOF
else
  cat > "$OUT/prompt.txt" <<EOF
Use the cua-computer-use skill and the agy-cua MCP server. In Calculator (bundle id com.apple.calculator), compute $A × $B with its on-screen buttons, leaving my current frontmost app in front, then report the value on Calculator's display. Do not use the terminal, the browser, or subagents, and do not write files.
EOF
fi

"$ROOT/scripts/probe-focus.sh" > "$OUT/focus-before.json"
SAMPLER=""
GK_REAPER=""
cleanup() {
  for pid in "$SAMPLER" "$GK_REAPER"; do
    if [[ -n "$pid" ]]; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

( while :; do "$ROOT/scripts/probe-focus.sh"; sleep 0.5; done ) > "$OUT/focus-trace.jsonl" 2>/dev/null &
SAMPLER=$!
# Workaround for agy CLI 1.3.5 ignoring "disabled": true on GitKraken in
# ~/.gemini/antigravity/mcp_config.json and blocking for 300s x 2 on gk mcp.
python3 - <<'PY' >/dev/null 2>&1 &
import json, os, pathlib, subprocess, time
cfg = pathlib.Path.home() / ".gemini/antigravity/mcp_config.json"
try:
    disabled = json.loads(cfg.read_text()).get("mcpServers", {}).get("GitKraken", {}).get("disabled") is True
except Exception:
    disabled = False
if not disabled:
    raise SystemExit(0)
def descendant_gk_pids(root_pid):
    out = subprocess.check_output(["ps", "-ax", "-o", "pid,ppid,command"], text=True).splitlines()[1:]
    ppid_of = {}
    gk_candidates = set()
    for line in out:
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, cmd = int(parts[0]), int(parts[1]), parts[2]
        ppid_of[pid] = ppid
        if cmd.endswith("--scheme=antigravity") and "gk mcp" in cmd:
            gk_candidates.add(pid)
    descendants = set()
    for pid in gk_candidates:
        cur = pid
        seen = set()
        while cur and cur not in seen:
            if cur == root_pid:
                descendants.add(pid)
                break
            seen.add(cur)
            cur = ppid_of.get(cur, 0)
    return descendants
parent_pid = os.getppid()
started = time.monotonic()
keep = {os.getpid()}
while os.getppid() == parent_pid and time.monotonic() - started < 600:
    for pid in descendant_gk_pids(parent_pid) - keep:
        subprocess.run(["kill", "-9", str(pid)], stderr=subprocess.DEVNULL)
    time.sleep(0.25)
PY
GK_REAPER=$!
START=$(date +%s)
( cd "$WS" && AGY_CUA_JEV_TRACE_FILE="$JEV_TRACE" "$AGY" -p "$(cat "$OUT/prompt.txt")" --model "$MODEL" \
    --dangerously-skip-permissions --sandbox --output-format stream-json \
    --print-timeout "$TIMEOUT" 2>/dev/null ) \
  | python3 "$ROOT/scripts/stream-metadata.py" > "$OUT/metadata.json"
PIPE_EXITS=("${PIPESTATUS[@]}")
AGY_EXIT=${PIPE_EXITS[0]}
METADATA_EXIT=${PIPE_EXITS[1]}
DURATION=$(( $(date +%s) - START ))
cleanup
SAMPLER=""
GK_REAPER=""
"$ROOT/scripts/probe-focus.sh" > "$OUT/focus-after.json"

python3 "$ROOT/scripts/raw_mcp.py" window-state Calculator --png "$OUT/calculator-window.png" \
  > "$OUT/readback.json" 2> "$OUT/readback.stderr"
DISPLAY_VALUE="$(jq -r '.static_texts[-1] // ""' "$OUT/readback.json" 2>/dev/null)"

python3 - "$OUT" "$EXPECTED" "$DISPLAY_VALUE" "$AGY_EXIT" "$DURATION" "$MODEL" "$METADATA_EXIT" "$INSTALLED" "$REQUIRE_JEV" "$USE_BATCH" "$A" "$B" <<'PY'
import json, os, statistics, sys
out, expected, display, agy_exit, duration, model, metadata_exit, installed, require_jev, use_batch, a_val, b_val = sys.argv[1:]
require_jev_bool = require_jev == "1"
use_batch_bool = use_batch == "1"
metadata = json.load(open(f"{out}/metadata.json", encoding="utf-8"))
rows = metadata["tools"]
servers = sorted({r["server"] for r in rows if r.get("server")})
focus = [json.loads(line) for line in open(f"{out}/focus-trace.jsonl", encoding="utf-8") if line.strip()]
calc_front = sum(r.get("front_bundle") == "com.apple.calculator" for r in focus)
unknown_focus = sum(not r.get("front_bundle") for r in focus)

trace_file = f"{out}/jev-trace.jsonl"
raw_jev_entries = []
if os.path.exists(trace_file):
    for line in open(trace_file, encoding="utf-8"):
        if line.strip():
            raw_jev_entries.append(json.loads(line))

if not raw_jev_entries:
    raw_jev_entries = list(metadata.get("jev_entries") or [])

batch_entries = [e for e in raw_jev_entries if e.get("schema") == "agy_cua.jev_batch_v1"]
step_entries = [e for e in raw_jev_entries if e.get("schema") == "agy_cua.jev_step_v1"]
if not step_entries and batch_entries:
    for b in batch_entries:
        for s in (b.get("completed_steps") or []):
            if isinstance(s, dict):
                step_item = dict(s)
                step_item.setdefault("schema", "agy_cua.jev_step_v1")
                step_item.setdefault("returned_model", b.get("returned_model"))
                step_item.setdefault("sdk_identity", b.get("sdk_identity"))
                step_item.setdefault("accounting", b.get("accounting"))
                step_entries.append(step_item)
        if isinstance(b.get("failed_step"), dict):
            failed_item = dict(b["failed_step"])
            failed_item.setdefault("schema", "agy_cua.jev_step_v1")
            failed_item.setdefault("accounting", b.get("accounting"))
            step_entries.append(failed_item)

jev_entries = step_entries if step_entries else raw_jev_entries

observe_vals = [e.get("timings_ms", {}).get("observe_ms", 0.0) for e in jev_entries if "timings_ms" in e]
provider_vals = [e.get("timings_ms", {}).get("provider_ms", e.get("provider_ms", 0.0)) for e in jev_entries]
dispatch_vals = [e.get("timings_ms", {}).get("dispatch_ms", 0.0) for e in jev_entries if "timings_ms" in e]
verify_vals = [e.get("timings_ms", {}).get("verify_ms", 0.0) for e in jev_entries if "timings_ms" in e]

jev_models = sorted({
    m
    for e in (jev_entries + batch_entries)
    for m in [e.get("returned_model")]
    if isinstance(m, str) and m and m != "deterministic-controller"
})
jev_sdks = sorted({
    s
    for e in (jev_entries + batch_entries)
    for s in [e.get("sdk_identity")]
    if isinstance(s, str) and s
})
jev_selected = [e.get("selected_id") for e in jev_entries if e.get("selected_id")]

mutating_driver_tools = {
    "click", "double_click", "right_click", "type_text", "press_key",
    "hotkey", "set_value", "scroll", "drag",
}
direct_cua_mutations = sum(
    1 for r in rows
    if r.get("server") in {"agy-cua", "agy-cua_agy-cua"} and r.get("tool") in mutating_driver_tools
)
min_jev_steps = len(str(a_val)) + len(str(b_val)) + 3
steps_verified = bool(step_entries) and all(
    e.get("schema") == "agy_cua.jev_step_v1"
    and e.get("status") == "verified"
    and e.get("verification_outcome") == "goal_verified"
    and e.get("verified") is True
    and e.get("returned_model") in {"jev-1.13.0", "deterministic-controller"}
    and not (e.get("accounting") or {}).get("halted", False)
    and (e.get("accounting") or {}).get("uncertain_calls", 0) == 0
    and (e.get("accounting") or {}).get("uncertain_side_effects", 0) == 0
    for e in step_entries
)
batches_verified = all(
    b.get("schema") == "agy_cua.jev_batch_v1"
    and b.get("status") == "verified"
    and b.get("verification_outcome") == "goal_verified"
    and b.get("verified") is True
    and not (b.get("accounting") or {}).get("halted", False)
    and (b.get("accounting") or {}).get("uncertain_calls", 0) == 0
    and (b.get("accounting") or {}).get("uncertain_side_effects", 0) == 0
    for b in batch_entries
)
all_jev_verified = steps_verified and batches_verified and (not use_batch_bool or bool(batch_entries))
final_post_texts = (
    (step_entries[-1].get("post_static_texts") or [])
    if step_entries
    else ((batch_entries[-1].get("post_static_texts") or []) if batch_entries else [])
)
jev_sequence_complete = (
    len(step_entries) >= min_jev_steps
    and bool(jev_selected)
    and "clear" in str(jev_selected[0])
    and any("multiply" in str(s) for s in jev_selected)
    and "equals" in str(jev_selected[-1])
    and bool(final_post_texts)
    and str(final_post_texts[-1]).replace(",", "") == expected
)

allowed_servers = {"agy-cua", "agy-cua-jev", "agy-cua_agy-cua", "agy-cua_agy-cua-jev"}
servers_ok = bool(servers) and all(s in allowed_servers for s in servers)
jev_used = any(s in {"agy-cua-jev", "agy-cua_agy-cua-jev"} for s in servers) and len(step_entries) > 0

result = {
    "source": "installed-plugin" if installed == "1" else "workspace",
    "model": model,
    "agy_exit": int(agy_exit),
    "duration_s": int(duration),
    "expected": expected,
    "display": display,
    "display_matches": display.replace(",", "") == expected,
    "tool_calls": len(rows),
    "errors": sum(r.get("state") == "ERROR" for r in rows),
    "mcp_servers_used": servers,
    "skill_file_read": metadata["skill_file_read"],
    "result_seen": metadata["result_seen"],
    "stream_parse_errors": metadata["parse_errors"],
    "token_usage": metadata["token_usage"],
    "final_status": metadata["final_status"],
    "focus_samples": len(focus),
    "unidentified_focus_samples": unknown_focus,
    "calculator_frontmost_samples": calc_front,
    "jev": {
        "required": require_jev_bool,
        "used": jev_used,
        "calls": len(step_entries),
        "batches": len(batch_entries),
        "min_steps_required": min_jev_steps,
        "direct_cua_mutations": direct_cua_mutations,
        "all_steps_verified": all_jev_verified,
        "sequence_complete": jev_sequence_complete,
        "returned_models": jev_models,
        "sdk_identities": jev_sdks,
        "selected_ids": jev_selected,
        "timings_ms": {
            "observe_total_ms": round(sum(observe_vals), 2),
            "provider_total_ms": round(sum(provider_vals), 2),
            "dispatch_total_ms": round(sum(dispatch_vals), 2),
            "verify_total_ms": round(sum(verify_vals), 2),
            "provider_median_ms": round(statistics.median(provider_vals), 2) if provider_vals else 0.0,
            "dispatch_median_ms": round(statistics.median(dispatch_vals), 2) if dispatch_vals else 0.0,
        },
    },
}
result["verdict"] = "PASS" if (
    result["display_matches"]
    and result["agy_exit"] == 0
    and int(metadata_exit) == 0
    and result["result_seen"]
    and result["final_status"] == "SUCCESS"
    and not result["stream_parse_errors"]
    and not result["errors"]
    and bool(focus)
    and not calc_front
    and not unknown_focus
    and servers_ok
    and (
        not require_jev_bool
        or (
            jev_used
            and jev_models == ["jev-1.13.0"]
            and direct_cua_mutations == 0
            and all_jev_verified
            and jev_sequence_complete
        )
    )
) else "FAIL"
json.dump(result, open(f"{out}/result.json", "w", encoding="utf-8"), indent=2)
print(json.dumps(result, indent=2))
sys.exit(0 if result["verdict"] == "PASS" else 1)
PY
STATUS=$?
echo "proof: $OUT"
exit $STATUS
