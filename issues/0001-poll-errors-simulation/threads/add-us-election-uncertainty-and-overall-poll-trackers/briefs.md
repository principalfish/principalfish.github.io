# Stage briefs: add-us-election-uncertainty-and-overall-poll-trackers

The builder presents a piece's brief **before starting it** and waits for the user's go-ahead. Each brief is written for someone who wants to understand the stage, not just approve it.

When a brief is presented:
- refresh any worked example with real numbers from the current data;
- if the implementation diverges from the brief, update this file in the same piece.

These are starting drafts written at review time (2026-10-22). Each states:
- what the stage does and why;
- what you will be able to see in the console afterwards;
- the decisions it needs from you.

## The whole pipeline in one picture

```
polls (individual + generic-ballot aggregates)
  → evidence: who was asked (LV/RV/A), how many, aggregate or survey      [piece 2]
  → central projection (unchanged): national swing + seat-poll blend α    [existing]
  → error sizes from history: G national, H state, R race, L local        [piece 3]
  → one simulated election = central + shared/state/race draws            [piece 5]
  → count it: 435 seats | 100 senators + VP | 538 EVs                     [6, 9, 11]
  → 10,000 of those → ranges, majority/control chances                    [6, 9, 11]
  → console (every stage) → export → map + tracker                        [each piece, 8/10/12]
  → after 3 Nov 2026: score the House/Senate nowcasts against reality     [piece 13]
```

Three things differ from Westminster:
- **Evidence.** The national number is an average of aggregators, not surveys. It has no sample size, and its error is set from history directly.
- **Counting.** Senate control needs the senators not up for election, plus independents' alignment and the VP. The President counts electoral votes, not states.
- **Calibration.** The DB holds no US polls from past elections, so national and state poll errors come from a committed, sourced history table (the PollBase pattern).

## Piece 1: Contracts, audit and calibration sources

- **What:** decide the exact shape of the stored, public and trend data for each model, and how every reader tells a US record from a Westminster one. Then audit what we actually have:
  - sample sizes;
  - LV/RV labels;
  - Senate roster and specials;
  - EV tables;
  - historical results and poll averages.
- **Why:** this is a second planning round. Many names in the plan are only proposals, and calibration sources decide whether everything after piece 3 can rest on evidence or on labelled assumptions.
- **You'll see:** an audit report listing gaps and proposed repairs, plus a source inventory showing which error components can be fitted and which are assumptions.
- **Decisions:**
  - sign-off on the specific source chosen for each series (policy already decided: one per series);
  - sign-off on contracts.

  Already decided: the Westminster trend-compression rule, and the phantom-vote fix as its own commit before piece 5.

Piece-1 review handover (2026-10-22): contracts and synthetic fixtures are implemented; the read-only inventory and RCP/FEC source recommendation are in `data/models/us/uncertainty-*.md`. Source coverage is seven verified generic-ballot cycles (2012–2024), with state/Senate spot checks; complete curation and parameter fitting follow source sign-off. The live inventory confirms 14 missing-N individual polls, including eight in the last saved 60-day windows with matching selections, subject to actual contributor admission. Checks pass; no live model/data changes or commits. Await contract/source sign-off before piece 2.

## Piece 2: Evidence provenance

- **What:**
  - Record for every poll whether it is an individual survey or an aggregate, and who was asked (likely voters, registered voters, all adults).
  - Re-fetch existing polls where the importer dropped an LV/RV variant.
  - Make the model's chosen polls carry this through.
- **Why:** an aggregate snapshot is not 1,000 fresh respondents. Treating repeated snapshots as independent would shrink the error to nothing.
- **You'll see:** US poll pages show kind, population, n, its source and MoE. For aggregates, n shows "not applicable".
- **Decisions:** whether to keep both LV and RV variants or a rule for which one counts; the go-ahead for the live migration and backfill after a backup.

## Piece 3: Calibration (go/no-go)

- **What:** measure from history how wrong the inputs have been:
  - the generic-ballot average against the House vote;
  - state polling averages against results;
  - how far individual seats strayed from the uniform swing.

  Convert those into the error sizes the simulation draws, in the engine's own units.
- **Why:** every probability downstream is only as good as these numbers.
- **You'll see:** a console calibration page with each σ, its source, method, per-election values and the fitted vs assumed split.
- **Decision (gate):** accept these values and assumptions, or stop and re-scope. Stored/public odds remain gated on this. The user separately authorized synthetic/scratch kernel mechanics and realised-spread checks before acceptance (2026-10-22).

## Piece 4: Pin the Senate field per cycle

- **What:** today the Senate model works out which races are up from the current calendar year and the live roster file. After 3 Nov it would forget the Florida and Ohio specials when re-running 2026 dates. This piece moves that into a dated per-cycle config.
- **Why:** backfill and replay must give the same answer next year as today.
- **You'll see:** the run log and console name the resolved field and config version. 2026 results are unchanged.
- **Decisions:** confirm the 2026 independent alignments (Osborn: none; King/Sanders: D) and the VP bloc (R) as configured assumptions.

## Piece 5: The simulation engine (House)

