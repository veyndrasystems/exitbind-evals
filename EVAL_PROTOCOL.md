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
