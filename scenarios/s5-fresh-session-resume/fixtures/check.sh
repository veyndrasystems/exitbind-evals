#!/bin/sh
# Frozen functional check for scenario s5.
# Deterministic: exits 0 when source.txt carries the required revision marker.
set -eu
cd "$(dirname "$0")"
grep -q '^revision=r2$' source.txt
