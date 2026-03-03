# .agents docs

Internal operating notes for this repository.

## Read order

1. `00-overview.md`
2. `01-file-map.md`
3. Area docs for the subsystem being changed

## Working rules

- Keep durable internal notes in `.agents/`; keep user-facing runbooks in `README.md`.
- Keep `learnings.md` high-level (no task-by-task logs).
- Treat `.agents/issues/` as issue-level tracking: update the active branch issue file; create a new one only when the user starts a new issue.
- Resolve the issue file directly from the current branch name (for example, `issue-18` -> `.agents/issues/issue-18.md`).
