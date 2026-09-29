#!/bin/bash
# Headless end-to-end check: Antigravity CLI + the agy-cua skill and MCP config
# drive Calculator in the background, then a separate MCP connection reads the
# display back. Writes runs/qualify-<UTC>/ (gitignored). Exit 0 only on PASS.
#
#   scripts/qualify-calculator.sh [--model MODEL] [--timeout 6m]
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="gemini-3.8-flash-high"
TIMEOUT="6m"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
AGY="$(command -v agy || echo "$HOME/.local/bin/agy")"
[[ -x "$AGY" ]] || { echo "agy (Antigravity CLI) not found" >&2; exit 2; }

OUT="$ROOT/runs/qualify-$(date -u +%Y%m%dT%H%M%SZ)"
WS="$OUT/workspace"
mkdir -p "$WS/.agents/skills"
jq --arg home "$ROOT" '.mcpServers["agy-cua"].env = {AGY_CUA_HOME: $home}' \
  "$ROOT/mcp_config.json" > "$WS/.agents/mcp_config.json"   # use this checkout's shim
cp -R "$ROOT/skills/cua-computer-use" "$WS/.agents/skills/"

A=$((RANDOM % 87 + 12)); B=$((RANDOM % 87 + 12)); EXPECTED=$((A * B))
cat > "$OUT/prompt.txt" <<EOF
Use the cua-computer-use skill and the agy-cua MCP server. In Calculator (bundle id com.apple.calculator), compute $A × $B with its on-screen buttons, leaving my current frontmost app in front, then report the value on Calculator's display. Do not use the terminal, the browser, or subagents, and do not write files.
EOF

"$ROOT/scripts/probe-focus.sh" > "$OUT/focus-before.json"
( while :; do "$ROOT/scripts/probe-focus.sh"; sleep 0.5; done ) > "$OUT/focus-trace.jsonl" 2>/dev/null &
SAMPLER=$!
START=$(date +%s)
( cd "$WS" && "$AGY" -p "$(cat "$OUT/prompt.txt")" --model "$MODEL" \
    --dangerously-skip-permissions --sandbox --output-format stream-json \
    --print-timeout "$TIMEOUT" > "$OUT/agy.jsonl" 2> "$OUT/agy.stderr" )
AGY_EXIT=$?
DURATION=$(( $(date +%s) - START ))
kill "$SAMPLER" 2>/dev/null; wait "$SAMPLER" 2>/dev/null
"$ROOT/scripts/probe-focus.sh" > "$OUT/focus-after.json"

jq -r 'select(.event=="step_update" and .step_update.step_type=="tool" and (.step_update.state=="DONE" or .step_update.state=="ERROR"))
  | .step_update | [.step_index, .state, (.tool_info.parameters.ServerName // "-"),
    (.tool_info.parameters.ToolName // .tool_name), (.tool_info.parameters.Arguments // .tool_info.parameters // {} | tojson | .[0:160]),
    ((.tool_info.error.message // .tool_info.output // "") | tostring | gsub("\n"; " ") | .[0:200])] | @tsv' \
  "$OUT/agy.jsonl" > "$OUT/tool-calls.tsv"
jq -r 'select(.event=="result") | .result.response // ""' "$OUT/agy.jsonl" > "$OUT/agent-answer.md"

python3 "$ROOT/scripts/raw_mcp.py" window-state Calculator --png "$OUT/calculator-window.png" \
  > "$OUT/readback.json" 2> "$OUT/readback.stderr"
DISPLAY_VALUE="$(jq -r '.static_texts[-1] // ""' "$OUT/readback.json" 2>/dev/null)"

python3 - "$OUT" "$EXPECTED" "$DISPLAY_VALUE" "$AGY_EXIT" "$DURATION" "$MODEL" <<'PY'
import csv, json, sys
out, expected, display, agy_exit, duration, model = sys.argv[1:]
rows = list(csv.reader(open(f"{out}/tool-calls.tsv"), delimiter="\t", quoting=csv.QUOTE_NONE))
servers = sorted({r[2] for r in rows if r[2] != "-"})
calc_front = sum(1 for line in open(f"{out}/focus-trace.jsonl")
                 if json.loads(line).get("front_bundle") == "com.apple.calculator")
skill_read = any("SKILL.md" in r[4] for r in rows)
result = {
    "model": model, "agy_exit": int(agy_exit), "duration_s": int(duration),
    "expected": expected, "display": display, "display_matches": display.replace(",", "") == expected,
    "tool_calls": len(rows), "errors": sum(1 for r in rows if r[1] == "ERROR"),
    "mcp_servers_used": servers, "skill_file_read": skill_read,
    "calculator_frontmost_samples": calc_front,
}
result["verdict"] = "PASS" if (result["display_matches"] and result["agy_exit"] == 0
                                and servers == ["agy-cua"]) else "FAIL"
json.dump(result, open(f"{out}/result.json", "w"), indent=2)
print(json.dumps(result, indent=2))
sys.exit(0 if result["verdict"] == "PASS" else 1)
PY
STATUS=$?
echo "proof: $OUT"
exit $STATUS