- **What:** one simulated House election is the central projection plus:
  - a national error shared by all 435 districts;
  - a state error shared within a state;
  - a race error, split between the seat's own polls (weight α) and the uniform-swing fallback (weight 1−α).

  With every error set to zero it must reproduce today's projection exactly.
- **Worked example (refresh with real data):** a district with two fresh polls (α ≈ 0.67). Its national draw −1.2, state draw +0.4, race draw split 0.67·local + 0.33·fallback, ending at the projected winner and margin.
- **You'll see:** an error budget per district and nationally, and a zero-error parity check.
- **Decisions:** none expected beyond approving the brief.

## Piece 6: Counting, storing and replaying (House)

- **What:**
  - count seats (435; majority 218) and the projected national vote share;
  - store ranges and chances with each daily run, plus full detail for the latest run;
  - store a replay snapshot.

  Readers learn to tell US records apart before the first one is written, so existing US trend files stay identical.
- **You'll see:** a House hub showing stored runs and which run holds full detail.
- **Decisions:** simulation count (Westminster: 10,000) and interval (Westminster: 90%).

## Piece 7: Console results, history and explorer (House)

- **What:** results tables, odds over time, and the simulation explorer: filter simulations, open one, open one district inside it with its full decomposition.
- **You'll see:** everything above, with links back to the full set.

## Piece 8: Export, map and tracker (House)

- **What:**
  - publish `us-house-uncertainty.json` and attach it to the forecast;
  - map popups and totals show ranges;
  - the House gets the overall tracker: seats with bands, and Chances (majority / no majority).
- **You'll see:** a local preview before anything is committed or published.
- **Decisions:** tracker defaults (Westminster: seats on, vote % off, uncertainty on).

## Piece 9: Senate counting and control

- **What:**
  - put simulated winners into the full 100-member chamber;
  - count party seats and caucus seats separately;
  - apply the VP to a 50–50 tie, and report the 50–50 probability on its own;
  - keep unaligned outcomes visible as unresolved.
- **You'll see:** control arithmetic per simulation in the console. Holdovers are shown as fixed.

## Piece 10: Senate map and tracker

- **What:** "Seats up" and "Full Senate" each show their own ranges. Fix the bug where switching tabs during loading loses them. Add the full-chamber tracker.

## Piece 11: President counting

- **What:**
  - weight states by electoral votes (538; 270 to win);
  - count the national popular vote without double-counting the Maine/Nebraska districts;
  - report no-majority, including 269–269.
- **Caveat to state:** with one state poll and a hypothetical 2028 matchup, this nowcast is mostly national error plus historical state-swing variation.

## Piece 12: President tracker

- **What:** an EV tracker with bands, and Chances (majority / no majority).

## Piece 13: The 2026 result

- **What:** after the official 2026 results are imported, score the final House and Senate nowcasts and their history:
  - did 90% ranges contain the result about 90% of the time?
  - were 70% favourites right about 70% of the time?
  - did the chamber totals fall inside their ranges?
- **Why:** it is the only real out-of-sample test. One election is limited evidence, and the report says so.
- **Decisions:** whether any recalibration for 2028 is adopted (a recorded config revision).

## Piece 14: Backfill and publish

- **What:** a scratch pilot per model, then backups, then each live step and the publication, each with its own go-ahead.

### Continuation and baseline-correction brief — 2026-10-22

The user authorized sequential completion with local commits, pausing for questions. Piece 1 committed `6c4de57d`; piece 2 committed `e2a8e6fa`. First-row LV/RV selection is preserved. The separately approved schema migration is live after a verified local backup; actual polling metadata repairs remain pending.

Before calibration, the user approved a separate House baseline audit/correction preparation. It will preserve the original source snapshot, package official candidate facts and the 51 proposed district changes, document fusion/RCV/invalid-ballot conventions, and provide a dry-run-first correction tool with stale-input and transaction guards. Tests and scratch-only application precede a correction-report handover; live baseline writes still require reviewed-report approval and backup.

Piece-5 implementation handover (2026-10-23): committed `15611eed`, exact435-district/2610-row scratch central parity, fullsuite5727 passed. The budget page is read-only and explicitly provisional; actual stochastic sources remain blocked by unresolved provenance/N. The user-authorized synthetic-first staging is complete. Next piece4 pins Senate fields on calendar-year rollover (2026→2028 on1January2027), with matching runner/export context and unavailable2028 full-chamber roster failing clearly.

Piece-4 handover (2026-10-23): committed `0dcd8bb3`, exact35-race/210-row parity at both sampled history endpoints, fullsuite5755 passed. Pinned snapshot, canonical latest-run selection and forecast-specific roster export are complete. Next piece3 will produce reproducible source fits and a measured/assumed calibration report plus console view and engine recovery checks. The individual House local-poll floor is not identified by the curated evidence; a user choice is pending between labelled assumptions with sensitivity and expanding individual-poll evidence. Production odds remain gated on final calibration acceptance and valid actual contributors.

Piece-3 start (2026-10-23): user chose labelled local-error assumptions and sensitivity. Build reproducible shared calibration machinery and the first House candidate report/console, with explicit units, input-versus-output target distinctions, unchanged central results, grouped historical diagnostics and independent kernel recovery. Senate/presidential calibrations remain pending for their respective slices. Candidate values and assumptions require user acceptance before each model enables stored/public odds.
