"""Small stdlib-only helpers shared by the evaluator commands."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

# Adapters are translation layers, not trusted extensions of the host process.
# Keep this list deliberately small; callers that need another value must make
# that dependency explicit in the adapter command itself.
CHILD_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "TZ")


def minimal_child_environment() -> dict[str, str]:
    """Return the only host-derived environment allowed for child commands."""
    environment = {name: os.environ[name] for name in CHILD_ENV_ALLOWLIST if name in os.environ}
    # Absolute binary paths are used by the runner, while adapter helper
    # commands (for example ``sh``) still need a predictable search path.
    environment.setdefault("PATH", os.defpath)
    return environment


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_adapter_path(manifest_path: Path, adapter: dict[str, Any]) -> Path:
    """Resolve the adapter implementation inside its manifest directory."""
    declared = adapter.get("adapter")
    if not isinstance(declared, str) or not declared:
        raise ValueError("adapter manifest must declare a non-empty adapter path")
    relative = Path(declared)
    if relative.is_absolute():
        raise ValueError("adapter path must be relative to its manifest")
    root = manifest_path.resolve().parent
    candidate = root / relative
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError(f"adapter path is symlinked: {declared}")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"adapter path is unreadable: {declared}") from error
    if not resolved.is_relative_to(root):
        raise ValueError(f"adapter path escapes its manifest: {declared}")
    try:
        mode = resolved.stat().st_mode
        if not stat.S_ISREG(mode) or not mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH):
            raise ValueError(f"adapter path is unreadable: {declared}")
        resolved.read_bytes()
    except (OSError, RuntimeError) as error:
        raise ValueError(f"adapter path is unreadable: {declared}") from error
    return resolved


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def case_paths(cases_dir: Path | None = None) -> list[Path]:
    directory = cases_dir or ROOT / "cases"
    return sorted(directory.glob("*.json"))


def load_cases(cases_dir: Path | None = None) -> list[dict[str, Any]]:
    return [load_json(path) for path in case_paths(cases_dir)]


def replace_tokens(argv: list[str], binary: str, case_id: str, workdir: Path, adapter: Path | None = None) -> list[str]:
    replacements = {"{binary}": binary, "{case_id}": case_id, "{workdir}": str(workdir)}
    if adapter is not None:
        replacements["{adapter}"] = str(adapter)
    return [replacements.get(token, token) for token in argv]


def observed_fields(payload: Any) -> tuple[str | None, str | None, str | None]:
    """Read the adapter's tiny public result envelope, not product internals."""
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        payload = payload["result"]
    if not isinstance(payload, dict):
        return None, None, None
    outcome = payload.get("outcome")
    reason = payload.get("reason")
    holytail = payload.get("holytail")
    return (
        outcome if isinstance(outcome, str) else None,
        reason if isinstance(reason, str) else None,
        holytail if isinstance(holytail, str) else None,
    )


def capture_environment(binary: Path | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "python": os.sys.version.split()[0],
        "platform": os.sys.platform,
        "cwd": str(Path.cwd()),
        "locale": os.environ.get("LC_ALL") or os.environ.get("LANG") or "",
        "child_environment": {
            "policy": "minimal-allowlist-v1",
            "allowlist": list(CHILD_ENV_ALLOWLIST),
            "os_sandbox": False,
        },
    }
    if binary:
        result["binary"] = str(binary.resolve())
        result["binary_sha256"] = sha256_file(binary)
    return result


def score_ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "percentage": round(100 * numerator / denominator, 4) if denominator else None,
        "status": "computed" if denominator else "not_computable",
    }


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive all public score fields from case evidence, not summary claims."""
    scored = [
        item for item in results
        if item.get("coverage") == {"support": "supported", "status": "exercised"}
    ]

    def panel(predicate, success) -> dict[str, Any]:
        selected = [item for item in scored if predicate(item)]
        return score_ratio(sum(1 for item in selected if success(item)), len(selected))

    def is_false(item):
        return item["expected"]["outcome"] != "READY"

    def is_valid(item):
        return item["expected"]["outcome"] == "READY"

    def refused(item):
        return item["observed"]["outcome"] == "REFUSED"

    def ready(item):
        return item["observed"]["outcome"] == "READY"

    def semantic_loss(item):
        return item["expected"]["holytail"] == "LOSS"

    def preserved(item):
        return item["expected"]["holytail"] == "PRESERVED"

    metrics = {
        "false_exit_rejection": panel(is_false, refused),
        "false_acceptance": panel(is_false, ready),
        "valid_exit_acceptance": panel(is_valid, ready),
        "false_refusal": panel(is_valid, lambda item: item["observed"]["outcome"] in {"REFUSED", "BLOCKED"}),
        "outcome_classification": panel(lambda item: True, lambda item: item["classification"]["outcome_match"]),
        "reason_classification": panel(lambda item: bool(item["expected"]["reason"]), lambda item: item["classification"]["reason_match"]),
        "holytail_preservation_recall": panel(semantic_loss, lambda item: item["observed"].get("holytail") == "LOSS"),
        "holytail_false_alarms": panel(preserved, lambda item: item["observed"].get("holytail") == "LOSS"),
    }
    return {
        "metrics": metrics,
        "false_acceptance_case_ids": [item["case_id"] for item in scored if is_false(item) and ready(item)],
        "mismatch_case_ids": [
            item["case_id"] for item in scored
            if not all(
                item["classification"][key]
                for key in ("outcome_match", "reason_match", "exit_code_match")
            ) or item["classification"]["holytail_match"] is False
        ],
    }
