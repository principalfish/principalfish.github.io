# Decisions: Restructure the data folder and rethink the data console

## Settled
- Scope is wider than the original US importer tidy: a full assessment of `data/`, UK and US, plus a rethink of the data console (user, 2026-11-08)
- The US importer untangling is one input to this issue, not a separate one, unless the discussion splits it (2026-11-08)
- Nothing is built in this chat; done means approved `issue.md` and `plan.md` with current and proposed structure, every caller of what moves, and how behaviour preservation is checked (2026-11-08)

## Open
- Is this one issue or several? A folder restructure and a console UI rethink have different risk profiles and different ways of proving they worked.
- The folder restructure is behaviour-preserving; the console rethink is a deliberate UI change. What has to stay the same in the console, and what may change?
- US importer brief still to be verified against master (written before PRs 74 and 80 merged)
- Overlap with 0003 and 0004 still to be checked

## Assumed
