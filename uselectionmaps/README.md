# US election maps

US election-maps page shell — thin `index.html` (header + shell mount; no fragments), the `uselectionmaps.js` entry (bundled to `uselectionmaps.min.js`), and `data/` = US exported maps/results + `map-modes(-shell).json`.

- [Manifest reference](../electionmaps/data/map-modes.md)
- [Shared engine, manifest contract and results schema](../electionmapslogic/README.md)
- [Static export runbook](../data/docs/export.md)
- [US polls and forecasts](../data/docs/us-polls-and-forecasts.md)
- [US results from NYT](../data/docs/us-results.md)

## Current Senate snapshot

Script paths in this section are relative to `data/`.

`uselectionmaps/data/results/senate-current.json` (`pf-senate-current-v1`) is a prebuilt
snapshot of all 100 seats, not an exported election: per state `n`, `r`, `w` (the pair's
shared party, or `split`) and two `members`, each `{party, name, class}` with the permanent
Senate class 1/2/3. Two generators write it:

- `archive/convert_senate_current.py` (under `data/`) built it from congress-legislators data
  (the pre-2026 chamber). It is archived and may no longer run.
- `importers/results/us/nyt/current_senate.py` derives it from the 2026 results: the 65 holdovers
  (every senator whose seat the 2026 election did not contest) plus the 2026 winners, being
  the 33 class 2 regular winners and the Florida and Ohio special winners (class 3), named
  from the winning party's row. It reads the committed `inputs/us/senate-2026.json`
  (and the specials file its `meta.special_file` names, a plain file name in the same
  folder), never the database.

```
cd data
./election_data/bin/python importers/results/us/nyt/cli.py current-senate --dry-run   # counts only, writes nothing
./election_data/bin/python importers/results/us/nyt/cli.py current-senate             # rewrite the snapshot in place
```

Options: `--snapshot` (input), `--election-file` (default `senate-2026.json`), `--out`
(default the snapshot). The console's `/senate/results` page has the same action, **Rebuild Current
Senate**; importing results never runs it, and the export is a separate step afterwards.
The build refuses (non-zero exit, nothing written) if the election lacks a class 2 state or the
Florida/Ohio special, a contested seat has no sitting senator, a snapshot member is malformed,
a state does not end with two members of different classes, or the chamber is not 100 members;
a winner without a name gets a placeholder and a warning. It is idempotent: seats are replaced
by what the election contested, not by who held them, so the input may be the converter's
snapshot or its own earlier output and the same election always gives identical bytes (sorted
by state, members by class). Re-run it as the 2026 counts finish.

Uncalled races are unknown members, never the current leader: `{"party": null, "name": null,
"class": 2, "c": false, "pc": 62.5}` (`c: false` is the results payload's uncalled flag; `pc`
is the percent counted, left out when unknown or not a number from 0 to 100, with a warning).
A state is coloured by its called members only. With one called and one uncalled member, `w`
is the called member's party and the state carries no `c`: the map draws it in that colour,
solid, until the other race is called. With both uncalled the seat carries `c: false` and no
`w`, and the map draws it in the neutral no-result colour (not a party, not `split`). The
chamber tally counts such a member in the total and the "N uncalled" line only, and no party
filter matches it. The Senate model reads only each member's class from this file, so uncalled
members do not affect its allowlist.

## Senate specials and per-seat baselines (Ohio, Florida)

Two keys in `uselectionmaps/data/map-modes-shell.json`, map mode `"23"`, move
individual seats off the field's baseline.

`senateSpecialElections` lists off-cycle races held alongside the regular class:

```json
{ "seat": "Ohio", "class": 3, "year": 2026, "baselineElectionId": "2022-us-senate" }
```

Entries whose `year` matches `parliamentFeatures.us_senate.nextElectionYear` join
the Senate model (and Predict) as extra seats, using that baseline election's
votes for that seat only. The export copies the key into `map-modes.json`
(dropping other years), and the Senate Predict "Full Senate" replaces that
state's member of the named class. It is empty after the 2026 specials were held.
A state with both a regular and a special race in one cycle cannot be
represented; the queue reports it as a page failure.

`senateBaselineOverrides` lists seats of the regular class whose last contest was a
special since the class voted, so they swing from that result instead:

```json
{ "seat": "Ohio", "class": 3, "baselineElectionId": "2026-us-senate" }
```

The `class` (1 to 3, required) is the class of the overridden seat: a state also holds
seats of other classes, so the override applies only while that class is the one being
contested (Florida's and Ohio's Class-1 seats in 2030 are not rebased). It has no year
and is not expired by the export, so remove the record once its class has voted again. For 2028 the Senate swings Class 3
from `2022-us-senate`, except Florida and Ohio, which swing from
`2026-us-senate`. The Senate model reads the same key, so the model and Predict
agree on each seat's baseline.

A saved Senate forecast (the current one, or an archived one from an earlier cycle)
gets **Seats up** and **Full Senate** tabs. It is recognised as a Senate model
election, not by the predict anchor, and the seats-up tab is labelled with the cycle
the forecast projects (the leading year of its name, "2026 Prediction"). **Seats up**
compares each seat with its previous election: for the 2026 forecast, the regular
seats use 2020 results and Ohio and Florida use the baselines their
`senateSpecialElections` entries declare for that year. These combined results drive
seat and vote-share changes, gains and seat popups. If a required baseline is
unavailable, the forecast still displays but its comparison is hidden and labelled
"Comparison unavailable". The export keeps only the specials of the shell's next
election year, so once Predict rolls to 2028 the archived 2026 forecast has no
Ohio and Florida baselines in the manifest and shows "Comparison unavailable".
**Full Senate** compares the projected chamber with current Senate membership;
returning to **Seats up** restores the per-seat election comparison.
