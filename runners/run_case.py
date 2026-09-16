#!/usr/bin/env python3
"""Execute one case through an explicitly supplied black-box binary."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from eval_common import load_json, minimal_child_environment, observed_fields, observed_measurements, replace_tokens, resolve_adapter_path, sha256_bytes, sha256_file, write_json


COMMIT_SHA = re.compile(r"^[a-f0-9]{40}$")
SUPPORTED_COVERAGE = {"support": "supported", "status": "exercised"}
UNSUPPORTED_COVERAGE = {"support": "unsupported", "status": "unexercised"}


def _verify_case_identity(
    binary: str,
    adapter_path: Path,
    adapter: dict,
    *,
    binary_sha256: str | None,
    manifest_sha256: str | None,
    implementation_sha256: str | None,
    product_commit_sha: str | None,
    evaluator_commit_sha: str | None,
) -> None:
    """Rebind every candidate identity from immutable bytes immediately before a case."""
    if binary_sha256 is not None and sha256_file(Path(binary)) != binary_sha256:
        raise ValueError("product binary changed after identity capture")
    manifest_bytes = adapter_path.read_bytes()
    if manifest_sha256 is not None and sha256_bytes(manifest_bytes) != manifest_sha256:
        raise ValueError("adapter manifest changed after identity capture")
    parsed = json.loads(manifest_bytes.decode("utf-8"))
    if parsed != adapter:
        raise ValueError("adapter manifest parse changed after identity capture")
    implementation = resolve_adapter_path(adapter_path, parsed)
    if implementation_sha256 is not None and sha256_file(implementation) != implementation_sha256:
        raise ValueError("adapter implementation changed after identity capture")
    for label, value in (("product", product_commit_sha), ("evaluator", evaluator_commit_sha)):
        if value is not None and not COMMIT_SHA.fullmatch(value):
            raise ValueError(f"{label} commit identity changed or is invalid")


def command_for(case: dict, binary: str, workdir: Path, adapter: dict | None, adapter_path: Path | None = None) -> list[str]:
    if adapter is not None:
        commands = adapter.get("commands", {})
        argv = commands.get(case["id"])
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise ValueError(f"adapter has no command for {case['id']}")
    else:
        argv = case["scenario"]["execution"]["argv"]
    if adapter is not None and adapter_path is not None and "{adapter}" not in argv:
        raise ValueError("adapter command must use its manifest adapter token")
    adapter_command = resolve_adapter_path(adapter_path, adapter) if adapter is not None and adapter_path is not None else None
    if "{adapter}" in argv and adapter_command is None:
        raise ValueError("adapter command requires its manifest path")
    return replace_tokens(argv, binary, case["id"], workdir, adapter_command)


def run(
    case: dict,
    binary: str,
    out_dir: Path,
    adapter: dict | None,
    timeout: float,
    adapter_path: Path | None = None,
    run_id: str | None = None,
    adapter_implementation_sha256: str | None = None,
    binary_sha256: str | None = None,
    adapter_manifest_sha256: str | None = None,
    product_commit_sha: str | None = None,
    evaluator_commit_sha: str | None = None,
) -> dict:
    case_id = case["id"]
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    workdir = out_dir / "work" / case_id
    workdir.mkdir(parents=True, exist_ok=True)
    if adapter is not None and adapter_path is not None:
        _verify_case_identity(
            binary,
            adapter_path,
            adapter,
            binary_sha256=binary_sha256,
            manifest_sha256=adapter_manifest_sha256,
            implementation_sha256=adapter_implementation_sha256,
            product_commit_sha=product_commit_sha,
            evaluator_commit_sha=evaluator_commit_sha,
        )
    argv = command_for(case, binary, workdir, adapter, adapter_path)
    started = time.monotonic()
    timed_out = False
    try:
        process = subprocess.run(
            argv,
            cwd=workdir,
            env=minimal_child_environment(),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        exit_code: int | None = process.returncode
        stdout = process.stdout
        stderr = process.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        exit_code = None
        stdout = error.stdout or b""
        stderr = error.stderr or b""
    except OSError as error:
        timed_out = False
        exit_code = None
        stdout = b""
        stderr = str(error).encode()
    duration_ms = round((time.monotonic() - started) * 1000, 3)
    stdout_path = raw_dir / f"{case_id}.stdout"
    stderr_path = raw_dir / f"{case_id}.stderr"
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)

    payload = None
    parse_error = None
    if stdout.strip():
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            parse_error = str(error)
    outcome, reason, holytail = observed_fields(payload)
    measurements = observed_measurements(payload, candidate_dependent=adapter is not None)
    if adapter is not None:
        candidate_envelope = (
            isinstance(payload, dict)
            and isinstance(payload.get("adapter_version"), str)
            and isinstance(payload.get("result"), dict)
            and set(payload["result"]) == {"outcome", "reason", "holytail"}
            and isinstance(payload.get("product_evidence"), dict)
            and isinstance(payload.get("coverage"), dict)
        )
        coverage = payload.get("coverage") if candidate_envelope else UNSUPPORTED_COVERAGE
        if coverage not in (SUPPORTED_COVERAGE, UNSUPPORTED_COVERAGE):
            coverage = UNSUPPORTED_COVERAGE
    else:
        coverage = payload.get("coverage") if isinstance(payload, dict) else None
        if not isinstance(coverage, dict):
            coverage = SUPPORTED_COVERAGE
    expected = case["ground_truth"]
    outcome_match = outcome == expected["expected_outcome"]
    reason_match = reason == expected["expected_reason"]
    exit_match = exit_code == expected["expected_exit_code"]
    holytail_match = None
    holytail_measurement = measurements["holytail"]
    if expected["holytail_expected"] != "NOT_APPLICABLE" and holytail_measurement["status"] == "measured":
        holytail_match = holytail_measurement["value"] == expected["holytail_expected"]
    result = {
        "schema_version": "result-v1",
        "run_id": run_id or out_dir.name,
        "case_id": case_id,
        "expected": {"outcome": expected["expected_outcome"], "reason": expected["expected_reason"], "exit_code": expected["expected_exit_code"], "holytail": expected["holytail_expected"]},
        "observed": {"outcome": outcome, "reason": reason, "exit_code": exit_code, "timed_out": timed_out, "json_parse_error": parse_error, "holytail": holytail, "duration_ms": duration_ms},
        "classification": {"outcome_match": outcome_match, "reason_match": reason_match, "exit_code_match": exit_match, "holytail_match": holytail_match},
        "measurements": measurements,
        "coverage": coverage,
        "raw": {"stdout_path": str(stdout_path.relative_to(out_dir)), "stderr_path": str(stderr_path.relative_to(out_dir)), "stdout_sha256": sha256_bytes(stdout), "stderr_sha256": sha256_bytes(stderr)},
        "command": argv,
    }
    write_json(out_dir / "cases" / f"{case_id}.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--case", dest="case_path", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--fixture", action="store_true", help="allow the evaluator-only fixture protocol")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.adapter is None and not args.fixture:
        parser.error("supply --adapter for a product candidate, or explicitly opt into --fixture")
    if args.adapter is not None and args.fixture:
        parser.error("--adapter and --fixture are mutually exclusive")
    case = load_json(args.case_path)
    adapter = load_json(args.adapter) if args.adapter else None
    result = run(case, args.binary, args.out, adapter, args.timeout, args.adapter)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["classification"]["outcome_match"] and result["classification"]["exit_code_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
