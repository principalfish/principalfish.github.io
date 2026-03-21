#!/usr/bin/env bash
# analysis.sh — Deep cross-check of recent commits against an issue file.
#
# Usage:
#   ./analysis.sh <num_commits> <issue_file>
#
# Example:
#   ./analysis.sh 10 .agents/issues/feat-polls.md
#
# The script builds a thorough prompt and sends it to Claude Code for analysis.
# Claude will be given the full git diff, commit log, issue description, and
# current handler source, then asked to perform a comprehensive review.

set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

# --- Locate claude binary ---
find_claude() {
  if command -v claude &>/dev/null; then
    echo "claude"
    return
  fi
  # VSCode extension install (version-agnostic)
  local bin
  bin="$(ls -t "$HOME"/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude 2>/dev/null | head -1)"
  if [[ -n "$bin" && -x "$bin" ]]; then
    echo "$bin"
    return
  fi
  echo "Error: claude binary not found. Add it to PATH or install the Claude Code VSCode extension." >&2
  exit 1
}
CLAUDE="$(find_claude)"

# --- Argument validation ---
if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <num_commits> <issue_file>" >&2
  exit 1
fi

NUM_COMMITS="$1"
ISSUE_FILE="$2"

if ! [[ "$NUM_COMMITS" =~ ^[0-9]+$ ]] || [[ "$NUM_COMMITS" -lt 1 ]]; then
  echo "Error: <num_commits> must be a positive integer." >&2
  exit 1
fi

if [[ ! -f "$ISSUE_FILE" ]]; then
  echo "Error: issue file not found: $ISSUE_FILE" >&2
  exit 1
fi

# Derive output path from issue filename stem
TASK_DESC="$(basename "$ISSUE_FILE" .md)"
OUTPUT_DIR="$REPO_ROOT/.agents/analysis"
OUTPUT_FILE="$OUTPUT_DIR/${TASK_DESC}.md"
mkdir -p "$OUTPUT_DIR"

# --- Gather context ---
ISSUE_CONTENT="$(cat "$ISSUE_FILE")"
COMMIT_LOG="$(git -C "$REPO_ROOT" log --oneline -"$NUM_COMMITS")"
COMMIT_DIFF="$(git -C "$REPO_ROOT" diff HEAD~"$NUM_COMMITS"...HEAD)"
CHANGED_FILES="$(git -C "$REPO_ROOT" diff --name-only HEAD~"$NUM_COMMITS"...HEAD)"

# Collect handler source: only include files that appear in the diff
HANDLER_SOURCE=""
for handler_file in \
  "$REPO_ROOT/data/server.py" \
  "$REPO_ROOT/electionmaps/electionmaps.js" \
  "$REPO_ROOT/electionmaps/core.js" \
  "$REPO_ROOT/site/main.js" \
  "$REPO_ROOT/guesstheyear/app.py" \
  "$REPO_ROOT/guesstheyear/script.js"; do
  [[ -f "$handler_file" ]] || continue
  rel="${handler_file#$REPO_ROOT/}"
  if echo "$CHANGED_FILES" | grep -qF "$rel"; then
    HANDLER_SOURCE+=$'\n\n'"--- $rel (full) ---"$'\n'
    HANDLER_SOURCE+="$(cat "$handler_file")"
  fi
done

# Collect test files: only include files related to changed paths
TEST_SOURCE=""
for test_glob in \
  "$REPO_ROOT/data/tests/"*.py \
  "$REPO_ROOT/tests/"*.js \
  "$REPO_ROOT/tests/"*.test.js; do
  for f in $test_glob; do
    [[ -f "$f" ]] || continue
    rel="${f#$REPO_ROOT/}"
    # Include if any changed file shares a directory prefix with this test
    changed_dir="$(echo "$CHANGED_FILES" | awk -F/ '{print $1}' | sort -u)"
    test_dir="$(echo "$rel" | awk -F/ '{print $1}')"
    if echo "$changed_dir" | grep -qF "$test_dir"; then
      TEST_SOURCE+=$'\n\n'"--- $rel ---"$'\n'
      TEST_SOURCE+="$(cat "$f")"
    fi
  done
done

# Collect agent docs: always include core docs; include area docs only if relevant
AGENT_DOCS=""
for doc in \
  "$REPO_ROOT/.agents/00-overview.md" \
  "$REPO_ROOT/.agents/coding-standards.md"; do
  [[ -f "$doc" ]] || continue
  rel="${doc#$REPO_ROOT/}"
  AGENT_DOCS+=$'\n\n'"--- $rel ---"$'\n'
  AGENT_DOCS+="$(cat "$doc")"
