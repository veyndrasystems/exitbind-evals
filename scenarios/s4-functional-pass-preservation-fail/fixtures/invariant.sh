#!/bin/sh
# Frozen PRESERVATION check for scenario s4.
#
# Exit 0  -> the declared invariant still holds.
# Exit 1  -> the invariant is violated (a real loss).
# Exit 99 -> the checker could not establish the invariant (use this to
#            distinguish "checker failed" from "invariant violated").
#
# The invariant: environment settings win over file settings.
set -eu
cd "$(dirname "$0")"
if [ ! -f precedence.md ]; then
  echo "invariant checker could not read precedence.md" >&2
  exit 99
fi
if grep -q '^resolution=file-wins$' precedence.md; then
  echo "invariant violated: file settings now win over environment settings" >&2
  exit 1
fi
if grep -q '^resolution=environment-wins$' precedence.md; then
  exit 0
fi
echo "invariant checker could not establish the resolution" >&2
exit 99
