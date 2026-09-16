#!/usr/bin/env python3
"""Export selected run results with bound, sanitized public stream evidence."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import sys
import urllib.parse
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_common import aggregate_results, load_json, sha256_bytes, sha256_file, write_json


PUBLIC_SCHEMA_VERSION = "public-export-v2"
_COMMIT_SHA = re.compile(r"^[a-f0-9]{40}$")
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\s]*")
_FILE_URI = re.compile(r"(?i)(?<![A-Za-z0-9_])file://(?:localhost)?/[^\s?#]*")
_NON_FILE_URI = re.compile(r"(?i)(?<![A-Za-z0-9_])(?:https?|ssh)://[^\s]+")
_POSIX_PATH = re.compile(r"(?<![A-Za-z0-9_])/(?:[^\s]+)")
_PATH_OPENERS = {"(": ")", "[": "]", "{": "}", "<": ">"}
_PATH_CLOSERS = set(_PATH_OPENERS.values())


def _is_absolute(value: str) -> bool:
    return Path(value).is_absolute() or bool(_WINDOWS_PATH.fullmatch(value)) or bool(_FILE_URI.fullmatch(value))


def _path_token(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"<redacted-absolute-path:{digest}>"


def _canonical_segments(value: str) -> list[tuple[str, list[tuple[int, int]]]]:
    """Build a separator-decoded view while retaining exact source spans."""
    segments: list[tuple[str, list[tuple[int, int]]]] = []
    chars: list[str] = []
    spans: list[tuple[int, int]] = []

    def flush() -> None:
        if chars:
            segments.append(("".join(chars), spans.copy()))
            chars.clear()
            spans.clear()

    index = 0
    while index < len(value):
        if value[index] != "\\":
            chars.append(value[index])
            spans.append((index, index + 1))
            index += 1
            continue

        run_end = index + 1
        while run_end < len(value) and value[run_end] == "\\":
            run_end += 1
        if run_end < len(value) and value[run_end] in {'"', "'"}:
            chars.append(value[run_end])
            spans.append((index, run_end + 1))
            index = run_end + 1
            continue
        if run_end < len(value) and value[run_end] == "/":
            chars.append("/")
            spans.append((index, run_end + 1))
            index = run_end + 1
            continue
        escape_start = run_end - 1
        if value[escape_start:escape_start + 6].lower() in {r"\u002f", r"\u005c"}:
            chars.append("/" if value[escape_start + 2:escape_start + 6].lower() == "002f" else "\\")
            spans.append((index, escape_start + 6))
            index = escape_start + 6
            continue
        unicode_escape = value[escape_start:escape_start + 6]
        if re.fullmatch(r"\\u[0-9a-fA-F]{4}", unicode_escape):
            decoded = chr(int(unicode_escape[2:], 16))
            if decoded == "`" or unicodedata.category(decoded).startswith("P"):
                chars.append(decoded)
                spans.append((index, escape_start + 6))
                index = escape_start + 6
                continue
        chars.append("\\")
        spans.append((index, run_end))
        index = run_end

    flush()
    return segments


def _looks_like_path_start(value: str, index: int) -> bool:
    tail = value[index:]
    return bool(tail.startswith("/") or re.match(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]", tail) or re.match(r"(?i)file://", tail))


def _is_boundary_after(value: str, index: int) -> bool:
    next_index = index + 1
    if next_index >= len(value) or value[next_index].isspace():
        return True
    if value[next_index] in ",;)]}>.!:?":
        return True
    return _looks_like_path_start(value, next_index)


def _is_terminal_presentation_punctuation(value: str, index: int, end: int) -> bool:
    """Treat terminal punctuation as presentation syntax; keep ambiguous bytes outside the token."""
    category = unicodedata.category(value[index])
    if category in {"Pi", "Pf"}:
        return False
    if value[index] != "`" and not category.startswith("P"):
        return False
    next_index = index
    while next_index < end and (
        value[next_index] == "`" or unicodedata.category(value[next_index]).startswith("P")
    ):
        next_index += 1
    return next_index >= len(value) or value[next_index].isspace() or _is_boundary_after(value, next_index - 1)


def _is_external_quote(
    value: str,
    index: int,
    start: int,
    end: int,
    spans: list[tuple[int, int]] | None = None,
    escaped_quote_is_external: bool = False,
) -> bool:
    """Keep a matching quote wrapper outside a path while retaining quote filename bytes."""
    quote = value[index]
    opener_start = start
    while opener_start > 0 and value[opener_start - 1] in {'"', "'"}:
        opener_start -= 1
    wrappers = list(value[opener_start:start])
    if wrappers and wrappers[-1] == quote:
        run_end = index + 1
        while run_end < end and value[run_end] == quote:
            run_end += 1
        top_count = 0
        for wrapper in reversed(wrappers):
            if wrapper != quote:
                break
            top_count += 1
        if run_end - index >= top_count:
            remaining = wrappers[:-top_count]
            cursor = run_end
            while cursor < end and remaining and value[cursor] == remaining[-1]:
                cursor += 1
                remaining.pop()
            boundary = cursor >= len(value) or value[cursor].isspace() or _is_boundary_after(value, cursor - 1)
            if boundary:
                return index >= run_end - top_count
    if (
        escaped_quote_is_external
        and not wrappers
        and spans is not None
        and spans[index][1] - spans[index][0] > 1
    ):
        run_end = index + 1
        while run_end < end and value[run_end] == quote:
            run_end += 1
        return run_end >= len(value) or value[run_end].isspace() or _is_boundary_after(value, run_end - 1)
    if spans is not None and not wrappers and quote == '"':
        run_start = index
        while run_start > start and value[run_start - 1] == quote:
            run_start -= 1
        run_end = index + 1
        while run_end < end and value[run_end] == quote:
            run_end += 1
        leading_count = 0
        while leading_count < len(value) and value[leading_count] == quote:
            leading_count += 1
        if (
            index == run_start
            and leading_count
            and run_end - run_start >= leading_count
            and quote not in value[leading_count:start]
            and spans[index][1] - spans[index][0] == spans[leading_count - 1][1] - spans[leading_count - 1][0]
            and (
                run_end >= len(value)
                or value[run_end].isspace()
                or _is_boundary_after(value, run_end - 1)
            )
        ):
            return True
    return False


def _path_candidate_end(
    value: str,
    start: int,
    end: int,
    spans: list[tuple[int, int]] | None = None,
    escaped_quote_is_external: bool = False,
) -> int:
    """Trim presentation delimiters while retaining balanced/internal path punctuation."""
    stack: list[tuple[str, bool]] = []
    if start and value[start - 1] in _PATH_OPENERS:
        stack.append((_PATH_OPENERS[value[start - 1]], True))
    index = start
    while index < end:
        char = value[index]
        if char in _PATH_OPENERS:
            stack.append((_PATH_OPENERS[char], False))
        elif char in _PATH_CLOSERS:
            if stack and stack[-1][0] == char:
                _, external = stack.pop()
                if external and _is_boundary_after(value, index):
                    return index
            elif not stack and _is_boundary_after(value, index):
                return index
        elif char in {'"', "'"}:
            if _is_external_quote(value, index, start, end, spans, escaped_quote_is_external):
                return index
        elif char in {"?", "#"}:
            return index
        elif char in {",", ";"}:
            next_index = index + 1
            if next_index >= len(value) or value[next_index].isspace():
                return index
            while next_index < len(value) and value[next_index].isspace():
                next_index += 1
            if next_index >= len(value) or value[next_index] in _PATH_CLOSERS or _looks_like_path_start(value, next_index):
                return index
        elif _is_terminal_presentation_punctuation(value, index, end):
            return index
        index += 1
    return end


def _detected_paths(value: str) -> list[tuple[int, int, str]]:
    """Return canonical absolute paths mapped back to non-overlapping source spans."""
    candidates: list[tuple[int, int, str]] = []
    patterns = (_FILE_URI, _WINDOWS_PATH, _POSIX_PATH)
    for canonical, spans in _canonical_segments(value):
        non_file_uris = [match.span() for match in _NON_FILE_URI.finditer(canonical)]
        protected_suffixes: list[tuple[int, int]] = []
        for delimiter in (
            position
            for position in (canonical.find("?"), canonical.find("#"))
            if position >= 0
        ):
            suffix_end_match = re.search(r"\s", canonical[delimiter:])
            suffix_end = delimiter + suffix_end_match.start() if suffix_end_match else len(canonical)
            protected_suffixes.append((spans[delimiter][0], spans[suffix_end - 1][1]))
        for pattern in patterns:
            cursor = 0
            while cursor < len(canonical):
                match = pattern.search(canonical, cursor)
                if match is None:
                    break
                start, end = match.span()
                if start == end:
                    cursor = end + 1
                    continue
                if pattern is _POSIX_PATH and any(uri_start <= start < uri_end for uri_start, uri_end in non_file_uris):
                    cursor = end
                    continue
                source_start = spans[start][0]
                end = _path_candidate_end(
                    canonical,
                    start,
                    end,
                    spans,
                    escaped_quote_is_external=pattern is _WINDOWS_PATH,
                )
                if end > start and not (
                    pattern is not _FILE_URI
                    and any(suffix_start <= source_start < suffix_end for suffix_start, suffix_end in protected_suffixes)
                ):
                    source_end = spans[end - 1][1]
                    candidates.append((source_start, source_end, canonical[start:end]))
                cursor = max(end, start + 1)
    selected: list[tuple[int, int, str]] = []
    for source_start, source_end, canonical in sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(source_start < end and source_end > start for start, end, _ in selected):
            continue
        selected.append((source_start, source_end, canonical))
    return selected


def _sanitize_text(value: str) -> tuple[str, int]:
    matches = _detected_paths(value)
    for start, end, canonical in reversed(matches):
        value = value[:start] + _path_token(canonical) + value[end:]
    return value, len(matches)


def _omit_encoded_originals(value: Any) -> tuple[Any, bool]:
    """Remove recoverable original-byte payloads from public JSON presentation."""
    changed = False

    def scrub(item: Any) -> Any:
        nonlocal changed
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                if key in {"stdout_base64", "stderr_base64"}:
                    changed = True
                    continue
                result[key] = scrub(child)
            return result
        if isinstance(item, list):
            return [scrub(child) for child in item]
        return item

    return scrub(value), changed


def _omit_product_streams(value: Any) -> tuple[Any, bool]:
    """Omit nested product stream text before a second JSON layer can leak paths."""
    changed = False

    def scrub(item: Any, in_commands: bool = False) -> Any:
        nonlocal changed
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                if in_commands and key in {"stdout", "stderr"}:
                    changed = True
                    result[key] = f"<omitted-product-{key}>"
                    continue
                child_context = in_commands or key == "commands"
                result[key] = scrub(child, child_context)
            return result
        if isinstance(item, list):
            return [scrub(child, in_commands) for child in item]
        return item

    return scrub(value), changed


def _sanitize_json(value: Any) -> tuple[Any, int]:
    """Sanitize JSON values without corrupting string quoting or escapes."""
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        count = 0
        for key, child in value.items():
            clean_key, key_count = _sanitize_json(key) if isinstance(key, str) else (key, 0)
            clean_child, child_count = _sanitize_json(child)
            result[clean_key] = clean_child
            count += key_count + child_count
        return result, count
    if isinstance(value, list):
        result = []
        count = 0
        for child in value:
            clean_child, child_count = _sanitize_json(child)
            result.append(clean_child)
            count += child_count
        return result, count
    if isinstance(value, str):
        clean, count = _sanitize_text(value)
        return clean, count
    return value, 0


def _validate_nested_product_streams(value: Any, label: str) -> None:
    """Reject invalid original product bytes before their encoded fields are omitted."""
    if isinstance(value, dict):
        for stream in ("stdout", "stderr"):
            encoded_key = f"{stream}_base64"
            valid_key = f"{stream}_valid_utf8"
            error_key = f"{stream}_decode_error"
            if encoded_key in value:
                encoded = value[encoded_key]
                if not isinstance(encoded, str):
                    raise ValueError(f"{label}.{encoded_key} must be base64 text")
                try:
                    original = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError) as error:
                    raise ValueError(f"{label}.{encoded_key} is invalid base64") from error
                claimed_hash = value.get(f"{stream}_sha256")
                if not isinstance(claimed_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", claimed_hash):
                    raise ValueError(f"{label}.{stream}_sha256 is missing or invalid")
                if claimed_hash != sha256_bytes(original):
                    raise ValueError(f"{label}.{stream}_sha256 does not match nested product bytes")
                try:
                    original.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ValueError(f"{label}.{stream} contains invalid UTF-8 product bytes") from error
                if valid_key in value and value[valid_key] is not True:
                    raise ValueError(f"{label}.{valid_key} disagrees with nested product bytes")
                if error_key in value and value[error_key] is not None:
                    raise ValueError(f"{label}.{error_key} disagrees with nested product bytes")
            elif value.get(valid_key) is False:
                raise ValueError(f"{label}.{stream} is marked invalid UTF-8")
        for key, child in value.items():
            _validate_nested_product_streams(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_nested_product_streams(child, f"{label}[{index}]")


def _reject_fixture_markers(value: Any, label: str) -> None:
    """Reject fixture identity markers before product evidence is omitted."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "fixture":
                raise ValueError(f"{label}.fixture marker is not valid candidate evidence")
            _reject_fixture_markers(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_fixture_markers(child, f"{label}[{index}]")


def sanitize(value: Any) -> Any:
    """Redact exact and embedded absolute paths while retaining identity hashes."""
    if isinstance(value, dict):
        return {sanitize(key) if isinstance(key, str) else key: sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if not isinstance(value, str):
        return value
    return _sanitize_text(value)[0]


def has_absolute_path(value: Any) -> bool:
    if isinstance(value, dict):
        return any(has_absolute_path(key) or has_absolute_path(item) for key, item in value.items())
    if isinstance(value, list):
        return any(has_absolute_path(item) for item in value)
    if not isinstance(value, str):
        return False
    return bool(_detected_paths(value))


def _regular_file(path: Path, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symlink: {path}")
    if not path.is_file():
        raise ValueError(f"{label} is missing or not a regular file: {path}")


def _source_files(run_dir: Path) -> list[dict[str, str]]:
    files = []
    for path in sorted(run_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"run contains symlinked input: {path}")
        if path.is_file():
            files.append({"path": str(path.relative_to(run_dir)), "sha256": sha256_file(path)})
    return files


def _local_raw_path(run_dir: Path, relative: Any, case_id: str, stream: str) -> Path:
    if not isinstance(relative, str) or not relative or not _safe_relative(relative):
        raise ValueError(f"{case_id} {stream}_path must be a relative path")
    relative_path = Path(relative)
    if relative_path.parts[0] != "raw":
        raise ValueError(f"{case_id} {stream}_path must address local raw evidence")
    candidate = run_dir / relative_path
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{case_id} {stream} input is missing: {relative}") from error
    if run_dir not in resolved.parents:
        raise ValueError(f"{case_id} {stream}_path escapes the run directory")
    current = run_dir
    for part in relative_path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{case_id} {stream} input must not be a symlink: {relative}")
    _regular_file(candidate, f"{case_id} {stream} input")
    return candidate


def _safe_relative(value: str) -> bool:
    path = Path(value)
    return not _is_absolute(value) and not any(part in {"", ".", ".."} for part in path.parts) and "\\" not in value


def _public_stream(
    run_dir: Path,
    case_id: str,
    stream: str,
    relative: Any,
    stored_hash: Any,
    *,
    candidate_dependent: bool,
) -> dict[str, Any]:
    path = _local_raw_path(run_dir, relative, case_id, stream)
    if not isinstance(stored_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", stored_hash):
        raise ValueError(f"{case_id} {stream}_sha256 is invalid")
    original = path.read_bytes()
    original_hash = sha256_bytes(original)
    if original_hash != stored_hash:
        raise ValueError(f"{case_id} {stream} hash does not match its result record")
    try:
        decoded = original.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"{case_id} {stream} is not valid UTF-8") from error
    product_streams_omitted = False
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError:
        sanitized, redaction_count = _sanitize_text(decoded)
        encoded_originals_omitted = False
        if "stdout_base64" in decoded or "stderr_base64" in decoded:
            sanitized = "<redacted-malformed-json>"
            redaction_count += 1
    else:
        if candidate_dependent:
            _reject_fixture_markers(payload, f"{case_id} {stream}")
        _validate_nested_product_streams(payload, f"{case_id} {stream}")
        payload, product_streams_omitted = _omit_product_streams(payload)
        payload, encoded_originals_omitted = _omit_encoded_originals(payload)
        payload, redaction_count = _sanitize_json(payload)
        sanitized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        "content": sanitized,
        "encoding": "utf-8",
        "original_sha256": original_hash,
        "sanitized_sha256": sha256_bytes(sanitized.encode("utf-8")),
        "source_local_raw": {"path": relative, "sha256": original_hash, "result_sha256": stored_hash},
        "redaction": {
            "algorithm": "absolute-path-v1",
            "applied": redaction_count > 0,
            "replacements": redaction_count,
            "scans_for_arbitrary_secrets": False,
            "encoded_originals_omitted": encoded_originals_omitted,
            "product_streams_omitted": product_streams_omitted,
        },
    }


def _case_public_evidence(run_dir: Path, case: dict[str, Any], *, candidate_dependent: bool) -> dict[str, Any]:
    if not isinstance(case, dict):
        raise ValueError("case result must be an object")
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not isinstance(case.get("raw"), dict):
        raise ValueError("case result must contain case_id and raw")
    raw = case["raw"]
    return {
        "stdout": _public_stream(
            run_dir, case_id, "stdout", raw.get("stdout_path"), raw.get("stdout_sha256"),
            candidate_dependent=candidate_dependent,
        ),
        "stderr": _public_stream(
            run_dir, case_id, "stderr", raw.get("stderr_path"), raw.get("stderr_sha256"),
            candidate_dependent=candidate_dependent,
        ),
    }


def export_run(run_dir: Path, output: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    output = output.resolve()
    if not run_dir.is_dir():
        raise ValueError(f"run directory does not exist: {run_dir}")
    if output == run_dir or run_dir in output.parents:
        raise ValueError("public output must not be inside the local run directory")

    environment_path = run_dir / "environment.json"
    summary_path = run_dir / "summary.json"
    _regular_file(environment_path, "environment.json")
    _regular_file(summary_path, "summary.json")
    cases_dir = run_dir / "cases"
    if cases_dir.is_symlink() or not cases_dir.is_dir():
        raise ValueError("run must contain a non-symlink cases directory")
    case_paths = sorted(cases_dir.glob("*.json"))
    if any(path.is_symlink() for path in case_paths):
        raise ValueError("case result must not be a symlink")
    cases = {path.stem: load_json(path) for path in case_paths}
    summary = load_json(summary_path)
    if isinstance(summary, dict) and "coverage" not in summary:
        summary["coverage"] = {
            "supported_exercised_case_ids": sorted(
                case_id for case_id, case in cases.items()
                if isinstance(case, dict)
                and case.get("coverage", {"support": "supported", "status": "exercised"})
                == {"support": "supported", "status": "exercised"}
            ),
            "unsupported_unexercised_case_ids": sorted(
                case_id for case_id, case in cases.items()
                if isinstance(case, dict)
                and case.get("coverage", {"support": "supported", "status": "exercised"})
                != {"support": "supported", "status": "exercised"}
            ),
        }
    candidate_dependent = isinstance(summary, dict) and summary.get("candidate_dependent") is True
    environment_source = load_json(environment_path)
    if candidate_dependent and (
        not isinstance(environment_source, dict)
        or not isinstance(environment_source.get("binary"), str)
        or "fixture" in environment_source["binary"].lower()
    ):
        raise ValueError("candidate environment contains committed fixture binary identity")
    evidence = {
        case_id: _case_public_evidence(run_dir, case, candidate_dependent=candidate_dependent)
        for case_id, case in cases.items()
    }
    source_files = _source_files(run_dir)
    source_run_id = summary.get("run_id") if isinstance(summary, dict) else None
    if not isinstance(source_run_id, str) or not source_run_id:
        raise ValueError("summary run_id is required")
    public = {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "local_raw_evidence": {
            "local_only": True,
            "contents_included": False,
            "files": [item for item in source_files if item["path"].startswith("raw/")],
        },
        "artifacts": {
            "environment": sanitize(environment_source),
            "summary": sanitize(summary),
            "cases": {
                case_id: {
                    **{
                        key: sanitize(value)
                        for key, value in {
                            **case,
                            "coverage": case.get(
                                "coverage", {"support": "supported", "status": "exercised"}
                            ),
                        }.items()
                        if key != "raw"
                    },
                    "local_raw": sanitize(case["raw"]),
                }
                for case_id, case in cases.items()
            },
        },
        "public_evidence": {
            "redaction_policy": {
                "algorithm": "absolute-path-v1",
                "scans_for_arbitrary_secrets": False,
            },
            "cases": evidence,
        },
    }
    if has_absolute_path(public):
        raise ValueError("public export contains an absolute path after sanitization")
    validate_public_export(public)
    write_json(output, public)
    return public


def _exact(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} has unexpected or missing fields")


def _known(value: Any, allowed: set[str], required: set[str], label: str) -> None:
    if not isinstance(value, dict) or not required <= set(value) or not set(value) <= allowed:
        raise ValueError(f"{label} has unexpected or missing fields")


class _SchemaMismatch(ValueError):
    pass


class _SchemaUnsupported(ValueError):
    pass


_SCHEMA_KEYWORDS = {
    "$schema", "$id", "$ref", "$defs", "title", "type", "const", "enum", "allOf", "if", "then", "else",
    "anyOf", "not", "additionalProperties", "required", "properties", "items", "minItems", "minLength",
    "pattern", "minimum", "maximum", "format",
}


def _schema_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/$defs/"):
        raise _SchemaUnsupported(f"unsupported schema reference: {reference}")
    current: Any = root.get("$defs", {})
    for component in reference[len("#/$defs/"):].split("/"):
        component = component.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or component not in current:
            raise ValueError(f"missing schema reference: {reference}")
        current = current[component]
    if not isinstance(current, dict):
        raise ValueError(f"schema reference is not an object: {reference}")
    return current


def _schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and (not isinstance(value, float) or math.isfinite(value)))
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise _SchemaUnsupported(f"unsupported schema type: {expected}")


def _schema_validate(value: Any, schema: dict[str, Any], root: dict[str, Any], label: str) -> None:
    unknown = set(schema) - _SCHEMA_KEYWORDS
    if unknown:
        raise _SchemaUnsupported(f"unsupported schema keywords at {label}: {sorted(unknown)}")
    if "$ref" in schema:
        _schema_validate(value, _schema_ref(root, schema["$ref"]), root, label)
        return
    if "allOf" in schema:
        for index, child in enumerate(schema["allOf"]):
            _schema_validate(value, child, root, f"{label}.allOf[{index}]")
    if "if" in schema:
        try:
            _schema_validate(value, schema["if"], root, f"{label}.if")
        except _SchemaMismatch:
            if "else" in schema:
                _schema_validate(value, schema["else"], root, f"{label}.else")
        else:
            if "then" in schema:
                _schema_validate(value, schema["then"], root, f"{label}.then")
    if "anyOf" in schema:
        matches = 0
        for index, child in enumerate(schema["anyOf"]):
            try:
                _schema_validate(value, child, root, f"{label}.anyOf[{index}]")
            except _SchemaMismatch:
                continue
            matches += 1
        if not matches:
            raise _SchemaMismatch(f"{label} does not match anyOf")
    if "not" in schema:
        try:
            _schema_validate(value, schema["not"], root, f"{label}.not")
        except _SchemaMismatch:
            pass
        else:
            raise _SchemaMismatch(f"{label} matches forbidden schema")
    if "const" in schema and value != schema["const"]:
        raise _SchemaMismatch(f"{label} does not equal const")
    if "enum" in schema and value not in schema["enum"]:
        raise _SchemaMismatch(f"{label} is not in enum")
    if "type" in schema:
        expected_types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_schema_type_matches(value, expected) for expected in expected_types):
            raise _SchemaMismatch(f"{label} has invalid type")
    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise _SchemaMismatch(f"{label} is missing required field {key}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise _SchemaUnsupported(f"{label}.properties is not an object")
        for key, child in value.items():
            if key in properties:
                _schema_validate(child, properties[key], root, f"{label}.{key}")
            elif schema.get("additionalProperties") is False:
                raise _SchemaMismatch(f"{label} has unknown field {key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                _schema_validate(child, schema["additionalProperties"], root, f"{label}.{key}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise _SchemaMismatch(f"{label} has too few items")
        if isinstance(schema.get("items"), dict):
            for index, child in enumerate(value):
                _schema_validate(child, schema["items"], root, f"{label}[{index}]")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise _SchemaMismatch(f"{label} is too short")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise _SchemaMismatch(f"{label} does not match pattern")
        if schema.get("format") == "uri":
            parsed = urllib.parse.urlparse(value)
            if not parsed.scheme or not parsed.netloc:
                raise _SchemaMismatch(f"{label} is not a URI")
        elif "format" in schema:
            raise _SchemaUnsupported(f"unsupported schema format: {schema['format']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise _SchemaMismatch(f"{label} is not finite")
        if "minimum" in schema and value < schema["minimum"]:
            raise _SchemaMismatch(f"{label} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise _SchemaMismatch(f"{label} is above maximum")


def validate_schema_document(value: Any, schema: dict[str, Any]) -> None:
    """Validate a document against the committed schema with no third-party dependency."""
    if not isinstance(schema, dict):
        raise ValueError("schema document must be an object")
    try:
        _schema_validate(value, schema, schema, "document")
    except _SchemaMismatch as error:
        raise ValueError(str(error)) from error


def _hash(value: Any, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError(f"{label} must be a SHA-256 hex string")


def _string(value: Any, label: str, *, nonempty: bool = False) -> None:
    if not isinstance(value, str) or (nonempty and not value):
        raise ValueError(f"{label} must be a{' non-empty' if nonempty else ''} string")


def _boolean(value: Any, label: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")


def _integer(value: Any, label: str, *, minimum: int | None = None) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or (minimum is not None and value < minimum):
        raise ValueError(f"{label} must be an integer")


def _number(value: Any, label: str, *, minimum: float | None = None) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or (minimum is not None and value < minimum):
        raise ValueError(f"{label} must be a number")


def _coverage(value: Any, label: str) -> None:
    _exact(value, {"support", "status"}, label)
    if value["support"] not in {"supported", "unsupported"}:
        raise ValueError(f"{label}.support is invalid")
    if value["status"] not in {"exercised", "unexercised"}:
        raise ValueError(f"{label}.status is invalid")
    if (value["support"], value["status"]) not in {
        ("supported", "exercised"),
        ("unsupported", "unexercised"),
    }:
        raise ValueError(f"{label} must pair support and status consistently")


def _expected(value: Any, label: str) -> None:
    _exact(value, {"outcome", "reason", "exit_code", "holytail"}, label)
    if value["outcome"] not in {"READY", "REFUSED", "BLOCKED"}:
        raise ValueError(f"{label}.outcome is invalid")
    _string(value["reason"], f"{label}.reason")
    _integer(value["exit_code"], f"{label}.exit_code")
    if value["holytail"] not in {"LOSS", "PRESERVED", "NOT_APPLICABLE"}:
        raise ValueError(f"{label}.holytail is invalid")


def _observed(value: Any, label: str) -> None:
    _exact(value, {"outcome", "reason", "exit_code", "timed_out", "json_parse_error", "holytail", "duration_ms"}, label)
    if value["outcome"] is not None:
        if value["outcome"] not in {"READY", "REFUSED", "BLOCKED"}:
            raise ValueError(f"{label}.outcome is invalid")
    if value["reason"] is not None:
        _string(value["reason"], f"{label}.reason")
    if value["exit_code"] is not None:
        _integer(value["exit_code"], f"{label}.exit_code")
    _boolean(value["timed_out"], f"{label}.timed_out")
    if value["json_parse_error"] is not None:
        _string(value["json_parse_error"], f"{label}.json_parse_error")
    if value["holytail"] is not None:
        if value["holytail"] not in {"LOSS", "PRESERVED", "NOT_APPLICABLE"}:
            raise ValueError(f"{label}.holytail is invalid")
    _number(value["duration_ms"], f"{label}.duration_ms", minimum=0)


def _measurements(value: Any, label: str) -> None:
    _exact(value, {"holytail"}, label)
    holytail = value["holytail"]
    if not isinstance(holytail, dict):
        raise ValueError(f"{label}.holytail must be an object")
    status = holytail.get("status")
    if status == "measured":
        _exact(holytail, {"status", "source", "value"}, f"{label}.holytail")
        if holytail["source"] != "product_observation":
            raise ValueError(f"{label}.holytail.source is invalid")
        if holytail["value"] not in {"LOSS", "PRESERVED"}:
            raise ValueError(f"{label}.holytail.value is invalid")
    elif status == "unavailable":
        _exact(holytail, {"status", "reason"}, f"{label}.holytail")
        _string(holytail["reason"], f"{label}.holytail.reason", nonempty=True)
    else:
        raise ValueError(f"{label}.holytail.status is invalid")


def _classification(value: Any, label: str) -> None:
    _exact(value, {"outcome_match", "reason_match", "exit_code_match", "holytail_match"}, label)
    for key in ("outcome_match", "reason_match", "exit_code_match"):
        _boolean(value[key], f"{label}.{key}")
    if value["holytail_match"] is not None:
        _boolean(value["holytail_match"], f"{label}.holytail_match")


def _derived_classification(case: dict[str, Any]) -> dict[str, Any]:
    expected = case["expected"]
    observed = case["observed"]
    measured_holytail = case.get("measurements", {}).get("holytail", {})
    if expected["holytail"] == "NOT_APPLICABLE":
        holytail_match = None
    elif measured_holytail.get("status") == "measured":
        holytail_match = measured_holytail.get("value") == expected["holytail"]
    else:
        holytail_match = (
            None
            if "measurements" in case
            else observed["holytail"] == expected["holytail"]
        )
    return {
        "outcome_match": observed["outcome"] == expected["outcome"],
        "reason_match": observed["reason"] == expected["reason"],
        "exit_code_match": observed["exit_code"] == expected["exit_code"],
        "holytail_match": holytail_match,
    }


def _raw(value: Any, label: str) -> None:
    _exact(value, {"stdout_path", "stderr_path", "stdout_sha256", "stderr_sha256"}, label)
    for stream in ("stdout", "stderr"):
        path = value[f"{stream}_path"]
        if not isinstance(path, str) or not _safe_relative(path) or not path.startswith("raw/"):
            raise ValueError(f"{label}.{stream}_path is invalid")
        _hash(value[f"{stream}_sha256"], f"{label}.{stream}_sha256")


def _ratio(value: Any, label: str) -> None:
    _known(value, {"numerator", "denominator", "percentage", "status", "unavailable_reason"}, {"numerator", "denominator", "percentage", "status"}, label)
    _integer(value["numerator"], f"{label}.numerator", minimum=0)
    _integer(value["denominator"], f"{label}.denominator", minimum=0)
    if value["numerator"] > value["denominator"]:
        raise ValueError(f"{label} numerator exceeds denominator")
    if value["status"] not in {"computed", "not_computable"}:
        raise ValueError(f"{label}.status is invalid")
    if value["denominator"] == 0:
        if value["status"] != "not_computable" or value["percentage"] is not None:
            raise ValueError(f"{label} zero denominator must be not_computable")
        if "unavailable_reason" in value:
            _string(value["unavailable_reason"], f"{label}.unavailable_reason", nonempty=True)
    else:
        if "unavailable_reason" in value:
            raise ValueError(f"{label} computed ratio must not include unavailable_reason")
        expected_percentage = round(100 * value["numerator"] / value["denominator"], 4)
        if value["status"] != "computed" or not isinstance(value["percentage"], (int, float)) or isinstance(value["percentage"], bool) or not 0 <= value["percentage"] <= 100 or value["percentage"] != expected_percentage:
            raise ValueError(f"{label} computed ratio is invalid")


def _summary_coverage(value: Any, case_ids: set[str]) -> None:
    _exact(value, {"supported_exercised_case_ids", "unsupported_unexercised_case_ids"}, "summary.coverage")
    for key in value:
        if not isinstance(value[key], list) or not all(isinstance(item, str) for item in value[key]):
            raise ValueError(f"summary.coverage.{key} must be a string array")
        if len(set(value[key])) != len(value[key]) or not set(value[key]) <= case_ids:
            raise ValueError(f"summary.coverage.{key} has invalid case IDs")
    if set(value["supported_exercised_case_ids"]) & set(value["unsupported_unexercised_case_ids"]):
        raise ValueError("summary.coverage case sets overlap")
    if set(value["supported_exercised_case_ids"]) | set(value["unsupported_unexercised_case_ids"] ) != case_ids:
        raise ValueError("summary.coverage does not cover every case")


def _validate_public_protocol_shape(public: dict[str, Any], candidate_dependent: bool) -> None:
    """Keep parsed fixture/candidate envelopes and parse state bound."""
    cases = public["public_evidence"]["cases"]
    artifacts = public["artifacts"]["cases"]
    for case_id, streams in cases.items():
        content = streams["stdout"]["content"]
        artifact = artifacts[case_id]
        observed = artifact["observed"]
        parse_error = observed["json_parse_error"]
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            if parse_error is None:
                raise ValueError(f"{case_id} stdout is malformed without a recorded parse error")
            if any(observed[key] is not None for key in ("outcome", "reason", "holytail")):
                raise ValueError(f"{case_id} malformed stdout retains scored result fields")
            continue
        if parse_error is not None:
            raise ValueError(f"{case_id} stdout is valid JSON despite a recorded parse error")
        if not isinstance(payload, dict):
            raise ValueError(f"{case_id} stdout envelope must be a JSON object")
        if candidate_dependent:
            if "fixture" in payload:
                raise ValueError(f"candidate evidence {case_id} has fixture-only stdout shape")
            if (
                not isinstance(payload.get("adapter_version"), str)
                or not payload["adapter_version"]
                or not isinstance(payload.get("result"), dict)
                or not isinstance(payload.get("product_evidence"), dict)
                or not isinstance(payload.get("coverage"), dict)
            ):
                raise ValueError(f"candidate evidence {case_id} has no candidate adapter envelope")
            _exact(payload["result"], {"outcome", "reason", "holytail"}, f"{case_id} candidate result")
            if payload["result"] != {
                "outcome": observed["outcome"],
                "reason": observed["reason"],
                "holytail": observed["holytail"],
            }:
                raise ValueError(f"candidate evidence {case_id} result disagrees with observed fields")
            if "measurements" in payload:
                _measurements(payload["measurements"], f"{case_id} candidate measurements")
                if payload["measurements"] != artifact.get("measurements"):
                    raise ValueError(f"candidate evidence {case_id} measurements disagree with observed fields")
            if payload["coverage"] != artifact["coverage"]:
                raise ValueError(f"candidate evidence {case_id} coverage disagrees with observed fields")
        else:
            allowed = {"outcome", "reason", "holytail", "fixture", "checks"}
            required = {"outcome", "reason", "holytail", "fixture"}
            if payload.get("fixture") is not True or not required <= set(payload) or not set(payload) <= allowed:
                raise ValueError(f"fixture evidence {case_id} has candidate adapter stdout shape")
            if {
                "outcome": payload["outcome"],
                "reason": payload["reason"],
                "holytail": payload["holytail"],
            } != {
                "outcome": observed["outcome"],
                "reason": observed["reason"],
                "holytail": observed["holytail"],
            }:
                raise ValueError(f"fixture evidence {case_id} result disagrees with observed fields")


def _validate_stream(stream: Any, label: str) -> None:
    _exact(stream, {"content", "encoding", "original_sha256", "sanitized_sha256", "source_local_raw", "redaction"}, label)
    if not isinstance(stream["content"], str) or stream["encoding"] != "utf-8":
        raise ValueError(f"{label} must be UTF-8 text")
    _hash(stream["original_sha256"], f"{label}.original_sha256")
    _hash(stream["sanitized_sha256"], f"{label}.sanitized_sha256")
    if sha256_bytes(stream["content"].encode("utf-8")) != stream["sanitized_sha256"]:
        raise ValueError(f"{label}.sanitized_sha256 does not bind content")
    _exact(stream["source_local_raw"], {"path", "sha256", "result_sha256"}, f"{label}.source_local_raw")
    if not isinstance(stream["source_local_raw"]["path"], str) or not _safe_relative(stream["source_local_raw"]["path"]):
        raise ValueError(f"{label}.source_local_raw.path must be relative")
    _hash(stream["source_local_raw"]["sha256"], f"{label}.source_local_raw.sha256")
    _hash(stream["source_local_raw"]["result_sha256"], f"{label}.source_local_raw.result_sha256")
    if stream["source_local_raw"]["sha256"] != stream["original_sha256"] or stream["source_local_raw"]["result_sha256"] != stream["original_sha256"]:
        raise ValueError(f"{label} source hashes are not bound")
    _exact(stream["redaction"], {"algorithm", "applied", "replacements", "scans_for_arbitrary_secrets", "encoded_originals_omitted", "product_streams_omitted"}, f"{label}.redaction")
    _boolean(stream["redaction"]["scans_for_arbitrary_secrets"], f"{label}.redaction.scans_for_arbitrary_secrets")
    if stream["redaction"]["algorithm"] != "absolute-path-v1" or not isinstance(stream["redaction"]["applied"], bool) or not isinstance(stream["redaction"]["replacements"], int) or isinstance(stream["redaction"]["replacements"], bool) or stream["redaction"]["replacements"] < 0 or stream["redaction"]["applied"] != (stream["redaction"]["replacements"] > 0) or stream["redaction"]["scans_for_arbitrary_secrets"] is not False or not isinstance(stream["redaction"]["encoded_originals_omitted"], bool) or not isinstance(stream["redaction"]["product_streams_omitted"], bool):
        raise ValueError(f"{label}.redaction is invalid")


def validate_public_export(public: Any) -> None:
    """Validate the closed v2 public-export shape without third-party packages."""
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "public-export.schema.json"
    validate_schema_document(public, load_json(schema_path))
    _exact(public, {"schema_version", "source_run_id", "local_raw_evidence", "artifacts", "public_evidence"}, "public export")
    if public["schema_version"] != PUBLIC_SCHEMA_VERSION:
        raise ValueError("public export identity is invalid")
    _string(public["source_run_id"], "source_run_id", nonempty=True)
    local = public["local_raw_evidence"]
    _exact(local, {"local_only", "contents_included", "files"}, "local_raw_evidence")
    if local["local_only"] is not True or local["contents_included"] is not False or not isinstance(local["files"], list):
        raise ValueError("local_raw_evidence boundary is invalid")
    raw_files: dict[str, str] = {}
    for item in local["files"]:
        _exact(item, {"path", "sha256"}, "local_raw_evidence file")
        if not isinstance(item["path"], str) or not item["path"].startswith("raw/") or not _safe_relative(item["path"]):
            raise ValueError("local raw evidence path is invalid")
        _hash(item["sha256"], "local_raw_evidence file hash")
        if item["path"] in raw_files:
            raise ValueError("local raw evidence paths must be unique")
        raw_files[item["path"]] = item["sha256"]
    artifacts = public["artifacts"]
    _exact(artifacts, {"environment", "summary", "cases"}, "artifacts")
    if not isinstance(artifacts["environment"], dict) or not isinstance(artifacts["summary"], dict) or not isinstance(artifacts["cases"], dict):
        raise ValueError("artifacts must be objects")
    environment = artifacts["environment"]
    _known(environment, {"schema_version", "started_at", "python", "platform", "cwd", "locale", "child_environment", "binary", "binary_sha256", "adapter_manifest", "adapter_manifest_sha256", "adapter_implementation", "adapter_implementation_sha256", "product_commit_sha", "evaluator_commit_sha"}, {"schema_version", "python", "platform", "cwd", "locale", "binary", "binary_sha256"}, "environment")
    if environment["schema_version"] != "environment-v1":
        raise ValueError("environment schema version is invalid")
    for key in ("python", "platform", "cwd", "locale"):
        _string(environment[key], f"environment.{key}")
    if "started_at" in environment:
        _string(environment["started_at"], "environment.started_at", nonempty=True)
    if "child_environment" in environment:
        child = environment["child_environment"]
        _exact(child, {"policy", "allowlist", "os_sandbox"}, "environment.child_environment")
        if child["policy"] != "minimal-allowlist-v1" or not isinstance(child["allowlist"], list) or not all(isinstance(item, str) for item in child["allowlist"]) or child["os_sandbox"] is not False:
            raise ValueError("environment.child_environment is invalid")
    for key in ("binary", "adapter_manifest", "adapter_implementation"):
        if key in environment:
            _string(environment[key], f"environment.{key}", nonempty=True)
    for key in ("binary_sha256", "adapter_manifest_sha256", "adapter_implementation_sha256"):
        if key in environment:
            _hash(environment[key], f"environment.{key}")
    summary = artifacts["summary"]
    _known(summary, {"schema_version", "run_id", "case_count", "candidate_dependent", "coverage", "aggregated", "environment_file", "case_result_directory"}, {"schema_version", "run_id", "case_count", "candidate_dependent", "coverage", "aggregated", "environment_file", "case_result_directory"}, "summary")
    if summary["schema_version"] != "summary-v1":
        raise ValueError("summary schema version is invalid")
    _string(summary["run_id"], "summary.run_id", nonempty=True)
    if public["source_run_id"] != summary["run_id"]:
        raise ValueError("source_run_id disagrees with summary.run_id")
    _integer(summary["case_count"], "summary.case_count", minimum=0)
    _boolean(summary["candidate_dependent"], "summary.candidate_dependent")
    _string(summary["environment_file"], "summary.environment_file", nonempty=True)
    _string(summary["case_result_directory"], "summary.case_result_directory", nonempty=True)
    case_ids = set(artifacts["cases"])
    if summary["case_count"] != len(case_ids):
        raise ValueError("summary.case_count does not match case results")
    _summary_coverage(summary["coverage"], case_ids)
    _exact(summary["aggregated"], {"false_acceptance_case_ids", "metrics", "mismatch_case_ids"}, "summary.aggregated")
    for key in ("false_acceptance_case_ids", "mismatch_case_ids"):
        if not isinstance(summary["aggregated"][key], list) or not all(isinstance(item, str) for item in summary["aggregated"][key]) or len(set(summary["aggregated"][key])) != len(summary["aggregated"][key]) or not set(summary["aggregated"][key]) <= case_ids:
            raise ValueError(f"summary.aggregated.{key} is invalid")
    metrics = summary["aggregated"]["metrics"]
    metric_names = {"false_acceptance", "false_exit_rejection", "false_refusal", "holytail_false_alarms", "holytail_preservation_recall", "outcome_classification", "reason_classification", "valid_exit_acceptance"}
    _exact(metrics, metric_names, "summary.aggregated.metrics")
    for name in metric_names:
        _ratio(metrics[name], f"summary.aggregated.metrics.{name}")
    candidate_dependent = artifacts["summary"]["candidate_dependent"]
    candidate_fields = {"adapter_manifest", "adapter_manifest_sha256", "adapter_implementation", "adapter_implementation_sha256", "product_commit_sha", "evaluator_commit_sha"}
    present_candidate = candidate_fields & set(environment)
    if candidate_dependent:
        if present_candidate != candidate_fields:
            raise ValueError("candidate environment must contain manifest, implementation, and both commit identities")
        for field in ("product_commit_sha", "evaluator_commit_sha"):
            if not _COMMIT_SHA.fullmatch(environment[field]):
                raise ValueError(f"environment.{field} is invalid")
    elif present_candidate:
        raise ValueError("fixture environment must not contain adapter or candidate identities")
    if not candidate_dependent and {"product_commit_sha", "evaluator_commit_sha"} & set(environment):
        raise ValueError("fixture environment must not contain product/evaluator commit identities")
    case_raw_files: dict[str, str] = {}
    for case_id, case in artifacts["cases"].items():
        if not isinstance(case_id, str) or not isinstance(case, dict) or "local_raw" not in case:
            raise ValueError("public case result is invalid")
        _known(case, {"schema_version", "run_id", "case_id", "expected", "observed", "classification", "measurements", "coverage", "command", "local_raw"}, {"schema_version", "run_id", "case_id", "expected", "observed", "classification", "coverage", "local_raw"}, f"public case {case_id}")
        if case["schema_version"] != "result-v1":
            raise ValueError(f"public case {case_id} schema version is invalid")
        _string(case["run_id"], f"public case {case_id}.run_id", nonempty=True)
        if case["run_id"] != public["source_run_id"] or case["run_id"] != summary["run_id"] or case["case_id"] != case_id:
            raise ValueError(f"public case {case_id} identity is not bound to the source run")
        _expected(case["expected"], f"public case {case_id}.expected")
        _observed(case["observed"], f"public case {case_id}.observed")
        if "measurements" in case:
            _measurements(case["measurements"], f"public case {case_id}.measurements")
        _classification(case["classification"], f"public case {case_id}.classification")
        if case["classification"] != _derived_classification(case):
            raise ValueError(f"public case {case_id}.classification is not derived from evidence")
        _coverage(case["coverage"], f"public case {case_id}.coverage")
        if not isinstance(case.get("command"), list) or not case["command"] or not all(isinstance(item, str) for item in case["command"]):
            raise ValueError(f"public case {case_id}.command is invalid")
        _raw(case["local_raw"], f"public case {case_id}.raw")
        for stream in ("stdout", "stderr"):
            path = case["local_raw"][f"{stream}_path"]
            digest = case["local_raw"][f"{stream}_sha256"]
            if path in case_raw_files and case_raw_files[path] != digest:
                raise ValueError(f"raw evidence path has conflicting hashes: {path}")
            case_raw_files[path] = digest
    if case_raw_files != raw_files:
        raise ValueError("local raw evidence files do not match case result evidence")
    evidence = public["public_evidence"]
    _exact(evidence, {"redaction_policy", "cases"}, "public_evidence")
    _exact(evidence["redaction_policy"], {"algorithm", "scans_for_arbitrary_secrets"}, "redaction_policy")
    _boolean(evidence["redaction_policy"]["scans_for_arbitrary_secrets"], "redaction_policy.scans_for_arbitrary_secrets")
    if evidence["redaction_policy"] != {"algorithm": "absolute-path-v1", "scans_for_arbitrary_secrets": False}:
        raise ValueError("redaction policy is invalid")
    if set(evidence["cases"]) != set(artifacts["cases"]):
        raise ValueError("public evidence cases do not match result cases")
    for case_id, streams in evidence["cases"].items():
        _exact(streams, {"stdout", "stderr"}, f"public_evidence.cases.{case_id}")
        _validate_stream(streams["stdout"], f"public_evidence.cases.{case_id}.stdout")
        _validate_stream(streams["stderr"], f"public_evidence.cases.{case_id}.stderr")
        for stream in ("stdout", "stderr"):
            metadata = artifacts["cases"][case_id]["local_raw"]
            source = streams[stream]["source_local_raw"]
            if source["path"] != metadata[f"{stream}_path"] or source["result_sha256"] != metadata[f"{stream}_sha256"]:
                raise ValueError(f"public evidence source binding is invalid for {case_id} {stream}")
    supported = set(summary["coverage"]["supported_exercised_case_ids"])
    unsupported = set(summary["coverage"]["unsupported_unexercised_case_ids"])
    for case_id, case in artifacts["cases"].items():
        expected = supported if case["coverage"]["support"] == "supported" else unsupported
        if case_id not in expected:
            raise ValueError(f"summary coverage disagrees with {case_id}")
    recomputed_aggregate = aggregate_results(list(artifacts["cases"].values()))
    if summary["aggregated"] != recomputed_aggregate:
        raise ValueError("summary aggregate is not derived from case evidence")
    _validate_public_protocol_shape(public, candidate_dependent)
    if has_absolute_path(public):
        raise ValueError("public export contains an absolute path")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path, dest="run_dir")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        public = export_run(args.run_dir, args.out)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    print(json.dumps({"output": "written", "source_run_id": public["source_run_id"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
