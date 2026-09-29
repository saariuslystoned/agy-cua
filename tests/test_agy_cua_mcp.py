"""Hermetic tests for bin/agy-cua-mcp. A fake driver stands in for cua-driver."""
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIM = os.path.join(ROOT, "bin", "agy-cua-mcp")

loader = importlib.machinery.SourceFileLoader("agy_cua_mcp", SHIM)
spec = importlib.util.spec_from_loader("agy_cua_mcp", loader)
shim = importlib.util.module_from_spec(spec)
loader.exec_module(shim)

FAKE_DRIVER = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, sys
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        name = msg.get("params", {}).get("name")
        if name == "get_window_state":
            result = {"content": [{"type": "text", "text": "window_id=1 pid=2 elements=1"}],
                      "structuredContent": {"snapshot_id": "s0000002a", "elements": []}}
        else:
            result = {"content": [{"type": "text", "text": "ok"}], "structuredContent": {"n": 1}}
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
""")


def window_state_response(snapshot_id="s0000002a"):
    return {"jsonrpc": "2.0", "id": 7, "result": {
        "content": [{"type": "text", "text": "window_id=1 pid=2 elements=1"}],
        "structuredContent": {"snapshot_id": snapshot_id}}}


class AnnotateTest(unittest.TestCase):
    def test_prepends_snapshot_id_to_first_text_block(self):
        pending = {7: True}
        message = shim.annotate(window_state_response(), pending)
        text = message["result"]["content"][0]["text"]
        self.assertTrue(text.startswith("snapshot_id=s0000002a "))
        self.assertIn("window_id=1 pid=2 elements=1", text)
        self.assertEqual(pending, {})

    def test_leaves_untracked_responses_alone(self):
        message = shim.annotate(window_state_response(), {})
        self.assertEqual(message["result"]["content"][0]["text"], "window_id=1 pid=2 elements=1")

    def test_leaves_error_responses_alone(self):
        error = {"jsonrpc": "2.0", "id": 7, "error": {"code": -32602, "message": "bad"}}
        self.assertEqual(shim.annotate(dict(error), {7: True}), error)

    def test_adds_text_block_when_result_has_none(self):
        message = {"jsonrpc": "2.0", "id": 7, "result": {"content": [], "structuredContent": {"snapshot_id": "s1"}}}
        message = shim.annotate(message, {7: True})
        self.assertTrue(message["result"]["content"][0]["text"].startswith("snapshot_id=s1 "))


class StdioTest(unittest.TestCase):
    def test_annotates_window_state_and_passes_other_tools_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "fake-driver")
            with open(fake, "w") as handle:
                handle.write(FAKE_DRIVER)
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_window_state", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "click", "arguments": {}}},
            ]
            proc = subprocess.run(
                [sys.executable, SHIM], input="".join(json.dumps(r) + "\n" for r in requests),
                capture_output=True, text=True, timeout=20,
                env={**os.environ, "AGY_CUA_DRIVER": fake},
            )
        responses = {m["id"]: m for m in map(json.loads, proc.stdout.splitlines())}
        self.assertTrue(responses[1]["result"]["content"][0]["text"].startswith("snapshot_id=s0000002a "))
        self.assertEqual(responses[2]["result"]["content"][0]["text"], "ok")


if __name__ == "__main__":
    unittest.main()
