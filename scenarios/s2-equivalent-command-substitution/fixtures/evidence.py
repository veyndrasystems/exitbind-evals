"""Fixture evidence for scenario s2.

The fixture variant records check evidence produced by the exact frozen
command, which is the state a correct implementation must reach.
"""

from __future__ import annotations

import json
from pathlib import Path


def fixture_evidence(scenario_dir: Path) -> dict:
    frozen = json.loads(
        (scenario_dir / "fixtures" / "frozen-command.json").read_text(encoding="utf-8")
    )
    return {
        "frozen_command": frozen["frozen_command"],
        "frozen_command_digest": frozen["frozen_command_digest"],
        "executed_command": frozen["frozen_command"],
        "executed_exit_code": 0,
    }
