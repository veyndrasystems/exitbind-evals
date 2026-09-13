#!/usr/bin/env python3
"""Focused self-tests for child isolation and public export boundaries."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runners"))
from eval_common import CHILD_ENV_ALLOWLIST, aggregate_results, minimal_child_environment, resolve_adapter_path, sha256_bytes
from export_public import export_run, has_absolute_path, sanitize, validate_public_export, validate_schema_document
from run_case import command_for, run
import run_suite as run_suite_module
from run_suite import exact_commit_sha, main as run_suite_main


class HardeningTests(unittest.TestCase):
    def test_candidate_commit_sha_requires_exact_lowercase_hex(self) -> None:
        self.assertEqual(exact_commit_sha("a" * 40), "a" * 40)
        for value in ("a" * 39, "a" * 41, "A" * 40, "g" * 40):
            with self.assertRaisesRegex(Exception, "40 lowercase hexadecimal"):
                exact_commit_sha(value)

    def test_candidate_run_requires_both_commit_shas_and_fixture_rejects_them(self) -> None:
        runner = Path(__file__).resolve().parents[1] / "runners/run_suite.py"
        base = [sys.executable, str(runner), "--binary", "/bin/true", "--run-id", "run", "--adapter", "adapter.json", "--out", "out"]
        missing = subprocess.run(base + ["--product-commit-sha", "a" * 40], capture_output=True, text=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("both --product-commit-sha and --evaluator-commit-sha", missing.stderr)
        fixture = subprocess.run([sys.executable, str(runner), "--binary", "/bin/true", "--run-id", "run", "--fixture", "--out", "out", "--product-commit-sha", "a" * 40, "--evaluator-commit-sha", "b" * 40], capture_output=True, text=True)
        self.assertNotEqual(fixture.returncode, 0)
        self.assertIn("only valid with --adapter", fixture.stderr)

    def test_candidate_run_persists_both_commit_shas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter_script = root / "adapter.py"
            adapter_script.write_text("import json; print(json.dumps({'result': {'outcome': 'READY', 'reason': 'ACCEPTED'}}))", encoding="utf-8")
            adapter_path = root / "adapter.json"
            adapter_path.write_text(json.dumps({"adapter": "adapter.py", "commands": {"candidate": [sys.executable, "{adapter}"]}}), encoding="utf-8")
            cases = root / "cases"
            cases.mkdir()
            (cases / "candidate.json").write_text(json.dumps({"id": "candidate", "scenario": {"execution": {"mode": "candidate-adapter"}}, "ground_truth": {"expected_outcome": "READY", "expected_reason": "ACCEPTED", "expected_exit_code": 0, "holytail_expected": "NOT_APPLICABLE"}}), encoding="utf-8")
            output = root / "run"
            product_sha = "a" * 40
            evaluator_sha = "b" * 40
            with patch.object(sys, "argv", ["run_suite.py", "--binary", "/bin/true", "--run-id", "candidate-run", "--adapter", str(adapter_path), "--product-commit-sha", product_sha, "--evaluator-commit-sha", evaluator_sha, "--cases-dir", str(cases), "--out", str(output)]):
                self.assertEqual(run_suite_main(), 0)
            environment = json.loads((output / "environment.json").read_text())
            self.assertEqual(environment["product_commit_sha"], product_sha)
            self.assertEqual(environment["evaluator_commit_sha"], evaluator_sha)
            self.assertEqual(environment["adapter_manifest_sha256"], sha256_bytes(adapter_path.read_bytes()))
            self.assertEqual(environment["adapter_implementation_sha256"], sha256_bytes(adapter_script.read_bytes()))
            drift_case = json.loads((cases / "candidate.json").read_text())
            old_implementation_hash = environment["adapter_implementation_sha256"]
            adapter_script.write_text("print('drifted')", encoding="utf-8")
            from run_case import run
            with self.assertRaisesRegex(ValueError, "implementation changed"):
                run(drift_case, "/bin/true", root / "drift-run", json.loads(adapter_path.read_text()), 5, adapter_path, "candidate-run", old_implementation_hash)

    def test_candidate_case_rechecks_immutable_manifest_binary_and_commit_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter_script = root / "adapter.py"
            adapter_script.write_text(
                "import json; print(json.dumps({'adapter_version':'v1','result':{'outcome':'READY','reason':'ACCEPTED','holytail':'NOT_APPLICABLE'},'product_evidence':{'commands':[]},'coverage':{'support':'supported','status':'exercised'}}))",
                encoding="utf-8",
            )
            adapter_path = root / "adapter.json"
            adapter_path.write_text(json.dumps({"adapter": "adapter.py", "commands": {"case": [sys.executable, "{adapter}"]}}), encoding="utf-8")
            case = {
                "id": "case",
                "ground_truth": {"expected_outcome": "READY", "expected_reason": "ACCEPTED", "expected_exit_code": 0, "holytail_expected": "NOT_APPLICABLE"},
            }
            binary_hash = sha256_bytes(Path("/bin/true").read_bytes())
            manifest_hash = sha256_bytes(adapter_path.read_bytes())
            implementation_hash = sha256_bytes(adapter_script.read_bytes())
            valid = run(
                case, "/bin/true", root / "valid", json.loads(adapter_path.read_text()), 5, adapter_path,
                "candidate-run", implementation_hash, binary_hash, manifest_hash, "a" * 40, "b" * 40,
            )
            self.assertEqual(valid["coverage"], {"support": "supported", "status": "exercised"})

            adapter_path.write_text(adapter_path.read_text() + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest"):
                run(
                    case, "/bin/true", root / "manifest-drift", json.loads(json.dumps({"adapter": "adapter.py", "commands": {"case": [sys.executable, "{adapter}"]}})), 5, adapter_path,
                    "candidate-run", implementation_hash, binary_hash, manifest_hash, "a" * 40, "b" * 40,
                )

            binary = root / "binary"
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            binary.chmod(0o755)
            binary_old_hash = sha256_bytes(binary.read_bytes())
            binary.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "binary"):
                run(
                    case, str(binary), root / "binary-drift", json.loads(json.dumps({"adapter": "adapter.py", "commands": {"case": [sys.executable, "{adapter}"]}})), 5, adapter_path,
                    "candidate-run", implementation_hash, binary_old_hash, sha256_bytes(adapter_path.read_bytes()), "a" * 40, "b" * 40,
                )

    def test_malformed_candidate_envelope_is_unsupported_not_scored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter_script = root / "adapter.py"
            adapter_script.write_text("print('not-json')", encoding="utf-8")
            adapter_path = root / "adapter.json"
            adapter_path.write_text(json.dumps({"adapter": "adapter.py", "commands": {"case": [sys.executable, "{adapter}"]}}), encoding="utf-8")
            case = {"id": "case", "ground_truth": {"expected_outcome": "READY", "expected_reason": "ACCEPTED", "expected_exit_code": 0, "holytail_expected": "NOT_APPLICABLE"}}
            result = run(case, "/bin/true", root / "run", json.loads(adapter_path.read_text()), 5, adapter_path)
            self.assertEqual(result["coverage"], {"support": "unsupported", "status": "unexercised"})
            self.assertIsNotNone(result["observed"]["json_parse_error"])

    def test_suite_captures_one_implementation_hash_for_every_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter_script = root / "adapter.py"
            adapter_script.write_text(
                "import json; print(json.dumps({'adapter_version':'v1','result':{'outcome':'READY','reason':'ACCEPTED','holytail':'NOT_APPLICABLE'},'coverage':{'support':'supported','status':'exercised'},'product_evidence':{'commands':[]}}))",
                encoding="utf-8",
            )
            adapter_path = root / "adapter.json"
            adapter_path.write_text(json.dumps({"adapter": "adapter.py", "commands": {
                "one": [sys.executable, "{adapter}"], "two": [sys.executable, "{adapter}"]
            }}), encoding="utf-8")
            cases = root / "cases"
            cases.mkdir()
            for case_id in ("one", "two"):
                (cases / f"{case_id}.json").write_text(json.dumps({
                    "id": case_id,
                    "scenario": {"execution": {"mode": "candidate-adapter"}},
                    "ground_truth": {"expected_outcome": "READY", "expected_reason": "ACCEPTED", "expected_exit_code": 0, "holytail_expected": "NOT_APPLICABLE"},
                }), encoding="utf-8")
            original_run = run_suite_module.run
            captured: list[str | None] = []

            def record(*args, **kwargs):
                captured.append(args[7])
                return original_run(*args, **kwargs)

            output = root / "stable-run"
            with patch.object(run_suite_module, "run", record), patch.object(sys, "argv", [
                "run_suite.py", "--binary", "/bin/true", "--run-id", "stable", "--adapter", str(adapter_path),
                "--product-commit-sha", "a" * 40, "--evaluator-commit-sha", "b" * 40,
                "--cases-dir", str(cases), "--out", str(output),
            ]):
                self.assertEqual(run_suite_main(), 0)
            expected = sha256_bytes(adapter_script.read_bytes())
            self.assertEqual(captured, [expected, expected])

            drift_output = root / "drift-run"
            drifted = False

            def drift(*args, **kwargs):
                nonlocal drifted
                if not drifted:
                    adapter_script.write_text("print('drifted')", encoding="utf-8")
                    drifted = True
                return original_run(*args, **kwargs)

            with patch.object(run_suite_module, "run", drift), patch.object(sys, "argv", [
                "run_suite.py", "--binary", "/bin/true", "--run-id", "drift", "--adapter", str(adapter_path),
                "--product-commit-sha", "a" * 40, "--evaluator-commit-sha", "b" * 40,
                "--cases-dir", str(cases), "--out", str(drift_output),
            ]):
                with self.assertRaisesRegex(ValueError, "implementation changed"):
                    run_suite_main()

    def test_adapter_command_resolves_from_manifest_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapters = root / "adapters"
            adapters.mkdir()
            script = adapters / "candidate.py"
            script.write_text("print('ok')", encoding="utf-8")
            manifest = adapters / "candidate.json"
            adapter = {"adapter": "candidate.py", "commands": {"case": ["python3", "{adapter}"]}}
            manifest.write_text(json.dumps(adapter), encoding="utf-8")
            case = {"id": "case", "scenario": {"execution": {"argv": ["/bin/true"]}}}
            self.assertEqual(resolve_adapter_path(manifest, adapter), script)
            self.assertEqual(command_for(case, "/bin/true", root, adapter, manifest)[1], str(script))

    def test_adapter_path_rejects_escape_symlink_and_unreadable_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapters = root / "adapters"
            adapters.mkdir()
            manifest = adapters / "candidate.json"
            outside = root / "outside.py"
            outside.write_text("print('outside')", encoding="utf-8")

            manifest.write_text(json.dumps({"adapter": "../outside.py"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "escapes"):
                resolve_adapter_path(manifest, json.loads(manifest.read_text()))

            symlink = adapters / "link.py"
            symlink.symlink_to(outside)
            manifest.write_text(json.dumps({"adapter": "link.py"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "symlinked"):
                resolve_adapter_path(manifest, json.loads(manifest.read_text()))

            unreadable = adapters / "unreadable.py"
            unreadable.write_text("print('no')", encoding="utf-8")
            unreadable.chmod(0)
            manifest.write_text(json.dumps({"adapter": "unreadable.py"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unreadable"):
                resolve_adapter_path(manifest, json.loads(manifest.read_text()))

    def test_child_environment_excludes_unrelated_host_values(self) -> None:
        with patch.dict(os.environ, {"EXITBIND_TEST_SECRET": "do-not-pass", "PATH": "/safe/bin"}):
            child = minimal_child_environment()
        self.assertEqual(child["PATH"], "/safe/bin")
        self.assertNotIn("EXITBIND_TEST_SECRET", child)
        self.assertEqual(set(child) - set(CHILD_ENV_ALLOWLIST), set())

    def test_runner_passes_only_allowlisted_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "run"
            probe = "import json,os; print(json.dumps(dict(os.environ), sort_keys=True))"
            case = {
                "id": "environment-probe",
                "ground_truth": {
                    "expected_outcome": "READY",
                    "expected_reason": "ACCEPTED",
                    "expected_exit_code": 0,
                    "holytail_expected": "NOT_APPLICABLE",
                },
            }
            adapter = {"commands": {"environment-probe": [sys.executable, "-c", probe]}}
            with patch.dict(os.environ, {"EXITBIND_TEST_SECRET": "do-not-pass"}):
                result = run(case, "/bin/true", output, adapter, 5)
            observed = json.loads((output / "raw/environment-probe.stdout").read_text())
            self.assertEqual(result["observed"]["exit_code"], 0)
            self.assertNotIn("EXITBIND_TEST_SECRET", observed)
            self.assertTrue(set(observed).issubset(set(CHILD_ENV_ALLOWLIST)))

    def _run_fixture(self, root: Path, stdout: bytes = b"output\n", stderr: bytes = b"", candidate: bool = False) -> tuple[Path, Path, bytes, bytes]:
        run_dir = root / "local-run"
        (run_dir / "cases").mkdir(parents=True)
        (run_dir / "raw").mkdir()
        environment = {
            "schema_version": "environment-v1",
            "cwd": "/var/tmp/private/eval",
            "locale": "C",
            "python": "3.12",
            "platform": "linux",
            "binary": "/var/tmp/fixture/exitbind",
            "binary_sha256": "e" * 64,
        }
        if candidate:
            environment.update(
                binary="/var/tmp/product/exitbind",
                binary_sha256="c" * 64,
                adapter_manifest="/var/tmp/eval/adapter.json",
                adapter_manifest_sha256="d" * 64,
                adapter_implementation="/var/tmp/eval/adapter.py",
                adapter_implementation_sha256="f" * 64,
                product_commit_sha="a" * 40,
                evaluator_commit_sha="b" * 40,
            )
        if stdout == b"output\n":
            stdout = (
                b'{"adapter_version":"adapter-v1","result":{"outcome":"READY",'
                b'"reason":"ACCEPTED","holytail":"NOT_APPLICABLE"},"product_evidence":{"commands":[]},'
                b'"coverage":{"support":"supported","status":"exercised"}}'
                if candidate
                else b'{"outcome":"READY","reason":"ACCEPTED","holytail":"NOT_APPLICABLE","fixture":true}'
            )
        try:
            json.loads(stdout.decode("utf-8"))
            stdout_parse_error = None
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            stdout_parse_error = str(error)
        (run_dir / "environment.json").write_text(json.dumps(environment))
        ratio = {"numerator": 1, "denominator": 1, "percentage": 100.0, "status": "computed"}
        summary = {
            "schema_version": "summary-v1", "run_id": run_dir.name, "case_count": 1,
            "candidate_dependent": candidate, "environment_file": "environment.json", "case_result_directory": "cases",
            "aggregated": {"false_acceptance_case_ids": [], "mismatch_case_ids": [], "metrics": {name: ratio for name in (
                "false_acceptance", "false_exit_rejection", "false_refusal", "holytail_false_alarms",
                "holytail_preservation_recall", "outcome_classification", "reason_classification", "valid_exit_acceptance")}},
        }
        (run_dir / "summary.json").write_text(json.dumps(summary))
        stdout_path = run_dir / "raw/case.stdout"
        stderr_path = run_dir / "raw/case.stderr"
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        observed_outcome = "READY" if stdout_parse_error is None else None
        observed_reason = "ACCEPTED" if stdout_parse_error is None else None
        observed_holytail = "NOT_APPLICABLE" if stdout_parse_error is None else None
        result = {
            "schema_version": "result-v1", "run_id": run_dir.name, "case_id": "case",
            "expected": {"outcome": "READY", "reason": "ACCEPTED", "exit_code": 0, "holytail": "NOT_APPLICABLE"},
            "observed": {"outcome": observed_outcome, "reason": observed_reason, "exit_code": 0, "timed_out": False, "json_parse_error": stdout_parse_error, "holytail": observed_holytail, "duration_ms": 1.0},
            "classification": {"outcome_match": observed_outcome == "READY", "reason_match": observed_reason == "ACCEPTED", "exit_code_match": True, "holytail_match": None},
            "coverage": {"support": "supported", "status": "exercised"},
            "raw": {"stdout_path": "raw/case.stdout", "stderr_path": "raw/case.stderr", "stdout_sha256": sha256_bytes(stdout), "stderr_sha256": sha256_bytes(stderr)},
            "command": ["/bin/true"],
        }
        (run_dir / "cases/case.json").write_text(json.dumps(result))
        summary["aggregated"] = aggregate_results([result])
        (run_dir / "summary.json").write_text(json.dumps(summary))
        return run_dir, root / "public.json", stdout, stderr

    def test_public_export_sanitizes_paths_and_binds_public_streams(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, stdout, stderr = self._run_fixture(Path(temporary), b"cwd=/var/tmp/private/eval\n", b"ok\n")
            original_stdout = (run_dir / "raw/case.stdout").read_bytes()
            public = export_run(run_dir, output)
            validate_public_export(public)
            self.assertFalse(has_absolute_path(public))
            stream = public["public_evidence"]["cases"]["case"]["stdout"]
            self.assertIn("redacted-absolute-path", stream["content"])
            self.assertEqual(stream["original_sha256"], sha256_bytes(stdout))
            self.assertEqual(stream["source_local_raw"]["result_sha256"], sha256_bytes(stdout))
            self.assertEqual(stream["sanitized_sha256"], sha256_bytes(stream["content"].encode()))
            self.assertTrue(stream["redaction"]["applied"])
            self.assertFalse(stream["redaction"]["scans_for_arbitrary_secrets"])
            self.assertEqual((run_dir / "raw/case.stdout").read_bytes(), original_stdout)
            self.assertEqual((run_dir / "raw/case.stderr").read_bytes(), stderr)
            self.assertEqual(public["local_raw_evidence"]["contents_included"], False)

    def test_public_export_rejects_tamper_and_hash_binding_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary))
            (run_dir / "raw/case.stdout").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "hash"):
                export_run(run_dir, output)
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary))
            public = export_run(run_dir, output)
            drifted = copy.deepcopy(public)
            drifted["public_evidence"]["cases"]["case"]["stdout"]["source_local_raw"]["result_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "bound|binding"):
                validate_public_export(drifted)

    def test_public_export_rejects_missing_traversal_and_symlink_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary))
            (run_dir / "raw/case.stdout").unlink()
            with self.assertRaisesRegex(ValueError, "missing"):
                export_run(run_dir, output)
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary))
            case_path = run_dir / "cases/case.json"
            case = json.loads(case_path.read_text())
            case["raw"]["stdout_path"] = "../raw/case.stdout"
            case_path.write_text(json.dumps(case))
            with self.assertRaisesRegex(ValueError, "relative"):
                export_run(run_dir, output)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, output, stdout, _ = self._run_fixture(root)
            target = root / "outside.stdout"
            target.write_bytes(stdout)
            (run_dir / "raw/case.stdout").unlink()
            (run_dir / "raw/case.stdout").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink|escapes"):
                export_run(run_dir, output)

    def test_public_export_rejects_invalid_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary), b"\xff")
            with self.assertRaisesRegex(ValueError, "UTF-8"):
                export_run(run_dir, output)

    def test_public_copy_omits_recoverable_original_stream_payloads(self) -> None:
        import base64

        with tempfile.TemporaryDirectory() as temporary:
            product_path = b"/var/tmp/private/product-output"
            product_error = b"/var/tmp/private/product-error"
            payload = {
                "adapter_version": "adapter-v1",
                "result": {"outcome": "READY", "reason": "ACCEPTED", "holytail": "NOT_APPLICABLE"},
                "product_evidence": {"commands": [{
                    "stdout_base64": base64.b64encode(product_path).decode(),
                    "stdout_sha256": sha256_bytes(product_path),
                    "stderr_base64": base64.b64encode(product_error).decode(),
                    "stderr_sha256": sha256_bytes(product_error),
                }]},
                "coverage": {"support": "supported", "status": "exercised"},
            }
            run_dir, output, stdout, _ = self._run_fixture(
                Path(temporary), json.dumps(payload).encode(), candidate=True
            )
            public = export_run(run_dir, output)
            stream = public["public_evidence"]["cases"]["case"]["stdout"]
            self.assertNotIn("stdout_base64", stream["content"])
            self.assertNotIn(base64.b64encode(product_path).decode(), stream["content"])
            self.assertNotIn(base64.b64encode(product_error).decode(), stream["content"])
            self.assertTrue(stream["redaction"]["encoded_originals_omitted"])
            self.assertEqual(stream["original_sha256"], sha256_bytes(stdout))

            mismatch = copy.deepcopy(payload)
            mismatch["product_evidence"]["commands"][0]["stderr_sha256"] = "0" * 64
            mismatch_run, mismatch_output, _, _ = self._run_fixture(
                Path(temporary) / "mismatch", json.dumps(mismatch).encode(), candidate=True
            )
            with self.assertRaisesRegex(ValueError, "does not match nested product bytes"):
                export_run(mismatch_run, mismatch_output)

    def test_candidate_export_rejects_fixture_identity_before_product_omission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary), candidate=True)
            environment_path = run_dir / "environment.json"
            environment = json.loads(environment_path.read_text())
            environment["binary"] = "/var/tmp/fixture/exitbind"
            environment_path.write_text(json.dumps(environment))
            with self.assertRaisesRegex(ValueError, "fixture binary"):
                export_run(run_dir, output)

        with tempfile.TemporaryDirectory() as temporary:
            payload = {
                "adapter_version": "adapter-v1",
                "result": {"outcome": "READY", "reason": "ACCEPTED", "holytail": "NOT_APPLICABLE"},
                "product_evidence": {"nested": {"fixture": True}},
                "coverage": {"support": "supported", "status": "exercised"},
            }
            run_dir, output, _, _ = self._run_fixture(Path(temporary), json.dumps(payload).encode(), candidate=True)
            with self.assertRaisesRegex(ValueError, "fixture marker"):
                export_run(run_dir, output)

    def test_recursive_schema_validation_rejects_unknown_fields_and_nonfinite_numbers(self) -> None:
        root = Path(__file__).resolve().parents[1]
        case_schema = json.loads((root / "schemas/case.schema.json").read_text())
        case = json.loads((root / "cases/false-completion-001.json").read_text())
        validate_schema_document(case, case_schema)
        unknown = copy.deepcopy(case)
        unknown["scenario"]["execution"]["unexpected"] = True
        with self.assertRaises(ValueError):
            validate_schema_document(unknown, case_schema)

        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary))
            valid = export_run(run_dir, output)
            validate_public_export(valid)
            nonfinite = copy.deepcopy(valid)
            nonfinite["artifacts"]["cases"]["case"]["observed"]["duration_ms"] = float("nan")
            with self.assertRaisesRegex(ValueError, "finite|number|duration"):
                validate_public_export(nonfinite)

    def test_public_copy_sanitizes_nested_escaped_and_plain_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = (
                b'{"adapter_version":"adapter-v1","result":{"outcome":"READY",'
                b'"reason":"ACCEPTED","holytail":"NOT_APPLICABLE"},"product_evidence":'
                b'{"commands":[{"stdout":"{\\"nested\\":\\"\\u002fvar\\u002ftmp\\u002fprivate\\u002fescaped\\"}",'
                b'"stderr":"{\\"nested\\":\\"/var/tmp/private/plain\\"}"},'
                b'{"stdout":"/var/tmp/private/depth1","stderr":"benign text"},'
                b'{"stdout":"{\\"nested\\":{\\"message\\":\\"benign\\"}}","stderr":"plain"}]},'
                b'"coverage":{"support":"supported",'
                b'"status":"exercised"}}'
            )
            run_dir, output, _, _ = self._run_fixture(Path(temporary), raw, candidate=True)
            public = export_run(run_dir, output)
            validate_public_export(public)
            stream = public["public_evidence"]["cases"]["case"]["stdout"]
            self.assertFalse(has_absolute_path(public))
            self.assertTrue(stream["redaction"]["product_streams_omitted"])
            self.assertIn("<omitted-product-stdout>", stream["content"])
            self.assertNotIn("/var/tmp/private", stream["content"])
            parsed = json.loads(stream["content"])
            self.assertFalse(has_absolute_path(parsed["product_evidence"]))
            for command in parsed["product_evidence"]["commands"]:
                self.assertEqual(command["stdout"], "<omitted-product-stdout>")
                self.assertEqual(command["stderr"], "<omitted-product-stderr>")

    def test_public_export_rejects_invalid_nested_product_utf8(self) -> None:
        import base64

        with tempfile.TemporaryDirectory() as temporary:
            payload = {
                "adapter_version": "adapter-v1",
                "result": {"outcome": "READY", "reason": "ACCEPTED", "holytail": "NOT_APPLICABLE"},
                "product_evidence": {
                    "commands": [{
                        "stdout": "�", "stdout_base64": base64.b64encode(b"\xff").decode(), "stdout_sha256": sha256_bytes(b"\xff"),
                        "stdout_valid_utf8": False, "stdout_decode_error": "invalid",
                        "stderr": "�", "stderr_base64": base64.b64encode(b"\xfe").decode(), "stderr_sha256": sha256_bytes(b"\xfe"),
                        "stderr_valid_utf8": False, "stderr_decode_error": "invalid",
                    }]
                },
                "coverage": {"support": "supported", "status": "exercised"},
            }
            run_dir, output, _, _ = self._run_fixture(Path(temporary), json.dumps(payload).encode(), candidate=True)
            with self.assertRaisesRegex(ValueError, "invalid UTF-8"):
                export_run(run_dir, output)

    def test_validator_recomputes_classification_and_aggregate_truth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary), candidate=True)
            valid = export_run(run_dir, output)
            validate_public_export(valid)
            mutations = {
                "all unsupported with old metrics": lambda value: (
                    value["artifacts"]["cases"]["case"]["coverage"].update(support="unsupported", status="unexercised"),
                    value["artifacts"]["summary"]["coverage"].update(supported_exercised_case_ids=[], unsupported_unexercised_case_ids=["case"]),
                ),
                "false acceptance ID": lambda value: value["artifacts"]["summary"]["aggregated"].update(false_acceptance_case_ids=["case"]),
                "mismatch ID": lambda value: value["artifacts"]["summary"]["aggregated"].update(mismatch_case_ids=["case"]),
                "false classification": lambda value: value["artifacts"]["cases"]["case"]["classification"].update(outcome_match=False),
                "inconsistent coverage": lambda value: value["artifacts"]["summary"]["coverage"].update(supported_exercised_case_ids=[]),
            }
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    candidate = copy.deepcopy(valid)
                    mutate(candidate)
                    with self.assertRaises(ValueError):
                        validate_public_export(candidate)

    def test_validator_rejects_bool_replacement_and_missing_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary))
            valid = export_run(run_dir, output)
            for label, mutate in {
                "bool replacements": lambda value: value["public_evidence"]["cases"]["case"]["stdout"]["redaction"].update(replacements=True),
                "numeric secret scan flag": lambda value: value["public_evidence"]["cases"]["case"]["stdout"]["redaction"].update(scans_for_arbitrary_secrets=0),
                "string secret scan flag": lambda value: value["public_evidence"]["cases"]["case"]["stdout"]["redaction"].update(scans_for_arbitrary_secrets="false"),
                "policy secret scan flag": lambda value: value["public_evidence"]["redaction_policy"].update(scans_for_arbitrary_secrets=0),
                "missing command": lambda value: value["artifacts"]["cases"]["case"].pop("command"),
            }.items():
                with self.subTest(label=label):
                    candidate = copy.deepcopy(valid)
                    mutate(candidate)
                    with self.assertRaises(ValueError):
                        validate_public_export(candidate)
            schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/public-export.schema.json").read_text())
            self.assertIn("command", schema["$defs"]["caseResult"]["required"])
            self.assertEqual(schema["$defs"]["publicStream"]["properties"]["redaction"]["properties"]["scans_for_arbitrary_secrets"]["const"], False)

    def test_validator_rejects_bool_for_all_integer_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary))
            valid = export_run(run_dir, output)
            mutations = {
                "case count": lambda value: value["artifacts"]["summary"].update(case_count=True),
                "expected exit": lambda value: value["artifacts"]["cases"]["case"]["expected"].update(exit_code=True),
                "observed exit": lambda value: value["artifacts"]["cases"]["case"]["observed"].update(exit_code=True),
                "duration": lambda value: value["artifacts"]["cases"]["case"]["observed"].update(duration_ms=True),
                "ratio numerator": lambda value: value["artifacts"]["summary"]["aggregated"]["metrics"]["false_acceptance"].update(numerator=True),
            }
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    candidate = copy.deepcopy(valid)
                    mutate(candidate)
                    with self.assertRaises(ValueError):
                        validate_public_export(candidate)

    def test_public_export_preserves_candidate_commit_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary), candidate=True)
            public = export_run(run_dir, output)
            self.assertEqual(public["artifacts"]["environment"]["product_commit_sha"], "a" * 40)
            self.assertEqual(public["artifacts"]["environment"]["evaluator_commit_sha"], "b" * 40)
            validate_public_export(public)

    def test_fixture_and_candidate_protocol_shapes_are_mode_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture_dir, fixture_output, _, _ = self._run_fixture(Path(temporary) / "fixture")
            fixture = export_run(fixture_dir, fixture_output)
            validate_public_export(fixture)
            candidate_dir, candidate_output, _, _ = self._run_fixture(Path(temporary) / "candidate", candidate=True)
            candidate = export_run(candidate_dir, candidate_output)
            validate_public_export(candidate)

            trailing_json = copy.deepcopy(candidate)
            stream = trailing_json["public_evidence"]["cases"]["case"]["stdout"]
            stream["content"] += "\n{}\n"
            stream["sanitized_sha256"] = sha256_bytes(stream["content"].encode())
            with self.assertRaisesRegex(ValueError, "valid JSON|malformed|envelope"):
                validate_public_export(trailing_json)

            disagreed_result = copy.deepcopy(candidate)
            stream = disagreed_result["public_evidence"]["cases"]["case"]["stdout"]
            stream["content"] = stream["content"].replace('"outcome":"READY"', '"outcome":"BLOCKED"')
            stream["sanitized_sha256"] = sha256_bytes(stream["content"].encode())
            with self.assertRaisesRegex(ValueError, "disagrees with observed"):
                validate_public_export(disagreed_result)

            escaped_marker = copy.deepcopy(candidate)
            stream = escaped_marker["public_evidence"]["cases"]["case"]["stdout"]
            stream["content"] = '{"outcome":"READY","reason":"ACCEPTED","holytail":"NOT_APPLICABLE","\\u0066ixture":true}'
            stream["sanitized_sha256"] = sha256_bytes(stream["content"].encode())
            escaped_marker["artifacts"]["cases"]["case"]["observed"]["json_parse_error"] = "recorded parse failure"
            with self.assertRaisesRegex(ValueError, "valid JSON despite"):
                validate_public_export(escaped_marker)

            malformed_with_credit = copy.deepcopy(candidate)
            stream = malformed_with_credit["public_evidence"]["cases"]["case"]["stdout"]
            stream["content"] = "not-json"
            stream["sanitized_sha256"] = sha256_bytes(stream["content"].encode())
            malformed_with_credit["artifacts"]["cases"]["case"]["observed"]["json_parse_error"] = "recorded parse failure"
            with self.assertRaisesRegex(ValueError, "retains scored"):
                validate_public_export(malformed_with_credit)

            candidate_with_fixture = copy.deepcopy(candidate)
            fixture_json = '{"outcome":"READY","reason":"ACCEPTED","holytail":"NOT_APPLICABLE","fixture":true}'
            stream = candidate_with_fixture["public_evidence"]["cases"]["case"]["stdout"]
            stream["content"] = fixture_json
            stream["sanitized_sha256"] = sha256_bytes(fixture_json.encode())
            with self.assertRaisesRegex(ValueError, "fixture-only"):
                validate_public_export(candidate_with_fixture)

            fixture_with_candidate = copy.deepcopy(fixture)
            candidate_json = '{"adapter_version":"adapter-v1","result":{"outcome":"READY","reason":"ACCEPTED","holytail":"NOT_APPLICABLE"}}'
            stream = fixture_with_candidate["public_evidence"]["cases"]["case"]["stdout"]
            stream["content"] = candidate_json
            stream["sanitized_sha256"] = sha256_bytes(candidate_json.encode())
            with self.assertRaisesRegex(ValueError, "candidate adapter"):
                validate_public_export(fixture_with_candidate)

            malformed_dir, malformed_output, _, _ = self._run_fixture(
                Path(temporary) / "malformed", stdout=b"not-json\n", candidate=True
            )
            malformed = export_run(malformed_dir, malformed_output)
            validate_public_export(malformed)

            relabeled = copy.deepcopy(fixture)
            relabeled["artifacts"]["summary"]["candidate_dependent"] = True
            relabeled["artifacts"]["environment"].update(
                adapter_manifest="<redacted-adapter-manifest>",
                adapter_manifest_sha256="d" * 64,
                adapter_implementation="<redacted-adapter-implementation>",
                adapter_implementation_sha256="f" * 64,
                product_commit_sha="a" * 40,
                evaluator_commit_sha="b" * 40,
            )
            with self.assertRaisesRegex(ValueError, "fixture-only"):
                validate_public_export(relabeled)

    def test_fixture_export_rejects_fake_candidate_commit_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary))
            environment_path = run_dir / "environment.json"
            environment = json.loads(environment_path.read_text())
            environment["product_commit_sha"] = "a" * 40
            environment_path.write_text(json.dumps(environment))
            with self.assertRaisesRegex(ValueError, "fixture environment|forbidden schema"):
                export_run(run_dir, output)

    def test_fixture_relabel_with_only_fake_commits_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary))
            environment_path = run_dir / "environment.json"
            environment = json.loads(environment_path.read_text())
            environment.update(product_commit_sha="a" * 40, evaluator_commit_sha="b" * 40)
            environment_path.write_text(json.dumps(environment))
            summary_path = run_dir / "summary.json"
            summary = json.loads(summary_path.read_text())
            summary["candidate_dependent"] = True
            summary_path.write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError, "manifest|implementation|candidate environment"):
                export_run(run_dir, output)

    def test_public_preflight_rejects_unsanitized_absolute_paths(self) -> None:
        self.assertTrue(has_absolute_path({"cwd": "/var/tmp/private/eval"}))
        self.assertTrue(has_absolute_path({"cwd": "file:///var/tmp/private/eval"}))
        self.assertFalse(has_absolute_path({"cwd": "<redacted-absolute-path:abc>"}))

    def test_global_path_escape_normalization_and_reparse_boundary(self) -> None:
        variants = (
            "/var/tmp/private/plain",
            r"\/var\/tmp\/private\/slash",
            r"\u002fvar\u002ftmp\u002fprivate\u002funicode",
            r"\\u002fvar\\u002ftmp\\u002fprivate\\u002fdouble",
            r"C:\\Temp\\Alice\\secret",
            r"C:\\u005cTemp\\u005cAlice\\u005cunicode",
            r"file:\\u002f\\u002f\\u002fvar\\u002ftmp\\private",
        )
        for value in variants:
            with self.subTest(value=value):
                self.assertTrue(has_absolute_path(value))
                self.assertFalse(has_absolute_path(sanitize({"value": value})))

    def test_non_file_uri_schemes_are_byte_identical_across_retained_surfaces_and_depths(self) -> None:
        def token(path: str) -> str:
            return f"<redacted-absolute-path:{sha256_bytes(path.encode('utf-8'))}>"

        sources = (
            "prefix http://example.test/var/tmp/private/name?keep=/var/tmp/private suffix",
            "prefix https://example.test/var/tmp/private/name#fragment suffix",
            "prefix ssh://example.test/var/tmp/private/name suffix",
            "prefix /var/tmp/private/name suffix",
            "prefix file:///var/tmp/private/name?keep=value#tail suffix",
        )
        expected = (
            sources[0],
            sources[1],
            sources[2],
            "prefix " + token("/var/tmp/private/name") + " suffix",
            "prefix " + token("file:///var/tmp/private/name") + "?keep=value#tail suffix",
        )

        def nested(source: str, depth: int) -> str:
            for _ in range(depth):
                source = json.dumps(source, separators=(",", ":"))
            return source

        with tempfile.TemporaryDirectory() as temporary:
            for depth in (1, 2, 3):
                encoded = [nested(value, depth) for value in sources]
                expected_encoded = [nested(value, depth) for value in expected]
                payload = {
                    "adapter_version": "adapter-v1",
                    "result": {"outcome": "READY", "reason": encoded[0], "holytail": "NOT_APPLICABLE"},
                    "limitation": encoded[1],
                    "product_evidence": {
                        "notes": encoded,
                        "log": [{"text": value} for value in encoded],
                        "argv": encoded,
                        "commands": [],
                    },
                    "coverage": {"support": "supported", "status": "exercised"},
                }
                root = Path(temporary) / f"run-{depth}"
                run_dir, output, _, _ = self._run_fixture(root, json.dumps(payload).encode(), candidate=True)
                case_path = run_dir / "cases/case.json"
                case = json.loads(case_path.read_text())
                case["command"] = encoded
                case["expected"]["reason"] = encoded[0]
                case["observed"]["reason"] = encoded[0]
                case["classification"]["reason_match"] = True
                case_path.write_text(json.dumps(case))
                summary_path = run_dir / "summary.json"
                summary = json.loads(summary_path.read_text())
                summary["aggregated"] = aggregate_results([case])
                summary_path.write_text(json.dumps(summary))
                public = export_run(run_dir, output)
                validate_public_export(public)
                exported = json.loads(public["public_evidence"]["cases"]["case"]["stdout"]["content"])
                self.assertEqual(exported["result"]["reason"], expected_encoded[0])
                self.assertEqual(exported["limitation"], expected_encoded[1])
                self.assertEqual(exported["product_evidence"]["notes"], expected_encoded)
                self.assertEqual([entry["text"] for entry in exported["product_evidence"]["log"]], expected_encoded)
                self.assertEqual(exported["product_evidence"]["argv"], expected_encoded)
                self.assertEqual(public["artifacts"]["cases"]["case"]["command"], expected_encoded)
                self.assertFalse(has_absolute_path(public))

        with tempfile.TemporaryDirectory() as temporary:
            payload = {
                "adapter_version": "adapter-v1",
                "result": {"outcome": "READY", "reason": "ACCEPTED", "holytail": "NOT_APPLICABLE"},
                "coverage": {"support": "supported", "status": "exercised"},
                "limitation": r"nested={\"path\":\"\\u002fvar\\u002ftmp\\u002fprivate\\u002flimitation\"}",
                "product_evidence": {
                    "notes": [r"C:\\u005cTemp\\Alice\\note", r"file:\\u002f\\u002f\\u002fvar\\u002ftmp\\note"],
                    "log": [{"text": r"{\"path\":\"\\u002fvar\\u002ftmp\\u002fprivate\\u002flog\"}"}],
                    "commands": [{"stdout": r"{\"path\":\"\\u002fvar\\u002ftmp\\u002fprivate\\u002fstdout\"}", "stderr": r"{\"path\":\"/var/tmp/private/stderr\"}"}],
                },
            }
            run_dir, output, _, _ = self._run_fixture(Path(temporary), json.dumps(payload).encode(), candidate=True)
            public = export_run(run_dir, output)
            validate_public_export(public)

            def walk(value: object, depth: int = 0) -> None:
                if isinstance(value, str):
                    self.assertFalse(has_absolute_path(value), value)
                    if depth < 4:
                        try:
                            reparsed = json.loads(value)
                        except json.JSONDecodeError:
                            return
                        walk(reparsed, depth + 1)
                elif isinstance(value, dict):
                    for child in value.values():
                        walk(child, depth)
                elif isinstance(value, list):
                    for child in value:
                        walk(child, depth)

            walk(public)

    def test_colon_adjacent_paths_are_redacted_across_retained_surfaces_and_depths(self) -> None:
        def nested(value: str, depth: int) -> str:
            for _ in range(depth):
                value = json.dumps(value, separators=(",", ":"))
            return value

        path = "cwd:/var/tmp/private/operator"
        token = f"<redacted-absolute-path:{sha256_bytes('/var/tmp/private/operator'.encode('utf-8'))}>"
        expected_path = f"cwd:{token}"
        values = (
            path,
            "cwd:relative/operator",
            "cwd:http://example.test/var/tmp/private/operator",
            "cwd:https://example.test/var/tmp/private/operator",
            "cwd:ssh://example.test/var/tmp/private/operator",
        )
        expected = (expected_path, *values[1:])

        def prepare_case(run_dir: Path, reason: str) -> None:
            case_path = run_dir / "cases/case.json"
            case = json.loads(case_path.read_text())
            encoded = nested(reason, depth)
            case["command"] = [encoded]
            case["expected"]["reason"] = encoded
            case["observed"]["reason"] = encoded
            case["classification"]["reason_match"] = True
            case_path.write_text(json.dumps(case))
            summary_path = run_dir / "summary.json"
            summary = json.loads(summary_path.read_text())
            summary["aggregated"] = aggregate_results([case])
            summary_path.write_text(json.dumps(summary))

        with tempfile.TemporaryDirectory() as temporary:
            run_index = 0
            for depth in (1, 2, 3):
                encoded_values = [nested(value, depth) for value in values]
                expected_values = [nested(value, depth) for value in expected]
                for reason, limitation in ((path, "cwd:relative/operator"), ("cwd:relative/operator", path)):
                    payload = {
                        "adapter_version": "adapter-v1",
                        "result": {"outcome": "READY", "reason": nested(reason, depth), "holytail": "NOT_APPLICABLE"},
                        "limitation": nested(limitation, depth),
                        "product_evidence": {
                            "notes": encoded_values,
                            "log": [{"text": value} for value in encoded_values],
                            "argv": encoded_values,
                            "commands": [],
                        },
                        "coverage": {"support": "supported", "status": "exercised"},
                    }
                    root = Path(temporary) / f"colon-{run_index}"
                    run_index += 1
                    run_dir, output, _, _ = self._run_fixture(
                        root, json.dumps(payload).encode(), candidate=True
                    )
                    prepare_case(run_dir, reason)
                    public = export_run(run_dir, output)
                    validate_public_export(public)
                    exported = json.loads(public["public_evidence"]["cases"]["case"]["stdout"]["content"])
                    self.assertEqual(exported["result"]["reason"], nested(expected_path if reason == path else "cwd:relative/operator", depth))
                    self.assertEqual(exported["limitation"], nested(expected_path if limitation == path else "cwd:relative/operator", depth))
                    self.assertEqual(exported["product_evidence"]["notes"], expected_values)
                    self.assertEqual([entry["text"] for entry in exported["product_evidence"]["log"]], expected_values)
                    self.assertEqual(exported["product_evidence"]["argv"], expected_values)
                    self.assertEqual(public["artifacts"]["cases"]["case"]["command"], [nested(expected_path if reason == path else "cwd:relative/operator", depth)])
                    self.assertFalse(has_absolute_path(public))

    def test_public_retained_strings_preserve_nested_json_depth_and_bytes(self) -> None:
        def nested(value: str, depth: int) -> str:
            for _ in range(depth):
                value = json.dumps(value, separators=(",", ":"))
            return value

        def reparse_depth(value: str) -> int:
            depth = 0
            while isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    break
                depth += 1
            return depth

        def path_variant(family: str, variant: str) -> str:
            if family == "posix":
                path = "/var/tmp/private/reparse"
            elif family == "windows":
                path = r"C:\Temp\Alice\reparse"
            else:
                path = "file:///var/tmp/private/reparse?keep=value#tail"
            if variant == "escaped":
                return path.replace("\\", "\\\\").replace("/", r"\/")
            if variant == "double":
                return path.replace("\\", r"\\u005c").replace("/", r"\\u002f")
            return path

        def terminal_for(family: str, variant: str, depth: int) -> str:
            return (".", ":", "!", "—", "`")[(depth + len(family) + len(variant)) % 5]

        def retained_source(family: str, variant: str, depth: int) -> str:
            base = path_variant(family, variant)
            terminal = terminal_for(family, variant, depth)
            if family == "file":
                path_part, suffix = base.split("?", 1)
                base = f"{path_part}(inner)[segment],part;piece?{suffix}{terminal}"
            else:
                base = f"{base}(inner)[segment],part;piece{terminal}"
            wrappers = (("(", ")"), ("[", "]"), ("{", "}"), ("<", ">"))
            opener, closer = wrappers[(depth + len(family) + len(variant)) % len(wrappers)]
            delimiter = ";" if variant == "escaped" else ","
            return f"prefix {opener}{base}{closer}{delimiter} suffix"

        matrix = []
        for family in ("posix", "windows", "file"):
            for variant in ("ordinary", "escaped", "double"):
                for depth in (1, 2, 3):
                    matrix.append({
                        "family": family,
                        "variant": variant,
                        "depth": depth,
                        "source": retained_source(family, variant, depth),
                        "terminal": terminal_for(family, variant, depth),
                        "path": nested(retained_source(family, variant, depth), depth),
                        "control": nested(f"prefix benign-{family}-{variant}-{depth} suffix", depth),
                    })
        self.assertEqual(len(matrix), 27)
        interleaved = [value for item in matrix for value in (item["path"], item["control"])]
        interleaved_metadata = [
            (item, kind) for item in matrix for kind in ("path", "control")
        ]
        self.assertEqual(len(interleaved), 54)

        def payload_for(reason: str, limitation: str) -> dict:
            return {
                "adapter_version": "adapter-v1",
                "result": {"outcome": "READY", "reason": reason, "holytail": "NOT_APPLICABLE"},
                "limitation": limitation,
                "product_evidence": {
                    "notes": interleaved,
                    "log": [{"text": item} for item in interleaved],
                    "argv": interleaved,
                    "commands": [],
                },
                "coverage": {"support": "supported", "status": "exercised"},
            }

        def prepare_case(run_dir: Path, reason: str) -> None:
            case_path = run_dir / "cases/case.json"
            case = json.loads(case_path.read_text())
            case["command"] = interleaved
            case["expected"]["reason"] = reason
            case["observed"]["reason"] = reason
            case["classification"]["reason_match"] = True
            case_path.write_text(json.dumps(case))
            summary_path = run_dir / "summary.json"
            summary = json.loads(summary_path.read_text())
            summary["aggregated"] = aggregate_results([case])
            summary_path.write_text(json.dumps(summary))

        def extract_surfaces(public: dict) -> dict[str, list[str]]:
            payload = json.loads(public["public_evidence"]["cases"]["case"]["stdout"]["content"])
            return {
                "result.reason": [payload["result"]["reason"]],
                "limitation": [payload["limitation"]],
                "product_evidence.notes[*]": payload["product_evidence"]["notes"],
                "product_evidence.log[*].text": [entry["text"] for entry in payload["product_evidence"]["log"]],
                "product_evidence.argv[*]": payload["product_evidence"]["argv"],
                "command[*]": public["artifacts"]["cases"]["case"]["command"],
            }

        def terminal(value: str, depth: int) -> str:
            for _ in range(depth):
                value = json.loads(value)
            self.assertIsInstance(value, str)
            return value

        def assert_value(value: str, item: dict, kind: str) -> None:
            self.assertEqual(reparse_depth(value), item["depth"])
            self.assertFalse(has_absolute_path(value), value)
            if kind == "control":
                self.assertEqual(value, item["control"])
            else:
                expected = sanitize(item["source"])
                self.assertEqual(terminal(value, item["depth"]), expected)
                if item["family"] == "file":
                    self.assertIn("?keep=value#tail", expected)
                self.assertTrue(expected.startswith("prefix "))
                self.assertTrue(expected.endswith(" suffix"))

        array_surfaces = {
            "product_evidence.notes[*]",
            "product_evidence.log[*].text",
            "product_evidence.argv[*]",
            "command[*]",
        }
        scalar_path_seen = {"result.reason": set(), "limitation": set()}
        scalar_control_seen = {"result.reason": set(), "limitation": set()}
        self.assertEqual(sum(kind == "path" for _, kind in interleaved_metadata), 27)
        self.assertEqual(sum(kind == "control" for _, kind in interleaved_metadata), 27)
        self.assertEqual({item["terminal"] for item in matrix}, {".", ":", "!", "—", "`"})
        with tempfile.TemporaryDirectory() as temporary:
            run_index = 0
            arrays_checked = False
            for index, item in enumerate(matrix):
                for reason_kind, limitation_kind in (("path", "control"), ("control", "path")):
                    reason = item[reason_kind]
                    limitation = item[limitation_kind]
                    root = Path(temporary) / f"run-{run_index}"
                    run_index += 1
                    run_dir, output, _, _ = self._run_fixture(
                        root,
                        json.dumps(payload_for(reason, limitation)).encode(),
                        candidate=True,
                    )
                    prepare_case(run_dir, reason)
                    public = export_run(run_dir, output)
                    validate_public_export(public)
                    self.assertFalse(has_absolute_path(public))
                    surfaces = extract_surfaces(public)
                    for surface, values in surfaces.items():
                        self.assertEqual(len(values), 1 if surface not in array_surfaces else 54, surface)
                    assert_value(surfaces["result.reason"][0], item, reason_kind)
                    assert_value(surfaces["limitation"][0], item, limitation_kind)
                    scalar_path_seen["result.reason" if reason_kind == "path" else "limitation"].add((item["family"], item["variant"], item["depth"]))
                    scalar_control_seen["result.reason" if reason_kind == "control" else "limitation"].add((item["family"], item["variant"], item["depth"]))
                    if not arrays_checked:
                        for surface in array_surfaces:
                            for value, (array_item, kind) in zip(surfaces[surface], interleaved_metadata):
                                assert_value(value, array_item, kind)
                        self.assertEqual(
                            public["public_evidence"]["cases"]["case"]["stdout"]["redaction"]["replacements"],
                            82,
                        )
                        arrays_checked = True

        expected_coverage = {(item["family"], item["variant"], item["depth"]) for item in matrix}
        for surface in ("result.reason", "limitation"):
            self.assertEqual(scalar_path_seen[surface], expected_coverage)
            self.assertEqual(scalar_control_seen[surface], expected_coverage)
        for family in ("posix", "windows", "file"):
            tokens = {sanitize(path_variant(family, variant)) for variant in ("ordinary", "escaped", "double")}
            self.assertEqual(len(tokens), 1)

    def test_path_span_detection_preserves_boundaries_and_canonical_hashes(self) -> None:
        values = (
            r"prefix /var/tmp/private/name\with suffix",
            r"prefix C:\Temp\Alice\ suffix",
            r"prefix file://localhost/var/tmp/private/name suffix",
            r"prefix file://localhost/var/tmp/private/name?keep=/query#tail suffix",
            r"prefix file:\/\/localhost\/var\/tmp\/private\/name suffix",
            r"prefix file:\\u002f\\u002flocalhost\\u002fvar\u002ftmp\\u002fprivate\\u002fname suffix",
            r"prefix /var/tmp/private/one, /var/tmp/private/two suffix",
            r"prefix C:\u005cTemp\\Alice\u002freparse suffix",
        )
        for value in values:
            with self.subTest(value=value):
                sanitized = sanitize(value)
                self.assertFalse(has_absolute_path(sanitized))
                self.assertTrue(sanitized.startswith("prefix "))
                self.assertTrue(sanitized.endswith(" suffix"))

        quote_run = r'prefix C:\\Temp\\Alice\\secret\\\" suffix'
        sanitized_quote_run = sanitize(quote_run)
        self.assertFalse(has_absolute_path(sanitized_quote_run))
        self.assertTrue(sanitized_quote_run.endswith(r'\\\" suffix'))

        malformed = r'broken \u002fvar\u002ftmp\private'
        sanitized_malformed = sanitize(malformed)
        self.assertFalse(has_absolute_path(sanitized_malformed))
        self.assertTrue(sanitized_malformed.startswith('broken '))
        self.assertFalse(sanitized_malformed == malformed)

        for family, values_for_family in {
            "posix": (r"/var/tmp/private/name", r"\/var\/tmp\/private\/name", r"\\u002fvar\\u002ftmp\\u002fprivate\\u002fname"),
            "windows": (r"C:\Temp\Alice\name", r"C:\\Temp\\Alice\\name", r"C:\\u005cTemp\\u005cAlice\\u005cname"),
            "file": ("file:///var/tmp/private/name", r"file:\/\/\/var\/tmp\/private\/name", r"file:\\u002f\\u002f\\u002fvar\\u002ftmp\\u002fprivate\\u002fname"),
        }.items():
            with self.subTest(family=family):
                self.assertEqual({sanitize(value) for value in values_for_family}, {sanitize(values_for_family[0])})

    def test_path_span_preserves_syntactic_closers_and_internal_punctuation(self) -> None:
        path = "/var/tmp/private/name"
        token = sanitize(path)
        self.assertEqual(sanitize(f"prefix ({path}) suffix"), f"prefix ({token}) suffix")
        self.assertEqual(sanitize(f"prefix {path}, suffix"), f"prefix {token}, suffix")
        windows = r"C:\Temp\Alice\name"
        self.assertEqual(sanitize(f"prefix {windows}; suffix"), f"prefix {sanitize(windows)}; suffix")
        first = "/var/tmp/private/one"
        second = "/var/tmp/private/two"
        self.assertEqual(
            sanitize(f"prefix {first},{second} suffix"),
            f"prefix {sanitize(first)},{sanitize(second)} suffix",
        )
        internal = "/var/tmp/private/name(part)[segment],part;piece"
        self.assertEqual(sanitize(f"prefix {internal} suffix"), f"prefix {sanitize(internal)} suffix")

    def test_path_span_preserves_terminal_presentation_punctuation(self) -> None:
        path = "/var/tmp/private/name"
        token = sanitize(path)
        for punctuation in (".", ":", "!", "—", "`"):
            with self.subTest(punctuation=punctuation):
                self.assertEqual(
                    sanitize(f"prefix {path}{punctuation} suffix"),
                    f"prefix {token}{punctuation} suffix",
                )
        self.assertEqual(sanitize(f"prefix `{path}` suffix"), f"prefix `{token}` suffix")
        self.assertEqual(sanitize(f"prefix '{path}' suffix"), f"prefix '{token}' suffix")
        internal = "/var/tmp/private/name.part:part!more—tail"
        self.assertEqual(sanitize(f"prefix {internal} suffix"), f"prefix {sanitize(internal)} suffix")

    def test_path_span_redacts_quote_bytes_inside_paths_and_preserves_wrappers(self) -> None:
        def token(path: str) -> str:
            return f"<redacted-absolute-path:{sha256_bytes(path.encode('utf-8'))}>"

        single_path = "/a'b"
        double_path = '/a"b'
        direct_cases = (
            ("/'start", token("/'start")),
            ('/"start', token('/"start')),
            (single_path, token(single_path)),
            (double_path, token(double_path)),
            ("/end'", token("/end'")),
            ('/end"', token('/end"')),
            (r'/a\"b', token('/a"b')),
            (r'/a\\\"b', token('/a"b')),
            (r'/end\"', token('/end"')),
            (r'/end\\\"', token('/end"')),
            ("prefix '/a'b' suffix", "prefix '" + token(single_path) + "' suffix"),
            ('prefix "/a\\"b" suffix', 'prefix "' + token(double_path) + '" suffix'),
            ("prefix /a'b,/c\"d suffix", "prefix " + token(single_path) + "," + token('/c"d') + " suffix"),
        )
        for source, expected in direct_cases:
            with self.subTest(source=source):
                self.assertEqual(sanitize(source), expected)
                self.assertFalse(has_absolute_path(sanitize(source)))

        relative_cases = ("a'b", 'a"b', "relative path'a")
        for source in relative_cases:
            with self.subTest(relative=source):
                self.assertEqual(sanitize(source), source)
                self.assertFalse(has_absolute_path(source))

        def nested(source: str, depth: int) -> str:
            for _ in range(depth):
                source = json.dumps(source, separators=(",", ":"))
            return source

        for source, expected in direct_cases[:8]:
            for depth in (1, 2, 3):
                with self.subTest(source=source, depth=depth):
                    sanitized = sanitize(nested(source, depth))
                    self.assertEqual(sanitized, nested(expected, depth))
                    self.assertFalse(has_absolute_path(sanitized))

        for source in relative_cases:
            for depth in (1, 2, 3):
                with self.subTest(relative=source, depth=depth):
                    self.assertEqual(sanitize(nested(source, depth)), nested(source, depth))

        with tempfile.TemporaryDirectory() as temporary:
            source = "prefix '/a'b' suffix"
            expected = "prefix '" + token(single_path) + "' suffix"
            for depth in (1, 2, 3):
                encoded = nested(source, depth)
                payload = {
                    "adapter_version": "adapter-v1",
                    "result": {"outcome": "READY", "reason": encoded, "holytail": "NOT_APPLICABLE"},
                    "limitation": encoded,
                    "product_evidence": {
                        "notes": [encoded],
                        "log": [{"text": encoded}],
                        "argv": [encoded],
                        "commands": [],
                    },
                    "coverage": {"support": "supported", "status": "exercised"},
                }
                root = Path(temporary) / f"run-{depth}"
                run_dir, output, _, _ = self._run_fixture(root, json.dumps(payload).encode(), candidate=True)
                case_path = run_dir / "cases/case.json"
                case = json.loads(case_path.read_text())
                case["command"] = [encoded]
                case["expected"]["reason"] = encoded
                case["observed"]["reason"] = encoded
                case["classification"]["reason_match"] = True
                case_path.write_text(json.dumps(case))
                summary_path = run_dir / "summary.json"
                summary = json.loads(summary_path.read_text())
                summary["aggregated"] = aggregate_results([case])
                summary_path.write_text(json.dumps(summary))
                public = export_run(run_dir, output)
                validate_public_export(public)
                exported = json.loads(public["public_evidence"]["cases"]["case"]["stdout"]["content"])
                expected_encoded = nested(expected, depth)
                self.assertEqual(exported["result"]["reason"], expected_encoded)
                self.assertEqual(exported["limitation"], expected_encoded)
                self.assertEqual(exported["product_evidence"]["notes"], [expected_encoded])
                self.assertEqual(exported["product_evidence"]["log"][0]["text"], expected_encoded)
                self.assertEqual(exported["product_evidence"]["argv"], [expected_encoded])
                self.assertEqual(public["artifacts"]["cases"]["case"]["command"], [expected_encoded])
                self.assertFalse(has_absolute_path(public))

    def test_quote_boundary_oracle_exhausts_small_mixed_wrapper_matrix(self) -> None:
        def token(path: str) -> str:
            return f"<redacted-absolute-path:{sha256_bytes(path.encode('utf-8'))}>"

        def wrapper_sequences(depth: int) -> list[tuple[str, ...]]:
            if depth == 0:
                return [()]
            return [prefix + (quote,) for prefix in wrapper_sequences(depth - 1) for quote in ("'", '"')]

        wrappers = [sequence for depth in range(4) for sequence in wrapper_sequences(depth)]
        paths = (
            ("/'begin", "/'begin"),
            ('/"begin', '/"begin'),
            ("/mid'quote", "/mid'quote"),
            ('/mid"quote', '/mid"quote'),
            ("/end'", "/end'"),
            ('/end"', '/end"'),
            ("/run''quote", "/run''quote"),
            ('/run""quote', '/run""quote'),
            (r'/mid\"quote', '/mid"quote'),
            (r'/mid\\\"quote', '/mid"quote'),
            ("/unicode’quote", "/unicode’quote"),
            ('/unicode“quote”', '/unicode“quote”'),
            ("file:///var/tmp/private/name%27", "file:///var/tmp/private/name%27"),
        )
        matrix: list[tuple[str, str, str, str]] = []
        for sequence in wrappers:
            opener = "".join(sequence)
            closer = "".join(reversed(sequence))
            for source_path, canonical_path in paths:
                source = f"prefix {opener}{source_path}{closer} suffix"
                expected = f"prefix {opener}{token(canonical_path)}{closer} suffix"
                control = f"prefix {opener}relative a'b{closer} suffix"
                matrix.extend(((source, expected, "path", canonical_path), (control, control, "control", "")))
        self.assertEqual(len(wrappers), 15)
        self.assertEqual(len(paths), 13)
        self.assertEqual(len(matrix), 390)
        self.assertEqual(sum(kind == "path" for _, _, kind, _ in matrix), 195)
        self.assertEqual(sum(kind == "control" for _, _, kind, _ in matrix), 195)

        def nested(source: str, depth: int) -> str:
            for _ in range(depth):
                source = json.dumps(source, separators=(",", ":"))
            return source

        for source, expected, kind, _ in matrix:
            with self.subTest(source=source, kind=kind):
                self.assertEqual(sanitize(source), expected)
                self.assertFalse(has_absolute_path(sanitize(source)))

        with tempfile.TemporaryDirectory() as temporary:
            for depth in (1, 2, 3):
                sources = [nested(source, depth) for source, _, _, _ in matrix]
                expected_values = [nested(expected, depth) for _, expected, _, _ in matrix]
                reason = nested(json.dumps(sources, separators=(",", ":")), depth)
                expected_reason = nested(json.dumps(expected_values, separators=(",", ":")), depth)
                payload = {
                    "adapter_version": "adapter-v1",
                    "result": {"outcome": "READY", "reason": reason, "holytail": "NOT_APPLICABLE"},
                    "limitation": reason,
                    "product_evidence": {
                        "notes": sources,
                        "log": [{"text": value} for value in sources],
                        "argv": sources,
                        "commands": [],
                    },
                    "coverage": {"support": "supported", "status": "exercised"},
                }
                root = Path(temporary) / f"run-{depth}"
                run_dir, output, _, _ = self._run_fixture(root, json.dumps(payload).encode(), candidate=True)
                case_path = run_dir / "cases/case.json"
                case = json.loads(case_path.read_text())
                case["command"] = sources
                case["expected"]["reason"] = reason
                case["observed"]["reason"] = reason
                case["classification"]["reason_match"] = True
                case_path.write_text(json.dumps(case))
                summary_path = run_dir / "summary.json"
                summary = json.loads(summary_path.read_text())
                summary["aggregated"] = aggregate_results([case])
                summary_path.write_text(json.dumps(summary))
                public = export_run(run_dir, output)
                validate_public_export(public)
                exported = json.loads(public["public_evidence"]["cases"]["case"]["stdout"]["content"])
                self.assertEqual(exported["result"]["reason"], expected_reason)
                self.assertEqual(exported["limitation"], expected_reason)
                self.assertEqual(exported["product_evidence"]["notes"], expected_values)
                self.assertEqual([entry["text"] for entry in exported["product_evidence"]["log"]], expected_values)
                self.assertEqual(exported["product_evidence"]["argv"], expected_values)
                self.assertEqual(public["artifacts"]["cases"]["case"]["command"], expected_values)
                self.assertFalse(has_absolute_path(public))

    def test_public_validator_rejects_nested_identity_and_schema_faults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary), candidate=True)
            valid = export_run(run_dir, output)
            validate_public_export(valid)

            mutations = {
                "missing adapter manifest/implementation identity/hash": lambda value: (
                    value["artifacts"]["environment"].pop("adapter_manifest"),
                    value["artifacts"]["environment"].pop("adapter_manifest_sha256"),
                    value["artifacts"]["environment"].pop("adapter_implementation"),
                    value["artifacts"]["environment"].pop("adapter_implementation_sha256"),
                ),
                "missing binary identity/hash": lambda value: (
                    value["artifacts"]["environment"].pop("binary"),
                    value["artifacts"]["environment"].pop("binary_sha256"),
                ),
                "unknown nested field": lambda value: value["artifacts"]["summary"]["aggregated"]["metrics"]["false_acceptance"].update(unknown=1),
                "os sandbox": lambda value: value["artifacts"]["environment"].update(child_environment={"policy": "minimal-allowlist-v1", "allowlist": ["PATH"], "os_sandbox": True}),
                "aggregate wrong type": lambda value: value["artifacts"]["summary"]["aggregated"]["metrics"].update(false_acceptance="wrong"),
                "case_count mismatch": lambda value: value["artifacts"]["summary"].update(case_count=2),
                "run ID mismatch": lambda value: value["artifacts"]["cases"]["case"].update(run_id="other"),
            }
            mutations["case ID mismatch"] = lambda value: value["artifacts"]["cases"]["case"].update(case_id="other")
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    candidate = copy.deepcopy(valid)
                    mutate(candidate)
                    with self.assertRaises(ValueError):
                        validate_public_export(candidate)

    def test_public_validator_binds_source_and_summary_ids_before_zero_case_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary), candidate=True)
            valid = export_run(run_dir, output)
            zero_cases = copy.deepcopy(valid)
            zero_cases["artifacts"]["cases"] = {}
            zero_cases["public_evidence"]["cases"] = {}
            zero_cases["local_raw_evidence"]["files"] = []
            zero_cases["artifacts"]["summary"].update(
                case_count=0,
                coverage={"supported_exercised_case_ids": [], "unsupported_unexercised_case_ids": []},
                aggregated=aggregate_results([]),
            )
            validate_public_export(zero_cases)
            zero_cases["source_run_id"] = "different-run"
            with self.assertRaisesRegex(ValueError, "source_run_id|summary.run_id"):
                validate_public_export(zero_cases)

        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary), candidate=False)
            environment_path = run_dir / "environment.json"
            environment = json.loads(environment_path.read_text())
            environment.update(product_commit_sha="a" * 40, evaluator_commit_sha="b" * 40)
            environment_path.write_text(json.dumps(environment))
            summary_path = run_dir / "summary.json"
            summary = json.loads(summary_path.read_text())
            summary["candidate_dependent"] = True
            summary_path.write_text(json.dumps(summary))
            with self.assertRaisesRegex(ValueError, "candidate environment|manifest|adapter"):
                export_run(run_dir, output)

        with tempfile.TemporaryDirectory() as temporary:
            run_dir, output, _, _ = self._run_fixture(Path(temporary), stdout=b"\xff")
            with self.assertRaisesRegex(ValueError, "UTF-8"):
                export_run(run_dir, output)


if __name__ == "__main__":
    unittest.main()
