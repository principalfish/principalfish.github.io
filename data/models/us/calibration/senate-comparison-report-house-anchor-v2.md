# Senate calibration comparison

Unaccepted comparison report; production calibration unresolved

Input SHA256: 5708f8de432c1325c765a38aedc6d76f91cd6d6ba9d5c1811cc839c7391f7cb1

## National proxy observations

```json
{
  "training_cycles": [
    2012,
    2014,
    2016,
    2020,
    2022,
    2024
  ],
  "raw": {
    "n": 6,
    "mean": 1.6711083945645997,
    "rms": 2.438083476146288,
    "mean_square": 5.944251036657566,
    "centered_mean_square": 3.151647770273293
  },
  "decided": {
    "n": 6,
    "mean": 1.7322816046107936,
    "rms": 2.5759171106574117,
    "mean_square": 6.635348960977628,
    "centered_mean_square": 3.6345494033046832
  },
  "scope": "Total aggregate proxy, not accepted coefficients or a pure systematic term"
}
```

## Conditional kernel boundaries

|Scenario|Target second moment|Source scale|Recovered moment|Recovery compatible|Status|
|-|-|-|-|-|-|
|candidate-votes-excluding-nota-raw-zero-origin-state|197.780863380862|15.2803955078125|199.70225173768694|True|conditional response fit|
|candidate-votes-excluding-nota-raw-zero-origin-race|197.780863380862|15.2825927734375|200.0692199727527|True|conditional response fit|
|candidate-votes-excluding-nota-raw-centered-state|197.36666330552586|15.5150146484375|199.31836787640293|True|conditional response fit|
|candidate-votes-excluding-nota-raw-centered-race|197.36666330552586|15.5189208984375|199.3963992980426|True|conditional response fit|
|candidate-votes-excluding-nota-decided-zero-origin-state|145.48273462451596|12.4339599609375|147.24180168645705|True|conditional response fit|
|candidate-votes-excluding-nota-decided-zero-origin-race|145.48273462451596|12.4468994140625|147.4347663214242|True|conditional response fit|
|candidate-votes-excluding-nota-decided-centered-state|144.023047682109|12.5726318359375|145.79413090576662|True|conditional response fit|
|candidate-votes-excluding-nota-decided-centered-race|144.023047682109|12.5870361328125|145.7950496479744|True|conditional response fit|
|reported-including-nota-raw-zero-origin-state|197.78098648424273|15.2803955078125|199.70225173768694|True|conditional response fit|
|reported-including-nota-raw-zero-origin-race|197.78098648424273|15.2825927734375|200.0692199727527|True|conditional response fit|
|reported-including-nota-raw-centered-state|197.3671841398495|15.5150146484375|199.31836787640293|True|conditional response fit|
|reported-including-nota-raw-centered-race|197.3671841398495|15.5191650390625|199.40261855414064|True|conditional response fit|
|reported-including-nota-decided-zero-origin-state|145.48273462451596|12.4339599609375|147.24180168645705|True|conditional response fit|
|reported-including-nota-decided-zero-origin-race|145.48273462451596|12.4468994140625|147.4347663214242|True|conditional response fit|
|reported-including-nota-decided-centered-state|144.023047682109|12.5726318359375|145.79413090576662|True|conditional response fit|
|reported-including-nota-decided-centered-race|144.023047682109|12.5870361328125|145.7950496479744|True|conditional response fit|

## Matching-year Senate reference

Comparison: us-senate-house-anchor-comparisons-v2; estimator: senate-house-anchor-v1

Anchor policy SHA256: 6568de70b4317be92b58a7450de2d4d262e397989e5364cb8a8447bfbd4e71ee

Anchor catalogue SHA256: e20765425615bdc43a2e063f0ab260ca6f00e2ebd27bff6e1be9101dabe50757

Reference identity SHA256: a5f6f86065f4f881d9ed0eaa37e0f40e6b9df33ee360a3f836045ade82198df6

Legacy selected-field reports remain in senate-comparison-report.json and its Markdown companion.

