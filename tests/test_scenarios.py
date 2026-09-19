#!/usr/bin/env python3
"""Scenario, oracle, and schema self-tests.

The load-bearing property is not that an oracle *accepts* a satisfied fixture —
it is that the same oracle *rejects* the corresponding violated one. An oracle
that always accepts would make every scenario vacuous, so each test below
asserts both directions, plus the anti-bias rules from ``EVAL_PROTOCOL.md``.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
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
                "fallback_executed": True,
                "reviewer_verdict_recorded": True,
                # A real substitution records the binding it ran on and the
                # contract it ran under. Two booleans are a claim by the same
                # caller that supplies the record; these bind it.
                "executed_runtime": dict(alternate_runtime),
                "executed_contract_sha256": hashlib.sha256(
                    json.dumps(contract, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest(),
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

        # A fallback that never executed cannot be credited as a substitution
        not_run = json.loads(json.dumps(satisfied))
        not_run["with_fallback"]["fallback_executed"] = False
        self.assertEqual(oracle_reviewer_substitution_contract(not_run)["verdict"], "indeterminate")

        # An executed fallback with no verdict is a demonstrated shortfall
        no_verdict = json.loads(json.dumps(satisfied))
        no_verdict["with_fallback"]["reviewer_verdict_recorded"] = False
        self.assertEqual(oracle_reviewer_substitution_contract(no_verdict)["verdict"], "reject")

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
        after = "a" * 64
        satisfied = {
            "before_digest": "0" * 64,
            "after_digest": after,
            "reused_evidence": [{"kind": "check", "status": "passed", "bound_digest": "0" * 64}],
            "freshly_reacquired": [{"kind": "check", "executed": True, "bound_digest": after}],
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
        # Drift acknowledged, and no exit claimed: nothing further is required.
        self.assertEqual(
            oracle_accepted_result_currency(
                {
                    "accepted_digest": "a" * 64,
                    "current_digest": "b" * 64,
                    "still_claims_current": False,
                    "exit_claimed": False,
                    "fresh_evidence": [],
                }
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

    def test_reads_product_state_true_is_rejected_by_the_schema(self) -> None:
        """The boundary, not just the convention: true must fail to validate.

        An earlier version of this test asserted only that the schema said
        ``type: boolean`` while its name claimed the flag was structurally
        false. It now proves the negative directly.
        """
        from export_public import validate_schema_document

        schema = json.loads(
            (ROOT / "schemas/scenario-result.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            schema["properties"]["oracle"]["properties"]["reads_product_state"],
            {"const": False},
        )
        result = run_scenario(
            SCENARIO_ROOT / "s1-stale-review-after-change",
            variant="fixture",
            product_commit=None,
            evaluator_commit="0" * 40,
        )
        result["oracle"]["reads_product_state"] = True
        with self.assertRaises(ValueError):
            validate_schema_document(result, schema)

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
                    directory,
                    variant=variant,
                    product_commit="a" * 40,
                    evaluator_commit="b" * 40,
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

    def test_evidence_bearing_run_requires_an_evaluator_commit(self) -> None:
        with self.assertRaises(Exception):
            run_scenario(
                SCENARIO_ROOT / "s1-stale-review-after-change",
                variant="fixture",
                product_commit=None,
                evaluator_commit=None,
            )

    def test_malformed_evaluator_commit_is_rejected(self) -> None:
        for bad in ("", "abc", "z" * 40, "0" * 39):
            with self.assertRaises(Exception, msg=f"accepted evaluator_commit={bad!r}"):
                run_scenario(
                    SCENARIO_ROOT / "s1-stale-review-after-change",
                    variant="fixture",
                    product_commit=None,
                    evaluator_commit=bad,
                )

    def test_missing_evaluator_commit_fails_schema_validation(self) -> None:
        from export_public import validate_schema_document

        schema = json.loads(
            (ROOT / "schemas/scenario-result.schema.json").read_text(encoding="utf-8")
        )
        result = run_scenario(
            SCENARIO_ROOT / "s1-stale-review-after-change",
            variant="fixture",
            product_commit=None,
            evaluator_commit="0" * 40,
        )
        del result["evaluator_commit"]
        with self.assertRaises(ValueError):
            validate_schema_document(result, schema)

    def test_results_bind_oracle_implementation_and_inputs(self) -> None:
        for directory in scenario_dirs():
            result = run_scenario(
                directory, variant="fixture", product_commit=None, evaluator_commit="0" * 40
            )
            oracle = result["oracle"]
            self.assertEqual(len(oracle["implementation_digest"]), 64)
            self.assertEqual(len(oracle["inputs_digest"]), 64)
            self.assertTrue(oracle["evidence_ref"])
            self.assertIn("evaluator_tree", result)
            self.assertIn("tree_digest", result["evaluator_tree"])

    def test_oracle_implementation_digest_distinguishes_oracles(self) -> None:
        digests = {
            load_scenario(directory)["oracle"]["id"]: run_scenario(
                directory, variant="fixture", product_commit=None, evaluator_commit="0" * 40
            )["oracle"]["implementation_digest"]
            for directory in scenario_dirs()
        }
        self.assertEqual(len(set(digests.values())), len(digests))

    def test_summary_declares_fixture_output_is_not_a_product_result(self) -> None:
        """A downstream reader must be able to tell fixture from product."""
        import tempfile

        from run_scenarios import main as run_main

        with tempfile.TemporaryDirectory() as tmp:
            argv = sys.argv
            try:
                sys.argv = [
                    "run_scenarios.py",
                    "--variant", "fixture",
                    "--evaluator-commit", "0" * 40,
                    "--out", tmp,
                ]
                self.assertEqual(run_main(), 0)
            finally:
                sys.argv = argv
            summary = json.loads((Path(tmp) / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["result_kind"], "fixture-scaffold")
        self.assertIs(summary["is_product_result"], False)
        self.assertTrue(summary["product_result_claim"])

    def test_fixture_results_are_marked_as_not_product_results(self) -> None:
        for directory in scenario_dirs():
            result = run_scenario(
                directory, variant="fixture", product_commit=None, evaluator_commit="0" * 40
            )
            self.assertEqual(result["result_kind"], "fixture-scaffold")
            self.assertIs(result["is_product_result"], False)

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


def _load_evidence_module(directory: Path) -> dict:
    module_path = directory / "fixtures" / "evidence.py"
    namespace: dict = {}
    exec(compile(module_path.read_text(encoding="utf-8"), str(module_path), "exec"), namespace)
    return namespace["fixture_evidence"](directory)


def _perturb(value):
    """Return a value that differs from *value* while keeping its shape."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        return value + "-mutated"
    if isinstance(value, int):
        return value + 1
    if isinstance(value, list):
        return list(value) + [{"kind": "mutated", "status": "failed", "bound_digest": "f" * 64,
                               "executed": False}]
    if isinstance(value, dict):
        return {**value, "__mutated__": True}
    if value is None:
        return "mutated"
    raise AssertionError(f"no mutation defined for {value!r}")


