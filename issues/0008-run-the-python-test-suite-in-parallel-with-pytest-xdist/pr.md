# feat: run the Python test suite in parallel with pytest-xdist

The Python suite (`data/tests`, about 7,470 tests) ran serially on one core. It took about 9 minutes locally, 13–18 under coverage, and about 5 minutes in CI's "mypy + pytest" step. This PR runs it in parallel and keeps its temporary files in RAM.

Measured locally on 12 CPUs, a full `./run_tests.sh` now takes about 1–1½ minutes against 8m39s serially. On CI the "mypy + pytest" step took 1m33s on the first run and 2m39s on the final commit, against 5m04s before.

**Parallel run** (`data/run_tests.sh`, `data/tests/conftest.py`, `ci.yml`)
- `run_tests.sh` now runs `pytest tests/ -n auto`. Your own `-n` overrides it: for example, `-n 0` runs serially, which `-s` needs.
- A `pytest_xdist_auto_num_workers` hook in `conftest.py` makes `-n auto` start half the CPUs.
- `PYTEST_XDIST_AUTO_NUM_WORKERS=<n>` overrides the count. CI sets it to 4, the runner's full vCPU count.
- `pytest-xdist>=3.8` is added to `data/requirements.txt`.

**Temp files in RAM** (`data/run_tests.sh`)
- Each test builds a fresh SQLite file. On this machine, `/tmp` on the WSL2 disk was the real bottleneck: serial runs sat 27–62% idle waiting on fsync.
- When `/dev/shm` is writable, `run_tests.sh` gives pytest a fresh per-run folder there (`--basetemp`) and deletes it on exit, Ctrl-C or SIGTERM.
- `TESTS_TMP_ON_DISK=1` keeps the temp files on disk so you can inspect them.
- A run killed with SIGKILL leaves its folder (about 350 MB) until reboot. That was accepted.

**Scan tests list files through git** (`data/tests/source_files.py`)
- Six repo-scanning tests walked `data/` with `rglob`. A gitignored folder of old scratch copies (6.5 GB in `data/.pytest_cache` in the main checkout) made five of them fail locally.
- A new helper, `python_files()`, lists `data/`'s `.py` files with `git ls-files --cached --others --exclude-standard`. Ignored folders are skipped and new untracked files still count.
- It fails loudly without git.
- For every scan, the old and new file sets are identical.

**Guard on exported data** (`data/tests/data_guard.py`)
- A plugin snapshots every file under `electionmaps/data` and `uselectionmaps/data` that git reports as changed or untracked, with a sha256, at the start and end of the run.
- If the two snapshots differ, the run fails and its summary names the files. A test once rewrote the tracked `model_output_trends.json`.
- A file already dirty before the run fails only if it changes again.
- It runs on the xdist controller only, and warns and skips without git.
- If you run a real export during a test run, it reports that too.

**Review**
- Each piece had its own check round. A whole-branch `/review self` then verified six findings; three are fixed in `a4db4762`.
- The high one was found on this PR's CI. An existing parametrize in `test_backup.py` embedded `gzip.compress(...)` bytes in its test ID. On Python 3.12, gzip's header carries the current time, so xdist workers that collected in different seconds saw different tests and refused to run.
  - Locally, Python 3.14 writes `mtime=0`, so the failure never showed there.
  - The fix gives the parametrize explicit `ids` and passes `mtime=0`. CI now passes on 3.12.
- Also fixed:
  - `os.fsdecode` for non-UTF-8 file names in the scan helper;
  - an end-to-end test that `-n auto` really starts half the CPUs. Its re-check caught the singular "1/1 worker" on small machines, which is now handled.
- Skipped by choice:
  - de-duplicating the throwaway-git-repo fixtures between `test_data_guard.py` and `test_source_files.py`;
  - moving the worker hook out of `conftest.py`;
  - a docs line saying a small or noexec `/dev/shm` (Docker's default) needs `TESTS_TMP_ON_DISK=1`.

**Worth checking by hand**
- The changed-lines coverage report has no figure: the coverage config omits `tests/*`, where nearly all of this change lives.

Closes #88
