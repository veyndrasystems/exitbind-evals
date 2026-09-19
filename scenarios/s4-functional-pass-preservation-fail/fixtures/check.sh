#!/bin/sh
# Frozen FUNCTIONAL check for scenario s4.
# Deterministic: the ordinary behaviour is intact, so this exits 0.
set -eu
cd "$(dirname "$0")"
test -f precedence.md
