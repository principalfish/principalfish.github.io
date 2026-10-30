# Plan: poll-errors-simulation

**Build model:** claude-opus-5-5. The open work is statistical modelling and gated live operations, so every piece needs frontier judgement.

## Context

Branch `poll-errors-simulation` (base `master`) holds all of the poll-error uncertainty work: 46 commits from 2026-10-12 to 2026-10-29. `master` was last merged in at `35e527a1` (2026-10-22). The work came from four separate plans. On 2026-10-30 they were consolidated into this one issue, moved here from `~/.project-ai`.

**The detail lives in the thread plans, kept unchanged** under `threads/<original-slug>/`: each plan's Context, Pieces, Verification, Notes, briefs and reviews, exactly as they were. This file is the single checklist and the index to them. When a piece starts, read its piece entry and Notes in its thread plan; don't re-derive anything the thread already settled.

| # | Thread | Folder | Done | State |
|-|-|-|-|-|
| 1 | Westminster Monte Carlo uncertainty: poll bases, n_eff/MoE, MC engine, calibration, storage, console, export, map, tracker bands | `threads/add-monte-carlo-uncertainty-to-westminster-projection/` | 12/13 | Built and committed (`4eb2646b` → `40d3933f`, plus tracker follow-ups `d50922ea`, `9bb8c2c8`). Only "Full backfill published" is open (see thread 4, Phase 5). |
| 2 | US uncertainty (House, Senate, President) and overall poll trackers | `threads/add-us-election-uncertainty-and-overall-poll-trackers/` | 16/20 | Software committed (`6c4de57d` → `03bbf46d`, review fixes `76be5ea8`). Open items are gated: calibration acceptance, certified-2026 validation, pilots/backfills, publication. |
| 3 | Anchor Senate swings to matching House national baselines (bug fix) | `threads/anchor-us-senate-swings-to-matching-house-national-baselines/` | 8/11 | Fix committed (`087afcfc`, `134d1fc4`, `282dd916`). Open: manual review of the corrected comparison, calibration acceptance, live runs. |
| 4 | Fix the branch review findings (validation harness, robustness, model changes, US fixes, backfill) | `threads/fix-poll-errors-simulation-review-findings/` | 4/18 | **Active thread.** Pieces 0/1a/1b/1c committed (`c067f150` → `92743751`). The Piece 2a experiment is committed as evidence that it failed (`ab3fc62e`). |

Reviews of the branch (all six) are in `threads/reviews/`.

### Where it stopped (updated 2026-10-30), and what's needed to resume

**Start with `resume.md`** in this folder. It has the state checks, the decision chain, the next open point and the standing constraints. In short:

**Thread 4, Piece 2a** (correlated vote-to-seat conversion error) is being worked through Pieces **2a-H1 → 2a-H3** below. **2a-H1 hasn't started.**
- The offline joint validation completed: cluster variants cover 15/20 against the unchanged 17/20 gate, and no-cluster controls cover 13/20. The decision was `stop_before_engine`.
- Phil chose to investigate more comparable history. On 2026-10-30 he decided:
  - **scope A:** 1997→2001 and 2005→2010; the 1980s/early 1990s are not included;
  - **entry "both":** the new transitions train in every original fold and are also held out as separate diagnostics;
  - **storage:** a frozen in-repo fixture; no DB write, no export.
- Don't add older outcomes to the 20-outcome gate's denominator. Don't lower the gate, integrate the failed model, or move to 2b/2c on this evidence.

Thread 4's archives of the 2a prototype were written to `/tmp/poll-errors-piece2a-prototype-complete-2026-10-29.*` and may not survive a reboot. The committed report (`data/models/westminster/uncertainty_conversion_prototype_report.json`) is the durable copy. Its file SHA-256 is `e732edbd…b317`; its internal `report_hash` field is `3ead7c58…1428`.

## Approach

Thread 4 is the controlling plan for the remaining work. It was written last, against the whole branch, and its pieces cover the model corrections every later live run depends on.

Threads 1–3 are complete apart from gated operational steps: calibration acceptance, backfills, live reruns, publication. Each needs Phil's explicit go-ahead at the time, as it always has.

