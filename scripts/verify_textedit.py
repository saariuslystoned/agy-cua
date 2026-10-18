#!/usr/bin/env python3
"""Read-only byte oracle for the documented TextEdit qualification recipe.

This does not launch apps, spend quota, write artifacts, or certify UI recovery.
"""
import argparse
import hashlib
import json
from pathlib import Path

NOTE = "Everyday workflow qualification\nStatus: ready\nNext: review the synthetic note\n"
SEED = "Everyday workflow qualification\nStatus: draft\nNext: review the synthetic note\n"


def verify_file(path, expected=NOTE):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return {"passed": False, "reason": "missing_or_symlink"}
    data = path.read_bytes()
    return {"passed": data == expected.encode("utf-8"), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--note", type=Path, required=True)
    args = parser.parse_args(argv)
    seed = verify_file(args.seed, SEED)
    note = verify_file(args.note)
    result = {"seed": seed, "note": note,
              "byte_checks_passed": seed["passed"] and note["passed"],
              "ui_recovery_verified": None}
    print(json.dumps(result, indent=2))
    return 0 if result["byte_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