|Family|Cycle|Official-input signed mean|Raw-poll signed mean|Centered conditional moment|
|-|-|-|-|-|
|candidate-votes-excluding-nota-raw|2022|0.6435837749167846|0.8324667510030442|197.36666330552586|
|candidate-votes-excluding-nota-decided|2022|1.2081750462606768|1.5280809190599571|144.02304768210897|
|reported-including-nota-raw|2022|0.6432747036789312|0.832157679765191|197.3671841398495|
|reported-including-nota-decided|2022|1.2081750462606768|1.5280809190599571|144.02304768210897|

## Complete-cycle reference availability

|Cycle|Denominator policy|Complete field|Reference available|Unavailable races|
|-|-|-|-|-|
|2022|candidate-votes-excluding-nota|35|True|0|
|2022|reported-including-nota|35|True|0|
|2024|candidate-votes-excluding-nota|34|False|33|
|2024|reported-including-nota|34|False|33|

Every race and its source baseline year, structural exclusions and availability reason remain in the companion JSON.
1 usable conditional election(s): 2022. Excluded complete fields: 2024. Fewer than two usable elections leave conditional holdout fits unavailable. Unavailable anchors: 2018: Unavailable: uncertified NC-09 is not an accepted historical proxy.


## Scope and unresolved assumptions

- Every scenario is unaccepted; no runtime coefficient set is nominated or emitted.
- Official-input point predictions use held-out outcomes for conditioning; folds test conditional scale transfer, not deployable forecasting performance.
- Post-blend broad-mask G is retained. Raw national/generic counterfactual exposure is different; its transfer is an unresolved modelling assumption.
- Only the combined singleton fallback moment is fitted. State-only/race-only and centered/zero-origin rows are sensitivity boundaries, not separately identified components.
- Local averages measure total estimator errors, including sampling. They are not admitted individual polls and supply no historical N, W, alpha, design effect or local floor.
- No district calibration, historical chamber/control scoring, tail validation or early held-today validation is claimed.
- Missing averages and access failures do not mean no historical polling existed. Retrieved observations are selected and vary in freshness.
- Gaussian kernel moment recovery validates numerical units, not distributional tails or the assumptions needed for release.
- Undefined decided contrasts at a probed scale remain unavailable; probe failure alone does not establish target unreachability.
- Prior-cycle baseline EV weights are not target allocations; no target EV outcome is scored.
- 1 usable conditional election(s): 2022. Excluded complete fields: 2024. Fewer than two usable elections leave conditional holdout fits unavailable. Unavailable anchors: 2018: Unavailable: uncertified NC-09 is not an accepted historical proxy.
- National and local proxy observations retain their own source qualifications.
- The matching-year House anchor changes deterministic fallback only. Pooled Senate baseline composition remains the stochastic source shape. Structural candidate zeros, paired-slot moment exclusions, minor-party mapping and RCV limitations are unchanged.
- Conditional and raw-poll signed means are observed minus projected; centered fits remove the cycle mean only for the target moment and do not correct central bias.
- Official current-national counterfactual levels and G facts retain the original final_RCV_no_nominal_view proxy. Baseline anchors use accepted published-general counts. Vote-stage/population transport remains unaccepted, including the 2022 Alaska general/tabulator discrepancy and 2024 verification limits; matching-year anchoring does not resolve it.

## Election holdouts

Official outcomes condition each reference. These intervals test conditional scale transfer, not deployable forecasting coverage.

|Family|Held-out election|Training elections|Boundary|90% conditional coverage|Units|
|-|-|-|-|-|-|
|candidate-votes-excluding-nota-raw|2022|[]|state|None|None|
|candidate-votes-excluding-nota-raw|2022|[]|race|None|None|
|candidate-votes-excluding-nota-decided|2022|[]|state|None|None|
|candidate-votes-excluding-nota-decided|2022|[]|race|None|None|
|reported-including-nota-raw|2022|[]|state|None|None|
|reported-including-nota-raw|2022|[]|race|None|None|
|reported-including-nota-decided|2022|[]|state|None|None|
|reported-including-nota-decided|2022|[]|race|None|None|

