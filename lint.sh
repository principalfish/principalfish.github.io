#!/usr/bin/env bash
# Lint and format checks for the whole repository.
#
#   ./lint.sh          check everything; exit non-zero if any step fails
#   ./lint.sh --fix    apply the safe fixes and formatting first, then check
#   ./lint.sh --help   show this help
#
# Steps: ruff check and ruff format --check (data/ and guesstheyear/), biome ci
# (all tracked JavaScript), and shellcheck (tracked shell scripts outside
# data/archive). Every step runs even when an earlier one fails; the failed
# steps are named at the end.
#
# ruff and shellcheck come from the data virtualenv (data/election_data, or
# data/.venv); Biome comes from node_modules.
set -euo pipefail
cd "$(dirname "$0")"

usage() {
	# The cd above makes the script's own name the path to read.
	sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]##*/}" | sed '$d' | sed 's/^# \{0,1\}//'
}

fix=false
for arg in "$@"; do
	case "$arg" in
	--fix) fix=true ;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		echo "Unknown argument: $arg (see ./lint.sh --help)" >&2
		exit 2
		;;
	esac
done

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

if ((${#failed[@]})); then
	printf -v joined '%s, ' "${failed[@]}"
	echo "Failed: ${joined%, }" >&2
	exit 1
fi
echo "All lint checks passed."
