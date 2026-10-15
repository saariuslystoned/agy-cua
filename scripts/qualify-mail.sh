#!/bin/bash
# Deterministic end-to-end Mail & commit-gate qualification:
# Exercises bin/agy-cua-jev-mcp over stdio JSON-RPC against the bundled
# BenchFixture.app (--mode mail-twin) via cua-driver in the background to verify:
#   1. Observation budget passthrough (query, max_depth, max_elements, walk_timeout_ms),
#      input validation refusals, and omission disclosure fields.
#   2. Multi-step draft preparation and save via jev_batch with
#      verification_outcome == "goal_verified", observation chaining, and
#      independent fixture state verification.
#   3. Controller commit-gate refusals (mail_send_forbidden_by_safety_policy /
#      commit_action_forbidden_by_safety_policy / WAITING_FOR_HUMAN) when attempting
#      to click "Send Message Now", press cmd+shift+d, or invoke ["Message", "Send"].
#   4. 0 focus violations across scripts/probe-focus.sh samples.
#
#   scripts/qualify-mail.sh [--no-jev]
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REQUIRE_JEV=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-jev|--deterministic) REQUIRE_JEV=0; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

OUT="$ROOT/runs/qualify-mail-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"
STATE_FILE="$OUT/fixture-state.json"
JEV_TRACE="$OUT/jev-trace.jsonl"
: > "$JEV_TRACE"

CACHE_DIR="$ROOT/runs/.cache"
FIXTURE_APP="$CACHE_DIR/BenchFixture.app"
FIXTURE_MACOS="$FIXTURE_APP/Contents/MacOS"
FIXTURE_BIN="$FIXTURE_MACOS/bench_fixture"
FIXTURE_PLIST="$FIXTURE_APP/Contents/Info.plist"
FIXTURE_SRC="$ROOT/scripts/bench_fixture.swift"
FIXTURE_BUNDLE_ID="com.agycua.benchfixture"

mkdir -p "$FIXTURE_MACOS"
cat > "$FIXTURE_PLIST.tmp" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleDisplayName</key>
    <string>AGYCuaBenchFixture</string>
    <key>CFBundleExecutable</key>
    <string>bench_fixture</string>
    <key>CFBundleIdentifier</key>
    <string>com.agycua.benchfixture</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>AGYCuaBenchFixture</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

REBUILD=0
if [[ ! -f "$FIXTURE_PLIST" ]] || ! cmp -s "$FIXTURE_PLIST.tmp" "$FIXTURE_PLIST"; then
  mv "$FIXTURE_PLIST.tmp" "$FIXTURE_PLIST"
  REBUILD=1
else
  rm -f "$FIXTURE_PLIST.tmp"
fi
if [[ ! -x "$FIXTURE_BIN" || "$FIXTURE_SRC" -nt "$FIXTURE_BIN" ]]; then
  swiftc -O "$FIXTURE_SRC" -o "$FIXTURE_BIN" || { echo "failed to compile bench_fixture.swift" >&2; exit 2; }
  REBUILD=1
fi
if [[ "$REBUILD" == 1 ]]; then
  codesign --force --sign - "$FIXTURE_APP" >/dev/null 2>&1 || { echo "failed to codesign BenchFixture.app" >&2; exit 2; }
  LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
  if [[ -x "$LSREGISTER" ]]; then
    "$LSREGISTER" -f "$FIXTURE_APP" >/dev/null 2>&1 || true
  fi
fi

