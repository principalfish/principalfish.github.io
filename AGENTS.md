# AGENTS Instructions for this repository

When working on any task in this repo, follow this workflow.

## 1) Read context first

Before changing code, read `.agents/` docs relevant to the area:

- `.agents/00-overview.md`
- `.agents/01-file-map.md` (file mapping)
- Area docs (data core, imports, polls, UNS, tests, guesstheyear, etc.)

Use `.agents/01-file-map.md` to locate where functionality lives.

## 2) Work the issue end-to-end

- Reproduce the issue/bug where possible.
- Identify root cause (avoid surface-only fixes).
- Implement the smallest safe change that resolves the issue.
- Validate with focused checks/tests/queries.

## 2.5) Maintain issue plan files (not per prompt)

- Plan files in `.agents/plans/` are issue-level, not prompt-level.
- The user will explicitly indicate when a new issue starts and when an issue ends.
- By default, continue updating the most recent active issue plan file.
- Create a new plan file only when the user indicates a new issue.
- File naming convention remains `<index>-<short-description-of-task>.md` (3-digit zero-padded index, for example `001-my-issue.md`).
- Keep the active issue plan updated as work progresses (status changes, scope updates, validation notes).
- If no plan is needed for a very small issue, no plan file is required.

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
