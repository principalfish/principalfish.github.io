# master

## Status
- [x] Add DB-level by-election tagging fields (`parent_election_id`, `election_date`)
- [x] Extend DB helper API and tests for parent-linked by-elections
- [x] Export by-election overlay files keyed by base election
- [x] Wire electionmaps by-election toggle for 2024 current-parliament view
- [x] Add baseline overlay data file and manifest setting

## Notes
- Branch: `master`
- `Election` rows can now link child elections to a base election and carry an explicit event date.
- Export pipeline now emits `settings.byElectionFilesByElectionId` in `elections.json` and writes `pf-by-elections-v1` overlay payloads.
- Electionmaps loads overlay metadata per election and can patch seat winners/votes client-side without requiring a separate map topology.
- For 2024, a toolbar toggle enables/disables by-election patches; when enabled, comparison baseline is the original 2024 result to show by-election gains.
- Added static fallback file `electionmaps/data/results/by-elections-since-2024.json` (currently empty changes array) for immediate compatibility.
- Added `data/scripts/migrate_add_election_parent_fields.py` for in-place schema migration on existing DBs.
