# AGENTS Instructions for this repository

When working on any task in this repo, follow this workflow.

## 1) Read context first

Before changing code, read `.agents/` docs relevant to the area:

- `.agents/00-overview.md`
- `.agents/01-file-map.md` (file mapping)
- `.agents/coding-standards.md` (documentation, testing, and code quality standards — always apply)
- Area docs (data core, imports, polls, UNS, tests, guesstheyear, etc.)

Use `.agents/01-file-map.md` to locate where functionality lives.

## 2) Work the issue end-to-end

- Reproduce the issue/bug where possible.
- Identify root cause (avoid surface-only fixes).
- Implement the smallest safe change that resolves the issue.
- Validate with focused checks/tests/queries.

## 2.5) Maintain issue files (not per prompt)

- Issue files in `.agents/issues/` are issue-level, not prompt-level.
- The user will explicitly indicate when a new issue starts and when an issue ends.
- By default, continue updating the active issue file for the current branch.
- Determine the active issue file from the checked-out branch name (for example, branch `issue-18` maps to `.agents/issues/issue-18.md`).
- When working on `master` or a branch without an issue number, the user will explicitly state the issue file name to use (for example `issue-19.md`).
- Create a new issue file only when the user indicates a new issue.
- File naming convention should match the branch name or the issue number stated by the user (for example `issue-13.md`, `feat-poll-ingest.md`).
- Keep the active issue file updated as work progresses (status changes, scope updates, validation notes).
- If no issue file is needed for a very small issue, no file is required.

## 3) Update learnings after every task

After completing a task, append key findings to:

- `.agents/learnings.md`

Include:
- what changed
- why it was needed
- any operational caveats discovered

## 4) Documentation expectations

- Keep `.agents/` docs accurate as understanding improves.
- If a task reveals a new subsystem behavior, update the relevant `.agents/*.md` file.
- Keep `.agents/learnings.md` concise (short takeaways, avoid long run logs).
- Promote durable operational behavior/caveats into overview docs (e.g. `.agents/00-overview.md` and area docs), not only into learnings.
- Keep `README.md` user-facing; keep deeper internal notes in `.agents/`.

## 5) Practical standards

- Prefer existing scripts and entrypoints over inventing new flows.
- Respect current DB schema and import pipelines unless task explicitly changes them.
- For `data/` tasks, verify environment assumptions (DB running, correct port/user/password, env active).
- For poll imports, be explicit about flags (`--include-unimported-parsers` on fresh DB).

## 6) Git branch and commit workflow

- Do not create new branches or push any branch without explicit user approval in the current conversation.
- Create a dedicated branch per task; do not work directly on `main`.
- Branch naming:
	- `feat/<short-topic>` for features
	- `fix/<short-topic>` for bug fixes
	- `docs/<short-topic>` for documentation-only changes
	- `chore/<short-topic>` for maintenance/refactors
- Keep commits small and scoped to one logical change.
- Commit message format:
	- `type(scope): short summary`
	- Example: `fix(polls): avoid zero-filling missing regional values`
- Before committing:
	- run focused checks relevant to the change
	- review `git diff` for unrelated edits
	- ensure docs/tests are updated where needed
- Prefer multiple clean commits over one large mixed commit.
- Open a PR against `main` with:
	- what changed
	- why it changed
	- how it was validated
	- follow-up work (if any)
