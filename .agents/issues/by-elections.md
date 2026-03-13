# By-Elections + Current Parliament

## Status: Code complete, needs DB migration + end-to-end test

## What was done

All code changes are on the current working tree (unstaged). Files changed/added:

| File | Change |
|-|-|
| `data/models.py` | Added `parent_election_id`, `election_date` columns to `Election` |
| `data/db.py` | Updated `add_election()` to accept new fields |
| `data/scripts/export_non_simulation_elections.py` | Wires by-election parent/date into overlay; generates composite "Current Parliament" results file |
| `data/server.py` | Added `/by-elections`, `/by-elections/preview`, `/by-elections/confirm/<token>` routes |
| `data/polls/importers/by_election_import.py` (new) | Wikipedia by-election scraper |
| `data/polls/templates/by_election_form.html` (new) | Form: URL input + parent election dropdown |
| `data/polls/templates/by_election_preview.html` (new) | Preview: parsed candidates table + confirm button |
| `data/polls/templates/home.html` | Added "Import By-Election" button |
| `.agents/learnings.md` | Updated with Current Parliament notes |

## Setup steps

### 1. Apply DB migration

The `Election` model now has two new nullable columns. Run against the dev DB:

```sql
ALTER TABLE elections ADD COLUMN parent_election_id INTEGER REFERENCES elections(id);
ALTER TABLE elections ADD COLUMN election_date DATE;
```

Verify: `SELECT column_name FROM information_schema.columns WHERE table_name = 'elections' AND column_name IN ('parent_election_id', 'election_date');` should return 2 rows.

### 2. Start the data server

```bash
cd data
python server.py
```

### 3. Import a by-election

1. Go to http://localhost:5001 (or whatever port `server.py` uses)
2. Click **Import By-Election**
3. Paste a Wikipedia by-election URL, e.g. `https://en.wikipedia.org/wiki/2025_Runcorn_and_Helsby_by-election`
4. Select parent election: `2024 General Election`
5. Click **Preview Import**
6. Check the preview: constituency match, date, candidates, vote counts, party mappings
7. Click **Confirm Import**

Verify in DB:
```sql
SELECT id, name, type, parent_election_id, election_date FROM elections WHERE type = 'by_election';
SELECT v.* FROM votes v JOIN elections e ON v.election_id = e.id WHERE e.type = 'by_election';
```

### 4. Run full export

```bash
cd data
python scripts/export_non_simulation_elections.py
```

This will:
- Export the by-election overlay to `electionmaps/data/results/by-elections-<parentId>.json`
- Generate composite `electionmaps/data/results/pf-current-parliament.json` merging 2024 GE + by-election overrides
- Add "Current Parliament" entry to `electionmaps/data/elections.json` with `comparisonElectionId` pointing to 2024 GE
- Set "Current Parliament" as the default election

### 5. Verify on frontend

```bash
./server.sh
```

Open the election maps page. "Current Parliament" should appear as a standalone election link and load by default. The by-election seats should show updated winners/votes compared to the 2024 GE.

### 6. Rebuild minified JS (if needed)

```bash
npm run minify:electionmaps
```

No JS changes were made for this feature (manifest-driven), but rebuild if the min file is stale.

## How it works

"Current Parliament" is a **composite election** — the export script reads the parent GE results file, applies by-election seat overrides (winner + votes), and writes a new `pf-results-v4` file. It appears as a normal election in the manifest, not a toggle or overlay. The frontend needs no special handling.

## Follow-up

- Import additional by-elections as they occur (repeat step 3)
- Re-run export (step 4) after each batch of imports to regenerate the composite
- The overlay file (`pf-by-elections-v1` schema) is also written but not used by the frontend — it's an intermediate artifact
- **Independent/Other distinction** — the old legacy format distinguished `"other"` (named individual, e.g. Corbyn) from `"others"` (minor candidate aggregate), but both currently collapse to `party_id=7` in pf-results-v4. To restore this: add a new party row (e.g. "Independent") and patch the 8 affected 2024 GE seats, plus update the by-election importer and export logic. Deferred.
