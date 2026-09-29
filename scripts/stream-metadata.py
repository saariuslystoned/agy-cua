#!/usr/bin/env python3
"""Reduce AGY stream-json to qualification metadata; never save raw turns."""
import json
import sys


def summarize(lines):
    steps = {}
    summary = {"result_seen": False, "parse_errors": 0, "skill_file_read": False,
               "token_usage": None, "final_status": None}
    for line in lines:
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("non-object event")
        except ValueError:
            summary["parse_errors"] += 1
            continue
        if event.get("event") == "result":
            result = event.get("result") or {}
            summary["result_seen"] = True
            summary["final_status"] = result.get("status")
            usage = result.get("usage") or {}
            summary["token_usage"] = {k: v for k, v in usage.items()
                                      if isinstance(v, (int, float)) and not isinstance(v, bool)}
        step = event.get("step_update") or {}
        if step.get("step_type") != "tool":
            continue
        info = step.get("tool_info") or {}
        params = info.get("parameters") or {}
        if not isinstance(params, dict):
            params = {}
        index = step.get("step_index")
        previous = steps.get(index, {})
        tool = params.get("ToolName") or step.get("tool_name") or previous.get("tool")
        if tool == "view_file":
            summary["skill_file_read"] |= any(
                isinstance(v, str) and v.endswith("/SKILL.md") for v in params.values())
        steps[index] = {"index": index, "state": step.get("state"),
                        "server": params.get("ServerName", previous.get("server")), "tool": tool}
    summary["tools"] = list(steps.values())
    return summary


if __name__ == "__main__":
    json.dump(summarize(sys.stdin), sys.stdout, indent=2)
    print()
