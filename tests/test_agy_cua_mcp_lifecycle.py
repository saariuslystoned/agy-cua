"""Actual launcher shutdown with only a registered fictional Linux child."""
import json
import hashlib
import os
from pathlib import Path
import platform
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unittest

if sys.platform == "linux":
    import resource

SHIM = Path(__file__).resolve().parents[1] / "bin" / "agy-cua-mcp"
READ_SYSCALL = {"x86_64": "0", "aarch64": "63"}.get(platform.machine())
DRIVER = '''import json, os, signal, sys, time
from pathlib import Path
root = Path(os.environ["FIXTURE_ROOT"])
def terminate(signum, frame):
    (root / "signal").write_text(str(signum))
    raise SystemExit(0)
signal.signal(signal.SIGTERM, terminate)
request = json.loads(sys.stdin.readline())
print(json.dumps({"jsonrpc":"2.0", "id":request["id"], "result":{
    "protocolVersion":"2025-06-18", "capabilities":{},
    "serverInfo":{"name":"fictional", "version":"1"}}}), flush=True)
while not (root / "exit").exists():
    time.sleep(0.01)
raise SystemExit(int((root / "exit").read_text()))
'''


@unittest.skipUnless(sys.platform == "linux" and READ_SYSCALL is not None,
                     "requires the Linux read(0) observation")
class LauncherLifecycleTest(unittest.TestCase):
    def wait_for_stdin_read(self, pid):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            for task in Path(f"/proc/{pid}/task").iterdir():
                try:
                    call = (task / "syscall").read_text().split()
                    if call and call[0] == READ_SYSCALL and int(call[1], 16) == 0:
                        return
                except (FileNotFoundError, PermissionError, ValueError):
                    continue
            time.sleep(0.01)
        self.fail("launcher input reader did not reach read(0)")

    def test_child_exit_while_client_stdin_remains_open(self):
        for action in (0, 23, signal.SIGTERM, signal.SIGINT):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                driver = root / "fictional-driver"
                driver.write_text("#!" + sys.executable + "\n" + DRIVER)
                driver.chmod(0o700)
                proc = subprocess.Popen(
                    [sys.executable, str(SHIM)], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env={"PATH": os.defpath, "HOME": tmp,
                         "AGY_CUA_DRIVER": str(driver), "FIXTURE_ROOT": tmp},
                    start_new_session=True,
                    preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)),
                )
                try:
                    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2025-06-18",
                                          "capabilities": {}, "clientInfo": {
                                              "name": "fictional", "version": "1"}}}
                    proc.stdin.write((json.dumps(request) + "\n").encode())
                    proc.stdin.flush()
                    with selectors.DefaultSelector() as ready:
                        ready.register(proc.stdout, selectors.EVENT_READ)
                        self.assertTrue(ready.select(3), "missing initialization reply")
                    self.assertEqual(json.loads(proc.stdout.readline())["id"], 1)
                    self.wait_for_stdin_read(proc.pid)
                    if isinstance(action, signal.Signals):
                        proc.send_signal(action)
                    else:
                        sentinel = root / "exit.tmp"
                        sentinel.write_text(str(action))
                        sentinel.replace(root / "exit")
                    proc.wait(timeout=4)
                    self.assertFalse(proc.stdin.closed)
                    self.assertEqual(proc.returncode, action if type(action) is int else 0,
                                     proc.stderr.read().decode())
                    if isinstance(action, signal.Signals):
                        self.assertEqual((root / "signal").read_text(), str(signal.SIGTERM.value))
                finally:
                    # This new process group contains only this test's launcher and child.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=3)
                    for stream in (proc.stdin, proc.stdout, proc.stderr):
                        stream.close()

    def test_large_and_final_partial_lines_keep_exact_bytes(self):
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping",
                        "params": {"fictional": "α" * 2_000_000}}, ensure_ascii=False).encode() + b"\n",
            b'{"jsonrpc":"2.0","id":2,"method":"ping"}\n',
            b'{"jsonrpc":"2.0","id":3,"method":"ping"}',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            driver = Path(tmp) / "fictional-driver"
            driver.write_text("#!" + sys.executable + "\n"
                              "import hashlib, json, sys\n"
                              "lines = [[len(line), hashlib.sha256(line).hexdigest()] "
                              "for line in sys.stdin.buffer]\n"
                              "print(json.dumps({'jsonrpc': '2.0', 'method': 'fictional/observed', "
                              "'params': {'lines': lines}}))\n")
            driver.chmod(0o700)
            proc = subprocess.run(
                [sys.executable, str(SHIM)], input=b"".join(lines),
                capture_output=True, timeout=20,
                env={"PATH": os.defpath, "HOME": tmp, "AGY_CUA_DRIVER": str(driver)},
            )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(proc.stderr, b"")
        self.assertEqual(json.loads(proc.stdout)["params"]["lines"],
                         [[len(line), hashlib.sha256(line).hexdigest()] for line in lines])


if __name__ == "__main__":
    unittest.main()
