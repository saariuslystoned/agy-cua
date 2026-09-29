#!/bin/bash
# Headless end-to-end check: Antigravity CLI + the agy-cua skill and MCP config
# drive Calculator in the background, then a separate MCP connection reads the
# display back. Writes runs/qualify-<UTC>/ (gitignored). Exit 0 only on PASS.
#
#   scripts/qualify-calculator.sh [--installed] [--model MODEL] [--timeout 6m]
#
# By default the skill and MCP config come from this checkout via a workspace
# .agents/ dir. --installed uses an empty workspace, so only the installed
# plugin (~/.gemini/config/plugins/agy-cua) can provide them.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="gemini-3.8-flash-high"
TIMEOUT="6m"
INSTALLED=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --installed) INSTALLED=1; shift ;;
    --model) MODEL="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
AGY="$(command -v agy || echo "$HOME/.local/bin/agy")"
[[ -x "$AGY" ]] || { echo "agy (Antigravity CLI) not found" >&2; exit 2; }

OUT="$ROOT/runs/qualify-$(date -u +%Y%m%dT%H%M%SZ)"
WS="$OUT/workspace"
mkdir -p "$WS"
if [[ "$INSTALLED" == 1 ]]; then
  [[ -d "$HOME/.gemini/config/plugins/agy-cua" ]] || { echo "agy-cua plugin not installed" >&2; exit 2; }
else
  mkdir -p "$WS/.agents/skills"
  jq --arg home "$ROOT" '.mcpServers["agy-cua"].env = {AGY_CUA_HOME: $home}' \
    "$ROOT/mcp_config.json" > "$WS/.agents/mcp_config.json"   # use this checkout's shim
  cp -R "$ROOT/skills/cua-computer-use" "$WS/.agents/skills/"
fi

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
    --print-timeout "$TIMEOUT" 2>/dev/null ) \
  | python3 "$ROOT/scripts/stream-metadata.py" > "$OUT/metadata.json"
PIPE_EXITS=("${PIPESTATUS[@]}")
AGY_EXIT=${PIPE_EXITS[0]}
METADATA_EXIT=${PIPE_EXITS[1]}
DURATION=$(( $(date +%s) - START ))
kill "$SAMPLER" 2>/dev/null; wait "$SAMPLER" 2>/dev/null
"$ROOT/scripts/probe-focus.sh" > "$OUT/focus-after.json"

python3 "$ROOT/scripts/raw_mcp.py" window-state Calculator --png "$OUT/calculator-window.png" \
  > "$OUT/readback.json" 2> "$OUT/readback.stderr"
DISPLAY_VALUE="$(jq -r '.static_texts[-1] // ""' "$OUT/readback.json" 2>/dev/null)"

python3 - "$OUT" "$EXPECTED" "$DISPLAY_VALUE" "$AGY_EXIT" "$DURATION" "$MODEL" "$METADATA_EXIT" "$INSTALLED" <<'PY'
import json, sys
out, expected, display, agy_exit, duration, model, metadata_exit, installed = sys.argv[1:]
metadata = json.load(open(f"{out}/metadata.json"))
rows = metadata['tools']
servers = sorted({r['server'] for r in rows if r.get('server')})
focus = [json.loads(line) for line in open(f"{out}/focus-trace.jsonl")]
calc_front = sum(r.get('front_bundle') == 'com.apple.calculator' for r in focus)
unknown_focus = sum(not r.get('front_bundle') for r in focus)
result = {
    "source": "installed-plugin" if installed == '1' else "workspace",
    "model": model, "agy_exit": int(agy_exit), "duration_s": int(duration),
    "expected": expected, "display": display, "display_matches": display.replace(",", "") == expected,
    "tool_calls": len(rows), "errors": sum(r['state'] == 'ERROR' for r in rows),
    "mcp_servers_used": servers, "skill_file_read": metadata['skill_file_read'],
    "result_seen": metadata['result_seen'], "stream_parse_errors": metadata['parse_errors'],
    "token_usage": metadata['token_usage'], "final_status": metadata['final_status'],
    "focus_samples": len(focus), "unidentified_focus_samples": unknown_focus,
    "calculator_frontmost_samples": calc_front,
}
result["verdict"] = "PASS" if (result["display_matches"] and result["agy_exit"] == 0
                                and int(metadata_exit) == 0 and result['result_seen']
                                and result['final_status'] == 'SUCCESS'
                                and not result['stream_parse_errors'] and not result['errors']
                                and focus and not calc_front and not unknown_focus
                                and len(servers) == 1
                                and (servers[0] == "agy-cua" or servers[0].endswith("_agy-cua"))) else "FAIL"
json.dump(result, open(f"{out}/result.json", "w"), indent=2)
print(json.dumps(result, indent=2))
sys.exit(0 if result["verdict"] == "PASS" else 1)
PY
STATUS=$?
echo "proof: $OUT"
exit $STATUS