done
# Include data-specific docs only when data/ files changed
if echo "$CHANGED_FILES" | grep -q '^data/'; then
  for doc in \
    "$REPO_ROOT/.agents/20-data-core.md" \
    "$REPO_ROOT/.agents/22-data-polls.md" \
    "$REPO_ROOT/.agents/23-data-uns.md"; do
    [[ -f "$doc" ]] || continue
    rel="${doc#$REPO_ROOT/}"
    AGENT_DOCS+=$'\n\n'"--- $rel ---"$'\n'
    AGENT_DOCS+="$(cat "$doc")"
  done
fi

# --- Build prompt ---
PROMPT="$(cat <<PROMPT
You are performing an in-depth engineering review of recent commits on this repository.
Your goal is to be exhaustive. Surface every issue you find, no matter how small.

## Repo context
${AGENT_DOCS}

## Issue / branch description
The following file describes the intended work for this branch:

${ISSUE_CONTENT}

## Recent commits (last ${NUM_COMMITS})
${COMMIT_LOG}

## Full diff (last ${NUM_COMMITS} commits)
\`\`\`diff
${COMMIT_DIFF}
\`\`\`

## Changed files
${CHANGED_FILES}

## Handler source (Flask routes + JS handlers)
${HANDLER_SOURCE}

## Test suite
${TEST_SOURCE}

---

## Your analysis tasks

Perform ALL of the following checks. For each, produce a clearly labelled section with
your findings. Be specific — cite file names, function names, and line content where possible.

### 1. Issue coverage
Cross-check every requirement, acceptance criterion, or stated goal in the issue file
against the commits and diff. For each requirement:
- Is it addressed by the diff? Which commit/file?
- Is it fully addressed or only partially?
- Is anything from the issue description missing from the implementation?

### 2. Handler path audit
For every Flask route and every significant JS event handler / callback in the handler
source files:
- Is the handler present in the diff or unchanged from before?
- If changed: does the change match the issue intent?
- Are all route parameters validated / type-checked?
- Are all error paths handled (404, 422, 500, DB errors, missing data)?
- Are there any unguarded assumptions (e.g. assumes non-empty list, assumes column exists)?
- Are all return paths typed correctly per the coding standards (mypy strict)?

### 3. Regression check
Review the diff for changes that could break existing behaviour:
- Renamed or removed functions/routes that may be called elsewhere.
- Changed return shapes that callers depend on.
- Changed DB queries that could return different row sets.
- Side effects introduced into previously pure functions.
- JS: changed event bindings, removed DOM ids/classes, altered render logic.
- Any import shuffled or removed that could cause a runtime ImportError.
- Any schema migration that may conflict with existing data.

### 4. Spec / test coverage gaps
Compare the changed logic against the test suite:
- Which changed functions/routes have no test coverage?
- Which edge cases are exercised? Which are missing?
- Are there tests for error paths (bad input, DB failure, empty result)?
- Does the test suite reflect the issue's acceptance criteria?
- Are there tests that were not updated but now test stale behaviour?

### 5. Coding standards compliance
Check every changed file against the project coding standards:
- Python: Google-style docstrings on all new/modified functions and classes.
- Python: mypy-strict type annotations on all parameters and return values.
- Python: Mapped[X] column style in any SQLAlchemy models.
- JS: JSDoc on all new/modified functions and handlers.
- JS: @param and @returns on public/exported functions.
- No dead code or unused imports introduced.
- No ad-hoc party key / colour lookups — must use labelParty()/colourParty().

### 6. Security and data integrity
- Are any user-supplied values interpolated into SQL without parameterisation?
- Are there any new shell-exec calls with unsanitised input?
- Does any new endpoint expose data that should be restricted?
- Could any DB write corrupt existing rows (missing WHERE, missing transaction)?

### 7. Commit hygiene
- Do commit messages accurately describe the change?
- Are there unrelated changes bundled into a single commit?
- Are there large blocks of commented-out code left behind?
- Are there debug print/console.log statements left in?

### 8. Overall verdict
Summarise:
- Critical issues (must fix before merge)
- Minor issues (should fix, non-blocking)
- Suggestions (optional improvements)
- Anything in the issue that remains unimplemented

Be direct and specific. Do not soften findings.
PROMPT
)"

# --- Run Claude ---
echo "Running deep analysis of last ${NUM_COMMITS} commits against: ${ISSUE_FILE}"
echo "Output: ${OUTPUT_FILE#$REPO_ROOT/}"
echo "---"

{
  echo "# Analysis: ${TASK_DESC}"
  echo ""
  echo "_Commits reviewed: ${NUM_COMMITS} | Issue: ${ISSUE_FILE} | $(date '+%Y-%m-%d %H:%M')_"
  echo ""
  printf '%s' "$PROMPT" | "$CLAUDE" -p
} | tee "$OUTPUT_FILE"

echo ""
echo "Saved to: ${OUTPUT_FILE#$REPO_ROOT/}"