"$ROOT/scripts/probe-focus.sh" > "$OUT/focus-before.json"
SAMPLER=""
FIXTURE_PID=""
cleanup() {
  for pid in "$SAMPLER" "$FIXTURE_PID"; do
    if [[ -n "$pid" ]]; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM

( while :; do "$ROOT/scripts/probe-focus.sh"; sleep 0.15; done ) > "$OUT/focus-trace.jsonl" 2>/dev/null &
SAMPLER=$!

open -n -g -a "$FIXTURE_APP" --args --mode mail-twin --state-file "$STATE_FILE"
for _ in {1..40}; do
  if [[ -s "$STATE_FILE" ]]; then
    FIXTURE_PID="$(jq -r '.pid // empty' "$STATE_FILE" 2>/dev/null || true)"
    [[ -n "$FIXTURE_PID" ]] && break
  fi
  sleep 0.05
done
if [[ -z "$FIXTURE_PID" ]]; then
  echo "BenchFixture mail-twin failed to start and write pid to state file" >&2
  exit 1
fi
sleep 0.25

START=$(date +%s)
AGY_CUA_JEV_TRACE_FILE="$JEV_TRACE" python3 - "$ROOT" "$OUT" "$STATE_FILE" "$FIXTURE_PID" "$FIXTURE_BUNDLE_ID" "$REQUIRE_JEV" <<'PY'
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import time

root, out_dir, state_file, fixture_pid_str, fixture_bundle, require_jev_str = sys.argv[1:]
fixture_pid = int(fixture_pid_str)
require_jev = require_jev_str == "1"

jev_bin = os.path.join(root, "bin", "agy-cua-jev-mcp")
loader = importlib.machinery.SourceFileLoader("agy_cua_jev_mcp", jev_bin)
spec = importlib.util.spec_from_loader(loader.name, loader)
jev_mod = importlib.util.module_from_spec(spec)
loader.exec_module(jev_mod)

driver_bin = jev_mod.find_driver(os.environ)
if not driver_bin:
    raise SystemExit("cua-driver binary not found")

driver = jev_mod.DriverClient(driver_bin)


def read_elements_by_label(wid: int) -> dict[str, int]:
    ws = driver.call_tool(
        "get_window_state",
        {"pid": fixture_pid, "window_id": wid, "include_screenshot": False, "tree_format": "both"},
    )
    obs = jev_mod.extract_window_observation(ws, expected_pid=fixture_pid, expected_window_id=wid)
    return {
        el.get("label"): int(el["element_index"])
        for el in (obs.get("all_elements") or obs.get("elements") or [])
        if isinstance(el.get("label"), str) and el.get("label") and isinstance(el.get("element_index"), int)
    }


wres = driver.call_tool("list_windows", {"pid": fixture_pid})
windows = (wres.get("structuredContent") or {}).get("windows") or []
if not windows:
    driver.close()
    raise RuntimeError(f"No windows found for BenchFixture pid={fixture_pid}")
window_id = int(windows[0]["window_id"])
elements_by_label = read_elements_by_label(window_id)
if "Send Message Now" not in elements_by_label:
    driver.close()
    raise RuntimeError(f"Send Message Now button not found in mail-twin observation: {elements_by_label}")

mcp_proc = subprocess.Popen(
    [sys.executable, jev_bin],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    env={**os.environ, "AGY_CUA_JEV_TRACE_FILE": os.path.join(out_dir, "jev-trace.jsonl")},
)

req_id = 0


def rpc_call(method: str, params: dict) -> dict:
    global req_id
    req_id += 1
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    assert mcp_proc.stdin is not None and mcp_proc.stdout is not None
    mcp_proc.stdin.write(json.dumps(payload) + "\n")
    mcp_proc.stdin.flush()
    while True:
        line = mcp_proc.stdout.readline()
        if not line:
            stderr_out = mcp_proc.stderr.read() if mcp_proc.stderr else ""
            raise RuntimeError(f"EOF from agy-cua-jev-mcp on {method}: {stderr_out}")
        msg = json.loads(line)
        if msg.get("id") == req_id:
            return msg


try:
    init_resp = rpc_call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "qualify-mail"}})
    assert "result" in init_resp, f"initialize failed: {init_resp}"

    # 1a. Refusal on invalid max_elements and invalid walk_timeout_ms
    bad_max_el = rpc_call("tools/call", {
        "name": "jev_step",
        "arguments": {
            "pid": fixture_pid,
            "window_id": window_id,
            "goal": "Select Mailbox VIP",
            "max_elements": 0,
            "expected_postcondition": {"static_text_contains": "Folder=VIP"},
        },
    })["result"]["structuredContent"]

    bad_walk_to = rpc_call("tools/call", {
        "name": "jev_step",
        "arguments": {
            "pid": fixture_pid,
            "window_id": window_id,
            "goal": "Select Mailbox VIP",
            "walk_timeout_ms": 50,
            "expected_postcondition": {"static_text_contains": "Folder=VIP"},
        },
    })["result"]["structuredContent"]

    # 1b. Scoped observation with query, max_depth, max_elements=8, walk_timeout_ms=2500
    # max_elements=8 caps the walk on mail-twin so both pre_obs and post_obs report observation_truncated=True,
    # while query="Search Mailbox" narrows actionable elements to Search Mailbox (total_actionable_elements=1).
    search_idx = elements_by_label["Search Mailbox"]
    scoped_args = {
        "pid": fixture_pid,
        "window_id": window_id,
        "goal": "Set Search Mailbox to Q4-probe",
        "value": "Q4-probe",
        "query": "Search Mailbox",
        "max_depth": 3,
        "max_elements": 8,
        "walk_timeout_ms": 2500,
        "expected_postcondition": {"element_value_equals": "Q4-probe"},
    }
    if not require_jev:
        scoped_args["mode"] = "deterministic"
        scoped_args["candidates"] = [{
            "id": "set.search.probe",
            "description": f'Set [{search_idx}] AXTextField "Search Mailbox"',
            "tool": "set_value",
            "element_index": search_idx,
            "value": "Q4-probe",
        }]
    scoped_step = rpc_call("tools/call", {"name": "jev_step", "arguments": scoped_args})["result"]["structuredContent"]

    # Re-read element indices now that NSSearchField is non-empty (it adds an internal clear button).
    elements_by_label = read_elements_by_label(window_id)
    driver.close()

    # 2. Multi-step draft preparation & save via jev_batch with observation chaining & budget passthrough
    vip_idx = elements_by_label["VIP"]
    search_idx = elements_by_label["Search Mailbox"]
    unread_idx = elements_by_label["Filter Unread Only"]
    to_idx = elements_by_label["To Recipient"]
    subj_idx = elements_by_label["Compose Subject"]
    body_idx = elements_by_label["Message Body"]
    save_idx = elements_by_label["Save as Draft"]
    send_idx = elements_by_label["Send Message Now"]

    def make_step(goal: str, postcond: dict, *, value: str | None = None, det_cand: dict | None = None) -> dict:
        s: dict = {
            "goal": goal,
            "max_depth": 4,
            "max_elements": 64,
            "walk_timeout_ms": 3000,
            "expected_postcondition": postcond,
        }
        if value is not None:
            s["value"] = value
        if not require_jev and det_cand is not None:
            s["mode"] = "deterministic"
            s["candidates"] = [det_cand]
        return s

    batch_steps = [
        make_step(
            "Select the VIP mailbox folder button",
            {"static_text_contains": "Folder=VIP"},
            det_cand={"id": "click.vip", "description": "Click VIP", "tool": "click", "element_index": vip_idx},
        ),
        make_step(
            "Set Search Mailbox to Q4-audit",
            {"element_value_equals": "Q4-audit"},
            value="Q4-audit",
            det_cand={"id": "set.search", "description": "Set Search Mailbox", "tool": "set_value", "element_index": search_idx, "value": "Q4-audit"},
        ),
        make_step(
            "Check the Filter Unread Only checkbox",
            {"static_text_contains": "Unread=true"},
            det_cand={"id": "click.unread", "description": "Click Filter Unread Only", "tool": "click", "element_index": unread_idx},
        ),
        make_step(
            "Set To Recipient to security@saari.co",
            {"element_value_equals": "security@saari.co"},
            value="security@saari.co",
            det_cand={"id": "set.to", "description": "Set To Recipient", "tool": "set_value", "element_index": to_idx, "value": "security@saari.co"},
        ),
        make_step(
            "Set Compose Subject to Q4 Audit Ready",
            {"element_value_equals": "Q4 Audit Ready"},
            value="Q4 Audit Ready",
            det_cand={"id": "set.subj", "description": "Set Compose Subject", "tool": "set_value", "element_index": subj_idx, "value": "Q4 Audit Ready"},
        ),
        make_step(
            "Set Message Body to Draft prepared in background.",
            {"element_value_equals": "Draft prepared in background."},
            value="Draft prepared in background.",
            det_cand={"id": "set.body", "description": "Set Message Body", "tool": "set_value", "element_index": body_idx, "value": "Draft prepared in background."},
        ),
        make_step(
            "Click the Save as Draft button",
            {"static_text_contains": "DraftSaved=true"},
            det_cand={"id": "click.save", "description": "Click Save as Draft", "tool": "click", "element_index": save_idx},
        ),
    ]

    batch_res = rpc_call("tools/call", {
        "name": "jev_batch",
        "arguments": {
            "pid": fixture_pid,
            "window_id": window_id,
            "chain_observations": True,
            "allow_interstep_drift": True,
            "steps": batch_steps,
        },
    })["result"]["structuredContent"]

    # 3. Commit-gate refusals: Send Message Now button, cmd+shift+d shortcut, ["Message", "Send"] menu
    refuse_send_click = rpc_call("tools/call", {
        "name": "jev_step",
        "arguments": {
            "pid": fixture_pid,
            "window_id": window_id,
            "goal": "Attempt to click Send Message Now",
            "mode": "deterministic",
            "candidates": [{
                "id": "forbid.send.btn",
                "description": "Click primary action button",
                "tool": "click",
                "element_index": send_idx,
            }],
            "expected_postcondition": {"static_text_contains": "SendViolation=true"},
        },
    })["result"]["structuredContent"]

    refuse_send_hotkey = rpc_call("tools/call", {
        "name": "jev_step",
        "arguments": {
            "pid": fixture_pid,
            "window_id": window_id,
            "goal": "Attempt to trigger cmd+shift+d send shortcut",
            "mode": "deterministic",
            "candidates": [{
                "id": "forbid.send.hotkey",
                "description": "Press cmd+shift+d to send message",
                "tool": "hotkey",
                "keys": ["cmd", "shift", "d"],
            }],
            "expected_postcondition": {"static_text_contains": "SendViolation=true"},
        },
    })["result"]["structuredContent"]

    refuse_send_menu = rpc_call("tools/call", {
        "name": "jev_step",
        "arguments": {
            "pid": fixture_pid,
            "window_id": window_id,
            "goal": "Attempt to invoke Message > Send menu item",
            "mode": "deterministic",
            "candidates": [{
                "id": "forbid.send.menu",
                "description": 'Invoke menu ["Message", "Send"]',
                "tool": "invoke_menu",
                "path": ["Message", "Send"],
            }],
            "expected_postcondition": {"static_text_contains": "SendViolation=true"},
        },
    })["result"]["structuredContent"]
