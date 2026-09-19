# External-Trust Evaluation Protocol

This document fixes the rules that make an Exitbind-vs-baseline comparison
trustworthy. It governs the `scenarios/` tree and any result that cites it.
It is deliberately short and normative: if a run violates a rule here, the
result is invalid regardless of what the numbers say.

## 1. What this scaffold is for

It compares two *governance mechanisms* running the *same task*:

- `baseline` — an ungoverned agent workflow (the host/model decides when work
  is done);
- `exitbind` — the same workflow with Exitbind's checked-run governance.

It is **not** a model benchmark. Model quality and governance mechanism are
separate variables and must stay separate in every report.

## 2. The oracle rule

An oracle decides `pass`, `fail`, or `inconclusive` for a scenario. The oracle:

- **judges the fixture and the recorded artifact/state**, not Exitbind's
  reported `READY`/`REFUSED`/`BLOCKED` label;
- is **deterministic** — same inputs, same verdict, no network, no model;
- is **variant-blind** — it never reads which variant produced the evidence;
- is **committed before the run** and its expected verdict is frozen in
  `scenario.json`.

Exitbind's own reported state may be *recorded* as an observation, but it can
never be the criterion. A scenario where "Exitbind said READY" is the pass
condition is not admissible.

### 2.1 Material-field consumption

An oracle must **consume** every field it relies on to decide. A field that
appears in the evidence but never changes the verdict is not evidence; it is
decoration, and an oracle that accepts it is validating the *shape* of a claim
rather than the claim.

Each oracle therefore declares an evidence contract in
`scenarios/oracles.py`:

- `material_fields` — fields the oracle must actually read. The field must be
  present, and perturbing it must move the verdict.
- `allow_missing` — fields that may legitimately be absent, each named
  explicitly. Anything else missing is a contract violation, not a pass.
- `nested` — material fields living inside a sub-object.
- `compared_blocks` — sub-objects judged key-by-key rather than as a whole.

`parse_evidence` enforces the contract at the owning boundary and fails
closed: a contract violation makes the oracle abstain (`indeterminate`), never
accept. `tests/test_scenarios.py` covers this with a mutation matrix that
perturbs each declared material field and requires the verdict to move, so a
new oracle that reads nothing cannot pass review silently.

Fields an oracle may *observe* without deciding on are declared in
`OBSERVED_ONLY_FIELDS`. Declaring a decorative field as material is a defect in
the contract, not a passing test.

### 2.2 Exit codes are integers, never booleans

An oracle that reads an exit code must reject a boolean and demand a real
integer. `False == 0` and `True == 1` in Python, so a record carrying
`"executed_exit_code": false` would otherwise read as a passing zero. A field
that *reads presence* — "did this happen" — may still test truthiness, because
a `False` there means "it did not happen" and the oracle fails closed; the
hazard applies only where a value is compared against a passing code.

### 2.3 Proof, not assertion

Where a scenario's claim is that something *executed*, the evidence must bind
the execution — a runtime binding, a contract digest, a result artifact. A bare
boolean asserting that it ran is supplied by the same caller that supplies the
rest of the record and cannot distinguish a substitution that ran from one that
was described. Prefer a field that would differ if the claim were false.

### 2.4 Abstain, never crash

Malformed evidence must produce `inconclusive`, not an exception. A raising
oracle aborts the run and hides every other scenario's result, and a crash is
not a verdict. Two boundaries hold this:

- `validate_evidence` type-checks the shapes an oracle will operate on —
  declared string fields, string lists, and lists of records — so a malformed
  record is rejected with a reason naming the offending field;
- `run_scenario` catches `BaseException` from the oracle itself and records the
  scenario as `inconclusive`, so one unreadable evidence file cannot take down
  the suite. `KeyboardInterrupt` and `SystemExit` are re-raised deliberately: a
  run the host cancelled is not a run the oracle declined to judge, and
  reporting it as `inconclusive` would misrepresent an unfinished run as a
  finished one.

