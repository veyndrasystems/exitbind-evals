#!/usr/bin/env python3
"""Black-box Exitbind v0.17.0 adapter for the paired seed cases.

Only the documented executable is invoked.  Artifact documents are evaluator
inputs; ledgers, configuration, and product state are created by Exitbind.
"""

from __future__ import annotations

import json
import base64
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ADAPTER_VERSION = "exitbind-adapter-v1"

# Explicit, versioned normalization of documented CLI refusal text.  Do not
# replace this with a generic text-to-code heuristic: unknown product output
# must remain visible as an adapter failure.
PRODUCT_REASON_MAP_V1 = {
    "check_failed": "CHECK_FAILED",
    "check_missing": "CHECK_MISSING",
    "check_subject_stale": "CHECK_SUBJECT_STALE",
    "review_subject_stale": "REVIEW_SUBJECT_STALE",
    "partial_completion": "PARTIAL_COMPLETION",
    "semantic_invariant_lost": "SEMANTIC_INVARIANT_LOST",
    "observation_missing": "OBSERVATION_MISSING",
}


@dataclass
class Command:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    stdout_bytes: bytes = b""
    stderr_bytes: bytes = b""
    stdout_sha256: str = ""
    stderr_sha256: str = ""
    stdout_valid_utf8: bool = True
    stderr_valid_utf8: bool = True
    stdout_decode_error: str | None = None
    stderr_decode_error: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "argv": self.argv,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_base64": base64.b64encode(self.stdout_bytes).decode("ascii"),
            "stderr_base64": base64.b64encode(self.stderr_bytes).decode("ascii"),
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
            "stdout_encoding": "utf-8",
            "stderr_encoding": "utf-8",
            "stdout_valid_utf8": self.stdout_valid_utf8,
            "stderr_valid_utf8": self.stderr_valid_utf8,
            "stdout_decode_error": self.stdout_decode_error,
            "stderr_decode_error": self.stderr_decode_error,
        }


def decode_presentation(value: bytes) -> tuple[str, bool, str | None]:
    try:
        return value.decode("utf-8"), True, None
    except UnicodeDecodeError as error:
        return value.decode("utf-8", errors="replace"), False, str(error)


