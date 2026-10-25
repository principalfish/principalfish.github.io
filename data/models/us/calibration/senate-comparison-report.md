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
|candidate-votes-excluding-nota-raw-zero-origin-state|295.9725660394755|18.520751953125|296.4809419630262|True|conditional response fit|
|candidate-votes-excluding-nota-raw-zero-origin-race|295.9725660394755|18.540771484375|297.5589616073932|True|conditional response fit|
|candidate-votes-excluding-nota-raw-centered-state|139.3497674564919|12.8709716796875|139.5029011100042|True|conditional response fit|
|candidate-votes-excluding-nota-raw-centered-race|139.3497674564919|12.8973388671875|140.17539846647895|True|conditional response fit|
|candidate-votes-excluding-nota-decided-zero-origin-state|318.27927862890755|None|None|None|decided response has a collapsed D/R denominator; bracket probe at scale 32.0|
|candidate-votes-excluding-nota-decided-zero-origin-race|318.27927862890755|None|None|None|decided response has a collapsed D/R denominator; bracket probe at scale 32.0|
|candidate-votes-excluding-nota-decided-centered-state|129.62607657598926|11.9010009765625|129.94663135339417|True|conditional response fit|
|candidate-votes-excluding-nota-decided-centered-race|129.62607657598926|11.9266357421875|130.49505746959494|True|conditional response fit|
|reported-including-nota-raw-zero-origin-state|295.9368822767748|18.519775390625|296.4501320252717|True|conditional response fit|
|reported-including-nota-raw-zero-origin-race|295.9368822767748|18.539306640625|297.51264571843524|True|conditional response fit|
|reported-including-nota-raw-centered-state|139.33938500968208|12.8704833984375|139.4923819929724|True|conditional response fit|
|reported-including-nota-raw-centered-race|139.33938500968208|12.8968505859375|140.1648510996347|True|conditional response fit|
|reported-including-nota-decided-zero-origin-state|318.27927862890755|None|None|None|decided response has a collapsed D/R denominator; bracket probe at scale 32.0|
|reported-including-nota-decided-zero-origin-race|318.27927862890755|None|None|None|decided response has a collapsed D/R denominator; bracket probe at scale 32.0|
|reported-including-nota-decided-centered-state|129.62607657598926|11.9010009765625|129.94663135339417|True|conditional response fit|
|reported-including-nota-decided-centered-race|129.62607657598926|11.9266357421875|130.49505746959494|True|conditional response fit|

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

## Election holdouts

Official outcomes condition each reference. These intervals test conditional scale transfer, not deployable forecasting coverage.

|Family|Held-out election|Training elections|Boundary|90% conditional coverage|Units|
|-|-|-|-|-|-|
|candidate-votes-excluding-nota-raw|2022|[2024]|state|0.7741935483870968|31|
|candidate-votes-excluding-nota-raw|2022|[2024]|race|0.7741935483870968|31|
|candidate-votes-excluding-nota-raw|2024|[2022]|state|0.9666666666666667|30|
|candidate-votes-excluding-nota-raw|2024|[2022]|race|0.9666666666666667|30|
|candidate-votes-excluding-nota-decided|2022|[2024]|state|None|None|
|candidate-votes-excluding-nota-decided|2022|[2024]|race|None|None|
|candidate-votes-excluding-nota-decided|2024|[2022]|state|0.9666666666666667|30|
|candidate-votes-excluding-nota-decided|2024|[2022]|race|0.9666666666666667|30|
|reported-including-nota-raw|2022|[2024]|state|0.7741935483870968|31|
|reported-including-nota-raw|2022|[2024]|race|0.7741935483870968|31|
|reported-including-nota-raw|2024|[2022]|state|0.9666666666666667|30|
|reported-including-nota-raw|2024|[2022]|race|0.9666666666666667|30|
|reported-including-nota-decided|2022|[2024]|state|None|None|
|reported-including-nota-decided|2022|[2024]|race|None|None|
|reported-including-nota-decided|2024|[2022]|state|0.9666666666666667|30|
|reported-including-nota-decided|2024|[2022]|race|0.9666666666666667|30|

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
