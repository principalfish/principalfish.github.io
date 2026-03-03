# 010 New issue kickoff

## Status
- [x] Capture issue scope and success criteria
- [x] Implement agreed changes
- [x] Validate behavior and note checks run
- [x] Update learnings with durable takeaways

## Scope
- Perform a thorough codebase audit for superfluous code, likely bugs, and modularization risks.
- Prioritize findings by user impact and implementation effort.
- Run available validation checks to ground findings in current runtime state.

## Decisions
- No production code changes in this pass; deliver audit findings first.
- Treat vendored files under `site/vendor/` as out-of-scope for quality findings unless repository integration is at fault.
- Focus recommendations on `data/`, `election-maps/`, and `guesstheyear/` user-authored code.

## Validation notes
- Ran test suite: `cd data && ./run_tests.sh`
- Result: `103 passed in 21.82s`.
- Checked diagnostics for key files (`data/server.py`, `data/db.py`, `election-maps/election-maps.js`, `guesstheyear/script.js`, `guesstheyear/app.py`): no editor-reported errors.
- Post-change validation rerun after implementing requested items 2/4/5:
	- Diagnostics: no editor errors in `data/server.py` and `guesstheyear/script.js`.
	- Tests: `cd data && ./run_tests.sh` => `103 passed`.

## Findings summary
- High: `guesstheyear/app.py` assumes a challenge row always exists; empty/invalid DB can crash `/api/challenge` due to unchecked `row` use.
- High: `data/server.py` creates a fresh SQLAlchemy engine per request via `_get_db() -> Database()`, causing avoidable connection/engine churn.
- Medium: `data/server.py` uses unbounded in-memory `PREVIEW_CACHE` without TTL/size cap, risking memory growth under repeated preview traffic.
- Medium: `guesstheyear/script.js` assigns `seed` without declaration, creating an implicit global and increasing mutation risk.
- Medium: very large mixed-responsibility modules (`election-maps/election-maps.js`, `guesstheyear/script.js`, `data/server.py`) reduce maintainability and raise regression risk.
- Low: `data/server.py` hardcodes dev secret key and runs Flask with `debug=True` in the module entrypoint; safe for local use but risky if reused in non-local deploys.

## Implemented now (user-requested 2, 4, 5)
- (2) DB lifecycle reuse: `data/server.py` now memoizes a process-level `Database` instance in `_get_db()` (`_DB` cache) instead of constructing a new engine per route call.
- (4) Implicit global fix: `guesstheyear/script.js` removed undeclared `seed` assignment by introducing `getDailyChallengeIndex(...)` with local `const` usage.
- (5) Targeted modularization: `guesstheyear/script.js` now centralizes repeated concerns into helpers:
	- daily save key/state load/save (`getDailySaveKey`, `loadDailyGameState`, `saveDailyGameState`)
	- guess-input control toggling (`setGuessControlsDisabled`)
	This reduces duplication in `init`, `handleGuess`, `viewGame`, and `resetGameState`.
