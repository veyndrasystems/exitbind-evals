# Scoring panels

For every panel, first restrict the denominator to cases with exactly
`{"support":"supported","status":"exercised"}` coverage, then apply the
panel's family and expected-field condition. A missing, malformed, timed-out,
or non-JSON result is an observed failure for execution health and does not
silently become `READY`.

```text
false_exit_rejection = REFUSED false exits / exercised false exits
false_acceptance      = READY false exits / false exits
valid_acceptance      = READY valid controls / valid controls
false_refusal         = (REFUSED + BLOCKED) valid controls / valid controls
outcome_accuracy      = exact outcome matches / supported, exercised cases
reason_accuracy       = exact reasons / supported, exercised cases with expected reasons
holytail_recall       = semantic losses detected / semantic-loss cases
holytail_false_alarm   = preserved controls reported as loss / preservation controls
```

The JSON summary exposes numerator, denominator, percentage, and
`not_computable` status for every panel. It also lists false-acceptance case
IDs and classification mismatches. No panel is collapsed into a release score.

`BLOCKED` is not `REFUSED`: a blocked or unsupported case remains in raw
evidence and does not contribute to the rejection numerator. Unsupported
semantic-loss and partial-completion faults are excluded from product
false-acceptance and refusal denominators; their paired valid controls remain
scored. A documented five-of-six rejection result therefore means five
exercised false exits were `REFUSED`, never five `REFUSED`-or-`BLOCKED` cases.
