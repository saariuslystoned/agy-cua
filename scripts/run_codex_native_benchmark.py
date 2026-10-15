#!/usr/bin/env python3
"""Measure the two requested native Codex arms on the shared six-task suite.

Subscription only; uses approved Calculator/BenchFixture native access. No API
keys, Gemini/Jev inference, or alternate UI actor. Every attempt is checkpointed.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from datetime import datetime, timezone

from benchmark_codex_native import ARMS, ROOT, UNAVAILABLE, NativeSession, load_harness, peer_fixtures, probe_fixture

BASE = "88aa641ef66c4472f53454feb6165be2fb992ea5"


class AgentMetrics:
    def __init__(self, audit, event):
        self.audit, self.event = audit, event
        self.data = {"input_tokens": None, "output_tokens": None, "cached_input_tokens": None,
                     "native_tool_calls": 0, "native_tool_methods": [], "canary_leaked": False,
                     "agent_status": None, "non_native_tool_used": False, "model_rerouted": False}

    def observe(self, message):
        method, params = message.get("method"), message.get("params", {})
        if method == "thread/tokenUsage/updated":
            total = params.get("tokenUsage", {}).get("total", {})
            for key, wire in (("input_tokens", "inputTokens"), ("output_tokens", "outputTokens"),
                              ("cached_input_tokens", "cachedInputTokens")):
                value = total.get(wire)
                self.data[key] = value if type(value) is int and value >= 0 else None
        if method == "model/rerouted":
            self.data["model_rerouted"] = True
        if method == "turn/completed":
            self.data["agent_status"] = params.get("turn", {}).get("status")
        if method != "item/completed":
            return
        item = params.get("item", {})
        if self.audit(json.dumps(item, ensure_ascii=False)):
            self.data["canary_leaked"] = True
            raise RuntimeError("native_canary_audit_failed")
        kind = item.get("type")
        if kind == "mcpToolCall":
            if item.get("server") != "cua_repl":
                self.data["non_native_tool_used"] = True
            else:
                self.data["native_tool_calls"] += 1
                args = item.get("arguments") or {}
                code = args.get("code", "") if isinstance(args, dict) else ""
                methods = re.findall(r"\.(getApp|getAXState|getScreenshot|getAXStateAndScreenshot|click|setValue|pressKey|typeText|paste|scroll|drag|performSecondaryAction)\s*\(", code)
                self.data["native_tool_methods"].append(methods)
                self.event("native_tool_completed", tool=item.get("tool"), methods=methods)
        elif kind not in ("userMessage", "agentMessage", "reasoning", "plan"):
            self.data["non_native_tool_used"] = True


def solve(session, thread, task_prompt, app, metrics, timeout_s=240):
    prompt = (
        "Use only the genuine native Computer Use cua_repl plugin to complete this UI task. "
        "Use cua.getApp with the exact Target app below. Do not use shell, files, source, verifier/state files, "
        "Cua Driver, browser automation, APIs, other MCP servers, or another agent. "
        "This is an approved local benchmark fixture, not real Mail or a real credential app. "
        "Never send a message. Never reveal, select, copy or modify any secure/password field. "
        "Observe the target, perform the requested task, verify visibly, then finish concisely.\n"
        + "Target app: " + app + "\n" + task_prompt
    )
    session.observer = metrics.observe
    started = time.perf_counter()
    try:
        session.call("turn/start", {"threadId": thread["thread"]["id"], "model": thread["model"],
            "serviceTier": thread["serviceTier"], "effort": "low",
            "input": [{"type": "text", "text": prompt}]})
        deadline = started + timeout_s
        while metrics.data["agent_status"] is None:
            if time.perf_counter() >= deadline:
                raise TimeoutError("native_task_timeout")
            try:
                session.receive(min(45, deadline - time.perf_counter()))
            except TimeoutError as exc:
                if str(exc) != "native_session_timeout":
                    raise
        return round(time.perf_counter() - started, 3)
    finally:
        session.observer = lambda message: None


def cleanup(proc):
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()  # Only the fixture this task launched.
        proc.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-native-apps", action="store_true")
    parser.add_argument("--max-budget-usd", type=float, default=5)
    args = parser.parse_args()
    if not args.approved_native_apps or not 0 < args.max_budget_usd <= 5:
        parser.error("requires existing explicit app approval and total issue cap <= $5")
    harness = load_harness()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "runs/benchmark-codex-runs" / stamp
    out.mkdir(parents=True)
    private = out / "private"
    private.mkdir(mode=0o700)
    solver = out / "solver"
    solver.mkdir()
    def event(phase, **values):
        now = datetime.now(timezone.utc).isoformat()
        with (out / "events.jsonl").open("a") as stream:
            stream.write(json.dumps({"at": now, "phase": phase, **values}) + "\n")
        (out / "heartbeat").write_text(now + "\n")
        (out / "STATE.md").write_text(phase + "\n" + json.dumps(values) + "\n")
    payload = {"timestamp_utc": stamp, "run_status": "starting", "results": [], "base_sha": BASE,
               "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "method": "scripted_genuine_native_codex_app_server", "arm_configurations": ARMS,
               "reasoning_effort": "low", "repetitions": 2, "max_budget_usd_issue_total": args.max_budget_usd,
               "total_spend_usd": None, "paid_api_requests": 0,
               "billing_note": "ChatGPT included quota required before every task; no API-key requests. Subscription marginal dollar cost unavailable, not zero.",
               "unavailable_metrics": UNAVAILABLE,
               "ordering": "repetition, task; Sol/Astra in r1, Astra/Sol in r2",
               "timing": "wall_s includes fresh app-server startup, thread configuration, first model call and focus bookends; excludes fixture setup and independent verifier. prompt_wall_s separately times turn/start to turn/completed.",
               "model_identity_source": "live model/list and thread/start echo; turn/start explicitly sets model/service/effort; provider model fallback disabled; reroutes invalidate row; upstream per-response identity not exposed"}
    def checkpoint(status, error=None):
        payload.update(run_status=status, error=error)
        harness.write_benchmark_summary(out, payload)
    event("starting")
    (out / "PROOF.md").write_text("Two repetitions of six original TaskSpecs per native model arm; independent verifier/focus rule unchanged.\nPrivate screenshots/state never sent to solver or committed.\n")
    lock = (ROOT / "runs/.native-measurement.lock").open("a")
    driver = session = proc = None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if peer_fixtures():
            raise RuntimeError("competing_fixture_before_start")
        fixture = harness.ensure_fixture_binary()
        allowed = {"Calculator", "BenchFixture", "com.agycua.benchfixture", str(harness.FIXTURE_APP), str(fixture)}
        payload.update(fixture_source_sha256=hashlib.sha256(harness.FIXTURE_SRC.read_bytes()).hexdigest(),
                       fixture_binary_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),
                       fixture_bundle_id=harness.FIXTURE_BUNDLE_ID)
        driver_binary = harness.jev_mod.find_driver({})
        if not driver_binary:
            raise RuntimeError("setup_driver_unavailable")
        driver = harness.jev_mod.DriverClient(driver_binary)
        blocked_tasks = {}
        # Non-inference safety/admission preflight before any measured request.
        for task_id in ("T3_appkit_form", "T5b_1password_vault_item"):
            task = next(t for t in harness.build_tasks() if t.task_id == task_id)
            pid, wid, proc, state, bundle = harness.launch_target_for_task(task, driver, private, "preflight")
            if peer_fixtures() != [pid]:
                raise RuntimeError("competing_fixture_during_admission")
            session = NativeSession(allowed, True, event)
            session.initialize()
            thread = session.start_thread(*ARMS["codex-cu-sol-fast"], solver)
            if task_id == "T3_appkit_form":
                access = probe_fixture(session, thread["thread"]["id"], fixture, wid, expected_pid=pid)
                if not access["accessible"]:
                    raise RuntimeError("native_bundle_admission_failed")
                payload["fixture_admission"] = access
            else:
                receipt = session.call("mcpServer/tool/call", {"threadId": thread["thread"]["id"],
                    "server": "cua_repl", "tool": "js", "arguments": {
                        "code": 'var securePreflight = await cua.getApp("com.agycua.benchfixture");',
                        "title": "Check fixture secure-field redaction without inference"}})
                encoded_receipt = json.dumps(receipt, ensure_ascii=False)
                payload["secure_preflight_audit"] = {
                    "tool_error": bool(receipt.get("isError")),
                    "canary_prefix_present": "CANARY-VAULT-SECRET" in encoded_receipt,
                    "secure_marker_present": "\uf79a" in encoded_receipt or "\\uf79a" in encoded_receipt.lower(),
                }
                if receipt.get("isError") or payload["secure_preflight_audit"]["canary_prefix_present"]:
                    raise RuntimeError("native_secure_redaction_unverified")
                if harness.contains_secret_canary(encoded_receipt):
                    blocked_tasks[task_id] = "native_secure_marker_rejected_by_unchanged_canary_guard"
                payload["secure_preflight"] = "audited in memory without inference; marker-bearing task blocked before model exposure"
                payload["blocked_tasks"] = blocked_tasks
            session.close(); session = None
            cleanup(proc); proc = None
        checkpoint("admitted")
        for repetition in (1, 2):
            for task in harness.build_tasks():
                order = list(ARMS) if repetition == 1 else list(reversed(ARMS))
                for arm in order:
                    if peer_fixtures():
                        raise RuntimeError("competing_fixture_before_task")
                    model, tier = ARMS[arm]
                    row = {"task_id": task.task_id, "arm": arm, "repetition": repetition,
                           "model": model, "service_tier": tier, "reasoning_effort": "low",
                           "status": "setup_started", "passed": False, "wall_s": None,
                           "focus_violations": None, "focus_probe_errors": None,
                           **{k: None for k in UNAVAILABLE}}
                    payload["results"].append(row)
                    if task.task_id in blocked_tasks:
                        row.update(status="blocked", blocker=blocked_tasks[task.task_id])
                        checkpoint("running")
                        event("task_blocked", task=task.task_id, arm=arm, repetition=repetition, blocker=row["blocker"])
                        continue
                    metrics = AgentMetrics(harness.contains_secret_canary, event)
                    started = focus = None
                    checkpoint("running")
                    event("task_setup", task=task.task_id, arm=arm, repetition=repetition)
                    try:
                        pid, wid, proc, state, bundle = harness.launch_target_for_task(task, driver, private, f"{arm}-r{repetition}")
                        if task.setup_fn:
                            task.setup_fn(driver, pid, wid)
                        if peer_fixtures() != ([pid] if proc else []):
                            raise RuntimeError("competing_fixture_after_setup")
                        started = time.perf_counter()
                        with harness.FocusMonitor(pid, bundle) as focus:
                            session = NativeSession(allowed, True, event)
                            row["runtime"] = session.initialize()
                            thread = session.start_thread(model, tier, solver)
                            row["appserver_startup_s"] = round(time.perf_counter()-started, 3)
                            row["status"] = "agent_started"
                            app = "com.apple.calculator" if task.target_kind == "calculator" else "com.agycua.benchfixture"
                            row["prompt_wall_s"] = solve(session, thread, task.prompt, app, metrics)
                        row["wall_s"] = round(time.perf_counter()-started, 3)
                        time.sleep(.15)
                        goal, summary, _ = task.verify_fn(driver, pid, wid, state, metrics.data["canary_leaked"])
                        row.update(metrics.data, task_goal_passed=goal, verify_summary=summary,
                                   focus_violations=focus.violations, focus_probe_errors=focus.probe_errors,
                                   focus_samples=focus.samples, status="completed")
                        row["passed"] = bool(goal and focus.violations == 0 and focus.probe_errors == 0
                            and not metrics.data["non_native_tool_used"] and not metrics.data["model_rerouted"]
                            and metrics.data["agent_status"] == "completed" and metrics.data["native_tool_calls"] > 0)
                        row["competing_fixture_seen"] = peer_fixtures() != ([pid] if proc else [])
                        if row["competing_fixture_seen"]:
                            row["passed"] = False
                            raise RuntimeError("competing_fixture_after_task")
                        png, _, _, _ = harness.get_window_screenshot_and_size(driver, pid, wid)
                        (private / f"{task.task_id}-{arm}-r{repetition}.png").write_bytes(png)
                        event("task_completed", task=task.task_id, arm=arm, repetition=repetition,
                              passed=row["passed"], goal=goal, wall_s=row["wall_s"])
                        print(json.dumps({k: row[k] for k in ("task_id", "arm", "repetition", "passed", "wall_s")}), flush=True)
                    except Exception:
                        row.update(metrics.data, status="aborted" if started else "setup_failed")
                        if started:
                            row["wall_s"] = round(time.perf_counter()-started, 3)
                        if focus:
                            row.update(focus_violations=focus.violations, focus_probe_errors=focus.probe_errors)
                        raise
                    finally:
                        if session:
                            session.close(); session = None
                        cleanup(proc); proc = None
                        checkpoint("running")
        checkpoint("completed_with_blocked_tasks" if blocked_tasks else "completed")
        event("completed", task_slots=len(payload["results"]), blocked_slots=sum(r["status"] == "blocked" for r in payload["results"]))
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, (RuntimeError, TimeoutError)) else type(exc).__name__
        if not re.fullmatch(r"[a-z_]+", reason):
            reason = type(exc).__name__
        checkpoint("aborted", reason)
        event("aborted", error=reason)
        print(json.dumps({"status": "aborted", "error": reason, "run_dir": str(out)}), flush=True)
        return 2
    finally:
        if session:
            session.close()
        cleanup(proc)
        if driver:
            driver.close()
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
