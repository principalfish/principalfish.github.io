#!/usr/bin/env bash
# Lint, format and test checks for the whole repository.
#
#   ./check.sh                 run all six steps; exit non-zero if any fails
#   ./check.sh --fix           apply the safe fixes and formatting first, then check
#   ./check.sh --lint-only     the four lint steps only (no tests)
#   ./check.sh -- ARGS...      pass ARGS to pytest, e.g. -- -k model_outputs -q
#   ./check.sh --help          show this help
#
# Steps, in order: ruff check and ruff format --check (data/ and guesstheyear/),
# biome ci (all tracked JavaScript), shellcheck (tracked shell scripts outside
# data/archive), mypy + pytest (data/run_tests.sh, which skips pytest when mypy
# fails), and vitest (npm test). Every step runs even when an earlier one fails;
# the failed steps are named at the end.
#
# ruff, shellcheck, mypy and pytest come from the data virtualenv
# (data/election_data, or data/.venv); Biome and Vitest come from node_modules.
set -euo pipefail
# pytest turns Ctrl-C into an ordinary failure, so stop here rather than run
# the next step.
trap 'echo "Interrupted." >&2; exit 130' INT
cd "$(dirname "$0")"

usage() {
	# The cd above makes the script's own name the path to read.
	sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]##*/}" | sed '$d' | sed 's/^# \{0,1\}//'
}

fix=false
lint_only=false
PYTEST_ARGS=()
while (($#)); do
	case "$1" in
	--fix) fix=true ;;
	--lint-only) lint_only=true ;;
	-h | --help)
		usage
		exit 0
		;;
	--)
		shift
		PYTEST_ARGS=("$@")
		break
		;;
	*)
		echo "Unknown argument: $1 (see ./check.sh --help)" >&2
		exit 2
		;;
	esac
	shift
done
if [[ "$lint_only" == true ]] && ((${#PYTEST_ARGS[@]})); then
	echo "Arguments after -- go to pytest, which --lint-only skips (see ./check.sh --help)" >&2
	exit 2
fi

if [[ -x ./data/election_data/bin/python ]]; then
	VENV=./data/election_data
elif [[ -x ./data/.venv/bin/python ]]; then
	VENV=./data/.venv
else
	echo "Error: no virtualenv found. Expected data/election_data or data/.venv (see data/README.md)." >&2
	exit 1
fi
RUFF=$VENV/bin/ruff
SHELLCHECK=$VENV/bin/shellcheck
BIOME=./node_modules/.bin/biome

missing=false
for tool in "$RUFF" "$SHELLCHECK"; do
	if [[ ! -x "$tool" ]]; then
		echo "Error: $tool not found. Install it with: $VENV/bin/pip install -r data/requirements.txt" >&2
		missing=true
	fi
done
if [[ ! -x "$BIOME" ]]; then
	echo "Error: $BIOME not found. Install it with: npm ci" >&2
	missing=true
fi
if [[ "$lint_only" == false ]] && ! command -v npm >/dev/null; then
	echo "Error: npm not found. Install Node.js to run the Vitest step." >&2
	missing=true
fi
if [[ "$missing" == true ]]; then
	exit 1
fi

PYTHON_DIRS=(data guesstheyear)
mapfile -t SHELL_SCRIPTS < <(git ls-files '*.sh' ':!data/archive/*')

if [[ "$fix" == true ]]; then
	echo "Fixing: ruff check --fix"
	"$RUFF" check --fix "${PYTHON_DIRS[@]}" || true
	echo "Fixing: ruff format"
	"$RUFF" format "${PYTHON_DIRS[@]}" || true
	echo "Fixing: biome check --write"
	"$BIOME" check --write . || true
fi

failed=()
run_step() {
	local name=$1
	shift
	echo "Running $name..."
	if ! "$@"; then
		failed+=("$name")
	fi
}

run_step "ruff check" "$RUFF" check "${PYTHON_DIRS[@]}"
run_step "ruff format" "$RUFF" format --check "${PYTHON_DIRS[@]}"
run_step "biome" "$BIOME" ci --error-on-warnings .
run_step "shellcheck" "$SHELLCHECK" "${SHELL_SCRIPTS[@]}"
if [[ "$lint_only" == false ]]; then
	run_step "mypy + pytest" ./data/run_tests.sh "${PYTEST_ARGS[@]}"
	run_step "vitest" npm test
fi

if ((${#failed[@]})); then
	printf -v joined '%s, ' "${failed[@]}"
	echo "Failed: ${joined%, }" >&2
	exit 1
fi
if [[ "$lint_only" == true ]]; then
	echo "All lint checks passed."
else
	echo "All checks passed."
fi
