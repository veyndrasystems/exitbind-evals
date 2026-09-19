#!/bin/sh
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python3 "$repo_dir/tools/validate.py"
run_id="fixture-self-test-$(date -u +%Y%m%dT%H%M%SZ)"
out="$repo_dir/results/runs/$run_id"
python3 "$repo_dir/runners/run_suite.py" \
  --binary "$repo_dir/tools/fixture-exitbind.py" \
  --run-id "$run_id" \
  --fixture \
  --out "$out"
public_out="$repo_dir/results/published/.fixture-$run_id.json"
python3 "$repo_dir/tools/export_public.py" --run "$out" --out "$public_out"
python3 "$repo_dir/tools/validate.py" --public-export "$public_out"
echo "fixture evidence: $out"
echo "fixture public evidence: $public_out"

# External-trust scenarios. The fixture variant exercises each scenario and its
# oracle; it is not a product result and no live adapter is invoked here. An
# evidence-bearing run is bound to the exact evaluator revision, so the harness
# refuses to run without one.
scenario_out="$repo_dir/results/scenarios/$run_id"
evaluator_commit=$(git -C "$repo_dir" rev-parse HEAD)
python3 "$repo_dir/runners/run_scenarios.py" \
  --variant fixture \
  --evaluator-commit "$evaluator_commit" \
  --out "$scenario_out"
echo "scenario evidence: $scenario_out"

# Governed-vs-ungoverned observation. Writes to a machine-local space outside
# this repository and prints the paired observation. It is observation data,
# not a published result, and it scores neither condition.
python3 "$repo_dir/runners/run_observation.py" \
  --evaluator-commit "$evaluator_commit" >/dev/null
echo "observation written under ${EXITBIND_EVALS_OBSERVATION_SPACE:-$HOME/.local/state/exitbind-evals-observation}/runs"
