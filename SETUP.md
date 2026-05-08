# Setup Guide

This is a repository-agnostic Claude Code workflow template. Run the setup script to configure it for your project.

## Quick Start

1. Copy this directory's contents (`.claude/`, `CLAUDE.md`, `SETUP.md`) to your project root
2. Run the setup script:
   ```bash
   python3 .claude/setup.py
   ```
3. Review the generated guides in `.claude/guides/`
4. Commit the `.claude/` directory and `CLAUDE.md`

## What Setup Configures

| Step | What it does |
|-|-|
| **Repository name** | Updates `CLAUDE.md` and `config.json` with your project name and description |
| **Languages** | Generates style guide, documentation guide, common mistakes guide, and debug logging guide based on your language conventions |
| **Component structure** | Defines what constitutes a "component" in your codebase (top-level folder, module, package, etc.) |
| **Commit message format** | Conventional commits, component-prefixed, or custom format |
| **Main branch** | The default branch for diffs and PRs (`main`, `develop`, `master`, etc.) |
| **Issue labels** | How issues are categorized (bug, enhancement, internal, etc.) |
| **Source control** | Standard git or stacked git (`stg`) for commit workflow |
| **Debug logging** | How to add temporary debug logging per language |

## Directory Structure

```
.claude/
├── config.json              # Project configuration (written by setup.py)
├── settings.json            # Claude Code permissions
├── setup.py                 # Interactive setup script
├── components/
│   ├── index.json           # Component knowledge base index
│   ├── find-relevant-files.py  # Keyword search against index
│   └── info/                # Generated documentation per source file
├── guides/                  # Style, documentation, and process guides
├── issues/
│   └── issues/              # Per-issue folders (assessments, plans, reviews)
├── skills/                  # Subagent task definitions
└── workflows/               # Multi-step workflow definitions
```

## After Setup

- **Start documenting files:** run `/learn-file <path>` on key source files to build the component knowledge base
- **Customize guides:** review and edit the generated guides in `.claude/guides/` — they are starting points based on language conventions
- **Add common mistakes:** as you discover codebase-specific pitfalls, add them to `.claude/guides/common-mistakes.md`
- **Use workflows:** see the Workers section in `CLAUDE.md` for available workflows and how to compose them
