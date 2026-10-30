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

### Where it stopped (2026-10-29), and what's needed to resume

**Start with `resume.md`** in this folder. It has the exact resume point: state checks, the decision chain so far, the pending decision, what to do first, and the standing constraints. In short:

The work is **stopped in thread 4, Piece 2a (correlated vote-to-seat conversion error), waiting on Phil.**
- The offline joint validation completed: cluster variants cover 15/20 against the unchanged 17/20 gate, and no-cluster controls cover 13/20. The decision was `stop_before_engine`.
- Phil chose to investigate more comparable historical evidence. Read-only research found two candidate transitions: 1997→2001 and 2005→2010, with the source limits recorded in thread 4's Notes.
- **Open question: the historical scope.** Start with 1997→2001 and 2005→2010 (recommended), or include the 1980s/early 1990s now? Phil asked for an explanation of the full flow, then said he'd come back to it.
- Don't add older outcomes to the 20-outcome gate's denominator. Don't lower the gate, integrate the failed model, or move to 2b/2c on this evidence.

Thread 4's archives of the 2a prototype were written to `/tmp/poll-errors-piece2a-prototype-complete-2026-10-29.*` and may not survive a reboot. The committed report (`data/models/westminster/uncertainty_conversion_prototype_report.json`, SHA-256 `3ead7c58…1428`) is the durable copy.

## Approach

Thread 4 is the controlling plan for the remaining work. It was written last, against the whole branch, and its pieces cover the model corrections every later live run depends on.

Threads 1–3 are complete apart from gated operational steps: calibration acceptance, backfills, live reruns, publication. Each needs Phil's explicit go-ahead at the time, as it always has.

Where an open checkpoint appears in two threads, both are listed below and cross-referenced, not merged, so neither thread's record changes meaning.

## Pieces

Remaining work, in order. Each entry's full text (Files, What, Acceptance, decisions) is in the named thread plan's `## Pieces`.

1. **Thread 4 / Piece 2a:** correlated vote-to-seat conversion error. Blocked on the historical-scope answer. Model: frontier.
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
- [ ] T4 · Piece 2a: Correlated vote-to-seat conversion error.
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
