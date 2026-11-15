#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ -x ./election_data/bin/python ]]; then
	PYTHON=./election_data/bin/python
elif [[ -x ./.venv/bin/python ]]; then
	PYTHON=./.venv/bin/python
else
	echo "Error: no virtualenv found. Expected ./election_data or ./.venv"
	exit 1
fi

echo "Running mypy..."
"$PYTHON" -m mypy

echo "Running tests..."
# pytest's temp files go to a fresh per-run folder in RAM, removed on exit;
# TESTS_TMP_ON_DISK=1 leaves them in pytest's default place on disk instead.
BASETEMP_ARGS=()
if [[ -z "${TESTS_TMP_ON_DISK:-}" && -d /dev/shm && -w /dev/shm ]]; then
	TESTS_TMP=$(mktemp -d /dev/shm/data-tests.XXXXXX)
	trap 'rm -rf "${TESTS_TMP}"' EXIT
	# Exiting from the signal traps runs the EXIT trap, so Ctrl-C and SIGTERM
	# clean up too. Bash runs them once pytest has exited.
	trap 'exit 130' INT
	trap 'exit 143' TERM
	BASETEMP_ARGS=(--basetemp="${TESTS_TMP}/t")
fi
# -n auto is half the CPUs (tests/conftest.py); a later -n or --basetemp in "$@" wins.
# The ${...+...} form keeps an empty array safe under set -u on older Bash.
"$PYTHON" -m pytest tests/ -n auto ${BASETEMP_ARGS[@]+"${BASETEMP_ARGS[@]}"} "$@"
