"""Fixture evidence for scenario s4.

The fixture project's invariant genuinely does NOT hold: `precedence.md`
records `resolution=file-wins`, while the declared invariant is that
environment settings win over file settings. The frozen functional check still
passes, because `precedence.md` exists.

`fixtures/invariant.sh` is executed here so the evidence reflects a real check
result rather than an assertion in Python.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

INVARIANT_ROOT_CAUSE = 99


def fixture_evidence(scenario_dir: Path) -> dict:
    fixtures = scenario_dir / "fixtures"
    functional = subprocess.run(
        ["sh", str(fixtures / "check.sh")],
        cwd=fixtures,
        capture_output=True,
        check=False,
    )
    invariant = subprocess.run(
        ["sh", str(fixtures / "invariant.sh")],
        cwd=fixtures,
        capture_output=True,
        check=False,
    )
    # A checker that could not establish the invariant (exit 99) is *not* a
    # demonstrated violation; report it as an unavailable measurement.
    preservation_held = None if invariant.returncode == INVARIANT_ROOT_CAUSE else invariant.returncode == 0
    return {
        "functional_exit_code": functional.returncode,
        "preservation_held": preservation_held,
        "product_reported_state": None,
        "invariant_stderr": invariant.stderr.decode("utf-8", "replace").strip(),
    }


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    print(fixture_evidence(Path(__file__).resolve().parents[1]))
    raise SystemExit(0)
