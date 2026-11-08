#!/usr/bin/env bash
# Set up a git worktree of this repo so its checks and coverage can run: the
# Python venv for data/ (data/election_data, from data/requirements.txt, plus
# the coverage tools), the npm packages, and the Vitest coverage plugin.
# Lives on the planner branch, not in the code: run it with the worktree as the
# current directory, e.g. "$(python3 ~/.claude/lib/planner.py tools)/setup-worktree.sh".
# Safe to run again: each step is skipped when it's already up to date.
# PYTHON overrides the interpreter used to create the venv.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
main="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
cd "$root"

venv=data/election_data
if [[ ! -x $venv/bin/python ]]; then
	echo "Creating $venv with ${PYTHON:-python3.14}..."
	"${PYTHON:-python3.14}" -m venv "$venv"
fi
# What was last installed, so an unchanged requirements file skips pip.
# The coverage tools are Claude workflow tools, so they're not in requirements.txt.
wanted="$(cat data/requirements.txt; printf 'pytest-cov>=7.0\ndiff-cover>=11.0\n')"
if [[ "$wanted" != "$(cat "$venv/.requirements-installed" 2>/dev/null)" ]]; then
	echo "Installing data/requirements.txt and the coverage tools..."
	"$venv/bin/python" -m pip install --quiet --disable-pip-version-check -r data/requirements.txt "pytest-cov>=7.0" "diff-cover>=11.0"
	printf '%s' "$wanted" >"$venv/.requirements-installed"
fi

# Once data/ is an editable install, this venv must point at this worktree's data/.
if [[ -f data/pyproject.toml ]]; then
	"$venv/bin/python" -m pip install --quiet --disable-pip-version-check --no-deps -e data --config-settings editable_mode=compat
fi

if [[ ! -f node_modules/.package-lock.json || package-lock.json -nt node_modules/.package-lock.json ]]; then
	echo "Installing npm packages..."
	npm ci --no-audit --no-fund --loglevel=error
fi
# The coverage plugin must match Vitest's version. --no-save keeps package.json
# and the lock file untouched; npm ci removes it again, hence the check.
vitest="$(node -p "require('./node_modules/vitest/package.json').version")"
if [[ "$(node -p "try{require('./node_modules/@vitest/coverage-v8/package.json').version}catch{''}")" != "$vitest" ]]; then
	echo "Installing @vitest/coverage-v8@$vitest..."
	npm install --no-save --no-audit --no-fund --loglevel=error "@vitest/coverage-v8@$vitest"
fi

# Gitignored files that aren't installed but copied from the main checkout.
if [[ $root != "$main" ]]; then
	for file in .env; do
		if [[ ! -e $file && -e $main/$file ]]; then
			cp "$main/$file" "$file"
			echo "Copied $file from the main checkout"
		fi
	done
fi

echo "Worktree ready: $root"
