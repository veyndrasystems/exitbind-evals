# Candidate adapters

An adapter is a small evaluator-side JSON mapping from case ID to an argv list
that exercises only the frozen, documented Exitbind CLI. The command must emit
the evaluator envelope on stdout:

```json
{
  "adapter_version": "exitbind-adapter-v1",
  "result": {"outcome": "READY|REFUSED|BLOCKED", "reason": "PUBLIC_REASON_CODE", "holytail": "LOSS|PRESERVED|NOT_APPLICABLE"},
  "product_evidence": {"commands": [{"stdout_sha256": "...", "stderr_sha256": "...", "stdout_valid_utf8": true, "stderr_valid_utf8": true}]},
  "coverage": {"support": "supported", "status": "exercised"}
}
```

The adapter may stage a case workspace using public files and commands, but it
must not import product modules, invoke test-only backdoors, rewrite expected
ground truth, or add product behavior. Keep one adapter input per exact
candidate and record both its manifest and resolved implementation SHA-256 in
the run environment. Do not create an adapter until the candidate's public CLI
and reason codes are frozen.

An adapter may mark a family `unsupported`/`unexercised` when the public CLI
cannot establish that family's semantic claim. The raw product transitions
remain reachable, and the paired valid control must still be exercised.

The candidate environment records `adapter_manifest`/
`adapter_manifest_sha256` separately from `adapter_implementation`/
`adapter_implementation_sha256`; the latter is the resolved executable whose
bytes are checked immediately before invocation. The runner supplies adapter
processes only `PATH`, `LANG`, `LC_ALL`, and `TZ`
from the host (with a standard default `PATH`). Other host variables, including
credentials, are not inherited. This does not provide OS-level sandboxing.
