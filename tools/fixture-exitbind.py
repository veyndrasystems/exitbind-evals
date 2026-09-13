#!/usr/bin/env python3
"""Deterministic black-box test double; never used as product evidence."""
import argparse
import json
import sys

CASES = {
    "false-completion-001": ("REFUSED", "CHECK_FAILED", 2), "false-completion-control-001": ("READY", "ACCEPTED", 0),
    "stale-check-001": ("REFUSED", "CHECK_SUBJECT_STALE", 2), "stale-check-control-001": ("READY", "ACCEPTED", 0),
    "semantic-loss-001": ("REFUSED", "SEMANTIC_INVARIANT_LOST", 2), "semantic-loss-control-001": ("READY", "ACCEPTED", 0),
    "stale-review-001": ("REFUSED", "REVIEW_SUBJECT_STALE", 2), "stale-review-control-001": ("READY", "ACCEPTED", 0),
    "reported-observed-001": ("BLOCKED", "OBSERVATION_MISSING", 3), "reported-observed-control-001": ("READY", "ACCEPTED", 0),
    "partial-completion-001": ("REFUSED", "PARTIAL_COMPLETION", 2), "partial-completion-control-001": ("READY", "ACCEPTED", 0),
}
parser = argparse.ArgumentParser()
parser.add_argument("--case", required=True)
parser.add_argument("--json", action="store_true")
args = parser.parse_args()
if args.case not in CASES:
    print("unknown fixture case", file=sys.stderr)
    raise SystemExit(64)
outcome, reason, code = CASES[args.case]
holytail = "LOSS" if args.case == "semantic-loss-001" else "PRESERVED" if args.case in {"semantic-loss-control-001", "partial-completion-control-001"} else "NOT_APPLICABLE"
print(json.dumps({"outcome": outcome, "reason": reason, "holytail": holytail, "fixture": True}, sort_keys=True))
raise SystemExit(code)
