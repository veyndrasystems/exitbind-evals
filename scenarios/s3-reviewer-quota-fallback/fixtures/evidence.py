"""Fixture evidence for scenario s3 — the reviewer-quota-fallback conditions.

This scenario is `adapter_required`: only a live adapter can produce a real
reviewer substitution. The fixture module therefore supplies the *shape* of the
evidence a correct implementation records, so the oracle's discrimination can
be tested mechanically without claiming a product result.

`tests/test_scenarios.py` exercises the oracle directly against the satisfied
and each violated variant of this structure.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PRIMARY_RUNTIME = {"host": "codex", "model": "primary-review", "reasoningEffort": "high"}
ALTERNATE_RUNTIME = {"host": "claude", "model": "alternate-review", "reasoningEffort": "high"}

CONTRACT = {
    "agent": "reviewer",
    "role": "reviewer",
    "purpose": "Review evidence and return findings to the lead.",
    "profile": "exitbind/agents/reviewer.md",
    "profileSha256": "0" * 64,
    "declaredBoundary": {"write": [], "observe": []},
    "stage": 2,
    "attempt": 1,
    "goal": "quota fallback",
}

CONTRACT_KEYS = (
    "agent",
    "role",
    "purpose",
    "profile",
    "profileSha256",
    "declaredBoundary",
    "stage",
    "attempt",
    "goal",
)


def contract_digest(contract: dict) -> str:
    """Digest the contract fields a reviewer actually executes under.

    Mirrors the oracle's own computation so the fixture records what a real
    substitution would have to record: a digest of the executed contract, not
    just a boolean saying one ran.
    """
    return hashlib.sha256(
        json.dumps(
            {key: contract[key] for key in CONTRACT_KEYS}, sort_keys=True, default=str
        ).encode("utf-8")
    ).hexdigest()


def fixture_evidence(scenario_dir: Path) -> dict:
    substitution = dict(CONTRACT)
    substitution["runtime"] = dict(ALTERNATE_RUNTIME)
    return {
        "no_fallback": {
            "executed": True,
            "blocked": True,
            "reviewer_verdict_recorded": False,
        },
        "with_fallback": {
            "primary_contract": dict(CONTRACT, runtime=dict(PRIMARY_RUNTIME)),
            "substitution": substitution,
            "primary_runtime": dict(PRIMARY_RUNTIME),
            "alternate_runtime": dict(ALTERNATE_RUNTIME),
            # The fixture models a correct implementation: the authorized
            # alternate actually ran and produced the required verdict. The
            # executed binding and contract digest are what distinguish a real
            # substitution from a record that merely resembles one — a bare
            # boolean is asserted by the same caller that supplies the record.
            "fallback_executed": True,
            "reviewer_verdict_recorded": True,
            "executed_runtime": dict(ALTERNATE_RUNTIME),
            "executed_contract_sha256": contract_digest(CONTRACT),
        },
        "negative_verdict": {
            "verdict_a": "rework",
            "unavailable_accepted": False,
        },
    }