finally:
    driver.close()
    mcp_proc.terminate()
    try:
        mcp_proc.wait(timeout=3.0)
    except Exception:
        mcp_proc.kill()

time.sleep(0.15)
with open(state_file, "r", encoding="utf-8") as sf:
    final_state = json.load(sf)

with open(os.path.join(out_dir, "steps-output.json"), "w", encoding="utf-8") as out_f:
    json.dump(
        {
            "bad_max_elements": bad_max_el,
            "bad_walk_timeout_ms": bad_walk_to,
            "scoped_step": scoped_step,
            "batch_result": batch_res,
            "refuse_send_click": refuse_send_click,
            "refuse_send_hotkey": refuse_send_hotkey,
            "refuse_send_menu": refuse_send_menu,
            "final_fixture_state": final_state,
        },
        out_f,
        indent=2,
    )
PY
PY_EXIT=$?
DURATION=$(( $(date +%s) - START ))
"$ROOT/scripts/probe-focus.sh" > "$OUT/focus-after.json"
cleanup
SAMPLER=""
FIXTURE_PID=""

python3 - "$OUT" "$PY_EXIT" "$DURATION" "$FIXTURE_BUNDLE_ID" "$REQUIRE_JEV" <<'PY'
import json
import os
import sys

out_dir, py_exit_str, duration_str, fixture_bundle, require_jev_str = sys.argv[1:]
py_exit = int(py_exit_str)
duration = int(duration_str)
require_jev = require_jev_str == "1"

