#!/bin/sh
# Runs every repository-fact check (group A) and reports one verdict.
#
# Every check runs, even after one fails: a build log that stops at the first
# problem trains people to fix things one round-trip at a time.
#
# Usage:
#   scripts/checks/run-all.sh            # the checks that read the repository
#   scripts/checks/run-all.sh --strict   # standing-configuration reports fail too
#
# The provider-API check (pool availability) is NOT run here. It needs a
# credential and it must run on a schedule rather than only on a pull request,
# so it lives in its own workflow -- see scripts/checks/README.md.

set -u

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# Pick an interpreter by RUNNING one, not by asking whether it exists.
#
# `command -v python3` succeeds on Windows even when no Python is installed:
# the Microsoft Store ships an execution-alias stub that is a genuine file on
# PATH, and running it prints an advertisement and exits non-zero. An existence
# test therefore selects an interpreter that cannot execute anything, and the
# fallback never fires.
#
# This is the same shape as most of LESSONS.md: the check succeeded and
# verified nothing. Probe by executing.
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3 python py; do
    if "$candidate" -c 'import sys; sys.exit(0)' >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
fi
if [ -z "$PYTHON" ]; then
  echo "FAIL: no working Python 3 interpreter found (tried python3, python, py)." >&2
  echo "      Set PYTHON=/path/to/python to override." >&2
  exit 1
fi

CHECKS="
check-version-pins.py
check-duplicate-yaml-keys.py
check-ingress-ha.py
check-acme-conflict.py
check-dashboard-auth.py
check-dashboard-not-exposed.py
check-flux-lockdown-flags.py
check-tenant-service-accounts.py
check-prune-enabled.py
check-state-bucket-versioning.py
check-rule-shadowing.py
"

status=0
failed=""

for check in $CHECKS; do
    printf '\n--- %s ---\n' "$check"
    if "$PYTHON" "$DIR/$check" "$@"; then
        :
    else
        code=$?
        status=1
        if [ "$code" -eq 2 ]; then
            failed="$failed $check(error)"
        else
            failed="$failed $check"
        fi
    fi
done

printf '\n--- check-manifests-render.sh ---\n'
if ! sh "$DIR/check-manifests-render.sh" "$@"; then
    status=1
    failed="$failed check-manifests-render.sh"
fi

printf '\n=========================================\n'
if [ "$status" -eq 0 ]; then
    printf 'GUARDRAILS: PASS\n'
else
    printf 'GUARDRAILS: FAIL --%s\n' "$failed"
    printf 'Security boundaries fail. Standing configuration reports.\n'
    printf 'A line marked REPORT (config) did not cause this failure.\n'
fi
exit "$status"
