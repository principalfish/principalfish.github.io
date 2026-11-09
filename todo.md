# Todo

Ideas and follow-ups not yet planned. When one is picked up, it goes through
`/planner build` (or `/quick-build` if small) and comes off this list.

## Pre-2010 historical Westminster maps

Added 2026-11-08. Nothing planned or started. Its own issue when picked up.

Add published historical Westminster maps before 2010 to the site: 1997, 2001,
2005, and maybe 1983–92.

What it needs:

- New TopoJSON boundaries for 1997. Source and licence not researched yet.
- For 2005, either a hybrid map (England, Wales and NI on 1997 boundaries plus
  Scotland on 2005, 646 seats) or notional results only, on the 2010 map.
- A database import of the maps, seats and elections, which the export then
  publishes.
- `map-modes-shell.json` entries, and colours for defunct parties.
- A bundle rebuild and a check of both pages.
- 1983–92 adds one more boundary set covering three elections.

Reusable work: per-seat 1997/2001/2005-notional results were fetched and audited
into `data/models/westminster/historical-transitions-v1.json`. That fixture is
not on master: it was added in `eaa73607` and removed in `39610fb9`. It survives
only on issue 0003's branch `refine-westminster-and-us-poll-error-models` (cut
at `44dd8336`, not yet pushed), so don't delete that branch without saving the
fixture first.

## Remove poll helpers only the archive uses

After the `data/` restructure (issue 0005), `ParsedPollWithBases` and `HasRegionalShares` in `data/importers/polls/bases.py`, and YouGov's `regional_shares()`, are used only by scripts in `data/archive/`. They were left in place because removing live code was outside a no-behaviour-change refactor.
