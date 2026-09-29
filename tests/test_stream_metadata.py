import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "metadata", Path(__file__).resolve().parents[1] / "scripts/stream-metadata.py")
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)


class StreamMetadataTest(unittest.TestCase):
    def test_deduplicates_updates_without_retaining_arguments_or_outputs(self):
        events = [
            {"event": "step_update", "step_update": {"step_type": "tool", "step_index": 1,
             "state": "RUNNING", "tool_name": "call_mcp_tool", "tool_info": {"parameters": {
                 "ServerName": "agy-cua", "ToolName": "click", "Arguments": "PRIVATE_SENTINEL"}}}},
            {"event": "step_update", "step_update": {"step_type": "tool", "step_index": 1,
             "state": "DONE", "tool_info": {"output": "PRIVATE_SENTINEL"}}},
            {"event": "result", "result": {"response": "PRIVATE_SENTINEL", "status": "SUCCESS",
             "usage": {"total_tokens": 12, "unrecognized_text": "PRIVATE_SENTINEL"}}},
        ]
        result = metadata.summarize(map(json.dumps, events))
        self.assertEqual(result['tools'], [{"index": 1, "state": "DONE", "server": "agy-cua", "tool": "click"}])
        self.assertTrue(result['result_seen'])
        self.assertEqual(result['token_usage'], {"total_tokens": 12})
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result))

    def test_missing_result_and_invalid_stream_are_not_success(self):
        result = metadata.summarize(['not json', '[]'])
        self.assertFalse(result['result_seen'])
        self.assertEqual(result['parse_errors'], 2)

    def test_error_state_is_retained(self):
        result = metadata.summarize([json.dumps({'step_update': {
            'step_type': 'tool', 'step_index': 2, 'state': 'ERROR', 'tool_name': 'view_file'}})])
        self.assertEqual(result['tools'][0]['state'], 'ERROR')
