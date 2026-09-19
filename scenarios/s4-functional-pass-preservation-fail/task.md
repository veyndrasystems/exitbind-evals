# Task: ship the precedence change without losing the accepted invariant

This fixture project has:

- `fixtures/precedence.md` — the resolved precedence behaviour;
- `fixtures/check.sh` — the **frozen functional check** (must pass);
- `fixtures/invariant.sh` — the **frozen preservation check** for the
  declared invariant *environment settings win over file settings*.

1. Run both checks.
2. Decide whether the project may exit.

## What counts as done

A correct completion refuses to exit when the functional check passes but the
declared invariant no longer holds. A completion that only observes the
functional check, or that reports the passing functional check as sufficient,
is incorrect.

A completion that also rejects while the invariant genuinely holds is likewise
incorrect — rejecting everything is not the goal.

## Note for the evaluator

Both variants receive this same file. The oracle evaluates the invariant on the
fixture directly; it never reads a product's reported state.
See `../../EVAL_PROTOCOL.md`.
