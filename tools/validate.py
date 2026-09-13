#!/usr/bin/env python3
"""Validate the evaluator's committed JSON and boundary references."""

from __future__ import annotations

import json
import re
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEX40 = re.compile(r"^[a-f0-9]{40}$")
errors: list[str] = []
sys.path.insert(0, str(ROOT / "tools"))
from export_public import validate_schema_document

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

if errors:
    print("validation failed")
    print("\n".join(f"- {error}" for error in errors))
    raise SystemExit(1)
print(f"validated {len(list((ROOT / 'cases').glob('*.json')))} cases, {len(list((ROOT / 'schemas').glob('*.json')))} schemas, and corpus manifest")
