"""Native runner admission tests without paid requests or desktop input."""
import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location("benchmark_codex_native", SCRIPTS / "benchmark_codex_native.py")
runner = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC.loader.exec_module(runner)
finally:
    sys.path.remove(str(SCRIPTS))


class ApprovalTests(unittest.TestCase):
    def request(self, app="Calculator"):
        return {"id": 1, "method": "mcpServer/elicitation/request", "params": {
            "serverName": "cua_repl", "mode": "form",
            "message": f'Allow Computer Use to use "{app}"?',
            "requestedSchema": {"type": "object", "properties": {}},
        }}

    def test_explicit_human_grant_for_exact_app(self):
        self.assertTrue(runner.approved_request(self.request(), {"Calculator"}, True))

    def test_unapproved_app_and_missing_human_grant(self):
        self.assertFalse(runner.approved_request(self.request("Mail"), {"Calculator"}, True))
        self.assertFalse(runner.approved_request(self.request(), {"Calculator"}, False))

    def test_different_server_or_expanded_schema_is_rejected(self):
        request = self.request()
        request["params"]["serverName"] = "other"
        self.assertFalse(runner.approved_request(request, {"Calculator"}, True))
        request = self.request()
        request["params"]["requestedSchema"]["properties"] = {"arbitrary": {"type": "string"}}
        self.assertFalse(runner.approved_request(request, {"Calculator"}, True))

    def test_catalog_missing_tier_never_starts_thread(self):
        session = object.__new__(runner.NativeSession)
        calls = []
        def call(method, params):
            calls.append(method)
            return {"data": [{"id": "gpt-6-astra", "serviceTiers": [{"id": "priority"}]}]}
        session.call = call
        with self.assertRaisesRegex(RuntimeError, "requested_model_tier_not_available"):
            session.start_thread("gpt-6-astra", "ultrafast", Path("/unused"))
        self.assertEqual(calls, ["model/list"])

    def test_thread_model_substitution_rejected(self):
        session = object.__new__(runner.NativeSession)
        def call(method, params):
            if method == "model/list":
                return {"data": [{"id": "gpt-6-astra", "serviceTiers": [{"id": "ultrafast"}]}]}
            return {"model": "gpt-6.1-sol", "serviceTier": "ultrafast", "reasoningEffort": "low"}
        session.call = call
        with self.assertRaisesRegex(RuntimeError, "native_thread_identity_mismatch"):
            session.start_thread("gpt-6-astra", "ultrafast", Path("/unused"))

    def test_api_auth_is_rejected_before_model_request(self):
        session = object.__new__(runner.NativeSession)
        calls = []
        def call(method, params):
            calls.append(method)
            return {"account": {"type": "apiKey"}} if method == "account/read" else {}
        session.call = call
        session.send = lambda message: None
        with self.assertRaisesRegex(RuntimeError, "subscription_only_no_api_key_admission"):
            session.initialize()
        self.assertEqual(calls, ["initialize", "account/read"])

    def test_exhausted_subscription_is_rejected(self):
        session = object.__new__(runner.NativeSession)
        session.send = lambda message: None
        session.event = lambda *args, **values: None
        def call(method, params):
            if method == "account/read":
                return {"account": {"type": "chatgpt"}}
            if method == "account/rateLimits/read":
                return {"rateLimits": {"primary": {"usedPercent": 100}}}
            return {}
        session.call = call
        with self.assertRaisesRegex(RuntimeError, "included_subscription_quota_unavailable_or_exhausted"):
            session.initialize()


class FixtureAdmissionTests(unittest.TestCase):
    def session(self, receipts):
        class Session:
            def __init__(self):
                self.calls = []
            def call(self, method, params):
                self.calls.append((method, params))
                return receipts[len(self.calls)-1]
        return Session()

    def test_path_success_does_not_persist_ui_or_try_other_targets(self):
        session = self.session([{"content": [{"type": "text", "text": 'Window: "AGYCuaBench-form" PRIVATE_UI_SENTINEL'}]}])
        result = runner.probe_fixture(session, "thread", Path("/fixture"), 42)
        self.assertTrue(result["accessible"])
        self.assertEqual(len(session.calls), 1)
        self.assertNotIn("PRIVATE_UI_SENTINEL", str(result))

    def test_original_macos_failures_and_empty_inventory_block(self):
        session = self.session([
            {"isError": True, "content": [{"type": "text", "text": "Invalid app: /fixture"}]},
            {"isError": True, "content": [{"type": "text", "text": "macOS getApp requires an app name, path, or bundle ID."}]},
            {"isError": True, "content": [{"type": "text", "text": "Invalid app: bench_fixture"}]},
            {"content": [{"type": "text", "text": "[]"}]},
        ])
        result = runner.probe_fixture(session, "thread", Path("/fixture"), 42)
        self.assertFalse(result["accessible"])
        self.assertEqual(result["inventory_matches"], 0)
        self.assertEqual(result["attempts"][0]["error"], "invalid_original_fixture_path")
        self.assertEqual(result["attempts"][1]["error"], "macos_requires_app_identity")
        self.assertEqual(result["attempts"][2]["error"], "invalid_original_fixture_name")

    def test_error_text_is_not_saved_and_malformed_inventory_cannot_admit(self):
        session = self.session([
            {"isError": True, "content": [{"type": "text", "text": "PRIVATE_ERROR"}]},
            {"isError": True, "content": []},
            {"isError": True, "content": []},
            {"isError": True, "content": [{"type": "text", "text": "not JSON"}]},
        ])
        result = runner.probe_fixture(session, "thread", Path("/fixture"), 42)
        self.assertFalse(result["accessible"])
        self.assertIsNone(result["inventory_matches"])
        self.assertNotIn("PRIVATE_ERROR", str(result))

    def test_exact_inventory_app_id_must_itself_work(self):
        for failed in (False, True):
            session = self.session([
                {"isError": True}, {"isError": True}, {"isError": True},
                {"content": [{"type": "text", "text": '[{"id":"fixture-only"}]'}]},
                {"isError": failed, "content": [{"type": "text", "text": 'Window: "AGYCuaBench-form"'}]},
            ])
            result = runner.probe_fixture(session, "thread", Path("/fixture"), 42)
            self.assertEqual(result["accessible"], not failed)
            self.assertIn('cua.getApp("fixture-only")', session.calls[-1][1]["arguments"]["code"])

    def test_empty_success_receipt_is_not_fixture_access(self):
        session = self.session([{}, {}, {}, {"content": [{"type": "text", "text": "[]"}]}])
        result = runner.probe_fixture(session, "thread", Path("/fixture"), 42)
        self.assertFalse(result["accessible"])
        self.assertEqual(result["attempts"][0]["error"], "missing_fixture_ui_evidence")


if __name__ == "__main__":
    unittest.main()