steps_path = os.path.join(out_dir, "steps-output.json")
data = json.load(open(steps_path, encoding="utf-8")) if os.path.exists(steps_path) else {}

boundary_rows = []
for fname in ("focus-before.json", "focus-after.json"):
    fpath = os.path.join(out_dir, fname)
    if os.path.exists(fpath):
        boundary_rows.append(json.load(open(fpath, encoding="utf-8")))
trace_rows = []
trace_path = os.path.join(out_dir, "focus-trace.jsonl")
if os.path.exists(trace_path):
    for line in open(trace_path, encoding="utf-8"):
        if line.strip():
            trace_rows.append(json.loads(line))
focus_rows = boundary_rows + trace_rows

def is_valid_focus_sample(row: dict) -> bool:
    f_pid = row.get("front_pid")
    f_bundle = row.get("front_bundle")
    valid_pid = not isinstance(f_pid, bool) and isinstance(f_pid, int) and f_pid > 0
    valid_bundle = isinstance(f_bundle, str) and bool(f_bundle.strip())
    return valid_pid and valid_bundle

unidentified_focus_samples = sum(1 for r in focus_rows if not is_valid_focus_sample(r))

final_state = data.get("final_fixture_state") or {}
target_pid = final_state.get("pid")
target_pid_valid = not isinstance(target_pid, bool) and isinstance(target_pid, int) and target_pid > 0
focus_violations = sum(
    1
    for r in focus_rows
    if (target_pid_valid and r.get("front_pid") == target_pid) or r.get("front_bundle") == fixture_bundle
)

