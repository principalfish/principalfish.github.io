# Guess the year

A static game: `index.html`, `script.js` and `styles.css` show events from a year,
decade or century and ask the player to guess it. The events come from
`challenges.json`, built from a committed SQLite database of Wikipedia history pages.

| File | What it does |
|-|-|
| `wiki.py` | Scrapes Wikipedia year, decade and century pages into `wikipedia_history.db` |
| `export.py` | Builds `challenges.json` from `wikipedia_history.db` |
| `app.py` | A small Flask preview of the database (`/api/challenge`), not used by the site |

Regenerate the challenges with the data pipeline's virtualenv:

```bash
cd guesstheyear && ../data/election_data/bin/python export.py
```
