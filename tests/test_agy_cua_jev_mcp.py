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
                if args.get("tree_format") != "both":
                    res = {"content": [{"type": "text", "text": "missing_tree_format_both"}], "isError": True}
                elif step == 2 and args.get("window_id") == 999:
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
                if "element_index" in args or "snapshot_id" in args or not str(args.get("element_token", "")).endswith(":5"):
                    res = {"content": [{"type": "text", "text": f"invalid_element_token_args:{args}"}], "isError": True}
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
        self.assertFalse(ok_noop)
        self.assertEqual(reason_noop, "no_observable_state_change")
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

    def test_element_token_binding_and_stale_token_rejection(self):
        observation = jev.extract_window_observation(
            {
                "structuredContent": {
                    "pid": 42,
                    "window_id": 100,
                    "snapshot_id": "s00000011",
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "Calculator", "actions": ["AXRaise"]},
                        {"element_index": 5, "role": "AXButton", "label": "7", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                    ],
                },
                "content": [{"type": "text", "text": '- [5] AXButton (7)'}],
            },
            expected_pid=42,
            expected_window_id=100,
        )
        bound_default = jev.build_bound_candidates(None, observation)
        self.assertEqual(bound_default[0]["arguments"]["element_token"], "s00000011:5")
        self.assertEqual(bound_default[0]["arguments"]["element_index"], 5)

        bound_from_tok = jev.build_bound_candidates(
            [{"id": "click-7", "description": "Click 7", "tool": "click", "element_token": "s00000011:5"}],
            observation,
        )
        self.assertEqual(bound_from_tok[0]["arguments"]["element_index"], 5)
        self.assertEqual(bound_from_tok[0]["arguments"]["element_token"], "s00000011:5")

        with self.assertRaisesRegex(ValueError, "stale_snapshot_id"):
            jev.build_bound_candidates(
                [{"id": "click-7", "description": "Click 7", "tool": "click", "element_index": 5, "element_token": "s00000001:5"}],
                observation,
            )

        with self.assertRaisesRegex(ValueError, "invalid_element_token"):
            jev.build_bound_candidates(
                [{"id": "click-7", "description": "Click 7", "tool": "click", "element_index": 5, "element_token": "s00000011:9"}],
                observation,
            )

        with self.assertRaisesRegex(ValueError, "invalid_element_token"):
            jev.build_bound_candidates(
                [{"id": "click-7", "description": "Click 7", "tool": "click", "element_token": "malformed"}],
                observation,
            )

    def test_check_postcondition_rejects_noop_without_expected_postcondition(self):
        pre_obs = {"snapshot_id": "s1", "static_texts": ["0"], "elements": [{"element_index": 2, "label": "All Clear", "value": "", "state": "enabled"}]}
        post_obs = {"snapshot_id": "s2", "static_texts": ["0"], "elements": [{"element_index": 2, "label": "All Clear", "value": "", "state": "enabled"}]}
        changed, met, reason = jev.check_postcondition(None, pre_obs, post_obs)
        self.assertFalse(changed)
        self.assertFalse(met)
        self.assertEqual(reason, "no_observable_state_change")

        changed2, met2, reason2 = jev.check_postcondition({"static_text_contains": "0"}, pre_obs, post_obs)
        self.assertFalse(changed2)
        self.assertTrue(met2)
        self.assertEqual(reason2, "expected_postcondition_verified")

    def test_false_positive_1_unrelated_change_without_predicate_never_verifies_goal(self):
        pre_obs = {
            "snapshot_id": "s00000011",
            "static_texts": ["0"],
            "elements": [{"element_index": 5, "role": "AXButton", "label": "7", "value": "", "state": "enabled"}],
        }
        post_obs = {
            "snapshot_id": "s00000012",
            "static_texts": ["0", "Unrelated notification banner"],
            "elements": [{"element_index": 5, "role": "AXButton", "label": "7", "value": "", "state": "enabled"}],
        }
        changed, met, reason = jev.check_postcondition(None, pre_obs, post_obs)
        self.assertTrue(changed)
        self.assertFalse(met)
        self.assertEqual(reason, "observable_state_changed")

        with tempfile.TemporaryDirectory() as tmp:
            fake_driver = os.path.join(tmp, "fake-driver-unrelated")
            with open(fake_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent("""\
                    #!/usr/bin/env python3
                    import json, sys
                    step = 0
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            res = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "fake"}}
                        elif method == "tools/call":
                            name = msg.get("params", {}).get("name")
                            if name == "get_window_state":
                                step += 1
                                md = '- [0] AXWindow "Calculator"\\n  - AXStaticText = "0"\\n'
                                if step >= 2:
                                    md += '  - AXStaticText = "Unrelated banner"\\n'
                                res = {
                                    "content": [{"type": "text", "text": md}],
                                    "structuredContent": {
                                        "pid": 42,
                                        "window_id": 100,
                                        "snapshot_id": f"s0000001{step}",
                                        "tree_markdown": md,
                                        "elements": [
                                            {"element_index": 0, "role": "AXWindow", "label": "Calculator"},
                                            {"element_index": 5, "role": "AXButton", "label": "7", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                                        ],
                                    },
                                }
                            else:
                                res = {"content": [{"type": "text", "text": "clicked"}], "structuredContent": {"ok": True}}
                        else:
                            res = {}
                        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
                """))
            os.chmod(fake_driver, os.stat(fake_driver).st_mode | stat.S_IEXEC)

            server = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": fake_driver, "TYPESAFE_API_KEY": "test-key"},
                keychain_reader=lambda _: None,
                http_post=lambda _req, _t: (200, json.dumps({
                    "model": "jev-1.13.0",
                    "answers": {
                        "candidate": {
                            "type": "choice",
                            "choice": "ax.5.7",
                            "confidence": 0.99,
                            "probabilities": {"ax.5.7": 0.99, "reobserve": 0.005, "abstain": 0.005},
                        }
                    },
                    "usage": {"input_tokens": 50, "output_tokens": 5},
                })),
            )
            try:
                resp = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {"goal": "Click button 7", "pid": 42, "window_id": 100},
                    },
                })
            finally:
                server.close()

            sc = resp["result"]["structuredContent"]
            self.assertFalse(sc["verified"])
            self.assertTrue(sc["state_changed"])
            self.assertEqual(sc["status"], "state_changed")
            self.assertEqual(sc["verification_outcome"], "state_changed")

    def test_false_positive_2_exact_static_text_equals_rejects_substring_match(self):
        pre_obs = {"snapshot_id": "s00000011", "static_texts": ["10"], "elements": []}
        post_obs = {"snapshot_id": "s00000012", "static_texts": ["10"], "elements": []}
        changed, met, reason = jev.check_postcondition({"static_text_equals": "0"}, pre_obs, post_obs)
        self.assertFalse(changed)
        self.assertFalse(met)
        self.assertIn("static_text_equals", reason)

        post_decimal = {"snapshot_id": "s00000013", "static_texts": ["0.5"], "elements": []}
        _, met_dec, _ = jev.check_postcondition({"static_text_equals": "0"}, pre_obs, post_decimal)
        self.assertFalse(met_dec)

        post_zero = {"snapshot_id": "s00000014", "static_texts": ["0"], "elements": []}
        _, met_zero, reason_zero = jev.check_postcondition({"static_text_equals": "0"}, pre_obs, post_zero)
        self.assertTrue(met_zero)
        self.assertEqual(reason_zero, "expected_postcondition_verified")

        post_grouped = {"snapshot_id": "s00000015", "static_texts": ["72×60", "4,320"], "elements": []}
        _, met_grouped, _ = jev.check_postcondition(
            {"static_text_equals": "4320", "static_text_index": -1},
            pre_obs,
            post_grouped,
        )
        self.assertTrue(met_grouped)

        _, met_wrong_idx, _ = jev.check_postcondition(
            {"static_text_equals": "4320", "static_text_index": 0},
            pre_obs,
            post_grouped,
        )
        self.assertFalse(met_wrong_idx)

        # Malformed comma-separated string '1,,0' must not match '10'
        post_malformed = {"snapshot_id": "s00000016", "static_texts": ["1,,0"], "elements": []}
        _, met_malformed, _ = jev.check_postcondition({"static_text_equals": "10"}, pre_obs, post_malformed)
        self.assertFalse(met_malformed)

    def test_false_positive_3_element_predicates_must_bind_to_target_element(self):
        pre_obs = {
            "snapshot_id": "s00000011",
            "static_texts": [],
            "elements": [
                {"element_index": 3, "role": "AXTextField", "label": "Subject", "value": "draft", "state": "enabled (has_value)"},
                {"element_index": 8, "role": "AXTextField", "label": "Status", "value": "validated", "state": "enabled (has_value)"},
            ],
        }
        post_obs = {
            "snapshot_id": "s00000012",
            "static_texts": [],
            "elements": [
                {"element_index": 3, "role": "AXTextField", "label": "Subject", "value": "draft", "state": "enabled (has_value)"},
                {"element_index": 8, "role": "AXTextField", "label": "Status", "value": "validated", "state": "enabled (has_value)"},
            ],
        }
        # Unbound element predicate without element_index or target_element_index must be refused
        _, met_unbound, reason_unbound = jev.check_postcondition(
            {"element_value_equals": "validated"},
            pre_obs,
            post_obs,
        )
        self.assertFalse(met_unbound)
        self.assertIn("unbound_element_postcondition", reason_unbound)

        # Bound via target_element_index=3 (or explicit element_index=3) must reject when element 3 still has 'draft'
        _, met_wrong_field, reason_wrong_field = jev.check_postcondition(
            {"element_value_equals": "validated"},
            pre_obs,
            post_obs,
            target_element_index=3,
        )
        self.assertFalse(met_wrong_field)
        self.assertIn("element_value_equals", reason_wrong_field)

        # Element index not present in pre_obs must fail
        _, met_missing_pre, reason_missing_pre = jev.check_postcondition(
            {"element_value_equals": "validated", "element_index": 99},
            pre_obs,
            post_obs,
        )
        self.assertFalse(met_missing_pre)
        self.assertIn("target_element_missing_in_pre_obs", reason_missing_pre)

        # Role drift between pre_obs and post_obs on bound element_index must fail
        post_role_drift = {
            "snapshot_id": "s00000013",
            "static_texts": [],
            "elements": [
                {"element_index": 3, "role": "AXButton", "label": "Subject", "value": "validated", "state": "enabled"},
            ],
        }
        _, met_drift, reason_drift = jev.check_postcondition(
            {"element_value_equals": "validated", "element_index": 3},
            pre_obs,
            post_role_drift,
        )
        self.assertFalse(met_drift)
        self.assertIn("role_mismatch", reason_drift)

        # Positive match when targeted element 3 updates to 'validated' with same role
        post_target_updated = {
            "snapshot_id": "s00000014",
            "static_texts": [],
            "elements": [
                {"element_index": 3, "role": "AXTextField", "label": "Subject", "value": "validated", "state": "enabled (has_value)"},
                {"element_index": 8, "role": "AXTextField", "label": "Status", "value": "other", "state": "enabled (has_value)"},
            ],
        }
        _, met_ok, reason_ok = jev.check_postcondition(
            {"element_value_equals": "validated", "element_label_equals": "Subject"},
            pre_obs,
            post_target_updated,
            target_element_index=3,
        )
        self.assertTrue(met_ok)
        self.assertEqual(reason_ok, "expected_postcondition_verified")

    def test_driver_client_deadline_on_partial_line_and_hang_after_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 1. Partial line without newline stalls; DriverClient must time out and reset proc
            stalling_driver = os.path.join(tmp, "stalling-driver")
            with open(stalling_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent("""\
                    #!/usr/bin/env python3
                    import json, sys, time
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        if msg.get("method") == "initialize":
                            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18"}}), flush=True)
                        else:
                            sys.stdout.write('{"jsonrpc": "2.0", "id": 2')
                            sys.stdout.flush()
                            time.sleep(30)
                """))
            os.chmod(stalling_driver, os.stat(stalling_driver).st_mode | stat.S_IEXEC)

            client = jev.DriverClient(stalling_driver, timeout_ms=150)
            try:
                with self.assertRaisesRegex((TimeoutError, RuntimeError), "timed out"):
                    client.call_tool("get_window_state", {"pid": 42, "window_id": 100})
                self.assertIsNone(client._proc)
            finally:
                client.close()

            # 2. Hang or pipe drop during Stage 4 dispatch latches outcome_unknown and halts session
            hang_dispatch_driver = os.path.join(tmp, "hang-dispatch-driver")
            with open(hang_dispatch_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent("""\
                    #!/usr/bin/env python3
                    import json, sys, time
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18"}}), flush=True)
                        elif method == "tools/call":
                            name = msg.get("params", {}).get("name")
                            if name == "get_window_state":
                                md = '- [0] AXWindow "Calculator"\\n  - AXStaticText = "0"\\n  - [5] AXButton (7)'
                                res = {
                                    "content": [{"type": "text", "text": md}],
                                    "structuredContent": {
                                        "pid": 42,
                                        "window_id": 100,
                                        "snapshot_id": "s00000011",
                                        "tree_markdown": md,
                                        "elements": [
                                            {"element_index": 0, "role": "AXWindow", "label": "Calculator"},
                                            {"element_index": 5, "role": "AXButton", "label": "7", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                                        ],
                                    },
                                }
                                print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
                            elif name == "click":
                                sys.stdout.write('{"partial": ')
                                sys.stdout.flush()
                                time.sleep(30)
                """))
            os.chmod(hang_dispatch_driver, os.stat(hang_dispatch_driver).st_mode | stat.S_IEXEC)

            server = jev.JevMcpServer(
                env={
                    "AGY_CUA_DRIVER": hang_dispatch_driver,
                    "AGY_CUA_DRIVER_TIMEOUT_MS": "400",
                    "TYPESAFE_API_KEY": "test-key",
                },
                keychain_reader=lambda _: None,
                http_post=lambda _req, _t: (200, json.dumps({
                    "model": "jev-1.13.0",
                    "answers": {
                        "candidate": {
                            "type": "choice",
                            "choice": "ax.5.7",
                            "confidence": 0.99,
                            "probabilities": {"ax.5.7": 0.99, "reobserve": 0.005, "abstain": 0.005},
                        }
                    },
                    "usage": {"input_tokens": 50, "output_tokens": 5},
                })),
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
                            "expected_postcondition": {"static_text_equals": "7"},
                        },
                    },
                })
            finally:
                server.close()

            sc = resp["result"]["structuredContent"]
            self.assertFalse(sc["verified"])
            self.assertTrue(sc["uncertain_side_effect"])
            self.assertEqual(sc["verification_outcome"], "outcome_unknown")
            self.assertTrue(sc["accounting"]["halted"])
            self.assertEqual(sc["accounting"]["uncertain_side_effects"], 1)

            # 3. Pipe drop (EOF) during Stage 5 post-dispatch verify also latches outcome_unknown and halts
            eof_verify_driver = os.path.join(tmp, "eof-verify-driver")
            with open(eof_verify_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent("""\
                    #!/usr/bin/env python3
                    import json, sys
                    obs_count = 0
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18"}}), flush=True)
                        elif method == "tools/call":
                            name = msg.get("params", {}).get("name")
                            if name == "get_window_state":
                                obs_count += 1
                                if obs_count >= 2:
                                    sys.exit(1)
                                md = '- [0] AXWindow "Calculator"\\n  - AXStaticText = "0"\\n  - [5] AXButton (7)'
                                res = {
                                    "content": [{"type": "text", "text": md}],
                                    "structuredContent": {
                                        "pid": 42,
                                        "window_id": 100,
                                        "snapshot_id": "s00000011",
                                        "tree_markdown": md,
                                        "elements": [
                                            {"element_index": 0, "role": "AXWindow", "label": "Calculator"},
                                            {"element_index": 5, "role": "AXButton", "label": "7", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                                        ],
                                    },
                                }
                                print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
                            elif name == "click":
                                res = {"content": [{"type": "text", "text": "clicked"}], "structuredContent": {"ok": True}}
                                print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
                """))
            os.chmod(eof_verify_driver, os.stat(eof_verify_driver).st_mode | stat.S_IEXEC)

            server_eof = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": eof_verify_driver, "TYPESAFE_API_KEY": "test-key"},
                keychain_reader=lambda _: None,
                http_post=lambda _req, _t: (200, json.dumps({
                    "model": "jev-1.13.0",
                    "answers": {
                        "candidate": {
                            "type": "choice",
                            "choice": "ax.5.7",
                            "confidence": 0.99,
                            "probabilities": {"ax.5.7": 0.99, "reobserve": 0.005, "abstain": 0.005},
                        }
                    },
                    "usage": {"input_tokens": 50, "output_tokens": 5},
                })),
            )
            try:
                resp_eof = server_eof.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Click button 7",
                            "pid": 42,
                            "window_id": 100,
                            "expected_postcondition": {"static_text_equals": "7"},
                        },
                    },
                })
            finally:
                server_eof.close()

            sc_eof = resp_eof["result"]["structuredContent"]
            self.assertEqual(sc_eof["status"], "verification_failed")
            self.assertEqual(sc_eof["verification_outcome"], "outcome_unknown")
            self.assertFalse(sc_eof["verified"])
            self.assertTrue(sc_eof["uncertain_side_effect"])
            self.assertTrue(sc_eof["accounting"]["halted"])
            self.assertEqual(sc_eof["accounting"]["uncertain_side_effects"], 1)

    def test_jev_batch_guarded_multi_step_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_driver = os.path.join(tmp, "batch-driver")
            log_file = os.path.join(tmp, "driver-calls.jsonl")
            with open(batch_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(f"""\
                    #!/usr/bin/env python3
                    import json, sys
                    snap_seq = 10
                    display = "99"
                    log_path = {log_file!r}
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            res = {{"protocolVersion": "2025-06-18", "capabilities": {{}}, "serverInfo": {{"name": "batch-driver"}}}}
                        elif method == "tools/call":
                            params = msg.get("params", {{}})
                            name = params.get("name")
                            args = params.get("arguments", {{}})
                            with open(log_path, "a", encoding="utf-8") as lf:
                                lf.write(json.dumps({{"name": name, "args": args}}) + "\\n")
                            if name == "get_window_state":
                                snap_seq += 1
                                snap = f"s000000{{snap_seq}}"
                                md = (
                                    f'- [0] AXWindow "Calculator"\\n'
                                    f'  - AXStaticText = "{{display}}"\\n'
                                    f'  - [2] AXButton (All Clear)\\n'
                                    f'  - [5] AXButton (7)\\n'
                                    f'  - [6] AXButton (8)'
                                )
                                res = {{
                                    "content": [{{"type": "text", "text": md}}],
                                    "structuredContent": {{
                                        "pid": args.get("pid", 42),
                                        "window_id": args.get("window_id", 100),
                                        "snapshot_id": snap,
                                        "tree_markdown": md,
                                        "elements": [
                                            {{"element_index": 0, "role": "AXWindow", "label": "Calculator"}},
                                            {{"element_index": 2, "role": "AXButton", "label": "All Clear", "enabled": True, "parent_index": 0, "actions": ["AXPress"]}},
                                            {{"element_index": 5, "role": "AXButton", "label": "7", "enabled": True, "parent_index": 0, "actions": ["AXPress"]}},
                                            {{"element_index": 6, "role": "AXButton", "label": "8", "enabled": True, "parent_index": 0, "actions": ["AXPress"]}},
                                        ],
                                    }},
                                }}
                            elif name == "click":
                                tok = str(args.get("element_token", ""))
                                expected_prefix = f"s000000{{snap_seq}}:"
                                if not tok.startswith(expected_prefix) or args.get("delivery_mode") != "background":
                                    res = {{"content": [{{"type": "text", "text": f"bad_token_or_mode:{{args}}"}}], "isError": True}}
                                else:
                                    idx = int(tok.split(":", 1)[1])
                                    if idx == 2:
                                        display = "0"
                                    elif idx == 5:
                                        display = "7" if display == "0" else display + "7"
                                    elif idx == 6:
                                        display = "8" if display == "0" else display + "8"
                                    res = {{"content": [{{"type": "text", "text": f"clicked {{idx}}"}}], "structuredContent": {{"ok": True}}}}
                            else:
                                res = {{"content": [{{"type": "text", "text": "ok"}}], "structuredContent": {{"ok": True}}}}
                        else:
                            res = {{}}
                        print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": res}}), flush=True)
                """))
            os.chmod(batch_driver, os.stat(batch_driver).st_mode | stat.S_IEXEC)

            jev_calls = []

            def fake_http(req, _timeout):
                payload = json.loads(req.data.decode("utf-8"))
                goal = payload["state"]["goal"]
                criteria = payload["questions"]["candidate"]["criteria"]
                jev_calls.append(goal)
                chosen = "ax.2.all-clear" if "All Clear" in goal else "ax.6.8"
                probs = {cid: (0.96 if cid == chosen else round(0.04 / (len(criteria) - 1), 4)) for cid in criteria}
                return 200, json.dumps({
                    "model": "jev-1.13.0",
                    "answers": {
                        "candidate": {
                            "type": "choice",
                            "choice": chosen,
                            "confidence": 0.96,
                            "probabilities": probs,
                        }
                    },
                    "usage": {"input_tokens": 80, "output_tokens": 10},
                })

            server = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": batch_driver, "TYPESAFE_API_KEY": "test-key"},
                keychain_reader=lambda _: None,
                http_post=fake_http,
            )
            try:
                # 1. Batch step missing expected_postcondition must be refused before any driver call
                refused = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_batch",
                        "arguments": {
                            "pid": 42,
                            "window_id": 100,
                            "steps": [{"goal": "Click All Clear"}],
                        },
                    },
                })
                self.assertTrue(refused["result"]["isError"])
                self.assertEqual(refused["result"]["structuredContent"]["status"], "refused")
                self.assertFalse(os.path.exists(log_file))

                # 2. Valid 3-step guarded batch (Jev + deterministic single-candidate + Jev)
                resp = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_batch",
                        "arguments": {
                            "pid": 42,
                            "window_id": 100,
                            "steps": [
                                {
                                    "goal": "Click All Clear",
                                    "expected_postcondition": {"static_text_equals": "0", "static_text_index": -1},
                                },
                                {
                                    "goal": "Click button 7 deterministically",
                                    "mode": "deterministic",
                                    "candidates": [
                                        {"id": "det.7", "description": 'Click [5] AXButton "7"', "tool": "click", "element_index": 5}
                                    ],
                                    "expected_postcondition": {"static_text_equals": "7", "static_text_index": -1},
                                },
                                {
                                    "goal": "Click button 8",
                                    "expected_postcondition": {"static_text_equals": "78", "static_text_index": -1},
                                },
                            ],
                        },
                    },
                })
            finally:
                server.close()

            sc = resp["result"]["structuredContent"]
            self.assertFalse(resp["result"]["isError"])
            self.assertEqual(sc["schema"], "agy_cua.jev_batch_v1")
            self.assertEqual(sc["status"], "verified")
            self.assertEqual(sc["verification_outcome"], "goal_verified")
            self.assertTrue(sc["verified"])
            self.assertFalse(sc["uncertain_side_effect"])
            self.assertEqual(sc["completed_count"], 3)
            self.assertEqual(len(sc["completed_steps"]), 3)
            self.assertIsNone(sc["failed_step"])
            self.assertEqual(sc["remaining_steps"], [])
            # Jev was only called for steps 0 and 2; step 1 used deterministic single-candidate mode
            self.assertEqual(len(jev_calls), 2)
            self.assertEqual(sc["completed_steps"][0]["pre_snapshot_id"], "s00000011")
            self.assertEqual(sc["completed_steps"][0]["post_snapshot_id"], "s00000012")
            self.assertEqual(sc["completed_steps"][1]["pre_snapshot_id"], "s00000013")
            self.assertEqual(sc["completed_steps"][1]["post_snapshot_id"], "s00000014")
            self.assertEqual(sc["completed_steps"][1]["dispatched_arguments"]["element_token"], "s00000013:5")
            self.assertEqual(sc["completed_steps"][2]["pre_snapshot_id"], "s00000015")
            self.assertEqual(sc["completed_steps"][2]["post_snapshot_id"], "s00000016")

    def test_jev_batch_stops_on_unexpected_intermediate_state_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch_driver = os.path.join(tmp, "batch-unmet-driver")
            with open(batch_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent("""\
                    #!/usr/bin/env python3
                    import json, sys
                    snap_seq = 10
                    display = "0"
                    clicks = []
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            res = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "batch-unmet"}}
                        elif method == "tools/call":
                            params = msg.get("params", {})
                            name = params.get("name")
                            args = params.get("arguments", {})
                            if name == "get_window_state":
                                snap_seq += 1
                                snap = f"s000000{snap_seq}"
                                md = (
                                    f'- [0] AXWindow "Calculator"\\n'
                                    f'  - AXStaticText = "{display}"\\n'
                                    f'  - [5] AXButton (7)\\n'
                                    f'  - [6] AXButton (8)\\n'
                                    f'  - [7] AXButton (9)'
                                )
                                res = {
                                    "content": [{"type": "text", "text": md}],
                                    "structuredContent": {
                                        "pid": 42,
                                        "window_id": 100,
                                        "snapshot_id": snap,
                                        "tree_markdown": md,
                                        "elements": [
                                            {"element_index": 0, "role": "AXWindow", "label": "Calculator"},
                                            {"element_index": 5, "role": "AXButton", "label": "7", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                                            {"element_index": 6, "role": "AXButton", "label": "8", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                                            {"element_index": 7, "role": "AXButton", "label": "9", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                                        ],
                                    },
                                }
                            elif name == "click":
                                tok = str(args.get("element_token", ""))
                                idx = int(tok.split(":", 1)[1])
                                clicks.append(idx)
                                if idx == 5:
                                    display = "7"
                                elif idx == 6:
                                    # Unexpected app state: display becomes "999" instead of "78"
                                    display = "999"
                                elif idx == 7:
                                    display = "9999"
                                res = {"content": [{"type": "text", "text": f"clicked {idx}"}], "structuredContent": {"ok": True}}
                            else:
                                res = {"content": [{"type": "text", "text": "ok"}], "structuredContent": {"ok": True}}
                        else:
                            res = {}
                        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
                """))
            os.chmod(batch_driver, os.stat(batch_driver).st_mode | stat.S_IEXEC)

            server = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": batch_driver, "TYPESAFE_API_KEY": "test-key"},
                keychain_reader=lambda _: None,
            )
            try:
                resp = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_batch",
                        "arguments": {
                            "pid": 42,
                            "window_id": 100,
                            "steps": [
                                {
                                    "goal": "Click 7",
                                    "mode": "deterministic",
                                    "candidates": [{"id": "c7", "description": "Click 7", "tool": "click", "element_index": 5}],
                                    "expected_postcondition": {"static_text_equals": "7"},
                                },
                                {
                                    "goal": "Click 8",
                                    "mode": "deterministic",
                                    "candidates": [{"id": "c8", "description": "Click 8", "tool": "click", "element_index": 6}],
                                    "expected_postcondition": {"static_text_equals": "78"},
                                },
                                {
                                    "goal": "Click 9",
                                    "mode": "deterministic",
                                    "candidates": [{"id": "c9", "description": "Click 9", "tool": "click", "element_index": 7}],
                                    "expected_postcondition": {"static_text_equals": "789"},
                                },
                            ],
                        },
                    },
                })
            finally:
                server.close()

            sc = resp["result"]["structuredContent"]
            self.assertTrue(resp["result"]["isError"])
            self.assertEqual(sc["status"], "postcondition_unmet")
            self.assertFalse(sc["verified"])
            self.assertEqual(sc["completed_count"], 1)
            self.assertEqual(len(sc["completed_steps"]), 1)
            self.assertEqual(sc["completed_steps"][0]["step_index"], 0)
            self.assertEqual(sc["completed_steps"][0]["status"], "verified")
            self.assertIsNotNone(sc["failed_step"])
            self.assertEqual(sc["failed_step"]["step_index"], 1)
            self.assertEqual(sc["failed_step"]["status"], "postcondition_unmet")
            self.assertEqual(sc["failed_step"]["verification_outcome"], "state_changed")
            self.assertEqual(len(sc["remaining_steps"]), 1)
            self.assertEqual(sc["remaining_steps"][0]["step_index"], 2)
            self.assertEqual(sc["remaining_steps"][0]["goal"], "Click 9")

    def test_jev_batch_partial_failure_timeout_latches_outcome_unknown_and_halts(self):
        with tempfile.TemporaryDirectory() as tmp:
            partial_driver = os.path.join(tmp, "partial-timeout-driver")
            with open(partial_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent("""\
                    #!/usr/bin/env python3
                    import json, sys, time
                    snap_seq = 10
                    display = "0"
                    click_count = 0
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            res = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "partial-timeout"}}
                        elif method == "tools/call":
                            params = msg.get("params", {})
                            name = params.get("name")
                            args = params.get("arguments", {})
                            if name == "get_window_state":
                                snap_seq += 1
                                snap = f"s000000{snap_seq}"
                                md = (
                                    f'- [0] AXWindow "Calculator"\\n'
                                    f'  - AXStaticText = "{display}"\\n'
                                    f'  - [5] AXButton (7)\\n'
                                    f'  - [6] AXButton (8)\\n'
                                    f'  - [7] AXButton (9)'
                                )
                                res = {
                                    "content": [{"type": "text", "text": md}],
                                    "structuredContent": {
                                        "pid": 42,
                                        "window_id": 100,
                                        "snapshot_id": snap,
                                        "tree_markdown": md,
                                        "elements": [
                                            {"element_index": 0, "role": "AXWindow", "label": "Calculator"},
                                            {"element_index": 5, "role": "AXButton", "label": "7", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                                            {"element_index": 6, "role": "AXButton", "label": "8", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                                            {"element_index": 7, "role": "AXButton", "label": "9", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                                        ],
                                    },
                                }
                            elif name == "click":
                                click_count += 1
                                if click_count == 1:
                                    display = "7"
                                    res = {"content": [{"type": "text", "text": "clicked 7"}], "structuredContent": {"ok": True}}
                                else:
                                    # Hang mid-dispatch on step 1
                                    sys.stdout.write('{"jsonrpc": "2.0"')
                                    sys.stdout.flush()
                                    time.sleep(30)
                            else:
                                res = {"content": [{"type": "text", "text": "ok"}], "structuredContent": {"ok": True}}
                        else:
                            res = {}
                        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
                """))
            os.chmod(partial_driver, os.stat(partial_driver).st_mode | stat.S_IEXEC)

            server = jev.JevMcpServer(
                env={
                    "AGY_CUA_DRIVER": partial_driver,
                    "AGY_CUA_DRIVER_TIMEOUT_MS": "250",
                    "TYPESAFE_API_KEY": "test-key",
                },
                keychain_reader=lambda _: None,
            )
            try:
                resp = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_batch",
                        "arguments": {
                            "pid": 42,
                            "window_id": 100,
                            "steps": [
                                {
                                    "goal": "Click 7",
                                    "mode": "deterministic",
                                    "candidates": [{"id": "c7", "description": "Click 7", "tool": "click", "element_index": 5}],
                                    "expected_postcondition": {"static_text_equals": "7"},
                                },
                                {
                                    "goal": "Click 8",
                                    "mode": "deterministic",
                                    "candidates": [{"id": "c8", "description": "Click 8", "tool": "click", "element_index": 6}],
                                    "expected_postcondition": {"static_text_equals": "78"},
                                },
                                {
                                    "goal": "Click 9",
                                    "mode": "deterministic",
                                    "candidates": [{"id": "c9", "description": "Click 9", "tool": "click", "element_index": 7}],
                                    "expected_postcondition": {"static_text_equals": "789"},
                                },
                            ],
                        },
                    },
                })
                sc = resp["result"]["structuredContent"]
                self.assertTrue(resp["result"]["isError"])
                self.assertEqual(sc["verification_outcome"], "outcome_unknown")
                self.assertTrue(sc["uncertain_side_effect"])
                self.assertEqual(sc["completed_count"], 1)
                self.assertEqual(sc["failed_step"]["step_index"], 1)
                self.assertEqual(sc["failed_step"]["verification_outcome"], "outcome_unknown")
                self.assertTrue(sc["failed_step"]["uncertain_side_effect"])
                self.assertEqual(len(sc["remaining_steps"]), 1)
                self.assertEqual(sc["remaining_steps"][0]["step_index"], 2)
                self.assertTrue(sc["accounting"]["halted"])
                self.assertEqual(sc["accounting"]["uncertain_side_effects"], 1)

                # Subsequent replay attempt is immediately refused because session is halted
                replay = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_batch",
                        "arguments": {
                            "pid": 42,
                            "window_id": 100,
                            "steps": [
                                {
                                    "goal": "Click 7",
                                    "mode": "deterministic",
                                    "candidates": [{"id": "c7", "description": "Click 7", "tool": "click", "element_index": 5}],
                                    "expected_postcondition": {"static_text_equals": "7"},
                                }
                            ],
                        },
                    },
                })
                self.assertEqual(replay["result"]["structuredContent"]["status"], "halted")
            finally:
                server.close()

    def test_session_bookkeeping_conflict_arbitration_and_end_session_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_driver = os.path.join(tmp, "session-driver")
            log_file = os.path.join(tmp, "session-calls.jsonl")
            with open(session_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(f"""\
                    #!/usr/bin/env python3
                    import json, sys
                    snap_seq = 10
                    log_path = {log_file!r}
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            res = {{"protocolVersion": "2025-06-18", "capabilities": {{}}, "serverInfo": {{"name": "session-driver"}}}}
                        elif method == "tools/call":
                            params = msg.get("params", {{}})
                            name = params.get("name")
                            args = params.get("arguments", {{}})
                            with open(log_path, "a", encoding="utf-8") as lf:
                                lf.write(json.dumps({{"name": name, "args": args}}) + "\\n")
                            if name == "get_window_state":
                                snap_seq += 1
                                snap = f"s000000{{snap_seq}}"
                                val = "7" if snap_seq % 2 == 0 else "0"
                                md = f'- [0] AXWindow "Calculator"\\n  - AXStaticText = "{{val}}"\\n  - [5] AXButton (7)'
                                res = {{
                                    "content": [{{"type": "text", "text": md}}],
                                    "structuredContent": {{
                                        "pid": args.get("pid", 42),
                                        "window_id": args.get("window_id", 100),
                                        "snapshot_id": snap,
                                        "tree_markdown": md,
                                        "elements": [
                                            {{"element_index": 0, "role": "AXWindow", "label": "Calculator"}},
                                            {{"element_index": 5, "role": "AXButton", "label": "7", "enabled": True, "parent_index": 0, "actions": ["AXPress"]}},
                                        ],
                                    }},
                                }}
                            elif name == "click":
                                res = {{"content": [{{"type": "text", "text": "clicked 7"}}], "structuredContent": {{"ok": True}}}}
                            elif name == "end_session":
                                res = {{"content": [{{"type": "text", "text": "session ended"}}], "structuredContent": {{"ok": True}}}}
                            else:
                                res = {{"content": [{{"type": "text", "text": "ok"}}], "structuredContent": {{"ok": True}}}}
                        else:
                            res = {{}}
                        print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": res}}), flush=True)
                """))
            os.chmod(session_driver, os.stat(session_driver).st_mode | stat.S_IEXEC)

            def fake_http(_req, _t):
                return 200, json.dumps({
                    "model": "jev-1.13.0",
                    "answers": {
                        "candidate": {
                            "type": "choice",
                            "choice": "ax.5.7",
                            "confidence": 0.99,
                            "probabilities": {"ax.5.7": 0.99, "reobserve": 0.005, "abstain": 0.005},
                        }
                    },
                    "usage": {"input_tokens": 50, "output_tokens": 5},
                })

            server_a = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": session_driver, "TYPESAFE_API_KEY": "test-key", "AGY_CUA_SESSION_ID": "jev-client-a"},
                keychain_reader=lambda _: None,
                http_post=fake_http,
            )
            server_b = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": session_driver, "TYPESAFE_API_KEY": "test-key", "AGY_CUA_SESSION_ID": "jev-client-b"},
                keychain_reader=lambda _: None,
                http_post=fake_http,
            )
            try:
                self.assertEqual(server_a.session_id, "jev-client-a")
                self.assertEqual(server_b.session_id, "jev-client-b")

                # Client A acts on (pid=42, window_id=100)
                resp_a = server_a.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Click 7",
                            "pid": 42,
                            "window_id": 100,
                            "expected_postcondition": {"static_text_equals": "7"},
                        },
                    },
                })
                self.assertEqual(resp_a["result"]["structuredContent"]["status"], "verified")
                self.assertEqual(resp_a["result"]["structuredContent"]["session_id"], "jev-client-a")

                # Client B attempting to act on the same (pid=42, window_id=100) while Client A owns it is refused
                resp_b_conflict = server_b.handle_message({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Click 7",
                            "pid": 42,
                            "window_id": 100,
                            "expected_postcondition": {"static_text_equals": "7"},
                        },
                    },
                })
                self.assertTrue(resp_b_conflict["result"]["isError"])
                self.assertEqual(resp_b_conflict["result"]["structuredContent"]["status"], "target_conflict")

                # Client B can act on a disjoint window (pid=42, window_id=200)
                resp_b_other = server_b.handle_message({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Click 7",
                            "pid": 42,
                            "window_id": 200,
                            "expected_postcondition": {"static_text_equals": "7"},
                        },
                    },
                })
                self.assertEqual(resp_b_other["result"]["structuredContent"]["status"], "verified")

                # Closing Client A sends end_session for jev-client-a and releases (pid=42, window_id=100)
                server_a.close()

                resp_b_after_close = server_b.handle_message({
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Click 7",
                            "pid": 42,
                            "window_id": 100,
                            "expected_postcondition": {"static_text_equals": "7"},
                        },
                    },
                })
                self.assertEqual(resp_b_after_close["result"]["structuredContent"]["status"], "verified")

                # Stale expected_snapshot_id is refused before dispatch
                resp_b_stale = server_b.handle_message({
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Click 7",
                            "pid": 42,
                            "window_id": 100,
                            "expected_snapshot_id": "s00000001",
                            "expected_postcondition": {"static_text_equals": "7"},
                        },
                    },
                })
                self.assertTrue(resp_b_stale["result"]["isError"])
                self.assertEqual(resp_b_stale["result"]["structuredContent"]["status"], "stale_snapshot")
            finally:
                server_a.close()
                server_b.close()

            with open(log_file, "r", encoding="utf-8") as lf:
                logged = [json.loads(line) for line in lf if line.strip()]
            end_sessions = [entry["args"].get("session") for entry in logged if entry["name"] == "end_session"]
            self.assertIn("jev-client-a", end_sessions)
            self.assertIn("jev-client-b", end_sessions)


if __name__ == "__main__":
    unittest.main()
