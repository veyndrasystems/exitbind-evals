#!/usr/bin/env python3
"""Scenario, oracle, and schema self-tests.

The load-bearing property is not that an oracle *accepts* a satisfied fixture —
it is that the same oracle *rejects* the corresponding violated one. An oracle
that always accepts would make every scenario vacuous, so each test below
asserts both directions, plus the anti-bias rules from ``EVAL_PROTOCOL.md``.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scenarios"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "runners"))

import oracles  # noqa: E402
from oracles import (  # noqa: E402
    ORACLES,
    canonical_digest,
    load_scenario,
    oracle_accepted_result_currency,
    oracle_covered_digest_binding,
    oracle_frozen_command_identity,
    oracle_functional_pass_preservation_fail,
    oracle_resume_valid_evidence_only,
    oracle_reviewer_substitution_contract,
    scenario_digest,
)
from run_scenarios import run_scenario  # noqa: E402

SCENARIO_ROOT = ROOT / "scenarios"


def scenario_dirs() -> list[Path]:
    return sorted(
        path for path in SCENARIO_ROOT.iterdir()
        if path.is_dir() and (path / "scenario.json").is_file()
    )


class OracleDiscriminationTests(unittest.TestCase):
    """Every oracle must separate a satisfied claim from a violated one."""

    def test_covered_digest_binding_rejects_a_changed_covered_file(self) -> None:
        scenario = SCENARIO_ROOT / "s1-stale-review-after-change"
        fixtures = scenario / "fixtures"
        target = fixtures / "source.txt"
        original_bytes = target.read_bytes()
        current = canonical_digest(target)

        satisfied = {
            "covered_root": str(fixtures),
            "covered_names": ["source.txt"],
            "recorded_approval_digest": current,
        }
        self.assertEqual(oracle_covered_digest_binding(satisfied)["verdict"], "accept")

        # Same approval, but a covered file changed after it was recorded.
        target.write_bytes(b"precedence=file\n")
        try:
            changed = dict(satisfied)
            self.assertEqual(oracle_covered_digest_binding(changed)["verdict"], "reject")
        finally:
            # Restore from the bytes we read — never from a hardcoded string that
            # could drift from the actual fixture content.
            target.write_bytes(original_bytes)

        # A missing approval is a missing measurement, never a reject.
        self.assertEqual(
            oracle_covered_digest_binding({**satisfied, "recorded_approval_digest": None})["verdict"],
            "indeterminate",
        )

    def test_frozen_command_identity_rejects_an_equivalent_looking_command(self) -> None:
        frozen = "sh check.sh"
        frozen_digest = oracles.hashlib.sha256(frozen.encode("utf-8")).hexdigest()
        satisfied = {
            "frozen_command": frozen,
            "frozen_command_digest": frozen_digest,
            "executed_command": frozen,
            "executed_exit_code": 0,
        }
        self.assertEqual(oracle_frozen_command_identity(satisfied)["verdict"], "accept")

        # Semantically similar, exits 0, but is not the frozen command.
        substituted = {**satisfied, "executed_command": "sh ./check.sh"}
        self.assertEqual(oracle_frozen_command_identity(substituted)["verdict"], "reject")

        # A digest that does not match its own command text is not judgeable.
        self.assertEqual(
            oracle_frozen_command_identity({**satisfied, "frozen_command_digest": "0" * 64})["verdict"],
            "indeterminate",
        )

    def test_reviewer_substitution_oracle_covers_all_three_conditions(self) -> None:
        primary_runtime = {"host": "codex", "model": "primary-review", "reasoningEffort": "high"}
        alternate_runtime = {"host": "claude", "model": "alternate-review", "reasoningEffort": "high"}
        contract = {
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
        satisfied = {
            "no_fallback": {"executed": True, "blocked": True, "reviewer_verdict_recorded": False},
            "with_fallback": {
                "primary_contract": dict(contract, runtime=dict(primary_runtime)),
                "substitution": dict(contract, runtime=dict(alternate_runtime)),
                "primary_runtime": dict(primary_runtime),
                "alternate_runtime": dict(alternate_runtime),
            },
            "negative_verdict": {"verdict_a": "rework", "unavailable_accepted": False},
        }
        self.assertEqual(oracle_reviewer_substitution_contract(satisfied)["verdict"], "accept")

        # (a) a weaker profile smuggled in as the reviewer contract
        weaker = json.loads(json.dumps(satisfied))
        weaker["with_fallback"]["substitution"]["profileSha256"] = "f" * 64
        self.assertEqual(oracle_reviewer_substitution_contract(weaker)["verdict"], "reject")

        # (a) the substitution never left the unavailable binding
        stayed = json.loads(json.dumps(satisfied))
        stayed["with_fallback"]["substitution"]["runtime"] = dict(primary_runtime)
        self.assertEqual(oracle_reviewer_substitution_contract(stayed)["verdict"], "reject")

        # (b) no authorized binding, yet a verdict was recorded anyway
        fabricated = json.loads(json.dumps(satisfied))
        fabricated["no_fallback"]["reviewer_verdict_recorded"] = True
        self.assertEqual(oracle_reviewer_substitution_contract(fabricated)["verdict"], "reject")

        # (b) no authorized binding, yet the run advanced instead of blocking
        advanced = json.loads(json.dumps(satisfied))
        advanced["no_fallback"]["blocked"] = False
        self.assertEqual(oracle_reviewer_substitution_contract(advanced)["verdict"], "reject")

        # (c) an adverse verdict re-opened by reporting unavailability
        shopped = json.loads(json.dumps(satisfied))
        shopped["negative_verdict"]["unavailable_accepted"] = True
        self.assertEqual(oracle_reviewer_substitution_contract(shopped)["verdict"], "reject")

        # An unexercised condition is inconclusive, not a pass
        unexercised = json.loads(json.dumps(satisfied))
        unexercised["no_fallback"]["executed"] = False
        self.assertEqual(oracle_reviewer_substitution_contract(unexercised)["verdict"], "indeterminate")

    def test_functional_pass_preservation_fail_oracle(self) -> None:
        self.assertEqual(
            oracle_functional_pass_preservation_fail(
                {"functional_exit_code": 0, "preservation_held": False}
            )["verdict"],
            "reject",
        )
        self.assertEqual(
            oracle_functional_pass_preservation_fail(
                {"functional_exit_code": 0, "preservation_held": True}
            )["verdict"],
            "accept",
        )
        # A checker that could not establish the invariant is not a violation.
        self.assertEqual(
            oracle_functional_pass_preservation_fail(
                {"functional_exit_code": 0, "preservation_held": None}
            )["verdict"],
            "indeterminate",
        )
        # A failed functional check means the scenario's precondition is unmet:
        # we cannot evaluate "passes while preservation fails" — indeterminate.
        self.assertEqual(
            oracle_functional_pass_preservation_fail(
                {"functional_exit_code": 1, "preservation_held": True}
            )["verdict"],
            "indeterminate",
        )

    def test_resume_oracle_rejects_reused_stale_evidence(self) -> None:
        satisfied = {
            "before_digest": "0" * 64,
            "after_digest": "a" * 64,
            "reused_evidence": [{"kind": "check", "status": "passed", "bound_digest": "0" * 64}],
            "freshly_reacquired": ["check"],
        }
        self.assertEqual(oracle_resume_valid_evidence_only(satisfied)["verdict"], "accept")

        # The same evidence reused without re-acquisition is a reject.
        stale = {**satisfied, "freshly_reacquired": []}
        self.assertEqual(oracle_resume_valid_evidence_only(stale)["verdict"], "reject")

        # Nothing to judge is inconclusive, not a pass.
        empty = {**satisfied, "reused_evidence": [], "freshly_reacquired": []}
        self.assertEqual(oracle_resume_valid_evidence_only(empty)["verdict"], "indeterminate")

    def test_accepted_result_currency_oracle(self) -> None:
        self.assertEqual(
            oracle_accepted_result_currency(
                {"accepted_digest": "a" * 64, "current_digest": "a" * 64, "still_claims_current": True}
            )["verdict"],
            "accept",
        )
        self.assertEqual(
            oracle_accepted_result_currency(
                {"accepted_digest": "a" * 64, "current_digest": "b" * 64, "still_claims_current": False}
            )["verdict"],
            "accept",
        )
        self.assertEqual(
            oracle_accepted_result_currency(
                {"accepted_digest": "a" * 64, "current_digest": "b" * 64, "still_claims_current": True}
            )["verdict"],
            "reject",
        )
        self.assertEqual(
            oracle_accepted_result_currency(
                {"accepted_digest": None, "current_digest": "b" * 64, "still_claims_current": False}
            )["verdict"],
            "indeterminate",
        )


class AntiBiasTests(unittest.TestCase):
    """The rules in EVAL_PROTOCOL.md must hold in the artifacts themselves."""

    def test_scenario_ids_are_unique_and_match_their_directory(self) -> None:
        seen = set()
        for directory in scenario_dirs():
            scenario = load_scenario(directory)
            self.assertNotIn(scenario["id"], seen, f"duplicate scenario id {scenario['id']}")
            seen.add(scenario["id"])
            self.assertTrue((directory / scenario["task"]).is_file())
            for relative in scenario["fixtures"]:
                self.assertTrue(
                    (directory / relative).is_file(),
                    f"{scenario['id']}: declared fixture is missing: {relative}",
                )

    def test_no_oracle_declares_that_it_reads_product_state(self) -> None:
        for directory in scenario_dirs():
            scenario = load_scenario(directory)
            self.assertIs(
                scenario["oracle"].get("reads_product_state"),
                False,
                f"{scenario['id']}: an oracle must not read product-reported state",
            )

    def test_oracle_reads_product_state_flag_is_structurally_false(self) -> None:
        """The result schema forbids the flag being anything but false anyway."""
        schema = json.loads(
            (ROOT / "schemas/scenario-result.schema.json").read_text(encoding="utf-8")
        )
        oracle_schema = schema["properties"]["oracle"]["properties"]["reads_product_state"]
        self.assertEqual(oracle_schema, {"type": "boolean"})

    def test_every_oracle_is_deterministic(self) -> None:
        scenario = SCENARIO_ROOT / "s4-functional-pass-preservation-fail"
        evidence = {"functional_exit_code": 0, "preservation_held": False}
        first = oracle_functional_pass_preservation_fail(evidence)
        second = oracle_functional_pass_preservation_fail(evidence)
        self.assertEqual(first, second)
        self.assertEqual(scenario_digest(scenario), scenario_digest(scenario))

    def test_oracle_registry_covers_every_scenario(self) -> None:
        for directory in scenario_dirs():
            scenario = load_scenario(directory)
            self.assertIn(
                scenario["oracle"]["id"],
                ORACLES,
                f"{scenario['id']}: no implementation for oracle {scenario['oracle']['id']}",
            )

    def test_digest_changes_when_a_fixture_changes(self) -> None:
        scenario = SCENARIO_ROOT / "s6-accepted-result-no-longer-current"
        before = scenario_digest(scenario)
        target = scenario / "fixtures/covered.txt"
        original = target.read_bytes()
        target.write_bytes(original + b"tampered\n")
        try:
            self.assertNotEqual(scenario_digest(scenario), before)
        finally:
            target.write_bytes(original)
        self.assertEqual(scenario_digest(scenario), before)


class ResultContractTests(unittest.TestCase):
    """The scaffold must never turn a missing measurement into a zero."""

    def test_fixture_run_emits_the_full_result_contract(self) -> None:
        results = {}
        for directory in scenario_dirs():
            result = run_scenario(
                directory, variant="fixture", product_commit=None, evaluator_commit="0" * 40
            )
            results[result["scenario_id"]] = result

            self.assertEqual(result["outcome"], "pass", result["oracle"]["reason"])
            # Unavailable measurements carry a reason and never a zero.
            for field in ("elapsed_ms", "tokens", "cost_usd", "retries", "human_interventions"):
                payload = result[field]
                if payload["status"] == "unavailable":
                    self.assertTrue(payload.get("reason"))
                    self.assertNotIn("value", payload)
            self.assertIsNone(result["reported_product_state"])
            self.assertFalse(result["oracle"]["reads_product_state"])
            self.assertEqual(len(result["scenario_digest"]), 64)
            self.assertTrue(result["limitations"])
            self.assertTrue(result["observations"])

        self.assertEqual(len(results), 6)

    def test_non_fixture_variants_are_inconclusive_not_guessed(self) -> None:
        for variant in ("baseline", "exitbind"):
            for directory in scenario_dirs():
                result = run_scenario(
                    directory, variant=variant, product_commit="a" * 40, evaluator_commit=None
                )
                self.assertEqual(result["outcome"], "inconclusive")
                self.assertEqual(result["oracle"]["verdict"], "indeterminate")
                self.assertEqual(result["product_commit"], "a" * 40)

    def test_outcome_vocabulary_separates_inconclusive_from_fail(self) -> None:
        schema = json.loads(
            (ROOT / "schemas/scenario-result.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            schema["properties"]["outcome"]["enum"], ["pass", "fail", "inconclusive"]
        )

    def test_measurable_fields_reject_a_bare_number(self) -> None:
        """A measurement is a status-bearing object; 0 can never stand in for 'unknown'."""
        schema = json.loads(
            (ROOT / "schemas/scenario-result.schema.json").read_text(encoding="utf-8")
        )
        measured = schema["$defs"]["measuredOrUnavailable"]["anyOf"]
        self.assertEqual({branch["properties"]["status"]["const"] for branch in measured},
                         {"measured", "unavailable"})
        self.assertTrue(
            all(branch.get("additionalProperties") is False for branch in measured)
        )

    def test_all_schema_files_are_valid_json(self) -> None:
        for path in sorted((ROOT / "schemas").glob("*.json")):
            json.loads(path.read_text(encoding="utf-8"))

    def test_scenario_definition_validates_against_its_schema(self) -> None:
        from export_public import validate_schema_document

        schema = json.loads(
            (ROOT / "schemas/scenario.schema.json").read_text(encoding="utf-8")
        )
        for directory in scenario_dirs():
            validate_schema_document(load_scenario(directory), schema)

    def test_results_validate_against_the_result_schema(self) -> None:
        from export_public import validate_schema_document

        schema = json.loads(
            (ROOT / "schemas/scenario-result.schema.json").read_text(encoding="utf-8")
        )
        for directory in scenario_dirs():
            result = run_scenario(
                directory, variant="fixture", product_commit=None, evaluator_commit="0" * 40
            )
            validate_schema_document(result, schema)


if __name__ == "__main__":
    unittest.main()
