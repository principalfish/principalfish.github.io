# Resume point: read this first

Updated 2026-11-07 (builder). Issue state: `building`.

State checks:
- `git rev-parse poll-errors-simulation origin/poll-errors-simulation` → both `789e36ae…` (Piece 6g data).
- Builder worktree clean.

The builder's pieces are all done and pushed:
- 6a `a5cdf8e0`
- 6 data `51c31966`, after the 15/60 rerun, backup `elections-2026-11-07-183903.db.gz`, trends rebuild and export
- 6b `c7a85eab`
- 6c code `ae61f984`, data `b2c33428` (tracker starts on the 2024-07-05 election-day result)
- 6d `950b22a2` (nowcast note below the latest poll)
- 6e `b788ae28` (vote-totals Seats from the simulated median)
- 6f `8d7c9d42` (poll tracker: no blank left gutter, Chances from 2024-08-07)
- 6g code `be4222f5`, data `789e36ae` (seats as the simulated mean; 2026-10-30 rerun)

Nothing is pending. Next: Piece 7, `/publish poll-errors-simulation` from the planner chat (PR #74). See `plan.md` for the full record.

Precondition until merge: no poll imports or model runs from the main checkout's console, which still has the old 30/30 defaults and no uncertainty.
- 2026-11-07: Piece 6b committed as `c7a85eab`, so every builder piece is done and the state is `review`. PR #74 progress is ticked through 6b. Next: Phil chooses between `/review self` on the post-review commits (`a5cdf8e0`, `51c31966`, `c7a85eab`) and going straight to `/publish poll-errors-simulation`.
- 2026-11-07: **self-review 2 in progress** on `a832137d..c7a85eab` (6a, 6, 6b). Correctness and security (opus) and style and common-mistakes (sonnet) are running on fresh subagents; coverage (sonnet) waits on the builder's `coverage-changed.sh origin/master` report; a fresh verifier follows. If this chat ends first, re-run `/review self poll-errors-simulation`.
- 2026-11-07: **self-review 2 findings; Phil choosing.** Scope `a832137d..c7a85eab`. Checks: correctness and security (opus) clean; style and common-mistakes (sonnet) and coverage (sonnet, changed lines 38/38) raised low items. The verifier (opus) kept three: V1 `electionmapslogic/dom.js:59-60` comment; V2 `dom.js:67-71` `renderHeader` `@param text`; V3 `README.md:745-747` nit. It dropped CM1 (trends start), C1, C2, CM2 and CM3. Planner note beside the dropped CM1: master's trends start on **2024-07-05**, with the unpolled election-result point (Lab 411, 33.7%), and the branch's on 2024-08-07, because `trends.py` drops unpolled runs. The tracker therefore loses its election-day anchor point. This is earlier branch behaviour, not these commits, and Phil decides whether it matters.
- 2026-11-07: Phil chose all of self-review 2 plus keeping the election-day point, which becomes new **Piece 6c** in `plan.md`. Next: the builder's look-ahead on 6c's open points (a)–(c), the next-stage briefing to Phil, then the build. Then `/publish`. Pieces are now 12 for the header: 6c is 11/12 and 7 is 12/12.
- 2026-11-07: 6c choices made (`plan.md` 6c Decisions) and 6c started on Phil's "Start 6c". Next: the 6c code review request, then the trends-rebuild live step, then the data-commit review, then `/publish`.
- 2026-11-07: self-review 2 closed (`reviews/02-2026-11-07.md`); HEAD `b2c33428`; PR #74 ticked through 6c. State `review`. No pending decision. Next: Piece 7, `/publish poll-errors-simulation`, after its next-stage briefing to Phil.
- 2026-11-07: new **Piece 6d** (header note) from Phil's manual check; choices made; sent to the builder. Pieces for the header line are now 13: 6d is 12/13, 7 is 13/13. After 6d: `/publish`.
- 2026-11-07: **Piece 6e queued** (vote-totals table rework). Don't send it to the builder until 6d is committed; then give the 6e next-stage briefing with open points (a)–(c) from `plan.md`. Pieces are now 14: 6d is 12/14, 6e 13/14, 7 14/14.
- 2026-11-07: **Piece 6f queued** (poll tracker Chances blank space), after 6e. Pieces are now 15: 6d 12/15, 6e 13/15, 6f 14/15, 7 15/15.
- 2026-11-07: 6d committed as `950b22a2`; PR #74 updated. 6e choices made (`plan.md` 6e Decisions) and 6e started on Phil's "Start 6e". After 6e: the 6f briefing, then `/publish`.
- 2026-11-07: 6e approved by Phil ("Approve, commit and push"), without the orphan-JSDoc removal; the builder is committing. Next: the 6f look-ahead, then the 6f briefing.
- 2026-11-07: 6e committed as `b788ae28`; PR #74 updated. The 6f briefing has been given. Pending Phil: the Chances start (R 2024-08-07, or 07-05), the vote-only gutter (R same rule), and the start go-ahead. The builder's causes are confirmed: the fixed 70px margin, the grid drawn into the margin (`polltracker-view.js:396`), and the 07-05 domain.
- 2026-11-07: 6f started on Phil's choices (`plan.md` 6f Decisions). **6g queued** (mean seats). Pieces are now 16: 6f 14/16, 6g 15/16, 7 16/16.
- 2026-11-07: 6f approved by Phil ("Approve, commit and push"); the builder is committing. Next: the 6g look-ahead, then the 6g briefing.
- 2026-11-07: 6f committed as `8d7c9d42`; PR #74 updated. 6g choices made (`plan.md` 6g Decisions, including the tracker mean line) and 6g started on Phil's "Start 6g". Next: the 6g code review, then live steps 1–5.
- 2026-11-07: 6g code approved by Phil ("Approve, commit and push"); the builder is committing. The export fails until the 10-30 rerun and re-export. Next: live steps backup → 10-30 rerun → trends rebuild → re-export → data commit.
- 2026-11-07: 6g step 1 backup = `elections-2026-11-07-210040.db.gz` (unchanged; it includes 0002's 9 US elections, ids 106320–106328, so restore only from this or later). Step 2 (10-30 rerun) approved ("Go ahead with rerun"). 0002's planner chat has been asked to hold DB writes until 0001 says done; **message it when the rerun, rebuild and export are complete.**
- 2026-11-07: 6g step 2 done (10-30 rerun, new id 106329; identical apart from mean; export dry-run passes). Step 3 (trends rebuild) approved. Phil: include `model_output_trends_meta.json` in the data commit. Step 4 must not pull 0002's US elections into `uselectionmaps/data` (use `--current-simulation` or prove the US files are unchanged). 0002's hold is kept until the export is done.
- 2026-11-07: 6g step 3 done (byte-identical). Step 4 approved as `export_elections.py --current-simulation` (a full export now fails on this branch: 0002's US map-24 is missing, and it would pull 0002's data in). After the export: message 0002's planner chat (local_71e7d97b…) to lift the hold.
- 2026-11-07: 6g step 4 done (scoped export); 0002's hold lifted. 6g data commit approved ("Approve, commit and push"). Next: the builder commits; then all builder pieces are done → Piece 7 `/publish`.
