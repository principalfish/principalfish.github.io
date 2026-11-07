#!/usr/bin/env bash
# Report which lines changed on this branch no test runs, for review.
# Runs the Python tests (data/) under coverage and the Vitest suite with
# coverage, then diff-cover compares both reports with the diff against the
# base branch (default origin/master; pass another as the first argument),
# uncommitted changes included. Exits non-zero only when a test run fails;
# uncovered lines are reported, not enforced.
# Lives on the planner branch: run it with the worktree as the current
# directory, after setup-worktree.sh. Reports go under the worktree's git dir,
# so nothing shows up in git status.
set -euo pipefail

tools="$(cd "$(dirname "$0")" && pwd)"
base="${1:-origin/master}"
root="$(git rev-parse --show-toplevel)"
out="$(git rev-parse --path-format=absolute --git-dir)/coverage-reports"
cd "$root"
rm -rf "$out"
mkdir -p "$out"

echo "Python tests under coverage..."
(cd data && COVERAGE_FILE="$out/.coverage" ./election_data/bin/python -m pytest tests/ -q -p no:cacheprovider \
	--cov --cov-config="$tools/coveragerc" --cov-report="xml:$out/python.xml" --cov-report=) >"$out/pytest.log" 2>&1 \
	|| { tail -20 "$out/pytest.log"; exit 1; }

echo "Vitest under coverage..."
npx vitest run --coverage.enabled --coverage.provider=v8 --coverage.reporter=cobertura \
	--coverage.reportsDirectory="$out/js" \
	--coverage.include='electionmapslogic/**/*.js' --coverage.include='routeweather/**/*.js' \
	--coverage.include='referdle-solver/**/*.{js,mjs}' \
	--coverage.exclude='**/tests/**' --coverage.exclude='**/*.min.js' >"$out/vitest.log" 2>&1 \
	|| { tail -20 "$out/vitest.log"; exit 1; }

echo
data/election_data/bin/diff-cover "$out/python.xml" "$out/js/cobertura-coverage.xml" --compare-branch="$base"
