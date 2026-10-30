# [[ data ]] Harden uncertainty tooling and record Westminster calibration diagnostics

Work in progress: this checkpoint implements the validation harness, robustness and coverage fixes, and trailing tracker uncertainty bands that respect configuration and kernel boundaries. Frozen aggregate validation inputs keep comparisons reproducible across archive changes.

The offline Westminster conversion experiment evaluates eight combinations of clustered errors, heavy seat tails and predictive lambda uncertainty, with recovery checks, durable checkpoints and a console report. The combined candidate covers 15/20 historical seat totals against the required 17/20 and fails additional calibration guards. Production integration remains blocked; remaining model/display work and a separate self-review are pending.

Validation: strict mypy passes on 304 files; 6,254 Python tests and 658 JavaScript tests pass. Both map bundles are rebuilt. The committed source tree matches the tested checkpoints. No live database changes, operational reruns, release activation or remote publication were performed.
