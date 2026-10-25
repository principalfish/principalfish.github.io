# President calibration comparison

Unaccepted comparison report; production calibration unresolved

Input SHA256: cd34bfd903129b92fe06ab2af91b9de4c013e7b454b436fb7948a7a9b8d9934b

## National proxy observations

```json
{
  "training_cycles": [
    2016,
    2020,
    2024
  ],
  "raw": {
    "n": 3,
    "mean": 1.8056576396120647,
    "rms": 1.9333028682401125,
    "mean_square": 3.737659980345446,
    "centered_mean_square": 0.4772604688560326
  },
  "decided": {
    "n": 3,
    "mean": 1.979827722959412,
    "rms": 2.116169753840778,
    "mean_square": 4.478174427070539,
    "centered_mean_square": 0.5584566144718891
  },
  "scope": "Total aggregate proxy, not accepted coefficients or a pure systematic term"
}
```

## Conditional kernel boundaries

|Scenario|Target second moment|Source scale|Recovered moment|Recovery compatible|Status|
|-|-|-|-|-|-|
|official-all-vote-raw-zero-origin-state|25.20492470883946|5.26068115234375|25.13130819854083|True|conditional response fit|
|official-all-vote-raw-zero-origin-race|25.20492470883946|5.26934814453125|25.247326269691214|True|conditional response fit|
|official-all-vote-raw-centered-state|23.071628745212806|5.08221435546875|22.99585995441843|True|conditional response fit|
|official-all-vote-raw-centered-race|23.071628745212806|5.09173583984375|23.11384971064352|True|conditional response fit|
|official-all-vote-decided-zero-origin-state|24.582400805018874|5.01007080078125|24.512404075404348|True|conditional response fit|
|official-all-vote-decided-zero-origin-race|24.582400805018874|5.01910400390625|24.628453075468716|True|conditional response fit|
|official-all-vote-decided-centered-state|21.838014549088943|4.76837158203125|21.767281840736125|True|conditional response fit|
|official-all-vote-decided-centered-race|21.838014549088943|4.77813720703125|21.88321887616889|True|conditional response fit|

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
|official-all-vote-raw|2016|[2020, 2024]|state|0.6470588235294118|51|
|official-all-vote-raw|2016|[2020, 2024]|race|0.6470588235294118|51|
|official-all-vote-raw|2020|[2016, 2024]|state|1.0|51|
|official-all-vote-raw|2020|[2016, 2024]|race|1.0|51|
|official-all-vote-raw|2024|[2016, 2020]|state|1.0|51|
|official-all-vote-raw|2024|[2016, 2020]|race|1.0|51|
|official-all-vote-decided|2016|[2020, 2024]|state|0.5490196078431373|51|
|official-all-vote-decided|2016|[2020, 2024]|race|0.5490196078431373|51|
|official-all-vote-decided|2020|[2016, 2024]|state|1.0|51|
|official-all-vote-decided|2020|[2016, 2024]|race|1.0|51|
|official-all-vote-decided|2024|[2016, 2020]|state|1.0|51|
|official-all-vote-decided|2024|[2016, 2020]|race|1.0|51|

## Local polling-average diagnostics

```json
{
  "official-all-vote": {
    "n_average_observations": 53,
    "election_clusters": [
      2016,
      2020,
      2024
    ],
    "contrast_groups": {
      "D-R": {
        "contrast_axis": "D-R",
        "n_average_observations": 53,
        "election_clusters": [
          2016,
          2020,
          2024
        ],
        "raw": {
          "n": 53,
          "mean": 1.476229036847029,
          "rms": 3.7524775016165046,
          "mean_square": 14.081087400138045,
          "centered_mean_square": 11.901835230907738
        },
        "decided": {
          "n": 53,
          "mean": 1.6750239160507285,
          "rms": 3.7954014182776152,
          "mean_square": 14.405071925863732,
          "centered_mean_square": 11.599366806521815
        },
        "by_cycle": {
          "2016": {
            "raw": {
              "n": 24,
              "mean": 1.0839829746685652,
              "rms": 4.421559047545334,
              "mean_square": 19.55018441093,
              "centered_mean_square": 18.375165321558686
            },
            "decided": {
              "n": 24,
              "mean": 1.3378503938967181,
              "rms": 4.510967209451156,
              "mean_square": 20.34882516474355,
              "centered_mean_square": 18.55898148829394
            }
          },
          "2020": {
            "raw": {
              "n": 14,
              "mean": 1.813153034061447,
              "rms": 3.730609856535355,
              "mean_square": 13.917449901678742,
              "centered_mean_square": 10.629925976752514
            },
            "decided": {
              "n": 14,
              "mean": 2.115834613568657,
              "rms": 3.7475710606726826,
              "mean_square": 14.044288854791377,
              "centered_mean_square": 9.567532742816146
            }
          },
          "2024": {
            "raw": {
              "n": 15,
              "mean": 1.7893603389324475,
              "rms": 2.3416362900259005,
              "mean_square": 5.483260514766263,
              "centered_mean_square": 2.2814500922218195
            },
            "decided": {
              "n": 15,
              "mean": 1.8030782338137457,
              "rms": 2.2873123114236558,
              "mean_square": 5.231797609990227,
              "centered_mean_square": 1.9807064927373312
            }
          }
        },
        "equal_election_mean_square": {
          "raw": 12.983631609125004,
          "decided": 13.208303876508383
        }
      }
    },
    "scope": "Contrast groups are separate; no mixed signed bias or local-floor estimate"
  }
}
```

Complete source rows, normalization, counterfactuals, independent recovery errors and holdout details are retained in the companion JSON report.
