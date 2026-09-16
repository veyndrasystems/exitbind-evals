# Methodology

The unit of evaluation is a case plus its paired control. A case describes an
accepted subject, an evidence transition or fault, and an expected public
Exitbind outcome. The evaluator executes a command through a supplied binary,
then compares observed JSON and process exit status to the fixed ground truth.

## Independent ground truth

Ground truth is committed before a run. It is based on deterministic state
transitions, subject identity, a known failing check, or an explicitly preserved
invariant. The product output is never used to create the expected answer. An
LLM can propose cases during corpus preparation but is not the final oracle.

## Families

The seed corpus covers:

1. false completion with a failing required check;
2. a check reused after the subject changes;
3. a semantic invariant removed while ordinary checks remain green;
4. a review reused after the subject changes;
5. reported evidence with no valid local observation;
6. partial work presented as complete;
7. an explicit configuration precedence rule violated while ordinary
   functional execution still succeeds.

Each family has a nearby valid control. The controls are necessary because a
system that rejects every result must not score well.

## Black-box boundary

The runner knows only the case schema, the adapter protocol, and documented
CLI output. It passes a binary path as an argument and never reads product
source. The adapter is an evaluator-side translation layer for public commands;
it cannot change case expectations or synthesize acceptance evidence.
Unsupported family faults remain raw observations with explicit
`unsupported`/`unexercised` coverage; they are not converted into product
false-acceptance denominators. Paired valid controls still exercise and pass
the public transition.

## Run identity

A candidate run records the exact product binary path/hash, adapter manifest
path/hash, resolved adapter implementation path/hash, both product and
evaluator commit SHAs, environment metadata, and its UTC start time. The
runner checks the implementation hash again immediately before invocation.
Raw streams remain addressable by case. Results are written to a new run
directory and are never overwritten.

## Public evidence export

`runners/export-public` produces the closed, versioned `public-export-v2`
schema. Before writing, it verifies every result's relative stdout/stderr path,
rejects path traversal and symlinks, and compares each stored SHA-256 with the
actual local file. Each valid UTF-8 stream is copied into `public_evidence` as
path-sanitized text with its original-byte and sanitized-copy SHA-256 values,
the source local-raw path/hash binding, and explicit redaction metadata.
Structured product stdout/stderr presentations and encoded-original fields are
omitted or replaced by stable placeholders in public JSON while local raw
bytes, hashes, validity/decode metadata remain reachable. Missing,
escaping, symlinked, hash-mismatched, and non-UTF-8 inputs are errors; local raw
bytes are not modified. The redactor is an absolute-path policy and does not
scan arbitrary secrets.
