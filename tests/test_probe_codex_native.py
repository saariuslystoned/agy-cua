"""Admission and privacy checks for the native-plugin probe (no model calls)."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "probe_codex_native", Path(__file__).resolve().parents[1] / "scripts/probe_codex_native.py",
)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class FakeProbe:
    def __init__(self, receipt=None, tier="priority", native=True, thread_model="gpt-6.1-sol"):
        self.receipt = receipt or {"content": [{"type": "text", "text": "PRIVATE_UI_SENTINEL"}]}
        self.tier, self.native, self.thread_model = tier, native, thread_model
        self.methods = []
        self.closed = False

    def send(self, message):
        pass

    def call(self, method, params):
        self.methods.append(method)
        return {
            "initialize": {"userAgent": "test", "platformOs": "macos", "codexHome": "PRIVATE_PATH"},
            "account/read": {"account": {"type": "chatgpt", "email": "PRIVATE_ACCOUNT"}},
            "model/list": {"data": [{"id": "gpt-6.1-sol", "serviceTiers": [{"id": self.tier}]}]},
            "mcpServerStatus/list": {"data": [{"name": "cua_repl", "tools": {"js": {}}}] if self.native else []},
            "thread/start": {"thread": {"id": "test"}, "model": self.thread_model,
                             "serviceTier": "priority", "reasoningEffort": "low"},
            "mcpServer/tool/call": self.receipt,
        }[method]

    def close(self):
        self.closed = True


class AdmissionTests(unittest.TestCase):
    def test_silent_server_is_classified_as_timeout(self):
        client = object.__new__(probe.Probe)
        client.next_id = 0
        client.messages = probe.queue.Queue()
        client.send = lambda message: None
        with self.assertRaisesRegex(TimeoutError, "^app_server_timeout$"):
            client.call("model/list", {}, timeout=0.001)

    def execute(self, fake):
        with patch.object(probe, "Probe", return_value=fake):
            result = probe.run()
        self.assertTrue(fake.closed)
        self.assertNotIn("turn/start", fake.methods)
        return result

    def test_positive_access_does_not_admit_measurement_or_persist_ui(self):
        result = self.execute(FakeProbe())
        self.assertEqual(result["status"], "plugin_accessible_measurement_not_admitted")
        self.assertIsNone(result["cost_usd"])
        encoded = json.dumps(result)
        for private in ("PRIVATE_UI_SENTINEL", "PRIVATE_ACCOUNT", "PRIVATE_PATH"):
            self.assertNotIn(private, encoded)

    def test_denied_plugin_is_blocked(self):
        result = self.execute(FakeProbe(receipt={"isError": True, "content": [
            {"type": "text", "text": "Computer Use was not approved to use Calculator"},
        ]}))
        self.assertEqual(result["blocker"], "native_calculator_not_approved")
        self.assertEqual(result["status"], "blocked")

    def test_other_plugin_error_never_counts_as_access(self):
        result = self.execute(FakeProbe(receipt={"isError": True, "content": [
            {"type": "text", "text": "PRIVATE_ERROR"},
        ]}))
        self.assertEqual(result["blocker"], "native_plugin_call_failed")
        self.assertNotIn("PRIVATE_ERROR", json.dumps(result))

    def test_missing_speed_stops_before_thread(self):
        fake = FakeProbe(tier="default")
        self.assertEqual(self.execute(fake)["blocker"], "sol_fast_not_in_live_catalog")
        self.assertNotIn("thread/start", fake.methods)

    def test_missing_native_plugin_stops_before_thread(self):
        fake = FakeProbe(native=False)
        self.assertEqual(self.execute(fake)["blocker"], "native_plugin_not_in_inventory")
        self.assertNotIn("thread/start", fake.methods)

    def test_substituted_model_stops_before_app_access(self):
        fake = FakeProbe(thread_model="other")
        self.assertEqual(self.execute(fake)["blocker"], "thread_configuration_mismatch")
        self.assertNotIn("mcpServer/tool/call", fake.methods)

    def test_rpc_error_closes_process(self):
        fake = FakeProbe()
        fake.call = lambda *args: (_ for _ in ()).throw(RuntimeError("app_server_rpc_error"))
        self.assertEqual(self.execute(fake)["blocker"], "app_server_rpc_error")


if __name__ == "__main__":
    unittest.main()
