"""Fixture evidence for scenario s1.

Models the two claim-relevant states a correct implementation distinguishes:

* ``satisfied`` — the approval binds to the current covered digest;
* ``violated``  — a covered file changed after the approval was recorded.
"""

from __future__ import annotations

from pathlib import Path

import oracles

COVERED_NAMES = ["source.txt"]


def fixture_evidence(scenario_dir: Path) -> dict:
    covered_root = scenario_dir / "fixtures"
    current = oracles.canonical_digest(*[covered_root / name for name in COVERED_NAMES])
    return {
        "covered_root": str(covered_root),
        "covered_names": COVERED_NAMES,
        # The fixture variant represents a correct implementation, so the
        # recorded approval names the digest that is actually current.
        "recorded_approval_digest": current,
    }
