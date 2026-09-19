"""Fixture evidence for scenario s6.

`covered.txt` changed after the acceptance was recorded, and the fixture
variant models a **correct** system: it no longer presents the acceptance as
current.
"""

from __future__ import annotations

from pathlib import Path

import oracles

COVERED_NAMES = ["covered.txt"]


def fixture_evidence(scenario_dir: Path) -> dict:
    fixtures = scenario_dir / "fixtures"
    current = oracles.canonical_digest(*[fixtures / name for name in COVERED_NAMES])
    return {
        # The acceptance was recorded for an earlier revision of the tree.
        "accepted_digest": "1" * 64,
        "current_digest": current,
        # A correct system no longer presents the stale acceptance as current.
        "still_claims_current": False,
        # It does claim exit — so it must carry evidence bound to the current
        # tree. Dropping the stale claim alone is not sufficient.
        "exit_claimed": True,
        "fresh_evidence": [
            {"kind": "check", "bound_digest": current},
        ],
    }