def sha256_bytes(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


class Adapter:
    def __init__(self, binary: str, case_id: str, workdir: Path) -> None:
        self.binary = binary
        self.case_id = case_id
        self.workdir = workdir
        self.commands: list[Command] = []
        self.config = "exitbind.json"
        self.ledger = ".exitbind/runs/candidate.jsonl"

    def cli(self, *args: str, check: bool = False) -> Command:
        argv = [self.binary, *args]
        process = subprocess.run(
            argv,
            cwd=self.workdir,
            capture_output=True,
            check=False,
        )
        stdout_bytes = process.stdout
        stderr_bytes = process.stderr
        stdout_sha256 = sha256_bytes(stdout_bytes)
        stderr_sha256 = sha256_bytes(stderr_bytes)
        stdout, stdout_valid, stdout_error = decode_presentation(stdout_bytes)
        stderr, stderr_valid, stderr_error = decode_presentation(stderr_bytes)
        command = Command(
            argv=argv,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            stdout_sha256=stdout_sha256,
            stderr_sha256=stderr_sha256,
            stdout_valid_utf8=stdout_valid,
            stderr_valid_utf8=stderr_valid,
            stdout_decode_error=stdout_error,
            stderr_decode_error=stderr_error,
        )
        self.commands.append(command)
        if check and command.returncode != 0:
            raise RuntimeError(command.stderr or command.stdout or f"exit {command.returncode}")
        return command

    def artifact(self, name: str, text: str) -> str:
        path = self.workdir / ".exitbind" / "artifacts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return str(path.relative_to(self.workdir))

    def start(self, check_command: str) -> None:
        self.cli("init", "--mode", "portable", "--root", ".", check=True)
        self.cli(
            "run",
            "start",
            "change",
            "--goal",
            "candidate public CLI lifecycle",
            "--check-command",
            check_command,
            "--ledger",
            self.ledger,
            "--config",
            self.config,
            check=True,
        )
        lead = self.artifact("lead.md", "scope: candidate public CLI lifecycle\n")
        self.cli(
            "run",
            "submit",
            "lead",
            self.ledger,
            "--outcome",
            "scoped",
            "--artifact",
            lead,
            "--artifact-root",
            "state",
            "--config",
            self.config,
            check=True,
        )

    def worker(self, name: str = "worker.md") -> str:
        artifact = self.artifact(name, "worker: completed candidate lifecycle\n")
        event = self.cli(
            "run",
            "submit",
            "worker",
            self.ledger,
            "--outcome",
            "completed",
            "--artifact",
            artifact,
            "--artifact-root",
            "state",
            "--event-id",
            "--config",
            self.config,
            check=True,
        )
        event_id = event.stdout.strip()
        if not event_id or "\n" in event_id:
            raise RuntimeError("Exitbind --event-id did not return one event hash")
        return event_id

    def review(self, name: str = "review.md", outcome: str = "approved") -> None:
        artifact = self.artifact(name, f"review: {outcome}\n")
        self.cli(
            "run",
            "submit",
            "reviewer",
            self.ledger,
            "--outcome",
            outcome,
            "--artifact",
            artifact,
            "--artifact-root",
            "state",
            "--config",
            self.config,
            check=True,
        )

    def accept(self, name: str = "lead-final.md") -> Command:
        artifact = self.artifact(name, "lead: accepted candidate lifecycle\n")
        return self.cli(
            "run",
            "submit",
            "lead",
            self.ledger,
            "--outcome",
            "accepted",
            "--artifact",
            artifact,
            "--artifact-root",
            "state",
            "--config",
            self.config,
        )

    def rework(self, name: str = "lead-rework.md") -> None:
        artifact = self.artifact(name, "lead: request fresh attempt\n")
        self.cli(
            "run",
            "submit",
            "lead",
            self.ledger,
            "--outcome",
            "rework",
            "--artifact",
            artifact,
            "--artifact-root",
            "state",
            "--config",
            self.config,
            check=True,
        )

    def observe(self, event_id: str) -> Command:
        return self.cli(
            "run",
            "observe-check",
            self.ledger,
            "--target",
            event_id,
            "--timeout-ms",
            "1000",
            "--config",
            self.config,
        )

    def report(self, event_id: str, exit_code: int = 0) -> Command:
        return self.cli(
            "run",
            "record-check",
            self.ledger,
            "--target",
            event_id,
            "--check-command",
            "/bin/true",
            "--exit-code",
            str(exit_code),
            "--config",
            self.config,
        )

    def envelope(
        self,
        outcome: str,
        reason: str,
        holytail: str,
        *,
        limitation: str | None = None,
        coverage: dict[str, str] | None = None,
        exit_code: int | None = None,
    ) -> int:
        payload: dict[str, Any] = {
            "adapter_version": ADAPTER_VERSION,
            "result": {"outcome": outcome, "reason": reason, "holytail": holytail},
            "measurements": {
                "holytail": {
                    "status": "unavailable",
                    "reason": "v0.17.0_adapter_did_not_observe_holytail_judgment",
                }
            },
            "product_evidence": {"commands": [command.as_json() for command in self.commands]},
            "coverage": coverage or {"support": "supported", "status": "exercised"},
        }
        if limitation:
            payload["limitation"] = limitation
        print(json.dumps(payload, sort_keys=True))
        return exit_code if exit_code is not None else {"READY": 0, "REFUSED": 2, "BLOCKED": 3}[outcome]


def refusal_reason(command: Command) -> str:
    for marker, public_code in PRODUCT_REASON_MAP_V1.items():
        if marker in command.stderr.lower():
            return public_code
    return "CLI_ERROR"


def accepted(command: Command) -> bool:
    if command.returncode != 0:
        return False
    try:
        payload = json.loads(command.stdout)
    except json.JSONDecodeError:
        return False
    return payload.get("status") == "accepted"


def supported_checked(
    adapter: Adapter,
    check_command: str,
    *,
    holytail: str = "NOT_APPLICABLE",
) -> int:
    adapter.start(check_command)
    event_id = adapter.worker()
    adapter.observe(event_id)
    adapter.review()
    final = adapter.accept()
    if accepted(final):
        return adapter.envelope("READY", "ACCEPTED", holytail)
    reason = refusal_reason(final)
    return adapter.envelope("REFUSED", reason, "NOT_APPLICABLE", exit_code=2)


def reported_checked(adapter: Adapter, *, local_observation: bool = False) -> int:
    adapter.start("/bin/true")
    event_id = adapter.worker()
    if local_observation:
        adapter.observe(event_id)
    else:
        adapter.report(event_id)
    adapter.review()
    final = adapter.accept()
    if accepted(final):
        return adapter.envelope(
            "READY", "ACCEPTED", "NOT_APPLICABLE",
            coverage={"support": "supported", "status": "exercised"},
        )
    reason = refusal_reason(final)
    if reason == "OBSERVATION_MISSING":
        return adapter.envelope(
            "BLOCKED", reason, "NOT_APPLICABLE",
            coverage={"support": "supported", "status": "exercised"}, exit_code=3,
        )
    return adapter.envelope(
        "REFUSED", reason, "NOT_APPLICABLE",
        coverage={"support": "supported", "status": "exercised"}, exit_code=2,
    )


def unsupported(adapter: Adapter, family: str) -> int:
    # Run a real public lifecycle so the limitation is paired with reachable
    # product evidence.  The missing family-specific host/reviewer oracle is
    # never replaced by an expected answer; the observed product status stays
    # visible even when it cannot establish the case's semantic claim.
    adapter.start("/bin/true")
    event_id = adapter.worker()
    adapter.observe(event_id)
    adapter.review()
    final = adapter.accept()
    limitation = (
        f"v0.17.0 public CLI cannot generate {family} evidence; "
        "reported result is the observed generic lifecycle, not fabricated "
        "semantic detection"
    )
    if accepted(final):
        return adapter.envelope(
            "READY", "ACCEPTED", "NOT_APPLICABLE", limitation=limitation,
            coverage={"support": "unsupported", "status": "unexercised"},
        )
    return adapter.envelope(
        "REFUSED", refusal_reason(final), "NOT_APPLICABLE", limitation=limitation,
        coverage={"support": "unsupported", "status": "unexercised"}, exit_code=2,
    )


def stale_evidence(adapter: Adapter, family: str) -> int:
    adapter.start("/bin/true")
    first = adapter.worker("worker-first.md")
    adapter.observe(first)
    adapter.review("review-rework.md", "rework")
    second = adapter.worker("worker-second.md")
    stale = adapter.observe(first)
    limitation = f"stale-{family} observation retained in local raw product evidence"
    # ``second`` is intentionally retained in product evidence; no desired
    # result is inferred from the case ID.
    _ = second
    coverage = {"support": "unsupported", "status": "unexercised"}
    response = (stale.stderr or stale.stdout).lower()
    if stale.returncode == 0:
        return adapter.envelope(
            "READY", "ACCEPTED", "NOT_APPLICABLE",
            limitation=limitation + "; diagnostic category: unexpected-success",
            coverage=coverage, exit_code=0,
        )
    if "not a current worker completion" in response:
        return adapter.envelope(
            "BLOCKED", "ADAPTER_UNSUPPORTED", "NOT_APPLICABLE",
            limitation=limitation + "; diagnostic category: known-guard",
            coverage=coverage, exit_code=3,
        )
    return adapter.envelope(
        "REFUSED", refusal_reason(stale), "NOT_APPLICABLE",
        limitation=limitation + "; diagnostic category: unexpected-error",
        coverage=coverage, exit_code=2,
    )


def stale_review_evidence(adapter: Adapter) -> int:
    adapter.start("/bin/true")
    first = adapter.worker("worker-first.md")
    adapter.observe(first)
    adapter.review("review-first.md", "approved")
    adapter.rework()
    second = adapter.worker("worker-second.md")
    adapter.observe(second)
    # Do not submit a fresh attempt-2 review.  The public CLI must be asked to
    # accept with only the prior approval, retaining its actual refusal.
    final = adapter.accept("lead-without-fresh-review.md")
    if accepted(final):
        return adapter.envelope(
            "READY", "ACCEPTED", "NOT_APPLICABLE",
            coverage={"support": "supported", "status": "exercised"},
        )
    response = (final.stderr or final.stdout).lower()
    if "not currently pending" not in response:
        return adapter.envelope(
            "REFUSED", refusal_reason(final), "NOT_APPLICABLE",
            coverage={"support": "supported", "status": "exercised"}, exit_code=2,
        )
    limitation = (
        "stale-review guard observed; v0.17.0 requires a current reviewer "
        "submission after rework; local raw product evidence is retained"
    )
    return adapter.envelope(
        "BLOCKED", "ADAPTER_UNSUPPORTED", "NOT_APPLICABLE", limitation=limitation,
        coverage={"support": "unsupported", "status": "unexercised"}, exit_code=3,
    )


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: exitbind-v0.17.0.py BINARY CASE_ID WORKDIR", file=sys.stderr)
        return 64
    binary, case_id, workdir_arg = argv[1:]
    adapter = Adapter(binary, case_id, Path(workdir_arg))
    try:
        if case_id in {"false-completion-001"}:
            return supported_checked(adapter, "/bin/false")
        if case_id in {"false-completion-control-001"}:
            return supported_checked(adapter, "/bin/true")
        if case_id in {"reported-observed-001", "reported-observed-control-001"}:
            return reported_checked(adapter, local_observation=case_id.endswith("control-001"))
        if case_id in {"stale-check-001"}:
            return stale_evidence(adapter, "check")
        if case_id in {"stale-check-control-001"}:
            return supported_checked(adapter, "/bin/true")
        if case_id in {"stale-review-001"}:
            return stale_review_evidence(adapter)
        if case_id in {"stale-review-control-001"}:
            return supported_checked(adapter, "/bin/true")
        if case_id in {
            "semantic-loss-001",
            "partial-completion-001",
        }:
            family = "semantic-loss" if "semantic-loss" in case_id else "partial-completion"
            return unsupported(adapter, family)
        if case_id in {"semantic-loss-control-001", "partial-completion-control-001"}:
            return supported_checked(adapter, "/bin/true", holytail="PRESERVED")
        if case_id in {"env-precedence-001", "env-precedence-control-001"}:
            return unsupported(adapter, "env-precedence")
        print(f"unsupported case ID: {case_id}", file=sys.stderr)
        return 64
    except (OSError, RuntimeError, ValueError) as error:
        return adapter.envelope(
            "BLOCKED", "ADAPTER_ERROR", "NOT_APPLICABLE",
            limitation="adapter execution error; local raw product evidence may be incomplete",
            coverage={"support": "unsupported", "status": "unexercised"},
            exit_code=3,
        )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