Where an open checkpoint appears in two threads, both are listed below and cross-referenced, not merged, so neither thread's record changes meaning.

## Pieces

Remaining work, in order. Pieces 2a-H1 to 2a-H3 are written out in full here, because they were planned after the threads were consolidated. Every other entry's full text (Files, What, Acceptance, decisions) is in the named thread plan's `## Pieces`.

1. **Thread 4 / Piece 2a:** correlated vote-to-seat conversion error. The thread 4 entry still holds the design constraints and the evidence gate. The current route is 2a-H1 → 2a-H3; 2a itself is done only after H3 passes and the engine integration in thread 4's entry is built. Model: frontier.
   1. **2a-H1: acquire and audit 1997→2001 and 2005→2010 into a frozen fixture.**
      - Files:
        - new `data/scripts/build_westminster_historical_fixture.py`;
        - new `data/models/westminster/historical-transitions-v1.json`;
        - a fixture loader module next to `data/model_support/westminster_validation_inputs.py` (follow its loader/fingerprint pattern);
        - new tests in `data/tests/`.
      - What:
        - **Sources.** Fetch read-only into a fresh scratch dir; parse with `python -I`.
          - 2005 notional (591 England/Wales/NI seats on 2010 boundaries): `https://electionresults.parliament.uk/general-elections/7/candidacies.csv`.
          - 1997, 2001, and the actual 2005 results for Scotland's 59 seats (already on the 2010 Scottish boundaries): `https://api.parliament.uk/uk-general-elections/general-elections/42`, `/43` and `/44`.
          - Boundary documentation: `https://commonslibrary.parliament.uk/research-briefings/cbp-8647/`.
        - **Fixture contents.** Per seat: party vote totals, nation/region, and the Speaker flag. Provenance: URLs, download SHA-256s and dates. A content fingerprint over the whole fixture.
        - **Crosswalks.**
          - 2005n seat code → existing 2010 seat names on the "UK Constituencies pre 2019" map.
          - A 659-seat 1997/2001 seat list with explicit nation/region. Reject unsupported regions; never default to England.
          - The old site's constituency IDs don't encode boundary changes, so match by code and region.
        - **Party mapping.**
          - Labour Co-op → Labour.
          - Speaker excluded: Boothroyd 1997, Martin 2001/2005.
          - Referendum Party, UKIP and the NI parties map to the existing keys, aligned with `calibrate_uncertainty.py` (`PARTY_ID_ALIASES`, `DB_PARTY_NAMES`).
          - The 349 unnamed 2005 minor-party aggregates stay one "unknown other" total. Never split them, and never turn unknown into zero.
        - **Off limits.** No `elections.db` write, no export, no change to production `ELECTION_PAIRS`.
      - Depends on: none.
      - Acceptance:
        - the fixture loads and its fingerprint verifies; a malformed or tampered fixture is rejected;
        - the audit is recorded in the fixture and asserted by tests:
          - seat counts 659 (1997/2001) and 650 (2005n/2010);
          - one winner per seat;
          - candidate sums reconcile with published valid votes;
          - national party shares match published totals;
          - every 2005n seat matches a 2010 seat by code and region;
        - the nation-mapping reject is tested;
        - the export dry-run lists no new elections.
      - **Pause:** show Phil the audit numbers and any open data-quality points before H2.
   2. **2a-H2: revise the experiment contract for six transitions.**
      - **Ask Phil before starting:**
        - the weight of the new transitions (recommended: equal across all six);
        - whether their held-out folds feed any guard (RMS, stability, drift) or are report-only.
      - Files:
        - `data/model_support/westminster_conversion_validation.py`, `westminster_conversion_design.py`, `westminster_conversion_report.py`, `westminster_conversion_checkpoint.py`;
        - `data/scripts/validate_westminster_conversion.py`;
        - `data/models/westminster/conversion-prototype-method.md`;
        - the console Validation view;
        - tests.
      - What:
        - Load the fixture transitions alongside the four DB transitions, as identical cell structures.
        - Each original fold trains on its 3 original transitions plus the 2 new ones. Two extra folds hold out each new transition and are reported separately.
        - The coverage gate denominator stays the original 20 cells.
        - Lambda predictive df follows the training count (5 per original fold).
        - New statistical contract hash, and a new report file (e.g. `uncertainty_conversion_historical_report.json`). The committed 15/20 report stays untouched.
      - Depends on: 2a-H1.
      - Acceptance:
        - tests cover holdout exclusion (including the new folds), the 20-cell gate denominator, and fixture/DB cell parity on a known seat;
        - synthetic recovery passes under the new contract;
        - the method doc describes the change.
      - **Pause:** Phil's go-ahead before the batch run.
   3. **2a-H3: run the six-transition experiment and report.**
      - Files: the new report file only.
      - What:
        - Run the checkpointed eight-variant batch from `data/`: `OPENBLAS_NUM_THREADS=1 ./election_data/bin/python scripts/validate_westminster_conversion.py`, plus H2's contract flag.
        - The gates are unchanged: 17/20, the RMS predictive reference, 128/256 stability, drift ≤0.15, and lambda attribution.
        - Report every variant, both new diagnostic folds, and 2024's intervals.
      - Depends on: 2a-H2 and Phil's go-ahead.
      - Acceptance: the report is saved and hash-verified, the console route returns HTTP 200, and the results are presented to Phil.
        - **Pass:** take the W-1 decisions, then do 2a's engine integration (off by default).
        - **Fail:** the replan-with-new-assumptions question; 1983–92 becomes a candidate.
        - Adding the transitions to production `ELECTION_PAIRS` is a separate live-calibration go-ahead.
