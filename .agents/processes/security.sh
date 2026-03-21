#!/usr/bin/env bash
# security.sh — Audit the repo for hardcoded credentials, secrets, and sensitive data.
#
# Usage:
#   ./security.sh [num_commits]
#
# Examples:
#   ./security.sh        # Audit all tracked source files + full git history
#   ./security.sh 5      # Audit only files changed in the last 5 commits + their diffs
#
# Claude will look for:
#   - Hardcoded API keys, passwords, tokens, private keys
#   - Connection strings with embedded credentials
#   - Secrets committed to history (even if later deleted)
#   - Sensitive data in JSON, config, or script files
#   - .gitignore gaps that leave secrets unprotected
#
# Output saved to .agents/analysis/security-YYYY-MM-DD.md

set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

# --- Locate claude binary ---
find_claude() {
  if command -v claude &>/dev/null; then
    echo "claude"
    return
  fi
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

if [[ -n "$NUM_COMMITS" ]]; then
  if ! [[ "$NUM_COMMITS" =~ ^[0-9]+$ ]] || [[ "$NUM_COMMITS" -lt 1 ]]; then
    echo "Error: [num_commits] must be a positive integer." >&2
    exit 1
  fi
fi

# --- Determine target files and diff context ---
if [[ -n "$NUM_COMMITS" ]]; then
  echo "Mode: last ${NUM_COMMITS} commits"
  CHANGED=$(git -C "$REPO_ROOT" diff --name-only HEAD~"$NUM_COMMITS"...HEAD 2>/dev/null || \
            git -C "$REPO_ROOT" diff --name-only "$(git -C "$REPO_ROOT" rev-list --max-parents=0 HEAD)"...HEAD)
  TARGET_FILES=()
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    full="$REPO_ROOT/$f"
    [[ -f "$full" ]] || continue      # skip deleted files
    case "$f" in
      *.min.js|*.min.css) continue ;;
      */vendor/*|*/node_modules/*|*/.git/*) continue ;;
      *.py|*.js|*.sh|*.json|*.yml|*.yaml|*.env*|*.cfg|*.ini|*.conf|*.toml|*.txt|*.md) ;;
      *) continue ;;
    esac
    TARGET_FILES+=("$full")
  done <<< "$CHANGED"

  DIFF_CONTEXT="$(git -C "$REPO_ROOT" log --oneline -"$NUM_COMMITS")"$'\n\n'"$(git -C "$REPO_ROOT" diff HEAD~"$NUM_COMMITS"...HEAD)"
  SCAN_SCOPE="files changed in the last ${NUM_COMMITS} commits"
else
  echo "Mode: full repo scan"
  TARGET_FILES=()
  while IFS= read -r f; do
    [[ -f "$f" ]] || continue
    case "$f" in
      *.min.js|*.min.css) continue ;;
      */vendor/*|*/node_modules/*|*/.git/*) continue ;;
      */election_data/*|*/venv/*|*/.venv/*|*/env/*) continue ;;
    esac
    TARGET_FILES+=("$f")
  done < <(git -C "$REPO_ROOT" ls-files \
    '*.py' '*.js' '*.sh' '*.json' '*.yml' '*.yaml' \
    '*.env' '*.cfg' '*.ini' '*.conf' '*.toml' '*.txt' \
    | sed "s|^|$REPO_ROOT/|")

  # Full commit log (messages only — keeps prompt size manageable)
  DIFF_CONTEXT="$(git -C "$REPO_ROOT" log --oneline)"
  SCAN_SCOPE="all tracked source files"
fi

if [[ ${#TARGET_FILES[@]} -eq 0 ]]; then
  echo "No target files found. Nothing to audit."
  exit 0
fi

echo "Files to audit (${#TARGET_FILES[@]}):"
for f in "${TARGET_FILES[@]}"; do
  echo "  ${f#$REPO_ROOT/}"
done
echo ""

# --- Build file list for prompt ---
FILE_LIST=""
for f in "${TARGET_FILES[@]}"; do
  FILE_LIST+="  ${f}"$'\n'
done

# --- Collect .gitignore for gap analysis ---
GITIGNORE_CONTENT=""
for gi in "$REPO_ROOT/.gitignore" "$REPO_ROOT/data/.gitignore"; do
  if [[ -f "$gi" ]]; then
    rel="${gi#$REPO_ROOT/}"
    GITIGNORE_CONTENT+=$'\n\n'"--- ${rel} ---"$'\n'
    GITIGNORE_CONTENT+="$(cat "$gi")"
  fi
done

# --- Output setup ---
OUTPUT_DIR="$REPO_ROOT/.agents/analysis"
mkdir -p "$OUTPUT_DIR"
OUTPUT_FILE="$OUTPUT_DIR/security-$(date '+%Y-%m-%d').md"

# --- Build prompt ---
PROMPT="$(cat <<PROMPT
You are a security auditor reviewing a web application repository for hardcoded credentials,
leaked secrets, and sensitive data exposure. This is a static site + Python/Flask data
backend + mini apps repo. Be thorough and direct. Do not soften findings.

## Scope
Auditing: ${SCAN_SCOPE}

---

## Git history / diff context

\`\`\`
${DIFF_CONTEXT}
\`\`\`

---

## .gitignore contents

${GITIGNORE_CONTENT}

---

## Files to audit

Read each of the following files using the Read tool before forming your findings.
Do not skip any file — every file must be checked.

${FILE_LIST}
---

## Your audit tasks

Perform ALL of the following checks. Produce a clearly labelled section for each.
Cite the exact file path and line content for every finding.

### 1. Hardcoded credentials
Search every file for:
- API keys, secret keys, access tokens, bearer tokens
- Passwords, passphrases, PINs
- Private keys or certificate data (PEM blocks, RSA/EC key material)
- OAuth client secrets or refresh tokens
- Database connection strings with embedded username/password
- SMTP credentials or mail server passwords
- Any variable named (case-insensitive): password, passwd, secret, api_key, apikey,
  token, auth, credential, private_key, access_key, secret_key, client_secret

For each finding:
- File and approximate line
- The exact string or pattern that triggered concern
- Severity: CRITICAL / HIGH / MEDIUM / LOW
- Whether it appears to be a real credential or a placeholder/example

### 2. Sensitive data in committed files
- Config files (.cfg, .ini, .toml, .env*) that contain real values instead of placeholders
- JSON data files that embed personal data, internal usernames, or system paths
- Shell scripts that pass secrets as command-line arguments (visible in process lists)
- Hard-coded hostnames, internal IPs, or internal service URLs that should be config

### 3. Git history exposure
Based on the commit log and diff context:
- Are there any removed lines that look like they contained credentials?
- Were secrets ever committed to history even if since deleted? (They remain in git history.)
- Any commit messages that reference secrets, passwords, or sensitive operations?

If secrets were committed to history, note that \`git filter-repo\` or BFG Repo Cleaner
is required to fully expunge them — deleting the file or line is not sufficient.

### 4. .gitignore gaps
Review the .gitignore files against the committed file list:
- Are .env files properly ignored?
- Are local config overrides (local_settings.py, config.local.*, *.local.json) ignored?
- Are database files (.db, .sqlite) ignored where appropriate?
- Are private key files (*.pem, *.key, id_rsa, *.p12) ignored?
- Are log files with potentially sensitive content ignored?
- List any file patterns that should be added to .gitignore

### 5. Code-level risks
- SQL queries built by string interpolation or % formatting (SQL injection)
- Shell commands built from user input without sanitisation (command injection)
- File paths constructed from user input without validation (path traversal)
- Any Flask routes that expose internal config, environment variables, or DB connection details
- Logging statements that may record sensitive values (passwords, tokens, PII)
- Any eval() or exec() calls on user-supplied data

### 6. Migration recommendations
For every CRITICAL or HIGH finding, provide a specific migration path:
- **Environment variables**: show the exact env var name to use and how to read it in code
- **Git history cleanup**: if a secret was ever committed, explain BFG / git filter-repo steps
- **Secrets manager**: note if the secret warrants a secrets manager (e.g. AWS Secrets Manager, HashiCorp Vault) rather than env vars
- **Rotate immediately**: flag any credential that should be rotated before any other step

### 7. Overall risk summary
Produce a prioritised list:
- CRITICAL findings (credentials in current code or history — immediate action required)
- HIGH findings (significant exposure risk)
- MEDIUM findings (hardening recommended)
- LOW / informational
- .gitignore gaps to address
- Suggested follow-up tasks

Be direct. If you find nothing concerning in a section, say "No issues found." briefly.
PROMPT
)"

# --- Run Claude ---
echo "Running security audit..."
echo "Output: ${OUTPUT_FILE#$REPO_ROOT/}"
echo "---"

{
  echo "# Security Audit"
  echo ""
  echo "_Scope: ${SCAN_SCOPE} | $(date '+%Y-%m-%d %H:%M')_"
  echo ""
  printf '%s\n' "$PROMPT" | "$CLAUDE" -p --allowedTools "Read"
} | tee "$OUTPUT_FILE"

echo ""
echo "Saved to: ${OUTPUT_FILE#$REPO_ROOT/}"
echo ""
echo "Next steps (if issues found):"
echo "  Rotate credentials:  Do this FIRST before any git cleanup"
echo "  Remove from code:    Replace hardcoded values with env var reads"
echo "  Clean git history:   pip install git-filter-repo && git filter-repo --path <file> --invert-paths"
echo "  Or use BFG:          java -jar bfg.jar --delete-files <file>"
echo "  Update .gitignore:   Add missing patterns, then git rm --cached <file>"
