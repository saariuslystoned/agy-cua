#!/usr/bin/env python3
"""Read-only Codex native-plugin admission probe; never starts a model turn.

Uses the installed app-server protocol, not Cua Driver or the Responses API.
Writes only selected metadata and a classified result, never app state/images,
credentials, raw transport messages, or stderr. Exit 2 means admission blocked.
"""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class Probe:
    def __init__(self) -> None:
        self.process = subprocess.Popen(
            ["codex", "app-server"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        self.messages: queue.Queue = queue.Queue()
        self.next_id = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        try:
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
        except (ValueError, OSError):
            pass
        finally:
            self.messages.put(None)

    def send(self, message: dict) -> None:
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def call(self, method: str, params: dict, timeout: float = 25) -> dict:
        self.next_id += 1
        request_id = self.next_id
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = self.messages.get(timeout=max(0.001, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise TimeoutError("app_server_timeout") from exc
            if message is None:
                raise RuntimeError("app_server_closed")
            if "id" in message and "method" in message:
                # Never synthesize approval or implement the desktop attestation host.
                raise RuntimeError("host_request_requires_human_or_supported_client")
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError("app_server_rpc_error")
                return message["result"]
        raise TimeoutError("app_server_timeout")

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()  # Only this probe's direct child.
            self.process.wait(timeout=5)
        self.reader.join(timeout=1)


def run() -> dict:
    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "probe_kind": "metadata_and_read_only_plugin_call",
        "model_turns_started": 0,
        "benchmark_tasks_started": 0,
        "requested_labels": ["6.1 sol fast", "6.1 sol astra ultrafast"],
        "status": "blocked",
        "cost_usd": None,
        "cost_note": "No model turn requested; subscription marginal billing unavailable.",
    }
    probe = None
    try:
        probe = Probe()
        init = probe.call("initialize", {
            "clientInfo": {"name": "agy_cua_native_probe", "version": "0.1"},
            "capabilities": {"experimentalApi": True},
        })
        result["runtime"] = {k: init.get(k) for k in ("userAgent", "platformFamily", "platformOs")}
        probe.send({"method": "initialized", "params": {}})
        account = probe.call("account/read", {"refreshToken": False}).get("account") or {}
        result["account_type"] = account.get("type")
        models = probe.call("model/list", {"includeHidden": False})
        result["models"] = [
            {k: m.get(k) for k in (
                "id", "model", "supportedReasoningEfforts", "defaultReasoningEffort",
                "serviceTiers", "defaultServiceTier",
            )}
            for m in models["data"] if m["id"] in ("gpt-6.1-sol", "gpt-6-astra")
        ]
        statuses = probe.call("mcpServerStatus/list", {
            "serverName": "cua_repl", "detail": "toolsAndAuthOnly",
        })
        result["native_servers"] = [
            {"name": s["name"], "tools": sorted(s.get("tools", {}))}
            for s in statuses["data"]
        ]
        sol = next((m for m in result["models"] if m["id"] == "gpt-6.1-sol"), None)
        if not sol or not any(t["id"] == "priority" for t in sol["serviceTiers"] or []):
            result["blocker"] = "sol_fast_not_in_live_catalog"
            return result
        if not any(s["name"] == "cua_repl" and "js" in s["tools"] for s in result["native_servers"]):
            result["blocker"] = "native_plugin_not_in_inventory"
            return result
        thread = probe.call("thread/start", {
            "model": "gpt-6.1-sol", "serviceTier": "priority",
            "config": {"model_reasoning_effort": "low"},
            "allowProviderModelFallback": False, "ephemeral": True,
            "cwd": str(Path.cwd()), "sandbox": "read-only", "approvalPolicy": "on-request",
        })
        result["thread_configuration"] = {k: thread.get(k) for k in (
            "model", "modelProvider", "serviceTier", "reasoningEffort",
        )}
        if (thread.get("model"), thread.get("serviceTier"), thread.get("reasoningEffort")) != (
            "gpt-6.1-sol", "priority", "low",
        ):
            result["blocker"] = "thread_configuration_mismatch"
            return result
        receipt = probe.call("mcpServer/tool/call", {
            "threadId": thread["thread"]["id"], "server": "cua_repl", "tool": "js",
            "arguments": {
                "code": 'let app = await cua.getApp("Calculator");',
                "title": "Probe native Calculator access",
            },
        })
        # Classify in memory; never persist arbitrary UI text or screenshot bytes.
        denied = any(
            c.get("type") == "text" and "Computer Use was not approved to use Calculator" in c.get("text", "")
            for c in receipt.get("content", [])
        )
        result["plugin_is_error"] = receipt.get("isError", False)
        result["blocker"] = "native_calculator_not_approved" if denied else (
            "native_plugin_call_failed" if receipt.get("isError") else None
        )
        if result["blocker"] is None:
            result["status"] = "plugin_accessible_measurement_not_admitted"
        return result
    except (RuntimeError, TimeoutError, queue.Empty, OSError, KeyError, ValueError) as exc:
        result["blocker"] = str(exc) if isinstance(exc, (RuntimeError, TimeoutError)) else type(exc).__name__
        return result
    finally:
        if probe is not None:
            probe.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New ignored local JSON evidence path")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "runs" / "benchmark-codex-runs"
    output = args.output.resolve()
    if not output.is_relative_to(root.resolve()) or output.exists():
        parser.error("output must be a new file under runs/benchmark-codex-runs/")
    result = run()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "blocker": result.get("blocker")}))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
