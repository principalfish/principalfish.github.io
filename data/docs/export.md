# Static election-map export (manifest + files)

Use the export under `data/export/` to generate static files for `electionmaps/`
and `uselectionmaps/`.

All commands on this page run from `data/`.

## Full export (elections and latest forecasts)

```bash
./election_data/bin/python export/export_elections.py
```

The full export includes supported UK and US elections and, when present in the
database, the latest Westminster UNS simulation and the latest US presidential
forecast (the House and Senate forecasts only when `EXPORT_US_CHAMBER_FORECASTS` is
on, see below). It writes each page's manifest and data files.

Election placement is owned by `data/export/ordering.py`. The previous
generated manifest remains an ordering input; the shell does not define election
order. After initial construction and restoration, final placement applies these
rules in sequence:

1. Restore the previous manifest's ID order. New entries prefer placement after
   their first inbound comparer in built order if that comparer appears in the
   previous manifest; later comparers are not searched. Otherwise they go before
   their own remembered comparison baseline, or at the end if neither anchor is
   remembered. Ties retain built order; without a previous order, the built order
   is retained.
2. Reapply supplemental anchors. A non-null `insertBeforeId` takes precedence
   over `insertAfterId`; a missing selected anchor appends the entry.
3. List each US chamber's results newest first (`us_house` and `us_senate` only; the
   President list keeps its curated order). Newness is the year the election id starts
   with, so a future 2028 result lands at the top and an imported older year
   (`2016-us-house`) lands in its place. The Senate's `current-senate` snapshot is pinned
   directly after the newest result. The Predict / Poll tracker links are not entries:
   the left bar places them after the newest result (House) or after Current Senate
   (Senate, via `predict.chamberElectionId`).
4. Promote forecasts to their parliament's first occurrence, preserving relative
   forecast and non-forecast order without regrouping interleaved parliaments.

**US House and Senate forecasts are switched off.** The module constant
`EXPORT_US_CHAMBER_FORECASTS` in `data/export/export_elections.py` is `False`, so the
export drops the `us_house_model` / `us_senate_model` elections before any page is
written: no `current-us-house` / `current-us-senate` ("2026 Prediction") manifest entries,
no `us-house-forecast.json` / `us-senate-forecast.json` result files (the export never
deletes result files, so the two committed ones were removed by hand). The presidential
forecast is unaffected. Model runs still record to the database and the trends; to
publish chamber forecasts again, set the constant to `True` and re-export (see [2028 model run: before you start](us-results.md#2028-model-run-before-you-start)). Old `?election=current-us-house` /
`?election=current-us-senate` links no longer match an election; the page then opens that
parliament's predict-anchor election (the 2026 result) instead of showing an error.

Initial supplemental registration replaces existing IDs in place. Current
Parliament initially follows `current-prediction` (or leads if absent); restored
Westminster models initially lead, while other restored entries precede the first
Holyrood entry (or append if absent). Final rules can override those initial
positions. Comparison assignment still runs both before restoration and after
final ordering, retaining links already set. Default selection is independent of
final order and preserves a valid configured default.

Dry-run:

```bash
./election_data/bin/python export/export_elections.py --dry-run
```

Scratch export (writes every page under `DIR/<page>/data`, seeding each from the
repository's page data directory on first use; the repository is not touched):

```bash
./election_data/bin/python export/export_elections.py --output-base /tmp/export-check
```

`--output-base` works with every mode. `--output-root DIR` applies only to
`--current-simulation` (the UK page directory to write under) and is refused for the
other modes.

The export checks its inputs (pre-built maps, map templates, supplemental files) before
writing anything: a real run with any missing fails with nothing written, and a dry run
prints each as "Missing (a real export would fail): …" and carries on.

## Filtered exports

`--sections` and `--content` narrow the full export to some sections and some kinds
of content. Each takes a comma-separated list; leaving a flag out means all of it.

- `--sections`: `westminster`, `holyrood`, `house`, `senate`, `president`.
- `--content`:
  - `results`: every past election's result file, map and manifest entry;
  - `forecast`: the model forecasts' files and manifest entries;
  - `metadata`: the selected sections' regions in the manifest, and the page's
    party list (which is refreshed whenever the manifest is written).

```bash
./election_data/bin/python export/export_elections.py --sections house,senate --content results
./election_data/bin/python export/export_elections.py --content forecast --dry-run
./election_data/bin/python export/export_elections.py --output-base /tmp/export-check --sections president --content results,forecast
```

The rules:

- No flags is the full export, unchanged; so is selecting every section and every
  kind of content.
- Files and manifest entries of the sections and content not selected are left as
  they are. Only the pages of the selected sections are written.
- A selection with only `metadata` refreshes the manifest's parties and the selected
  sections' regions and writes nothing else, like `--metadata-only` limited to
  those sections.
- `forecast` applies to Westminster (the latest UNS simulation) and President only.
  For **Holyrood** the script prints a note and writes nothing: the Holyrood model run
  itself writes `results/holyrood-prediction.json` and its meta file, even when its
  console form's **Export when done** is unticked, and the export only keeps the
  manifest entry. For **House** and **Senate** it prints a note and writes nothing
  while `EXPORT_US_CHAMBER_FORECASTS` is off. When that leaves nothing to do, it
  prints `Nothing to export for this selection.`
- The first line printed is the scope (`Export scope: sections=… content=…`).
- An unknown name or an empty selection is a usage error. The flags cannot be
  combined with `--election-name`, `--current-simulation`, `--metadata-only` or
  `--output-file`; they do combine with `--dry-run` and `--output-base`.

## The Export page

The data console's **Export** page (`/export`) is a form for the same thing: tick the
sections, tick the content (**Results**, **Current forecast**, **Metadata (parties
and regions)**), choose the dry run and **Run export**. At least one of each is
needed, and the page opens with nothing ticked unless a link pre-ticks boxes with
`?sections=` and `?content=` (for example the **Export to site** button after an NYT
results import, which ticks that chamber and Results). A link never sets the dry run.

It always runs `export_elections.py` once with `--sections` (in the order above) and
`--content`, adding `--dry-run` for a dry run. Dry run is on by default and a post
that leaves it out is a dry run, so a real export needs an explicit choice of No and a
confirmation. The export runs as a background job: the page redirects to `/runs/<id>`
for the live output and the result. It holds the console's one run slot, so it is
refused while another run is in progress (see
[the console](console.md#background-runs-and-the-run-slot)).

The model run forms' **Export when done** step is separate and unchanged: the
Westminster form refreshes only the current simulation file
(`--current-simulation`), and the Holyrood and US forms run the full export.

## Targeted exports

```bash
./election_data/bin/python export/export_elections.py --election-name "2019 General Election" --output-file /tmp/2019.json
./election_data/bin/python export/export_elections.py --current-simulation --output-file /tmp/current-simulation.json
```

`--election-name` on a folded Senate special (a same-day special such as
"2020 US Senate Special Election") is refused: its results ship inside the parent
election's payload, so export the parent instead.

## Metadata-only manifest refresh

```bash
./election_data/bin/python export/export_elections.py --metadata-only
```

## Manifest, results schema and Current Senate

These live with the pages that use them: the [manifest contract](../../electionmapslogic/README.md#manifest-contract-used-by-webpage), the [results schema](../../electionmapslogic/README.md#results-schema) and the [Current Senate snapshot](../../uselectionmaps/README.md#current-senate-snapshot).

## Topo output behavior

- Bulk export writes TopoJSON per map (`maps.id`), not per election.
- Stale per-election topo files are removed during bulk export.
