"""Fixture evidence for scenario s3 — the reviewer-quota-fallback conditions.

This scenario is `adapter_required`: only a live adapter can produce a real
reviewer substitution. The fixture module therefore supplies the *shape* of the
evidence a correct implementation records, so the oracle's discrimination can
be tested mechanically without claiming a product result.

`tests/test_scenarios.py` exercises the oracle directly against the satisfied
and each violated variant of this structure.
"""

from __future__ import annotations

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
        },
        "negative_verdict": {
            "verdict_a": "rework",
            "unavailable_accepted": False,
        },
    }