2. **Thread 4 / Pieces 2b–2f:** heavy seat-level tails; error-scale parameter uncertainty; house effects and sparse windows; nation-only parties and lead calibration; joint recalibration. Depends on 2a.
3. **Thread 4 / Piece 3a:** Westminster and US display changes.
4. **Thread 4 / Pieces 4a–4d:** small US model fixes; US national parameter uncertainty; Senate state/race allocation; correlated US state errors.
5. **Gated US calibration and validation:** thread 2's Pieces 3, 13 and 14 and thread 3's calibration acceptance, after thread 4's US pieces.
6. **Thread 4 / Piece 4e:** approved anchored Senate rerun. This also covers thread 3's live reruns.
7. **Thread 4 / Phase 5:** approved Westminster backfill and publish. This also covers thread 1's "Full backfill published".
8. **Thread 2:** publication, only with explicit authorisation.
9. **Thread 4 / Phase 6:** style cleanup.

## Checkpoints

- [x] Threads 1–4: 40 completed checkpoints, recorded with their commits in each thread plan's `## Checkpoints` (thread 1: 12, thread 2: 16, thread 3: 8, thread 4: 4).
- [ ] T4 · Piece 2a-H1: 1997→2001 and 2005→2010 fixture built and audited.
- [ ] T4 · Piece 2a-H2: six-transition experiment contract revised and tested.
- [ ] T4 · Piece 2a-H3: six-transition experiment run and reported to Phil.
- [ ] T4 · Piece 2a: Correlated vote-to-seat conversion error (ticked only after H3 passes and the engine integration is built).
- [ ] T4 · Piece 2b: Heavy seat-level tails.
- [ ] T4 · Piece 2c: Error-scale parameter uncertainty.
- [ ] T4 · Piece 2d: House effects and sparse windows.
- [ ] T4 · Piece 2e: Nation-only parties and lead calibration.
- [ ] T4 · Piece 2f: Joint recalibration.
- [ ] T4 · Piece 3a: Westminster and US display changes.
- [ ] T4 · Piece 4a: Small US model fixes.
- [ ] T4 · Piece 4b: US national parameter uncertainty.
- [ ] T4 · Piece 4c: Senate state/race allocation.
- [ ] T4 · Piece 4d: Correlated US state errors.
- [ ] T2 · Piece 3: calibration accepted (go/no-go gate); console calibration view.
- [ ] T2 · Piece 13: actual certified2026 result validation report, reviewed evaluation basis and compatible frozen inputs.
- [ ] T3 · Corrected comparison proposal manually reviewed.
- [ ] T3 · Calibration assumptions/values separately accepted, or release remains gated.
- [ ] T2 · Piece 14: actual accepted-calibration scratch pilots reviewed; separately authorised backfills done.
- [ ] T4 · Piece 4e: Approved anchored Senate rerun.
- [ ] T3 · Any live reruns/backfills/publication separately authorised. (Overlaps T4 · 4e.)
- [ ] T4 · Phase 5: Approved Westminster backfill and publish.
- [ ] T1 · Full backfill published. (Overlaps T4 · Phase 5.)
- [ ] T2 · Publication completed only with explicit authorisation, or handed over as pending.
- [ ] T4 · Phase 6: Style cleanup.

