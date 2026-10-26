# House national anchor candidates

Audit date: 26 October 2026. The immutable evidence catalogue retains its
original candidate status (`accepted: false`, `activation_policy: null`). The
offline inspector remains an evidence reader. Separate user approval now lives in
`accepted-v1.json`, integrity-pinned by `model_support/us_senate_anchors.py`; the
evidence bytes and their audited SHA256 have not been rewritten.

## Accepted policy and software boundary

The user approved the published-general 2020/2022 population, preserving Other in
the denominator, audited same-candidate fusion, no invented uncontested votes,
and the certified NY-14 +30 Republican/+30 denominator correction. Final-RCV
remains sensitivity evidence. The user also approved the explicit 2016 Welch
Democratic mapping and **left 2018 unavailable** because NC-09 was uncertified.
2024 remains unavailable.

`senate-house-anchor-v1` applies to newly computed runs. Each race subtracts the
House reference for its actual Senate baseline year; its own Senate baseline,
local blend, eligibility rules, clamping, rounding and error-shape population
remain unchanged. Regional own-poll swings retain their previous behavior.
Absent national evidence contributes zero swing. National observations for a
named minor party have no approved component in this D/R/Other reference and
fail with a repair message; a local minor-party candidate remains supported.

New central-only context wraps the frozen Senate cycle and complete resolved
anchor evidence under `pf-senate-central-context-v1`. New uncertainty options
freeze the same estimator, policy, race baselines and source identity, splitting
its context fingerprint from legacy runs. Saved replay uses frozen central
inputs and never resolves today's anchors. Raw legacy cycle snapshots keep
their previous interpretation. Senate release and calibration both need matching
`central_estimator` and `house_anchor_policy_sha256` values; source acceptance is
**not calibration acceptance**, and old saved odds cannot inherit a new release.
No existing comparison artifact, live database, backfill or public export was
changed. Historical recomputation still requires explicit instructions.

The following population discussion records the original candidate audit;
its proposed operational choice is now approved as described above.

## Proposed population and decision

The Senate national signal is House generic-party polling. A corresponding House
reference should retain raw D/R/Other levels, rather than normalize D/R to 100.
The two named views make the unresolved counting choice explicit:

- `published-general`: measured candidate votes in the regular November
  full-term contests, using the FEC general column. Alaska and Maine use the
  published general/first-choice count where the workbook supplies it.
- `final-rcv`: the same measured district population, with the named RCV
  districts replaced by final continuing-ballot counts. This is a different
  vote-stage population, with eliminated/exhausted ballots outside that
  district's final denominator.

For generic-party intention, the proposed policy is the **published-general
measured-candidate-party convention**, with final RCV retained as a sensitivity
view. The acceptance above covers stage, measured population, fusion affiliation,
the NY-14 correction and activation/version boundary. It does not accept
uncertainty calibration.

The proposed activation boundary is a new `senate-house-anchor-v1` central
estimator for newly computed runs after the implementation is accepted. Its
frozen provenance would name the selected anchor view and exact catalogue
identity. Existing saved runs and their replay retain their original estimator;
no automatic live rerun, historical backfill or export is included. Recomputing
an old date would create a new, distinguishable run only when explicitly
requested. This activation boundary was approved with the source policy above.

Neither view estimates unrecorded votes in unopposed districts. Denominators are
the sum of measured valid candidate/scattered votes, not all ballots cast or an
imputed complete 435-race electorate. Standalone minor candidates remain Other;
same-candidate minor fusion lines follow their unambiguous major nomination.

## Counts and qualifications

| Year/view | D | R | Other | Denominator | Measured districts |
|-|-|-|-|-|-|
| 2016 both | 61,821,857 | 63,288,574 | 3,619,287 | 128,729,718 | 433 |
| 2018 published-general | 60,720,318 | 50,930,303 | 2,072,691 | 113,723,312 | 431 |
| 2018 final-rcv | 60,730,745 | 50,935,050 | 2,049,264 | 113,715,059 | 431 |
| 2020 both | 77,548,029 | 72,835,828 | 2,260,206 | 152,644,063 | 434 |
| 2022 published-general | 51,530,747 | 54,440,810 | 1,743,481 | 107,715,038 | 433 |
| 2022 final-rcv | 51,551,519 | 54,428,784 | 1,715,755 | 107,696,058 | 433 |

