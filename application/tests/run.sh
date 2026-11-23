#!/bin/sh
# Usage: application/tests/run.sh [image]   Runs every unit test inside the built image (default eda-services:test). Run from the repo root.
set -u
IMG=${1:-eda-services:test}; rc=0
for t in test_lint test_submit test_worker test_worker_recovery test_jira test_ui test_brief; do
  echo "== $t"
  docker run --rm -v "$PWD/application/tests:/t:ro" --entrypoint python3 -e PYTHONPATH=/app "$IMG" "/t/$t.py" || rc=1
done
exit $rc
