# Exitbind v0.17.0 candidate evaluation

This is the exact-candidate v1 publication for the 12-case black-box run. The
machine-readable, path-sanitized result is [`exitbind-0.17.0-candidate-20260914-9ffd.json`](exitbind-0.17.0-candidate-20260914-9ffd-a5b3.json).

## Identity

- Run: `exitbind-0.17.0-candidate-20260914-9ffd-a5b3`
- Product commit: `9ffd135947c8236046b5b2128b03caf95e5e2987`
- Evaluator commit: `a5b3ca3a06407fbdf64b214dfe16f615388d8af1`
- Binary SHA-256: `9b94df0ccb59cc285b972d27d3ea6a7b4a80a4ae706403498e4f09dc1548ae98`
- Adapter manifest SHA-256: `92914a10ee4c1d3a5511ae97edd2d8215e7cd39a022937e3746e02023aeedaff`
- Adapter implementation SHA-256: `46c29a501067b6f610bcd0d5ae1b9b3d8dc50fd37402597e8278665ae3131268`
- Public JSON SHA-256: `7c92326b73349cd693e5192f54192741bf855b3294a0d78ceaeb2162a077a1b3`

## Observed result

The run contains 12 cases: 8 supported and exercised, and 4 unsupported and
unexercised (`partial-completion-001`, `semantic-loss-001`, `stale-check-001`,
and `stale-review-001`). Metrics use the evaluator's separate denominators:

| Panel | Result |
| --- | ---: |
| False Exit Rejection | 1/2 (50.0%) |
| False Acceptance | 1/2 (50.0%) |
| Valid Exit Acceptance | 6/6 (100.0%) |
| False Refusal | 0/6 (0.0%) |
| Outcome classification | 7/8 (87.5%) |
| Reason classification | 7/8 (87.5%) |
| Holytail preservation recall | not computable (0/0) |
| Holytail false alarms | 0/2 (0.0%) |

The supported mismatch is `reported-observed-001`: expected
`BLOCKED`/`OBSERVATION_MISSING` with exit code 3, observed
`READY`/`ACCEPTED` with exit code 0. This is preserved as an observation-only
policy limitation under the current product authority, not normalized into
success. Unsupported cases remain outside product false-acceptance and
false-refusal denominators.

## Method and limits

The evaluator used fixed, independently defined ground truth, the pinned
12-case corpus, the supplied public binary, and the versioned CLI adapter. The
export is `public-export-v2`: public streams are sanitized and bound to their
stored hashes; local raw streams remain the source evidence and are not part of
this publication. This bounded run does not establish universal effectiveness,
CI parity, adoption, production loss avoided, or a stable release.