2020 published-general shares are D 50.8031740481%, R 47.7161224410%.
2022 published-general shares are D 47.8398819299%, R 50.5415130615%; final-RCV
shares are D 47.8676006879%, R 50.5392537209%. Other remains in every denominator.

2016 has unmeasured FL-24 and OK-01. Peter Welch's VT-01 general row P3755 is
264,414 with source party `D/R`; this is an explicit dual-endorsement ambiguity.
The proposed Democratic mapping is supported by the same official workbook's
Table 7, House by Party, row 54: general E54 assigns 264,414 Democratic votes,
F54 is blank Republican, G54 has 30,920 Other votes. Row 3758 is Republican
**primary-only**, not extra general votes. This evidence supports an explicit
affiliation convention; it does not silently settle the policy. Both 2016 views
remain candidate audit records; runtime approval of the 2016 mapping is recorded
separately in the accepted policy, while 2018 stays unavailable.

2018 has unmeasured FL-10/14/21/24. NC-09 November rows 2987/2991/2994 contain
139,246 R, 138,341 D and 5,130 Libertarian votes (282,717 total), but were **not
certified**. The workbook note and the [FEC report introduction, page 6](https://www.fec.gov/documents/2705/federalelections2018.pdf)
explain the new election. These counts are only an explicitly labelled
November-return proxy. Both views remain blocked; no 2019 substitution and no
admission of 2018 to national-error fitting is authorized. ME-02 first choices
are Golden 132,013, Poliquin 134,184, Bond 16,552 and Hoar 6,875. The W1585 note
gives final Golden 142,440 and Poliquin 138,931, replacing the district total
289,624 with 281,371 (D +10,427; R +4,747; Other −23,427; denominator −8,253).

2020 FL-25 P778 is `Unopposed`, with no measured Mario Diaz-Balart votes. Do not
invent nominal votes. ME-02's row 1408 second-round note describes a Republican
**primary**, so its 28,019/20,295 figures never replace general-election counts.

2022 FL-05 P773 and LA-04 P1627 are unmeasured unopposed contests. Final RCV
replaces AK-01 P59:P63 (128,553 D; 67,866+61,513 R; 4,570+1,108 Other) with
AC59=137,263 D and AC60=112,471 R. It replaces ME-02 P1650/P1652/P1655/P1656
(153,074 D; 141,260 R; 21,655+393 Other) with AA1650=165,136 D and
AA1652=146,142 R. Importantly, Alaska's general column totals **263,610**, while
the RCV-tabulator's first round Y59:Y62 totals **264,589**. These are distinct
published views: the entire general-to-final difference is not purely ballot
exhaustion. The catalogue records this qualification instead of conflating them.

## Sources, extraction and correction

The raw source URLs, SHA256 values, exact sheet names, selected record references,
extraction rules and correction identities are pinned in
[candidates-v1.json](candidates-v1.json). The four original workbooks were
independently extracted and hash-checked on the audit date:

- [FEC 2016 workbook](https://www.fec.gov/documents/1890/federalelections2016.xlsx),
  `2016 US House Results by State`, 1,429 atomic general rows.
- [FEC 2018 workbook](https://www.fec.gov/documents/2706/federalelections2018.xlsx),
  `2018 US House Results by State`, 1,415 atomic general rows.
- [FEC 2020 workbook](https://www.fec.gov/documents/4228/federalelections2020.xlsx),
  `13. US House Results by State`, 1,390 atomic general rows.
- [FEC 2022 workbook](https://www.fec.gov/documents/5676/federalelections2022.xlsx),
  `8. US House Results by State`, 1,308 atomic general rows.

The reproducible extractor selects the 50 states, a numeric regular district
(00 means at-large 01), or explicitly labelled statewide scattered write-ins.
It skips J-marked totals, missing candidate identities except K=`W`, all
`UNEXPIRED` districts, territories, primary counts and separate runoff columns.
Only nonnegative integer votes from P count; `Unopposed` is a missing measurement.
It groups normalized exact candidate names **within district and stage**, not
FEC IDs. FEC 2020 E3203/E3205 incorrectly share H0TN07255 for opposing Green and
Sreepada; 2022 E1238/E1243 share H2IL03162 for opposing Ramirez and Burau.
Names, source rows and explicit affiliations keep those candidates distinct.
CRV/WF/IP labels alone never determine a major party: for example, 2022 NY-14
Cuellar's 2,208 CRV votes remain Other rather than Forte's Republican fusion.

FEC summary totals are unsuitable substitutes: 2020 adds LA-05's December runoff
79,307 votes; 2022 adds IN-02's unexpired-term 188,045 votes. The extractor
excludes both. It does not combine different rounds or terms as extra voters.

2020 NY-14 FEC P2379 says Republican 52,447, inconsistent with the signed
[NYC BOE district certification](https://vote.nyc/sites/default/files/pdf/election_results/2020/20201103General%20Election/00000300014Crossover%20Representative%20in%20Congress%2014th%20Congressional%20District%20Recap.pdf).
The certified Republican line is 52,477 = Bronx 21,290 + Queens 31,187; add
**30 R and 30 denominator**. Conservative 5,963 remains the same candidate's
fusion line. Retain 222 counted scattered write-ins. The NYC grand total
213,340 includes 17 `UNCOUNTED WRITE-IN PER STATUTE`; excluding those gives
213,323 valid candidate votes. Blank/unrecorded ballots are also outside the
denominator. The raw FEC source stays unchanged. The correction PDF's verified
raw SHA256 is `0530670951444ece59dec45ff05aacb979836984b04441514ffa70d38de59c08`.

## Integrity and offline reproduction

The loader pins the exact catalogue bytes independently of the JSON's own payload
hash. Rewriting counts and recomputing that payload hash does not match the
reviewed byte identity. Hashes establish integrity, not independent source
authenticity or human policy approval. Raw-source hashes identify the retrieved
official file bytes. The extraction
hash separately identifies JSON atomic records
`[one_based_row, unit, normalized_name, raw_party, integer_votes]`, in source row
order, with compact JSON separators, sorted object keys and no NaN. It is a
derived-record digest, not a raw workbook hash. Structural validation additionally
checks years/views, integer count sums, measured-unit accounting, missing units,
source/extraction/correction identities and unresolved policy status.

From `data/`, supply downloaded official files explicitly; the command has no
network or database access and writes no source files:

```bash
./election_data/bin/python -m scripts.audit_us_house_anchors --year 2016 --workbook /path/to/federalelections2016.xlsx
./election_data/bin/python -m scripts.audit_us_house_anchors --year 2018 --workbook /path/to/federalelections2018.xlsx
./election_data/bin/python -m scripts.audit_us_house_anchors --year 2020 --workbook /path/to/federalelections2020.xlsx --correction-pdf /path/to/ny14-certified.pdf
./election_data/bin/python -m scripts.audit_us_house_anchors --year 2022 --workbook /path/to/federalelections2022.xlsx
```

The command verifies pinned workbook bytes, re-extracts rows/groups, checks the
record digest/population, applies the documented certified NY-14 correction only
after verifying its PDF bytes, and reads final RCV counts from source cells or
the explicit 2018 final-general note. The PDF's certified numerical interpretation
is the audited correction above, rather than an automatic generic PDF parser.

The current 2026 field requires **2020 regular and 2022 special** anchors; the
configured 2028 field requires 2022. Historical 2022 comparisons require 2016
regular and 2020 special; historical 2024 requires 2018 regular and 2020 special.
The inspector derives these years from supplied race `baseline_id` values, not
the target year or a region-wide assumption. An anchor never supplies a missing
2028 chamber roster or resolves other release gates.

## 2024: audit only, unavailable

2024 is not required by these configured Senate fields. The catalogue retains
the earlier qualified audit's final counts (D 70,788,832; R 74,830,201;
Other 2,369,800; denominator 147,988,833), but marks **both views unavailable**.
The [Clerk report](https://clerk.house.gov/member_info/electionInfo/2024/statistics2024.pdf)
uses Alaska first choices and Maine final counts, so it is not a complete
first-choice series. The qualified final audit substitutes Alaska final
164,861 R and 156,985 D, with 321,846 continuing ballots and 14,007 nontransferable
ballots, from indexed extraction of the
[official Alaska PDF](https://www.elections.alaska.gov/results/24GENR/RCV-USRep.pdf),
page 3, round 3, report timestamp 30 November 2024 15:21:13. Direct PDF retrieval
failed; **no raw Alaska PDF hash or complete-document verification exists**.
The 2024 extraction digest identifies the committed audit's derived `baseline`
mapping, not the original PDF or a newly verified complete anchor. FL-20 and OK-03
remain unmeasured; nominal 100 placeholders are excluded. Separate source work
and human acceptance are needed before either 2024 view could become available.
