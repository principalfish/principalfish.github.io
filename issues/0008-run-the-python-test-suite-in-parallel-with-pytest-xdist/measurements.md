# Measurements (2026-11-15)

Main checkout at `bb39d207` (master after #87), 12 logical CPUs (WSL2), load ≈1 at start.
pytest-xdist 3.8.0 + execnet 2.1.2 installed into a scratch `--target` dir on `PYTHONPATH`, venv untouched.
All runs with `-p no:cacheprovider`. Every run left `git status` clean.

| Run | Tests | Wall | CPU | Speedup |
|-|-|-|-|-|
| serial | 7,424 | 8m39s | 73% | 1.0x |
| `-n 4` | 7,424 | 5m02s | 163% | 1.7x |
| `-n auto` (12 workers) | 7,424 | 2m37s | 563% | 3.3x |
| `-n auto` + `--cov` (coverage-changed.sh's pytest line) | 7,424 | 2m29s | 604% | (serial coverage reported 13–18 min) |

(A first serial run took 9m38s, but the checkout was fast-forwarded to #87 mid-run, so it's not counted.)

- Same 5 failures in every run, serial and parallel; nothing new under xdist (no order dependence or race seen across 4 full runs):
  `test_core_package::test_no_docstring_names_a_core_module_without_the_package`,
  `test_core_package::test_no_code_imports_a_core_module_by_its_bare_name`,
  `test_model_package_paths::test_no_model_or_script_module_names_the_old_packages`,
  `test_project_layout::test_no_live_code_adjusts_sys_path`,
  `test_us_importer_packages::test_no_live_code_imports_from_the_archive`.
  Cause: `data/.pytest_cache` holds 6.5 GB of old scratch repo copies (3,016 `.py` files vs 318 real ones). The scans `DATA_DIR.rglob("*.py")` and skip only `archive`, `election_data`, `.venv`, `venv`, `__pycache__` (`tests/test_project_layout.py:18`, `tests/test_core_package.py:26`). Local only; CI's clean checkout passes.
- The scan tests are the slowest in the suite (23s serial, up to 45s each under `-n auto` contention), and they bound the parallel wall time.
- Coverage on a 292-test subset (jobs, backup, trends, console westminster): identical XML, 6,434 / 22,682 lines covered, serial and `-n auto`. Full `-n auto` coverage: 22,116 / 22,682 lines.
- Serial wall time exceeds CPU time by about 140s (sleeps in timeout tests, SQLite file I/O), so serial runs leave a core 27% idle.
- CI (`ubuntu-latest`, 4 vCPU): the "mypy + pytest" step took 5m04s on the #87 branch run. `-n auto` there means 4 workers (no `psutil`, so `os.cpu_count()`).

## Later runs (the machine was shared from about 12:45: another repo's coverage run, load 3–13)

| Run | Wall | CPU | Load at the time |
|-|-|-|-|
| `-n 6` (CPU/2) | 3m29s | 266% | ≈1–4 |
| `-n 2` | 10m16s | 72% | ≈3–4, another repo's coverage running. Not trusted. |
| `-n 6`, `TMPDIR=/dev/shm` | 3m28s | 363% | ≈10–13 |

Subset (westminster/us model + all console tests, 1,742 tests), back to back: serial 4m49s at 38% CPU · `-n 2` 3m48s · `-n 4` 1m18s.

**Temp files are the hidden bottleneck.** Serial runs leave the CPU 27–62% idle. Every test builds a fresh SQLite file under pytest's temp dir, and `/tmp` here is the ext4 root disk, where fsync is slow under WSL2. `test_console_westminster.py` + `test_console_polls.py` (105 tests), serial: 46.6s with the temp dir on disk, 19.7s with `--basetemp` on `/dev/shm` (tmpfs), even though load had risen from 7 to 13. The full `-n 6` run took the same time on tmpfs at load 10–13 as it did on disk at load 1–4. A full run writes about 350 MB of temp files on tmpfs, and pytest keeps the last 3 runs. `/dev/shm` is 3.9 GB; the machine has 7 GB of RAM.
