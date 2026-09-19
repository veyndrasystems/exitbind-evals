#!/usr/bin/env python3
"""Observe the same evaluator work under two conditions.

Answers one question: *what does governing the evaluator's own work change?*

The two conditions are:

``ungoverned``
    Work is done and reported by the actor that did it. Nothing external
    attests that the recorded steps happened, and nothing can refuse the
    claim. The evaluator's own scenario suite is already of this kind: each
    result binds the revision it was produced from, and that is all.

``governed``
    The same kind of work is driven through an Exitbind-managed run. A handle
    exists, each step is chained to its predecessor by hash, and a claim that
    contradicts the recorded check is refused rather than recorded.

The observation records the *structural difference* between those two, using
the product's own model-free benchmark as the governed specimen and the
evaluator's own fixture suite as the ungoverned one. Both are deterministic,
neither makes a model call, and the comparison is of provenance and refusal —
not of scenario verdicts, which are identical by construction.

A signal that cannot be established is recorded as ``unavailable`` with a
reason. It is never scored as zero and never counted as a failure.

This harness produces observation data, not a product result — see
``EVAL_PROTOCOL.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SPACE = Path(
    os.environ.get(
        "EXITBIND_EVALS_OBSERVATION_SPACE",
        Path.home() / ".local" / "state" / "exitbind-evals-observation",
    )
)


def _unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason}


def _measured(value, unit: str | None = None) -> dict:
    payload = {"status": "measured", "value": value}
    if unit:
        payload["unit"] = unit
    return payload


def _run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)


def _exitbind_binary() -> str | None:
    found = shutil.which("exitbind")
    if found:
        return found
    candidate = Path.home() / ".local" / "bin" / "exitbind"
    return str(candidate) if candidate.is_file() else None


def _read_ledger(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _chain_is_sound(records: list[dict]) -> bool:
    """Every record must name its predecessor's hash, and the chain must link."""
    for index, record in enumerate(records):
        expected = None if index == 0 else records[index - 1]["eventSha256"]
        if record.get("previousEventSha256") != expected:
            return False
    return True


def _ungoverned_condition(space: Path, evaluator_commit: str) -> dict:
    """The evaluator's own suite: it reports, and nothing can refuse it."""
    out = space / "ungoverned" / "scenario-results"
    suite = _run(
        [
            sys.executable,
            str(ROOT / "runners" / "run_scenarios.py"),
            "--variant", "fixture",
            "--evaluator-commit", evaluator_commit,
            "--out", str(out),
        ]
    )
    condition = {
        "condition": "ungoverned",
        "subject": "the evaluator's own external-trust scenario suite (fixture variant)",
        "suite_exit_code": _measured(suite.returncode),
        "reports_its_own_steps": _measured(True, "boolean"),
        "handle_exists": _measured(False, "boolean"),
        "records_are_hash_chained": _measured(False, "boolean"),
        "contradicting_claim_was_refused": _unavailable(
            "no governed medium exists in this condition, so a false completion claim "
            "has nothing that can refuse it; the suite has no acceptance step at all"
        ),
        "third_party_can_re_check": _unavailable(
            "results bind their evaluator revision, but nothing independent re-runs the "
            "claim; re-checking is a manual act by whoever holds the revision"
        ),
    }
    summary = out / "summary.json"
    if summary.is_file():
        payload = json.loads(summary.read_text(encoding="utf-8"))
        condition["outcomes"] = _measured(payload.get("outcomes"))
        condition["result_kind"] = payload.get("result_kind")
        condition["is_product_result"] = payload.get("is_product_result")
    return condition


