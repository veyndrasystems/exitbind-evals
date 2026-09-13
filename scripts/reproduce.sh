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
