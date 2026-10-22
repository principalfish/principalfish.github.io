# House 2024 baseline audit and prepared corrections

Prepared 2026-10-22. **Review artifact; no live correction has been applied.**

The curated [candidate and ballot-line facts](baseline_audit/house-2024.json)
reconcile all 435 voting districts. Under the explicit conventions below,
51 district party aggregates change and no recorded party winner changes.
This audit prepares corrections separately from the original seed JSON and from
calibration. It does not certify the 2022 baseline or identify House uncertainty
parameters from one transition.

## Source and reproducibility

Source: [Office of the Clerk, Statistics of the November 5, 2024 Election](https://clerk.house.gov/member_info/electionInfo/2024/statistics2024.pdf),
published March 10, 2025. Downloaded PDF SHA256:
`d4ba800fc17135b616ae556c92861080ab962d95916ace34b546c5aac824aec4`.
Each candidate retains its literal source party, grouped fusion ballot lines,
counts and **one-based PDF page**, distinct from printed page labels. The 68
excluded category records retain their own labels, counts and pages. The PDF
binary is not checked in. Structured extraction and manual spot checks support
this proposal; all 435 districts have not been independently re-keyed by a
second reviewer. Future PDF editions require explicit provenance review.

The curated input also freezes the original party aggregates and winners, plus
the SHA256 of the original seed file. The validator recomputes each proposed
party aggregate from the candidate facts, checks fusion-line sums, exclusions,
coverage, aliases and winner preservation. Alaska is explicitly excepted.
The dry-run report deterministically derives all 51 before/after district
corrections and full pre/post database fingerprints. Repeating it against an
unchanged database produces the same report.

Material examples: NY07 Democratic votes rise from 61,668 to 172,795 (PDF49).
NY20 Tonko's 200,354 Democratic plus 21,643 Working Families votes give 221,997;
Waltz's 121,609 Republican plus 19,542 Conservative votes give 141,151 (PDF50).
WA09 Smith 182,780 and Chaudhry 90,601 are both Democrats (PDF76); their party
aggregate must not misclassify Chaudhry as Republican. Candidate votes remain
separate in the audit even though the baseline database stores party aggregates.
ME01's existing Others14,037 is a labelled Blank count, removed here (PDF31).

## Counting conventions and unresolved exceptions

- **Alaska AK01:** retain existing final-round D156,985/R164,861 exactly. The
  Clerk lists first-choice counts. The [official final-round PDF](https://www.elections.alaska.gov/results/24GENR/RCV-USRep.pdf)
  could not be independently retrieved during preparation; its contents are
  **not newly verified**. Do not use Alaska as a verified calibration fact
  until separately checked. Maine02 uses the Clerk's final D197,151/R194,445.
- **FL20 and OK03:** retain the existing nominal100 votes for each unopposed
  winner (PDF17/58). These are model representations, not measured turnout.
- **Six existing aliases:** Joseph Gaye (CO05), Jody Madlom Puffett (IA02), and
  Austin Johnson (NJ01) remain Independent. Dennis Kucinich (OH07), Michael
  Harbaugh (OH10), and Sean Freeman (OH11) remain Others. Literal source party
  labels are retained. This preserves current mappings, including the unusual
  Ohio convention; it does not introduce a general party-reclassification rule.
- **Fusion:** group candidate ballot lines before party aggregation; NY05 Paul
  King's Conservative/Common Sense5,840 continues his Republican candidacy.
  VT01 Mark Coester's Republican/Libertarian label preserves Republican
  precedence. Pacific Green remains Others under the existing mapping.
- **Non-candidate counts:** exclude Blank, Blanks, Void, Over Votes, Under Votes,
  Blank Votes, Continuing Ballots and Exhausted Ballots. Keep named and unnamed
  valid write-ins. National recapitulation sums are unsuitable denominators
  without these exclusions and fusion/final-round treatment.

Proposed model totals: 147,989,033 including 200 nominal votes, or 147,988,833
excluding them. Republican74,830,301; Democratic70,788,932; Others941,593;
Libertarian709,405; Independent560,537; US Green158,265. These are conditional
project-convention totals, not a new certified national popular-vote measure.
Original total148,713,135 includes invalid categories and partial NY results.
The upstream538 CSV still contains the partial NY07/NY20 counts and WA09
misclassification, so rerunning the existing converter does not fix them.

## Local commands and safeguards

From `data/`, prepare a report against an **existing** database copy first:

```bash
./election_data/bin/python scripts/audit_us_house_baseline.py \
  --database /tmp/house-review.db --report /tmp/house-review.json
```

The default audit opens SQLite `mode=ro` with `query_only=ON`. No tables, polls,
model runs, exports, source JSON or missing database files are created.
Inspect the report and these exception conventions. After separate user
approval and a fresh backup, the exact reviewed database may be corrected:

```bash
./election_data/bin/python scripts/audit_us_house_baseline.py \
  --database /tmp/house-review.db --apply-reviewed /tmp/house-review.json
```

Apply opens `mode=rw` and obtains `BEGIN IMMEDIATE` before rechecking the full
map/election/seat identity and all vote-row fields against the reviewed
fingerprint. Any stale, partial, wrong-scope or duplicate-party baseline is
rejected. All51 corrections update/delete only target vote rows in one
transaction; retained row IDs, election identity, candidate names and elected
flags are preserved. No candidate winner is inferred from aggregated party
votes. A failed post-check or SQL error rolls back everything. Reapplying the
same report to the exact corrected state is a no-op. Later model runs and
exports require their own explicit operational step; existing forecasts do not
change automatically. Restore a reviewed backup for rollback after a successful
application; this tool does not overwrite newer data using a reverse patch.

## Complete proposed district deltas

Deltas are new minus old votes by project party key. PDF pages are one-based.

| District | Party deltas | PDF pages |
|-|-|-|
| GA-06 | others: -45 | 20 |
| GA-11 | others: -10,226 | 20 |
| HI-01 | others: -22,822 | 21 |
| HI-02 | others: -20,648 | 21 |
| IA-01 | others: -13,262 | 27 |
| IA-02 | others: -15,217 | 27 |
| IA-03 | others: -13,641 | 27 |
| IA-04 | others: -22,284 | 27 |
| MA-01 | others: -26,947 | 35 |
| MA-02 | others: -23,009 | 35 |
| MA-03 | others: -91,119 | 35 |
| MA-04 | democrat: -27, others: -113,762 | 35 |
| MA-05 | others: -87,811 | 35 |
| MA-06 | others: -110,196 | 35 |
| MA-07 | others: -44,193 | 35 |
| MA-08 | others: -20,937 | 35 |
| MA-09 | others: -18,702 | 35 |
| ME-01 | others: -14,037 | 31 |
| NH-01 | democrat: -5, others: -11,755, republican: -5 | 44 |
| NH-02 | democrat: -2, others: -14,579, republican: -3 | 44 |
| NY-01 | others: -20,360 | 48 |
| NY-02 | others: -23,959 | 49 |
| NY-03 | others: -18,758 | 49 |
| NY-04 | others: -16,812 | 49 |
| NY-05 | others: -12,060 | 49 |
| NY-06 | others: -12,467 | 49 |
| NY-07 | democrat: +111,127, others: -4,805, republican: +21,720 | 49 |
| NY-08 | others: -17,346 | 49 |
| NY-09 | others: -17,562 | 49 |
| NY-10 | others: -24,718 | 49 |
| NY-11 | others: -12,861 | 50 |
| NY-12 | others: -17,949 | 50 |
| NY-13 | others: -22,917 | 50 |
| NY-14 | others: -12,366 | 50 |
| NY-15 | others: -17,450 | 50 |
| NY-16 | others: -26,008 | 50 |
| NY-17 | others: -14,452 | 50 |
| NY-18 | others: -13,779 | 50 |
| NY-19 | others: -11,983 | 50 |
| NY-20 | democrat: +180,034, others: -3,033, republican: +113,332 | 51 |
| NY-21 | others: -11,818 | 51 |
| NY-22 | others: -10,471 | 51 |
| NY-23 | others: -22,027 | 51 |
| NY-24 | others: -19,504 | 51 |
| NY-25 | others: -18,983 | 51 |
| NY-26 | others: -21,100 | 51 |
| TX-28 | democrat: -210, republican: -99 | 68 |
| VA-06 | democrat: +1,513, independent: +20, others: +3, republican: +1,084 | 74 |
| VT-01 | others: -22,269 | 72 |
| WA-09 | democrat: +90,601, republican: -90,601 | 76 |
| WY-01 | others: -9,575 | 81 |
