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
    args = sys.argv[1:]
    if "--claude-code-computer-use-compat" in args or "--session-policy" in args:
        sys.stderr.write("removed flag\\n")
        sys.exit(64)
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        name = msg.get("params", {}).get("name")
        if name == "get_window_state":
            result = {
                "content": [{"type": "text", "text": "window_id=1 pid=2 elements=1 snapshot_id=s0000002a\\n"}],
                "structuredContent": {"snapshot_id": "s0000002a", "elements": []},
            }
        else:
            result = {
                "content": [{"type": "text", "text": "ok"}],
                "structuredContent": {"argv": args},
            }
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
""")


class ArgvTest(unittest.TestCase):
    def test_prepends_direct_once_when_env_set(self):
        self.assertEqual(
            shim.build_driver_argv("/usr/bin/cua-driver", ["--socket", "/tmp/x.sock"], {"AGY_CUA_DRIVER_DIRECT": "1"}),
            ["/usr/bin/cua-driver", "mcp", "--direct", "--socket", "/tmp/x.sock"],
        )
        self.assertEqual(
            shim.build_driver_argv("/usr/bin/cua-driver", ["--direct"], {"AGY_CUA_DRIVER_DIRECT": "1"}),
            ["/usr/bin/cua-driver", "mcp", "--direct"],
        )
        self.assertEqual(
            shim.build_driver_argv("/usr/bin/cua-driver", ["--socket", "/tmp/x.sock"], {}),
            ["/usr/bin/cua-driver", "mcp", "--socket", "/tmp/x.sock"],
        )


class StdioPassThroughTest(unittest.TestCase):
    def test_passes_messages_through_unchanged_without_text_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "fake-driver")
            with open(fake, "w", encoding="utf-8") as handle:
                handle.write(FAKE_DRIVER)
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_window_state", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "click", "arguments": {}}},
            ]
            proc = subprocess.run(
                [sys.executable, SHIM, "--socket", "/tmp/test.sock"],
                input="".join(json.dumps(r) + "\n" for r in requests),
                capture_output=True,
                text=True,
                timeout=20,
                env={**os.environ, "AGY_CUA_DRIVER": fake, "AGY_CUA_DRIVER_DIRECT": "1"},
            )
        self.assertEqual(proc.returncode, 0)
        responses = {m["id"]: m for m in map(json.loads, proc.stdout.splitlines())}
        self.assertEqual(
            responses[1]["result"]["content"][0]["text"],
            "window_id=1 pid=2 elements=1 snapshot_id=s0000002a\n",
        )
        self.assertEqual(
            responses[2]["result"]["structuredContent"]["argv"],
            ["mcp", "--direct", "--socket", "/tmp/test.sock"],
        )

    def test_preserves_exit_code_64_on_removed_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "fake-driver")
            with open(fake, "w", encoding="utf-8") as handle:
                handle.write(FAKE_DRIVER)
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
            proc = subprocess.run(
                [sys.executable, SHIM, "--claude-code-computer-use-compat"],
                input="",
                capture_output=True,
                text=True,
                timeout=20,
                env={**os.environ, "AGY_CUA_DRIVER": fake},
            )
        self.assertEqual(proc.returncode, 64)
        self.assertIn("removed flag", proc.stderr)


if __name__ == "__main__":
    unittest.main()
