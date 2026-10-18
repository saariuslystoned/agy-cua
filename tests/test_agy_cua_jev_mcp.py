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
                    "AGY_CUA_DRIVER_TIMEOUT_MS": "800",
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

            # Cross-process target conflict via shared target state file (alive external PID vs dead PID)
            state_file = os.path.join(tmp, "targets.json")
            sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                with open(state_file, "w", encoding="utf-8") as sf:
                    json.dump(
                        {
                            "42:100": {
                                "session_id": "jev-external-live",
                                "server_pid": sleeper.pid,
                                "last_snapshot_id": "s00000010",
                            },
                            "42:300": {
                                "session_id": "jev-external-dead",
                                "server_pid": 999_999_999,
                                "last_snapshot_id": "s00000009",
                            },
                        },
                        sf,
                    )
                server_c = jev.JevMcpServer(
                    env={
                        "AGY_CUA_DRIVER": session_driver,
                        "TYPESAFE_API_KEY": "test-key",
                        "AGY_CUA_SESSION_ID": "jev-client-c",
                        "AGY_CUA_TARGET_STATE_FILE": state_file,
                    },
                    keychain_reader=lambda _: None,
                    http_post=fake_http,
                )
                try:
                    ext_conflict = server_c.handle_message({
                        "jsonrpc": "2.0",
                        "id": 10,
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
                    self.assertTrue(ext_conflict["result"]["isError"])
                    self.assertEqual(ext_conflict["result"]["structuredContent"]["status"], "target_conflict")
                    self.assertIn("jev-external-live", ext_conflict["result"]["structuredContent"]["reason"])

                    # Dead external PID on (42, 300) is pruned and acquired cleanly
                    dead_reclaim = server_c.handle_message({
                        "jsonrpc": "2.0",
                        "id": 11,
                        "method": "tools/call",
                        "params": {
                            "name": "jev_step",
                            "arguments": {
                                "goal": "Click 7",
                                "pid": 42,
                                "window_id": 300,
                                "expected_postcondition": {"static_text_equals": "7"},
                            },
                        },
                    })
                    self.assertEqual(dead_reclaim["result"]["structuredContent"]["status"], "verified")

                    # Mixed element + non-element candidates without explicit element_index in expected_postcondition are refused
                    mixed_refused = server_c.handle_message({
                        "jsonrpc": "2.0",
                        "id": 12,
                        "method": "tools/call",
                        "params": {
                            "name": "jev_batch",
                            "arguments": {
                                "pid": 42,
                                "window_id": 300,
                                "steps": [
                                    {
                                        "goal": "Submit form",
                                        "candidates": [
                                            {"id": "c_el", "description": "Click field", "tool": "click", "element_index": 5},
                                            {"id": "c_key", "description": "Press Return", "tool": "press_key", "key": "return"},
                                        ],
                                        "expected_postcondition": {"element_value_equals": "done"},
                                    }
                                ],
                            },
                        },
                    })
                    self.assertTrue(mixed_refused["result"]["isError"])
                    self.assertEqual(mixed_refused["result"]["structuredContent"]["status"], "refused")
                    self.assertIn("unbound_element_postcondition", mixed_refused["result"]["structuredContent"]["reason"])
                finally:
                    server_c.close()
            finally:
                sleeper.terminate()
                sleeper.wait(timeout=2)

            self.assertEqual(jev.clamp_batch_timeout_ms(25), jev.MIN_BATCH_TIMEOUT_MS)
            self.assertEqual(jev.clamp_batch_timeout_ms(0), jev.DEFAULT_BATCH_TIMEOUT_MS)

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

        # Also verify tree_markdown indentation fallback when parent_index is omitted or containers are unindexed
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
                        {"element_index": 30, "role": "AXButton", "label": "Sidebar Action", "enabled": True, "actions": ["AXPress"]},
                    ],
                    "tree_markdown": (
                        '- [0] AXWindow "Inbox"\n'
                        '  - AXSplitGroup\n'
                        '    - [2] AXScrollArea\n'
                        '      - [3] AXOutline\n'
                        '        - [10] AXRow "Deep row"\n'
                        '  - AXToolbar\n'
                        '    - [20] AXButton "New Message"\n'
                        '  - AXSplitGroup\n'
                        '    - [30] AXButton "Sidebar Action"\n'
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
        self.assertEqual(md_only_obs["elements"][1]["element_index"], 30)
        self.assertFalse(md_only_obs["elements"][1]["in_toolbar"])
        self.assertEqual(md_only_obs["elements"][1]["depth"], 2)
        self.assertEqual(md_only_obs["elements"][2]["element_index"], 10)
        self.assertFalse(md_only_obs["elements"][2]["in_toolbar"])
        self.assertEqual(md_only_obs["elements"][2]["depth"], 4)

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
                        # Secure field reported as role=AXTextField + subrole=AXSecureTextField must redact value and skip auto-click
                        {
                            "element_index": 21,
                            "role": "AXTextField",
                            "subrole": "AXSecureTextField",
                            "title": "Password",
                            "value": "s3cr3t!",
                            "enabled": True,
                            "parent_index": 0,
                            "actions": ["AXConfirm"],
                        },
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

        # Verify secure subrole redacted its value in extracted elements
        sec_els = [e for e in obs["all_elements"] if e["element_index"] == 21]
        self.assertEqual(len(sec_els), 1)
        self.assertNotIn("value", sec_els[0])
        self.assertEqual(sec_els[0]["label"], "Password")

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

        # validate_select_request preserves enabled, depth, and in_toolbar metadata while rejecting raw value exposure
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
                    "state": "enabled (has_value)",
                    "enabled": True,
                    "depth": 2,
                    "in_toolbar": True,
                }
            ],
            "candidates": [{"id": "search.set", "description": "Set Search"}],
        })
        self.assertTrue(validated["elements"][0]["enabled"])
        self.assertEqual(validated["elements"][0]["depth"], 2)
        self.assertTrue(validated["elements"][0]["in_toolbar"])
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            jev.validate_select_request({
                "goal": "Set Search",
                "pid": 501,
                "window_id": 2001,
                "snapshot_id": "s0000cafe",
                "elements": [
                    {
                        "element_index": 15,
                        "role": "AXSearchField",
                        "label": "Search",
                        "value": "secret-or-private-value",
                    }
                ],
                "candidates": [{"id": "search.set", "description": "Set Search"}],
            })

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

            # Verify Jev wire payload preserved depth and in_toolbar metadata without leaking raw value
            self.assertEqual(len(captured_wire_payloads), 1)
            sent_el = captured_wire_payloads[0]["state"]["elements"][0]
            self.assertEqual(sent_el["element_index"], 92)
            self.assertEqual(sent_el["depth"], 2)
            self.assertTrue(sent_el["in_toolbar"])
            self.assertNotIn("value", sent_el)

            with open(calls_log, "r", encoding="utf-8") as lf:
                logged = [json.loads(line) for line in lf if line.strip()]
            obs_calls = [c["arguments"] for c in logged if c["name"] == "get_window_state"]
            self.assertEqual(len(obs_calls), 2)
            for call_args in obs_calls:
                self.assertEqual(call_args.get("query"), "Search")
                self.assertEqual(call_args.get("max_depth"), 2)

    def test_value_auto_candidates_secure_field_refusal_and_label_continuity(self):
        obs = jev.extract_window_observation(
            {
                "structuredContent": {
                    "pid": 77,
                    "window_id": 500,
                    "snapshot_id": "s00001234",
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "BenchApp — Form"},
                        {"element_index": 3, "role": "AXTextField", "label": "Recipient", "value": "", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                        {"element_index": 4, "role": "AXTextField", "label": "Subject", "value": "", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                        # NSSecureTextField masked with \uf79a bullets without explicit secure subrole
                        {"element_index": 5, "role": "AXTextField", "label": "API Token", "value": "\uf79a\uf79a\uf79a\uf79a", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                        {"element_index": 6, "role": "AXSecureTextField", "label": "Passcode", "value": "topsecret", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                        {"element_index": 7, "role": "AXButton", "label": "Apply", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                    ],
                },
                "content": [{"type": "text", "text": '- [0] AXWindow "BenchApp — Form"'}],
            },
            expected_pid=77,
            expected_window_id=500,
        )

        # Root AXWindow fallback populates window_title when top-level window_title is omitted
        self.assertEqual(obs["window_title"], "BenchApp — Form")
        # Both [5] (\uf79a bullet mask) and [6] (AXSecureTextField) are marked secure and excluded from auto_candidates
        self.assertEqual(obs["secure_element_ids"], {5, 6})
        el_by_id = {e["element_index"]: e for e in obs["all_elements"]}
        self.assertEqual(el_by_id[5]["state"], "enabled (secure)")
        self.assertNotIn("value", el_by_id[5])
        self.assertEqual(el_by_id[6]["state"], "enabled (secure)")
        self.assertNotIn("value", el_by_id[6])

        # Passing action_value generates sv.<idx>.<slug> set_value candidates for non-secure editable fields without leaking value in description
        secret_like_val = "ops-quarterly-sync@example.com"
        cands = jev.build_bound_candidates(None, obs, action_value=secret_like_val)
        cand_by_id = {c["id"]: c for c in cands}
        self.assertIn("sv.3.recipient", cand_by_id)
        self.assertIn("sv.4.subject", cand_by_id)
        self.assertNotIn("sv.5.api-token", cand_by_id)
        self.assertNotIn("sv.6.passcode", cand_by_id)
        self.assertNotIn("ax.5.api-token", cand_by_id)
        self.assertNotIn("ax.6.passcode", cand_by_id)
        self.assertEqual(cand_by_id["sv.3.recipient"]["tool"], "set_value")
        self.assertEqual(cand_by_id["sv.3.recipient"]["arguments"]["value"], secret_like_val)
        self.assertEqual(cand_by_id["sv.3.recipient"]["arguments"]["element_token"], "s00001234:3")
        self.assertNotIn(secret_like_val, cand_by_id["sv.3.recipient"]["description"])

        # Explicit candidate targeting a secure element is refused with secure_element_refused at Stage 2 and Stage 4
        with self.assertRaisesRegex(ValueError, "secure_element_refused"):
            jev.build_bound_candidates(
                [{"id": "set.sec", "description": "Set API Token", "tool": "set_value", "element_index": 5, "value": "x"}],
                obs,
            )
        with self.assertRaisesRegex(ValueError, "secure_element_refused"):
            jev.authorize_candidate_dispatch(
                {"kind": "selected", "selected_id": "tampered.sec", "pid": 77, "window_id": 500, "snapshot_id": "s00001234"},
                [{
                    "id": "tampered.sec",
                    "description": "Click API Token",
                    "pid": 77,
                    "window_id": 500,
                    "snapshot_id": "s00001234",
                    "tool": "click",
                    "arguments": {
                        "pid": 77,
                        "window_id": 500,
                        "snapshot_id": "s00001234",
                        "element_index": 5,
                        "element_token": "s00001234:5",
                        "delivery_mode": "background",
                    },
                }],
                expected_pid=77,
                expected_window_id=500,
                current_snapshot_id="s00001234",
                valid_element_indices=obs["indexed_element_ids"],
                observation=obs,
            )

        # Cross-snapshot element_index re-indexing guard: if element [3] was "Recipient" pre-action but re-indexed to "Subject" post-action, reject with target_element_label_mismatch
        reindexed_post = jev.extract_window_observation(
            {
                "structuredContent": {
                    "pid": 77,
                    "window_id": 500,
                    "snapshot_id": "s00001235",
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "BenchApp — Form"},
                        {"element_index": 3, "role": "AXTextField", "label": "Subject", "value": secret_like_val, "enabled": True, "parent_index": 0},
                    ],
                },
                "content": [],
            },
            expected_pid=77,
            expected_window_id=500,
        )
        changed, met, reason = jev.check_postcondition(
            {"element_value_equals": secret_like_val},
            obs,
            reindexed_post,
            target_element_index=3,
        )
        self.assertTrue(changed)
        self.assertFalse(met)
        self.assertIn("target_element_label_mismatch", reason)

    def test_invoke_menu_and_consequential_commit_gate_across_actuators(self):
        mail_obs = jev.extract_window_observation(
            {
                "structuredContent": {
                    "pid": 501,
                    "window_id": 2001,
                    "snapshot_id": "s0000aaaa",
                    "app_name": "Mail",
                    "window_title": "New Message",
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "New Message"},
                        {"element_index": 10, "role": "AXButton", "label": "Send", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                        {"element_index": 11, "role": "AXButton", "label": "Delete", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                        {"element_index": 12, "role": "AXButton", "label": "Save Draft", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                    ],
                },
                "content": [{"type": "text", "text": '- [0] AXWindow "New Message"'}],
            },
            expected_pid=501,
            expected_window_id=2001,
        )
        # Auto-candidates must filter out Send [10] and Delete [11] while keeping Save Draft [12]
        auto_cands = jev.build_bound_candidates(None, mail_obs)
        self.assertEqual([c["id"] for c in auto_cands], ["ax.12.save-draft"])

        # Explicit click on Send -> mail_send_forbidden_by_safety_policy
        with self.assertRaisesRegex(ValueError, "mail_send_forbidden_by_safety_policy"):
            jev.build_bound_candidates(
                [{"id": "c.send", "description": "Click Send", "tool": "click", "element_index": 10}],
                mail_obs,
            )
        # Explicit click on Delete -> commit_action_forbidden_by_safety_policy
        with self.assertRaisesRegex(ValueError, "commit_action_forbidden_by_safety_policy"):
            jev.build_bound_candidates(
                [{"id": "c.del", "description": "Click Delete", "tool": "click", "element_index": 11}],
                mail_obs,
            )
        # Hotkey cmd+shift+d (Mail Send) and cmd+delete -> refused by commit gate
        with self.assertRaisesRegex(ValueError, "mail_send_forbidden_by_safety_policy"):
            jev.build_bound_candidates(
                [{"id": "hk.send", "description": "Press Send shortcut", "tool": "hotkey", "keys": ["cmd", "shift", "d"]}],
                mail_obs,
            )
        with self.assertRaisesRegex(ValueError, "commit_action_forbidden_by_safety_policy"):
            jev.build_bound_candidates(
                [{"id": "hk.del", "description": "Delete message shortcut", "tool": "hotkey", "keys": ["cmd", "delete"]}],
                mail_obs,
            )
        # invoke_menu high-risk paths (Message > Send, Mailbox > Erase Deleted Items, Finder > Empty Trash) -> refused
        with self.assertRaisesRegex(ValueError, "mail_send_forbidden_by_safety_policy"):
            jev.build_bound_candidates(
                [{"id": "m.send", "description": "Invoke Message > Send", "tool": "invoke_menu", "path": ["Message", "Send"]}],
                mail_obs,
            )
        with self.assertRaisesRegex(ValueError, "commit_action_forbidden_by_safety_policy"):
            jev.build_bound_candidates(
                [{"id": "m.erase", "description": "Erase deleted items", "tool": "invoke_menu", "path": ["Mailbox", "Erase Deleted Items"]}],
                mail_obs,
            )
        with self.assertRaisesRegex(ValueError, "commit_action_forbidden_by_safety_policy"):
            jev.build_bound_candidates(
                [{"id": "m.trash", "description": "Empty Trash", "tool": "invoke_menu", "path": ["Finder", "Empty Trash"]}],
                mail_obs,
            )
        # invoke_menu invalid path validation
        with self.assertRaisesRegex(ValueError, "missing_or_invalid_menu_path"):
            jev.build_bound_candidates(
                [{"id": "m.empty", "description": "Empty path", "tool": "invoke_menu", "path": []}],
                mail_obs,
            )

        # Safe draft preparation (File > Save via invoke_menu and cmd+s via hotkey) is allowed and authorized in background
        safe_cands = jev.build_bound_candidates(
            [
                {"id": "m.save", "description": "Invoke File > Save", "tool": "invoke_menu", "path": ["File", "Save"]},
                {"id": "hk.save", "description": "Save draft shortcut", "tool": "hotkey", "keys": ["cmd", "s"]},
            ],
            mail_obs,
        )
        self.assertEqual(safe_cands[0]["arguments"]["path"], ["File", "Save"])
        # cua-driver invoke_menu is additionalProperties=false over {pid, window_id, path, session}
        self.assertEqual(set(safe_cands[0]["arguments"]), {"pid", "window_id", "path"})
        self.assertEqual(safe_cands[1]["arguments"]["delivery_mode"], "background")
        with self.assertRaisesRegex(ValueError, "unsupported_menu_arguments"):
            jev.build_bound_candidates(
                [{"id": "m.idx", "description": "Menu on element", "tool": "invoke_menu", "path": ["File", "Save"], "element_index": 12}],
                mail_obs,
            )
        with self.assertRaisesRegex(ValueError, "missing_or_invalid_menu_path"):
            jev.build_bound_candidates(
                [{"id": "m.long", "description": "Too deep", "tool": "invoke_menu", "path": ["x"] * 17}],
                mail_obs,
            )
        auth_menu = jev.authorize_candidate_dispatch(
            {"kind": "selected", "selected_id": "m.save", "pid": 501, "window_id": 2001, "snapshot_id": "s0000aaaa"},
            safe_cands,
            expected_pid=501,
            expected_window_id=2001,
            current_snapshot_id="s0000aaaa",
            valid_element_indices=mail_obs["indexed_element_ids"],
            observation=mail_obs,
        )
        self.assertEqual(auth_menu["tool"], "invoke_menu")
        self.assertEqual(auth_menu["arguments"]["path"], ["File", "Save"])

        # Action-time (Stage 4) commit gate also catches a tampered candidate bypassing Stage 2
        with self.assertRaisesRegex(ValueError, "mail_send_forbidden_by_safety_policy"):
            jev.authorize_candidate_dispatch(
                {"kind": "selected", "selected_id": "tampered.send", "pid": 501, "window_id": 2001, "snapshot_id": "s0000aaaa"},
                [{
                    "id": "tampered.send",
                    "description": "Invoke Message > Send",
                    "pid": 501,
                    "window_id": 2001,
                    "snapshot_id": "s0000aaaa",
                    "tool": "invoke_menu",
                    "arguments": {
                        "pid": 501,
                        "window_id": 2001,
                        "path": ["Message", "Send"],
                    },
                }],
                expected_pid=501,
                expected_window_id=2001,
                current_snapshot_id="s0000aaaa",
                valid_element_indices=mail_obs["indexed_element_ids"],
                observation=mail_obs,
            )

        # Stage 4 also refuses a tampered invoke_menu carrying driver-incompatible arguments
        with self.assertRaisesRegex(ValueError, "unsupported_menu_arguments"):
            jev.authorize_candidate_dispatch(
                {"kind": "selected", "selected_id": "tampered.save", "pid": 501, "window_id": 2001, "snapshot_id": "s0000aaaa"},
                [{
                    "id": "tampered.save",
                    "description": "Invoke File > Save",
                    "pid": 501,
                    "window_id": 2001,
                    "snapshot_id": "s0000aaaa",
                    "tool": "invoke_menu",
                    "arguments": {"pid": 501, "window_id": 2001, "path": ["File", "Save"], "delivery_mode": "background"},
                }],
                expected_pid=501,
                expected_window_id=2001,
                current_snapshot_id="s0000aaaa",
                valid_element_indices=mail_obs["indexed_element_ids"],
                observation=mail_obs,
            )

        # Calculator Delete / Clear buttons must NOT be blocked by the commit gate
        calc_obs = jev.extract_window_observation(
            {
                "structuredContent": {
                    "pid": 42,
                    "window_id": 100,
                    "snapshot_id": "s0000ca1c",
                    "app_name": "Calculator",
                    "window_title": "Calculator",
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "Calculator"},
                        {"element_index": 2, "role": "AXButton", "label": "Delete", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                        {"element_index": 3, "role": "AXButton", "label": "Clear", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                    ],
                },
                "content": [],
            },
            expected_pid=42,
            expected_window_id=100,
        )
        calc_cands = jev.build_bound_candidates(None, calc_obs)
        self.assertEqual([c["id"] for c in calc_cands], ["ax.2.delete", "ax.3.clear"])

    def test_fast_session_cursor_init_and_chained_jev_batch_with_invoke_menu(self):
        with tempfile.TemporaryDirectory() as tmp:
            driver_script = os.path.join(tmp, "cua-driver-fast")
            log_file = os.path.join(tmp, "fast-calls.jsonl")
            with open(driver_script, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(f"""\
                    #!/usr/bin/env python3
                    import json, sys
                    snap_seq = 20
                    recipient = ""
                    saved = "unsaved"
                    log_path = {log_file!r}
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            # Advertise serverInfo.name == "cua-driver" to trigger fast session cursor init
                            res = {{"protocolVersion": "2025-06-18", "capabilities": {{}}, "serverInfo": {{"name": "cua-driver"}}}}
                        elif method == "tools/call":
                            params = msg.get("params", {{}})
                            name = params.get("name")
                            args = params.get("arguments", {{}})
                            with open(log_path, "a", encoding="utf-8") as lf:
                                lf.write(json.dumps({{"name": name, "args": args}}) + "\\n")
                            if name in ("start_session", "set_agent_cursor_enabled", "end_session"):
                                res = {{"content": [{{"type": "text", "text": "ok"}}], "structuredContent": {{"ok": True}}}}
                            elif name == "get_window_state":
                                snap_seq += 1
                                snap = f"s000000{{snap_seq}}"
                                md = f'- [0] AXWindow "Draft"\\n  - AXStaticText = "{{saved}}"\\n  - [3] AXTextField (Recipient) = "{{recipient}}"'
                                res = {{
                                    "content": [{{"type": "text", "text": md}}],
                                    "structuredContent": {{
                                        "pid": args.get("pid", 88),
                                        "window_id": args.get("window_id", 900),
                                        "snapshot_id": snap,
                                        "app_name": "BenchApp",
                                        "window_title": "Draft",
                                        "tree_markdown": md,
                                        "elements": [
                                            {{"element_index": 0, "role": "AXWindow", "label": "Draft"}},
                                            {{"element_index": 1, "role": "AXStaticText", "label": saved, "value": saved, "parent_index": 0}},
                                            {{"element_index": 3, "role": "AXTextField", "label": "Recipient", "value": recipient, "enabled": True, "parent_index": 0, "actions": ["AXPress"]}},
                                        ],
                                    }},
                                }}
                            elif name == "set_value":
                                recipient = str(args.get("value", ""))
                                res = {{"content": [{{"type": "text", "text": "value set"}}], "structuredContent": {{"ok": True}}}}
                            elif name == "invoke_menu":
                                extra = sorted(set(args) - {{"pid", "window_id", "path", "session"}})
                                if extra:
                                    res = {{"content": [{{"type": "text", "text": "additionalProperties not allowed: " + ",".join(extra)}}], "isError": True}}
                                elif args.get("path") == ["File", "Save"]:
                                    saved = "saved"
                                    res = {{"content": [{{"type": "text", "text": "menu invoked"}}], "structuredContent": {{"ok": True}}}}
                                else:
                                    res = {{"content": [{{"type": "text", "text": "bad menu"}}], "isError": True}}
                            else:
                                res = {{"content": [{{"type": "text", "text": "ok"}}], "structuredContent": {{"ok": True}}}}
                        else:
                            res = {{}}
                        print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": res}}), flush=True)
                """))
            os.chmod(driver_script, os.stat(driver_script).st_mode | stat.S_IEXEC)

            def fake_http(req, _t):
                body = json.loads(req.data.decode("utf-8"))
                opts = list(body.get("questions", {}).get("candidate", {}).get("criteria", {}).keys())
                choice = "sv.3.recipient" if "sv.3.recipient" in opts else "menu.save"
                rem_opts = [o for o in opts if o != choice]
                probs = {choice: 0.97}
                for o in rem_opts:
                    probs[o] = round(0.03 / max(1, len(rem_opts)), 4)
                return 200, json.dumps({
                    "model": "jev-1.13.0",
                    "answers": {
                        "candidate": {
                            "type": "choice",
                            "choice": choice,
                            "confidence": 0.97,
                            "probabilities": probs,
                        }
                    },
                    "usage": {"input_tokens": 40, "output_tokens": 6},
                })

            server = jev.JevMcpServer(
                env={
                    "AGY_CUA_DRIVER": driver_script,
                    "TYPESAFE_API_KEY": "test-key",
                    "AGY_CUA_SESSION_ID": "jev-fast-sess",
                },
                keychain_reader=lambda _: None,
                http_post=fake_http,
            )
            try:
                resp = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_batch",
                        "arguments": {
                            "pid": 88,
                            "window_id": 900,
                            "chain_observations": True,
                            "allow_interstep_drift": True,
                            "steps": [
                                {
                                    "goal": "Fill the Recipient field",
                                    "value": "team@example.com",
                                    "expected_postcondition": {"element_value_equals": "team@example.com"},
                                },
                                {
                                    "goal": "Save draft via File > Save menu",
                                    "candidates": [
                                        {
                                            "id": "menu.save",
                                            "description": 'Invoke menu ["File", "Save"]',
                                            "tool": "invoke_menu",
                                            "path": ["File", "Save"],
                                        }
                                    ],
                                    "expected_postcondition": {"static_text_equals": "saved"},
                                },
                            ],
                        },
                    },
                })
            finally:
                server.close()

            sc = resp["result"]["structuredContent"]
            self.assertEqual(sc["status"], "verified")
            self.assertTrue(sc["verified"])
            self.assertTrue(sc["all_verified"])
            self.assertEqual(sc["completed_count"], 2)
            # Step 0 observed s00000021 -> post s00000022; Step 1 reused chained s00000022 -> post s00000023
            self.assertEqual(sc["completed_steps"][0]["pre_snapshot_id"], "s00000021")
            self.assertEqual(sc["completed_steps"][0]["post_snapshot_id"], "s00000022")
            self.assertEqual(sc["completed_steps"][1]["pre_snapshot_id"], "s00000022")
            self.assertEqual(sc["completed_steps"][1]["post_snapshot_id"], "s00000023")
            self.assertEqual(sc["completed_steps"][1]["timings_ms"]["observe_ms"], 0.0)
            self.assertTrue(sc["chain_observations_effective"])
            self.assertEqual([s["observation_chained"] for s in sc["completed_steps"]], [False, True])

            with open(log_file, "r", encoding="utf-8") as lf:
                logged = [json.loads(line) for line in lf if line.strip()]
            names = [c["name"] for c in logged]
            # Fast session init called start_session and set_agent_cursor_enabled(enabled=False) on jev-fast-sess,
            # and chain_observations=True reduced get_window_state calls for 2 steps from 4 to 3 (N+1).
            self.assertEqual(
                names,
                [
                    "start_session",
                    "set_agent_cursor_enabled",
                    "get_window_state",
                    "set_value",
                    "get_window_state",
                    "invoke_menu",
                    "get_window_state",
                    "end_session",
                ],
            )
            self.assertEqual(logged[0]["args"]["session"], "jev-fast-sess")
            self.assertEqual(logged[1]["args"], {"session": "jev-fast-sess", "enabled": False})
            menu_call = next(c for c in logged if c["name"] == "invoke_menu")
            self.assertEqual(set(menu_call["args"]) - {"session"}, {"pid", "window_id", "path"})

    def test_chain_observations_without_drift_opt_in_rereads_and_detects_external_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            driver_script = os.path.join(tmp, "cua-driver-drift")
            log_file = os.path.join(tmp, "drift-calls.jsonl")
            with open(driver_script, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(f"""\
                    #!/usr/bin/env python3
                    import json, sys
                    reads = 0
                    recipient = ""
                    log_path = {log_file!r}
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            res = {{"protocolVersion": "2025-06-18", "capabilities": {{}}, "serverInfo": {{"name": "fake"}}}}
                        elif method == "tools/call":
                            params = msg.get("params", {{}})
                            name = params.get("name")
                            args = params.get("arguments", {{}})
                            with open(log_path, "a", encoding="utf-8") as lf:
                                lf.write(json.dumps({{"name": name}}) + "\\n")
                            if name == "get_window_state":
                                reads += 1
                                # External drift lands after step 0's post-observation (read #2).
                                banner = "external change" if reads >= 3 else "idle"
                                snap = f"s0000{{reads:04d}}"
                                md = f'- [0] AXWindow "Draft"\\n  - AXStaticText = "{{banner}}"\\n  - [3] AXTextField (Recipient) = "{{recipient}}"'
                                res = {{
                                    "content": [{{"type": "text", "text": md}}],
                                    "structuredContent": {{
                                        "pid": 88, "window_id": 900, "snapshot_id": snap, "tree_markdown": md,
                                        "app_name": "BenchApp", "window_title": "Draft",
                                        "elements": [
                                            {{"element_index": 0, "role": "AXWindow", "label": "Draft"}},
                                            {{"element_index": 1, "role": "AXStaticText", "label": banner, "value": banner, "parent_index": 0}},
                                            {{"element_index": 3, "role": "AXTextField", "label": "Recipient", "value": recipient, "enabled": True, "parent_index": 0, "actions": ["AXPress"]}},
                                        ],
                                    }},
                                }}
                            elif name == "set_value":
                                recipient = str(args.get("value", ""))
                                res = {{"content": [{{"type": "text", "text": "value set"}}], "structuredContent": {{"ok": True}}}}
                            else:
                                res = {{"content": [{{"type": "text", "text": "ok"}}], "structuredContent": {{"ok": True}}}}
                        else:
                            res = {{}}
                        print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": res}}), flush=True)
                """))
            os.chmod(driver_script, os.stat(driver_script).st_mode | stat.S_IEXEC)

            def fake_http(req, _t):
                body = json.loads(req.data.decode("utf-8"))
                opts = list(body.get("questions", {}).get("candidate", {}).get("criteria", {}).keys())
                choice = opts[0]
                return 200, json.dumps({
                    "model": "jev-1.13.0",
                    "answers": {"candidate": {"type": "choice", "choice": choice, "confidence": 0.99,
                                              "probabilities": {o: (0.99 if o == choice else 0.01) for o in opts}}},
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                })

            server = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": driver_script, "TYPESAFE_API_KEY": "test-key"},
                keychain_reader=lambda _: None,
                http_post=fake_http,
            )
            try:
                resp = server.handle_message({
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "jev_sequence", "arguments": {
                        "pid": 88, "window_id": 900,
                        "steps": [
                            {"goal": "Fill recipient", "value": "a@example.com",
                             "expected_postcondition": {"element_value_equals": "a@example.com"}},
                            {"goal": "Fill recipient again", "value": "b@example.com",
                             "expected_postcondition": {"element_value_equals": "b@example.com"}},
                        ],
                    }},
                })
            finally:
                server.close()

            sc = resp["result"]["structuredContent"]
            # jev_sequence defaults chain_observations=true, but drift protection stays on, so chaining is not honoured.
            self.assertFalse(sc["chain_observations_effective"])
            self.assertFalse(sc["verified"])
            self.assertEqual(sc["completed_count"], 1)
            self.assertEqual(sc["failed_step"]["status"], "stale_snapshot")
            self.assertIn("interstep_state_drift", sc["failed_step"]["reason"])
            self.assertFalse(sc["failed_step"]["observation_chained"])
            with open(log_file, "r", encoding="utf-8") as lf:
                names = [json.loads(line)["name"] for line in lf if line.strip()]
            # Fresh pre-read for step 1 happened, and the second set_value was never dispatched.
            self.assertEqual(names.count("get_window_state"), 3)
            self.assertEqual(names.count("set_value"), 1)

    def test_commit_gate_closes_modifier_activation_coordinate_and_messages_bypasses(self):
        def obs(app, title, elements):
            return jev.extract_window_observation(
                {"structuredContent": {"pid": 7, "window_id": 70, "snapshot_id": "s0000bbbb",
                                       "app_name": app, "window_title": title, "elements": elements},
                 "content": []},
                expected_pid=7, expected_window_id=70,
            )

        mail = obs("Mail", "New Message", [
            {"element_index": 0, "role": "AXWindow", "label": "New Message"},
            {"element_index": 10, "role": "AXButton", "label": "Send", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
            {"element_index": 20, "role": "AXTextArea", "label": "Body", "enabled": True, "parent_index": 0},
            {"element_index": 30, "role": "AXButton", "label": "Inbox row", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
        ])
        build = jev.build_bound_candidates

        # press_key modifiers are a chord: cmd+shift+d / cmd+return / cmd+delete must be refused like hotkey.
        for mods, key in ((["cmd", "shift"], "d"), (["command"], "return"), (["cmd"], "backspace")):
            with self.assertRaisesRegex(ValueError, "commit_action_forbidden_by_safety_policy"):
                build([{"id": "pk", "description": "Press key", "tool": "press_key", "key": key, "modifiers": mods}], mail)
        # Activation key focused on the Send button presses it.
        for tool, extra in (("press_key", {"key": "space"}), ("hotkey", {"keys": ["return"]})):
            with self.assertRaisesRegex(ValueError, "mail_send_forbidden_by_safety_policy"):
                build([{"id": "act", "description": "Press key", "tool": tool, "element_index": 10, **extra}], mail)
        # Bare Delete outside a text input trashes the selection in Mail; inside the body it is text editing.
        with self.assertRaisesRegex(ValueError, "commit_action_forbidden_by_safety_policy"):
            build([{"id": "del", "description": "Press key", "tool": "press_key", "key": "delete", "element_index": 30}], mail)
        with self.assertRaisesRegex(ValueError, "commit_action_forbidden_by_safety_policy"):
            build([{"id": "del2", "description": "Press key", "tool": "press_key", "key": "delete"}], mail)
        ok = build([{"id": "del3", "description": "Erase a char", "tool": "press_key", "key": "delete", "element_index": 20}], mail)
        self.assertEqual(ok[0]["arguments"]["key"], "delete")
        # Coordinate clicks cannot be label-gated in sensitive apps (caller description is not authority).
        with self.assertRaisesRegex(ValueError, "cannot be label-gated"):
            build([{"id": "xy", "description": "Click somewhere harmless", "tool": "click", "x": 10, "y": 10}], mail)
        with self.assertRaisesRegex(ValueError, "cannot be label-gated"):
            jev.authorize_candidate_dispatch(
                {"kind": "selected", "selected_id": "xy", "pid": 7, "window_id": 70, "snapshot_id": "s0000bbbb"},
                [{"id": "xy", "description": "Click", "pid": 7, "window_id": 70, "snapshot_id": "s0000bbbb", "tool": "click",
                  "arguments": {"pid": 7, "window_id": 70, "x": 10, "y": 10}}],
                expected_pid=7, expected_window_id=70, current_snapshot_id="s0000bbbb",
                valid_element_indices=mail["indexed_element_ids"], observation=mail,
            )
        # Plain typing / tab in Mail stays allowed.
        ok = build([{"id": "tab", "description": "Next field", "tool": "press_key", "key": "tab", "element_index": 20}], mail)
        self.assertEqual(ok[0]["tool"], "press_key")

        messages = obs("Messages", "Alex", [
            {"element_index": 0, "role": "AXWindow", "label": "Alex"},
            {"element_index": 5, "role": "AXTextField", "label": "iMessage", "enabled": True, "parent_index": 0},
        ])
        with self.assertRaisesRegex(ValueError, "mail_send_forbidden_by_safety_policy"):
            build([{"id": "ret", "description": "Press key", "tool": "press_key", "key": "return", "element_index": 5}], messages)
        with self.assertRaisesRegex(ValueError, "mail_send_forbidden_by_safety_policy"):
            build([{"id": "nl", "description": "Type", "tool": "type_text", "text": "hi\n", "element_index": 5}], messages)
        ok = build([{"id": "tt", "description": "Type", "tool": "type_text", "text": "hi", "element_index": 5}], messages)
        self.assertEqual(ok[0]["tool"], "type_text")

        # Non-sensitive apps keep coordinate clicks and plain return.
        calc = obs("Calculator", "Calculator", [{"element_index": 0, "role": "AXWindow", "label": "Calculator"}])
        ok = build([
            {"id": "xy", "description": "Click", "tool": "click", "x": 5, "y": 5},
            {"id": "ret", "description": "Equals", "tool": "press_key", "key": "return"},
        ], calc)
        self.assertEqual([c["id"] for c in ok], ["xy", "ret"])

    def test_observation_budget_validation_refuses_invalid_max_elements_and_walk_timeout_ms(self):
        server = jev.JevMcpServer(
            env={"TYPESAFE_API_KEY": "test-key"},
            keychain_reader=lambda _: None,
        )
        try:
            for invalid_max_el in (0, -5, 1001, True, False, "64", 12.5):
                step_sc = server.handle_step({
                    "pid": 10,
                    "window_id": 20,
                    "goal": "Click button",
                    "max_elements": invalid_max_el,
                })["structuredContent"]
                self.assertEqual(step_sc["status"], "refused")
                self.assertEqual(step_sc["reason"], "invalid_max_elements")

                batch_sc = server.handle_batch({
                    "pid": 10,
                    "window_id": 20,
                    "steps": [{
                        "goal": "Click button",
                        "max_elements": invalid_max_el,
                        "expected_postcondition": {"static_text_equals": "ok"},
                    }],
                })["structuredContent"]
                self.assertEqual(batch_sc["status"], "refused")
                self.assertEqual(batch_sc["reason"], "invalid_max_elements")

            for invalid_walk_to in (0, 99, -100, 10001, True, False, "1000", 250.5):
                step_sc = server.handle_step({
                    "pid": 10,
                    "window_id": 20,
                    "goal": "Click button",
                    "walk_timeout_ms": invalid_walk_to,
                })["structuredContent"]
                self.assertEqual(step_sc["status"], "refused")
                self.assertEqual(step_sc["reason"], "invalid_walk_timeout_ms")

                batch_sc = server.handle_batch({
                    "pid": 10,
                    "window_id": 20,
                    "steps": [{
                        "goal": "Click button",
                        "walk_timeout_ms": invalid_walk_to,
                        "expected_postcondition": {"static_text_equals": "ok"},
                    }],
                })["structuredContent"]
                self.assertEqual(batch_sc["status"], "refused")
                self.assertEqual(batch_sc["reason"], "invalid_walk_timeout_ms")
        finally:
            server.close()

    def test_observation_budget_passthrough_and_deadline_expansion_stdio(self):
        with tempfile.TemporaryDirectory() as tmp:
            driver_script = os.path.join(tmp, "cua-driver-slow-walk")
            log_file = os.path.join(tmp, "slow-walk-calls.jsonl")
            with open(driver_script, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(f"""\
                    #!/usr/bin/env python3
                    import json, sys, time
                    reads = 0
                    status = "Inbox"
                    log_path = {log_file!r}
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            res = {{"protocolVersion": "2025-06-18", "capabilities": {{}}, "serverInfo": {{"name": "fake"}}}}
                        elif method == "tools/call":
                            params = msg.get("params", {{}})
                            name = params.get("name")
                            args = params.get("arguments", {{}})
                            with open(log_path, "a", encoding="utf-8") as lf:
                                lf.write(json.dumps({{"name": name, "args": args}}) + "\\n")
                            if name == "get_window_state":
                                reads += 1
                                # Sleep 350ms: exceeds AGY_CUA_DRIVER_TIMEOUT_MS=200 unless walk_timeout_ms expands outer deadline
                                time.sleep(0.35)
                                snap = f"s0000{{reads:04d}}"
                                md = f'- [0] AXWindow "Mail"\\n  - AXStaticText = "Folder={{status}}"\\n  - [5] AXButton "Mailbox VIP"'
                                res = {{
                                    "content": [{{"type": "text", "text": md}}],
                                    "structuredContent": {{
                                        "pid": 55, "window_id": 500, "snapshot_id": snap, "tree_markdown": md,
                                        "app_name": "Mail", "window_title": "Mail",
                                        "elements": [
                                            {{"element_index": 0, "role": "AXWindow", "label": "Mail"}},
                                            {{"element_index": 1, "role": "AXStaticText", "label": f"Folder={{status}}", "value": f"Folder={{status}}", "parent_index": 0}},
                                            {{"element_index": 5, "role": "AXButton", "label": "Mailbox VIP", "enabled": True, "parent_index": 0, "actions": ["AXPress"]}},
                                        ],
                                    }},
                                }}
                            elif name == "click":
                                status = "VIP"
                                res = {{"content": [{{"type": "text", "text": "clicked"}}], "structuredContent": {{"ok": True}}}}
                            else:
                                res = {{"content": [{{"type": "text", "text": "ok"}}], "structuredContent": {{"ok": True}}}}
                        else:
                            res = {{}}
                        print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": res}}), flush=True)
                """))
            os.chmod(driver_script, os.stat(driver_script).st_mode | stat.S_IEXEC)

            proc = subprocess.Popen(
                [sys.executable, JEV_MCP],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={
                    **os.environ,
                    "AGY_CUA_DRIVER": driver_script,
                    "AGY_CUA_DRIVER_TIMEOUT_MS": "200",
                    "TYPESAFE_API_KEY": "test-key",
                },
            )
            try:
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
                proc.stdin.flush()
                init_line = json.loads(proc.stdout.readline())
                self.assertEqual(init_line["id"], 1)

                # Without walk_timeout_ms, default AGY_CUA_DRIVER_TIMEOUT_MS=200 times out against 350ms driver sleep
                proc.stdin.write(json.dumps({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "pid": 55,
                            "window_id": 500,
                            "goal": "Select Mailbox VIP",
                            "mode": "deterministic",
                            "candidates": [{
                                "id": "click.vip",
                                "description": "Click Mailbox VIP",
                                "tool": "click",
                                "element_index": 5,
                            }],
                            "expected_postcondition": {"static_text_contains": "Folder=VIP"},
                        },
                    },
                }) + "\n")
                proc.stdin.flush()
                timeout_resp = json.loads(proc.stdout.readline())
                self.assertEqual(timeout_resp["result"]["structuredContent"]["status"], "observation_failed")

                # With max_elements=120 and walk_timeout_ms=600, outer driver deadline expands to 1600ms (> 350ms sleep)
                proc.stdin.write(json.dumps({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "pid": 55,
                            "window_id": 500,
                            "goal": "Select Mailbox VIP",
                            "mode": "deterministic",
                            "query": "Mailbox",
                            "max_depth": 3,
                            "max_elements": 120,
                            "walk_timeout_ms": 600,
                            "candidates": [{
                                "id": "click.vip",
                                "description": "Click Mailbox VIP",
                                "tool": "click",
                                "element_index": 5,
                            }],
                            "expected_postcondition": {"static_text_contains": "Folder=VIP"},
                        },
                    },
                }) + "\n")
                proc.stdin.flush()
                step_resp = json.loads(proc.stdout.readline())
                sc = step_resp["result"]["structuredContent"]
                self.assertEqual(sc["status"], "verified")
                self.assertEqual(sc["verification_outcome"], "goal_verified")
                self.assertTrue(sc["verified"])
            finally:
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream is not None:
                        stream.close()
                proc.terminate()
                proc.wait(timeout=3.0)

            with open(log_file, "r", encoding="utf-8") as lf:
                logged = [json.loads(line) for line in lf if line.strip()]
            gws_calls = [c["args"] for c in logged if c["name"] == "get_window_state"]
            # First call was the unexpanded timeout attempt; next two are pre_obs and post_obs with walk_timeout_ms=600
            self.assertEqual(len(gws_calls), 3)
            for gws_arg in gws_calls[1:]:
                self.assertEqual(gws_arg.get("query"), "Mailbox")
                self.assertEqual(gws_arg.get("max_depth"), 3)
                self.assertEqual(gws_arg.get("max_elements"), 120)
                self.assertEqual(gws_arg.get("timeout_ms"), 600)

    def test_batch_chain_observations_requires_matching_full_observation_budget_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            driver_script = os.path.join(tmp, "cua-driver-scope-chain")
            log_file = os.path.join(tmp, "scope-chain-calls.jsonl")
            with open(driver_script, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(f"""\
                    #!/usr/bin/env python3
                    import json, sys
                    reads = 0
                    val = "v0"
                    log_path = {log_file!r}
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            res = {{"protocolVersion": "2025-06-18", "capabilities": {{}}, "serverInfo": {{"name": "fake"}}}}
                        elif method == "tools/call":
                            params = msg.get("params", {{}})
                            name = params.get("name")
                            args = params.get("arguments", {{}})
                            with open(log_path, "a", encoding="utf-8") as lf:
                                lf.write(json.dumps({{"name": name, "args": args}}) + "\\n")
                            if name == "get_window_state":
                                reads += 1
                                snap = f"s0000{{reads:04d}}"
                                md = f'- [0] AXWindow "Draft"\\n  - [3] AXTextField (Subject) = "{{val}}"'
                                res = {{
                                    "content": [{{"type": "text", "text": md}}],
                                    "structuredContent": {{
                                        "pid": 88, "window_id": 900, "snapshot_id": snap, "tree_markdown": md,
                                        "app_name": "BenchApp", "window_title": "Draft",
                                        "elements": [
                                            {{"element_index": 0, "role": "AXWindow", "label": "Draft"}},
                                            {{"element_index": 3, "role": "AXTextField", "label": "Subject", "value": val, "enabled": True, "parent_index": 0, "actions": ["AXPress"]}},
                                        ],
                                    }},
                                }}
                            elif name == "set_value":
                                val = str(args.get("value", ""))
                                res = {{"content": [{{"type": "text", "text": "value set"}}], "structuredContent": {{"ok": True}}}}
                            else:
                                res = {{"content": [{{"type": "text", "text": "ok"}}], "structuredContent": {{"ok": True}}}}
                        else:
                            res = {{}}
                        print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": res}}), flush=True)
                """))
            os.chmod(driver_script, os.stat(driver_script).st_mode | stat.S_IEXEC)

            server = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": driver_script, "TYPESAFE_API_KEY": "test-key"},
                keychain_reader=lambda _: None,
            )
            try:
                def step_spec(v: str, *, max_el: int, walk_ms: int) -> dict:
                    return {
                        "goal": f"Set Subject to {v}",
                        "mode": "deterministic",
                        "query": "Subject",
                        "max_depth": 3,
                        "max_elements": max_el,
                        "walk_timeout_ms": walk_ms,
                        "candidates": [{
                            "id": f"set.{v}",
                            "description": f"Set Subject to {v}",
                            "tool": "set_value",
                            "element_index": 3,
                            "value": v,
                        }],
                        "expected_postcondition": {"element_value_equals": v},
                    }

                resp = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_batch",
                        "arguments": {
                            "pid": 88,
                            "window_id": 900,
                            "chain_observations": True,
                            "allow_interstep_drift": True,
                            "steps": [
                                step_spec("v1", max_el=50, walk_ms=1500),
                                # Same (query, max_depth, max_elements, walk_timeout_ms) -> chained
                                step_spec("v2", max_el=50, walk_ms=1500),
                                # Changed max_elements (50 -> 100) -> must re-observe
                                step_spec("v3", max_el=100, walk_ms=1500),
                                # Changed walk_timeout_ms (1500 -> 2500) -> must re-observe
                                step_spec("v4", max_el=100, walk_ms=2500),
                            ],
                        },
                    },
                })
            finally:
                server.close()

            sc = resp["result"]["structuredContent"]
            self.assertEqual(sc["status"], "verified")
            self.assertTrue(sc["all_verified"])
            self.assertEqual(sc["completed_count"], 4)
            self.assertEqual(
                [s["observation_chained"] for s in sc["completed_steps"]],
                [False, True, False, False],
            )
            with open(log_file, "r", encoding="utf-8") as lf:
                logged = [json.loads(line) for line in lf if line.strip()]
            gws_calls = [c["args"] for c in logged if c["name"] == "get_window_state"]
            # Step 0: pre+post (2); Step 1: post only (1); Step 2: pre+post (2); Step 3: pre+post (2) -> total 7 calls
            self.assertEqual(len(gws_calls), 7)
            self.assertEqual(
                [(c.get("max_elements"), c.get("timeout_ms")) for c in gws_calls],
                [
                    (50, 1500),   # Step 0 pre
                    (50, 1500),   # Step 0 post
                    (50, 1500),   # Step 1 post (pre was chained)
                    (100, 1500),  # Step 2 pre (max_elements changed)
                    (100, 1500),  # Step 2 post
                    (100, 2500),  # Step 3 pre (walk_timeout_ms changed)
                    (100, 2500),  # Step 3 post
                ],
            )

    def test_1password_electron_lock_screen_item_list_secret_4tuple_and_exfiltration_guards(self) -> None:
        # 1. Lock Screen — 1Password observation
        lock_md = "\n".join([
            '- [0] AXWindow "Lock Screen — 1Password"',
            '  - [1] AXWebArea "Lock Screen — 1Password"',
            '    - [2] AXButton',
            '    - [3] AXTextField = "Enter your password"',
            '    - [4] AXCheckBox',
            '    - [5] AXStaticText = "Tooltip:"',
            '    - [6] AXStaticText = "Reveal Password"',
            '    - [7] AXButton',
            '    - [8] AXStaticText = "1Password was locked manually."',
            '    - [11] AXStaticText = "Unlock using Touch ID"',
        ])
        lock_obs = jev.extract_window_observation(
            {
                "content": [{"type": "text", "text": lock_md}],
                "structuredContent": {
                    "pid": 16993,
                    "window_id": 71766,
                    "snapshot_id": "s000006b8",
                    "app_name": "1Password",
                    "window_title": "Lock Screen — 1Password",
                    "tree_markdown": lock_md,
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "Lock Screen — 1Password"},
                        {"element_index": 1, "role": "AXWebArea", "label": "Lock Screen — 1Password", "parent_index": 0},
                        {"element_index": 2, "role": "AXButton", "label": None, "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 3, "role": "AXTextField", "label": None, "value": "Enter your password", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 4, "role": "AXCheckBox", "label": None, "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 5, "role": "AXStaticText", "label": None, "value": "Tooltip:", "parent_index": 1},
                        {"element_index": 6, "role": "AXStaticText", "label": None, "value": "Reveal Password", "parent_index": 1},
                        {"element_index": 7, "role": "AXButton", "label": None, "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 8, "role": "AXStaticText", "label": None, "value": "1Password was locked manually.", "parent_index": 1},
                        {"element_index": 11, "role": "AXStaticText", "label": None, "value": "Unlock using Touch ID", "parent_index": 1},
                    ],
                },
            },
            expected_pid=16993,
            expected_window_id=71766,
        )
        self.assertIn(3, lock_obs["secure_element_ids"])
        self.assertIn(4, lock_obs["secure_element_ids"])
        self.assertNotIn(7, lock_obs["secure_element_ids"])
        lock_cand_ids = [c["id"] for c in lock_obs["auto_candidates"]]
        self.assertIn("ax.7.unlock-using-touch-id", lock_cand_ids)
        self.assertNotIn("ax.3.enter-your-password", lock_cand_ids)
        with self.assertRaisesRegex(ValueError, "secure_element_refused"):
            jev.build_bound_candidates(
                [{"id": "type.pw", "description": "Type master password", "tool": "set_value", "element_index": 3, "value": "x"}],
                lock_obs,
            )
        with self.assertRaisesRegex(ValueError, "secure_element_refused"):
            jev.build_bound_candidates(
                [{"id": "click.reveal", "description": "Toggle reveal", "tool": "click", "element_index": 4}],
                lock_obs,
            )

        # 2. Main Vault Window with 35 sidebar tags/vaults, AXToolbar search, AXList AXMenuItem rows, and Detail 4-tuples
        sidebar_els = [
            {"element_index": 8, "role": "AXButton", "label": "All Items", "parent_index": 1, "actions": ["AXPress"]},
            {"element_index": 10, "role": "AXButton", "label": "VAULTS", "parent_index": 1, "actions": ["AXPress"]},
        ]
        sidebar_md = [
            '- [0] AXWindow "SPCLLC — Smoky Ops — 1Password"',
            '  - [1] AXWebArea "SPCLLC — Smoky Ops — 1Password"',
            '    - [8] AXButton "All Items"',
            '    - [10] AXButton "VAULTS"',
        ]
        for tag_i in range(12, 47):
            sidebar_els.append({
                "element_index": tag_i,
                "role": "AXButton",
                "label": f"tag-{tag_i}",
                "parent_index": 1,
                "actions": ["AXPress"],
            })
            sidebar_md.append(f'    - [{tag_i}] AXButton "tag-{tag_i}"')

        vault_md = "\n".join(sidebar_md + [
            '    - [53] AXSplitter',
            '    - [62] AXButton "Share"',
            '    - [63] AXButton "Edit"',
            '    - [68] AXStaticText = "credential"',
            '    - [69] AXStaticText = "••••••••••"',
            '    - [70] AXButton "Copy"',
            '    - [71] AXPopUpButton (credential. More Actions)',
            '    - [72] AXStaticText = "shipper_id"',
            '    - [73] AXStaticText = "3800272922"',
            '    - [74] AXButton "Copy"',
            '    - [75] AXPopUpButton (shipper_id. More Actions)',
            '    - [80] AXStaticText = "api_key"',
            '    - [81] AXStaticText = "LEAKED-PLAIN-API-KEY-999"',
            '    - [82] AXButton "Copy"',
            '    - [83] AXPopUpButton (api_key. More Actions)',
            '    - [153] AXToolbar',
            '      - [155] AXTextField = "Search in SPCLLC"',
            '      - [157] AXButton "New Item"',
            '    - [162] AXList (Item list)',
            '      - [198] AXMenuItem = "Pitney Bowes Shipping API — Sandbox selected"',
            '- [238] AXMenuBar',
            '  - [279] AXMenuBarItem "Edit"',
            '    - [280] AXMenu',
            '      - [287] AXMenuItem "Search"',
        ])
        vault_obs = jev.extract_window_observation(
            {
                "content": [{"type": "text", "text": vault_md}],
                "structuredContent": {
                    "pid": 16993,
                    "window_id": 71766,
                    "snapshot_id": "s000006b3",
                    "app_name": "1Password",
                    "window_title": "SPCLLC — Smoky Ops — 1Password",
                    "tree_markdown": vault_md,
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "SPCLLC — Smoky Ops — 1Password"},
                        {"element_index": 1, "role": "AXWebArea", "label": "SPCLLC — Smoky Ops — 1Password", "parent_index": 0},
                        *sidebar_els,
                        {"element_index": 53, "role": "AXSplitter", "label": None, "parent_index": 1},
                        {"element_index": 62, "role": "AXButton", "label": "Share", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 63, "role": "AXButton", "label": "Edit", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 68, "role": "AXStaticText", "label": None, "value": "credential", "parent_index": 1},
                        {"element_index": 69, "role": "AXStaticText", "label": None, "value": "••••••••••", "parent_index": 1},
                        {"element_index": 70, "role": "AXButton", "label": "Copy", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 71, "role": "AXPopUpButton", "label": "credential. More Actions", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 72, "role": "AXStaticText", "label": None, "value": "shipper_id", "parent_index": 1},
                        {"element_index": 73, "role": "AXStaticText", "label": None, "value": "3800272922", "parent_index": 1},
                        {"element_index": 74, "role": "AXButton", "label": "Copy", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 75, "role": "AXPopUpButton", "label": "shipper_id. More Actions", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 80, "role": "AXStaticText", "label": None, "value": "api_key", "parent_index": 1},
                        {"element_index": 81, "role": "AXStaticText", "label": None, "value": "LEAKED-PLAIN-API-KEY-999", "parent_index": 1},
                        {"element_index": 82, "role": "AXButton", "label": "Copy", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 83, "role": "AXPopUpButton", "label": "api_key. More Actions", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 153, "role": "AXToolbar", "label": None, "parent_index": 1},
                        {"element_index": 155, "role": "AXTextField", "label": None, "value": "Search in SPCLLC", "parent_index": 153, "actions": ["AXPress"]},
                        {"element_index": 157, "role": "AXButton", "label": "New Item", "parent_index": 153, "actions": ["AXPress"]},
                        {"element_index": 162, "role": "AXList", "label": "Item list", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 198, "role": "AXMenuItem", "label": None, "value": "Pitney Bowes Shipping API — Sandbox selected", "parent_index": 162, "actions": ["AXPress"]},
                        {"element_index": 238, "role": "AXMenuBar", "label": None, "actions": ["AXCancel"]},
                        {"element_index": 279, "role": "AXMenuBarItem", "label": "Edit", "parent_index": 238, "actions": ["AXPress"]},
                        {"element_index": 280, "role": "AXMenu", "label": None, "parent_index": 279, "actions": ["AXCancel"]},
                        {"element_index": 287, "role": "AXMenuItem", "label": "Search", "parent_index": 280, "actions": ["AXPress"]},
                    ],
                },
            },
            expected_pid=16993,
            expected_window_id=71766,
        )
        # Bullet mask and revealed secret field values are redacted from static_texts
        self.assertNotIn("••••••••••", vault_obs["static_texts"])
        self.assertNotIn("LEAKED-PLAIN-API-KEY-999", vault_obs["static_texts"])
        self.assertIn("3800272922", vault_obs["static_texts"])
        # Secret Copy and More Actions controls are marked secure; non-secret Copy is disambiguated and allowed
        self.assertTrue({70, 71, 82, 83}.issubset(vault_obs["secure_element_ids"]))
        self.assertNotIn(74, vault_obs["secure_element_ids"])
        # MenuBar item [287] is excluded, while in-window AXList AXMenuItem [198] is included
        self.assertNotIn(287, vault_obs["indexed_element_ids"])
        self.assertIn(198, vault_obs["indexed_element_ids"])
        # Primary controls (Toolbar [155, 157], Detail [63, 74], Item list [198]) beat the 35 sidebar tags into top 28 auto_candidates
        vault_cand_ids = [c["id"] for c in vault_obs["auto_candidates"]]
        self.assertIn("ax.155.search-in-spcllc", vault_cand_ids)
        self.assertIn("ax.157.new-item", vault_cand_ids)
        self.assertIn("ax.63.edit", vault_cand_ids)
        self.assertIn("ax.74.copy-shipper-id", vault_cand_ids)
        self.assertIn("ax.198.pitney-bowes-shipping-ap", vault_cand_ids)
        self.assertNotIn("ax.62.share", vault_cand_ids)
        self.assertNotIn("ax.70.copy-credential", vault_cand_ids)

        # Toolbar Search field [155] keeps stable raw_label="Search" even when cua-driver echoes value into label,
        # and transient toolbar AXMenuItem autocomplete rows are excluded from auto_candidates
        post_search_md = vault_md.replace('AXTextField = "Search in SPCLLC"', 'AXTextField = "Sandbox"')
        post_search_obs = jev.extract_window_observation(
            {
                "content": [{"type": "text", "text": post_search_md}],
                "structuredContent": {
                    "pid": 16993,
                    "window_id": 71766,
                    "snapshot_id": "s000006b4",
                    "app_name": "1Password",
                    "window_title": "SPCLLC — Smoky Ops — 1Password",
                    "tree_markdown": post_search_md,
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "SPCLLC — Smoky Ops — 1Password"},
                        {"element_index": 1, "role": "AXWebArea", "label": "SPCLLC — Smoky Ops — 1Password", "parent_index": 0},
                        {"element_index": 153, "role": "AXToolbar", "label": None, "parent_index": 1},
                        {"element_index": 155, "role": "AXTextField", "label": "Sandbox", "value": "Sandbox", "parent_index": 153, "actions": ["AXPress"]},
                        {"element_index": 156, "role": "AXButton", "label": "Clear", "parent_index": 153, "actions": ["AXPress"]},
                        {"element_index": 158, "role": "AXMenuItem", "label": "Sandbox autocomplete row", "value": "Sandbox autocomplete row", "parent_index": 153, "actions": ["AXPress"]},
                    ],
                },
            },
            expected_pid=16993,
            expected_window_id=71766,
        )
        self.assertNotIn("ax.158.sandbox-autocomplete-row", [c["id"] for c in post_search_obs["auto_candidates"]])
        changed, met, reason = jev.check_postcondition(
            {"element_value_equals": "Sandbox"},
            vault_obs,
            post_search_obs,
            target_element_index=155,
        )
        self.assertTrue(changed)
        self.assertTrue(met, reason)

        # Secret exfiltration menu items and hotkeys are refused; Lock and Open and Fill are permitted
        for bad_menu in (
            ["View", "Reveal Secure Fields"],
            ["Edit", "Copy Password"],
            ["Edit", "Copy One-Time Password"],
            ["File", "Export"],
            ["Edit", "Share…"],
            ["Edit", "Archive Item"],
        ):
            with self.assertRaisesRegex(ValueError, "commit_action_forbidden_by_safety_policy"):
                jev.build_bound_candidates(
                    [{"id": "bad.menu", "description": "Run menu", "tool": "invoke_menu", "path": bad_menu}],
                    vault_obs,
                )
        for bad_keys in (["cmd", "option", "c"], ["cmd", "option", "r"]):
            with self.assertRaisesRegex(ValueError, "secret_exfiltration_forbidden_by_safety_policy"):
                jev.build_bound_candidates(
                    [{"id": "bad.hk", "description": "Secret hotkey", "tool": "hotkey", "keys": bad_keys}],
                    vault_obs,
                )
        safe_cands = jev.build_bound_candidates(
            [
                {"id": "lock.op", "description": "Lock 1Password", "tool": "invoke_menu", "path": ["1Password", "Lock 1Password"]},
                {"id": "fill.op", "description": "Open and Fill item", "tool": "invoke_menu", "path": ["Edit", "Open and Fill"]},
                {"id": "item.fill.op", "description": "Item > Open and Fill", "tool": "invoke_menu", "path": ["Item", "Open and Fill"]},
            ],
            vault_obs,
        )
        self.assertEqual([c["id"] for c in safe_cands], ["lock.op", "fill.op", "item.fill.op"])

    def test_1password_open_and_fill_and_copilot_safety_hardening(self):
        # Website row with inline Open + Tooltip: Open and Fill, AXLink row, and multi-node secret span with Watchtower badge
        login_md = "\n".join([
            '- [0] AXWindow "SPCLLC — 1Password"',
            '  - [1] AXWebArea "SPCLLC — 1Password"',
            '    - [10] AXButton "All Items"',
            '    - [20] AXSplitter',
            '    - [60] AXStaticText = "username"',
            '    - [61] AXStaticText = "ops@example.com"',
            '    - [62] AXButton "Copy"',
            '    - [63] AXPopUpButton (username. More Actions)',
            '    - [70] AXStaticText = "password"',
            '    - [71] AXStaticText = "UNMASKED-SUPER-SECRET-PW-99"',
            '    - [72] AXStaticText = "Vulnerable"',
            '    - [73] AXButton "Hide"',
            '    - [74] AXStaticText = "Tooltip:"',
            '    - [75] AXStaticText = "Hide Password"',
            '    - [76] AXButton "Copy"',
            '    - [77] AXPopUpButton (password. More Actions)',
            '    - [80] AXStaticText = "website"',
            '    - [81] AXStaticText = "https://dashboard.stripe.com/share/delete/send"',
            '    - [82] AXButton "Open"',
            '    - [83] AXStaticText = "Tooltip:"',
            '    - [84] AXStaticText = "Open and Fill"',
            '    - [85] AXButton "Copy"',
            '    - [86] AXPopUpButton (website. More Actions)',
            '    - [90] AXStaticText = "login url"',
            '    - [91] AXLink "https://github.com/login"',
            '    - [92] AXButton "Copy"',
            '    - [93] AXPopUpButton (login url. More Actions)',
            '    - [95] AXMenuItem "Open and Fill"',
            '    - [96] AXTextField (API Secret) = "plain-api-secret-val"',
            '    - [97] AXButton "••••••••"',
            '    - [100] AXStaticText = "custom_pin"',
            '    - [101] AXStaticText = "REVEALED-CUSTOM-PIN-7777"',
            '    - [102] AXButton "Hide"',
            '    - [103] AXButton "Copy"',
            '    - [104] AXPopUpButton (custom_pin. More Actions)',
        ])
        login_obs = jev.extract_window_observation(
            {
                "content": [{"type": "text", "text": login_md}],
                "structuredContent": {
                    "pid": 16993,
                    "window_id": 71766,
                    "snapshot_id": "s000007a1",
                    "app_name": "1Password",
                    "window_title": "SPCLLC — 1Password",
                    "tree_markdown": login_md,
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "SPCLLC — 1Password"},
                        {"element_index": 1, "role": "AXWebArea", "label": "SPCLLC — 1Password", "parent_index": 0},
                        {"element_index": 10, "role": "AXButton", "label": "All Items", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 20, "role": "AXSplitter", "label": None, "parent_index": 1},
                        {"element_index": 60, "role": "AXStaticText", "label": None, "value": "username", "parent_index": 1},
                        {"element_index": 61, "role": "AXStaticText", "label": None, "value": "ops@example.com", "parent_index": 1},
                        {"element_index": 62, "role": "AXButton", "label": "Copy", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 63, "role": "AXPopUpButton", "label": "username. More Actions", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 70, "role": "AXStaticText", "label": None, "value": "password", "parent_index": 1},
                        {"element_index": 71, "role": "AXStaticText", "label": None, "value": "UNMASKED-SUPER-SECRET-PW-99", "parent_index": 1},
                        {"element_index": 72, "role": "AXStaticText", "label": None, "value": "Vulnerable", "parent_index": 1},
                        {"element_index": 73, "role": "AXButton", "label": "Hide", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 74, "role": "AXStaticText", "label": None, "value": "Tooltip:", "parent_index": 1},
                        {"element_index": 75, "role": "AXStaticText", "label": None, "value": "Hide Password", "parent_index": 1},
                        {"element_index": 76, "role": "AXButton", "label": "Copy", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 77, "role": "AXPopUpButton", "label": "password. More Actions", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 80, "role": "AXStaticText", "label": None, "value": "website", "parent_index": 1},
                        {"element_index": 81, "role": "AXStaticText", "label": None, "value": "https://dashboard.stripe.com/share/delete/send", "parent_index": 1},
                        {"element_index": 82, "role": "AXButton", "label": "Open", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 83, "role": "AXStaticText", "label": None, "value": "Tooltip:", "parent_index": 1},
                        {"element_index": 84, "role": "AXStaticText", "label": None, "value": "Open and Fill", "parent_index": 1},
                        {"element_index": 85, "role": "AXButton", "label": "Copy", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 86, "role": "AXPopUpButton", "label": "website. More Actions", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 90, "role": "AXStaticText", "label": None, "value": "login url", "parent_index": 1},
                        {"element_index": 91, "role": "AXLink", "label": "https://github.com/login", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 92, "role": "AXButton", "label": "Copy", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 93, "role": "AXPopUpButton", "label": "login url. More Actions", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 95, "role": "AXMenuItem", "label": "Open and Fill", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 96, "role": "AXTextField", "label": "API Secret", "value": "plain-api-secret-val", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 97, "role": "AXButton", "label": "••••••••", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 100, "role": "AXStaticText", "label": None, "value": "custom_pin", "parent_index": 1},
                        {"element_index": 101, "role": "AXStaticText", "label": None, "value": "REVEALED-CUSTOM-PIN-7777", "parent_index": 1},
                        {"element_index": 102, "role": "AXButton", "label": "Hide", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 103, "role": "AXButton", "label": "Copy", "parent_index": 1, "actions": ["AXPress"]},
                        {"element_index": 104, "role": "AXPopUpButton", "label": "custom_pin. More Actions", "parent_index": 1, "actions": ["AXPress"]},
                    ],
                },
            },
            expected_pid=16993,
            expected_window_id=71766,
        )

        # Multi-node password span [71] and revealed custom concealed span [101] are redacted
        self.assertNotIn("UNMASKED-SUPER-SECRET-PW-99", login_obs["static_texts"])
        self.assertNotIn("REVEALED-CUSTOM-PIN-7777", login_obs["static_texts"])
        self.assertIn("Vulnerable", login_obs["static_texts"])
        self.assertTrue({71, 73, 76, 77, 96, 97, 101, 102, 103, 104}.issubset(login_obs["secure_element_ids"]))

        # Open and Fill controls [82, 91, 95] are synthesized with URL context, not blocked by /share/delete/send in URL, and promoted to priority_tier=0
        cand_ids = [c["id"] for c in login_obs["auto_candidates"]]
        self.assertEqual(
            cand_ids[:3],
            [
                "ax.95.open-and-fill",
                "ax.82.open-and-fill-https-dash",
                "ax.91.open-and-fill-https-gith",
            ],
        )
        self.assertIn("ax.85.copy-website", cand_ids)
        self.assertIn("ax.86.website-more-actions", cand_ids)
        self.assertIn("ax.62.copy-username", cand_ids)

        # Safety gates: cmd+shift+c (Copy One-Time Password), activation key on secret control, right_click, and Stage 4 element label check
        with self.assertRaisesRegex(ValueError, "secret_exfiltration_forbidden_by_safety_policy"):
            jev.build_bound_candidates(
                [{"id": "hk.totp", "description": "Copy TOTP", "tool": "hotkey", "keys": ["cmd", "shift", "c"]}],
                login_obs,
            )
        with self.assertRaisesRegex(ValueError, "secret_exfiltration_forbidden_by_safety_policy"):
            jev.build_bound_candidates(
                [{"id": "rc.share", "description": "Right click Share", "tool": "right_click", "element_index": 62}],
                login_obs,
            )
        # Stage 4 authorize_candidate_dispatch inspects target element label in observation even if candidate description is benign
        share_obs = jev.extract_window_observation(
            {
                "content": [{"type": "text", "text": '- [0] AXWindow "1Password"\n  - [5] AXButton "Share"'}],
                "structuredContent": {
                    "pid": 16993,
                    "window_id": 71766,
                    "snapshot_id": "s000007a2",
                    "app_name": "1Password",
                    "window_title": "1Password",
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "1Password"},
                        {"element_index": 5, "role": "AXButton", "label": "Share", "parent_index": 0, "actions": ["AXPress"]},
                    ],
                },
            },
            expected_pid=16993,
            expected_window_id=71766,
        )
        with self.assertRaisesRegex(ValueError, "secret_exfiltration_forbidden_by_safety_policy"):
            jev.authorize_candidate_dispatch(
                {"kind": "selected", "selected_id": "benign.desc", "pid": 16993, "window_id": 71766, "snapshot_id": "s000007a2"},
                [{
                    "id": "benign.desc",
                    "description": "Click primary button",
                    "pid": 16993,
                    "window_id": 71766,
                    "snapshot_id": "s000007a2",
                    "tool": "click",
                    "arguments": {"pid": 16993, "window_id": 71766, "snapshot_id": "s000007a2", "element_index": 5, "delivery_mode": "background", "scope": "window"},
                }],
                expected_pid=16993,
                expected_window_id=71766,
                current_snapshot_id="s000007a2",
                valid_element_indices=share_obs["indexed_element_ids"],
                observation=share_obs,
            )
        with self.assertRaisesRegex(ValueError, "secret_exfiltration_forbidden_by_safety_policy"):
            jev.authorize_candidate_dispatch(
                {"kind": "selected", "selected_id": "activate.share", "pid": 16993, "window_id": 71766, "snapshot_id": "s000007a2"},
                [{
                    "id": "activate.share",
                    "description": "Press Return",
                    "pid": 16993,
                    "window_id": 71766,
                    "snapshot_id": "s000007a2",
                    "tool": "press_key",
                    "arguments": {"pid": 16993, "window_id": 71766, "snapshot_id": "s000007a2", "element_index": 5, "key": "return", "delivery_mode": "background", "scope": "window"},
                }],
                expected_pid=16993,
                expected_window_id=71766,
                current_snapshot_id="s000007a2",
                valid_element_indices=share_obs["indexed_element_ids"],
                observation=share_obs,
            )

        # Non-1Password labeled text field whose value equals its label preserves raw_label,
        # and check_postcondition rejects re-indexing between a labeled and unlabeled element
        notes_pre = jev.extract_window_observation(
            {
                "content": [{"type": "text", "text": '- [0] AXWindow "Notes"\n  - [10] AXTextField (Subject) = "Subject"'}],
                "structuredContent": {
                    "pid": 800,
                    "window_id": 900,
                    "snapshot_id": "s00000801",
                    "app_name": "Notes",
                    "window_title": "Notes",
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "Notes"},
                        {"element_index": 10, "role": "AXTextField", "label": "Subject", "value": "Subject", "parent_index": 0, "actions": ["AXPress"]},
                    ],
                },
            },
            expected_pid=800,
            expected_window_id=900,
        )
        notes_post_reindexed = jev.extract_window_observation(
            {
                "content": [{"type": "text", "text": '- [0] AXWindow "Notes"\n  - [10] AXTextField = "Subject"'}],
                "structuredContent": {
                    "pid": 800,
                    "window_id": 900,
                    "snapshot_id": "s00000802",
                    "app_name": "Notes",
                    "window_title": "Notes",
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "Notes"},
                        {"element_index": 10, "role": "AXTextField", "label": "Subject", "value": "Subject", "parent_index": 0, "actions": ["AXPress"]},
                    ],
                },
            },
            expected_pid=800,
            expected_window_id=900,
        )
        self.assertEqual(notes_pre["elements"][0]["raw_label"], "Subject")
        self.assertEqual(notes_post_reindexed["elements"][0]["raw_label"], "")
        _, met, reason = jev.check_postcondition(
            {"element_value_equals": "Subject"},
            notes_pre,
            notes_post_reindexed,
            target_element_index=10,
        )
        self.assertFalse(met)
        self.assertIn("target_element_label_mismatch", reason)

    def test_observation_query_completeness_supported_actions_and_live_driver_semantics(self) -> None:
        # 1. Supported actions and complete query match
        complete_obs = jev.extract_window_observation(
            {
                "content": [{"type": "text", "text": '- [0] AXWindow "Form"\n  - [2] AXTextField "Name" = ""\n  - [3] AXButton "Apply"'}],
                "structuredContent": {
                    "pid": 55,
                    "window_id": 101,
                    "snapshot_id": "s00000101",
                    "elements_complete": True,
                    "truncated": False,
                    "window_bounds": {"x": 10, "y": 20, "width": 640, "height": 480},
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "Form"},
                        {"element_index": 2, "role": "AXTextField", "label": "Name", "value": "", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                        {"element_index": 3, "role": "AXButton", "label": "Apply", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                    ],
                },
            },
            expected_pid=55,
            expected_window_id=101,
            query="Name",
        )
        self.assertEqual(complete_obs["query_status"], "matched")
        self.assertFalse(complete_obs["query_incomplete"])
        self.assertTrue(complete_obs["search_complete"])
        self.assertFalse(complete_obs["visual_fallback_recommended"])
        self.assertEqual(complete_obs["matched_elements_count"], 1)
        tf_actions = complete_obs["elements"][0]["supported_actions"]
        self.assertIn("click", tf_actions)
        self.assertIn("set_value", tf_actions)
        self.assertIn("type_text", tf_actions)

        # 2. Live cua-driver semantics: elements_complete=False with truncated=False is NOT walk_truncated
        live_canvas_obs = jev.extract_window_observation(
            {
                "content": [{"type": "text", "text": '- [0] AXWindow "Canvas"\n  - AXStaticText = "Draw here"'}],
                "structuredContent": {
                    "pid": 55,
                    "window_id": 101,
                    "snapshot_id": "s00000102",
                    "elements_complete": False,
                    "truncated": False,
                    "window_bounds": {"x": 0, "y": 0, "width": 720, "height": 532},
                    "screenshot_width": 1366,
                    "screenshot_height": 1009,
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "Canvas"},
                    ],
                },
            },
            expected_pid=55,
            expected_window_id=101,
        )
        self.assertFalse(live_canvas_obs["observation_truncated"])
        self.assertTrue(live_canvas_obs["search_complete"])
        self.assertEqual(live_canvas_obs["query_status"], "unfiltered_complete")
        self.assertTrue(live_canvas_obs["visual_fallback_recommended"])
        self.assertEqual(live_canvas_obs["window_bounds"]["width"], 1366.0)
        self.assertEqual(live_canvas_obs["window_bounds"]["height"], 1009.0)
        self.assertEqual(live_canvas_obs["window_bounds"]["logical_width"], 720.0)
        self.assertEqual(live_canvas_obs["window_bounds"]["logical_height"], 532.0)
        with self.assertRaisesRegex(ValueError, "visual_fallback_recommended=true"):
            jev.build_bound_candidates(None, live_canvas_obs)

        # 3. Truncated walk with empty query match signals query_incomplete and refuses empty auto-candidates
        truncated_obs = jev.extract_window_observation(
            {
                "content": [{"type": "text", "text": '- [0] AXWindow "LargeTable"'}],
                "structuredContent": {
                    "pid": 55,
                    "window_id": 101,
                    "snapshot_id": "s00000103",
                    "elements_complete": False,
                    "truncated": True,
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "LargeTable"},
                        {"element_index": 1, "role": "AXButton", "label": "Header", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                    ],
                },
            },
            expected_pid=55,
            expected_window_id=101,
            query="Row 45",
        )
        self.assertEqual(truncated_obs["query_status"], "no_matches_in_truncated_scope")
        self.assertTrue(truncated_obs["query_incomplete"])
        self.assertFalse(truncated_obs["search_complete"])
        self.assertFalse(truncated_obs["visual_fallback_recommended"])
        with self.assertRaisesRegex(ValueError, "no_matching_candidates_in_truncated_scope"):
            jev.build_bound_candidates(None, truncated_obs)

    def test_incremental_diff_reconstruction_and_invalid_baseline_fallback_stdio(self) -> None:
        base_obs = jev.extract_window_observation(
            {
                "content": [{"type": "text", "text": '- [0] AXWindow "Editor"\n  - [2] AXButton "Save"\n  - [3] AXTextField "Title" = "Draft"'}],
                "structuredContent": {
                    "pid": 77,
                    "window_id": 202,
                    "snapshot_id": "s00000301",
                    "elements_complete": True,
                    "truncated": False,
                    "window_bounds": {"width": 800, "height": 600},
                    "elements": [
                        {"element_index": 0, "role": "AXWindow", "label": "Editor"},
                        {"element_index": 2, "role": "AXButton", "label": "Save", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                        {"element_index": 3, "role": "AXTextField", "label": "Title", "value": "Draft", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                    ],
                },
            },
            expected_pid=77,
            expected_window_id=202,
        )
        diff_obs = jev.extract_window_observation(
            {
                "content": [{"type": "text", "text": 'AXStaticText = "Saved"'}],
                "structuredContent": {
                    "pid": 77,
                    "window_id": 202,
                    "snapshot_id": "s00000302",
                    "is_diff": True,
                    "diff_from_snapshot_id": "s00000301",
                    "removed_element_indices": [2],
                    "updated_elements": [
                        {"element_index": 3, "value": "Final"},
                    ],
                    "added_elements": [
                        {"element_index": 4, "role": "AXButton", "label": "Publish", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                    ],
                },
            },
            expected_pid=77,
            expected_window_id=202,
            baseline_observation=base_obs,
        )
        self.assertTrue(diff_obs["reconstructed_from_diff"])
        self.assertEqual(diff_obs["indexed_element_ids"], {3, 4})
        by_idx = {e["element_index"]: e for e in diff_obs["elements"]}
        self.assertEqual(by_idx[3]["value"], "Final")
        self.assertEqual(by_idx[4]["label"], "Publish")
        self.assertEqual(diff_obs["window_bounds"]["width"], 800.0)

        # Invalid diff baseline refusals
        with self.assertRaisesRegex(ValueError, "invalid_diff_baseline"):
            jev.extract_window_observation(
                {
                    "content": [],
                    "structuredContent": {
                        "pid": 77,
                        "window_id": 202,
                        "snapshot_id": "s00000303",
                        "is_diff": True,
                        "diff_from_snapshot_id": "s00000999",
                        "updated_elements": [],
                    },
                },
                expected_pid=77,
                expected_window_id=202,
                baseline_observation=base_obs,
            )

        # Stdio end-to-end: jev_observe automatically falls back to full get_window_state(include_diff=False)
        with tempfile.TemporaryDirectory() as tmp:
            driver_log = os.path.join(tmp, "driver_calls.jsonl")
            diff_driver = os.path.join(tmp, "diff-driver")
            with open(diff_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(f"""\
                    #!/usr/bin/env python3
                    import json, sys
                    log_path = {driver_log!r}
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": {{"protocolVersion": "2025-06-18"}}}}), flush=True)
                        elif method == "tools/call":
                            name = msg["params"]["name"]
                            args = msg["params"].get("arguments", {{}})
                            with open(log_path, "a", encoding="utf-8") as lf:
                                lf.write(json.dumps({{"name": name, "args": args}}) + "\\n")
                            if name == "get_window_state":
                                if args.get("include_diff") is False:
                                    sc = {{
                                        "pid": 77,
                                        "window_id": 202,
                                        "snapshot_id": "s00000305",
                                        "elements_complete": True,
                                        "truncated": False,
                                        "elements": [
                                            {{"element_index": 0, "role": "AXWindow", "label": "Editor"}},
                                            {{"element_index": 5, "role": "AXButton", "label": "Recovered", "enabled": True, "parent_index": 0, "actions": ["AXPress"]}},
                                        ],
                                    }}
                                else:
                                    sc = {{
                                        "pid": 77,
                                        "window_id": 202,
                                        "snapshot_id": "s00000304",
                                        "is_diff": True,
                                        "diff_from_snapshot_id": "s00000001",
                                        "updated_elements": [{{"element_index": 99, "value": "bad"}}],
                                    }}
                                res = {{"content": [{{"type": "text", "text": 'AXStaticText = "Ready"'}}], "structuredContent": sc}}
                            else:
                                res = {{"content": [{{"type": "text", "text": "[]"}}], "structuredContent": {{"windows": []}}}}
                            print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": res}}), flush=True)
                """))
            os.chmod(diff_driver, os.stat(diff_driver).st_mode | stat.S_IEXEC)

            server = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": diff_driver, "TYPESAFE_API_KEY": "test-key"},
                keychain_reader=lambda _: None,
            )
            try:
                resp = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_observe",
                        "arguments": {"pid": 77, "window_id": 202, "include_diff": True, "discover_windows": False},
                    },
                })
            finally:
                server.close()

            sc = resp["result"]["structuredContent"]
            self.assertFalse(resp["result"]["isError"])
            self.assertEqual(sc["status"], "ok")
            self.assertTrue(sc["diff_fallback_used"])
            self.assertEqual(sc["snapshot_id"], "s00000305")
            self.assertEqual(sc["elements"][0]["label"], "Recovered")

    def test_visual_coordinate_fallback_retina_bounds_source_snapshot_rebinding_and_refusals(self) -> None:
        # 1. Image / base64 payload rejection in validate_select_request and build_bound_candidates
        with self.assertRaisesRegex(ValueError, "image_payload_forbidden_in_jev"):
            jev.validate_select_request({
                "goal": "Click button",
                "pid": 42,
                "window_id": 100,
                "snapshot_id": "s00000401",
                "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAUA",
                "elements": [{"element_index": 1, "role": "AXButton", "label": "OK"}],
                "candidates": [{"id": "c1", "description": "Click OK"}],
            })

        canvas_obs = jev.extract_window_observation(
            {
                "content": [{"type": "text", "text": '- [0] AXWindow "CanvasFixture"'}],
                "structuredContent": {
                    "pid": 42,
                    "window_id": 100,
                    "snapshot_id": "s00000402",
                    "elements_complete": False,
                    "truncated": False,
                    "window_bounds": {"x": 50, "y": 50, "width": 720, "height": 532},
                    "screenshot_width": 1366,
                    "screenshot_height": 1009,
                    "elements": [{"element_index": 0, "role": "AXWindow", "label": "CanvasFixture"}],
                },
            },
            expected_pid=42,
            expected_window_id=100,
        )
        self.assertTrue(canvas_obs["visual_fallback_recommended"])

        # Out-of-bounds coordinates rejected (> Retina screenshot bounds or < 0)
        with self.assertRaisesRegex(ValueError, "out_of_bounds_coordinates"):
            jev.build_bound_candidates(
                [{"id": "coord.oob", "description": "Click outside window", "tool": "click", "x": 1500, "y": 300}],
                canvas_obs,
            )
        with self.assertRaisesRegex(ValueError, "out_of_bounds_coordinates"):
            jev.build_bound_candidates(
                [{"id": "coord.neg", "description": "Click negative", "tool": "click", "x": -1, "y": 100}],
                canvas_obs,
            )
        # Foreground or global scope rejected
        with self.assertRaisesRegex(ValueError, "foreground_or_global_input_forbidden"):
            jev.build_bound_candidates(
                [{
                    "id": "coord.fg",
                    "description": "Foreground click",
                    "tool": "click",
                    "arguments": {"x": 200, "y": 200, "delivery_mode": "foreground"},
                }],
                canvas_obs,
            )

        # Valid Retina screenshot coordinate candidate with source_snapshot_id provenance/freshness/scope checks
        with tempfile.TemporaryDirectory() as tmp:
            driver_log = os.path.join(tmp, "coord-calls.jsonl")
            coord_driver = os.path.join(tmp, "coord-driver")
            with open(coord_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(f"""\
                    #!/usr/bin/env python3
                    import json, sys
                    obs_n = 0
                    activated = False
                    log_path = {driver_log!r}
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": {{"protocolVersion": "2025-06-18"}}}}), flush=True)
                        elif method == "tools/call":
                            name = msg["params"]["name"]
                            args = msg["params"].get("arguments", {{}})
                            with open(log_path, "a", encoding="utf-8") as lf:
                                lf.write(json.dumps({{"name": name, "args": args}}) + "\\n")
                            if name == "get_window_state":
                                obs_n += 1
                                wid = int(args.get("window_id") or 100)
                                snap = f"s0000040{{obs_n}}"
                                txt = 'AXStaticText = "Canvas Activated"' if activated else 'AXStaticText = "Canvas Idle"'
                                sc = {{
                                    "pid": 42,
                                    "window_id": wid,
                                    "snapshot_id": snap,
                                    "elements_complete": False,
                                    "truncated": False,
                                    "window_bounds": {{"x": 0, "y": 0, "width": 720, "height": 532}},
                                    "screenshot_width": 1366,
                                    "screenshot_height": 1009,
                                    "elements": [{{"element_index": 0, "role": "AXWindow", "label": "Canvas"}}],
                                }}
                                content = [{{"type": "text", "text": txt}}]
                                if args.get("include_screenshot"):
                                    content.append({{"type": "image", "mimeType": "image/png", "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="}})
                                res = {{"content": content, "structuredContent": sc}}
                            elif name == "click":
                                activated = True
                                res = {{"content": [{{"type": "text", "text": f"clicked ({{args.get('x')}}, {{args.get('y')}})"}}]}}
                            else:
                                res = {{"content": [{{"type": "text", "text": "[]"}}], "structuredContent": {{"windows": []}}}}
                            print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": res}}), flush=True)
                """))
            os.chmod(coord_driver, os.stat(coord_driver).st_mode | stat.S_IEXEC)

            def read_logged_clicks() -> list[dict]:
                if not os.path.exists(driver_log):
                    return []
                with open(driver_log, "r", encoding="utf-8") as lf:
                    return [json.loads(ln) for ln in lf if ln.strip() and json.loads(ln)["name"] == "click"]

            server = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": coord_driver, "TYPESAFE_API_KEY": "test-key"},
                keychain_reader=lambda _: None,
            )
            try:
                # 1. Unknown source_snapshot_id refused before dispatch
                resp_unk = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Click custom canvas target at Retina pixel (950, 720)",
                            "pid": 42,
                            "window_id": 100,
                            "mode": "deterministic",
                            "source_snapshot_id": "s00009999",
                            "candidates": [{
                                "id": "canvas.hit",
                                "description": "Click custom canvas control at (950, 720)",
                                "snapshot_id": "s00009999",
                                "tool": "click",
                                "x": 950,
                                "y": 720,
                            }],
                        },
                    },
                })
                sc_unk = resp_unk["result"]["structuredContent"]
                self.assertTrue(resp_unk["result"]["isError"])
                self.assertEqual(sc_unk["status"], "stale_snapshot")
                self.assertFalse(sc_unk["action_dispatched"])
                self.assertIn("unknown_source_snapshot_id", sc_unk["reason"])
                self.assertEqual(len(read_logged_clicks()), 0)

                # 2. Observe visual snapshot on (pid=42, window_id=100), then attempt to use it on wrong window_id=200
                obs1 = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_observe",
                        "arguments": {"pid": 42, "window_id": 100, "include_screenshot": True},
                    },
                })
                snap_w100_old = obs1["result"]["structuredContent"]["snapshot_id"]
                resp_wrong_win = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 12,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Click custom canvas target on wrong window",
                            "pid": 42,
                            "window_id": 200,
                            "mode": "deterministic",
                            "source_snapshot_id": snap_w100_old,
                            "candidates": [{
                                "id": "canvas.hit",
                                "description": "Click custom canvas control at (950, 720)",
                                "snapshot_id": snap_w100_old,
                                "tool": "click",
                                "x": 950,
                                "y": 720,
                            }],
                        },
                    },
                })
                sc_wrong = resp_wrong_win["result"]["structuredContent"]
                self.assertTrue(resp_wrong_win["result"]["isError"])
                self.assertEqual(sc_wrong["status"], "out_of_scope_target")
                self.assertFalse(sc_wrong["action_dispatched"])
                self.assertIn("out_of_scope_target", sc_wrong["reason"])
                self.assertEqual(len(read_logged_clicks()), 0)

                # 3. Observe a newer visual snapshot on (pid=42, window_id=100), then verify stale snap_w100_old is refused
                obs2 = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 13,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_observe",
                        "arguments": {"pid": 42, "window_id": 100, "include_screenshot": True},
                    },
                })
                snap_w100_latest = obs2["result"]["structuredContent"]["snapshot_id"]
                self.assertNotEqual(snap_w100_old, snap_w100_latest)

                resp_stale = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 14,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Click custom canvas target using stale visual snapshot",
                            "pid": 42,
                            "window_id": 100,
                            "mode": "deterministic",
                            "source_snapshot_id": snap_w100_old,
                            "candidates": [{
                                "id": "canvas.hit",
                                "description": "Click custom canvas control at (950, 720)",
                                "snapshot_id": snap_w100_old,
                                "tool": "click",
                                "x": 950,
                                "y": 720,
                            }],
                        },
                    },
                })
                sc_stale = resp_stale["result"]["structuredContent"]
                self.assertTrue(resp_stale["result"]["isError"])
                self.assertEqual(sc_stale["status"], "stale_snapshot")
                self.assertFalse(sc_stale["action_dispatched"])
                self.assertIn("stale_snapshot_id", sc_stale["reason"])
                self.assertEqual(len(read_logged_clicks()), 0)

                # 4. Valid candidate bound to latest visual inspection snapshot snap_w100_latest
                resp = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 15,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Click custom canvas target at Retina pixel (950, 720)",
                            "pid": 42,
                            "window_id": 100,
                            "mode": "deterministic",
                            "source_snapshot_id": snap_w100_latest,
                            "candidates": [{
                                "id": "canvas.hit",
                                "description": "Click custom canvas control at (950, 720)",
                                "snapshot_id": snap_w100_latest,
                                "tool": "click",
                                "x": 950,
                                "y": 720,
                            }],
                            "expected_postcondition": {"static_text_contains": "Canvas Activated"},
                        },
                    },
                })
            finally:
                server.close()

            sc = resp["result"]["structuredContent"]
            self.assertFalse(resp["result"]["isError"])
            self.assertEqual(sc["status"], "verified")
            self.assertTrue(sc["action_dispatched"])
            self.assertTrue(sc["point_fallback_used"])
            self.assertEqual(sc["source_snapshot_id"], snap_w100_latest)
            self.assertEqual(sc["dispatched_arguments"]["x"], 950)
            self.assertEqual(sc["dispatched_arguments"]["y"], 720)
            self.assertEqual(sc["dispatched_arguments"]["delivery_mode"], "background")
            clicks = read_logged_clicks()
            self.assertEqual(len(clicks), 1)
            self.assertEqual(clicks[0]["args"]["x"], 950)
            self.assertEqual(clicks[0]["args"]["y"], 720)

    def test_cross_window_same_pid_discovery_t7a_stdio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            modal_driver = os.path.join(tmp, "modal-driver")
            with open(modal_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent("""\
                    #!/usr/bin/env python3
                    import json, sys
                    modal_open = False
                    modal_applied = False
                    snap_n = 0
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18"}}), flush=True)
                        elif method == "tools/call":
                            name = msg["params"]["name"]
                            args = msg["params"].get("arguments", {})
                            if name == "get_window_state":
                                snap_n += 1
                                wid = args.get("window_id")
                                snap = f"s0000050{snap_n}"
                                if wid == 100:
                                    sc = {
                                        "pid": 42,
                                        "window_id": 100,
                                        "snapshot_id": snap,
                                        "elements_complete": True,
                                        "truncated": False,
                                        "elements": [
                                            {"element_index": 0, "role": "AXWindow", "label": "Main App"},
                                            {"element_index": 2, "role": "AXButton", "label": "Open Preferences", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                                        ],
                                    }
                                    txt = 'AXStaticText = "Main Window"'
                                else:
                                    status_txt = "Preferences Applied" if modal_applied else "Preferences Modal"
                                    sc = {
                                        "pid": 42,
                                        "window_id": 205,
                                        "snapshot_id": snap,
                                        "elements_complete": True,
                                        "truncated": False,
                                        "elements": [
                                            {"element_index": 0, "role": "AXWindow", "label": "Preferences Sheet"},
                                            {"element_index": 3, "role": "AXButton", "label": "Confirm Preferences", "enabled": True, "parent_index": 0, "actions": ["AXPress"]},
                                        ],
                                    }
                                    txt = f'AXStaticText = "{status_txt}"'
                                res = {"content": [{"type": "text", "text": txt}], "structuredContent": sc}
                            elif name == "click":
                                if args.get("window_id") == 100:
                                    modal_open = True
                                elif args.get("window_id") == 205:
                                    modal_applied = True
                                res = {"content": [{"type": "text", "text": "clicked"}]}
                            elif name == "list_windows":
                                wins = [{"pid": 42, "window_id": 100, "title": "Main App", "app_name": "TestApp"}]
                                if modal_open:
                                    wins.append({"pid": 42, "window_id": 205, "title": "Preferences Sheet", "app_name": "TestApp", "role": "AXSheet"})
                                res = {"content": [{"type": "text", "text": json.dumps(wins)}], "structuredContent": {"windows": wins}}
                            else:
                                res = {}
                            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
                """))
            os.chmod(modal_driver, os.stat(modal_driver).st_mode | stat.S_IEXEC)

            server = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": modal_driver, "TYPESAFE_API_KEY": "test-key"},
                keychain_reader=lambda _: None,
            )
            try:
                # Step 1 on window 100 opens modal window 205; main window 100 AX tree is unchanged,
                # so jev_step auto-discovers window 205 for the same PID.
                step1 = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Open Preferences dialog",
                            "pid": 42,
                            "window_id": 100,
                            "mode": "deterministic",
                            "candidates": [{"id": "open.prefs", "description": "Click Open Preferences", "tool": "click", "element_index": 2}],
                        },
                    },
                })
                sc1 = step1["result"]["structuredContent"]
                self.assertFalse(step1["result"]["isError"])
                self.assertTrue(sc1["state_changed"])
                self.assertEqual(sc1["verification_reason"], "new_same_pid_window_discovered")
                self.assertEqual(len(sc1["newly_opened_windows"]), 1)
                self.assertEqual(sc1["newly_opened_windows"][0]["window_id"], 205)

                # Step 2 binds to discovered modal window 205 and verifies completion
                step2 = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_step",
                        "arguments": {
                            "goal": "Confirm Preferences in sheet",
                            "pid": 42,
                            "window_id": 205,
                            "mode": "deterministic",
                            "candidates": [{"id": "confirm.prefs", "description": "Click Confirm Preferences", "tool": "click", "element_index": 3}],
                            "expected_postcondition": {"static_text_contains": "Preferences Applied"},
                        },
                    },
                })
                sc2 = step2["result"]["structuredContent"]
                self.assertFalse(step2["result"]["isError"])
                self.assertEqual(sc2["status"], "verified")
            finally:
                server.close()

    def test_scoped_browser_prepare_and_browser_step_t7b_and_safety_gates_stdio(self) -> None:
        # 1. Policy refusals for non-isolated mode, existing_profile strategy, foreground delivery, and non-loopback/file/data URLs
        self.assertTrue(jev.is_allowed_synthetic_browser_url("http://127.0.0.1:8123/form"))
        self.assertTrue(jev.is_allowed_synthetic_browser_url("http://localhost:8123/form"))
        self.assertTrue(jev.is_allowed_synthetic_browser_url("http://[::1]:8123/form"))
        self.assertTrue(jev.is_allowed_synthetic_browser_url("about:blank"))
        self.assertFalse(jev.is_allowed_synthetic_browser_url("file:///tmp/fixture.html"))
        self.assertFalse(jev.is_allowed_synthetic_browser_url("data:text/html,<h1>test</h1>"))
        self.assertFalse(jev.is_allowed_synthetic_browser_url("http://user:secret@127.0.0.1:8123/form"))
        self.assertFalse(jev.is_allowed_synthetic_browser_url("https://example.com/login"))

        with self.assertRaisesRegex(ValueError, "browser_mode_refused"):
            jev.validate_browser_prepare_args({"mode": "default", "url": "http://127.0.0.1:8080/test"})
        with self.assertRaisesRegex(ValueError, "browser_mode_refused"):
            jev.validate_browser_prepare_args({"strategy": {"kind": "existing_profile"}, "url": "http://127.0.0.1:8080/test"})
        with self.assertRaisesRegex(ValueError, "foreground_or_global_input_forbidden"):
            jev.validate_browser_prepare_args({"mode": "isolated", "delivery_mode": "foreground"})
        with self.assertRaisesRegex(ValueError, "external_browser_url_forbidden"):
            jev.validate_browser_prepare_args({"mode": "isolated", "url": "https://example.com/login"})
        with self.assertRaisesRegex(ValueError, "external_browser_url_forbidden"):
            jev.validate_browser_prepare_args({"mode": "isolated", "url": "file:///tmp/fixture.html"})
        with self.assertRaisesRegex(ValueError, "external_browser_url_forbidden"):
            jev.validate_browser_prepare_args({"mode": "isolated", "url": "data:text/html,<h1>fixture</h1>"})

        with tempfile.TemporaryDirectory() as tmp:
            driver_log = os.path.join(tmp, "browser_driver_calls.jsonl")
            browser_driver = os.path.join(tmp, "browser-driver")
            with open(browser_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(f"""\
                    #!/usr/bin/env python3
                    import json, sys
                    log_path = {driver_log!r}
                    snap_n = 10
                    email_val = ""
                    submitted = False
                    nav_url = "about:blank"
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": {{"protocolVersion": "2025-06-18"}}}}), flush=True)
                        elif method == "tools/call":
                            name = msg["params"]["name"]
                            args = msg["params"].get("arguments", {{}})
                            with open(log_path, "a", encoding="utf-8") as lf:
                                lf.write(json.dumps({{"name": name, "args": args}}) + "\\n")
                            if name == "browser_prepare":
                                res = {{
                                    "content": [{{"type": "text", "text": "prepared"}}],
                                    "structuredContent": {{
                                        "prepared_pid": 9100,
                                        "endpoint_ownership": {{"owner_pid": 9100}},
                                    }},
                                }}
                            elif name == "list_windows":
                                res = {{
                                    "content": [{{"type": "text", "text": "[]"}}],
                                    "structuredContent": {{
                                        "windows": [{{"pid": 9100, "window_id": 4401, "title": "Chromium"}}],
                                    }},
                                }}
                            elif name == "browser_navigate":
                                nav_url = args.get("url") or nav_url
                                res = {{"content": [{{"type": "text", "text": "navigated"}}]}}
                            elif name == "get_browser_state":
                                snap_n += 1
                                snap = f"p{{snap_n}}"
                                status_txt = f"Saved profile for {{email_val}}" if submitted else "Profile Form"
                                res = {{
                                    "content": [{{"type": "text", "text": status_txt}}],
                                    "structuredContent": {{
                                        "target_id": "t_iso_1",
                                        "tabs": [{{"tab_id": "tab_1", "active": True}}],
                                        "snapshot": {{"id": snap, "format": args.get("snapshot_format") or "semantic_v2"}},
                                        "page": {{"url": nav_url, "title": "Synthetic Browser Fixture"}},
                                        "content_refs": [{{"ref": f"{{snap}}:c0", "name": status_txt}}],
                                        "refs": [
                                            {{"ref": "p11:99", "name": None, "role": "generic", "visibility": "unknown"}},
                                            {{"ref": "p11:0", "name": "Display Name", "role": "textbox", "actions": ["type", "pointer"], "value": email_val, "visibility": "in_viewport"}},
                                            {{"ref": "p11:1", "name": "Password", "role": "textbox", "type": "password", "actions": ["type"], "value": "••••••••", "visibility": "in_viewport"}},
                                            {{"ref": "p11:2", "name": "Save Draft", "role": "button", "actions": ["pointer"], "visibility": "in_viewport"}},
                                            {{"ref": "p11:3", "name": "Place Order", "role": "button", "actions": ["pointer"], "visibility": "in_viewport"}},
                                        ],
                                    }},
                                }}
                            elif name == "browser_type":
                                if "snapshot_id" in args:
                                    res = {{"isError": True, "content": [{{"type": "text", "text": "unexpected snapshot_id in browser_type"}}]}}
                                else:
                                    if args.get("ref") == "p11:0":
                                        email_val = str(args.get("text") or "")
                                    res = {{"content": [{{"type": "text", "text": "typed"}}]}}
                            elif name == "browser_click":
                                if "snapshot_id" in args:
                                    res = {{"isError": True, "content": [{{"type": "text", "text": "unexpected snapshot_id in browser_click"}}]}}
                                else:
                                    if args.get("ref") == "p11:2":
                                        submitted = True
                                    res = {{"content": [{{"type": "text", "text": "clicked"}}]}}
                            else:
                                res = {{}}
                            print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": res}}), flush=True)
                """))
            os.chmod(browser_driver, os.stat(browser_driver).st_mode | stat.S_IEXEC)

            server = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": browser_driver, "TYPESAFE_API_KEY": "test-key"},
                keychain_reader=lambda _: None,
            )
            try:
                prep = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_browser_prepare",
                        "arguments": {"mode": "isolated", "url": "http://127.0.0.1:8123/form"},
                    },
                })
                prep_sc = prep["result"]["structuredContent"]
                self.assertFalse(prep["result"]["isError"])
                self.assertEqual(prep_sc["prepared_pid"], 9100)
                self.assertEqual(prep_sc["window_id"], 4401)
                self.assertEqual(prep_sc["target_id"], "t_iso_1")
                self.assertEqual(prep_sc["tab_id"], "tab_1")
                self.assertEqual(prep_sc["url"], "http://127.0.0.1:8123/form")

                # Step 1: browser_type into p11:0 and verify element_value_equals
                bstep1 = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_browser_step",
                        "arguments": {
                            "goal": "Enter display name Alice",
                            "target_id": "t_iso_1",
                            "tab_id": "tab_1",
                            "mode": "deterministic",
                            "candidates": [{
                                "id": "btype.p11_0",
                                "description": "Type Alice into Display Name",
                                "tool": "browser_type",
                                "ref": "p11:0",
                                "text": "Alice",
                            }],
                            "expected_postcondition": {"element_value_equals": "Alice"},
                        },
                    },
                })
                bsc1 = bstep1["result"]["structuredContent"]
                self.assertFalse(bstep1["result"]["isError"])
                self.assertEqual(bsc1["status"], "verified")
                self.assertEqual(bsc1["dispatched_arguments"]["text"], "<redacted>")
                self.assertTrue(bsc1["dispatched_arguments"]["replace"])

                # Step 2: targeting secure password field p11:1 is refused before dispatch
                bstep_sec = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_browser_step",
                        "arguments": {
                            "goal": "Type into Password",
                            "target_id": "t_iso_1",
                            "tab_id": "tab_1",
                            "mode": "deterministic",
                            "candidates": [{
                                "id": "btype.pw",
                                "description": "Type into Password",
                                "tool": "browser_type",
                                "ref": "p11:1",
                                "text": "hunter2",
                            }],
                        },
                    },
                })
                self.assertTrue(bstep_sec["result"]["isError"])
                self.assertEqual(bstep_sec["result"]["structuredContent"]["status"], "candidate_refused")
                self.assertIn("secure_element_refused", bstep_sec["result"]["structuredContent"]["reason"])

                # Step 3: browser_click on p11:2 ("Save Draft") and verify static_text_contains from content_refs
                bstep2 = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_browser_step",
                        "arguments": {
                            "goal": "Click Save Draft",
                            "target_id": "t_iso_1",
                            "tab_id": "tab_1",
                            "mode": "deterministic",
                            "candidates": [{
                                "id": "bclick.p11_2",
                                "description": "Click Save Draft",
                                "tool": "browser_click",
                                "ref": "p11:2",
                            }],
                            "expected_postcondition": {"static_text_contains": "Saved profile for Alice"},
                        },
                    },
                })
                bsc2 = bstep2["result"]["structuredContent"]
                self.assertFalse(bstep2["result"]["isError"])
                self.assertEqual(bsc2["status"], "verified")
                self.assertEqual(bsc2["dispatched_arguments"]["delivery_mode"], "background")
                self.assertEqual(bsc2["dispatched_arguments"]["input_route"], "dom_event")

                # Step 4: Commit-action trap ("Place Order" on p11:3) is refused before dispatch
                bstep_trap = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_browser_step",
                        "arguments": {
                            "goal": "Click Place Order",
                            "target_id": "t_iso_1",
                            "tab_id": "tab_1",
                            "mode": "deterministic",
                            "candidates": [{
                                "id": "bclick.p11_3",
                                "description": "Click Place Order",
                                "tool": "browser_click",
                                "ref": "p11:3",
                            }],
                        },
                    },
                })
                trap_sc = bstep_trap["result"]["structuredContent"]
                self.assertTrue(bstep_trap["result"]["isError"])
                self.assertEqual(trap_sc["status"], "candidate_refused")
                self.assertIn("commit_action_forbidden_by_safety_policy", trap_sc["reason"])
            finally:
                server.close()

            with open(driver_log, "r", encoding="utf-8") as lf:
                calls = [json.loads(line) for line in lf if line.strip()]
            prep_call = next(c for c in calls if c["name"] == "browser_prepare")
            self.assertEqual(prep_call["args"]["allow_launch"], True)
            self.assertEqual(prep_call["args"]["profile"], {"mode": "isolated_new"})

    def test_search_field_label_synthesis_and_non_boolean_checkbox_filtering(self):
        raw_1p = {
            "structuredContent": {
                "pid": 77,
                "window_id": 501,
                "snapshot_id": "s00001a5b",
                "app_name": "BenchFixture",
                "window_title": "AGYCuaBench-1password-twin",
                "tree_markdown": "\n".join([
                    '- [0] AXWindow (AGYCuaBench-1password-twin)',
                    '  - AXStaticText = "1Password — Vault Item Manager (Synthetic Twin)"',
                    '  - AXStaticText = "Vault:"',
                    '  - [1] AXPopUpButton (Vault Selector) = "Personal"',
                    '  - [2] AXTextField = "Search Vault Items"',
                    '    - [3] AXButton (search)',
                    '  - [8] AXCheckBox (Require Touch ID Prompt) = "0"',
                ]),
                "elements": [
                    {"element_index": 0, "role": "AXWindow", "label": "AGYCuaBench-1password-twin"},
                    {"element_index": 1, "role": "AXPopUpButton", "label": "Vault Selector", "value": "Personal", "enabled": True, "parent_index": 0},
                    {"element_index": 2, "role": "AXTextField", "label": "Search Vault Items", "value": "Search Vault Items", "enabled": True, "parent_index": 0},
                    {"element_index": 3, "role": "AXButton", "label": "search", "enabled": True, "parent_index": 2},
                    {"element_index": 8, "role": "AXCheckBox", "label": "Require Touch ID Prompt", "value": "0", "enabled": True, "parent_index": 0},
                ],
            },
            "content": [],
        }
        obs = jev.extract_window_observation(raw_1p, expected_pid=77, expected_window_id=501)
        sv_cands = jev.build_bound_candidates(None, obs, action_value="prod-db")
        sv_ids = [c["id"] for c in sv_cands]
        self.assertIn("sv.2.search-vault-items", sv_ids)
        self.assertNotIn("sv.8.require-touch-id-prompt", sv_ids)

        bool_cands = jev.build_bound_candidates(None, obs, action_value="true")
        bool_ids = [c["id"] for c in bool_cands]
        self.assertIn("sv.8.require-touch-id-prompt", bool_ids)

    def test_observe_refuses_screenshot_on_secure_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sec_driver = os.path.join(tmp, "sec-driver")
            with open(sec_driver, "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent("""\
                    #!/usr/bin/env python3
                    import json, sys
                    for line in sys.stdin:
                        msg = json.loads(line)
                        if "id" not in msg:
                            continue
                        method = msg.get("method")
                        if method == "initialize":
                            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"protocolVersion": "2025-06-18"}}), flush=True)
                        elif method == "tools/call":
                            name = msg["params"]["name"]
                            if name == "get_window_state":
                                sc = {
                                    "pid": 77,
                                    "window_id": 501,
                                    "snapshot_id": "s00001a99",
                                    "elements_complete": True,
                                    "truncated": False,
                                    "elements": [
                                        {"element_index": 0, "role": "AXWindow", "label": "1Password Vault"},
                                        {"element_index": 1, "role": "AXSecureTextField", "label": "Master Vault Password", "value": "CANARY-VAULT-SECRET"},
                                    ],
                                }
                                content = [
                                    {"type": "text", "text": 'AXStaticText = "Vault"\\n- [1] AXSecureTextField (Master Vault Password) = "CANARY-VAULT-SECRET"'},
                                    {"type": "image", "mimeType": "image/png", "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="},
                                ]
                                res = {"content": content, "structuredContent": sc}
                            else:
                                res = {"content": [{"type": "text", "text": "[]"}], "structuredContent": {"windows": []}}
                            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}), flush=True)
                """))
            os.chmod(sec_driver, os.stat(sec_driver).st_mode | stat.S_IEXEC)

            server = jev.JevMcpServer(
                env={"AGY_CUA_DRIVER": sec_driver, "TYPESAFE_API_KEY": "test-key"},
                keychain_reader=lambda _: None,
            )
            try:
                resp = server.handle_message({
                    "jsonrpc": "2.0",
                    "id": 91,
                    "method": "tools/call",
                    "params": {
                        "name": "jev_observe",
                        "arguments": {"pid": 77, "window_id": 501, "include_screenshot": True},
                    },
                })
            finally:
                server.close()

            self.assertTrue(resp["result"]["isError"])
            sc = resp["result"]["structuredContent"]
            self.assertEqual(sc["status"], "screenshot_refused_secure_window")
            self.assertEqual(sc["secure_fields_redacted_count"], 1)
            image_blocks = [c for c in resp["result"]["content"] if c.get("type") == "image"]
            self.assertEqual(len(image_blocks), 0)

    def test_benchmark_resume_key_and_focus_monitor_browser_tracking(self) -> None:
        from pathlib import Path
        from unittest import mock

        bench_path = os.path.join(ROOT, "scripts", "benchmark_cua.py")
        with mock.patch.dict(
            sys.modules,
            {"dotenv": None, "google": None, "google.genai": None, "PIL": None},
        ):
            loader = importlib.machinery.SourceFileLoader("benchmark_cua_test_mod", bench_path)
            spec = importlib.util.spec_from_loader("benchmark_cua_test_mod", loader)
            assert spec is not None
            bench_mod = importlib.util.module_from_spec(spec)
            sys.modules["benchmark_cua_test_mod"] = bench_mod
            loader.exec_module(bench_mod)

        # Thread 5: completed failures across ANY arm are preserved on resume, while fatal error aborts without arm_failed are not
        self.assertTrue(bench_mod.is_resumable_completed_row({"arm": "agy-cua-direct", "passed": False}))
        self.assertTrue(bench_mod.is_resumable_completed_row({"arm": "agy-cua-jev-v0", "passed": False, "arm_failed": True, "error": "ArmActionError: boom"}))
        self.assertFalse(bench_mod.is_resumable_completed_row({"arm": "agy-cua-jev-v1", "passed": False, "error": "RuntimeError: budget ceiling exceeded"}))

        # Thread 6: resume key includes thinking_level / thinking_budget and rejects legacy rows missing both
        k_low = bench_mod.extract_prior_row_resume_key({"repetition": 1, "model": "gemini-4-argon", "thinking_level": "low", "task_id": "T1", "arm": "agy-cua-jev-v1"})
        k_high = bench_mod.extract_prior_row_resume_key({"repetition": 1, "model": "gemini-4-argon", "thinking_level": "high", "task_id": "T1", "arm": "agy-cua-jev-v1"})
        k_budget = bench_mod.extract_prior_row_resume_key(
            {"repetition": 1, "model": "gemini-3.8-flash", "thinking_level": None, "thinking_budget": 512, "task_id": "T1", "arm": "agy-cua-jev-v1"}
        )
        k_legacy = bench_mod.extract_prior_row_resume_key({"repetition": 1, "model": "gemini-3.8-flash", "task_id": "T1", "arm": "agy-cua-jev-v1"})
        self.assertEqual(k_low, (1, "gemini-4-argon", "level:low", "T1", "agy-cua-jev-v1"))
        self.assertEqual(k_high, (1, "gemini-4-argon", "level:high", "T1", "agy-cua-jev-v1"))
        self.assertNotEqual(k_low, k_high)
        self.assertEqual(k_budget, (1, "gemini-3.8-flash", "budget:512", "T1", "agy-cua-jev-v1"))
        self.assertIsNone(k_legacy)

        # Multi-file --resume-from: chained summaries do not double-count, independent summaries sum spend
        with tempfile.TemporaryDirectory() as td:
            p_a = Path(td) / "a.json"
            p_b_chained = Path(td) / "b_chained.json"
            p_b_indep = Path(td) / "b_indep.json"
            p_a.write_text(
                json.dumps(
                    {
                        "resumed_from": None,
                        "total_spend_usd": 1.25,
                        "reserved_usd": 0.025,
                        "gemini_input_tokens": 1000,
                        "gemini_output_tokens": 200,
                        "gemini_thought_tokens": 50,
                        "jev_input_tokens": 500,
                        "jev_output_tokens": 100,
                        "gemini_uncertain_failures": 1,
                        "jev_uncertain_failures": 0,
                        "results": [
                            {
                                "repetition": 1,
                                "model": "gemini-3.8-flash",
                                "thinking_level": "low",
                                "task_id": "T1",
                                "arm": "agy-cua-jev-v1",
                                "passed": True,
                                "screenshots_sent": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            p_b_chained.write_text(
                json.dumps(
                    {
                        "resumed_from": str(p_a),
                        "total_spend_usd": 2.00,
                        "reserved_usd": 0.025,
                        "gemini_input_tokens": 1800,
                        "gemini_output_tokens": 350,
                        "gemini_thought_tokens": 80,
                        "jev_input_tokens": 900,
                        "jev_output_tokens": 180,
                        "gemini_uncertain_failures": 1,
                        "jev_uncertain_failures": 0,
                        "results": [
                            {
                                "repetition": 1,
                                "model": "gemini-3.8-flash",
                                "thinking_level": "low",
                                "task_id": "T2",
                                "arm": "agy-cua-jev-v1",
                                "passed": True,
                                "screenshots_sent": 3,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            p_b_indep.write_text(
                json.dumps(
                    {
                        "resumed_from": None,
                        "total_spend_usd": 0.75,
                        "reserved_usd": 0.002,
                        "gemini_input_tokens": 800,
                        "gemini_output_tokens": 150,
                        "gemini_thought_tokens": 30,
                        "jev_input_tokens": 400,
                        "jev_output_tokens": 80,
                        "gemini_uncertain_failures": 0,
                        "jev_uncertain_failures": 1,
                        "results": [
                            {
                                "repetition": 1,
                                "model": "gemini-4-argon",
                                "thinking_level": "low",
                                "task_id": "T1",
                                "arm": "agy-cua-jev-v1",
                                "passed": True,
                                "screenshots_sent": 4,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            spend_chained = bench_mod.SpendTracker(max_budget_usd=5.0)
            rows_chained = bench_mod.restore_resume_state([p_a, p_b_chained], spend_chained)
            self.assertEqual(len(rows_chained), 2)
            self.assertAlmostEqual(spend_chained.total_usd, 2.00, places=5)
            self.assertEqual(spend_chained.gemini_input_tokens, 1800)
            self.assertEqual(spend_chained.screenshots_sent, 5)

            spend_reversed = bench_mod.SpendTracker(max_budget_usd=5.0)
            rows_reversed = bench_mod.restore_resume_state([p_b_chained, p_a], spend_reversed)
            self.assertEqual(len(rows_reversed), 2)
            self.assertAlmostEqual(spend_reversed.total_usd, 2.00, places=5)
            self.assertEqual(spend_reversed.gemini_input_tokens, 1800)
            self.assertEqual(spend_reversed.screenshots_sent, 5)

            spend_repeated = bench_mod.SpendTracker(max_budget_usd=5.0)
            rows_repeated = bench_mod.restore_resume_state([p_a, p_a], spend_repeated)
            self.assertEqual(len(rows_repeated), 1)
            self.assertAlmostEqual(spend_repeated.total_usd, 1.25, places=5)
            self.assertEqual(spend_repeated.gemini_input_tokens, 1000)
            self.assertEqual(spend_repeated.screenshots_sent, 2)

            spend_indep = bench_mod.SpendTracker(max_budget_usd=5.0)
            rows_indep = bench_mod.restore_resume_state([p_a, p_b_indep], spend_indep)
            self.assertEqual(len(rows_indep), 2)
            self.assertAlmostEqual(spend_indep.total_usd, 2.00, places=5)
            self.assertEqual(spend_indep.gemini_input_tokens, 1800)
            self.assertEqual(spend_indep.gemini_uncertain_failures, 1)
            self.assertEqual(spend_indep.jev_uncertain_failures, 1)
            self.assertEqual(spend_indep.screenshots_sent, 6)

        # Thread 7: FocusMonitor detects isolated browser PID or newly foregrounded browser bundle while preserving operator's initial frontmost app
        mon = bench_mod.FocusMonitor(target_pid=100, target_bundle="com.agycua.benchfixture", monitor_browser=True)
        mon.register_pid(250)
        samples_seq = [
            (900, "com.apple.Terminal"),     # initial operator frontmost app: 0 violations
            (250, "org.chromium.Chromium"),  # registered isolated browser PID becomes frontmost: violation!
            (900, "com.apple.Terminal"),     # back to operator app: 0 new violations
            (333, "org.chromium.Chromium"),  # unregistered Chromium PID steals focus from Terminal: violation!
        ]
        orig_sample = bench_mod.sample_frontmost_info
        try:
            it = iter(samples_seq)
            bench_mod.sample_frontmost_info = lambda: next(it)
            for _ in samples_seq:
                mon.sample_once()
        finally:
            bench_mod.sample_frontmost_info = orig_sample
        self.assertEqual(mon.violations, 2)


if __name__ == "__main__":
    unittest.main()