# Fields whose mutation must be expressed as a value change the oracle reads,
# rather than the generic perturbation above. A path is answered by pointing it
# at a different real file; a contract block is answered by changing a contract
# field the oracle compares.
def _mutate_field(oracle_id: str, field: str, evidence: dict):
    if oracle_id == "covered-digest-binding" and field == "covered_names":
        return ["check.sh"]  # a different real file under the same root
    if oracle_id == "reviewer-substitution-contract" and field in {"primary_contract", "substitution"}:
        mutated = json.loads(json.dumps(evidence[field]))
        mutated["profileSha256"] = "f" * 64
        return mutated
    if oracle_id == "accepted-result-currency" and field in {"accepted_digest", "current_digest"}:
        # The fixture's baseline has the two digests differ, so a value change
        # here cannot move the verdict. The discriminating mutation is removal:
        # a missing digest is a missing measurement, not an accept.
        return None
    return _perturb(evidence[field])


class EvidenceContractTests(unittest.TestCase):
    """The owning boundary: evidence must be declared, complete, and consumed.

    The same defect appeared in three oracles independently — each checked the
    shape of a claimed record without consuming the field that asserted the
    claim happened. Fixing three conditionals would not stop a fourth. These
    tests enforce the general property instead.
    """

    def test_every_oracle_declares_an_evidence_contract(self) -> None:
        for oracle_id in ORACLES:
            self.assertIn(
                oracle_id,
                oracles.EVIDENCE_CONTRACTS,
                f"oracle {oracle_id!r} has no declared evidence contract",
            )

    def test_contracts_cover_exactly_the_known_oracles(self) -> None:
        self.assertEqual(set(oracles.EVIDENCE_CONTRACTS), set(ORACLES))

    def test_every_scenario_fixture_evidence_satisfies_its_contract(self) -> None:
        for directory in scenario_dirs():
            scenario = load_scenario(directory)
            evidence = _load_evidence_module(directory)
            oracles.validate_evidence(scenario["oracle"]["id"], evidence)

    def test_unknown_fields_are_rejected(self) -> None:
        for directory in scenario_dirs():
            scenario = load_scenario(directory)
            evidence = _load_evidence_module(directory)
            tainted = {**evidence, "not_a_declared_field": 1}
            with self.assertRaises(oracles.EvidenceContractError):
                oracles.validate_evidence(scenario["oracle"]["id"], tainted)

    def test_missing_material_fields_are_rejected(self) -> None:
        for oracle_id, contract in oracles.EVIDENCE_CONTRACTS.items():
            required = [
                field
                for field in contract["material_fields"]
                if field not in contract.get("allow_missing", ())
            ]
            self.assertTrue(required, f"{oracle_id}: no required material field to test")
            for field in required:
                evidence = self._satisfied_evidence(oracle_id)
                del evidence[field]
                with self.assertRaises(
                    oracles.EvidenceContractError, msg=f"{oracle_id}: missing {field} was accepted"
                ):
                    oracles.validate_evidence(oracle_id, evidence)

    def test_nested_material_fields_are_rejected_when_absent(self) -> None:
        oracle_id = "reviewer-substitution-contract"
        for parent, fields in oracles.EVIDENCE_CONTRACTS[oracle_id]["nested"].items():
            for field in fields:
                evidence = self._satisfied_evidence(oracle_id)
                del evidence[parent][field]
                with self.assertRaises(
                    oracles.EvidenceContractError, msg=f"{parent}.{field} was accepted when absent"
                ):
                    oracles.validate_evidence(oracle_id, evidence)

    def test_mutating_any_material_field_changes_the_verdict(self) -> None:
        """The consumption check: an unread material field would fail this.

        If a field is declared material but the oracle never reads it,
        perturbing that field leaves the verdict unchanged and the oracle is
        silently not enforcing the claim the field encodes.

        Block-valued material fields are decomposed: their declared sub-fields
        are the real granularity, and mutating a whole block would test nothing
        (the oracle compares blocks field-by-field).
        """
        checked = 0
        for oracle_id, oracle in ORACLES.items():
            contract = oracles.EVIDENCE_CONTRACTS[oracle_id]
            baseline_evidence = self._satisfied_evidence(oracle_id)
            baseline_verdict = oracle(baseline_evidence)["verdict"]
            self.assertIn(baseline_verdict, {"accept", "reject", "indeterminate"})

            nested = contract.get("nested", {})
            for field in contract["material_fields"]:
                if field in contract.get("allow_missing", ()) or field not in baseline_evidence:
                    continue
                if field in nested:
                    # Decomposed below via the block's declared sub-fields.
                    continue
                mutated = dict(baseline_evidence)
                mutated[field] = _mutate_field(oracle_id, field, baseline_evidence)
                verdict = oracle(mutated)["verdict"]
                self.assertNotEqual(
                    verdict,
                    baseline_verdict,
                    f"{oracle_id}: field {field!r} is declared material but the oracle "
                    f"returned {verdict!r} unchanged — the field is not consumed",
                )
                checked += 1

            # Every top-level material field is either directly mutable or
            # decomposed into nested material sub-fields — never neither.
            for field in contract["material_fields"]:
                if field in nested or field in contract.get("allow_missing", ()):
                    continue
                self.assertIn(
                    field,
                    baseline_evidence,
                    f"{oracle_id}: declared material field {field!r} is absent from the "
                    "satisfied evidence, so its consumption cannot be tested",
                )
        self.assertGreaterEqual(checked, 8, "the mutation matrix exercised too few fields")

    def test_nested_material_fields_are_consumed(self) -> None:
        oracle_id = "reviewer-substitution-contract"
        oracle = ORACLES[oracle_id]
        baseline = self._satisfied_evidence(oracle_id)
        baseline_verdict = oracle(baseline)["verdict"]
        contract = oracles.EVIDENCE_CONTRACTS[oracle_id]
        # Blocks that are judged key-by-key are covered by
        # test_every_compared_contract_key_is_consumed instead.
        judged_as_block = {
            name
            for blocks in contract.get("compared_blocks", {}).values()
            for name in blocks
        }
        for parent, fields in contract["nested"].items():
            for field in fields:
                if field in judged_as_block:
                    continue
                mutated = json.loads(json.dumps(baseline))
                value = baseline[parent][field]
                if parent == "with_fallback" and field in {
                    "primary_runtime",
                    "alternate_runtime",
                    "executed_runtime",
                }:
                    # Runtime bindings are compared after normalization, which
                    # reads host/model/reasoningEffort. For primary_runtime the
                    # discriminating mutation is to collapse it onto the
                    # substitution (the binding did not move); for
                    # alternate_runtime it is to move it away from the
                    # substitution (the substitution ran somewhere else). For
                    # executed_runtime the mutation must move it off the
                    # authorized alternate the oracle checks it against.
                    if field == "primary_runtime":
                        mutated[parent][field] = dict(mutated["with_fallback"]["substitution"]["runtime"])
                    else:
                        mutated[parent][field] = {"host": "mutated", "model": "mutated", "reasoningEffort": "low"}
                else:
                    mutated[parent][field] = _perturb(value)
                self.assertNotEqual(
                    oracle(mutated)["verdict"],
                    baseline_verdict,
                    f"{parent}.{field} is declared material but is not consumed",
                )

    def test_every_compared_contract_key_is_consumed(self) -> None:
        """Each REVIEWER_CONTRACT_KEYS entry must change the verdict when altered.

        The oracle compares the two contract blocks key by key. A key that is
        listed in the comparison but not actually enforced would let a fallback
        weaken that part of the contract unnoticed.
        """
        oracle_id = "reviewer-substitution-contract"
        oracle = ORACLES[oracle_id]
        baseline = self._satisfied_evidence(oracle_id)
        baseline_verdict = oracle(baseline)["verdict"]
        for key in oracles.REVIEWER_CONTRACT_KEYS:
            mutated = json.loads(json.dumps(baseline))
            mutated["with_fallback"]["substitution"][key] = _perturb(
                baseline["with_fallback"]["substitution"][key]
            )
            self.assertNotEqual(
                oracle(mutated)["verdict"],
                baseline_verdict,
                f"contract key {key!r} is compared but altering it does not change the verdict",
            )

    @staticmethod
    def _satisfied_evidence(oracle_id: str) -> dict:
        """The satisfied fixture evidence for whichever scenario owns *oracle_id*."""
        for directory in scenario_dirs():
            if load_scenario(directory)["oracle"]["id"] == oracle_id:
                return _load_evidence_module(directory)
        raise AssertionError(f"no scenario owns oracle {oracle_id!r}")


