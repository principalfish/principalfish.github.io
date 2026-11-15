"""The parallel test run: xdist's worker count and run_tests.sh's temp folder."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import half_the_cpus, pytest_xdist_auto_num_workers

DATA_DIR = Path(__file__).resolve().parent.parent
RUN_TESTS = DATA_DIR / "run_tests.sh"

# Stands in for the venv's python: mypy passes, and pytest records its arguments
# and whether the basetemp's parent exists, then exits with $STUB_STATUS. With
# $STUB_SIGNAL set it first sends that signal to the script running it.
STUB_PYTHON = """#!/usr/bin/env bash
if [[ "$2" == "mypy" ]]; then
	exit 0
fi
shift 2
printf '%s\\n' "$@" > "${STUB_RECORD}"
for arg in "$@"; do
	if [[ "${arg}" == --basetemp=* ]]; then
		dir="${arg#--basetemp=}"
		[[ -d "${dir%/t}" ]] && echo "parent exists" >> "${STUB_RECORD}"
	fi
done
if [[ -n "${STUB_SIGNAL:-}" ]]; then
	kill "-${STUB_SIGNAL}" "${PPID}"
fi
exit "${STUB_STATUS:-0}"
"""

needs_shm = pytest.mark.skipif(
    not (Path("/dev/shm").is_dir() and os.access("/dev/shm", os.W_OK)),
    reason="no writable /dev/shm",
)


def test_half_the_cpus_uses_the_affinity_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    """Half the CPUs the process may run on, rounded down, and never below 1."""
    for available, expected in [(12, 6), (5, 2), (1, 1)]:
        monkeypatch.setattr(
            os, "sched_getaffinity", lambda _pid, n=available: set(range(n))
        )
        assert half_the_cpus() == expected


def test_half_the_cpus_falls_back_to_cpu_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without sched_getaffinity, os.cpu_count is used, and None counts as 1."""
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    assert half_the_cpus() == 4
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert half_the_cpus() == 1


def test_auto_workers_are_half_the_cpus(
    pytestconfig: pytest.Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no override in the environment, -n auto means half the CPUs."""
    monkeypatch.delenv("PYTEST_XDIST_AUTO_NUM_WORKERS", raising=False)
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(range(10)))
    assert pytest_xdist_auto_num_workers(pytestconfig) == 5


def test_auto_workers_defer_to_the_environment(
    pytestconfig: pytest.Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A numeric PYTEST_XDIST_AUTO_NUM_WORKERS is left to xdist; empty or junk is not."""
    monkeypatch.setattr(os, "sched_getaffinity", lambda _pid: set(range(10)))
    monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", "4")
    assert pytest_xdist_auto_num_workers(pytestconfig) is None
    for ignored_by_xdist in ["", "abc"]:
        monkeypatch.setenv("PYTEST_XDIST_AUTO_NUM_WORKERS", ignored_by_xdist)
        assert pytest_xdist_auto_num_workers(pytestconfig) == 5


def test_run_tests_passes_n_auto_before_the_callers_args() -> None:
    """-n auto comes before "$@", so a caller's own -n (say -n 0) wins."""
    pytest_lines = [
        line for line in RUN_TESTS.read_text().splitlines() if " -m pytest " in line
    ]
    assert len(pytest_lines) == 1
    assert re.search(r"pytest tests/ -n auto .*\"\$@\"$", pytest_lines[0])


def _run_stubbed_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str
) -> tuple[int, list[str]]:
    """Run a copy of run_tests.sh against the stub python; return status and record."""
    shutil.copy(RUN_TESTS, tmp_path / "run_tests.sh")
    stub = tmp_path / "election_data" / "bin" / "python"
    stub.parent.mkdir(parents=True)
    stub.write_text(STUB_PYTHON)
    stub.chmod(0o755)
    record = tmp_path / "record.txt"
    monkeypatch.delenv("TESTS_TMP_ON_DISK", raising=False)
    monkeypatch.setenv("STUB_RECORD", str(record))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    completed = subprocess.run(
        ["bash", str(tmp_path / "run_tests.sh"), "-q"],
        capture_output=True,
        check=False,
    )
    return completed.returncode, record.read_text().splitlines()


def _basetemp(record: list[str]) -> Path:
    (arg,) = [line for line in record if line.startswith("--basetemp=")]
    return Path(arg.removeprefix("--basetemp=")).parent


@needs_shm
def test_run_tests_uses_a_ram_folder_and_removes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The basetemp is a fresh /dev/shm folder, gone after the run; status passes through."""
    status, record = _run_stubbed_script(tmp_path, monkeypatch, STUB_STATUS="3")
    assert status == 3
    assert record[:3] == ["tests/", "-n", "auto"]
    assert record[-2] == "-q"
    assert "parent exists" in record
    folder = _basetemp(record)
    assert folder.parent == Path("/dev/shm")
    assert folder.name.startswith("data-tests.")
    assert not folder.exists()


@needs_shm
@pytest.mark.parametrize(("signal_name", "expected"), [("INT", 130), ("TERM", 143)])
def test_run_tests_removes_the_ram_folder_on_a_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signal_name: str, expected: int
) -> None:
    """Ctrl-C or SIGTERM during the run still removes the folder."""
    status, record = _run_stubbed_script(tmp_path, monkeypatch, STUB_SIGNAL=signal_name)
    assert status == expected
    assert "parent exists" in record
    assert not _basetemp(record).exists()


def test_run_tests_keeps_temp_files_on_disk_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TESTS_TMP_ON_DISK=1 passes no basetemp, so pytest's default applies."""
    status, record = _run_stubbed_script(tmp_path, monkeypatch, TESTS_TMP_ON_DISK="1")
    assert status == 0
    assert record == ["tests/", "-n", "auto", "-q"]
