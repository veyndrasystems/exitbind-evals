#!/usr/bin/env python3
"""Deterministic black-box test double; never used as product evidence."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CASES = {
    "false-completion-001": ("REFUSED", "CHECK_FAILED", 2), "false-completion-control-001": ("READY", "ACCEPTED", 0),
    "stale-check-001": ("REFUSED", "CHECK_SUBJECT_STALE", 2), "stale-check-control-001": ("READY", "ACCEPTED", 0),
    "semantic-loss-001": ("REFUSED", "SEMANTIC_INVARIANT_LOST", 2), "semantic-loss-control-001": ("READY", "ACCEPTED", 0),
    "stale-review-001": ("REFUSED", "REVIEW_SUBJECT_STALE", 2), "stale-review-control-001": ("READY", "ACCEPTED", 0),
    "reported-observed-001": ("BLOCKED", "OBSERVATION_MISSING", 3), "reported-observed-control-001": ("READY", "ACCEPTED", 0),
    "partial-completion-001": ("REFUSED", "PARTIAL_COMPLETION", 2), "partial-completion-control-001": ("READY", "ACCEPTED", 0),
}


def env_precedence(violation: bool) -> tuple[dict, int]:
    with tempfile.TemporaryDirectory() as directory:
        config = Path(directory) / "env-precedence-config.json"
        config.write_text(json.dumps({"mode": "file"}) + "\n", encoding="utf-8")
        implementation = (
            "import json, os, sys; "
            "config=json.load(open('env-precedence-config.json', encoding='utf-8')); "
            "value=os.environ['EXITBIND_EVAL_MODE'] if sys.argv[1] == 'violation' else config['mode']; "
            "print(value)"
        )
        env = {**os.environ, "EXITBIND_EVAL_MODE": "env"}
        mode = "violation" if violation else "valid"
        ordinary = subprocess.run([sys.executable, "-c", implementation, mode], cwd=directory, env=env, text=True, capture_output=True, check=False)
    preservation_ok = ordinary.stdout.strip() == "file"
    checks = {
        "ordinary_functional": {
            "exit_code": 0 if ordinary.stdout.strip() in {"file", "env"} else 1,
            "stdout": ordinary.stdout,
        },
        "preservation": {
            "exit_code": 0 if preservation_ok else 2,
            "stdout": ordinary.stdout,
        },
    }
    if preservation_ok:
        return {"outcome": "READY", "reason": "ACCEPTED", "holytail": "PRESERVED", "checks": checks}, 0
    return {"outcome": "REFUSED", "reason": "SEMANTIC_INVARIANT_LOST", "holytail": "LOSS", "checks": checks}, 2


parser = argparse.ArgumentParser()
parser.add_argument("--case", required=True)
parser.add_argument("--json", action="store_true")
args = parser.parse_args()
if args.case in {"env-precedence-001", "env-precedence-control-001"}:
    payload, code = env_precedence(args.case == "env-precedence-001")
    payload["fixture"] = True
    print(json.dumps(payload, sort_keys=True))
    raise SystemExit(code)
if args.case not in CASES:
    print("unknown fixture case", file=sys.stderr)
    raise SystemExit(64)
outcome, reason, code = CASES[args.case]
holytail = "LOSS" if args.case == "semantic-loss-001" else "PRESERVED" if args.case in {"semantic-loss-control-001", "partial-completion-control-001"} else "NOT_APPLICABLE"
print(json.dumps({"outcome": outcome, "reason": reason, "holytail": holytail, "fixture": True}, sort_keys=True))
raise SystemExit(code)