class ReproducedBypassTests(unittest.TestCase):
    """Regressions for the three bypasses reproduced at baseline 1ab094a.

    Each probe previously returned ``accept`` for evidence that does not
    establish the scenario's claim. They must now fail closed.
    """

    def test_s3_shape_without_execution_is_not_accepted(self) -> None:
        evidence = _load_evidence_module(SCENARIO_ROOT / "s3-reviewer-quota-fallback")
        evidence["with_fallback"]["fallback_executed"] = False
        evidence["with_fallback"]["reviewer_verdict_recorded"] = False
        self.assertNotEqual(
            oracle_reviewer_substitution_contract(evidence)["verdict"], "accept"
        )

    def test_s3_executed_without_a_verdict_is_rejected(self) -> None:
        evidence = _load_evidence_module(SCENARIO_ROOT / "s3-reviewer-quota-fallback")
        evidence["with_fallback"]["reviewer_verdict_recorded"] = False
        self.assertEqual(
            oracle_reviewer_substitution_contract(evidence)["verdict"], "reject"
        )

    def test_s5_kind_shadowing_does_not_excuse_a_failed_reuse(self) -> None:
        """Re-acquiring a kind must not make a *failed* reuse of it pass.

        The two reused-evidence filters previously skipped any item whose
        ``kind`` appeared in the re-acquisition list, so a distinct failed
        record was never examined and was credited as passing.
        """
        self.assertEqual(
            oracle_resume_valid_evidence_only(
                {
                    "before_digest": "0" * 64,
                    "after_digest": "a" * 64,
                    "reused_evidence": [
                        {"kind": "check", "status": "failed", "bound_digest": "a" * 64}
                    ],
                    "freshly_reacquired": [
                        {"kind": "check", "executed": True, "bound_digest": "a" * 64}
                    ],
                }
            )["verdict"],
            "reject",
        )

    def test_s3_asserted_booleans_without_a_binding_are_not_accepted(self) -> None:
        """Two caller-asserted booleans must not substitute for proof.

        A record can assert ``fallback_executed`` and
        ``reviewer_verdict_recorded`` while binding the execution to nothing.
        The executed binding and contract digest are what make the claim
        checkable.
        """
        contract = {
            "agent": "reviewer", "role": "reviewer", "purpose": "review findings",
            "profile": "exitbind/agents/reviewer.md", "profileSha256": "0" * 64,
            "declaredBoundary": {"write": [], "observe": []},
            "stage": 2, "attempt": 1, "goal": "quota fallback",
        }
        primary = {"host": "codex", "model": "primary-review", "reasoningEffort": "high"}
        alternate = {"host": "claude", "model": "alternate-review", "reasoningEffort": "high"}
        asserted_only = {
            "no_fallback": {"executed": True, "blocked": True, "reviewer_verdict_recorded": False},
            "with_fallback": {
                "primary_contract": dict(contract, runtime=dict(primary)),
                "substitution": dict(contract, runtime=dict(alternate)),
                "primary_runtime": dict(primary),
                "alternate_runtime": dict(alternate),
                "fallback_executed": True,
                "reviewer_verdict_recorded": True,
            },
            "negative_verdict": {"verdict_a": "rework", "unavailable_accepted": False},
        }
        self.assertNotEqual(
            oracle_reviewer_substitution_contract(asserted_only)["verdict"], "accept"
        )

        # Binding the execution elsewhere than the authorized alternate is a reject.
        elsewhere = json.loads(json.dumps(asserted_only))
        elsewhere["with_fallback"]["executed_runtime"] = {
            "host": "unlisted", "model": "unlisted", "reasoningEffort": "low",
        }
        elsewhere["with_fallback"]["executed_contract_sha256"] = hashlib.sha256(
            json.dumps(contract, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            oracle_reviewer_substitution_contract(elsewhere)["verdict"], "reject"
        )

    def test_s2_boolean_exit_code_is_not_a_passing_exit_code(self) -> None:
        """``False == 0`` in Python, so a boolean must not read as a pass."""
        command = "bash fixtures/check.sh"
        base = {
            "frozen_command": command,
            "frozen_command_digest": hashlib.sha256(command.encode("utf-8")).hexdigest(),
            "executed_command": command,
        }
        self.assertNotEqual(
            oracle_frozen_command_identity({**base, "executed_exit_code": False})["verdict"],
            "accept",
        )
        self.assertEqual(
            oracle_frozen_command_identity({**base, "executed_exit_code": 1})["verdict"],
            "reject",
        )
        self.assertEqual(
            oracle_frozen_command_identity({**base, "executed_exit_code": 0})["verdict"],
            "accept",
        )

    def test_exit_codes_must_be_integers_not_booleans(self) -> None:
        """The bool/int hazard is swept across every oracle that reads a code.

        ``False == 0`` and ``True == 1`` in Python, so a boolean exit status
        would otherwise read as a passing zero in any oracle comparing to 0.
        """
        command = "bash fixtures/check.sh"
        frozen = {
            "frozen_command": command,
            "frozen_command_digest": hashlib.sha256(command.encode("utf-8")).hexdigest(),
            "executed_command": command,
        }
        for value in (False, True):
            self.assertNotEqual(
                oracle_frozen_command_identity({**frozen, "executed_exit_code": value})["verdict"],
                "accept",
            )
            self.assertNotEqual(
                oracle_functional_pass_preservation_fail(
                    {"functional_exit_code": value, "preservation_held": True}
                )["verdict"],
                "accept",
            )
        # A genuine integer exit code still decides normally.
        self.assertEqual(
            oracle_functional_pass_preservation_fail(
                {"functional_exit_code": 0, "preservation_held": True}
            )["verdict"],
            "accept",
        )
        self.assertEqual(
            oracle_functional_pass_preservation_fail(
                {"functional_exit_code": 0, "preservation_held": False}
            )["verdict"],
            "reject",
        )

    def test_malformed_shapes_abstain_instead_of_crashing(self) -> None:
        """A malformed record must abstain at the boundary, not raise inside.

        An oracle that raises takes down every other scenario's result, and a
        crash is not a verdict. Each shape below previously reached the oracle
        and raised there.
        """
        malformed = [
            (
                "resume-valid-evidence-only",
                {
                    "before_digest": "0" * 64,
                    "after_digest": "a" * 64,
                    "reused_evidence": ["check"],
                    "freshly_reacquired": [],
                },
            ),
            (
                "covered-digest-binding",
                {
                    "recorded_approval_digest": "a" * 64,
                    "covered_root": ".",
                    "covered_names": [1],
                },
            ),
            (
                "frozen-command-identity",
                {
                    "frozen_command": 123,
                    "frozen_command_digest": "a" * 64,
                    "executed_command": "x",
                    "executed_exit_code": 0,
                },
            ),
            (
                "accepted-result-currency",
                {
                    "accepted_digest": "a" * 64,
                    "current_digest": "b" * 64,
                    "still_claims_current": False,
                    "exit_claimed": True,
                    "fresh_evidence": ["check"],
                },
            ),
        ]
        for oracle_id, evidence in malformed:
            with self.subTest(oracle=oracle_id):
                with self.assertRaises(oracles.EvidenceContractError):
                    oracles.parse_evidence(oracle_id, evidence)

    def test_a_raising_oracle_abstains_rather_than_aborting_the_suite(self) -> None:
        """One unreadable evidence file must not hide the other results."""
        import run_scenarios

        original = run_scenarios.ORACLES["resume-valid-evidence-only"]

        def exploding(_evidence):
            raise AttributeError("'str' object has no attribute 'get'")

        run_scenarios.ORACLES["resume-valid-evidence-only"] = exploding
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "results"
                argv = sys.argv
                sys.argv = [
                    "run_scenarios.py", "--variant", "fixture",
                    "--evaluator-commit", "0" * 40, "--out", str(out),
                ]
                try:
                    exit_code = run_scenarios.main()
                finally:
                    sys.argv = argv
                # main() returns 1 when any scenario did not pass. The property
                # under test is that it *returned* at all — a crash would have
                # propagated instead — and that the surviving results exist.
                self.assertEqual(exit_code, 1)

                summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
                # Every other scenario still produced a result.
                self.assertEqual(summary["scenario_count"], 6)
                self.assertEqual(
                    summary["outcomes"]["fresh-session-resume"], "inconclusive"
                )
                self.assertEqual(
                    summary["outcomes"]["reviewer-quota-fallback"], "pass"
                )
                broken = json.loads(
                    (out / "fresh-session-resume.json").read_text(encoding="utf-8")
                )
                self.assertEqual(broken["oracle"]["verdict"], "indeterminate")
                self.assertIn("could not judge", broken["oracle"]["reason"])
        finally:
            run_scenarios.ORACLES["resume-valid-evidence-only"] = original

    def test_a_baseexception_from_an_oracle_does_not_abort_the_suite(self) -> None:
        """`except Exception` misses anything deriving from `BaseException`.

        A `GeneratorExit` or a custom `BaseException` raised inside an oracle
        would escape the boundary and abort the run before any result is
        written, dropping every other scenario's result.
        """
        import run_scenarios

        class Boom(BaseException):
            pass

        original = run_scenarios.ORACLES["resume-valid-evidence-only"]

        def exploding(_evidence):
            raise Boom("hard")

        run_scenarios.ORACLES["resume-valid-evidence-only"] = exploding
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "results"
                argv = sys.argv
                sys.argv = [
                    "run_scenarios.py", "--variant", "fixture",
                    "--evaluator-commit", "0" * 40, "--out", str(out),
                ]
                try:
                    exit_code = run_scenarios.main()
                finally:
                    sys.argv = argv
                self.assertEqual(exit_code, 1)
                summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
                self.assertEqual(summary["scenario_count"], 6)
                self.assertEqual(
                    summary["outcomes"]["fresh-session-resume"], "inconclusive"
                )
                self.assertEqual(summary["outcomes"]["reviewer-quota-fallback"], "pass")
        finally:
            run_scenarios.ORACLES["resume-valid-evidence-only"] = original

    def test_a_host_interrupt_is_not_swallowed_as_an_abstention(self) -> None:
        """`KeyboardInterrupt`/`SystemExit` must still propagate.

        A cancelled run is not a judged run. Reporting an interruption as
        `inconclusive` would mispresent a run that never finished.
        """
        import run_scenarios

        original = run_scenarios.ORACLES["resume-valid-evidence-only"]

        def interrupted(_evidence):
            raise KeyboardInterrupt

        run_scenarios.ORACLES["resume-valid-evidence-only"] = interrupted
        try:
            with self.assertRaises(KeyboardInterrupt):
                run_scenarios.run_scenario(
                    SCENARIO_ROOT / "s5-fresh-session-resume",
                    variant="fixture",
                    product_commit=None,
                    evaluator_commit="0" * 40,
                )
        finally:
            run_scenarios.ORACLES["resume-valid-evidence-only"] = original

    def test_an_unhashable_adverse_verdict_abstains_instead_of_crashing(self) -> None:
        """A nested field the oracle hashes is type-checked before the lookup.

        `verdict_a` is tested for membership in a set, so a list or dict there
        would raise inside the oracle. The type must be checked first, on the
        same boundary the oracle's own return value is checked on.
        """
        from oracles import ORACLES

        directory = SCENARIO_ROOT / "s3-reviewer-quota-fallback"
        base = json.loads(json.dumps(_load_evidence_module(directory)))
        for bad in ([], {}, 3, None, True):
            evidence = json.loads(json.dumps(base))
            # Put the malformed value where the oracle will read it.
            evidence["negative_verdict"].pop("verdict_a", None)
            evidence["negative_verdict"]["verdict_a"] = bad
            with self.subTest(verdict_a=bad):
                verdict = ORACLES["reviewer-substitution-contract"](evidence)
                self.assertEqual(verdict["verdict"], "indeterminate")
                self.assertIn("adverse verdict", verdict["reason"])

    def test_a_malformed_oracle_return_abstains_instead_of_crashing(self) -> None:
        """The oracle's return value is judged at the same boundary as its input.

        A malformed return must abstain. It must not crash the suite, and it
        must not record an outcome outside the allowed three.
        """
        import run_scenarios

        directory = SCENARIO_ROOT / "s5-fresh-session-resume"
        original = run_scenarios.ORACLES["resume-valid-evidence-only"]
        malformed = [
            lambda _e: None,
            lambda _e: ["accept"],
            lambda _e: {"reason": "no verdict key"},
            lambda _e: {"verdict": "maybe", "reason": "not a verdict"},
            lambda _e: {"verdict": [], "reason": "unhashable"},
            lambda _e: {"verdict": {}, "reason": "unhashable"},
            lambda _e: {"verdict": True, "reason": "not a string"},
        ]
        try:
            for index, returned in enumerate(malformed):
                with self.subTest(case=index):
                    run_scenarios.ORACLES["resume-valid-evidence-only"] = returned
                    result = run_scenarios.run_scenario(
                        directory, variant="fixture",
                        product_commit=None, evaluator_commit="0" * 40,
                    )
                    self.assertEqual(result["oracle"]["verdict"], "indeterminate")
                    self.assertEqual(result["outcome"], "inconclusive")
                    self.assertIn(result["outcome"], run_scenarios.ALLOWED_OUTCOMES)

            # A missing or non-string reason is a malformed return, not a
            # verdict: the reason is what a reader judges the verdict by, so
            # substituting a default would present a malformed return as a
            # well-formed one.
            for bad_reason in (None, [], "", 0, {"why": "x"}):
                with self.subTest(reason=bad_reason):
                    run_scenarios.ORACLES["resume-valid-evidence-only"] = (
                        lambda _e, r=bad_reason: {"verdict": "accept", "reason": r}
                    )
                    result = run_scenarios.run_scenario(
                        directory, variant="fixture",
                        product_commit=None, evaluator_commit="0" * 40,
                    )
                    self.assertEqual(result["outcome"], "inconclusive")
                    self.assertIn(result["outcome"], run_scenarios.ALLOWED_OUTCOMES)
                    self.assertIn("without a usable reason", result["oracle"]["reason"])
        finally:
            run_scenarios.ORACLES["resume-valid-evidence-only"] = original

    def test_an_unreadable_scenario_does_not_abort_the_suite(self) -> None:
        """A scenario that cannot be read must not hide the others' results.

        `run_scenario` guards the oracle; this guards its own preamble, where
        a corrupt `scenario.json` or an unknown oracle id is read before that
        boundary is reached.
        """
        import run_scenarios

        with tempfile.TemporaryDirectory() as tmp:
            scenarios = Path(tmp) / "scenarios"
            for name in ("a-good", "b-corrupt", "c-unknown-oracle"):
                (scenarios / name).mkdir(parents=True)
            # A valid scenario copied wholesale.
            source = SCENARIO_ROOT / "s5-fresh-session-resume"
            for child in source.rglob("*"):
                if child.is_file():
                    target = scenarios / "a-good" / child.relative_to(source)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(child.read_bytes())
            # A scenario whose definition is not valid JSON.
            (scenarios / "b-corrupt" / "scenario.json").write_text("{ not json", encoding="utf-8")
            # A scenario naming an oracle that does not exist.
            unknown = json.loads((source / "scenario.json").read_text(encoding="utf-8"))
            unknown["id"] = "unknown-oracle-scenario"
            unknown["oracle"]["id"] = "no-such-oracle"
            (scenarios / "c-unknown-oracle" / "scenario.json").write_text(
                json.dumps(unknown), encoding="utf-8"
            )

            out = Path(tmp) / "results"
            argv = sys.argv
            sys.argv = [
                "run_scenarios.py", "--variant", "fixture",
                "--evaluator-commit", "0" * 40,
                "--scenarios", str(scenarios), "--out", str(out),
            ]
            try:
                exit_code = run_scenarios.main()
            finally:
                sys.argv = argv

            self.assertEqual(exit_code, 1)
            summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
            # All three scenarios produced a result, not just the readable one.
            self.assertEqual(summary["scenario_count"], 3)
            self.assertEqual(summary["outcomes"]["fresh-session-resume"], "pass")
            self.assertEqual(summary["outcomes"]["b-corrupt"], "inconclusive")
            self.assertEqual(summary["outcomes"]["c-unknown-oracle"], "inconclusive")
            corrupt = json.loads((out / "b-corrupt.json").read_text(encoding="utf-8"))
            self.assertIn("could not be read", corrupt["oracle"]["reason"])
            # The placeholder stands in for a real result, so it must satisfy
            # the same schema. An off-schema placeholder would be rejected by
            # any downstream consumer that validates, turning a readable
            # failure into an unreadable artifact.
            from export_public import validate_schema_document

            schema = json.loads(
                (ROOT / "schemas" / "scenario-result.schema.json").read_text(encoding="utf-8")
            )
            for name in ("fresh-session-resume", "b-corrupt", "c-unknown-oracle"):
                result = json.loads((out / f"{name}.json").read_text(encoding="utf-8"))
                with self.subTest(result=name):
                    validate_schema_document(result, schema)
                    self.assertIs(result["is_product_result"], False)

    def test_an_unreadable_scenarios_placeholder_id_is_schema_valid(self) -> None:
        """The placeholder stands in for a result, so it must satisfy the schema.

        The directory name is arbitrary — uppercase, underscores, spaces, an
        empty fold — and the schema constrains `scenario_id` to
        `^[a-z0-9][a-z0-9-]+$`. Assuming the name satisfies that would emit an
        artifact any validating consumer rejects.
        """
        import run_scenarios

        names = ["s1-stale-review-after-change", "s7_new_scenario", "SX", "x", "a b c", "___", "9-ok"]
        with tempfile.TemporaryDirectory() as tmp:
            scenarios = Path(tmp) / "scenarios"
            for name in names:
                (scenarios / name).mkdir(parents=True)
                (scenarios / name / "scenario.json").write_text("{ corrupt", encoding="utf-8")

            out = Path(tmp) / "results"
            argv = sys.argv
            sys.argv = [
                "run_scenarios.py", "--variant", "fixture",
                "--evaluator-commit", "0" * 40,
                "--scenarios", str(scenarios), "--out", str(out),
            ]
            try:
                run_scenarios.main()
            finally:
                sys.argv = argv

            from export_public import validate_schema_document

            schema = json.loads(
                (ROOT / "schemas" / "scenario-result.schema.json").read_text(encoding="utf-8")
            )
            written = [p for p in sorted(out.glob("*.json")) if p.name != "summary.json"]
            # Every unreadable scenario still produced exactly one result.
            self.assertEqual(len(written), len(names))
            for path in written:
                result = json.loads(path.read_text(encoding="utf-8"))
                with self.subTest(result=path.name):
                    validate_schema_document(result, schema)
                    self.assertEqual(result["outcome"], "inconclusive")

    def test_s5_failed_evidence_with_current_digest_is_rejected(self) -> None:
        self.assertEqual(
            oracle_resume_valid_evidence_only(
                {
                    "before_digest": "0" * 64,
                    "after_digest": "a" * 64,
                    "reused_evidence": [
                        {"kind": "check", "status": "failed", "bound_digest": "a" * 64}
                    ],
                    "freshly_reacquired": [],
                }
            )["verdict"],
            "reject",
        )

    def test_s5_unproven_reacquisition_is_rejected(self) -> None:
        self.assertEqual(
            oracle_resume_valid_evidence_only(
                {
                    "before_digest": "0" * 64,
                    "after_digest": "a" * 64,
                    "reused_evidence": [
                        {"kind": "check", "status": "passed", "bound_digest": "0" * 64}
                    ],
                    # A bare kind list is a claim, not proof the check ran.
                    "freshly_reacquired": ["check"],
                }
            )["verdict"],
            "reject",
        )

    def test_s6_exit_claim_without_fresh_evidence_is_rejected(self) -> None:
        self.assertEqual(
            oracle_accepted_result_currency(
                {
                    "accepted_digest": "a" * 64,
                    "current_digest": "b" * 64,
                    "still_claims_current": False,
                    "exit_claimed": True,
                    "fresh_evidence": [],
                }
            )["verdict"],
            "reject",
        )

    def test_s6_exit_claim_with_bound_evidence_is_accepted(self) -> None:
        self.assertEqual(
            oracle_accepted_result_currency(
                {
                    "accepted_digest": "a" * 64,
                    "current_digest": "b" * 64,
                    "still_claims_current": False,
                    "exit_claimed": True,
                    "fresh_evidence": [{"kind": "check", "bound_digest": "b" * 64}],
                }
            )["verdict"],
            "accept",
        )


class ObservationHarnessTests(unittest.TestCase):
    """The observation must distinguish the conditions without scoring them."""

    def _observation(self) -> dict:
        import run_observation

        with tempfile.TemporaryDirectory() as outer:
            space = Path(outer)
            return run_observation.observe(space, "a" * 40)

    def test_observation_carries_both_conditions(self) -> None:
        observation = self._observation()
        self.assertEqual(observation["schema_version"], "observation-v2")
        self.assertEqual(
            set(observation["conditions"]), {"ungoverned", "governed"}
        )

    def test_ungoverned_condition_has_no_handle_and_claims_none(self) -> None:
        condition = self._observation()["conditions"]["ungoverned"]
        self.assertEqual(condition["handle_exists"]["value"], False)
        self.assertEqual(condition["records_are_hash_chained"]["value"], False)
        # A signal with no meaning in this condition is unavailable, not false.
        self.assertEqual(
            condition["contradicting_claim_was_refused"]["status"], "unavailable"
        )
        self.assertTrue(condition["contradicting_claim_was_refused"]["reason"])

    def test_ungoverned_findings_are_marked_not_product_results(self) -> None:
        condition = self._observation()["conditions"]["ungoverned"]
        self.assertEqual(condition["result_kind"], "fixture-scaffold")
        self.assertIs(condition["is_product_result"], False)

    def test_missing_measurement_is_never_zero(self) -> None:
        """Every unavailable signal carries a reason and no value."""
        observation = self._observation()

        def walk(node, path=""):
            if isinstance(node, dict):
                if node.get("status") == "unavailable":
                    self.assertTrue(node.get("reason"), f"{path} has no reason")
                    self.assertNotIn("value", node, f"{path} carries a value")
                elif node.get("status") == "measured":
                    self.assertIn("value", node, f"{path} is measured but valueless")
                for key, value in node.items():
                    walk(value, f"{path}/{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}[{index}]")

        walk(observation)

    def test_observation_is_not_a_product_result(self) -> None:
        observation = self._observation()
        self.assertTrue(observation["what_this_is_not"])
        self.assertTrue(observation["limitations"])
        joined = " ".join(observation["what_this_is_not"]).lower()
        self.assertIn("not a product result", joined)

    def test_governed_condition_reports_the_product_only_when_present(self) -> None:
        """Absent product is 'unavailable'; present product is measured."""
        import run_observation

        condition = self._observation()["conditions"]["governed"]
        if run_observation._exitbind_binary() is None:
            self.assertEqual(condition["status"], "unavailable")
            self.assertTrue(condition["reason"])
        else:
            self.assertEqual(condition["handle_exists"]["value"], True)
            self.assertTrue(condition["third_party_can_re_check"]["value"])
            self.assertTrue(condition["verify_command"])

    def test_hash_chain_check_rejects_a_broken_chain(self) -> None:
        import run_observation

        sound = [
            {"eventSha256": "a" * 64, "previousEventSha256": None},
            {"eventSha256": "b" * 64, "previousEventSha256": "a" * 64},
        ]
        self.assertTrue(run_observation._chain_is_sound(sound))
        broken = [dict(sound[0]), dict(sound[1], previousEventSha256="c" * 64)]
        self.assertFalse(run_observation._chain_is_sound(broken))

    def test_observation_space_defaults_outside_the_repository(self) -> None:
        from run_observation import DEFAULT_SPACE

        self.assertNotIn(str(ROOT), str(DEFAULT_SPACE))
        self.assertTrue(DEFAULT_SPACE.is_absolute())


if __name__ == "__main__":
    unittest.main()