When ticking a checkpoint here, also tick it in its thread plan and add the commit there, so the two never disagree.

## Verification

From thread 4, the latest and most complete set. Run from `data/` unless noted.
- `./run_tests.sh`: strict mypy gates it, then pytest. Baseline at the last commit: mypy 304 files, 6254 tests passed.
- `./election_data/bin/python scripts/export_elections.py --dry-run`
- `./election_data/bin/python -c "from console import create_app; create_app()"`
- After model pieces: `./election_data/bin/python scripts/validate_westminster_uncertainty.py`. Compare with the committed baseline report and present the deltas.
- After front-end pieces, from the repo root: `npm test` (baseline 658 passed), `npm run minify:electionmaps`, and the jsdom harness on the Westminster and US pages.
- `git diff --check`. Stage only that piece's files, and make sure `model_output_trends.json` isn't staged unintentionally.

## Notes

- 2026-10-30: Consolidated from four `~/.project-ai` plans and six reviews into this issue; the originals were copied unchanged into `threads/`.
  - Every non-merge commit on the branch is accounted for in a thread. `d50922ea` (tracker band smoothing) isn't cited by SHA, but it sits within thread 1's tracker work, between `423a8419` and `9bb8c2c8`.
  - The branch is on origin, with 10 local commits not yet pushed (`c067f150` onward and earlier). `/builder` pushes the branch after its next approved commit.
- **Decisions to take to Phil when their piece starts**, from thread 4:
  1. W-1 cluster definition and how 2024 is weighted in the fit (2a);
  2. whether to add the W-2 regime/fragmentation scaling (2b);
  3. S-2: trailing window or common random numbers (1c; the committed tracker look-ahead fix is `f417a14e`);
  4. W-4: MC median as the headline, or shown alongside (3a);
  5. the US-2 correlation data source (4d);
  6. go-aheads for each live run: 4e and Phase 5.
- **Authorisations:** the thread Notes record approvals given in earlier sessions (for example "existing all-pieces authorization"). Under the current workflow every piece still stops for review before its commit. Live database writes, backfills, reruns and publication each need a fresh, explicit go-ahead.
- 2026-10-30: Resumed. Explained the full flow for the historical-evidence route; Phil chose **scope A** (1997→2001, 2005→2010), **entry "both"** (training augmentation in every fold plus separate held-out diagnostics; gate stays 17/20 on the original 20 cells) and **storage as a frozen in-repo fixture** (no DB write or export). Next: Piece 2a-H1 (acquire and audit both transitions into the fixture, then pause), 2a-H2 (contract revision, then pause before the run), 2a-H3 (run and report). Resume check corrected: `3ead7c58…1428` is the report's internal `report_hash` (verified), not the file's `sha256sum` (`e732edbd…b317`).
- 2026-10-30 (builder): 2a-H1 implemented and paused before commit. Deviation: 1997/2001/Scotland-2005 taken from Parliament's psephology.db at a pinned commit rather than scraped HTML (Phil accepted). Phil confirmed Referendum→other, NI Con→con, NI/Scottish Greens→green, and the 2005n unknown_other limitation. CBP-8647 returned 403; no independent national totals available (recorded as `independent: false`). `_content_hash` in `westminster_validation_inputs.py` made public (`content_hash`) for reuse. Open: 5 zero-vote 2005n party entries (see resume.md).
