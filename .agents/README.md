# .agents docs

This folder contains internal area-by-area repository understanding.

Start with:
1. `00-overview.md`
2. `01-file-map.md`
3. Area-specific files for the subsystem you are changing

Always append task-level discoveries to `learnings.md`.

## Task plans

- Plan files are issue-level, not prompt-level.
- The user explicitly defines issue boundaries (when an issue starts/ends).
- Default behavior: update the most recent active issue plan file.
- Create a new plan file only when a new issue is explicitly started.
- Naming format: `<index>-<short-description-of-task>.md`.
- Use a 3-digit zero-padded index (example: `001-remove-seat-results.md`).
- Update the active issue plan throughout implementation so it reflects current status.
