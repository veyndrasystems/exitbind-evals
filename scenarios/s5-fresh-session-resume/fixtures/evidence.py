"""Fixture evidence for scenario s5.

The covered file was edited between the interruption and the resume, so the
fixture variant models a **correct** resume: the check result recorded against
the previous revision is re-acquired rather than reused, and the approval —
which cannot be re-acquired without a reviewer — carries no stale binding.
"""

from __future__ import annotations

from pathlib import Path

import oracles

COVERED_NAMES = ["source.txt"]


def fixture_evidence(scenario_dir: Path) -> dict:
    fixtures = scenario_dir / "fixtures"
    current = oracles.canonical_digest(*[fixtures / name for name in COVERED_NAMES])
    return {
        # The recorded check bound to a previous revision of source.txt.
        "before_digest": "0" * 64,
        "after_digest": current,
        "reused_evidence": [
            {"kind": "check", "status": "passed", "bound_digest": "0" * 64},
        ],
        # A correct resume re-acquires the check instead of reusing it.
        "freshly_reacquired": ["check"],
    }