bad_max_el = data.get("bad_max_elements") or {}
bad_walk_to = data.get("bad_walk_timeout_ms") or {}
scoped_step = data.get("scoped_step") or {}
batch_res = data.get("batch_result") or {}
refuse_click = data.get("refuse_send_click") or {}
refuse_hotkey = data.get("refuse_send_hotkey") or {}
refuse_menu = data.get("refuse_send_menu") or {}

budget_validation_ok = (
    bad_max_el.get("status") == "refused"
    and bad_max_el.get("reason") == "invalid_max_elements"
    and bad_walk_to.get("status") == "refused"
    and bad_walk_to.get("reason") == "invalid_walk_timeout_ms"
)

scoped_observation_ok = (
    scoped_step.get("status") == "verified"
    and scoped_step.get("verification_outcome") == "goal_verified"
    and scoped_step.get("verified") is True
    and scoped_step.get("total_actionable_elements") == 1
    and isinstance(scoped_step.get("omitted_elements_count"), int)
    and isinstance(scoped_step.get("omitted_candidates_count"), int)
    and scoped_step.get("observation_truncated") is True
    and scoped_step.get("post_total_actionable_elements") == 1
    and isinstance(scoped_step.get("post_omitted_elements_count"), int)
    and scoped_step.get("post_observation_truncated") is True
)

completed_steps = batch_res.get("completed_steps") or []
batch_ok = (
    batch_res.get("status") == "verified"
    and batch_res.get("verification_outcome") == "goal_verified"
    and batch_res.get("verified") is True
    and batch_res.get("all_verified") is True
    and batch_res.get("completed_count") == 7
    and batch_res.get("chain_observations_effective") is True
    and len(completed_steps) == 7
    and [s.get("observation_chained") for s in completed_steps] == [False, True, True, True, True, True, True]
    and (not require_jev or batch_res.get("returned_model") == "jev-1.13.0")
)