## Local polling-average diagnostics

```json
{
  "reported-including-nota": {
    "n_average_observations": 24,
    "election_clusters": [
      2022,
      2024
    ],
    "contrast_groups": {
      "D-R": {
        "contrast_axis": "D-R",
        "n_average_observations": 24,
        "election_clusters": [
          2022,
          2024
        ],
        "raw": {
          "n": 24,
          "mean": -0.4214285708474301,
          "rms": 4.449600412964841,
          "mean_square": 19.798943835056885,
          "centered_mean_square": 19.621341794730377
        },
        "decided": {
          "n": 24,
          "mean": -0.3930341992389456,
          "rms": 4.48665280400248,
          "mean_square": 20.130053383663313,
          "centered_mean_square": 19.975577501891916
        },
        "by_cycle": {
          "2022": {
            "raw": {
              "n": 13,
              "mean": -2.407345612562599,
              "rms": 5.2424263193904475,
              "mean_square": 27.483033714237674,
              "centered_mean_square": 21.687720815913277
            },
            "decided": {
              "n": 13,
              "mean": -2.480776821297077,
              "rms": 5.2922929648536705,
              "mean_square": 28.008364825839653,
              "centered_mean_square": 21.85411118875482
            }
          },
          "2024": {
            "raw": {
              "n": 11,
              "mean": 1.925564296634134,
              "rms": 3.273796985934826,
              "mean_square": 10.717746705115953,
              "centered_mean_square": 7.009948844643845
            },
            "decided": {
              "n": 11,
              "mean": 2.0742979904661185,
              "rms": 3.289273731277635,
              "mean_square": 10.819321679273095,
              "centered_mean_square": 6.516609526021318
            }
          }
        },
        "equal_election_mean_square": {
          "raw": 19.100390209676814,
          "decided": 19.413843252556376
        }
      }
    },
    "scope": "Contrast groups are separate; no mixed signed bias or local-floor estimate"
  },
  "candidate-votes-excluding-nota": {
    "n_average_observations": 24,
    "election_clusters": [
      2022,
      2024
    ],
    "contrast_groups": {
      "D-R": {
        "contrast_axis": "D-R",
        "n_average_observations": 24,
        "election_clusters": [
          2022,
          2024
        ],
        "raw": {
          "n": 24,
          "mean": -0.4239662515530396,
          "rms": 4.448422227433516,
          "mean_square": 19.788460313524567,
          "centered_mean_square": 19.60871293106863
        },
        "decided": {
          "n": 24,
          "mean": -0.3930341992389456,
          "rms": 4.48665280400248,
          "mean_square": 20.130053383663313,
          "centered_mean_square": 19.975577501891916
        },
        "by_cycle": {
          "2022": {
            "raw": {
              "n": 13,
              "mean": -2.4080826285913264,
              "rms": 5.243014135917007,
              "mean_square": 27.48919722942556,
              "centered_mean_square": 21.690335283302247
            },
            "decided": {
              "n": 13,
              "mean": -2.480776821297077,
              "rms": 5.2922929648536705,
              "mean_square": 28.008364825839653,
              "centered_mean_square": 21.85411118875482
            }
          },
          "2024": {
            "raw": {
              "n": 11,
              "mean": 1.920898557674027,
              "rms": 3.2691878827798044,
              "mean_square": 10.687589412914301,
              "centered_mean_square": 6.997738144040144
            },
            "decided": {
              "n": 11,
              "mean": 2.0742979904661185,
              "rms": 3.289273731277635,
              "mean_square": 10.819321679273095,
              "centered_mean_square": 6.516609526021318
            }
          }
        },
        "equal_election_mean_square": {
          "raw": 19.08839332116993,
          "decided": 19.413843252556376
        }
      }
    },
    "scope": "Contrast groups are separate; no mixed signed bias or local-floor estimate"
  }
}
```

Complete source rows, normalization, counterfactuals, independent recovery errors and holdout details are retained in the companion JSON report.
