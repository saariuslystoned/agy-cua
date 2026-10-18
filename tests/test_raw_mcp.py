from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import raw_mcp


class RawSessionDeadlineTest(unittest.TestCase):
    def start(self, directory, reply_to_init, reply_to_call):
        driver = Path(directory) / "driver"
        driver.write_text(f'''#!{sys.executable}
import json, sys, time
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    respond = {reply_to_init!r} if request["method"] == "initialize" else {reply_to_call!r}
    if respond:
        print(json.dumps({{"jsonrpc": "2.0", "id": request["id"], "result": {{"ok": True}}}}), flush=True)
    else:
        time.sleep(60)
''')
        driver.chmod(0o700)
        children = []
        original = subprocess.Popen
        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        return driver, children, launch

    def test_initialization_timeout_reaps_owned_child(self):
        with tempfile.TemporaryDirectory() as directory:
            driver, children, launch = self.start(directory, False, False)
            started = time.monotonic()
            with patch.object(raw_mcp, "driver_path", return_value=str(driver)), patch.object(raw_mcp.subprocess, "Popen", side_effect=launch):
                with self.assertRaises(TimeoutError):
                    raw_mcp.Session(timeout_s=0.2)
            self.assertLess(time.monotonic() - started, 5)
            self.assertIsNotNone(children[0].poll())

    def test_request_timeout_reaps_owned_child(self):
        with tempfile.TemporaryDirectory() as directory:
            driver, children, launch = self.start(directory, True, False)
            with patch.object(raw_mcp, "driver_path", return_value=str(driver)), patch.object(raw_mcp.subprocess, "Popen", side_effect=launch):
                session = raw_mcp.Session(timeout_s=2)
                session.timeout_s = 0.2
                with self.assertRaises(TimeoutError):
                    session.call("never-respond", {})
                self.assertIsNotNone(children[0].poll())
                session.close()

    def test_successful_requests_and_close(self):
        with tempfile.TemporaryDirectory() as directory:
            driver, children, launch = self.start(directory, True, True)
            with patch.object(raw_mcp, "driver_path", return_value=str(driver)), patch.object(raw_mcp.subprocess, "Popen", side_effect=launch):
                session = raw_mcp.Session(timeout_s=2)
                self.assertEqual(session.call("read", {}), {"ok": True})
                session.close()
                self.assertIsNotNone(children[0].poll())


if __name__ == "__main__":
    unittest.main()
