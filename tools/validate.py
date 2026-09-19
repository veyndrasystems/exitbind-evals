#!/usr/bin/env python3
"""Validate the evaluator's committed JSON and boundary references."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEX40 = re.compile(r"^[a-f0-9]{40}$")
errors: list[str] = []
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "scenarios"))
from export_public import validate_schema_document
from oracles import EVIDENCE_CONTRACTS, ORACLES, load_scenario

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--public-export", type=Path, help="validate one public-export-v2 JSON artifact")
args = parser.parse_args()


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        errors.append(f"{path}: invalid JSON: {error}")
        return None


schemas = {schema.name: load(schema) for schema in sorted((ROOT / "schemas").glob("*.json"))}
manifest = load(ROOT / "corpus/manifest.json")
if not isinstance(manifest, dict):
    errors.append("manifest is not an object")
else:
    try:
        validate_schema_document(manifest, schemas["manifest.schema.json"])
    except (KeyError, ValueError) as error:
        errors.append(f"manifest schema validation failed: {error}")
    if manifest.get("schema_version") != "manifest-v1" or manifest.get("status") != "metadata-only":
        errors.append("manifest must be manifest-v1 metadata-only")
    repos = manifest.get("repositories", [])
    repo_ids = {item.get("id") for item in repos if isinstance(item, dict)}
    if len(repo_ids) != len(repos):
        errors.append("repository IDs must be unique")
    for repo in repos:
        if not isinstance(repo, dict) or not HEX40.fullmatch(repo.get("commit", "")):
            errors.append(f"invalid pinned repository commit: {repo}")
        if isinstance(repo, dict) and (not repo.get("license_url") or not repo.get("fetch_verification")):
            errors.append(f"repository lacks license/fetch verification: {repo.get('id')}")
    case_files = manifest.get("cases", [])
    listed = {str(path) for path in case_files}
    actual = {str(path.relative_to(ROOT)) for path in (ROOT / "cases").glob("*.json")}
    if listed != actual:
        errors.append(f"manifest case list differs: listed={sorted(listed)} actual={sorted(actual)}")
    ids: set[str] = set()
    pairs: dict[str, list[dict]] = {}
    for relative in sorted(listed):
        case_path = ROOT / relative
        case = load(case_path)
        if not isinstance(case, dict):
            continue
        try:
            validate_schema_document(case, schemas["case.schema.json"])
        except (KeyError, ValueError) as error:
            errors.append(f"{relative}: case schema validation failed: {error}")
        required = {"schema_version", "id", "pair_id", "family", "title", "scenario", "ground_truth"}
        missing = required - set(case)
        if missing:
            errors.append(f"{relative}: missing {sorted(missing)}")
        if case.get("id") in ids:
            errors.append(f"duplicate case ID: {case.get('id')}")
        ids.add(case.get("id"))
        if case.get("repository") not in repo_ids:
            errors.append(f"{relative}: unknown repository {case.get('repository')}")
        pairs.setdefault(case.get("pair_id"), []).append(case)
        gt = case.get("ground_truth", {})
        if gt.get("expected_outcome") not in {"READY", "REFUSED", "BLOCKED"}:
            errors.append(f"{relative}: invalid expected outcome")
        if not isinstance(gt.get("expected_exit_code"), int) or not 0 <= gt["expected_exit_code"] <= 255:
            errors.append(f"{relative}: invalid expected exit code")
        execution = case.get("scenario", {}).get("execution", {})
        if not isinstance(execution.get("argv"), list) or "{binary}" not in execution.get("argv", []):
            errors.append(f"{relative}: execution argv must contain {{binary}}")
    for pair, members in pairs.items():
        if len(members) != 2 or not any(item.get("family") == "valid-control" for item in members) or not any(item.get("family") != "valid-control" for item in members):
            errors.append(f"pair {pair} must contain one adversarial case and one valid control")

if args.public_export:
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        from export_public import validate_public_export

        validate_public_export(json.loads(args.public_export.read_text(encoding="utf-8")))
    except Exception as error:
        errors.append(f"{args.public_export}: invalid public export: {error}")

# External-trust scenarios: definition, declared fixtures, and oracle wiring.
scenario_root = ROOT / "scenarios"
scenario_paths = sorted(scenario_root.glob("*/scenario.json"))
scenario_ids: set[str] = set()
for scenario_path in scenario_paths:
    scenario = load(scenario_path)
    if not isinstance(scenario, dict):
        continue
    try:
        validate_schema_document(scenario, schemas["scenario.schema.json"])
    except (KeyError, ValueError) as error:
        errors.append(f"{scenario_path.relative_to(ROOT)}: scenario schema validation failed: {error}")
        continue
    if scenario["id"] in scenario_ids:
        errors.append(f"{scenario_path}: duplicate scenario id {scenario['id']}")
    scenario_ids.add(scenario["id"])
    task = scenario_root / scenario_path.parent.name / scenario["task"]
    if not task.is_file():
        errors.append(f"{scenario_path}: task file is missing: {scenario['task']}")
    for relative in scenario.get("fixtures", []):
        if not (scenario_path.parent / relative).is_file():
            errors.append(f"{scenario_path}: declared fixture is missing: {relative}")
    oracle = scenario["oracle"]
    if oracle.get("reads_product_state") is not False:
        errors.append(f"{scenario_path}: an oracle must not declare reads_product_state true")
    if scenario.get("run_status") == "adapter_required" and not scenario.get("not_yet_run_reason"):
        errors.append(f"{scenario_path}: adapter_required scenario needs a not_yet_run_reason")
    if scenario.get("run_status") == "NOT_YET_RUN" and not scenario.get("not_yet_run_reason"):
        errors.append(f"{scenario_path}: NOT_YET_RUN scenario needs a not_yet_run_reason")
    if oracle.get("id") not in EVIDENCE_CONTRACTS:
        errors.append(f"{scenario_path}: oracle {oracle.get('id')!r} has no declared evidence contract")

# The frozen-command digest must match its own command text.
frozen_path = (
    scenario_root
    / "s2-equivalent-command-substitution/fixtures/frozen-command.json"
)
if frozen_path.is_file():
    frozen = load(frozen_path)
    if isinstance(frozen, dict):
        command = frozen.get("frozen_command")
        recorded = frozen.get("frozen_command_digest")
        if not isinstance(command, str) or not isinstance(recorded, str):
            errors.append(f"{frozen_path}: frozen command identity is incomplete")
        else:
            actual = hashlib.sha256(command.encode("utf-8")).hexdigest()
            if actual != recorded:
                errors.append(
                    f"{frozen_path}: frozen_command_digest does not match sha256(frozen_command)"
                )


if errors:
    print("validation failed")
    print("\n".join(f"- {error}" for error in errors))
    raise SystemExit(1)
print(f"validated {len(list((ROOT / 'cases').glob('*.json')))} cases, {len(list((ROOT / 'schemas').glob('*.json')))} schemas, and corpus manifest")
