# Learnings Log

Top-level durable insights only.

## Workflow

- Use `.agents/01-file-map.md` first to locate affected files quickly.
- Treat `.agents/issues/` files as issue-level tracking: keep one active file per branch and name it from the branch (for example `issue-13.md`).
- Keep user-facing docs in `README.md`; keep operational/internal detail in `.agents/`.

## Data + DB

- The base import sequence is: topojson -> parties -> general elections.
- Fresh poll imports should include `--include-unimported-parsers` to avoid skipped rows.
- Current schema expectations: electorate on `seats.electorate`; turnout derived from summed `votes.vote_total`.
- `data/start_db.sh` assumes local Docker Postgres on port `5432`; local host Postgres conflicts can block startup.

## UNS + Trends

- `run_retrospective_uns.py` resets existing model outputs by default in non-dry-run mode; use `--no-reset-existing` to preserve prior output.
- `run_uns_model.py` now enforces same-date overwrite and can auto-backfill missing dates, preventing duplicate same-day model outputs.
- `run_uns_model.py` trend-cache writes now skip appending a new date when the full seat snapshot is unchanged from the latest prior date (`TREND_CACHE_SKIP`).
- `backfill_model_output_trends.py` now also skips consecutive model elections with unchanged full seat snapshots, so rebuilt trend CSVs stay compact by default.
- Trend timelines should use canonical UNS date semantics (`UNS YYYY-MM-DD` / normalized `as_of_date`) to avoid chart drift.
- `electionmaps/data/results/model_output_trends.csv` can be safely reduced by dropping consecutive dates whose full seat snapshot is unchanged; these often represent no-new-poll periods and add chart noise/size.

## Electionmaps

- Poll tracker now uses a true date-based x-axis when trend dates are parseable ISO dates, and expands to daily timeline points with carry-forward values so missing poll days render as flat status-quo segments.
- Predict 2029 now supports shareable URL state via `predict` query param; links restore regional share overrides and England expanded/collapsed state.

## Electionmaps

- Runtime architecture is manifest-driven (`electionmaps/data/elections.json`) with data resolved by configured map/result file mappings.
- Exported results use compact schema `pf-results-v2`; frontend supports compact and legacy normalization.
- Keep map interaction state centralized and re-render from canonical seat datasets to avoid stale filters/search/highlights.
- Route/path naming is now `electionmaps` (not `election-maps`); keep links/scripts/exports/docs aligned when making path changes.

## Frontend + Assets

- Prefer local vendored assets for static reliability; bundle D3 rather than relying on jsDelivr ESM entrypoints.
- `server.sh` is the canonical local preview entrypoint and runs required frontend build steps before serving.
- Shared styles should remain centralized in `site/styles.css` to keep root, bio, and maps pages visually consistent.
- Topbar is centralized via `site/topbar.js` + `site/topbar.css`; configure per-page behavior with body data attributes instead of duplicating page-specific header markup.
- `npm run minify:electionmaps` now also builds `site/topbar.min.js` and `site/topbar.min.css`; pages should reference these minified topbar assets for served output.

## Quality + Risks

- Current baseline test status in `data/` is green (`data/run_tests.sh`).
- Main maintainability risks are large monolithic files (`electionmaps/electionmaps.js`, `data/server.py`, `guesstheyear/script.js`) and should be modularized incrementally.
- In `data/server.py`, avoid per-request DB engine creation and watch unbounded preview cache growth patterns.