def _governed_condition(space: Path, binary: str) -> dict:
    """The product's model-free benchmark: a real ledger with real refusals."""
    out = space / "governed" / "benchmark"
    if out.exists():
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    bench = _run([binary, "benchmark", "--output", str(out)])
    condition = {
        "condition": "governed",
        "subject": "the product's model-free checked-work benchmark (synthetic origin)",
        "suite_exit_code": _measured(bench.returncode),
        "handle_exists": _measured(out.is_dir(), "boolean"),
    }
    if not out.is_dir():
        condition["records_are_hash_chained"] = _unavailable(
            f"the benchmark produced no output directory (exit {bench.returncode})"
        )
        return condition

    final = out / "ledgers" / "final.jsonl"
    blocked = out / "ledgers" / "blocked.jsonl"
    if not final.is_file() or not blocked.is_file():
        condition["records_are_hash_chained"] = _unavailable(
            "the benchmark did not emit both a final and a blocked ledger"
        )
        return condition

    governed_records = _read_ledger(final)
    blocked_records = _read_ledger(blocked)
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    observed = result.get("observedResult", {})
    protected = observed.get("protectedFailedCheck", {})
    recovery = observed.get("protectedRecovery", {})

    condition.update(
        {
            "producer": result.get("producer"),
            "product_version": (
                (governed_records[0].get("producer") or {}).get("version")
                if governed_records else None
            ),
            "record_count": _measured(len(governed_records)),
            "records_are_hash_chained": _measured(_chain_is_sound(governed_records)),
            "distinct_event_hashes": _measured(
                len({r["eventSha256"] for r in governed_records})
            ),
            "actions_observed": _measured(
                sorted({
                    r.get("action") for r in governed_records if r.get("action")
                })
            ),
            "contradicting_claim_was_refused": _measured(
                protected.get("canonicalAcceptedEvent") is False
                and protected.get("status") != "accepted"
            ),
            "refusal_reason_recorded": _measured(
                next(
                    (r.get("reason") for r in governed_records
                     if r.get("action") == "protect"),
                    None,
                )
            ),
            "passing_check_alone_accepted": _measured(
                recovery.get("passingCheckAloneAccepted")
            ),
            "reviewer_approval_alone_accepted": _measured(
                recovery.get("reviewerApprovalAloneAccepted")
            ),
            "prior_attempt_preserved": _measured(recovery.get("priorAttemptPreserved")),
            "third_party_can_re_check": _measured(True, "boolean"),
            "verify_command": f"{binary} run report <ledger>",
            "blocked_ledger_records": _measured(len(blocked_records)),
            "automated_elapsed_ms": _measured(result.get("automatedElapsedMs"), "ms"),
        }
    )

    # A run past the refusal point must have stopped, not been rewritten.
    condition["blocked_ledger_preserved"] = _measured(
        [r["eventSha256"] for r in blocked_records]
        == [r["eventSha256"] for r in governed_records[: len(blocked_records)]]
    )
    return condition


def observe(space: Path, evaluator_commit: str) -> dict:
    """Take one paired observation and write it under ``space/runs``."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = space / "runs" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    binary = _exitbind_binary()
    if binary is None:
        governed = {
            "condition": "governed",
            "status": "unavailable",
            "reason": "no exitbind binary is installed on this machine",
        }
        version = None
    else:
        version_out = _run([binary, "--version"])
        version = version_out.stdout.strip() or None
        governed = _governed_condition(space, binary)

    ungoverned = _ungoverned_condition(space, evaluator_commit)

    differences = [
        {
            "signal": "handle_exists",
            "ungoverned": False,
            "governed": governed.get("handle_exists", {}).get("value"),
        },
        {
            "signal": "records_are_hash_chained",
            "ungoverned": False,
            "governed": governed.get("records_are_hash_chained", {}).get("value"),
        },
        {
            "signal": "contradicting_claim_was_refused",
            "ungoverned": None,
            "governed": governed.get("contradicting_claim_was_refused", {}).get("value"),
        },
        {
            "signal": "passing_check_alone_accepted",
            "ungoverned": None,
            "governed": governed.get("passing_check_alone_accepted", {}).get("value"),
        },
        {
            "signal": "third_party_can_re_check",
            "ungoverned": None,
            "governed": governed.get("third_party_can_re_check", {}).get("value"),
        },
    ]

    observation = {
        "schema_version": "observation-v2",
        "observed_at_utc": stamp,
        "baseline_revision": evaluator_commit,
        "exitbind_version": version,
        "conditions": {"ungoverned": ungoverned, "governed": governed},
        "differences": differences,
        "what_this_is": (
            "A structural observation of evaluator work under two conditions: one "
            "with an Exitbind-managed handle, one without. Neither side is a "
            "product performance measurement. No model was called."
        ),
        "what_this_is_not": [
            "It is not a product result or a claim about Exitbind's quality.",
            "It does not compare pass rates: the two conditions exercise different "
            "work (the product's benchmark scenario vs the evaluator's suite), so "
            "their outcomes are not commensurable.",
            "It does not establish provider-level reviewer independence, which "
            "remains unverified.",
            "It measures no human time and no cost.",
        ],
        "limitations": [
            "The governed condition is the product's own synthetic benchmark, not "
            "the evaluator's six scenarios driven under governance. Driving the "
            "evaluator's scenarios through a governed run remains NOT_YET_RUN.",
            "A 'None' in the differences table means the signal has no meaning in "
            "that condition, not that it measured false.",
            "The governed specimen runs in a machine-local scratch project; its "
            "ledger is evidence about the product's mechanism, not about this "
            "repository.",
        ],
    }
    (run_dir / "observation.json").write_text(
        json.dumps(observation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return observation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--space", type=Path, default=DEFAULT_SPACE)
    parser.add_argument("--evaluator-commit", default=None)
    args = parser.parse_args()

    commit = args.evaluator_commit
    if not commit:
        head = _run(["git", "-C", str(ROOT), "rev-parse", "HEAD"])
        commit = head.stdout.strip() if head.returncode == 0 else None
    if not commit:
        print("observation requires --evaluator-commit or a git checkout", file=sys.stderr)
        return 2

    args.space.mkdir(parents=True, exist_ok=True)
    (args.space / "governed").mkdir(exist_ok=True)
    (args.space / "ungoverned").mkdir(exist_ok=True)
    observation = observe(args.space, commit)
    print(json.dumps(observation, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
