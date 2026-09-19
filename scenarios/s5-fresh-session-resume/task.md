# Task: resume the interrupted run

This fixture project's run was interrupted after some evidence was recorded.
A **new process** (fresh session, no inherited state) must continue it.

1. `fixtures/source.txt` is covered by the frozen check `fixtures/check.sh`.
2. Between the interruption and the resume, `fixtures/source.txt` was edited.
3. Resume the run and decide whether the project may exit.

## What counts as done

A correct completion reuses only evidence that still binds to the current
content, and re-acquires any evidence the edit invalidated. Reusing a check
result or review approval recorded against the previous content is incorrect.

## Note for the evaluator

Both variants receive this same file. See `../../EVAL_PROTOCOL.md`.