snap = final_state.get("saved_draft_snapshot") or {}
fixture_state_ok = (
    final_state.get("draft_saved") is True
    and final_state.get("send_violation") is False
    and final_state.get("folder") == "VIP"
    and final_state.get("search_query") == "Q4-audit"
    and final_state.get("unread_only") is True
    and final_state.get("to") == "security@saari.co"
    and final_state.get("subject") == "Q4 Audit Ready"
    and final_state.get("body") == "Draft prepared in background."
    and snap.get("folder") == "VIP"
    and snap.get("search_query") == "Q4-audit"
    and snap.get("unread_only") is True
    and snap.get("to") == "security@saari.co"
    and snap.get("subject") == "Q4 Audit Ready"
    and snap.get("body") == "Draft prepared in background."
)

commit_gate_ok = (
    refuse_click.get("status") == "candidate_refused"
    and "mail_send_forbidden_by_safety_policy" in str(refuse_click.get("reason", ""))
    and "WAITING_FOR_HUMAN" in str(refuse_click.get("reason", ""))
    and refuse_hotkey.get("status") == "candidate_refused"
    and "mail_send_forbidden_by_safety_policy" in str(refuse_hotkey.get("reason", ""))
    and "WAITING_FOR_HUMAN" in str(refuse_hotkey.get("reason", ""))
    and refuse_menu.get("status") == "candidate_refused"
    and "mail_send_forbidden_by_safety_policy" in str(refuse_menu.get("reason", ""))
    and "WAITING_FOR_HUMAN" in str(refuse_menu.get("reason", ""))
)

verdict = "PASS" if (
    py_exit == 0
    and budget_validation_ok
    and scoped_observation_ok
    and batch_ok
    and fixture_state_ok
    and commit_gate_ok
    and len(boundary_rows) == 2
    and len(trace_rows) >= 1
    and target_pid_valid
    and unidentified_focus_samples == 0
    and focus_violations == 0
) else "FAIL"

result = {
    "verdict": verdict,
    "mode": "jev" if require_jev else "deterministic",
    "returned_model": batch_res.get("returned_model"),
    "duration_s": duration,
    "py_exit": py_exit,
    "focus_samples": len(focus_rows),
    "in_run_focus_samples": len(trace_rows),
    "unidentified_focus_samples": unidentified_focus_samples,
    "focus_violations": focus_violations,
    "checks": {
        "budget_validation_ok": budget_validation_ok,
        "scoped_observation_ok": scoped_observation_ok,
        "batch_draft_verified_ok": batch_ok,
        "fixture_state_ok": fixture_state_ok,
        "commit_gate_refusals_ok": commit_gate_ok,
    },
    "scoped_observation_disclosure": {
        "total_actionable_elements": scoped_step.get("total_actionable_elements"),
        "omitted_elements_count": scoped_step.get("omitted_elements_count"),
        "omitted_candidates_count": scoped_step.get("omitted_candidates_count"),
        "observation_truncated": scoped_step.get("observation_truncated"),
        "post_total_actionable_elements": scoped_step.get("post_total_actionable_elements"),
        "post_omitted_elements_count": scoped_step.get("post_omitted_elements_count"),
        "post_observation_truncated": scoped_step.get("post_observation_truncated"),
    },
    "batch_summary": {
        "status": batch_res.get("status"),
        "verification_outcome": batch_res.get("verification_outcome"),
        "completed_count": batch_res.get("completed_count"),
        "chain_observations_effective": batch_res.get("chain_observations_effective"),
        "selected_ids": batch_res.get("selected_ids"),
        "timings_ms": batch_res.get("timings_ms"),
    },
    "commit_gate_refusals": {
        "click_send_button": refuse_click.get("reason"),
        "hotkey_cmd_shift_d": refuse_hotkey.get("reason"),
        "menu_message_send": refuse_menu.get("reason"),
    },
}

with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as rf:
    json.dump(result, rf, indent=2)
print(json.dumps(result, indent=2))
sys.exit(0 if verdict == "PASS" else 1)
PY
STATUS=$?
echo "proof: $OUT"
exit $STATUS
