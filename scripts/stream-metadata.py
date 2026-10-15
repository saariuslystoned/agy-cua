#!/usr/bin/env python3
"""Reduce AGY stream-json to qualification metadata; never save raw turns."""
import json
import sys

JEV_SERVERS = {"agy-cua-jev", "agy-cua_agy-cua-jev"}
JEV_SCHEMAS = {"agy_cua.jev_step_v1", "agy_cua.jev_batch_v1", "agy_cua.jev_select_action_v1"}


def _sanitize_timings(timings):
    return (
        {
            k: v
            for k, v in timings.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        if isinstance(timings, dict)
        else {}
    )


def _sanitize_static_texts(texts):
    return (
        [str(x) for x in texts if isinstance(x, (str, int, float))]
        if isinstance(texts, list)
        else []
    )


def _sanitize_batch_step(step_obj):
    if not isinstance(step_obj, dict):
        return None
    return {
        "step_index": step_obj.get("step_index") if isinstance(step_obj.get("step_index"), int) else None,
        "status": step_obj.get("status") if isinstance(step_obj.get("status"), str) else None,
        "verification_outcome": (
            step_obj.get("verification_outcome")
            if isinstance(step_obj.get("verification_outcome"), str)
            else None
        ),
        "verified": step_obj.get("verified") if isinstance(step_obj.get("verified"), bool) else None,
        "selected_id": step_obj.get("selected_id") if isinstance(step_obj.get("selected_id"), str) else None,
        "returned_model": step_obj.get("returned_model") if isinstance(step_obj.get("returned_model"), str) else None,
        "sdk_identity": step_obj.get("sdk_identity") if isinstance(step_obj.get("sdk_identity"), str) else None,
        "pre_snapshot_id": (
            step_obj.get("pre_snapshot_id") if isinstance(step_obj.get("pre_snapshot_id"), str) else None
        ),
        "post_snapshot_id": (
            step_obj.get("post_snapshot_id") if isinstance(step_obj.get("post_snapshot_id"), str) else None
        ),
        "post_static_texts": _sanitize_static_texts(step_obj.get("post_static_texts")),
        "timings_ms": _sanitize_timings(step_obj.get("timings_ms")),
    }


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
        "timings_ms": _sanitize_timings(parsed_obj.get("timings_ms")),
        "post_static_texts": _sanitize_static_texts(parsed_obj.get("post_static_texts")),
    }
    for int_key in ("completed_count", "total_steps"):
        val = parsed_obj.get(int_key)
        if isinstance(val, int) and not isinstance(val, bool):
            entry[int_key] = val
    raw_selected_ids = parsed_obj.get("selected_ids")
    if isinstance(raw_selected_ids, list):
        entry["selected_ids"] = [s for s in raw_selected_ids if isinstance(s, str)]
    raw_completed = parsed_obj.get("completed_steps")
    if isinstance(raw_completed, list):
        entry["completed_steps"] = [
            s for s in (_sanitize_batch_step(item) for item in raw_completed) if s is not None
        ]
    raw_failed = _sanitize_batch_step(parsed_obj.get("failed_step"))
    if raw_failed is not None:
        entry["failed_step"] = raw_failed
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
