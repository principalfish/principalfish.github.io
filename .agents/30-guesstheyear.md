# Area: Guess The Year (`guesstheyear/`)

## Purpose

A separate mini-app/game area that appears independent from `data/` workflows.

## Components

- `app.py`: app entrypoint
- `wiki.py`: data/content retrieval helpers
- `challenges.json`: prompt/challenge dataset
- `index.html`, `script.js`, `styles.css`: frontend
- `wikipedia_history.db`: local data store/artifact

## UI notes

- `index.html` now includes a lightweight `site-topbar` with a `../` back link to the root homepage.
- The top bar styling is isolated in `styles.css` (`site-topbar*` selectors) to avoid altering game logic/UI components.

## Relationship to `data/`

- No direct dependency observed on `data/models.py` or `data/db.py`.
- Can be treated as a separate product area for most tasks.
