"""Hermetic unit and stdio integration tests for bin/agy-cua-jev-mcp."""
import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JEV_MCP = os.path.join(ROOT, "bin", "agy-cua-jev-mcp")

loader = importlib.machinery.SourceFileLoader("agy_cua_jev_mcp", JEV_MCP)
spec = importlib.util.spec_from_loader("agy_cua_jev_mcp", loader)
jev = importlib.util.module_from_spec(spec)
loader.exec_module(jev)


FAKE_DRIVER_SCRIPT = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, sys

    step = 0
    display = "0"

    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        method = msg.get("method")
        if method == "initialize":
            res = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "fake-driver"}}
        elif method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})
            if name == "get_window_state":
                step += 1
                if step == 2 and args.get("window_id") == 999:
                    res = {"content": [{"type": "text", "text": "window_closed"}], "isError": True}
                else:
                    snap = f"s0000001{step}"
                    md = f'- [0] AXWindow "Calculator"\\n  - AXStaticText = "{display}"\\n  - [5] AXButton (7) [actions=[press]]'
                    res = {
                        "content": [{"type": "text", "text": md}],
                        "structuredContent": {
                            "pid": args.get("pid", 42),
                            "window_id": args.get("window_id", 100),
                            "snapshot_id": snap,
                            "app_name": "Calculator",
                            "window_title": "Calculator",
                            "elements_complete": True,
                            "truncated": False,
                            "tree_markdown": md,
                            "elements": [
                                {"element_index": 0, "role": "AXWindow", "label": "Calculator", "actions": ["AXRaise"]},
                                {"element_index": 5, "role": "AXButton", "label": "7", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                                {"element_index": 26, "role": "AXMenuBar", "label": None, "actions": ["AXCancel"]},
                                {"element_index": 27, "role": "AXMenuBarItem", "label": "Apple", "parent_index": 26, "actions": ["AXPress"]},
                            ],
                        },
                    }
            elif name == "click":
                if args.get("element_index") == 999:
                    res = {"content": [{"type": "text", "text": "element_not_found"}], "isError": True}
                else:
                    display = "7"
                    res = {"content": [{"type": "text", "text": "clicked 7"}], "structuredContent": {"ok": True}}
            else:
                res = {"content": [{"type": "text", "text": f"ran {name}"}], "structuredContent": {"ok": True}}
        else:
            res = {}
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
""")


def sample_select_args(**overrides):
    base = {
        "goal": "Click button 7",
        "pid": 42,
        "window_id": 100,
        "snapshot_id": "s00000011",
        "elements": [{"element_index": 5, "role": "AXButton", "label": "7", "state": "enabled"}],
        "candidates": [{"id": "ax.5.7", "description": 'Click [5] AXButton "7"'}],
    }
    base.update(overrides)
    return base


class RouteAndCredentialsTest(unittest.TestCase):
    def test_disabled_route(self):
        route = jev.resolve_route({"JEV_DISABLED": "1"}, lambda _: "should-not-read")
        self.assertEqual(route.kind, "none")
        self.assertEqual(route.reason, "disabled")

    def test_rejects_non_loopback_base_url(self):
        route = jev.resolve_route({"TYPESAFE_BASE_URL": "https://example.com"}, lambda _: None)
        self.assertEqual(route.kind, "none")
        self.assertEqual(route.reason, "not-configured")

    def test_accepts_loopback_base_url(self):
        route = jev.resolve_route({"TYPESAFE_BASE_URL": "http://127.0.0.1:8088"}, lambda _: None)
        self.assertEqual(route.kind, "kev-local")
        self.assertEqual(route.endpoint, "http://127.0.0.1:8088/v1/systemone")

    def test_rejects_unpinned_hosted_model(self):
        route = jev.resolve_route(
            {"TYPESAFE_API_KEY": "ts-secret", "AGY_CUA_JEV_MODEL": "jev-floating"},
            lambda _: None,
        )
        self.assertEqual(route.kind, "none")
        self.assertEqual(route.reason, "unpinned-model-refused")

    def test_uses_keychain_and_never_exposes_secret_in_status(self):
        route = jev.resolve_route({}, lambda svc: "super-secret-key" if svc == "typesafe-jev" else None)
        self.assertEqual(route.kind, "typesafe-hosted")
        self.assertEqual(route.model, "jev-1.13.0")
        self.assertEqual(route.key_source, "keychain")
        status = jev.describe_status(route, jev.SessionAccounting(), {})
        self.assertTrue(status["ready"])
        self.assertNotIn("super-secret-key", json.dumps(status))


class RequestValidationTest(unittest.TestCase):
    def test_rejects_screenshots_and_base64(self):
        with self.assertRaisesRegex(ValueError, "screenshots and base64"):
            jev.validate_select_request(sample_select_args(goal="data:image/png;base64,AAAA"))
        with self.assertRaisesRegex(ValueError, "screenshots and base64"):
            jev.validate_select_request(sample_select_args(goal="iVBORw0KGgoAAAANSUhEUgAA"))

    def test_auto_adds_reserved_candidates_and_rejects_empty_executables(self):
        validated = jev.validate_select_request(sample_select_args())
        ids = [c["id"] for c in validated["candidates"]]
        self.assertEqual(ids, ["ax.5.7", "reobserve", "abstain"])

        with self.assertRaisesRegex(ValueError, "at least one executable candidate"):
            jev.validate_select_request(sample_select_args(candidates=[
                {"id": "reobserve", "description": "wait"},
                {"id": "abstain", "description": "stop"},
            ]))


class ProviderDecisionValidationTest(unittest.TestCase):
    def setUp(self):
        self.route = jev.JevRoute(
            kind="typesafe-hosted",
            endpoint=jev.HOSTED_ENDPOINT,
            model="jev-1.13.0",
            api_key="secret-token-xyz",
            key_source="keychain",
        )
        self.req = jev.validate_select_request(sample_select_args())

    def test_accepts_valid_selection_and_records_tokens(self):
        acc = jev.SessionAccounting()
        body = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "candidate": {
                    "type": "choice",
                    "choice": "ax.5.7",
                    "confidence": 0.94,
                    "probabilities": {"ax.5.7": 0.96, "reobserve": 0.02, "abstain": 0.02},
                }
            },
            "usage": {"input_tokens": 120, "output_tokens": 15},
        })
        res = jev.evaluate_jev_choice(self.req, self.route, acc, lambda _r, _t: (200, body))
        self.assertEqual(res["kind"], "selected")
        self.assertEqual(res["selected_id"], "ax.5.7")
        self.assertEqual(res["returned_model"], "jev-1.13.0")
        self.assertEqual(acc.calls_used, 1)
        self.assertEqual(acc.input_tokens, 120)
        self.assertFalse(acc.halted)

    def test_non_actionable_reobserve_and_abstain(self):
        for outcome in ("reobserve", "abstain"):
            acc = jev.SessionAccounting()
            probs = {"ax.5.7": 0.05, "reobserve": 0.90 if outcome == "reobserve" else 0.05, "abstain": 0.90 if outcome == "abstain" else 0.05}
            body = json.dumps({
                "model": "jev-1.13.0",
                "answers": {"candidate": {"type": "choice", "choice": outcome, "confidence": 0.9, "probabilities": probs}},
            })
            res = jev.evaluate_jev_choice(self.req, self.route, acc, lambda _r, _t, b=body: (200, b))
            self.assertEqual(res["kind"], outcome)
            self.assertEqual(res["selected_id"], outcome)

    def test_rejects_model_mismatch_and_halts(self):
        acc = jev.SessionAccounting()
        body = json.dumps({
            "model": "other-model-1.0",
            "answers": {
                "candidate": {
                    "type": "choice",
                    "choice": "ax.5.7",
                    "confidence": 0.9,
                    "probabilities": {"ax.5.7": 0.9, "reobserve": 0.05, "abstain": 0.05},
                }
            },
        })
        res = jev.evaluate_jev_choice(self.req, self.route, acc, lambda _r, _t: (200, body))
        self.assertEqual(res["kind"], "error")
        self.assertEqual(res["reason"], "model_mismatch")
        self.assertTrue(acc.halted)

    def test_rejects_incomplete_or_tied_or_mismatched_scores(self):
        # Tied top score
        acc = jev.SessionAccounting()
        tied = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "candidate": {
                    "type": "choice",
                    "choice": "ax.5.7",
                    "confidence": 0.45,
                    "probabilities": {"ax.5.7": 0.45, "reobserve": 0.45, "abstain": 0.10},
                }
            },
        })
        res = jev.evaluate_jev_choice(self.req, self.route, acc, lambda _r, _t: (200, tied))
        self.assertEqual(res["kind"], "error")
        self.assertEqual(res["reason"], "tied_top_score")

        # Incomplete candidate scores
        acc2 = jev.SessionAccounting()
        incomplete = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "candidate": {
                    "type": "choice",
                    "choice": "ax.5.7",
                    "confidence": 0.9,
                    "probabilities": {"ax.5.7": 0.9, "reobserve": 0.1},
                }
            },
        })
        res2 = jev.evaluate_jev_choice(self.req, self.route, acc2, lambda _r, _t: (200, incomplete))
        self.assertEqual(res2["reason"], "incomplete_candidate_scores")
        self.assertTrue(acc2.halted)

    def test_timeout_or_500_retains_uncertain_accounting_and_stops_without_retry(self):
        acc = jev.SessionAccounting()
        calls = []

        def failing_post(req, timeout):
            calls.append((req.full_url, timeout))
            raise TimeoutError("timed out")

        res1 = jev.evaluate_jev_choice(self.req, self.route, acc, failing_post)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res1["kind"], "error")
        self.assertEqual(res1["reason"], "provider_transport_error")
        self.assertEqual(acc.uncertain_calls, 1)
        self.assertTrue(acc.halted)

        # Subsequent call is refused immediately without touching network
        res2 = jev.evaluate_jev_choice(self.req, self.route, acc, failing_post)
        self.assertEqual(len(calls), 1)
        self.assertEqual(res2["reason"], "session_halted")

    def test_secret_leak_guard_scrubs_and_halts(self):
        acc = jev.SessionAccounting()
        leaked = json.dumps({"model": "jev-1.13.0", "note": "secret-token-xyz"})
        res = jev.evaluate_jev_choice(self.req, self.route, acc, lambda _r, _t: (200, leaked))
        self.assertEqual(res["reason"], "invalid_response")
        self.assertNotIn("secret-token-xyz", json.dumps(res))
        self.assertTrue(acc.halted)


class ControllerBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.observation = {
            "pid": 42,
            "window_id": 100,
            "snapshot_id": "s00000011",
            "static_texts": ["0"],
            "indexed_element_ids": {0, 5},
            "elements": [{"element_index": 5, "role": "AXButton", "label": "7", "state": "enabled"}],
            "auto_candidates": [{
                "id": "ax.5.7",
                "description": 'Click [5] AXButton "7"',
                "pid": 42,
                "window_id": 100,
                "snapshot_id": "s00000011",
                "tool": "click",
                "arguments": {
                    "pid": 42,
                    "window_id": 100,
                    "snapshot_id": "s00000011",
                    "element_index": 5,
                    "delivery_mode": "background",
                },
            }],
        }

    def test_refuses_stale_snapshot_out_of_scope_unknown_element_and_foreground(self):
        with self.assertRaisesRegex(ValueError, "stale_snapshot_id"):
            jev.build_bound_candidates(
                [{"id": "c1", "description": "click", "snapshot_id": "s00000009", "element_index": 5}],
                self.observation,
            )
        with self.assertRaisesRegex(ValueError, "out_of_scope_target"):
            jev.build_bound_candidates(
                [{"id": "c1", "description": "click", "pid": 99, "element_index": 5}],
                self.observation,
            )
        with self.assertRaisesRegex(ValueError, "unknown_element_index"):
            jev.build_bound_candidates(
                [{"id": "c1", "description": "click", "element_index": 99}],
                self.observation,
            )
        with self.assertRaisesRegex(ValueError, "foreground_or_global_input_forbidden"):
            jev.build_bound_candidates(
                [{"id": "c1", "description": "click", "element_index": 5, "arguments": {"element_index": 5, "delivery_mode": "foreground"}}],
                self.observation,
            )
        with self.assertRaisesRegex(ValueError, "forbidden_or_unsupported_tool"):
            jev.build_bound_candidates(
                [{"id": "c1", "description": "front", "tool": "bring_to_front", "element_index": 5}],
                self.observation,
            )
        with self.assertRaisesRegex(ValueError, "background_drag_unavailable_on_macos"):
            jev.build_bound_candidates(
                [{"id": "c1", "description": "drag", "tool": "drag", "from_x": 10, "from_y": 10, "to_x": 40, "to_y": 40}],
                self.observation,
            )

    def test_extract_window_observation_preserves_empty_text_field(self):
        obs = jev.extract_window_observation(
            {
                "structuredContent": {
                    "pid": 42,
                    "window_id": 100,
                    "snapshot_id": "s00000011",
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "Win", "enabled": True},
                        {"element_index": 1, "role": "AXTextField", "label": "", "value": "", "enabled": True, "parent_index": 0},
                    ],
                },
                "content": [{"type": "text", "text": '[1] AXTextField ""'}],
            },
            expected_pid=42,
            expected_window_id=100,
        )
        self.assertEqual(len(obs["elements"]), 1)
        self.assertEqual(obs["elements"][0]["role"], "AXTextField")
        self.assertEqual(obs["elements"][0]["element_index"], 1)
        self.assertEqual(obs["indexed_element_ids"], {1})

    def test_refuses_invented_selection_at_authorization(self):
        bound = jev.build_bound_candidates(None, self.observation)
        fake_decision = {
            "kind": "selected",
            "pid": 42,
            "window_id": 100,
            "snapshot_id": "s00000011",
            "selected_id": "invented.candidate",
        }
        with self.assertRaisesRegex(ValueError, "invented_selection"):
            jev.authorize_candidate_dispatch(
                fake_decision,
                bound,
                expected_pid=42,
                expected_window_id=100,
                current_snapshot_id="s00000011",
                valid_element_indices={0, 5},
            )

    def test_authorizes_double_click_and_right_click_in_background(self):
        bound = jev.build_bound_candidates(
            [
                {"id": "dbl.5", "description": "Double-click 7", "tool": "double_click", "element_index": 5},
                {"id": "rgt.5", "description": "Right-click 7", "tool": "right_click", "element_index": 5},
            ],
            self.observation,
        )
        for cid, expected_tool in (("dbl.5", "double_click"), ("rgt.5", "right_click")):
            auth = jev.authorize_candidate_dispatch(
                {
                    "kind": "selected",
                    "pid": 42,
                    "window_id": 100,
                    "snapshot_id": "s00000011",
                    "selected_id": cid,
                },
                bound,
                expected_pid=42,
                expected_window_id=100,
                current_snapshot_id="s00000011",
                valid_element_indices={0, 5},
            )
            self.assertEqual(auth["tool"], expected_tool)
            self.assertEqual(auth["arguments"]["delivery_mode"], "background")
            self.assertEqual(auth["arguments"]["snapshot_id"], "s00000011")

    def test_check_postcondition_rejects_unchanged_snapshot_and_empty_predicates(self):
        pre = {"snapshot_id": "s00000011", "static_texts": ["0"], "elements": []}
        post_noop = {"snapshot_id": "s00000012", "static_texts": ["0"], "elements": []}
        post = {"snapshot_id": "s00000012", "static_texts": ["7"], "elements": []}
        _, ok_same, reason_same = jev.check_postcondition(None, pre, pre)
        self.assertFalse(ok_same)
        self.assertEqual(reason_same, "post_snapshot_id_unchanged")
        changed_noop, ok_noop, reason_noop = jev.check_postcondition(None, pre, post_noop)
        self.assertFalse(changed_noop)
        self.assertTrue(ok_noop)
        self.assertEqual(reason_noop, "fresh_observation_captured")
        _, ok_empty_obj, reason_empty_obj = jev.check_postcondition({}, pre, post)
        self.assertFalse(ok_empty_obj)
        self.assertEqual(reason_empty_obj, "empty_expected_postcondition")
        _, ok_empty_str, reason_empty_str = jev.check_postcondition({"static_text_contains": ""}, pre, post)
        self.assertFalse(ok_empty_str)
        self.assertEqual(reason_empty_str, "invalid_empty_static_text_contains")


class StdioEndToEndTest(unittest.TestCase):
    def test_jev_step_verifies_effect_and_records_stage_timings(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_driver = os.path.join(tmp, "fake-driver")
            with open(fake_driver, "w", encoding="utf-8") as handle:
                handle.write(FAKE_DRIVER_SCRIPT)
            os.chmod(fake_driver, os.stat(fake_driver).st_mode | stat.S_IEXEC)

            trace_file = os.path.join(tmp, "trace.jsonl")

            def fake_http(_req, _timeout):
                return 200, json.dumps({
                    "model": "jev-1.13.0",
                    "answers": {
                        "candidate": {
                            "type": "choice",
                            "choice": "ax.5.7",
                            "confidence": 0.97,
                            "probabilities": {"ax.5.7": 0.97, "reobserve": 0.02, "abstain": 0.01},
                        }
                    },
                    "usage": {"input_tokens": 200, "output_tokens": 20},
                })

            server = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": fake_driver, "TYPESAFE_API_KEY": "test-key", "AGY_CUA_JEV_TRACE_FILE": trace_file},
                keychain_reader=lambda _: None,
                http_post=fake_http,
            )
            try:
                resp = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Click button 7",
                            "pid": 42,
                            "window_id": 100,
                            "expected_postcondition": {"static_text_contains": "7"},
                        },
                    },
                })
            finally:
                server.close()

            sc = resp["result"]["structuredContent"]
            self.assertEqual(sc["status"], "verified")
            self.assertTrue(sc["verified"])
            self.assertTrue(sc["state_changed"])
            self.assertEqual(sc["pre_snapshot_id"], "s00000011")
            self.assertEqual(sc["post_snapshot_id"], "s00000012")
            self.assertEqual(sc["pre_static_texts"], ["0"])
            self.assertEqual(sc["post_static_texts"], ["7"])
            self.assertEqual(sc["selected_id"], "ax.5.7")
            self.assertEqual(sc["returned_model"], "jev-1.13.0")
            for k in ("observe_ms", "provider_ms", "dispatch_ms", "verify_ms", "total_ms"):
                self.assertIn(k, sc["timings_ms"])
                self.assertGreaterEqual(sc["timings_ms"][k], 0.0)
            self.assertTrue(os.path.exists(trace_file))

    def test_jev_step_halts_on_post_dispatch_verification_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_driver = os.path.join(tmp, "fake-driver")
            with open(fake_driver, "w", encoding="utf-8") as handle:
                handle.write(FAKE_DRIVER_SCRIPT)
            os.chmod(fake_driver, os.stat(fake_driver).st_mode | stat.S_IEXEC)

            def fake_http(_req, _timeout):
                return 200, json.dumps({
                    "model": "jev-1.13.0",
                    "answers": {
                        "candidate": {
                            "type": "choice",
                            "choice": "ax.5.7",
                            "confidence": 0.97,
                            "probabilities": {"ax.5.7": 0.97, "reobserve": 0.02, "abstain": 0.01},
                        }
                    },
                    "usage": {"input_tokens": 200, "output_tokens": 20},
                })

            server = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": fake_driver, "TYPESAFE_API_KEY": "test-key"},
                keychain_reader=lambda _: None,
                http_post=fake_http,
            )
            try:
                resp = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Click button 7",
                            "pid": 42,
                            "window_id": 999,
                            "expected_postcondition": {"static_text_contains": "7"},
                        },
                    },
                })
            finally:
                server.close()

            sc = resp["result"]["structuredContent"]
            self.assertEqual(sc["status"], "verification_failed")
            self.assertFalse(sc["verified"])
            self.assertEqual(sc["accounting"]["uncertain_side_effects"], 1)
            self.assertTrue(sc["accounting"]["halted"])
            self.assertEqual(sc["accounting"]["halt_reason"], "uncertain_verification_after_dispatch")


if __name__ == "__main__":
    unittest.main()

