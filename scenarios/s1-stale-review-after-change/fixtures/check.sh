#!/bin/sh
# Frozen functional check for scenario s1.
# Deterministic: exits 0 when the precedence marker is present in source.txt.
set -eu
cd "$(dirname "$0")"
grep -q '^precedence=environment$' source.txt
