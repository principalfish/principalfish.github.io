# 001 Remove seat_results and enforce task plan workflow

## Status
- [x] Define implementation approach and decisions
- [x] Update plan-process documentation rules
- [x] Refactor schema to remove `seat_results`
- [x] Refactor DB helpers/import pipeline for electorate + derived turnout
- [x] Update tests and internal docs
- [x] Run focused validation checks
- [x] Append task learnings

## Scope
1. Document mandatory task-plan files in `.agents/plans/` with naming `<index>-<short-description-of-task>.md` and in-process updates.
2. Remove `SeatResult`/`seat_results` from schema and DB helpers.
3. Store `electorate` on `Seat`.
4. Stop storing turnout and derive it from `Vote.vote_total`.
5. Update imports/tests/docs to match new model.

## Decisions
- Electorate location: `Seat.electorate`.
- Turnout: derived on demand from vote totals.
- Canonical process docs: `AGENTS.md` and `.agents/README.md`; README contains a short pointer only.
- Plan filename format: 3-digit index (e.g. `001-short-description.md`).

## Validation plan
- `pytest data/tests/test_votes.py -q`
- `pytest data/tests/test_elections.py data/tests/test_regions.py -q`
- Search for stale references to `SeatResult` / `seat_results`.

## Notes
- Keep changes surgical and avoid unrelated runtime behavior changes.
