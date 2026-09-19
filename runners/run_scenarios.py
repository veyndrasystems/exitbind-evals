#!/usr/bin/env python3
"""Run external-trust scenarios through their deterministic oracles.

This runner answers one narrow question for each scenario: *can the scenario's
claim be decided mechanically, from fixture and recorded-artifact state, by an
oracle that never reads the product's own reported state?*

It runs the evaluator-owned `fixture` variant only. A fixture result proves the
scenario, oracle, schema, and digests are sound. It is **not** a product result
and must never be reported as one — see `EVAL_PROTOCOL.md`.

Baseline and exitbind variants require an adapter that drives a real agent
workflow; until one exists those variants are recorded as `inconclusive` with
an explicit unavailable reason rather than being guessed at.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scenarios"))

from oracles import ORACLES, load_scenario, scenario_digest  # noqa: E402

ALLOWED_OUTCOMES = {"pass", "fail", "inconclusive"}


class Unavailable(Exception):
    """Raised when a scenario cannot establish its claim with available inputs."""


def _unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason}


def _measured(value, unit: str | None = None) -> dict:
    payload = {"status": "measured", "value": value}
    if unit:
        payload["unit"] = unit
    return payload


def _fixture_evidence(scenario_id: str, scenario_dir: Path) -> dict:
    """Deterministic fixture-variant evidence for one scenario.

    The fixture variant models the evaluator's own bookkeeping: it records the
    claim-relevant state the way a *correct* implementation would. It exists to
    prove the oracle discriminates, so each scenario's evidence module supplies
    both a satisfied and a violated case (see ``tests/test_scenarios.py``).
    """
    module_path = scenario_dir / "fixtures" / "evidence.py"
    if not module_path.is_file():
        raise Unavailable("scenario has no fixture evidence module yet")
    namespace: dict = {}
    exec(compile(module_path.read_text(encoding="utf-8"), str(module_path), "exec"), namespace)
    build = namespace.get("fixture_evidence")
    if not callable(build):
        raise Unavailable("fixture evidence module exposes no fixture_evidence()")
    return build(scenario_dir)


def run_scenario(
    scenario_dir: Path,
    *,
    variant: str,
    product_commit: str | None,
    evaluator_commit: str | None,
) -> dict:
    scenario = load_scenario(scenario_dir)
    digest = scenario_digest(scenario_dir)
    oracle_id = scenario["oracle"]["id"]
    oracle = ORACLES.get(oracle_id)
    if oracle is None:
        raise Unavailable(f"unknown oracle id {oracle_id!r}")

    result = {
        "schema_version": "scenario-result-v1",
        "scenario_id": scenario["id"],
        "scenario_version": scenario["version"],
        "scenario_digest": digest,
        "product_commit": product_commit,
        "variant": variant,
        "host_model": _unavailable("no host or model participates in a fixture or scaffold run"),
        "outcome": "inconclusive",
        "oracle": {
            "id": oracle_id,
            "version": scenario["oracle"]["version"],
            "verdict": "indeterminate",
            "reads_product_state": bool(scenario["oracle"].get("reads_product_state", False)),
            "reason": "oracle did not run",
        },
        "reported_product_state": None,
        "retries": _unavailable("the scaffold does not retry; retry accounting needs a live adapter"),
        "human_interventions": _unavailable("no human participates in a scaffold run"),
        "elapsed_ms": _unavailable("not measured in a scaffold run"),
        "tokens": _unavailable("no model call is made"),
        "cost_usd": _unavailable("no model call is made"),
        "observations": [],
        "limitations": list(scenario.get("threats_to_validity", [])),
    }
    if evaluator_commit:
        result["evaluator_commit"] = evaluator_commit

    if variant != "fixture":
        result["oracle"]["reason"] = (
            f"variant '{variant}' requires a live adapter; the scaffold does not fabricate its evidence"
        )
        result["observations"].append(
            f"variant '{variant}' is not mechanically runnable yet; recorded as inconclusive"
        )
        return result

    try:
        evidence = _fixture_evidence(scenario["id"], scenario_dir)
    except Unavailable as error:
        result["oracle"]["reason"] = str(error)
        result["observations"].append("fixture evidence unavailable; no verdict was fabricated")
        return result

    verdict = oracle(evidence)
    result["oracle"]["verdict"] = verdict["verdict"]
    result["oracle"]["reason"] = verdict["reason"]

    expected = scenario["expected"]["correct_exit_verdict"]
    if verdict["verdict"] == "indeterminate":
        outcome = "inconclusive"
    elif verdict["verdict"] == expected:
        outcome = "pass"
    else:
        outcome = "fail"
    result["outcome"] = outcome
    result["observations"].append(
        f"oracle verdict '{verdict['verdict']}' against expected '{expected}' -> {outcome}"
    )
    # A fixture pass proves the oracle discriminates; it is never a product
    # result. Say so in the artifact itself, not only in the prose docs.
    result["observations"].append(
        "variant=fixture: this result exercises the scenario and oracle only and is not a product result"
    )
    if scenario.get("run_status") == "adapter_required":
        result["observations"].append(
            "scenario is declared adapter_required; a live adapter must re-run it before any product claim"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=ROOT / "scenarios")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--variant", default="fixture", choices=["fixture", "baseline", "exitbind"])
    parser.add_argument("--product-commit", default=None)
    parser.add_argument("--evaluator-commit", default=None)
    parser.add_argument("--only", default=None, help="run one scenario id")
    args = parser.parse_args()

    scenario_dirs = sorted(
        path
        for path in args.scenarios.iterdir()
        if path.is_dir() and (path / "scenario.json").is_file()
    )
    if args.only:
        scenario_dirs = [path for path in scenario_dirs if load_scenario(path)["id"] == args.only]
        if not scenario_dirs:
            parser.error(f"no scenario with id {args.only!r}")

    results = []
    for scenario_dir in scenario_dirs:
        results.append(
            run_scenario(
                scenario_dir,
                variant=args.variant,
                product_commit=args.product_commit,
                evaluator_commit=args.evaluator_commit,
            )
        )
    assert all(item["outcome"] in ALLOWED_OUTCOMES for item in results)
    args.out.mkdir(parents=True, exist_ok=True)
    for item in results:
        (args.out / f"{item['scenario_id']}.json").write_text(
            json.dumps(item, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    summary = {
        "schema_version": "scenario-suite-v1",
        "variant": args.variant,
        "scenario_count": len(results),
        "outcomes": {item["scenario_id"]: item["outcome"] for item in results},
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if all(item["outcome"] == "pass" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
