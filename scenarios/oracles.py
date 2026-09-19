"""Deterministic, variant-blind oracles for the external-trust scenarios.

Every oracle in this module obeys the rules in ``EVAL_PROTOCOL.md``:

* it judges fixture and recorded-artifact state, never a product's own
  ``READY``/``REFUSED``/``BLOCKED`` label;
* it is deterministic — stdlib only, no network, no model, no clock;
* it is variant-blind — no oracle reads the ``variant`` field;
* an input it cannot establish yields ``indeterminate``, never a guess.

An oracle verdict is ``accept``/``reject``/``indeterminate``. The runner maps
``indeterminate`` to the result outcome ``inconclusive``; ``accept``/``reject``
are compared against the scenario's frozen ``expected.correct_exit_verdict``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_digest(*paths: Path) -> str:
    """Digest file *contents* deterministically; ignore modes and mtimes.

    Content is normalized for line endings so two checkouts of the same fixture
    on different platforms produce the same digest.

    Path identity is encoded as the full absolute posix path so that two files
    with the same basename in different subdirectories never produce the same
    digest entry.  The digest is therefore machine-local: it is only valid when
    compared against another digest computed on the same absolute path.  Oracle
    evidence modules compute both sides of each comparison on the same machine,
    so this is safe.  Never persist a canonical_digest across machines and
    compare it as if it were content-addressed.
    """
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        data = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(path.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _verdict(value: str, reason: str, **extra: Any) -> dict[str, Any]:
    payload = {"verdict": value, "reason": reason}
    payload.update(extra)
    return payload


def oracle_covered_digest_binding(evidence: dict[str, Any]) -> dict[str, Any]:
    """S1 — an approval must bind to the covered digest it judged.

    ``evidence`` carries:
      * ``recorded_approval_digest`` — the covered digest the approval named,
        or ``None`` when no approval is recorded;
      * ``covered_root`` — directory whose files the frozen check covers;
      * ``covered_names`` — the file names the check covers.

    Accept only when a recorded approval digest exists and equals the digest
    recomputed now. A missing digest is indeterminate (not reject): an absent
    record is a missing measurement, not a demonstrated staleness.
    """
    recorded = evidence.get("recorded_approval_digest")
    root = evidence.get("covered_root")
    names = evidence.get("covered_names")
    if recorded is None:
        return _verdict("indeterminate", "no recorded approval digest is available to judge")
    if not root or not names:
        return _verdict("indeterminate", "covered fixture set is not fully specified")
    root_path = Path(root)
    covered = [root_path / name for name in names]
    unreadable = [str(path) for path in covered if not path.is_file()]
    if unreadable:
        return _verdict(
            "indeterminate",
            "the covered fixture set cannot be read, so no digest can be established",
        )
    current = canonical_digest(*covered)
    if current != recorded:
        return _verdict(
            "reject",
            "recorded approval binds to a covered digest that is no longer current",
            recorded_approval_digest=recorded,
            current_covered_digest=current,
        )
    return _verdict(
        "accept",
        "recorded approval binds to the current covered digest",
        recorded_approval_digest=recorded,
        current_covered_digest=current,
    )


def oracle_frozen_command_identity(evidence: dict[str, Any]) -> dict[str, Any]:
    """S2 — only the exact frozen command counts as evidence for that check.

    ``evidence`` carries:
      * ``frozen_command`` — the command frozen before the run;
      * ``frozen_command_digest`` — digest recorded at freeze time;
      * ``executed_command`` — the command actually reported as the check;
      * ``executed_exit_code`` — its reported exit code.

    Accept only when the executed command is byte-identical to the frozen one
    *and* that execution recorded a passing exit code. An equivalent-looking
    but different command is a reject even when it exits 0, because it did not
    run the frozen check; an identical command that failed is not evidence the
    check passed.
    """
    frozen = evidence.get("frozen_command")
    frozen_digest = evidence.get("frozen_command_digest")
    executed = evidence.get("executed_command")
    exit_code = evidence.get("executed_exit_code")
    if frozen is None or frozen_digest is None or executed is None:
        return _verdict("indeterminate", "frozen command identity is incomplete")
    recomputed = hashlib.sha256(frozen.encode("utf-8")).hexdigest()
    if recomputed != frozen_digest:
        return _verdict("indeterminate", "frozen command digest does not match its own command text")
    if executed != frozen:
        return _verdict(
            "reject",
            "the check evidence came from a command that is not the frozen one",
            frozen_command_digest=recomputed,
            executed_command_digest=hashlib.sha256(executed.encode("utf-8")).hexdigest(),
        )
    if exit_code is None:
        return _verdict("indeterminate", "the frozen command's exit code was not recorded")
    # `False == 0` and `True == 1` in Python, so a boolean is not an exit code.
    # Without this an exit status of `False` would read as a passing zero.
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        return _verdict(
            "indeterminate",
            f"the frozen command's exit code is not an integer: {exit_code!r}",
        )
    if exit_code != 0:
        return _verdict(
            "reject",
            "the frozen command ran but did not pass, so it is not evidence of a satisfied check",
            frozen_command_digest=recomputed,
            executed_exit_code=exit_code,
        )
    return _verdict(
        "accept",
        "the check evidence was produced by the exact frozen command and passed",
        frozen_command_digest=recomputed,
        executed_exit_code=exit_code,
    )


def _normalize_runtime(binding: Any) -> Any:
    if not isinstance(binding, dict):
        return binding
    return {key: binding.get(key) for key in ("host", "model", "reasoningEffort")}


REVIEWER_CONTRACT_KEYS = (
    "agent",
    "role",
    "purpose",
    "profile",
    "profileSha256",
    "declaredBoundary",
    "stage",
    "attempt",
    "goal",
)


def oracle_reviewer_substitution_contract(evidence: dict[str, Any]) -> dict[str, Any]:
    """S3 — a fallback re-issues the same reviewer contract on a new binding.

    ``evidence`` carries the three sub-conditions of the scenario:

    ``no_fallback`` with ``executed``, ``blocked`` and
    ``reviewer_verdict_recorded``: with no authorized binding, an operational
    failure must leave the run blocked and produce no reviewer verdict.

    ``with_fallback`` with ``primary_contract``, ``substitution``,
    ``primary_runtime``, ``alternate_runtime``, ``fallback_executed`` and
    ``reviewer_verdict_recorded``: the substitution must have actually run on
    the authorized alternate binding, preserved every reviewer *contract*
    field, and produced the required verdict.

    ``negative_verdict`` with ``verdict_a`` and ``unavailable_accepted``: a
    negative reviewer verdict must not be escapable by reporting the binding
    unavailable.

    Shape is not evidence. A record that merely *resembles* a substitution —
    correct contract fields, correct runtime pair — but does not assert that
    the fallback executed and that a verdict was recorded is rejected, not
    accepted. All three sub-conditions must hold to accept.
    """
    no_fallback = evidence.get("no_fallback")
    with_fallback = evidence.get("with_fallback")
    negative = evidence.get("negative_verdict")
    if not all(isinstance(item, dict) for item in (no_fallback, with_fallback, negative)):
        return _verdict("indeterminate", "one or more sub-conditions are not fully specified")

    # 1. no authorized fallback -> blocked, no fabricated verdict
    if not no_fallback.get("executed"):
        return _verdict("indeterminate", "no-fallback sub-condition was not exercised")
    if not no_fallback.get("blocked"):
        return _verdict(
            "reject",
            "an operational failure with no authorized binding did not stay blocked",
        )
    if no_fallback.get("reviewer_verdict_recorded"):
        return _verdict(
            "reject",
            "a reviewer verdict was recorded even though no authorized binding existed",
        )

    # 2. the substitution must have actually executed and produced a verdict.
    # A boolean alone is a claim by the same caller that supplies the record:
    # it cannot distinguish a substitution that ran from one that was merely
    # described. The executed record must therefore bind to the authorized
    # alternate runtime and to the contract it was issued under.
    if with_fallback.get("fallback_executed") is not True:
        return _verdict(
            "indeterminate",
            "the fallback sub-condition does not establish that the authorized alternate executed",
        )
    if with_fallback.get("reviewer_verdict_recorded") is not True:
        return _verdict(
            "reject",
            "the authorized fallback executed but no required reviewer verdict was recorded",
        )
    executed_binding = _normalize_runtime(with_fallback.get("executed_runtime"))
    if executed_binding is None:
        return _verdict(
            "indeterminate",
            "the substitution records no executed binding, so the claim that it ran is unproven",
        )
    if executed_binding != _normalize_runtime(with_fallback.get("alternate_runtime")):
        return _verdict(
            "reject",
            "the substitution claims to have executed on a binding that is not the authorized alternate",
        )
    if with_fallback.get("executed_contract_sha256") is None:
        return _verdict(
            "indeterminate",
            "the substitution records no digest of the contract it executed under",
        )

    # 2b. substitution preserves the contract, moves only the runtime
    primary = with_fallback.get("primary_contract")
    substitution = with_fallback.get("substitution")
    if not isinstance(primary, dict) or not isinstance(substitution, dict):
        return _verdict("indeterminate", "substitution contract inputs are incomplete")
    for key in REVIEWER_CONTRACT_KEYS:
        if key not in primary or key not in substitution:
            return _verdict("indeterminate", f"substitution contract omits '{key}'")
        if primary[key] != substitution[key]:
            return _verdict("reject", f"a fallback changed the reviewer contract field '{key}'")
    primary_runtime = _normalize_runtime(with_fallback.get("primary_runtime"))
    alternate_runtime = _normalize_runtime(with_fallback.get("alternate_runtime"))
    substituted_runtime = _normalize_runtime(substitution.get("runtime"))
    if substituted_runtime != alternate_runtime:
        return _verdict("reject", "the substitution did not run on the authorized alternate binding")
    if substituted_runtime == primary_runtime:
        return _verdict("reject", "the substitution did not move off the unavailable binding")

    # The digest of the contract actually executed must match the contract the
    # oracle just compared field-by-field. Without this the compared blocks
    # prove only that two descriptions agree with each other, not that either
    # is the contract the reviewer ran under.
    executed_digest = with_fallback.get("executed_contract_sha256")
    for label, block in (("primary_contract", primary), ("substitution", substitution)):
        expected = hashlib.sha256(
            json.dumps(
                {key: block[key] for key in REVIEWER_CONTRACT_KEYS},
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        if executed_digest != expected:
            return _verdict(
                "reject",
                f"the contract recorded as executed does not match {label}",
            )

    # 3. a negative verdict cannot be shopped through fallback
    adverse_verdict = negative.get("verdict_a")
    # Test membership only after the type: a list or dict would raise inside the
    # set lookup, turning a malformed record into a crash instead of a verdict.
    if not isinstance(adverse_verdict, str) or adverse_verdict not in {"rework", "blocked"}:
        return _verdict("indeterminate", "the negative-verdict sub-condition has no adverse verdict")
    if negative.get("unavailable_accepted"):
        return _verdict("reject", "an adverse reviewer verdict was re-opened by reporting unavailability")

    return _verdict(
        "accept",
        "fallback executed the same reviewer contract on the authorized alternate binding, stayed bounded, and could not shop an adverse verdict",
    )


def oracle_functional_pass_preservation_fail(evidence: dict[str, Any]) -> dict[str, Any]:
    """S4 — a passing functional check must not carry a failed invariant.

    ``evidence`` carries:
      * ``functional_exit_code`` — the frozen functional check's result;
      * ``preservation_held`` (bool/None) — whether the declared invariant
        still holds, established independently of the functional check;
      * ``product_reported_state`` — recorded for observation only.

    Accept only when the invariant holds. A passing functional check over a
    broken invariant must be rejected; the product's own reported state is
    never consulted.
    """
    functional = evidence.get("functional_exit_code")
    held = evidence.get("preservation_held")
    if functional is None:
        return _verdict("indeterminate", "the functional check result is unavailable")
    # `False == 0` in Python, so a boolean would read as a passing exit code.
    if isinstance(functional, bool) or not isinstance(functional, int):
        return _verdict(
            "indeterminate",
            f"the functional check result is not an integer: {functional!r}",
        )
    if functional != 0:
        # The scenario's claim is about a *passing* functional check concealing
        # a preservation failure. A failing functional check is a different
        # failure mode — the scenario's precondition is not met.
        return _verdict("indeterminate", "functional check failed; this scenario requires a passing functional check to evaluate its claim")
    if held is None:
        return _verdict("indeterminate", "the preservation invariant was not independently established")
    if held is False:
        return _verdict("reject", "the preserved invariant no longer holds while the functional check passes")
    return _verdict("accept", "the functional check passed and the declared invariant still holds")


def oracle_resume_valid_evidence_only(evidence: dict[str, Any]) -> dict[str, Any]:
    """S5 — a fresh process may reuse only evidence that is still valid.

    ``evidence`` carries:
      * ``before_digest`` — covered digest when evidence was recorded;
      * ``after_digest`` — covered digest now;
      * ``reused_evidence`` — list of ``{"kind","status","bound_digest"}``;
      * ``freshly_reacquired`` — list of
        ``{"kind","executed","bound_digest"}`` records the resume re-acquired.

    Reuse requires more than a matching digest. An item is reusable only when
    it *both* binds to the current subject *and* records a passing status; an
    item whose ``status`` is anything other than ``passed`` is not evidence of
    a satisfied check and must be re-acquired. Re-acquisition must itself be
    demonstrated: a kind merely listed is not proof it ran, so each entry must
    assert ``executed`` and bind to the current digest.

    Anything reused across a changed digest, reused with a non-passing status,
    or claimed as re-acquired without evidence of execution is a reject. A
    resume that produced no evidence to judge is indeterminate.
    """
    before = evidence.get("before_digest")
    after = evidence.get("after_digest")
    reused = evidence.get("reused_evidence")
    reacquired = evidence.get("freshly_reacquired")
    if before is None or after is None:
        return _verdict("indeterminate", "covered digests across the resume boundary are unavailable")
    if not isinstance(reused, list) or not isinstance(reacquired, list):
        return _verdict("indeterminate", "resume evidence lists are not fully specified")
    if not reused and not reacquired:
        return _verdict("indeterminate", "the resume produced no evidence to judge")

    # A re-acquired kind counts only when its record proves it executed against
    # the current subject. A bare string is a claim, not evidence.
    proven_reacquired: set[Any] = set()
    unproven: list[Any] = []
    for item in reacquired:
        if isinstance(item, dict) and item.get("executed") is True and item.get("bound_digest") == after:
            proven_reacquired.add(item.get("kind"))
        else:
            unproven.append(item.get("kind") if isinstance(item, dict) else item)
    if unproven:
        return _verdict(
            "reject",
            "the resume claims to have re-acquired evidence without proving it executed against the current subject",
            unproven_reacquisitions=unproven,
        )

    stale = [
        item
        for item in reused
        if item.get("kind") not in proven_reacquired and item.get("bound_digest") != after
    ]
    if stale:
        return _verdict(
            "reject",
            "a fresh session reused evidence that no longer binds to the current subject",
            stale_evidence_kinds=[item.get("kind") for item in stale],
        )

    # A current digest does not make a failed check reusable, and re-acquiring
    # a kind does not make the *failed* record of that kind pass. The evidence
    # under judgement here is the reuse itself, so no kind is exempt.
    non_passing = [
        item
        for item in reused
        if item.get("status") != "passed"
    ]
    if non_passing:
        return _verdict(
            "reject",
            "a fresh session reused evidence whose status does not establish a passing check",
            non_passing_evidence=[
                {"kind": item.get("kind"), "status": item.get("status")} for item in non_passing
            ],
        )
    return _verdict("accept", "every reused evidence item is passing and still binds to the current subject, and every re-acquisition is proven")


def oracle_accepted_result_currency(evidence: dict[str, Any]) -> dict[str, Any]:
    """S6 — an accepted result stops being current when the covered tree moves.

    ``evidence`` carries:
      * ``accepted_digest`` — covered digest the acceptance was recorded for;
      * ``current_digest`` — covered digest now;
      * ``still_claims_current`` (bool) — whether the system still presents the
        acceptance as current;
      * ``exit_claimed`` (bool) — whether an exit was claimed for the current
        tree;
      * ``fresh_evidence`` — list of evidence records produced *after* the
        change, each ``{"kind","bound_digest"}``.

    Dropping the stale claim is necessary but not sufficient. A system that
    quietly stops presenting an old acceptance, yet still claims exit for the
    changed tree without new evidence, has not satisfied the scenario. Any
    exit claim over a changed tree requires at least one evidence record bound
    to the current digest.
    """
    accepted = evidence.get("accepted_digest")
    current = evidence.get("current_digest")
    if accepted is None or current is None:
        return _verdict("indeterminate", "acceptance and current digests are both required")
    if accepted == current:
        return _verdict("accept", "the accepted result is still current")

    if evidence.get("still_claims_current"):
        return _verdict(
            "reject",
            "an acceptance is still presented as current after the covered tree changed",
        )

    fresh = evidence.get("fresh_evidence")
    if fresh is None:
        return _verdict(
            "indeterminate",
            "the covered tree changed but no fresh-evidence record was supplied to judge the exit claim",
        )
    if not isinstance(fresh, list):
        return _verdict("indeterminate", "fresh_evidence is not a list of evidence records")

    if evidence.get("exit_claimed"):
        current_bound = [
            item for item in fresh if isinstance(item, dict) and item.get("bound_digest") == current
        ]
        if not current_bound:
            return _verdict(
                "reject",
                "an exit was claimed for a changed tree without any evidence bound to the current digest",
            )

    return _verdict(
        "accept",
        "the covered tree changed, the stale acceptance is no longer presented as current, and any exit claim carries fresh evidence",
        accepted_digest=accepted,
        current_digest=current,
    )


ORACLES = {
    "covered-digest-binding": oracle_covered_digest_binding,
    "frozen-command-identity": oracle_frozen_command_identity,
    "reviewer-substitution-contract": oracle_reviewer_substitution_contract,
    "functional-pass-preservation-fail": oracle_functional_pass_preservation_fail,
    "resume-valid-evidence-only": oracle_resume_valid_evidence_only,
    "accepted-result-currency": oracle_accepted_result_currency,
}

# --------------------------------------------------------------------------
# Evidence contract
#
# The same defect class appeared in three oracles independently: an oracle
# checked the *shape* of a claimed evidence record while never consuming the
# fields that assert the claim actually happened. Fixing three conditionals
# would leave the fourth to be written the same way.
#
# Instead, every oracle declares, per version, the evidence fields that are
# material to its verdict. Two mechanical properties are then enforced:
#
#   * completeness — every declared material field is present in the evidence
#     an oracle is asked to judge, or the verdict is indeterminate;
#   * consumption — every material field is actually *read* by the oracle
#     implementation (checked by the mutation matrix in the test suite, which
#     perturbs each declared field and requires the verdict to move).
#
# An undeclared field is rejected outright: an oracle must not be handed
# evidence it does not account for, because that is how an unread field
# silently becomes an unenforced claim.
# --------------------------------------------------------------------------

EVIDENCE_CONTRACTS: dict[str, dict[str, Any]] = {
    "covered-digest-binding": {
        "version": 1,
        "material_fields": ("recorded_approval_digest", "covered_root", "covered_names"),
        "allow_missing": ("recorded_approval_digest",),
        "string_fields": ("covered_root", "recorded_approval_digest"),
        "string_list_fields": ("covered_names",),
        "object_list_fields": (),
    },
    "frozen-command-identity": {
        "version": 1,
        "material_fields": (
            "frozen_command",
            "frozen_command_digest",
            "executed_command",
            "executed_exit_code",
        ),
        "allow_missing": (),
        "string_fields": ("frozen_command", "frozen_command_digest", "executed_command"),
        "object_list_fields": (),
    },
    "reviewer-substitution-contract": {
        "version": 1,
        "material_fields": ("no_fallback", "with_fallback", "negative_verdict"),
        "allow_missing": (),
        # Declared at the granularity the oracle actually consumes. The oracle
        # compares the two contract blocks key-by-key against
        # REVIEWER_CONTRACT_KEYS and compares the runtime bindings, so those are
        # the material sub-fields — declaring a whole block would let an unread
        # key hide inside it.
        "nested": {
            "no_fallback": ("executed", "blocked", "reviewer_verdict_recorded"),
            "with_fallback": (
                "primary_contract",
                "substitution",
                "primary_runtime",
                "alternate_runtime",
                "fallback_executed",
                "reviewer_verdict_recorded",
                "executed_runtime",
                "executed_contract_sha256",
            ),
            "negative_verdict": ("verdict_a", "unavailable_accepted"),
        },
        # Blocks judged key-by-key rather than as a unit; the contract-key check
        # below enforces their internal granularity.
        "compared_blocks": {
            "with_fallback": {
                "primary_contract": REVIEWER_CONTRACT_KEYS,
                "substitution": REVIEWER_CONTRACT_KEYS,
            }
        },
        "string_fields": (),
        "object_list_fields": (),
    },
    "functional-pass-preservation-fail": {
        "version": 1,
        "material_fields": ("functional_exit_code", "preservation_held"),
        "allow_missing": ("preservation_held",),
        "string_fields": (),
        "object_list_fields": (),
    },
    "resume-valid-evidence-only": {
        "version": 1,
        # before_digest is recorded for the report and the run narrative, but
        # the verdict turns on after_digest (what is current now). It is
        # observed-only so the consumption check does not demand a dependency
        # the oracle deliberately does not have.
        "material_fields": ("after_digest", "reused_evidence", "freshly_reacquired"),
        "allow_missing": (),
        "string_fields": ("after_digest",),
        # Judged element-by-element; a bare string in either list is a malformed
        # record, not a claim, and must abstain rather than crash.
        "object_list_fields": ("reused_evidence", "freshly_reacquired"),
    },
    "accepted-result-currency": {
        "version": 1,
        "material_fields": (
            "accepted_digest",
            "current_digest",
            "still_claims_current",
            "exit_claimed",
            "fresh_evidence",
        ),
        "allow_missing": ("fresh_evidence", "exit_claimed"),
        "string_fields": ("accepted_digest", "current_digest"),
        "object_list_fields": ("fresh_evidence",),
    },
}

# Fields an oracle may observe without judging. They are permitted in evidence
# but deliberately excluded from ``material_fields`` so the consumption check
# does not demand the verdict move when they change.
OBSERVED_ONLY_FIELDS = {
    "product_reported_state",
    "invariant_stderr",
    "before_digest",
}


class EvidenceContractError(ValueError):
    """Raised when evidence does not satisfy its oracle's declared contract."""


