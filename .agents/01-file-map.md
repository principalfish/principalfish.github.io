# File Map

## Root

- `README.md` — primary documentation/runbook
- `index.html` — root static homepage entrypoint
- `imgs/logo.png` — shared logo used for favicon and homepage map-card icon
- `bio/index.html` — static bio page with profile text/photos
- `electionmaps/index.html` — static election maps landing page
- `electionmaps/data/elections.json` — election manifest (`?election=` routing + file/metadata settings)
- `electionmaps/data/maps/*.topo.json` — exported map data keyed by `maps.id`
- `electionmaps/data/results/*.json` — exported election result payloads (`pf-results-v2` compact schema)
- `site/` — root homepage assets (`tailwind-config.js`, `styles.css`, `main.js`)
- `electionmaps/electionmaps.js` — election maps interaction scaffold (zoom, pan, control hooks)
- `site/vendor/` — vendored third-party frontend assets (Tailwind runtime, Bootstrap, confetti)
- `site/assets/photos/` — local bio photo assets/placeholders
- `site/design-showcase.css` — shared styling for design concept previews
- `designs/*.html` — alternative homepage visual concept pages
- `server.sh` — static site local preview (`python3 -m http.server`)
- `404.html`, `CNAME` — static hosting artifacts
- `models/` — non-`data/` model artifacts (currently sparse)

## Data system (`data/`)

### Core runtime
- `data/server.py` — Flask app, admin/import/model UI routes
- `data/config.py` — DB env/config defaults
- `data/db.py` — SQLAlchemy DB wrapper and CRUD helpers
- `data/models.py` — SQLAlchemy schema (maps, regions, seats, elections, votes, polls)

### DB/bootstrap
- `data/docker-compose.yml` — PostGIS container
- `data/start_db.sh` — DB startup + port-conflict handling
- `data/init-test-db.sql` — test DB init script

### Base data import
- `data/old_data/import_topojson.py`
- `data/old_data/import_parties.py`
- `data/old_data/import_general_elections.py`
- `data/old_data/import_region_populations.py` (optional external input)

### Static export scripts
- `data/scripts/export_non_simulation_elections.py` — bulk/targeted static export + manifest generation
- `data/scripts/run_export_targets.py` — wrapper invoking targeted exports for all elections + current simulation
- `data/scripts/export_manifest_metadata.py` — updates manifest `settings` metadata only (parties/regions)
- `data/scripts/split_ukip_reform_parties.py` — DB migration script for UKIP/Reform split (`<2024` UKIP, `>=2024` Reform)

### Poll import system
- `data/polls/build_wikipedia_poll_mappings.py` — mapping source generator
- `data/polls/sync_pollsters_from_mapping.py` — pollster sync
- `data/polls/update_mapping_and_import_new.py` — orchestrator
- `data/polls/importers/*.py` — per-pollster parser/import modules
- `data/polls/mappings/` — generated mapping/registry/report files
- `data/update_polls.sh` — convenience wrapper

### UNS model system
- `data/models/uns/run_uns_model.py` — single-run UNS model
- `data/models/uns/run_retrospective_uns.py` — day-by-day retrospective runner
- `data/models/uns/backfill_model_output_trends.py` — trend cache repair/backfill
- `electionmaps/data/results/model_output_trends.csv` — UNS trend cache used by poll tracker chart

### Tests
- `data/tests/*.py` — DB-backed tests (maps, seats, votes, polls, etc.)
- `data/run_tests.sh` — convenience test wrapper

## Guess The Year (`guesstheyear/`)

- `guesstheyear/app.py` — app entrypoint
- `guesstheyear/wiki.py` — Wikipedia/data helper logic
- `guesstheyear/challenges.json` — challenge content
- `guesstheyear/index.html`, `script.js`, `styles.css` — frontend
- `guesstheyear/wikipedia_history.db` — local DB artifact
