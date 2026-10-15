#!/usr/bin/env python3
"""Admission/setup adapter for genuine Codex native Computer Use benchmarks.

Task setup, fixture binary, independent oracles, focus policy and summary
serialization come from benchmark_cua.py. No model turn is ever started here.
--approved-native-apps forwards an explicit human grant for these test apps only.
The probe remains fail-closed by default; this runner never grants other access.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

from probe_codex_native import Probe

ROOT = Path(__file__).resolve().parents[1]
ARMS = {
    "codex-cu-sol-fast": ("gpt-6.1-sol", "priority"),
    "codex-cu-astra-ultrafast": ("gpt-6-astra", "ultrafast"),
}
UNAVAILABLE = {
    "outer_turns": "App-server user turns do not expose internal model request count.",
    "actions_dispatched": "Native tool calls may batch inputs; no executed input-action counter is exposed.",
    "screenshots_sent": "No provider-transmitted image/history-resend counter is exposed.",
    "cost_usd": "ChatGPT subscription marginal billing unavailable; API prices are not applied.",
}


def load_harness():
    # Do not let the shared harness's optional dotenv bootstrap read any .env.
    os.environ["AGY_CUA_BENCH_ENV_FILE"] = str(ROOT / "runs" / "__no_env_file__")
    if Path(os.environ["AGY_CUA_BENCH_ENV_FILE"]).exists():
        raise RuntimeError("unexpected_env_bootstrap_file")
    spec = importlib.util.spec_from_file_location("native_benchmark_authority", ROOT / "scripts/benchmark_cua.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def approved_request(message: dict, app_targets: set[str], human_approved: bool) -> bool:
    params = message.get("params", {})
    schema = params.get("requestedSchema", {})
    match = re.fullmatch(r'Allow Computer Use to use "(.+)"\?', params.get("message", ""))
    return bool(
        human_approved and message.get("method") == "mcpServer/elicitation/request"
        and params.get("serverName") == "cua_repl" and params.get("mode") == "form"
        and schema.get("type") == "object" and schema.get("properties") == {}
        and match and match.group(1) in app_targets
    )


class NativeSession(Probe):
    def __init__(self, app_targets: set[str], human_approved: bool, event):
        self.app_targets = app_targets
        self.human_approved = human_approved
        self.event = event
        self.observer = lambda message: None
        super().__init__()

    def receive(self, timeout: float) -> dict:
        try:
            message = self.messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("native_session_timeout") from exc
        if message is None:
            raise RuntimeError("native_session_closed")
        if "id" in message and "method" in message:
            if approved_request(message, self.app_targets, self.human_approved):
                self.send({"id": message["id"], "result": {"action": "accept", "content": {}}})
                self.event("human_app_approval_forwarded", app=message["params"]["message"])
            else:
                self.send({"id": message["id"], "result": {"action": "cancel", "content": None}})
                raise RuntimeError("unapproved_host_request")
        self.observer(message)
        return message

    def call(self, method: str, params: dict, timeout: float = 45) -> dict:
        self.next_id += 1
        request_id = self.next_id
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self.receive(max(.001, deadline - time.monotonic()))
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise RuntimeError("native_rpc_error")
                return message["result"]
        raise TimeoutError("native_rpc_timeout")

    def initialize(self):
        metadata = self.call("initialize", {
            "clientInfo": {"name": "agy_cua_native_benchmark", "version": "0.1"},
            "capabilities": {"experimentalApi": True},
        })
        self.send({"method": "initialized", "params": {}})
        account = self.call("account/read", {"refreshToken": False}).get("account") or {}
        if account.get("type") != "chatgpt":
            raise RuntimeError("subscription_only_no_api_key_admission")
        limits = self.call("account/rateLimits/read", {}).get("rateLimits", {})
        used = (limits.get("primary") or {}).get("usedPercent")
        if type(used) not in (int, float) or not 0 <= used < 100:
            raise RuntimeError("included_subscription_quota_unavailable_or_exhausted")
        self.event("subscription_admission", included_quota_used_percent=used,
                   marginal_dollar_cost=None, paid_api_requests=0)
        return {k: metadata.get(k) for k in ("userAgent", "platformFamily", "platformOs")}

    def start_thread(self, model: str, tier: str, solver_dir: Path):
        catalog = self.call("model/list", {"includeHidden": False})
        candidate = next((m for m in catalog["data"] if m["id"] == model), None)
        if not candidate or tier not in {t["id"] for t in candidate.get("serviceTiers") or []}:
            raise RuntimeError("requested_model_tier_not_available")
        thread = self.call("thread/start", {
            "model": model, "serviceTier": tier, "ephemeral": True,
            "allowProviderModelFallback": False, "cwd": str(solver_dir),
            "sandbox": "read-only", "approvalPolicy": "on-request",
            "config": {"model_reasoning_effort": "low", "features.shell_tool": False},
        })
        if (thread.get("model"), thread.get("serviceTier"), thread.get("reasoningEffort")) != (model, tier, "low"):
            raise RuntimeError("native_thread_identity_mismatch")
        return thread


def peer_fixtures() -> list[int]:
    # Process names only: never inspect another worker's argv, environment or logs.
    output = subprocess.check_output(["ps", "-axo", "pid,comm"], text=True)
    return [int(line.split(maxsplit=1)[0]) for line in output.splitlines()[1:]
            if line.rstrip().endswith("/bench_fixture")]


def probe_fixture(session, thread_id: str, fixture: Path, window_id: int) -> dict:
    """Try documented native target forms without changing or rebuilding the app.

    The form-mode fixture contains no canary. Arbitrary UI/error text never leaves
    memory; only the expected resolution failures and inventory count are saved.
    """
    result = {"accessible": False, "attempts": []}
    code = 'var fixtureApp = await cua.getApp(' + json.dumps(str(fixture)) + ');'
    probes = [("path", code), ("window_id", 'var fixtureApp = await cua.getApp({windowId:' + str(window_id) + '});')]
    def native(code):
        return session.call("mcpServer/tool/call", {"threadId": thread_id, "server": "cua_repl", "tool": "js",
            "arguments": {"code": code, "title": "Check original benchmark fixture native access"}})
    for kind, code in probes:
        receipt = native(code)
        texts = [c.get("text", "") for c in receipt.get("content", []) if c.get("type") == "text"]
        invalid_path = any(t.startswith("Invalid app: " + str(fixture)) for t in texts)
        unsupported_window = any(t.startswith("macOS getApp requires an app name, path, or bundle ID.") for t in texts)
        error = "invalid_original_fixture_path" if invalid_path else (
            "macos_requires_app_identity" if unsupported_window else "native_tool_error"
        )
        failed = bool(receipt.get("isError"))
        result["attempts"].append({"target_kind": kind, "is_error": failed, "error": error if failed else None})
        if not failed:
            result.update(accessible=True, accepted_target_kind=kind)
            return result
    code = ('var inventory = await cua.listApps({emit:false}); nodeRepl.write(inventory.filter(a => '
            'a.windows?.some(w => w.id === ' + str(window_id) + ')).map(a => ({id:a.id})));')
    receipt = native(code)
    matches = None
    for content in receipt.get("content", []):
        if content.get("type") == "text":
            try:
                value = json.loads(content.get("text", ""))
            except ValueError:
                continue
            if isinstance(value, list):
                matches = value
    result["inventory_matches"] = len(matches) if matches is not None else None
    if matches and len(matches) == 1 and isinstance(matches[0], dict) and isinstance(matches[0].get("id"), str):
        receipt = native('var fixtureApp = await cua.getApp(' + json.dumps(matches[0]["id"]) + ');')
        result["accessible"] = not bool(receipt.get("isError"))
        result["attempts"].append({"target_kind": "exact_window_inventory_app_id", "is_error": not result["accessible"]})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-native-apps", action="store_true", help="Forward Bobby's explicit Calculator/fixture grant")
    parser.add_argument("--max-budget-usd", type=float, default=5.0)
    args = parser.parse_args()
    if not args.approved_native_apps or not 0 < args.max_budget_usd <= 5:
        parser.error("requires explicit app grant and an issue-wide cap no greater than $5")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "runs/benchmark-codex-runs" / stamp
    out.mkdir(parents=True)
    private = out / "private"
    private.mkdir(mode=0o700)
    def event(phase, **values):
        now = datetime.now(timezone.utc).isoformat()
        with (out / "events.jsonl").open("a") as stream:
            stream.write(json.dumps({"at": now, "phase": phase, **values}) + "\n")
        (out / "heartbeat").write_text(now + "\n")
        (out / "STATE.md").write_text("Phase: " + phase + "\n" + json.dumps(values) + "\n")
    event("starting", human_app_grant="Allow these benchmark apps")
    (out / "PROOF.md").write_text("Native Codex admission only; zero model turns. Original form fixture, read-only native access.\n")
    harness = load_harness()
    payload = {"timestamp_utc": stamp, "run_status": "starting", "results": [],
               "arms": list(ARMS), "arm_configurations": {a: {"model": m, "service_tier": t, "reasoning_effort": "low"} for a, (m, t) in ARMS.items()},
               "max_budget_usd_total": args.max_budget_usd, "cost_usd": None,
               "model_turns_started": 0, "benchmark_tasks_started": 0, "paid_api_requests": 0,
               "method": "scripted_native_codex_admission_only", "unavailable_metrics": UNAVAILABLE,
               "base_sha": "154edf54e6223b4c61543d78206247535b3e2b89",
               "runner_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               "runner_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "fixture_source_sha256": hashlib.sha256(harness.FIXTURE_SRC.read_bytes()).hexdigest(),
               "peer_fixture_count_before_setup": len(peer_fixtures()),
               "cost_note": "No model request made; subscription marginal pricing unavailable, not zero."}
    driver = session = proc = None
    try:
        fixture = harness.ensure_fixture_binary()
        payload["fixture_sha256"] = hashlib.sha256(fixture.read_bytes()).hexdigest()
        binary = harness.jev_mod.find_driver(os.environ)
        if not binary:
            raise RuntimeError("setup_driver_unavailable")
        driver = harness.jev_mod.DriverClient(binary)
        tasks = harness.build_tasks()
        payload["task_ids"] = [t.task_id for t in tasks]
        task = next(t for t in tasks if t.task_id == "T3_appkit_form")
        pid, wid, proc, state, _ = harness.launch_target_for_task(task, driver, private, "native-admission")
        payload["initial_form_oracle_passed"] = task.verify_fn(driver, pid, wid, state, False)[0]
        png, _, _, _ = harness.get_window_screenshot_and_size(driver, pid, wid)
        (private / "original-form.png").write_bytes(png)
        payload["private_fixture_screenshot_sha256"] = hashlib.sha256(png).hexdigest()
        session = NativeSession({"Calculator", "bench_fixture", str(fixture)}, True, event)
        payload["runtime"] = session.initialize()
        thread = session.start_thread(*ARMS["codex-cu-sol-fast"], out)
        payload["thread_configuration"] = {k: thread.get(k) for k in ("model", "modelProvider", "serviceTier", "reasoningEffort")}
        # Recheck the second arm without generating a response or reusing solver context.
        astra = session.start_thread(*ARMS["codex-cu-astra-ultrafast"], out)
        payload["astra_thread_configuration"] = {k: astra.get(k) for k in ("model", "modelProvider", "serviceTier", "reasoningEffort")}
        fixture_access = probe_fixture(session, thread["thread"]["id"], fixture, wid)
        payload["fixture_native_access"] = fixture_access
        payload["run_status"] = "apps_accessible_measurements_not_started" if fixture_access["accessible"] else "blocked"
        payload["blocker"] = None if fixture_access["accessible"] else "original_unbundled_fixture_not_addressable_by_native_plugin"
    except Exception as exc:
        reason = str(exc) if isinstance(exc, (RuntimeError, TimeoutError)) else type(exc).__name__
        payload.update(run_status="blocked", blocker=reason if re.fullmatch(r"[a-z_]+", reason) else type(exc).__name__)
    finally:
        if session:
            session.close()
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        if driver:
            driver.close()
        harness.write_benchmark_summary(out, payload)
        event(payload["run_status"], blocker=payload.get("blocker"))
    print(json.dumps({"run_dir": str(out), "status": payload["run_status"], "blocker": payload.get("blocker")}))
    return 2 if payload["run_status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())


