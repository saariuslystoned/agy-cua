import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "metadata", Path(__file__).resolve().parents[1] / "scripts/stream-metadata.py"
)
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)


class StreamMetadataTest(unittest.TestCase):
    def test_retains_real_recovery_discriminators_without_window_content(self):
        payload = {"schema": "agy_cua.jev_observe_v1", "status": "ok",
                   "query_status": "truncated_before_match", "query_incomplete": True,
                   "search_complete": False, "diff_fallback_used": True,
                   "is_diff": False, "observe_ms": 17.2,
                   "discovered_windows": [{"window_id": 123, "title": "PRIVATE_SENTINEL"}],
                   "window_title": "PRIVATE_SENTINEL", "elements": ["PRIVATE_SENTINEL"],
                   "pid": 123, "image": "PRIVATE_SENTINEL"}
        result = metadata.summarize([json.dumps({"step_update": {
            "step_type": "tool", "step_index": 1, "state": "DONE",
            "tool_info": {"parameters": {"ServerName": "agy-cua-jev", "ToolName": "jev_observe"},
                          "output": json.dumps(payload)}}})])
        entry = result["jev_entries"][0]
        self.assertTrue(entry["query_incomplete"])
        self.assertFalse(entry["search_complete"])
        self.assertTrue(entry["diff_fallback_used"])
        self.assertEqual(entry["query_status"], "truncated_before_match")
        self.assertEqual(entry["discovered_windows_count"], 1)
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))
        self.assertNotIn("pid", entry)

    def test_deduplicates_updates_without_retaining_arguments_or_outputs(self):
        events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "tool",
                    "step_index": 1,
                    "state": "RUNNING",
                    "tool_name": "call_mcp_tool",
                    "tool_info": {
                        "parameters": {
                            "ServerName": "agy-cua",
                            "ToolName": "click",
                            "Arguments": "PRIVATE_SENTINEL",
                        }
                    },
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "tool",
                    "step_index": 1,
                    "state": "DONE",
                    "tool_info": {"output": "PRIVATE_SENTINEL"},
                },
            },
            {
                "event": "result",
                "result": {
                    "response": "PRIVATE_SENTINEL",
                    "status": "SUCCESS",
                    "usage": {"total_tokens": 12, "unrecognized_text": "PRIVATE_SENTINEL"},
                },
            },
        ]
        result = metadata.summarize(map(json.dumps, events))
        self.assertEqual(
            result["tools"],
            [{"index": 1, "state": "DONE", "server": "agy-cua", "tool": "click"}],
        )
        self.assertTrue(result["result_seen"])
        self.assertEqual(result["token_usage"], {"total_tokens": 12})
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))

    def test_missing_result_and_invalid_stream_are_not_success(self):
        result = metadata.summarize(["not json", "[]"])
        self.assertFalse(result["result_seen"])
        self.assertEqual(result["parse_errors"], 2)

    def test_error_state_is_retained(self):
        result = metadata.summarize(
            [
                json.dumps(
                    {
                        "step_update": {
                            "step_type": "tool",
                            "step_index": 2,
                            "state": "ERROR",
                            "tool_name": "view_file",
                        }
                    }
                )
            ]
        )
        self.assertEqual(result["tools"][0]["state"], "ERROR")

    def test_extracts_sanitized_jev_entries_across_step_updates(self):
        jev_payload = {
            "schema": "agy_cua.jev_step_v1",
            "status": "verified",
            "verified": True,
            "returned_model": "jev-1.13.0",
            "sdk_identity": "openai-python/2.26.0",
            "selected_id": "click-14-equals",
            "reason": "PRIVATE_SENTINEL",
            "dispatch_output": "PRIVATE_SENTINEL",
            "accounting": {
                "halted": False,
                "uncertain_calls": 0,
                "uncertain_side_effects": 0,
                "secret": "PRIVATE_SENTINEL",
            },
            "timings_ms": {
                "observe_ms": 11.2,
                "provider_ms": 210.5,
                "dispatch_ms": 1004.1,
                "verify_ms": 9.8,
            },
            "post_static_texts": ["4,320"],
        }
        events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "tool",
                    "step_index": 10,
                    "state": "ACTIVE",
                    "tool_name": "call_mcp_tool",
                    "tool_info": {
                        "parameters": {
                            "ServerName": "agy-cua_agy-cua-jev",
                            "ToolName": "jev_step",
                            "Arguments": "PRIVATE_SENTINEL",
                        }
                    },
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "tool",
                    "step_index": 10,
                    "state": "DONE",
                    "tool_name": "call_mcp_tool",
                    "tool_info": {"output": "Result:\n" + json.dumps(jev_payload)},
                },
            },
        ]
        result = metadata.summarize(map(json.dumps, events))
        self.assertEqual(len(result["jev_entries"]), 1)
        entry = result["jev_entries"][0]
        self.assertEqual(entry["schema"], "agy_cua.jev_step_v1")
        self.assertEqual(entry["returned_model"], "jev-1.13.0")
        self.assertEqual(entry["selected_id"], "click-14-equals")
        self.assertEqual(entry["post_static_texts"], ["4,320"])
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))

    def test_extracts_sanitized_jev_batch_entry(self):
        batch_payload = {
            "schema": "agy_cua.jev_batch_v1",
            "status": "verified",
            "verification_outcome": "goal_verified",
            "verified": True,
            "returned_model": "jev-1.13.0",
            "sdk_identity": "agy-cua-jev-stdlib/0.2.0",
            "selected_ids": ["ax.2.all-clear", "ax.5.7"],
            "completed_count": 2,
            "total_steps": 2,
            "post_static_texts": ["7"],
            "completed_steps": [
                {
                    "step_index": 0,
                    "goal": "PRIVATE_SENTINEL",
                    "status": "verified",
                    "verification_outcome": "goal_verified",
                    "verified": True,
                    "selected_id": "ax.2.all-clear",
                    "returned_model": "jev-1.13.0",
                    "sdk_identity": "agy-cua-jev-stdlib/0.2.0",
                    "post_static_texts": ["0"],
                    "timings_ms": {"observe_ms": 5.0, "provider_ms": 100.0, "dispatch_ms": 10.0, "verify_ms": 5.0},
                }
            ],
            "accounting": {"halted": False, "uncertain_calls": 0, "uncertain_side_effects": 0},
            "timings_ms": {"observe_ms": 10.0, "provider_ms": 100.0, "dispatch_ms": 20.0, "verify_ms": 10.0},
        }
        events = [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "tool",
                    "step_index": 11,
                    "state": "DONE",
                    "tool_name": "call_mcp_tool",
                    "tool_info": {
                        "parameters": {"ServerName": "agy-cua-jev", "ToolName": "jev_batch"},
                        "output": "Result:\n" + json.dumps(batch_payload),
                    },
                },
            }
        ]
        result = metadata.summarize(map(json.dumps, events))
        self.assertEqual(len(result["jev_entries"]), 1)
        entry = result["jev_entries"][0]
        self.assertEqual(entry["schema"], "agy_cua.jev_batch_v1")
        self.assertEqual(entry["returned_model"], "jev-1.13.0")
        self.assertEqual(entry["selected_ids"], ["ax.2.all-clear", "ax.5.7"])
        self.assertEqual(len(entry["completed_steps"]), 1)
        self.assertEqual(entry["completed_steps"][0]["selected_id"], "ax.2.all-clear")
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
