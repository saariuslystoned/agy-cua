#!/usr/bin/env python3
"""Bounded real TextEdit CLI pilot. Raw UI material stays in ignored runs/.

No installation, daemon restart, or global settings changes are made. Jev's
installed private credential delivery is used without exposing its value.
The installed Jev controller enforces its existing guards and four-call cap.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import raw_mcp

ROOT = Path(__file__).resolve().parent.parent
MODELS = ("gemini-4-argon-low", "gemini-3.8-flash-high")
NOTE = "Everyday workflow qualification\nStatus: ready\nNext: review the synthetic note\n"
SEED = "Everyday workflow qualification\nStatus: draft\nNext: review the synthetic note\n"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def verify_file(path, expected=NOTE):
    """Read bytes independently; UI success text is never an oracle."""
    if not path.is_file() or path.is_symlink():
        return {"passed": False, "reason": "missing_or_symlink"}
    data = path.read_bytes()
    return {"passed": data == expected.encode(), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def event(out, phase):
    (out / "heartbeat").write_text(str(time.time()) + "\n")
    (out / "STATE.md").write_text(f"Phase: {phase}\n")
    with (out / "events.jsonl").open("a") as f:
        f.write(json.dumps({"time": time.time(), "phase": phase}) + "\n")


def new_seed_window_id(prior_windows, current_windows, title):
    prior_ids = {w["window_id"] for w in prior_windows}
    matches = [w["window_id"] for w in current_windows
               if w.get("title") == title and w["window_id"] not in prior_ids]
    if len(matches) != 1:
        raise RuntimeError("New task seed window is missing or ambiguous")
    return matches[0]


def inspect(out, pid, prefix, *, allowed_ids=()):
    """Read pixels/content only for task-owned windows; list metadata to discover."""
    session = raw_mcp.Session()
    try:
        result = session.call("list_windows", {"pid": pid})
        write_json(out / f"{prefix}-windows.private.json", result)
        windows = (result.get("structuredContent") or {}).get("windows", [])
        for i, window in enumerate(windows):
            if window.get("window_id") not in allowed_ids:
                continue
            state = session.call("get_window_state", {
                "pid": pid, "window_id": window["window_id"],
                "tree_format": "both", "include_screenshot": True,
            })
            for j, block in enumerate(state.get("content", [])):
                if block.get("type") == "image":
                    (out / f"{prefix}-{i}-{j}.png").write_bytes(base64.b64decode(block["data"]))
            state["content"] = [b for b in state.get("content", []) if b.get("type") != "image"]
            write_json(out / f"{prefix}-{i}.private.json", state)
        return windows
    finally:
        session.close()


def task_prompt(pid, seed_id, seed, target):
    return f"""Use the cua-computer-use skill and only agy-cua / agy-cua-jev MCP tools.
