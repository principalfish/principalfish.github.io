#!/usr/bin/env bash
# Set up a git worktree of this repo so its checks can run: the Python venv for
# data/ (data/election_data, from data/requirements.txt) and the npm packages.
# Safe to run again: each step is skipped when it's already up to date.
# Run from anywhere inside the worktree. PYTHON overrides the interpreter.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
main="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
cd "$root"

venv=data/election_data
if [[ ! -x $venv/bin/python ]]; then
	echo "Creating $venv with ${PYTHON:-python3.14}..."
	"${PYTHON:-python3.14}" -m venv "$venv"
fi
# A copy of the requirements last installed, so an unchanged file skips pip.
if ! cmp -s data/requirements.txt "$venv/.requirements-installed"; then
	echo "Installing data/requirements.txt..."
	"$venv/bin/python" -m pip install --quiet --disable-pip-version-check -r data/requirements.txt
	cp data/requirements.txt "$venv/.requirements-installed"
fi

if [[ ! -f node_modules/.package-lock.json || package-lock.json -nt node_modules/.package-lock.json ]]; then
	echo "Installing npm packages..."
	npm ci --no-audit --no-fund --loglevel=error
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
