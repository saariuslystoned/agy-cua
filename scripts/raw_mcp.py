#!/usr/bin/env python3
"""Talk to `cua-driver mcp` over raw stdio JSON-RPC, independent of Antigravity.

Used by the qualification harness to check results on a fresh connection.

  raw_mcp.py call <tool> '<json-args>'           print text content and structuredContent
  raw_mcp.py window-state <app-name> [--png OUT]  print the app window's static texts
"""
import base64
import json
import os
import re
import subprocess
import sys

DRIVER_CANDIDATES = [
    os.environ.get("AGY_CUA_DRIVER", ""),
    os.path.expanduser("~/.local/bin/cua-driver"),
    "/Applications/CuaDriver.app/Contents/MacOS/cua-driver",
]


def driver_path():
    for candidate in DRIVER_CANDIDATES:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    sys.exit("cua-driver not found; install it from https://cua.ai/driver/install.sh")


class Session:
    def __init__(self):
        self.proc = subprocess.Popen(
            [driver_path(), "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": os.environ["HOME"]},
        )
        self.next_id = 0
        self.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "agy-cua-raw", "version": "0"},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params):
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                sys.exit(f"cua-driver mcp exited during {method}")
            message = json.loads(line)
            if message.get("id") == self.next_id:
                if "error" in message:
                    sys.exit(f"{method} failed: {message['error']}")
                return message["result"]

    def call(self, tool, arguments):
        return self.request("tools/call", {"name": tool, "arguments": arguments})

    def close(self):
        self.proc.stdin.close()
        self.proc.terminate()


def text_of(result):
    return "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")


def window_state(app_name, png_out):
    session = Session()
    try:
        apps = session.call("list_apps", {}).get("structuredContent", {}).get("apps", [])
        running = [a for a in apps if a.get("name") == app_name and a.get("running", a.get("pid"))]
        if not running:
            sys.exit(f"{app_name} is not running")
        pid = running[0]["pid"]
        windows = session.call("list_windows", {"pid": pid}).get("structuredContent", {}).get("windows", [])
        titled = [w for w in windows if w.get("title") == app_name] or windows
        if not titled:
            sys.exit(f"{app_name} (pid {pid}) has no windows")
        window_id = titled[0]["window_id"]
        result = session.call("get_window_state", {
            "pid": pid, "window_id": window_id, "include_screenshot": bool(png_out),
        })
    finally:
        session.close()
    text = text_of(result)
    static_texts = [t.replace("‎", "") for t in re.findall(r'AXStaticText = "([^"]*)"', text)]
    print(json.dumps({"pid": pid, "window_id": window_id, "static_texts": static_texts[:5],
                      "text_has_snapshot_id": "snapshot_id" in text}))
    for content in result.get("content", []):
        if png_out and content.get("type") == "image":
            with open(png_out, "wb") as handle:
                handle.write(base64.b64decode(content["data"]))


def main(argv):
    if len(argv) >= 3 and argv[1] == "call":
        session = Session()
        try:
            result = session.call(argv[2], json.loads(argv[3]) if len(argv) > 3 else {})
        finally:
            session.close()
        print(text_of(result))
        print(json.dumps(result.get("structuredContent"), indent=2))
    elif len(argv) >= 3 and argv[1] == "window-state":
        png_out = argv[argv.index("--png") + 1] if "--png" in argv else None
        window_state(argv[2], png_out)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
