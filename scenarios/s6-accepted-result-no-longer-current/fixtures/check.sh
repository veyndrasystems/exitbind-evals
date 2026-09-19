#!/bin/sh
# Frozen functional check for scenario s6.
# Deterministic: exits 0 when the covered file carries the required marker.
set -eu
cd "$(dirname "$0")"
grep -q '^state=accepted$' covered.txt
