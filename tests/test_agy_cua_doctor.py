"""Exercise the real doctor CLI with fictional external command replies."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


DOCTOR = Path(__file__).resolve().parents[1] / "bin" / "agy-cua"
STUB = r'''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
config = json.loads(pathlib.Path(os.environ["FIXTURE_CONFIG"]).read_text())
with open(os.environ["FIXTURE_CALLS"], "a") as log:
    log.write(json.dumps([name, args]) + "\n")
if name == "uname" and args == ["-s"]:
    print(config["platform"])
elif name == "sw_vers" and args == ["-productVersion"]:
    print("fictional-macos")
elif name == "agy" and args == ["--version"]:
    print("agy fictional")
elif name == "agy" and args == ["mcp", "list"]:
    print("NAME TYPE STATUS")
elif name == "driver" and args == ["--version"]:
    print("cua-driver fictional")
elif name == "driver" and args == ["permissions", "status", "--json"]:
    print(config["reply"])
elif name == "python3" and args == [config["jev"], "--check"]:
    print(json.dumps({"route": "fictional", "configured_model": "fixture",
                      "key_source": "fixture"}))
else:
    raise SystemExit("unexpected fixture command: " + repr([name, args]))
'''


class DoctorTest(unittest.TestCase):
    def run_doctor(self, platform, reply):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = root / "commands"
            commands.mkdir()
            home = root / "home"
            home.mkdir()
            config = root / "config.json"
            config.write_text(json.dumps({
                "platform": platform, "reply": reply,
                "jev": str(DOCTOR.with_name("agy-cua-jev-mcp")),
            }))
            calls = root / "calls.jsonl"
            for name in ("uname", "sw_vers", "agy", "driver", "python3"):
                path = commands / name
                path.write_text("#!" + sys.executable + "\n" + STUB)
                path.chmod(0o755)
            result = subprocess.run(
                ["/bin/bash", str(DOCTOR), "doctor"], capture_output=True,
                text=True, timeout=15, env={
                    "PATH": str(commands) + ":/usr/bin:/bin",
                    "HOME": str(home), "LANG": "C.UTF-8",
                    "AGY_CUA_DRIVER": str(commands / "driver"),
                    "FIXTURE_CONFIG": str(config), "FIXTURE_CALLS": str(calls),
                },
            )
            self.assertEqual(result.stderr, "")
            trace = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertIn(["python3", [str(DOCTOR.with_name("agy-cua-jev-mcp")),
                                      "--check"]], trace)
            self.assertTrue(all(name in {"uname", "sw_vers", "agy", "driver", "python3"}
                                for name, _ in trace))
            return result

    def test_linux_reports_backend_facts_without_mac_remedy_or_support_claim(self):
        for available in (True, False):
            with self.subTest(available=available):
                result = self.run_doctor("Linux", json.dumps({
                    "x11": available, "atspi": available,
                    "wayland": False, "wayland_enabled": False,
                    "dbus_session_bus_address": "fictional-private-address",
                }))
                self.assertEqual(result.returncode, 1)
                self.assertIn("only macOS is qualified", result.stdout)
                self.assertIn("Linux remains unqualified", result.stdout)
                self.assertIn("X11=" + str(available).lower(), result.stdout)
                self.assertIn("AT-SPI=" + str(available).lower(), result.stdout)
                self.assertNotIn("open -n", result.stdout)
                self.assertNotIn("permissions grant", result.stdout)
                self.assertNotIn("fictional-private-address", result.stdout)

    def test_linux_unknown_or_invalid_status_never_becomes_ready(self):
        for reply in ('{"status":"unknown"}', 'not-json', '[]',
                      '{"x11":"true","atspi":1}'):
            with self.subTest(reply=reply):
                result = self.run_doctor("Linux", reply)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Linux remains unqualified", result.stdout)
                self.assertNotIn("permissions grant", result.stdout)
                if reply in ('not-json', '[]'):
                    self.assertIn("status unavailable or invalid", result.stdout)
                else:
                    self.assertIn("X11=unknown", result.stdout)

    def test_mac_granted_and_denied_behavior_is_preserved(self):
        for granted in (True, False):
            with self.subTest(granted=granted):
                result = self.run_doctor("Darwin", json.dumps({
                    "accessibility": granted, "screen_recording": True,
                }))
                self.assertEqual(result.returncode, 0 if granted else 1)
                self.assertIn("macOS fictional-macos", result.stdout)
                self.assertNotIn("Linux driver", result.stdout)
                if granted:
                    self.assertIn("Accessibility and Screen Recording granted", result.stdout)
                else:
                    self.assertIn("open -n -g -a CuaDriver --args serve", result.stdout)


if __name__ == "__main__":
    unittest.main()
