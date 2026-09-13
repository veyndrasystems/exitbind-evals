#!/usr/bin/env python3
"""Run the pinned cases and emit raw results plus transparent score panels."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from eval_common import ROOT, aggregate_results, capture_environment, load_json, load_cases, resolve_adapter_path, sha256_bytes, sha256_file, write_json
from run_case import run


EXACT_COMMIT_SHA = re.compile(r"^[a-f0-9]{40}$")


def exact_commit_sha(value: str) -> str:
    if not EXACT_COMMIT_SHA.fullmatch(value):
        raise argparse.ArgumentTypeError("must be exactly 40 lowercase hexadecimal characters")
    return value


def aggregate(results: list[dict]) -> dict:
    return aggregate_results(results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--fixture", action="store_true", help="allow the evaluator-only fixture protocol")
    parser.add_argument("--product-commit-sha", type=exact_commit_sha, help="exact 40-character lowercase product commit SHA")
    parser.add_argument("--evaluator-commit-sha", type=exact_commit_sha, help="exact 40-character lowercase evaluator commit SHA")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--cases-dir", type=Path, default=ROOT / "cases")
    args = parser.parse_args()
    if args.run_id in {"", ".", ".."} or "/" in args.run_id:
        parser.error("--run-id must be a simple directory name")
    loaded_cases = load_cases(args.cases_dir)
    if args.adapter is None and not args.fixture:
        parser.error("supply --adapter for a product candidate, or explicitly opt into --fixture")
    if args.adapter is not None and args.fixture:
        parser.error("--adapter and --fixture are mutually exclusive")
    if args.fixture and (args.product_commit_sha is not None or args.evaluator_commit_sha is not None):
        parser.error("commit SHAs are only valid with --adapter candidate runs")
    if args.adapter is not None and (args.product_commit_sha is None or args.evaluator_commit_sha is None):
        parser.error("candidate runs require both --product-commit-sha and --evaluator-commit-sha")
    if any(case.get("scenario", {}).get("execution", {}).get("mode") == "candidate-adapter" for case in loaded_cases) and args.adapter is None:
        parser.error("candidate-adapter cases require an explicit --adapter manifest")
    out_dir = args.out.resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        parser.error(f"refusing to overwrite non-empty output: {out_dir}")
    cases = loaded_cases
    adapter_manifest_bytes = args.adapter.read_bytes() if args.adapter else None
    adapter_manifest = json.loads(adapter_manifest_bytes.decode("utf-8")) if adapter_manifest_bytes is not None else None
    adapter_implementation = resolve_adapter_path(args.adapter, adapter_manifest) if args.adapter and adapter_manifest else None
    adapter_implementation_sha256 = sha256_file(adapter_implementation) if adapter_implementation else None
    adapter_manifest_sha256 = sha256_bytes(adapter_manifest_bytes) if adapter_manifest_bytes is not None else None
    started = datetime.now(timezone.utc).isoformat()
    environment = {"schema_version": "environment-v1", "started_at": started, **capture_environment(Path(args.binary))}
    if args.adapter:
        environment["adapter_manifest"] = str(args.adapter.resolve())
        environment["adapter_manifest_sha256"] = adapter_manifest_sha256
        environment["adapter_implementation"] = str(adapter_implementation)
        environment["adapter_implementation_sha256"] = adapter_implementation_sha256
        environment["product_commit_sha"] = args.product_commit_sha
        environment["evaluator_commit_sha"] = args.evaluator_commit_sha
    write_json(out_dir / "environment.json", environment)
    results = []
    for case in cases:
        results.append(
            run(
                case,
                args.binary,
                out_dir,
                adapter_manifest,
                args.timeout,
                args.adapter,
                args.run_id,
                adapter_implementation_sha256,
                environment.get("binary_sha256"),
                adapter_manifest_sha256,
                args.product_commit_sha,
                args.evaluator_commit_sha,
            )
        )
    summary = {
        "schema_version": "summary-v1",
        "run_id": args.run_id,
        "case_count": len(results),
        "candidate_dependent": args.adapter is not None,
        "coverage": {
            "supported_exercised_case_ids": [item["case_id"] for item in results if item.get("coverage", {}).get("support") == "supported" and item.get("coverage", {}).get("status") == "exercised"],
            "unsupported_unexercised_case_ids": [item["case_id"] for item in results if item.get("coverage", {}).get("support") == "unsupported" or item.get("coverage", {}).get("status") == "unexercised"],
        },
        "aggregated": aggregate(results),
        "environment_file": "environment.json",
        "case_result_directory": "cases",
    }
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not summary["aggregated"]["mismatch_case_ids"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
