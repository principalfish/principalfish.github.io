# File Map

## Root static

- `index.html`, `bio/index.html`, `electionmaps/index.html`
- `site/styles.css`, `site/main.js`, `site/vendor/`
- `electionmaps/electionmaps.js`
- `electionmaps/data/elections.json`
- `electionmaps/data/maps/`, `electionmaps/data/results/`
- `server.sh`, `404.html`, `CNAME`

## Data backend (`data/`)

- Core: `server.py`, `models.py`, `db.py`, `config.py`
- DB bootstrap: `docker-compose.yml`, `start_db.sh`
- Imports: `old_data/*`, `polls/*`
- UNS: `models/uns/*`
- Export scripts: `scripts/*`
- Tests: `tests/*`, `run_tests.sh`

## Guess The Year (`guesstheyear/`)

- App: `app.py`, `wiki.py`
- Frontend: `index.html`, `script.js`, `styles.css`
- Data: `challenges.json`, `wikipedia_history.db`
