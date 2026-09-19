# Exitbind Acceptance Integrity Eval

An external, black-box evaluator for the published Exitbind CLI. It asks one
narrow question: when a result appears ready to exit, does Exitbind accept the
valid path and refuse the false one?

This repository is deliberately separate from [`exitbind`](https://github.com/veyndrasystems/exitbind).
It does not import Rust modules, use test-only product backdoors, or add
benchmark behavior to Exitbind. The evaluator invokes an explicitly supplied
binary and records raw stdout/stderr, exit status, environment, and exact case
ground truth.

## Status

The v1 harness, 14-case paired seed corpus, and versioned `v0.17.0` CLI adapter
are implemented. An exact-candidate 12-case result is published in
[`results/published/`](results/published/exitbind-0.17.0-candidate-20260914-55d2-summary.md).
Publication is the accepted evaluation milestone, not an all-pass claim; the
result records its supported coverage, unsupported cases, mismatch, and
limitations.

The included fixture binary is only a deterministic runner/scorer self-test;
it is not Exitbind and its output is not a product result.

## Reproduce the harness self-test

Requirements: Python 3.11+ and a POSIX shell. No third-party Python packages
are required.

```sh
./scripts/reproduce.sh
```

Run a candidate after its exact binary and adapter inputs have been selected:

```sh
product_commit_sha=0123456789abcdef0123456789abcdef01234567
evaluator_commit_sha=abcdef0123456789abcdef0123456789abcdef01
python3 runners/run_suite.py \
  --binary /absolute/path/to/exitbind \
  --run-id exitbind-0.17.0-candidate-YYYYMMDD \
  --adapter /absolute/path/to/adapter-manifest.json \
  --product-commit-sha "$product_commit_sha" \
  --evaluator-commit-sha "$evaluator_commit_sha" \
  --out results/runs/exitbind-0.17.0-candidate-YYYYMMDD
```

Replace both example values with the exact reviewed lowercase commit SHAs for
the product and this evaluator before running. Candidate mode fails closed if
either is missing or malformed. The adapter maps each case's public protocol scenario to documented CLI
commands. It may not import product internals or alter expected ground truth.
The included adapter targets the current public CLI and remains invalidated by
any later interface or behavior change. `run_suite.py --fixture`
uses the included fixture protocol instead.

Adapter commands run with the explicit `PATH`, `LANG`, `LC_ALL`, and `TZ`
allowlist (and no other inherited host variables). This is process-environment
minimization, not an OS sandbox. Local run directories retain raw streams and
full environment paths. To prepare a selected result for public review, use:

```sh
./runners/export-public \
  --run results/runs/fixture-self-test-YYYYMMDDTHHMMSSZ \
  --out results/published/fixture-self-test.json
```

The export is a versioned `public-export-v2` artifact. It keeps local-raw
evidence references and source hashes, and adds independently inspectable
UTF-8 public stream copies. Absolute paths are deterministically redacted with
per-copy original and sanitized SHA-256 hashes; missing, escaping, symlinked,
hash-mismatched, or non-UTF-8 streams fail closed. The local run is never
modified. This redactor handles absolute paths only; it does not scan arbitrary
secrets. Structured product command `stdout`/`stderr` presentations and their
`stdout_base64`/`stderr_base64` fields are omitted or replaced by stable
placeholders before public output; their hashes, validity/decode metadata, and
local raw evidence remain bound. This also prevents paths hidden in a nested
JSON string from being recovered after reparsing. This is not arbitrary-secret
scanning or tamper resistance.

Both fixture and candidate runs bind the executed binary path and SHA-256.
Candidate runs additionally bind the adapter manifest path/hash, the resolved
implementation path/hash, and exact product/evaluator commit identities;
fixture runs carry the fixture marker and cannot be relabeled as candidate
adapter evidence. `scripts/reproduce.sh` exercises fixture export and public
validation as part of its self-test.

Validate only:

```sh
python3 tools/validate.py
```

## Evidence and scoring

Every case has independent expected outcome, reason, and exit-code fields fixed
in `cases/*.json`. A result stores hashes and paths to raw stdout/stderr, so a
summary never replaces the underlying evidence. The scorer reports separate
panels:

| Panel | Numerator / denominator | Direction |
| --- | --- | --- |
| False Exit Rejection | exercised false exits refused / exercised false exits | higher |
| False Acceptance | exercised false exits marked `READY` / exercised false exits | lower |
| Valid Exit Acceptance | valid controls marked `READY` / valid controls | higher |
| False Refusal | valid controls refused or blocked / valid controls | lower |
| Outcome classification | exact outcome matches / supported, exercised cases | higher |
| Reason classification | exact reason matches / supported, exercised cases with an expected reason | higher |
| Holytail preservation recall | measured semantic losses detected / semantic-loss cases with product Holytail measurement | higher |
| Holytail false alarms | measured preserved controls reported as loss / preservation controls with product Holytail measurement | lower |

There is no combined “magic score.” Empty denominators are reported as
`not_computable`, never as zero or one. No LLM is used as the sole oracle.
`BLOCKED` is distinct from `REFUSED`: unsupported semantic-loss and
partial-completion faults remain visible in raw results but are excluded from
product false-acceptance denominators. The REFUSED-only rejection panel must
not count BLOCKED cases as refusals.

## Corpus

`corpus/manifest.json` records three public repositories, exact commits,
license references, and read-only fetch verification. The v1 checkout contains
metadata only: no cloned source, generated patches, or large artifacts. The
seed cases are protocol-level deterministic scenarios mapped to those sources;
repository-specific adapters and any fetched working trees are candidate-run
inputs, not product files.

The current corpus has 14 cases: seven adversarial families and a nearby valid
control for each. It remains smaller than the eventual 40+ target because
padding ungrounded mutations would make the result less credible.

## External-trust scenarios

`scenarios/` is a separate, newer track from the case corpus. Each scenario
pairs a fixture with a variant-blind oracle that judges the recorded evidence
rather than Exitbind's reported label, and declares which evidence fields it
must actually consume. `EVAL_PROTOCOL.md` fixes the rules.

A scenario run is runnable today only in its `fixture` variant, which exercises
the scenario definition, its oracle, and the result schema. Fixture output
carries `result_kind: "fixture-scaffold"` and `is_product_result: false`, so it
cannot be read as a product result. The `baseline` and `exitbind` variants need
host adapters that do not exist yet and are marked `NOT_YET_RUN` rather than
estimated.

`runners/run_observation.py` records what governing the evaluator's own work
changes, under an Exitbind-managed handle versus without one. It is an
observation of provenance and refusal structure, not a score, and it writes to
a machine-local space outside this repository.

## Limitations

- A passing fixture self-test proves only the evaluator's parsing, raw evidence,
  and aggregation paths.
- A scenario run in the `fixture` variant proves the scenario, oracle, and
  schema are mechanically sound. It is not a product result, and no live
  `baseline` or `exitbind` variant has been run.
- The observation space compares provenance and refusal structure between
  governed and ungoverned evaluator work. It scores neither condition, is not
  commensurable with any panel above, and establishes nothing about the
  product's quality.
- A candidate run measures only the supplied binary, adapter, environment, and
  pinned case set; it does not establish general agent compliance, code
  quality, adoption, human time saved, or production loss avoided.
- Local files and commands are not tamper-proof against an attacker who can
  rewrite the evaluator workspace or product binary.
- Repository manifests do not imply that a repository's maintainers endorse
  this evaluation. License links are references for case preparation.
- No release or stable tag should be claimed until the exact candidate,
  adapter, raw results, environment, and accepted external milestone are
  independently reviewed.

See [methodology/acceptance-integrity.md](methodology/acceptance-integrity.md),
[methodology/scoring.md](methodology/scoring.md), and
[methodology/limitations.md](methodology/limitations.md).
