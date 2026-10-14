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

    def test_mail_toolbar_prioritized_over_deep_outline_and_discloses_omissions(self):
        elements = [
            {"element_index": 0, "role": "AXWindow", "label": "Inbox — Mail", "enabled": True},
            {"element_index": 1, "role": "AXSplitGroup", "label": "", "enabled": True, "parent_index": 0},
            {"element_index": 2, "role": "AXScrollArea", "label": "mailboxes", "enabled": True, "parent_index": 1},
            {"element_index": 3, "role": "AXOutline", "label": "mailboxes", "enabled": True, "parent_index": 2},
        ]
        # 80 deep outline rows/buttons (indices 4..83) before shallow AXToolbar (indices 90..92)
        for i in range(4, 84):
            if i % 2 == 0:
                elements.append({
                    "element_index": i,
                    "role": "AXRow",
                    "label": f"Mailbox Row {i}",
                    "enabled": True,
                    "parent_index": 3,
                    "actions": ["AXPress"],
                })
            else:
                elements.append({
                    "element_index": i,
                    "role": "AXButton",
                    "label": f"Unread Count {i}",
                    "enabled": True,
                    "parent_index": i - 1,
                    "actions": ["AXPress"],
                })
        elements.extend([
            {"element_index": 90, "role": "AXToolbar", "label": "Toolbar", "enabled": True, "parent_index": 0},
            {
                "element_index": 91,
                "role": "AXButton",
                "label": "New Message",
                "enabled": True,
                "parent_index": 90,
                "actions": ["AXPress"],
            },
            {
                "element_index": 92,
                "role": "AXSearchField",
                "label": "Search",
                "value": "",
                "enabled": True,
                "parent_index": 90,
                "actions": ["AXPress"],
            },
        ])

        obs = jev.extract_window_observation(
            {
                "structuredContent": {
                    "pid": 501,
                    "window_id": 2001,
                    "snapshot_id": "s0000beef",
                    "app_name": "Mail",
                    "window_title": "Inbox — Mail",
                    "elements_complete": True,
                    "truncated": False,
                    "elements": elements,
                },
                "content": [{"type": "text", "text": '- [0] AXWindow "Inbox — Mail"'}],
            },
            expected_pid=501,
            expected_window_id=2001,
        )

        # Shallow toolbar controls [91] and [92] must be prioritized ahead of deep outline rows
        self.assertEqual(obs["elements"][0]["element_index"], 91)
        self.assertEqual(obs["elements"][1]["element_index"], 92)
        self.assertTrue(obs["elements"][0]["in_toolbar"])
        self.assertTrue(obs["elements"][1]["in_toolbar"])

        cands = jev.build_bound_candidates(None, obs)
        cand_ids = [c["id"] for c in cands]
        self.assertEqual(cand_ids[0], "ax.91.new-message")
        self.assertEqual(cand_ids[1], "ax.92.search")

        # Omission counts must be disclosed when 82 actionable elements exceed MAX_ELEMENTS=64 and MAX_AUTO_CANDIDATES=28
        self.assertEqual(obs["total_actionable_elements"], 82)
        self.assertEqual(obs["omitted_elements_count"], 82 - jev.MAX_ELEMENTS)
        self.assertEqual(obs["omitted_candidates_count"], 82 - jev.MAX_AUTO_CANDIDATES)
        self.assertTrue(obs["observation_truncated"])

        # Explicit candidate targeting a valid in-snapshot element beyond the 64-element prompt cap must still bind and verify
        explicit_deep = jev.build_bound_candidates(
            [{"id": "click.deep.83", "description": "Click deep unread button 83", "tool": "click", "element_index": 83}],
            obs,
        )
        self.assertEqual(explicit_deep[0]["arguments"]["element_token"], "s0000beef:83")

        post_elements = [
            dict(el, label="Unread Count 83 (Selected)") if el.get("element_index") == 83 else dict(el)
            for el in elements
        ]
        post_obs = jev.extract_window_observation(
            {
                "structuredContent": {
                    "pid": 501,
                    "window_id": 2001,
                    "snapshot_id": "s0000bef0",
                    "elements": post_elements,
                },
                "content": [{"type": "text", "text": '- [0] AXWindow "Inbox — Mail"'}],
            },
            expected_pid=501,
            expected_window_id=2001,
        )
        changed, met, reason = jev.check_postcondition(
            {"element_label_equals": "Unread Count 83 (Selected)"},
            obs,
            post_obs,
            target_element_index=83,
        )
        self.assertTrue(changed)
        self.assertTrue(met)
        self.assertEqual(reason, "expected_postcondition_verified")

        # Also verify tree_markdown indentation fallback when parent_index is omitted
        md_only_obs = jev.extract_window_observation(
            {
                "structuredContent": {
                    "pid": 501,
                    "window_id": 2001,
                    "snapshot_id": "s0000bef1",
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "Inbox"},
                        {"element_index": 10, "role": "AXRow", "label": "Deep row", "enabled": True, "actions": ["AXPress"]},
                        {"element_index": 20, "role": "AXButton", "label": "New Message", "enabled": True, "actions": ["AXPress"]},
                    ],
                    "tree_markdown": (
                        '- [0] AXWindow "Inbox"\n'
                        '  - [1] AXSplitGroup\n'
                        '    - [2] AXScrollArea\n'
                        '      - [3] AXOutline\n'
                        '        - [10] AXRow "Deep row"\n'
                        '  - [19] AXToolbar\n'
                        '    - [20] AXButton "New Message"\n'
                    ),
                },
                "content": [],
            },
            expected_pid=501,
            expected_window_id=2001,
        )
        self.assertEqual(md_only_obs["elements"][0]["element_index"], 20)
        self.assertTrue(md_only_obs["elements"][0]["in_toolbar"])
        self.assertEqual(md_only_obs["elements"][0]["depth"], 2)
        self.assertEqual(md_only_obs["elements"][1]["element_index"], 10)
        self.assertFalse(md_only_obs["elements"][1]["in_toolbar"])
        self.assertEqual(md_only_obs["elements"][1]["depth"], 4)

    def test_mail_role_and_action_support_filtering_for_search_link_and_cell(self):
        obs = jev.extract_window_observation(
            {
                "structuredContent": {
                    "pid": 501,
                    "window_id": 2001,
                    "snapshot_id": "s0000cafe",
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "Mail", "enabled": True},
                        # Non-pressable AXCell must not become a click candidate
                        {"element_index": 10, "role": "AXCell", "label": "Sender cell", "enabled": True, "parent_index": 0, "actions": []},
                        # Pressable AXCell is allowed
                        {"element_index": 11, "role": "AXCell", "label": "Flagged cell", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                        # Pressable enabled AXLink is allowed
                        {"element_index": 12, "role": "AXLink", "label": "Open thread link", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                        # Disabled AXLink must be excluded
                        {"element_index": 13, "role": "AXLink", "label": "Disabled link", "enabled": False, "parent_index": 0, "actions": ["AXPress"]},
                        # Non-pressable AXLink (explicit actions without AXPress) must not generate click candidate
                        {"element_index": 14, "role": "AXLink", "label": "Static link", "enabled": True, "parent_index": 0, "actions": ["AXShowMenu"]},
                        # Enabled AXSearchField (even with empty actions list) supports both focus-click and explicit set_value / type_text
                        {"element_index": 15, "role": "AXSearchField", "label": "Search", "value": "", "enabled": True, "parent_index": 0, "actions": []},
                        # Disabled AXSearchField must be excluded
                        {"element_index": 16, "role": "AXSearchField", "label": "Disabled Search", "value": "", "enabled": False, "parent_index": 0, "actions": ["AXPress"]},
                        # Enabled AXSearchField with explicit non-clickable action only (AXCancel) skips auto-click but allows set_value / type_text
                        {"element_index": 17, "role": "AXSearchField", "label": "Filter", "value": "", "enabled": True, "parent_index": 0, "actions": ["AXCancel"]},
                        # Enabled AXScrollArea and AXWebArea support explicit scroll / type_text without polluting auto-click candidates
                        {"element_index": 18, "role": "AXScrollArea", "label": "Message list scroll", "enabled": True, "parent_index": 0, "actions": []},
                        {"element_index": 19, "role": "AXWebArea", "label": "Message body", "enabled": True, "parent_index": 0, "actions": []},
                        {"element_index": 20, "role": "AXSlider", "label": "Zoom level", "value": "100", "enabled": True, "parent_index": 0, "actions": ["AXIncrement", "AXDecrement"]},
                    ],
                },
                "content": [{"type": "text", "text": '- [0] AXWindow "Mail"'}],
            },
            expected_pid=501,
            expected_window_id=2001,
        )

        auto_cands = jev.build_bound_candidates(None, obs)
        auto_indices = [c["arguments"]["element_index"] for c in auto_cands]
        self.assertEqual(auto_indices, [11, 12, 15])

        # Enabled AXSearchField [15] and [17] support explicit set_value and type_text candidates,
        # and enabled AXCell [10], AXScrollArea [18], AXWebArea [19], and AXSlider [20] support valid explicit actions
        search_cands = jev.build_bound_candidates(
            [
                {"id": "search.set", "description": "Set Search to invoice", "tool": "set_value", "element_index": 15, "value": "invoice"},
                {"id": "search.type", "description": "Type invoice into Search", "tool": "type_text", "element_index": 15, "text": "invoice"},
                {"id": "filter.set", "description": "Set Filter to unread", "tool": "set_value", "element_index": 17, "value": "unread"},
                {"id": "cell.dbl", "description": "Double-click sender cell to open message", "tool": "double_click", "element_index": 10},
                {"id": "cell.ctx", "description": "Right-click sender cell for menu", "tool": "right_click", "element_index": 10},
                {"id": "list.scroll", "description": "Scroll message list down", "tool": "scroll", "element_index": 18, "direction": "down"},
                {"id": "body.type", "description": "Type draft body into AXWebArea", "tool": "type_text", "element_index": 19, "text": "Hello"},
                {"id": "slider.click", "description": "Click zoom slider", "tool": "click", "element_index": 20},
            ],
            obs,
        )
        self.assertEqual(len(search_cands), 8)
        self.assertEqual(search_cands[0]["arguments"]["element_token"], "s0000cafe:15")
        self.assertEqual(search_cands[1]["arguments"]["element_token"], "s0000cafe:15")
        self.assertEqual(search_cands[2]["arguments"]["element_token"], "s0000cafe:17")
        self.assertEqual(search_cands[3]["arguments"]["element_token"], "s0000cafe:10")
        self.assertEqual(search_cands[4]["arguments"]["element_token"], "s0000cafe:10")
        self.assertEqual(search_cands[5]["arguments"]["element_token"], "s0000cafe:18")
        self.assertEqual(search_cands[6]["arguments"]["element_token"], "s0000cafe:19")
        self.assertEqual(search_cands[7]["arguments"]["element_token"], "s0000cafe:20")

        # Clicking AXSearchField [17] (which only supports AXCancel) is rejected
        with self.assertRaisesRegex(ValueError, "unsupported_element_action"):
            jev.build_bound_candidates(
                [{"id": "bad.filter.click", "description": "Click non-clickable search field", "tool": "click", "element_index": 17}],
                obs,
            )

        # Non-pressable AXCell [10] must be rejected specifically with unsupported_element_action on click,
        # and disabled AXSearchField [16] must be rejected with unknown_element_index
        with self.assertRaisesRegex(ValueError, "unsupported_element_action"):
            jev.build_bound_candidates(
                [{"id": "bad.cell", "description": "Click non-pressable cell", "tool": "click", "element_index": 10}],
                obs,
            )
        with self.assertRaisesRegex(ValueError, "unknown_element_index"):
            jev.build_bound_candidates(
                [{"id": "bad.search", "description": "Set disabled search", "tool": "set_value", "element_index": 16, "value": "x"}],
                obs,
            )

        # Incompatible tool for role (e.g. set_value or type_text on AXLink or AXCell) must be rejected
        with self.assertRaisesRegex(ValueError, "incompatible_tool_for_role"):
            jev.build_bound_candidates(
                [{"id": "bad.link.set", "description": "Set value on link", "tool": "set_value", "element_index": 12, "value": "x"}],
                obs,
            )
        with self.assertRaisesRegex(ValueError, "incompatible_tool_for_role"):
            jev.build_bound_candidates(
                [{"id": "bad.link.type", "description": "Type text on link", "tool": "type_text", "element_index": 12, "text": "x"}],
                obs,
            )
        with self.assertRaisesRegex(ValueError, "incompatible_tool_for_role"):
            jev.build_bound_candidates(
                [{"id": "bad.cell.set", "description": "Set value on cell", "tool": "set_value", "element_index": 11, "value": "x"}],
                obs,
            )

        # validate_select_request preserves value, enabled, depth, and in_toolbar metadata
        validated = jev.validate_select_request({
            "goal": "Set Search",
            "pid": 501,
            "window_id": 2001,
            "snapshot_id": "s0000cafe",
            "elements": [
                {
                    "element_index": 15,
                    "role": "AXSearchField",
                    "label": "Search",
                    "value": "invoice",
                    "state": "enabled (has_value)",
                    "enabled": True,
                    "depth": 2,
                    "in_toolbar": True,
                }
            ],
            "candidates": [{"id": "search.set", "description": "Set Search"}],
        })
        self.assertEqual(validated["elements"][0]["value"], "invoice")
        self.assertTrue(validated["elements"][0]["enabled"])
        self.assertEqual(validated["elements"][0]["depth"], 2)
        self.assertTrue(validated["elements"][0]["in_toolbar"])

    def test_jev_step_forwards_query_and_max_depth_to_stage1_and_stage5_for_mail_continuity(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_mail_driver = os.path.join(tmp, "fake-mail-driver")
            calls_log = os.path.join(tmp, "driver_calls.jsonl")
            with open(fake_mail_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(f"""\
                    #!/usr/bin/env python3
                    import json, sys

                    step = 0
                    search_val = ""
                    log_path = {calls_log!r}

                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            res = {{"protocolVersion": "2025-06-18", "capabilities": {{}}, "serverInfo": {{"name": "fake-mail"}}}}
                        elif method == "tools/call":
                            params = msg.get("params", {{}})
                            name = params.get("name")
                            args = params.get("arguments", {{}})
                            with open(log_path, "a", encoding="utf-8") as lf:
                                lf.write(json.dumps({{"name": name, "arguments": args}}) + "\\n")
                            if name == "get_window_state":
                                step += 1
                                snap = f"s0000002{{step}}"
                                # Simulate Mail starvation unless scoped with max_depth=2 and query="Search"
                                if args.get("max_depth") == 2 and args.get("query") == "Search":
                                    md = f'- [0] AXWindow "Inbox"\\n  - [90] AXToolbar\\n    - [92] AXSearchField (Search) = "{{search_val}}"'
                                    els = [
                                        {{"element_index": 0, "role": "AXWindow", "label": "Inbox"}},
                                        {{"element_index": 90, "role": "AXToolbar", "label": "Toolbar", "parent_index": 0}},
                                        {{
                                            "element_index": 92,
                                            "role": "AXSearchField",
                                            "label": "Search",
                                            "value": search_val,
                                            "enabled": True,
                                            "parent_index": 90,
                                            "actions": ["AXPress"],
                                        }},
                                    ]
                                    truncated = False
                                    complete = True
                                else:
                                    md = '- [0] AXWindow "Inbox"\\n  - [4] AXRow "Deep row"'
                                    els = [
                                        {{"element_index": 0, "role": "AXWindow", "label": "Inbox"}},
                                        {{"element_index": 4, "role": "AXRow", "label": "Deep row", "enabled": True, "parent_index": 0, "actions": ["AXPress"]}},
                                    ]
                                    truncated = True
                                    complete = False
                                res = {{
                                    "content": [{{"type": "text", "text": md}}],
                                    "structuredContent": {{
                                        "pid": args.get("pid", 501),
                                        "window_id": args.get("window_id", 2001),
                                        "snapshot_id": snap,
                                        "app_name": "Mail",
                                        "window_title": "Inbox",
                                        "elements_complete": complete,
                                        "truncated": truncated,
                                        "tree_markdown": md,
                                        "elements": els,
                                    }},
                                }}
                            elif name == "set_value":
                                search_val = str(args.get("value", ""))
                                res = {{"content": [{{"type": "text", "text": "value set"}}], "structuredContent": {{"ok": True}}}}
                            else:
                                res = {{"content": [{{"type": "text", "text": "ok"}}], "structuredContent": {{"ok": True}}}}
                        else:
                            res = {{}}
                        print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": res}}), flush=True)
                """))
            os.chmod(fake_mail_driver, os.stat(fake_mail_driver).st_mode | stat.S_IEXEC)

            captured_wire_payloads = []

            def fake_http_post(req, _t):
                captured_wire_payloads.append(json.loads(req.data.decode("utf-8")))
                return 200, json.dumps({
                    "model": "jev-1.13.0",
                    "answers": {
                        "candidate": {
                            "type": "choice",
                            "choice": "set.search",
                            "confidence": 0.98,
                            "probabilities": {"set.search": 0.98, "reobserve": 0.01, "abstain": 0.01},
                        }
                    },
                    "usage": {"input_tokens": 80, "output_tokens": 10},
                })

            server = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": fake_mail_driver, "TYPESAFE_API_KEY": "test-key"},
                keychain_reader=lambda _: None,
                http_post=fake_http_post,
            )
            try:
                # Invalid query / max_depth are refused before touching driver
                bad_q = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {"goal": "Search", "pid": 501, "window_id": 2001, "query": "   "},
                    },
                })
                self.assertEqual(bad_q["result"]["structuredContent"]["reason"], "invalid_query")

                bad_d = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {"goal": "Search", "pid": 501, "window_id": 2001, "max_depth": 15},
                    },
                })
                self.assertEqual(bad_d["result"]["structuredContent"]["reason"], "invalid_max_depth")

                resp = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Set Search field to quarterly report",
                            "pid": 501,
                            "window_id": 2001,
                            "query": "Search",
                            "max_depth": 2,
                            "candidates": [
                                {
                                    "id": "set.search",
                                    "description": 'Set [92] AXSearchField "Search" to quarterly report',
                                    "tool": "set_value",
                                    "element_index": 92,
                                    "value": "quarterly report",
                                }
                            ],
                            "expected_postcondition": {"element_value_equals": "quarterly report"},
                        },
                    },
                })
            finally:
                server.close()

            sc = resp["result"]["structuredContent"]
            self.assertEqual(sc["status"], "verified")
            self.assertEqual(sc["verification_outcome"], "goal_verified")
            self.assertTrue(sc["verified"])
            self.assertEqual(sc["verification_reason"], "expected_postcondition_verified")
            self.assertEqual(sc["total_actionable_elements"], 1)
            self.assertEqual(sc["omitted_elements_count"], 0)
            self.assertEqual(sc["omitted_candidates_count"], 0)
            self.assertFalse(sc["pre_observation_truncated"])
            self.assertEqual(sc["post_total_actionable_elements"], 1)
            self.assertEqual(sc["post_omitted_elements_count"], 0)
            self.assertEqual(sc["post_omitted_candidates_count"], 0)
            self.assertFalse(sc["post_observation_truncated"])
            self.assertFalse(sc["observation_truncated"])

            # Verify Jev wire payload preserved depth, in_toolbar, and value metadata
            self.assertEqual(len(captured_wire_payloads), 1)
            sent_el = captured_wire_payloads[0]["state"]["elements"][0]
            self.assertEqual(sent_el["element_index"], 92)
            self.assertEqual(sent_el["depth"], 2)
            self.assertTrue(sent_el["in_toolbar"])
            self.assertEqual(sent_el["value"], "")

            with open(calls_log, "r", encoding="utf-8") as lf:
                logged = [json.loads(line) for line in lf if line.strip()]
            obs_calls = [c["arguments"] for c in logged if c["name"] == "get_window_state"]
            self.assertEqual(len(obs_calls), 2)
            for call_args in obs_calls:
                self.assertEqual(call_args.get("query"), "Search")
                self.assertEqual(call_args.get("max_depth"), 2)


if __name__ == "__main__":
    unittest.main()




