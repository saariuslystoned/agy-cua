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

    def test_same_title_other_worker_cannot_admit_or_persist_ui(self):
        ui = {"content": [{"type": "text", "text": 'Window: "AGYCuaBench-form" PRIVATE_UI_SENTINEL'}]}
        session = self.session([ui, {"isError": True}, ui,
            {"content": [{"type": "text", "text": '[{"id":"other-worker","window_ids":[43]}]'}]}])
        result = runner.probe_fixture(session, "thread", Path("/fixture"), 42)
        self.assertFalse(result["accessible"])
        self.assertFalse(result["window_identity_verified"])
        self.assertEqual(len(session.calls), 4)
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

    def test_bound_window_requires_matching_native_inventory(self):
        for native_id in (42, 43):
            session = self.session([
                {"isError": True},
                {"content": [{"type": "text", "text": 'Window: "AGYCuaBench-form"'}]},
                {"isError": True},
                {"content": [{"type": "text", "text": '[{"id":"fixture-only","window_ids":[' + str(native_id) + ']}]'}]},
            ])
            result = runner.probe_fixture(session, "thread", Path("/fixture"), 42)
            self.assertEqual(result["accessible"], native_id == 42)
            self.assertEqual(result["window_identity_verified"], native_id == 42)

    def test_inventory_error_cannot_supply_identity(self):
        ui = {"content": [{"type": "text", "text": 'Window: "AGYCuaBench-form"'}]}
        session = self.session([ui, ui, ui, {"isError": True, "content": [
            {"type": "text", "text": '[{"id":"fixture-only","window_ids":[42]}]'}]}])
        result = runner.probe_fixture(session, "thread", Path("/fixture"), 42)
        self.assertFalse(result["accessible"])
        self.assertIsNone(result["inventory_matches"])

    def test_empty_success_receipt_is_not_fixture_access(self):
        session = self.session([{}, {}, {}, {"content": [{"type": "text", "text": "[]"}]}])
        result = runner.probe_fixture(session, "thread", Path("/fixture"), 42)
        self.assertFalse(result["accessible"])
        self.assertEqual(result["attempts"][0]["error"], "missing_fixture_ui_evidence")

    def test_fixture_bundle_path_extracts_app_bundle_root(self):
        self.assertIsNone(runner.fixture_bundle_path(Path("/fixture")))
        self.assertEqual(
            runner.fixture_bundle_path(Path("/tmp/BenchFixture.app/Contents/MacOS/bench_fixture")),
            Path("/tmp/BenchFixture.app"),
        )

    def test_bundled_macos_fixture_admits_only_when_single_instance_verified(self):
        ui = {"content": [{"type": "text", "text": 'Window: "AGYCuaBench-form", App: BenchFixture.'}]}
        inv = {"content": [{"type": "text", "text": '[{"id":"com.agycua.benchfixture","is_running":true,"window_ids":[]}]'}]}
        fixture_bin = Path("/tmp/BenchFixture.app/Contents/MacOS/bench_fixture")
        orig_peers = runner.peer_fixtures
        try:
            runner.peer_fixtures = lambda: [1234]
            session = self.session([ui, {"isError": True, "content": [{"type": "text", "text": "macOS getApp requires an app name, path, or bundle ID."}]}, ui, inv])
            admitted = runner.probe_fixture(session, "thread", fixture_bin, 42, expected_pid=1234)
            self.assertTrue(admitted["accessible"])
            self.assertTrue(admitted["window_identity_verified"])
            self.assertEqual(admitted["accepted_target_kind"], "bundle_app")

            runner.peer_fixtures = lambda: [1234, 5678]
            session2 = self.session([ui, {"isError": True}, ui, inv])
            rejected = runner.probe_fixture(session2, "thread", fixture_bin, 42, expected_pid=1234)
            self.assertFalse(rejected["accessible"])
            self.assertFalse(rejected["window_identity_verified"])
        finally:
            runner.peer_fixtures = orig_peers


if __name__ == "__main__":
    unittest.main()
