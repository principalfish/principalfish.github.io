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
"$PYTHON" -m pytest tests/ "$@"
