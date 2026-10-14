#!/usr/bin/env python3
"""Reduce AGY stream-json to qualification metadata; never save raw turns."""
import json
import sys

JEV_SERVERS = {"agy-cua-jev", "agy-cua_agy-cua-jev"}
JEV_SCHEMAS = {"agy_cua.jev_step_v1", "agy_cua.jev_select_action_v1"}


def _extract_sanitized_jev_entry(raw_output):
    if not isinstance(raw_output, str) or "{" not in raw_output:
        return None
    decoder = json.JSONDecoder()
    pos = 0
    parsed_obj = None
    while True:
        idx = raw_output.find("{", pos)
        if idx < 0:
            break
        try:
            obj, end = decoder.raw_decode(raw_output, idx)
            if isinstance(obj, dict) and obj.get("schema") in JEV_SCHEMAS:
                parsed_obj = obj
            pos = end
        except ValueError:
            pos = idx + 1
    if parsed_obj is None:
        return None
    accounting = parsed_obj.get("accounting")
    clean_accounting = (
        {
            k: v
            for k, v in accounting.items()
            if isinstance(v, (int, float, bool))
        }
        if isinstance(accounting, dict)
        else {}
    )
    timings = parsed_obj.get("timings_ms")
    clean_timings = (
        {
            k: v
            for k, v in timings.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        if isinstance(timings, dict)
        else {}
    )
    post_texts = parsed_obj.get("post_static_texts")
    clean_post_texts = (
        [str(x) for x in post_texts if isinstance(x, (str, int, float))]
        if isinstance(post_texts, list)
        else []
    )
    entry = {
        "schema": parsed_obj.get("schema"),
        "status": parsed_obj.get("status") if isinstance(parsed_obj.get("status"), str) else None,
        "verification_outcome": (
            parsed_obj.get("verification_outcome")
            if isinstance(parsed_obj.get("verification_outcome"), str)
            else None
        ),
        "verified": parsed_obj.get("verified") if isinstance(parsed_obj.get("verified"), bool) else None,
        "returned_model": (
            parsed_obj.get("returned_model")
            if isinstance(parsed_obj.get("returned_model"), str)
            else None
        ),
        "sdk_identity": (
            parsed_obj.get("sdk_identity")
            if isinstance(parsed_obj.get("sdk_identity"), str)
            else None
        ),
        "selected_id": (
            parsed_obj.get("selected_id")
            if isinstance(parsed_obj.get("selected_id"), str)
            else None
        ),
        "accounting": clean_accounting,
        "timings_ms": clean_timings,
        "post_static_texts": clean_post_texts,
    }
    provider_ms = parsed_obj.get("provider_ms")
    if isinstance(provider_ms, (int, float)) and not isinstance(provider_ms, bool):
        entry["provider_ms"] = provider_ms
    return entry


def summarize(lines):
    steps = {}
    jev_entries = []
    summary = {
        "result_seen": False,
        "parse_errors": 0,
        "skill_file_read": False,
        "token_usage": None,
        "final_status": None,
    }
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
            summary["token_usage"] = {
                k: v
                for k, v in usage.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            }
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
        server = params.get("ServerName", previous.get("server"))
        if tool == "view_file":
            summary["skill_file_read"] |= any(
                isinstance(v, str) and v.endswith("/SKILL.md") for v in params.values()
            )
        state = step.get("state")
        steps[index] = {
            "index": index,
            "state": state,
            "server": server,
            "tool": tool,
        }
        if state == "DONE" and server in JEV_SERVERS:
            jev_entry = _extract_sanitized_jev_entry(info.get("output"))
            if jev_entry is not None:
                jev_entries.append(jev_entry)
    summary["tools"] = list(steps.values())
    summary["jev_entries"] = jev_entries
    return summary


if __name__ == "__main__":
    json.dump(summarize(sys.stdin), sys.stdout, indent=2)
    print()