This is a bounded real TextEdit qualification. Do not use terminal, browser, other MCP
servers, clipboard, subagents, foreground input, settings or scripts. Existing documents
are protected. The only permitted source is the task-created {seed.name} window in TextEdit
pid {pid}, verified seed window_id {seed_id}, opened from {seed}.
The bootstrap File > New must target exactly pid {pid}, window_id {seed_id};
do not rediscover the seed by title. Do not read or act on unrelated windows.
Create a NEW document through TextEdit File > New using this seed window only as the
menu anchor. Discover the new untitled window; it is yours. Do not modify the seed.
If needed, use Format > Make Plain Text on your NEW document. Enter the draft note
then edit Status: draft to Status: ready, preserving the other lines and final newline.
Draft bytes: {SEED!r}
Required final bytes are:
{NOTE!r}
Use jev_step/jev_batch for mutations. For a previously observed target and established
action, use mode deterministic with exactly one candidate and a meaningful bound
postcondition. Leave ambiguous semantic choice to Jev (maximum four provider calls).
File > New and File > Save may change windows: discover and read the new target;
window creation alone is not goal verification. Bootstrap New is the sole permitted
direct invoke_menu, targeted to the observed seed window. All later mutations are guarded.
Do NOT click a text area to focus it: on this Driver, AXShowMenu does not mean AXPress.
Use a guarded set_value candidate directly on the freshly observed editable text area.
Exercise recovery deliberately: open File > Save, observe the real save sheet with
jev_observe using query Save and max_elements 1; if incomplete, read a sufficient bounded
scope before acting. Cancel this first save sheet, independently observe the document
again, then reopen Save and save ONLY to this currently nonexistent absolute path:
{target}
Never overwrite or replace an existing file. Use the save dialog's native controls;
use its Go to Folder sheet if needed. Do not claim recovery without actual observations.
Retain failed or slow attempts. On uncertain dispatch/verification or halted controller,
stop and report the exact blocker; never replay completed actions or switch to raw input.
Observe the final note. Report chosen model, provider/deterministic steps, save/cancel
transitions and verification. Do not close windows or quit TextEdit. No external effects.
"""


def run(out, model, repetition, plugin_ref="HEAD"):
    event(out, "prepare")
    installed = Path.home() / ".gemini/config/plugins/agy-cua"
    installed_hashes = {}
    for relative in ("bin/agy-cua-mcp", "bin/agy-cua-jev-mcp", "skills/cua-computer-use/SKILL.md"):
        baseline = subprocess.check_output(["git", "show", f"{plugin_ref}:{relative}"], cwd=ROOT)
        current = (installed / relative).read_bytes()
        if current != baseline:
            raise RuntimeError("Installed plugin differs from committed qualification base")
        installed_hashes[relative] = hashlib.sha256(current).hexdigest()
    write_json(out / "configuration.json", {"recipe": 2, "model": model,
        "effort": "low" if model == MODELS[0] else "high",
        "base": subprocess.check_output(["git", "rev-parse", plugin_ref], cwd=ROOT, text=True).strip(),
        "installed_sha256": installed_hashes, "max_jev_calls": 4,
        "billing_receipt_available": False})
    seed, target = out / f"seed-{out.name}.txt", out / f"note-{out.name}.txt"
    seed.write_text(SEED)
    write_json(out / "expected.json", {"seed": SEED, "note": NOTE, "target": str(target)})
    session = raw_mcp.Session()
    try:
        initial = session.call("launch_app", {"bundle_id": "com.apple.TextEdit"})
        prior_windows = (initial.get("structuredContent") or {}).get("windows", [])
        launch = session.call("launch_app", {"bundle_id": "com.apple.TextEdit", "urls": [str(seed)]})
        pid = (launch.get("structuredContent") or {})["pid"]
        current = session.call("list_windows", {"pid": pid})
        seed_id = new_seed_window_id(prior_windows,
            (current.get("structuredContent") or {}).get("windows", []), seed.name)
    finally:
        session.close()
    write_json(out / "launch.private.json", launch)
    pid = (launch.get("structuredContent") or {})["pid"]
    before = inspect(out, pid, "before", allowed_ids={seed_id})

    workspace = out / "workspace"
    (workspace / ".agents/skills/cua-computer-use").mkdir(parents=True)
    (workspace / ".agents/skills/cua-computer-use/SKILL.md").write_bytes(
        (installed / "skills/cua-computer-use/SKILL.md").read_bytes())
    config = json.loads((ROOT / "mcp_config.json").read_text())
    for server in config["mcpServers"].values():
        server["env"] = {"AGY_CUA_HOME": str(installed), "AGY_CUA_JEV_MAX_CALLS": "4",
                         "AGY_CUA_JEV_TRACE_FILE": str(out / "jev.private.jsonl")}
    write_json(workspace / ".agents/mcp_config.json", config)
    prompt = task_prompt(pid, seed_id, seed, target)
    (out / "prompt.txt").write_text(prompt)
    effort = "low" if model == MODELS[0] else "high"
    cmd = ["agy", "-p", prompt, "--model", model, "--effort", effort,
           "--dangerously-skip-permissions", "--output-format", "stream-json",
           "--print-timeout", "7m"]
    focus = []
    stop = threading.Event()
    def sample():
        while not stop.is_set():
            (out / "heartbeat").write_text(str(time.time()) + "\n")
            try:
                focus.append(json.loads(subprocess.check_output([str(ROOT / "scripts/probe-focus.sh")], text=True)))
            except (subprocess.SubprocessError, ValueError):
                focus.append({"error": True})
            stop.wait(0.5)
    event(out, "running")
    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    started = time.monotonic()
    # The stream is consumed only in memory. Never persist raw AGY conversations.
    proc = subprocess.Popen(cmd, cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            env={**os.environ, "AGY_CUA_JEV_MAX_CALLS": "4",
                                 "AGY_CUA_JEV_TRACE_FILE": str(out / "jev.private.jsonl")})
    try:
        raw, _ = proc.communicate(timeout=450)
    except subprocess.TimeoutExpired:
        proc.terminate()  # This runner's child only.
        try:
            raw, _ = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()  # Escalate only against this runner's own child.
            raw, _ = proc.communicate()
    finally:
        stop.set()
        thread.join(timeout=5)
    duration = time.monotonic() - started
    spec = importlib.util.spec_from_file_location("stream_metadata", ROOT / "scripts/stream-metadata.py")
    metadata_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(metadata_module)
    metadata = metadata_module.summarize(raw.splitlines())
    del raw
    write_json(out / "metadata.private.json", metadata)
    write_json(out / "focus.private.json", focus)
    event(out, "independent_readback")
    traces = [json.loads(line) for line in (out / "jev.private.jsonl").read_text().splitlines()] if (out / "jev.private.jsonl").exists() else []
    steps = [e for e in traces if e.get("schema") == "agy_cua.jev_step_v1"]
    prior_ids = {w["window_id"] for w in before}
    task_ids = {e.get("window_id") for e in steps if e.get("pid") == pid} - prior_ids
    inspect(out, pid, "after", allowed_ids=task_ids | {seed_id})
    observations = [e for e in metadata.get("jev_entries", []) if e.get("schema") == "agy_cua.jev_observe_v1"]
    result = {"model": model, "effort": effort, "repetition": repetition,
              "surface": "Antigravity CLI", "duration_s": round(duration, 3),
              "exit_code": proc.returncode, "final_status": metadata.get("final_status"),
              "file": verify_file(target), "seed_unchanged": verify_file(seed, SEED)["passed"],
              "token_usage": metadata.get("token_usage"), "planner_time_ms": None,
              "planner_time_note": "CLI stream does not expose isolated inference wall time",
              "steps": [{k: e.get(k) for k in ("status", "mode", "verification_outcome", "action_dispatched", "selected_id", "returned_model", "timings_ms", "query_status", "query_incomplete", "diff_fallback_used")} for e in steps],
              "observations": observations,
              "jev_usage": steps[-1].get("accounting") if steps else None,
              "actual_api_cost_usd": None,
              "cost_note": "No billing receipt. Jev capped at 4 calls; published 64k context at $0.042/M input gives <=$0.010752 per attempt. CLI subscription usage is separate.",
              "textedit_frontmost_samples": sum(r.get("front_pid") == pid for r in focus),
              "focus_samples": len(focus),
              "focus_probe_errors": sum(bool(r.get("error")) for r in focus),
              "cursor_positions_changed": len({json.dumps(r.get("cursor"),sort_keys=True) for r in focus}) > 1,
              "recovery_verified": False,
              "verdict": "NEEDS_EVIDENCE_REVIEW"}
    write_json(out / "result.json", result)
    event(out, "completed_needs_evidence_review")
    (out / "PROOF.md").write_text("Independent byte verification is in result.json. Window images and raw controller traces are private. Recovery and overall acceptance require review of actual transitions; no automatic PASS.\n")
    print(json.dumps({k: result[k] for k in ("model", "duration_s", "file", "seed_unchanged", "exit_code", "final_status", "verdict")}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=MODELS, required=True)
    parser.add_argument("--repetition", type=int, choices=(1, 2), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--plugin-ref", default="HEAD", help="Committed revision the installed plugin must match")
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if not out.is_relative_to(ROOT / "runs"):
        parser.error("output must be a new directory under this checkout's ignored runs/")
    # Establish exclusive ownership before any exception handler can write here.
    # An existing attempt must remain byte-for-byte intact, including status files.
    out.mkdir(parents=True, exist_ok=False)
    try:
        run(out, args.model, args.repetition, args.plugin_ref)
    except (Exception, SystemExit) as error:
        if out.is_dir():
            # No exception message: third-party errors can contain private paths.
            write_json(out / "blocker.json", {"phase": "runner", "exception_type": type(error).__name__})
            event(out, "blocked")
            (out / "PROOF.md").write_text("Runner stopped; no qualification PASS. See blocker.json.\n")
        raise


if __name__ == "__main__":
    main()
