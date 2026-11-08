# Decisions: Restructure the data folder and rethink the data console

Full sweep reports, read-only at master `35e5326c`: `findings/us-importers.md`, `findings/data-structure.md`, `findings/console.md`.

## Settled
- Scope is wider than the original US importer tidy: a full assessment of `data/`, UK and US, plus a rethink of the data console (user, 2026-11-08)
- Nothing is built in this chat; done means approved `issue.md` and `plan.md` with current and proposed structure, every caller of what moves, and how behaviour preservation is checked (2026-11-08)
- Two issues, planned together in this chat, with separate branches and PRs: this one for the folders, a new one for the console (user, 2026-11-08)
- `data/` is organised role first, jurisdiction second (user, 2026-11-08)
- `model_support/` and the Westminster calibrate/validate scripts move now (user, 2026-11-08). How the hashed validation report on master is handled is still open
- Issues 0003 and 0004 are not a constraint on this work: no sequencing, path or merge decision here is made for their sake, and their plans aren't edited (user, 2026-11-08)
- Console scope: navigation by family with a fixed stage order, consolidated templates, consistent URLs (28 routes, redirects on GET pages only), missing confirms and small fixes, a run log, and new Holyrood/US diagnostic views (user, 2026-11-08)

## Open
- What specifically hurts in the console today? Sets what the rethink is for and how to judge it. Put to the user 2026-11-08, not answered.
- Which issue goes first? Recommendation: folders first, so the console's internal layout mirrors the finished folders and only moves once. Put to the user, not answered.
- Hashed validation report after `model_support/` moves. Recommendation: regenerate once with the user's go-ahead (reads the DB read-only, writes only the report) and check the numbers are identical apart from source hashes and paths. Put to the user, not answered.
- Finished one-off scripts (five applied `migrate_*`, the 538 and Wikipedia converters, the retired Senate converter). Recommendation: keep them, grouped in `scripts/migrations/` and a `one_shot/` folder beside their importers, with tests kept. Put to the user, not answered. The TopoJSON builders stay either way.
- Is `core/` worth 271 rewritten imports? Recommendation: yes, as the last piece, with a test pinning `.env` to the repo root.

## Assumed
- Local `master` and `origin/master` are both `35e5326c` (user pulled during this chat; confirmed)

## Brief corrections, verified against master
- `convert_538_house.py` is still needed: `scripts/us_results/convert.py:75` imports its constant at module level, and the console imports that at startup. `convert_538_senate.py` is test-only.
- Both presidential converters are manual one-shots; only tests call them.
- `cli.py:57-60` inserts on `sys.path`; only `convert.py` and `current_senate.py` append.
- `old_data/scripts/usa/` also holds `regions`, `convert_senate_current`, `house_2026_sources` and `check_redistricting`.
- `us_electoral_votes` is live: rebuild step 2, through `migrate_us_electoral_votes.py:37`.
