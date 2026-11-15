# Run the Python test suite in parallel with pytest-xdist

The Python suite (`data/tests`, 7,424 tests) runs serially on one core and takes about 9 minutes, 13–18 under coverage. That makes `./check.sh`, CI and every builder's coverage step slow. Measured on 2026-11-15, it runs safely in parallel: four full runs, serial and under xdist, failed the same five tests and nothing else, and none touched a tracked file. 6 workers took 3m29s, 12 workers 2m37s, and 12 workers with coverage 2m29s, against 8m39s serially.

`data/run_tests.sh` will run pytest across half the machine's CPUs by default, so `check.sh` and CI get it too. CI uses all four of its runner's CPUs, and `-n 0` turns it off. Pytest's temporary files, a fresh SQLite database per test, move to a RAM folder (`/dev/shm`) for the run. On this machine's disk they're the hidden bottleneck: two console test files ran 2.4x faster serially from RAM.

Two safety fixes come with it. The tests that scan `data/` for source files will list files through git, so a gitignored folder of old scratch copies can't make them fail (today 6.5 GB of them in `data/.pytest_cache` fail five tests locally). And the run fails if any test changes the tracked exported data under `electionmaps/data` or `uselectionmaps/data`, naming the file, because a test once rewrote `model_output_trends.json`.

Out of scope: gating the GitHub Pages deploy on CI (considered and dropped), and keeping `data/` off the published site.

## Plan

_2026-11-15 · 2 of 3 pieces done · now: waiting on a decision_

- [x] **1/3 Run the suite in parallel** · `817554d4`
  run_tests.sh runs pytest on half the CPUs (4 on CI), with temp files in a per-run RAM folder
- [x] **2/3 List the scanned source files through git** · `d15b8f04`
  the repo-scan tests list data/'s files with git, so gitignored folders can't fail them
- [ ] **3/3 Fail the run if it changes tracked exported data** · not started
  a session guard names any file under electionmaps/data or uselectionmaps/data that a run changed
