#!/usr/bin/env bash
# document.sh — Add or update detailed handler documentation across the codebase.
#
# Usage:
#   ./document.sh [num_commits]
#
# Examples:
#   ./document.sh           # Document all handler files in the repo
#   ./document.sh 5         # Document only files changed in the last 5 commits
#
# For each target file, Claude will add or update:
#   - Python: Google-style docstrings with Args/Returns/Raises blocks, type annotations
#   - JS: JSDoc blocks with @param (type + structure) and @returns, for all handlers
#
# Claude makes in-place edits to each file. Run tests / minify after.

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

# --- Argument handling ---
NUM_COMMITS="${1:-}"

# --- Determine target files ---
if [[ -n "$NUM_COMMITS" ]]; then
  if ! [[ "$NUM_COMMITS" =~ ^[0-9]+$ ]] || [[ "$NUM_COMMITS" -lt 1 ]]; then
    echo "Error: [num_commits] must be a positive integer." >&2
    exit 1
  fi
  echo "Targeting files changed in the last ${NUM_COMMITS} commits..."
  CHANGED=$(git -C "$REPO_ROOT" diff --name-only HEAD~"$NUM_COMMITS"...HEAD)
  TARGET_FILES=()
  while IFS= read -r f; do
    full="$REPO_ROOT/$f"
    [[ -f "$full" ]] || continue
    case "$f" in
      *.py|*.js) TARGET_FILES+=("$full") ;;
    esac
  done <<< "$CHANGED"
else
  echo "Targeting all Python and JS handler files in the repo..."
  # Collect handler-bearing files; skip build outputs and vendor dirs
  TARGET_FILES=()
  while IFS= read -r f; do
    case "$f" in
      # Skip minified outputs and vendor files
      *.min.js|*.min.css) continue ;;
      */vendor/*) continue ;;
      # Skip node_modules
      */node_modules/*) continue ;;
    esac
    TARGET_FILES+=("$f")
  done < <(find "$REPO_ROOT" \
    \( -name "*.py" -o -name "*.js" \) \
    -not -path "*/node_modules/*" \
    -not -path "*/.git/*" \
    -not -name "*.min.js" \
    -not -path "*/vendor/*" \
    -not -path "*/venv/*" \
    -not -path "*/.venv/*" \
    -not -path "*/env/*" \
    -not -path "*/election_data/*" \
    | sort)
fi

if [[ ${#TARGET_FILES[@]} -eq 0 ]]; then
  echo "No target files found. Nothing to document."
  exit 0
fi

echo "Files to document:"
for f in "${TARGET_FILES[@]}"; do
  echo "  ${f#$REPO_ROOT/}"
done
echo ""

# --- Load coding standards for prompt context ---
STANDARDS=""
if [[ -f "$REPO_ROOT/.agents/coding-standards.md" ]]; then
  STANDARDS="$(cat "$REPO_ROOT/.agents/coding-standards.md")"
fi

# --- Process each file ---
for FILE in "${TARGET_FILES[@]}"; do
  REL="${FILE#$REPO_ROOT/}"
  EXT="${FILE##*.}"
  echo "--- Documenting: $REL ---"

  FILE_CONTENT="$(cat "$FILE")"

  if [[ "$EXT" == "py" ]]; then
    LANG_INSTRUCTIONS="$(cat <<'PYINSTR'
This is a Python file. Apply the following documentation rules:

- Use Google-style docstrings for every function, method, and class.
- One-line docstrings are acceptable only for truly trivial helpers with obvious behaviour.
- For Flask route handlers: describe the route purpose, all query/form parameters
  (name, type, whether required, valid values or structure), what the handler returns
  (status codes, response shape), and any side effects (DB writes, subprocess calls).
- For every parameter document: its type, whether it is optional, and its expected
  structure or constraints (e.g. "ISO date string YYYY-MM-DD", "positive integer",
  "one of ['left', 'right', 'swing']").
- For return values document the type and structure of the returned data.
- Document Raises: sections for any exception types that may be raised.
- Add or update type annotations on every function parameter and return value.
  Use mypy strict style: Mapped[X] for SQLAlchemy columns, parameterised generics
  (list[str], dict[str, Any]), Optional[X] or X | None for nullable values.
- Do NOT change any logic, variable names, or import order.
- Do NOT remove any existing code.
- Do NOT add comments inline in logic — only add/update docstrings at definition sites.
PYINSTR
)"
  else
    LANG_INSTRUCTIONS="$(cat <<'JSINSTR'
This is a JavaScript file. Apply the following documentation rules:

- Add or update JSDoc blocks (/** ... */) for every function, method, and event handler.
- Single-line /** ... */ is acceptable only for trivial, self-evident helpers.
- For event handlers (addEventListener callbacks, on* properties): describe what event
  triggers the handler, what DOM state or data it reads, and what side effects it has
  (DOM mutations, API calls, state updates, re-renders).
- For every @param: include the JS type in braces, the parameter name, and a description
  that covers the expected structure if it is an object or array
  (e.g. {Object} seat - Seat record with keys: id, name, party, votes).
- Add @returns for every function that returns a meaningful value; include type and
  description of the returned structure.
- For async functions / Promises: document what the promise resolves to.
- Do NOT change any logic, variable names, or import order.
- Do NOT remove any existing code.
- Do NOT add inline comments in logic — only add/update JSDoc at definition sites.
JSINSTR
)"
  fi

  PROMPT="$(cat <<PROMPT
You are a documentation engineer. Your only task is to add or update documentation
in the file at: ${FILE}

Do not change any logic.

## Coding standards
${STANDARDS}

## Language-specific instructions
${LANG_INSTRUCTIONS}

## Task
Read the file, then write it back with documentation added or improved.
Every function, method, class, and route handler must have a docstring/JSDoc block.
If a docstring already exists and is adequate, leave it as-is.
If it is incomplete or missing parameters/returns, update it.
Do not change anything else.
PROMPT
)"

  "$CLAUDE" --print "$PROMPT" --allowedTools "Read,Write,Edit"
  echo "  Done: $REL"
done

echo ""
echo "Documentation pass complete."
echo ""
echo "Next steps:"
echo "  - Review diffs: git diff"
echo "  - Run tests:    cd '$REPO_ROOT' && npm test    # JS"
echo "  - Run mypy:     cd '$REPO_ROOT/data' && mypy . # Python"
echo "  - Rebuild:      cd '$REPO_ROOT' && npm run minify:electionmaps  # if JS changed"
