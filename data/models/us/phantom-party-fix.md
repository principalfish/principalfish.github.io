# US absent-party swing correction

National and regional swings previously assigned positive votes to parties with no votes in a seat’s baseline. This correction suppresses those broad polling effects seat by seat. A party named in that seat’s own contributing polls can still enter through its local poll contribution. Its national/regional fallback remains zero until it has positive baseline votes. The Others aggregate retains its existing policy.

## Preserved policies

- Candidate admission, matchup selection and material missing-candidate handling are unchanged. A usable contributing average is required; skipped or zero-weight averages cannot admit entrants.
- Poll blend weight remains `alpha = W / (W + k)`. Maine/Nebraska districts still inherit state swings for parties with positive district baseline votes; state polling does not admit a new party into an unpolled district.
- The vote-share clamp, normalization, baseline turnout, recorded-count rounding and deterministic winner selection remain unchanged. Removing a phantom share changes other parties’ normalized counts, including Others; those collateral changes do not alter Others’ swing policy.
- Positive-baseline parties retain the same applied swing. A baseline zero counts as absence.

## Corrected-baseline scratch comparison

House nowcast as of **19 October 2026**, 60-day polling window, 30-day decay half-life, default seat prior weight 1. The before and after runs use the same scratch database with the reviewed 51-district 2024 baseline corrections already applied. Both are dry runs; this comparison writes no saved forecast, trend export or live database data.

The correction changes **21 of 435 seats** and **56 party vote rows**. **No winners change:** Democratic 242, Republican 193 before and after. Every changed seat and party count is listed below; unchanged party rows are omitted. These are rounded projected vote counts, not actual election results.

|Seat|Changed projected counts (before → after)|Winner before → after|
|-|-|-|
|AL-03|Others 5,480 → 5,633; Democratic 6,747 → 0; Republican 236,781 → 243,375|Republican → Republican|
|AL-04|Others 3,584 → 3,683; Democratic 7,529 → 0; Republican 266,760 → 274,189|Republican → Republican|
|AL-05|Others 12,839 → 13,196; Democratic 7,110 → 0; Republican 242,462 → 249,214|Republican → Republican|
|CA-20|Democratic 7,824 → 0; Republican 280,964 → 288,788|Republican → Republican|
|IL-15|Others 1,497 → 1,538; Democratic 8,405 → 0; Republican 300,332 → 308,696|Republican → Republican|
|IL-16|Others 194 → 200; Democratic 8,429 → 0; Republican 302,484 → 310,908|Republican → Republican|
|KY-04|Others 1,201 → 1,235; Democratic 7,573 → 0; Republican 270,743 → 278,282|Republican → Republican|
|KY-05|Democratic 7,083 → 0; Republican 254,324 → 261,407|Republican → Republican|
|LA-04|Democratic 8,297 → 0; Republican 297,951 → 306,248|Republican → Republican|
|MS-03|Democratic 7,184 → 0; Republican 257,975 → 265,159|Republican → Republican|
|NC-03|Democratic 8,693 → 0; Republican 235,076 → 241,623; Libertarian 77,072 → 79,218|Republican → Republican|
|NC-06|Others 110,477 → 113,554; Democratic 9,139 → 0; Republican 217,704 → 223,766|Republican → Republican|
|NV-02|Others 145,892 → 147,950; Democratic 73,917 → 69,322; Republican 171,149 → 173,563; Libertarian 8,626 → 8,748|Republican → Republican|
|OK-03|Democratic 3 → 0; Republican 97 → 100|Republican → Republican|
|TX-01|Democratic 7,004 → 0; Republican 251,519 → 258,523|Republican → Republican|
|TX-09|Democratic 134,906 → 129,944; Republican 49,235 → 54,197|Democratic → Democratic|
|TX-11|Democratic 5,743 → 0; Republican 206,232 → 211,975|Republican → Republican|
|TX-13|Democratic 6,519 → 0; Republican 234,103 → 240,622|Republican → Republican|
|TX-19|Democratic 7,217 → 0; Republican 204,539 → 210,235; Independent 29,166 → 29,979; Libertarian 25,452 → 26,161|Republican → Republican|
|TX-25|Others 1,764 → 1,813; Democratic 7,172 → 0; Republican 255,767 → 262,890|Republican → Republican|
|WA-04|Others 5,735 → 5,895; Democratic 7,994 → 0; Republican 281,323 → 289,157|Republican → Republican|

NV-02 and TX-09 retain Democratic votes from their own seat polls; only their ineligible broad fallback is removed. The other 19 changed seats lose a phantom Democratic share entirely.

## Verification

The shared US model regression suite covers all three runners and the corrected candidate-admission boundaries. Added cases cover missing and explicit-zero baseline parties, local new entrants, an unpolled neighbouring seat sharing regional polling, positive-baseline swing behavior, Others without baseline support, and Maine/Nebraska inherited polling. An existing presidential contributor test now expects a state-only Libertarian entrant to remain absent from an unpolled district, with the surviving shares normalized as usual.

Focused US model regression: 334 tests passed. Strict mypy: 250 source files passed. Full pipeline verification is run by the coordinating builder before committing.