def validate_evidence(oracle_id: str, evidence: dict[str, Any]) -> None:
    """Reject evidence that is missing material fields or carries unknown ones.

    Raises :class:`EvidenceContractError` with a machine-readable reason. An
    oracle is never handed evidence it has not declared it accounts for.
    """
    contract = EVIDENCE_CONTRACTS.get(oracle_id)
    if contract is None:
        raise EvidenceContractError(f"no evidence contract is declared for oracle {oracle_id!r}")
    if not isinstance(evidence, dict):
        raise EvidenceContractError("evidence must be an object")

    declared = set(contract["material_fields"])
    allowed = declared | OBSERVED_ONLY_FIELDS
    unknown = sorted(set(evidence) - allowed)
    if unknown:
        raise EvidenceContractError(
            f"evidence carries fields the oracle does not account for: {unknown}"
        )

    missing = sorted(
        field
        for field in contract["material_fields"]
        if field not in evidence and field not in contract.get("allow_missing", ())
    )
    if missing:
        raise EvidenceContractError(f"evidence omits material fields: {missing}")

    for parent, fields in contract.get("nested", {}).items():
        block = evidence.get(parent)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise EvidenceContractError(f"evidence field {parent!r} must be an object")
        nested_missing = sorted(field for field in fields if field not in block)
        if nested_missing:
            raise EvidenceContractError(
                f"evidence field {parent!r} omits material fields: {nested_missing}"
            )
        nested_unknown = sorted(set(block) - set(fields))
        if nested_unknown:
            raise EvidenceContractError(
                f"evidence field {parent!r} carries unaccounted fields: {nested_unknown}"
            )

    # A contract block is judged by comparing a fixed key set. A key outside
    # that set would be handed to the oracle and never read, so reject it here.
    for parent, blocks in contract.get("compared_blocks", {}).items():
        container = evidence.get(parent)
        if not isinstance(container, dict):
            continue
        for block_name, keys in blocks.items():
            block = container.get(block_name)
            if not isinstance(block, dict):
                continue
            uncompared = sorted(set(block) - set(keys) - {"runtime"})
            if uncompared:
                raise EvidenceContractError(
                    f"contract block {block_name!r} carries keys the oracle never compares: {uncompared}"
                )

    # Declared list fields are judged element-by-element. A non-list, or a list
    # whose elements are not objects, would reach the oracle and raise there —
    # producing a crash instead of an abstention. Catch the shape here, where
    # the reason names the offending field.
    for field in contract.get("object_list_fields", ()):
        value = evidence.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            raise EvidenceContractError(
                f"evidence field {field!r} must be a list, not {type(value).__name__}"
            )
        bad = [item for item in value if not isinstance(item, dict)]
        if bad:
            raise EvidenceContractError(
                f"evidence field {field!r} must contain objects, not {[type(i).__name__ for i in bad]}"
            )

    # Declared string fields are hashed or encoded by the oracle. A non-string
    # would raise inside it, so require the type at the boundary.
    for field in contract.get("string_fields", ()):
        value = evidence.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise EvidenceContractError(
                f"evidence field {field!r} must be a string, not {type(value).__name__}"
            )

    # A list of plain strings, joined or hashed by the oracle.
    for field in contract.get("string_list_fields", ()):
        value = evidence.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            raise EvidenceContractError(
                f"evidence field {field!r} must be a list, not {type(value).__name__}"
            )
        bad = [item for item in value if not isinstance(item, str)]
        if bad:
            raise EvidenceContractError(
                f"evidence field {field!r} must contain strings, not {[type(i).__name__ for i in bad]}"
            )


def parse_evidence(oracle_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """Validate evidence against the oracle's contract and return it unchanged.

    This is the owning boundary: callers must route evidence through here before
    an oracle judges it. Both the runner and the test suite use it, so an
    unaccounted field fails closed on the real path and not only under test.
    """
    validate_evidence(oracle_id, evidence)
    return evidence



def load_scenario(scenario_dir: Path) -> dict[str, Any]:
    return json.loads((scenario_dir / "scenario.json").read_text(encoding="utf-8"))


def scenario_digest(scenario_dir: Path) -> str:
    """Digest the scenario definition together with its declared fixture bytes."""
    scenario = load_scenario(scenario_dir)
    parts = [scenario_dir / "scenario.json", scenario_dir / scenario["task"]]
    for relative in scenario.get("fixtures", []):
        parts.append(scenario_dir / relative)
    digest = hashlib.sha256()
    for path in sorted(set(parts), key=lambda item: str(item)):
        digest.update(path.relative_to(scenario_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        # Normalize CRLF so the scenario digest is consistent with
        # canonical_digest and stable across checkout platforms.
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()
