import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location("run_codex_native_benchmark", SCRIPTS / "run_codex_native_benchmark.py")
runner = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC.loader.exec_module(runner)
finally:
    sys.path.remove(str(SCRIPTS))


class MetricsTests(unittest.TestCase):
    def test_usage_uses_reported_cumulative_counts_and_preserves_missing(self):
        metrics = runner.AgentMetrics(lambda text: False, lambda *args, **kwargs: None)
        self.assertIsNone(metrics.data["input_tokens"])
        metrics.observe({"method": "thread/tokenUsage/updated", "params": {"tokenUsage": {"total": {
            "inputTokens": 1234, "outputTokens": 45, "cachedInputTokens": 600}}}})
        self.assertEqual(metrics.data["input_tokens"], 1234)
        self.assertEqual(metrics.data["output_tokens"], 45)
        metrics.observe({"method": "thread/tokenUsage/updated", "params": {"tokenUsage": {"total": {"inputTokens": True}}}})
        self.assertIsNone(metrics.data["input_tokens"])
        self.assertIsNone(metrics.data["output_tokens"])

    def test_native_rpc_count_does_not_pretend_to_count_actions(self):
        events = []
        metrics = runner.AgentMetrics(lambda text: False, lambda phase, **data: events.append(data))
        metrics.observe({"method": "item/completed", "params": {"item": {
            "type": "mcpToolCall", "server": "cua_repl", "tool": "js", "arguments": {
                "code": 'await app.click(1); await app.click(2); await app.getAXState(); // PRIVATE_VALUE'}}}})
        self.assertEqual(metrics.data["native_tool_calls"], 1)
        self.assertEqual(metrics.data["native_tool_methods"], [["click", "click", "getAXState"]])
        self.assertNotIn("PRIVATE_VALUE", str(events))
        self.assertNotIn("actions_dispatched", metrics.data)

    def test_canary_audit_halts_without_saving_payload(self):
        events = []
        metrics = runner.AgentMetrics(lambda text: "DO_NOT_PERSIST" in text,
                                      lambda *args, **kwargs: events.append(kwargs))
        with self.assertRaisesRegex(RuntimeError, "native_canary_audit_failed"):
            metrics.observe({"method": "item/completed", "params": {"item": {"text": "DO_NOT_PERSIST"}}})
        self.assertTrue(metrics.data["canary_leaked"])
        self.assertNotIn("DO_NOT_PERSIST", str(events))

    def test_other_actors_and_reroutes_invalidate_identity(self):
        metrics = runner.AgentMetrics(lambda text: False, lambda *args, **kwargs: None)
        metrics.observe({"method": "item/completed", "params": {"item": {"type": "commandExecution"}}})
        metrics.observe({"method": "model/rerouted", "params": {}})
        self.assertTrue(metrics.data["non_native_tool_used"])
        self.assertTrue(metrics.data["model_rerouted"])


if __name__ == "__main__":
    unittest.main()
