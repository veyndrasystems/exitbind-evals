#!/bin/sh
# Frozen required check for scenario s3.
# Deterministic: exits 0 when the pass marker exists.
set -eu
cd "$(dirname "$0")"
test -f pass-marker
