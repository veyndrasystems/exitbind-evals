#!/usr/bin/env python3
"""Focused interface and anti-cheating checks for the candidate adapter."""

from __future__ import annotations

import json
import hashlib
import re
import io
import tempfile
import unittest
import importlib.util
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "adapters/exitbind-v0.17.0.py"
MAPPING = ROOT / "adapters/exitbind-v0.17.0.json"
CASES = ROOT / "cases"


class CandidateAdapterTests(unittest.TestCase):
    def test_mapping_covers_exactly_all_seed_cases(self) -> None:
        mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
        case_ids = {json.loads(path.read_text(encoding="utf-8"))["id"] for path in CASES.glob("*.json")}
        commands = mapping["commands"]
        self.assertEqual(set(commands), case_ids)
        for case_id, argv in commands.items():
            self.assertIsInstance(argv, list)
            self.assertTrue(all(isinstance(item, str) for item in argv), case_id)
            self.assertIn("{binary}", argv, case_id)
            self.assertIn("{case_id}", argv, case_id)
            self.assertIn("{workdir}", argv, case_id)
            self.assertIn("{adapter}", argv, case_id)
            self.assertNotIn(str(Path.home()) + "/", " ".join(argv), case_id)

    def test_adapter_has_explicit_versioned_product_reason_map(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertIn('ADAPTER_VERSION = "exitbind-adapter-v1"', source)
        self.assertIn("PRODUCT_REASON_MAP_V1", source)
        self.assertNotIn(".upper()", source)

    def test_adapter_does_not_read_ground_truth_or_product_modules(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"ground[_-]?truth|expected[_-]?(outcome|reason|exit)", source, re.I))
        self.assertIsNone(re.search(r"(?:from|import)\s+(?:exitbind|src)(?:\.|\s)", source))
        self.assertNotRegex(source, r"open\([^\n]*jsonl|write_text\([^\n]*jsonl")

    def test_adapter_never_mutates_ledger_bytes_directly(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertNotRegex(source, r"(?:unlink|rename|replace|write_bytes)\([^\n]*ledger")
        self.assertIn('"run",\n            "start"', source)
        self.assertIn('"run",\n            "submit"', source)

    def test_stale_review_preserves_unexpected_acceptance_and_known_guard(self) -> None:
        spec = importlib.util.spec_from_file_location("candidate_adapter", ADAPTER)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        class Command:
            def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
                self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

        class FakeAdapter:
            def __init__(self, final: Command) -> None:
                self.final = final
                self.envelope_args = None

            def start(self, _command): pass
            def worker(self, _name): return "event"
            def observe(self, _event): pass
            def review(self, _name, _outcome): pass
            def rework(self): pass
            def accept(self, _name): return self.final

            def envelope(self, *args, **kwargs):
                self.envelope_args = (args, kwargs)
                return 0

        accepted = FakeAdapter(Command(0, '{"status":"accepted"}'))
        self.assertEqual(module.stale_review_evidence(accepted), 0)
        self.assertEqual(accepted.envelope_args[0][:2], ("READY", "ACCEPTED"))
        self.assertEqual(accepted.envelope_args[1]["coverage"], {"support": "supported", "status": "exercised"})

        guarded = FakeAdapter(Command(1, stderr="exitbind: agent 'lead' is not currently pending /var/tmp/private/review"))
        self.assertEqual(module.stale_review_evidence(guarded), 0)
        self.assertEqual(guarded.envelope_args[0][:2], ("BLOCKED", "ADAPTER_UNSUPPORTED"))
        self.assertEqual(guarded.envelope_args[1]["coverage"], {"support": "unsupported", "status": "unexercised"})
        self.assertNotIn("/var/tmp/private/review", guarded.envelope_args[1]["limitation"])

    def test_stale_check_preserves_guard_success_and_unexpected_error(self) -> None:
        spec = importlib.util.spec_from_file_location("candidate_adapter_stale_check", ADAPTER)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        class Command:
            def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
                self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

        class StaleAdapter:
            def __init__(self, final):
                self.observations = [Command(0), final]
                self.envelope_args = None
            def start(self, _command): pass
            def worker(self, _name): return "event"
            def observe(self, _event): return self.observations.pop(0)
            def review(self, _name, _outcome): pass
            def envelope(self, *args, **kwargs): self.envelope_args = (args, kwargs); return 0

        guard = StaleAdapter(Command(1, stderr=r"target is not a current worker completion \\u002fvar\\u002ftmp\\private\\check"))
        self.assertEqual(module.stale_evidence(guard, "check"), 0)
        self.assertEqual(guard.envelope_args[0][:2], ("BLOCKED", "ADAPTER_UNSUPPORTED"))
        self.assertEqual(guard.envelope_args[1]["coverage"], {"support": "unsupported", "status": "unexercised"})
        self.assertNotIn("private", guard.envelope_args[1]["limitation"])

        success = StaleAdapter(Command(0, stdout="unexpected success"))
        self.assertEqual(module.stale_evidence(success, "check"), 0)
        self.assertEqual(success.envelope_args[0][:2], ("READY", "ACCEPTED"))
        self.assertEqual(success.envelope_args[1]["coverage"], {"support": "unsupported", "status": "unexercised"})

        error = StaleAdapter(Command(1, stderr="unexpected product error"))
        self.assertEqual(module.stale_evidence(error, "check"), 0)
        self.assertEqual(error.envelope_args[0][:2], ("REFUSED", "CLI_ERROR"))
        self.assertEqual(error.envelope_args[1]["coverage"], {"support": "unsupported", "status": "unexercised"})

    def test_observation_missing_is_blocked_with_valid_control_shape(self) -> None:
        spec = importlib.util.spec_from_file_location("candidate_adapter_observation_mapping", ADAPTER)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        class Command:
            returncode = 1
            stdout = ""
            stderr = "observation_missing"

        class FakeAdapter:
            def __init__(self) -> None:
                self.envelope_args = None
            def start(self, _command): pass
            def worker(self): return "event"
            def report(self, _event): pass
            def review(self): pass
            def accept(self): return Command()
            def envelope(self, *args, **kwargs):
                self.envelope_args = (args, kwargs)
                return 0

        adapter = FakeAdapter()
        self.assertEqual(module.reported_checked(adapter), 0)
        self.assertEqual(adapter.envelope_args[0][:2], ("BLOCKED", "OBSERVATION_MISSING"))
        self.assertEqual(adapter.envelope_args[1]["exit_code"], 3)
        self.assertEqual(adapter.envelope_args[1]["coverage"], {"support": "supported", "status": "exercised"})

    def test_adapter_exception_is_unsupported_and_valid_envelope_remains_supported(self) -> None:
        spec = importlib.util.spec_from_file_location("candidate_adapter_error_coverage", ADAPTER)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        class FakeAdapter:
            def __init__(self, *_args) -> None:
                self.envelope_args = None
                instances.append(self)
            def envelope(self, *args, **kwargs):
                self.envelope_args = (args, kwargs)
                return 0

        instances = []
        module.Adapter = FakeAdapter
        original = module.supported_checked
        module.supported_checked = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("adapter failure"))
        try:
            self.assertEqual(module.main(["adapter", "/bin/false", "false-completion-001", "."]), 0)
        finally:
            module.supported_checked = original
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].envelope_args[0][:2], ("BLOCKED", "ADAPTER_ERROR"))
        self.assertEqual(instances[0].envelope_args[1]["coverage"], {"support": "unsupported", "status": "unexercised"})

    def test_bin_false_adapter_error_is_explicitly_unexercised(self) -> None:
        spec = importlib.util.spec_from_file_location("candidate_adapter_bin_false", ADAPTER)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = module.main(["adapter", "/bin/false", "false-completion-001", temporary])
        envelope = json.loads(output.getvalue())
        self.assertEqual(exit_code, 3)
        self.assertEqual(envelope["result"], {"outcome": "BLOCKED", "reason": "ADAPTER_ERROR", "holytail": "NOT_APPLICABLE"})
        self.assertEqual(envelope["coverage"], {"support": "unsupported", "status": "unexercised"})

    def test_command_hashes_raw_streams_before_presentation_decode(self) -> None:
        spec = importlib.util.spec_from_file_location("candidate_adapter_hash_order", ADAPTER)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        class Process:
            returncode = 0

            def __init__(self, stdout: bytes, stderr: bytes) -> None:
                self.stdout = stdout
                self.stderr = stderr

        for stdout, stderr, valid in ((b"ok\n", b"err\n", True), (b"\xff", b"\xfe", False)):
            with self.subTest(valid=valid):
                events: list[str] = []
                original_decode = module.decode_presentation

                def hash_bytes(value: bytes) -> str:
                    events.append(f"hash:{value!r}")
                    return hashlib.sha256(value).hexdigest()

                def decode(value: bytes) -> tuple[str, bool, str | None]:
                    events.append(f"decode:{value!r}")
                    return original_decode(value)

                with patch.object(module.subprocess, "run", return_value=Process(stdout, stderr)), patch.object(module, "sha256_bytes", side_effect=hash_bytes), patch.object(module, "decode_presentation", side_effect=decode):
                    command = module.Adapter("/bin/true", "case", Path(".")).cli()
                self.assertEqual(events[:2], [f"hash:{stdout!r}", f"hash:{stderr!r}"])
                self.assertEqual(events[2:], [f"decode:{stdout!r}", f"decode:{stderr!r}"])
                self.assertEqual(command.as_json()["stdout_sha256"], hashlib.sha256(stdout).hexdigest())
                self.assertEqual(command.as_json()["stderr_sha256"], hashlib.sha256(stderr).hexdigest())
                self.assertEqual(command.stdout_valid_utf8, valid)
                self.assertEqual(command.stderr_valid_utf8, valid)


if __name__ == "__main__":
    unittest.main()
