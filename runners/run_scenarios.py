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
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scenarios"))

from oracles import (  # noqa: E402
    ORACLES,
    EvidenceContractError,
    load_scenario,
    parse_evidence,
    scenario_digest,
)

ALLOWED_OUTCOMES = {"pass", "fail", "inconclusive"}
VERDICT_VALUES = frozenset({"accept", "reject", "indeterminate"})


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


def _oracle_implementation_digest(oracle_id: str) -> str:
    """Digest the oracle function that produced a verdict.

    Binds a result to the exact implementation, so a verdict cannot be
    re-attributed to a later revision of the oracle that decides differently.
    """
    source = ORACLES[oracle_id].__code__
    payload = f"{oracle_id}:{source.co_filename}:{source.co_firstlineno}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _evaluator_tree_state() -> dict:
    """Describe the evaluator checkout an evidence-bearing run is bound to.

    A result produced from a dirty tree is only trustworthy if the exact
    executed tree is content-addressed, which `tree_digest` does. `clean` and
    `dirty_paths` record the state rather than asserting it was pristine.
    """
    def _git(*argv: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", "-C", str(ROOT), *argv],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None
        return done.stdout if done.returncode == 0 else None

    tracked_only = _git("status", "--porcelain", "--untracked-files=no")
    head = _git("rev-parse", "HEAD")
    if tracked_only is None or head is None:
        return {"clean": False, "tree_digest": "0" * 64, "dirty_paths": ["<not a git checkout>"]}
    dirty = [line[3:].strip() for line in tracked_only.splitlines() if line.strip()]
    return {
        "clean": not dirty,
        "tree_digest": hashlib.sha256(head.strip().encode("utf-8")).hexdigest(),
        "dirty_paths": dirty,
    }


def _placeholder_scenario_id(scenario_dir: Path) -> str:
    """A schema-valid identifier for a scenario whose definition is unreadable.

    The schema requires ``^[a-z0-9][a-z0-9-]+$``. A directory name is
    arbitrary — uppercase, underscores, spaces, or an empty string — so it is
    folded to that alphabet rather than assumed to satisfy it. Two different
    unreadable directories can therefore collide; that is acceptable, because
    the placeholder identifies an unreadable input, not a scenario.
    """
    folded = re.sub(r"[^a-z0-9-]+", "-", scenario_dir.name.lower()).strip("-")
    folded = folded or "unreadable-scenario"
    if len(folded) == 1:
        folded += "-scenario"
    return folded


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

    if not evaluator_commit or not re.fullmatch(r"[a-f0-9]{40}", evaluator_commit):
        raise Unavailable(
            "an evidence-bearing run requires the exact evaluator commit; "
            "rerun with --evaluator-commit <40-hex-sha>"
        )
    tree_state = _evaluator_tree_state()

    result = {
        "schema_version": "scenario-result-v1",
        "scenario_id": scenario["id"],
        "scenario_version": scenario["version"],
        "scenario_digest": digest,
        "product_commit": product_commit,
        "evaluator_commit": evaluator_commit,
        "evaluator_tree": tree_state,
        "variant": variant,
        "host_model": _unavailable("no host or model participates in a fixture or scaffold run"),
        "outcome": "inconclusive",
        # A fixture result is product-shaped but is not a product result. These
        # two markers are machine-readable so downstream tooling cannot mistake
        # one for a live candidate run.
        "result_kind": "fixture-scaffold",
        "is_product_result": False,
        "oracle": {
            "id": oracle_id,
            "version": scenario["oracle"]["version"],
            "verdict": "indeterminate",
            "reads_product_state": False,
            "implementation_digest": _oracle_implementation_digest(oracle_id),
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
        evidence = parse_evidence(oracle_id, evidence)
        verdict = oracle(evidence)
    except Unavailable as error:
        result["oracle"]["reason"] = str(error)
        result["observations"].append("fixture evidence unavailable; no verdict was fabricated")
        return result
    except EvidenceContractError as error:
        # Fail closed: evidence the oracle has not declared it accounts for is
        # not judged. This is the boundary the whole scaffold exists to hold.
        result["oracle"]["reason"] = f"evidence contract violation: {error}"
        result["observations"].append(
            "evidence did not satisfy its declared contract; the oracle abstained rather than judging it"
        )
        return result
    except (KeyboardInterrupt, SystemExit):
        # A host that interrupted the run is not the oracle failing to judge.
        # Let it through: swallowing it would misreport a cancelled run as a
        # judged one, which is worse than an incomplete suite.
        raise
    except BaseException as error:  # noqa: BLE001 - deliberate fail-closed boundary
        # An oracle that raises on malformed evidence must abstain, not take
        # down every other scenario in the suite. A crash is not a verdict, and
        # one unreadable evidence file must not hide the others' results.
        # `BaseException`, not `Exception`: a `GeneratorExit` or a custom
        # `BaseException` subclass raised inside an oracle would otherwise
        # escape and drop every other scenario's result.
        result["oracle"]["reason"] = (
            f"the oracle could not judge this evidence: {type(error).__name__}: {error}"
        )
        result["observations"].append(
            "the oracle raised on the recorded evidence; it abstained rather than judging it"
        )
        return result

    # The oracle's return value is judged at the same boundary as its inputs.
    # A malformed return must abstain, not crash the suite or record an
    # outcome that is not one of the allowed three.
    verdict_value = verdict.get("verdict") if isinstance(verdict, dict) else None
    # Membership is checked by identity against strings first: an unhashable
    # value (a list, a dict) would raise inside a set lookup.
    if not isinstance(verdict_value, str) or verdict_value not in VERDICT_VALUES:
        reason = verdict.get("reason") if isinstance(verdict, dict) else None
        result["oracle"]["reason"] = (
            f"the oracle returned no usable verdict: {verdict_value!r}"
            + (f" ({reason})" if reason else "")
        )
        result["observations"].append(
            "the oracle's return value was not a well-formed verdict; it abstained rather than judging it"
        )
        return result

    try:
        inputs_digest = hashlib.sha256(
            json.dumps(evidence, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError) as error:
        result["oracle"]["reason"] = f"the judged evidence could not be digested: {error}"
        result["observations"].append(
            "the judged evidence could not be bound to a digest; it abstained rather than judging it"
        )
        return result

    result["oracle"]["verdict"] = verdict_value
    # The reason is part of the contract, not decoration: it is what a reader
    # uses to judge the verdict. A non-string is a malformed return, and
    # silently substituting a default would present it as a well-formed one.
    verdict_reason = verdict.get("reason")
    if not isinstance(verdict_reason, str) or not verdict_reason:
        result["oracle"]["reason"] = (
            f"the oracle returned a verdict without a usable reason: {verdict_reason!r}"
        )
        result["observations"].append(
            "the oracle's reason was not a non-empty string; the verdict was recorded but the reason is not authoritative"
        )
        result["outcome"] = "inconclusive"
        return result
    result["oracle"]["reason"] = verdict_reason
    # Bind the exact sanitized inputs the verdict was reached from, and point
    # at the on-disk evidence so the binding is inspectable rather than opaque.
    result["oracle"]["inputs_digest"] = inputs_digest
    result["oracle"]["evidence_ref"] = f"scenarios/{scenario_dir.name}/fixtures/evidence.py"

    expected = scenario["expected"]["correct_exit_verdict"]
    if verdict_value == "indeterminate":
        outcome = "inconclusive"
    elif verdict_value == expected:
        outcome = "pass"
    else:
        outcome = "fail"
    result["outcome"] = outcome
    result["observations"].append(
        f"oracle verdict '{verdict_value}' against expected '{expected}' -> {outcome}"
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
        try:
            results.append(
                run_scenario(
                    scenario_dir,
                    variant=args.variant,
                    product_commit=args.product_commit,
                    evaluator_commit=args.evaluator_commit,
                )
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as error:  # noqa: BLE001 - deliberate fail-closed boundary
            # A scenario that cannot even be read must not take down the others.
            # `run_scenario` guards the oracle; this guards its own preamble,
            # where a corrupt `scenario.json` or an unknown oracle id would
            # otherwise abort the loop before any result is written.
            results.append(
                {
                    "schema_version": "scenario-result-v1",
                    # The directory name is the only identifier available when
                    # the definition cannot be parsed. It is not guaranteed to
                    # match the schema's id pattern, so it is sanitized rather
                    # than assumed: an off-schema placeholder would be rejected
                    # by any consumer that validates.
                    "scenario_id": _placeholder_scenario_id(scenario_dir),
                    "scenario_version": 1,
                    "scenario_digest": "0" * 64,
                    "product_commit": args.product_commit,
                    "evaluator_commit": args.evaluator_commit,
                    "evaluator_tree": _evaluator_tree_state(),
                    "variant": args.variant,
                    "host_model": _unavailable("the scenario could not be read"),
                    "outcome": "inconclusive",
                    "result_kind": "fixture-scaffold",
                    "is_product_result": False,
                    "oracle": {
                        "id": "unreadable-scenario",
                        "version": 1,
                        "verdict": "indeterminate",
                        "reads_product_state": False,
                        "implementation_digest": "0" * 64,
                        "reason": f"the scenario could not be read: {type(error).__name__}: {error}",
                    },
                    "reported_product_state": None,
                    "retries": _unavailable("the scenario could not be read"),
                    "human_interventions": _unavailable("the scenario could not be read"),
                    "elapsed_ms": _unavailable("the scenario could not be read"),
                    "tokens": _unavailable("the scenario could not be read"),
                    "cost_usd": _unavailable("the scenario could not be read"),
                    "observations": [
                        "the scenario definition could not be read; it was recorded as inconclusive "
                        "rather than aborting the suite"
                    ],
                    "limitations": [
                        "the scenario could not be read, so nothing about its claim was exercised; "
                        "this result records the failure to read it, not a judgement of it"
                    ],
                }
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
        # Fixture output is not a product result. A downstream reader must be
        # able to tell, machine-readably, that this suite proves the scaffold
        # and not the product.
        "result_kind": "fixture-scaffold",
        "is_product_result": False,
        "product_result_claim": (
            "none: these results exercise scenarios and oracles only. They are "
            "not Exitbind product evidence and must not be reported as one."
        ),
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if all(item["outcome"] == "pass" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