A field an oracle tests for membership in a set is checked for its type first,
on the same boundary the oracle's own return value is checked on. Otherwise a
list or dict in that field raises inside the oracle — caught, but only after
the record has already been misjudged as unreadable rather than malformed.

The corresponding tests inject a raising oracle and a malformed record and
assert the other scenarios still produce results.

## 3. Symmetry rules

- Do **not** assume baseline must fail. A baseline that passes a scenario is a
  real finding, not a broken scenario.
- Do **not** encode Exitbind-specific output (reason codes, progress
  percentages, ledger layout) as the correctness oracle.
- Do **not** change pass conditions between variants. The oracle is one
  function used by both.
- Task inputs (`task.md`, fixture files, the frozen functional check) are
  byte-identical across variants. The only permitted difference is the
  presence of the governance mechanism.
- If a scenario cannot be expressed symmetrically, mark it
  `NOT_YET_RUN`/`unsupported` rather than biasing it.

## 4. Variants and plumbing

| Variant | What runs | Status today |
| --- | --- | --- |
| `fixture` | evaluator-owned deterministic double | mechanically runnable |
| `baseline` | host/model workflow with no governance | adapter work required |
| `exitbind` | host/model workflow under Exitbind governance | adapter work required |

`fixture` exists to prove the scenario, oracle, and schema are mechanically
sound. **A fixture run is not a product result and must never be reported as
one.** No frontier-model experiment is in scope for this scaffold.

## 5. Missing measurement

- A missing measurement is `null` plus a reason string. It is **never** `0`.
- `inconclusive` is a distinct outcome from `fail`. A scenario that could not
  establish its claim (host never ran, evidence missing, oracle inputs
  incomplete) is `inconclusive`, not `fail`.
- Retry counts, human interventions, elapsed time, tokens, and cost are
  recorded only when actually observed. Unavailable fields carry
  `"unavailable"` with a reason.

## 6. Reproducibility

Every result records:

- scenario `id` and `version`;
- a `scenario_digest` over the scenario definition and its fixture bytes;
- the product commit under test;
- the variant and host/model identifier;
- the oracle id, version, and verdict;
- the evaluator commit.

A result that cannot be tied to those hashes is not evidence.

An evidence-bearing run additionally refuses to start without an exact
40-hex `--evaluator-commit`, and each result records:

- `evaluator_tree` — whether the tree was clean, its digest, and any dirty
  paths;
- `oracle.implementation_digest` — the oracle code that produced the verdict;
- `oracle.inputs_digest` — the evidence the oracle actually judged;
- `oracle.evidence_ref` — a repository-relative pointer to that evidence.

### 6.1 Observation of evaluator work

`runners/run_observation.py` records what governing the evaluator's *own* work
changes, by taking a paired observation of the same kind of work under an
Exitbind-managed handle and without one. It writes
`observation-v2` JSON to a machine-local space outside this repository
(override with `EXITBIND_EVALS_OBSERVATION_SPACE`).

An observation is not a result. It compares provenance and refusal structure,
not pass rates: the two conditions exercise different work, so their outcomes
are not commensurable and no score is computed from them. Any signal that
cannot be established is recorded as `unavailable` with a reason and is never
scored as zero.

## 7. Privacy

No private machine paths, credentials, provider account data, tokens, or
private repository names may appear in a committed scenario, fixture, oracle,
or result. Local run directories are local; only sanitized digests and
redacted streams may be published. `tools/export_public.py`'s policy applies.

## 8. Scope boundaries

- Run **only** local deterministic tests for the scaffold.
- Do **not** call frontier models.
- Do **not** publish performance claims.
- Do **not** modify the Exitbind product repository from this scaffold.

## 9. Where scenarios may legitimately differ

The governance mechanism is not the only thing that can differ from a
baseline — the *evidence acquisition path* can too. A scenario declares
`shared` semantics (what both variants must satisfy) and `divergent` semantics
(where the two variants legitimately acquire the same evidence differently).
Anything not declared `shared` is a known threat to validity and must be named
in the scenario's `threats_to_validity`.
