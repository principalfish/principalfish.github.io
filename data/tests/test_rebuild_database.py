"""Tests for the database-rebuild orchestrator: step building + run accounting."""

from pathlib import Path

import pytest

import scripts.rebuild_database as rebuild
from scripts.rebuild_database import (
    BY_ELECTION_IMPORT,
    ByElectionEntry,
    Step,
    build_steps,
    by_election_steps,
    main,
    parse_by_elections_file,
    run_step,
    us_election_steps,
)


class TestParseByElectionsFile:
    """Covers parse_by_elections_file — comments, blanks, and pipe overrides."""

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert parse_by_elections_file(tmp_path / "nope.txt") == []

    def test_skips_comments_and_blanks(self, tmp_path: Path) -> None:
        f = tmp_path / "by.txt"
        f.write_text("# header\n\nhttps://x/a\n   \n# c\nhttps://x/b\n", encoding="utf-8")
        entries = parse_by_elections_file(f)
        assert [e.url for e in entries] == ["https://x/a", "https://x/b"]

    def test_bare_url_has_no_overrides(self, tmp_path: Path) -> None:
        f = tmp_path / "by.txt"
        f.write_text("https://x/a\n", encoding="utf-8")
        entry = parse_by_elections_file(f)[0]
        assert entry == ByElectionEntry(url="https://x/a", parent_election=None, map_name=None)

    def test_pipe_overrides_parsed(self, tmp_path: Path) -> None:
        f = tmp_path / "by.txt"
        f.write_text("https://x/a | 2019 General Election | Old Map\n", encoding="utf-8")
        entry = parse_by_elections_file(f)[0]
        assert entry.parent_election == "2019 General Election"
        assert entry.map_name == "Old Map"

    def test_blank_override_field_is_none(self, tmp_path: Path) -> None:
        f = tmp_path / "by.txt"
        f.write_text("https://x/a |  | Only Map\n", encoding="utf-8")
        entry = parse_by_elections_file(f)[0]
        assert entry.parent_election is None
        assert entry.map_name == "Only Map"


class TestByElectionSteps:
    """Covers by_election_steps — arg construction per entry."""

    def test_bare_entry_url_and_refresh_only(self) -> None:
        steps = by_election_steps([ByElectionEntry(url="https://x/2025_foo_by-election")])
        assert steps[0].script == BY_ELECTION_IMPORT
        assert steps[0].args == ("--url", "https://x/2025_foo_by-election", "--refresh")
        assert steps[0].label.endswith("2025_foo_by-election")

    def test_overrides_appear_in_args(self) -> None:
        entry = ByElectionEntry(url="https://x/a", parent_election="2019 GE", map_name="Old Map")
        args = by_election_steps([entry])[0].args
        assert args == (
            "--url", "https://x/a",
            "--parent-election", "2019 GE",
            "--map-name", "Old Map",
            "--refresh",
        )


class TestUsElectionSteps:
    """Covers us_election_steps — file naming to importer + election name."""

    def _write(self, d: Path, name: str) -> None:
        (d / name).write_text("{}", encoding="utf-8")

    def test_maps_chamber_year_to_name_and_importer(self, tmp_path: Path) -> None:
        self._write(tmp_path, "house-2024.json")
        self._write(tmp_path, "senate-2020.json")
        self._write(tmp_path, "presidential-1968.json")
        steps = us_election_steps(tmp_path)
        by_script = {s.script.name: s for s in steps}
        assert by_script["import_house_elections.py"].args[:6] == (
            "--file", str(tmp_path / "house-2024.json"),
            "--year", "2024",
            "--name", "2024 US House Election",
        )[:6]
        assert "2024 US House Election" in by_script["import_house_elections.py"].args
        assert "2020 US Senate Election" in by_script["import_senate_elections.py"].args
        assert "1968 US Presidential Election" in by_script["import_presidential_elections.py"].args

    def test_all_steps_refresh(self, tmp_path: Path) -> None:
        self._write(tmp_path, "house-2024.json")
        assert us_election_steps(tmp_path)[0].args[-1] == "--refresh"

    def test_ignores_unrecognised_files(self, tmp_path: Path) -> None:
        self._write(tmp_path, "notes.json")
        self._write(tmp_path, "house-notayear.json")
        assert us_election_steps(tmp_path) == []


class TestBuildSteps:
    """Covers build_steps — full pipeline shape and ordering."""

    def test_starts_with_parties_ends_with_export(self) -> None:
        steps = build_steps()
        assert steps[0].label == "Import parties"
        assert steps[-1].label == "Export elections to static data"
        assert steps[-1].script.name == "export_elections.py"

    def test_includes_core_importers_in_order(self) -> None:
        labels = [s.label for s in build_steps()]
        # Geometry must precede region populations (which match regions by name).
        assert labels.index("Import Westminster geometry") < labels.index(
            "Import region populations"
        )
        assert "Import Holyrood elections" in labels

    def test_refresh_importers_carry_the_flag(self) -> None:
        by_label = {s.label: s for s in build_steps()}
        assert "--refresh" in by_label["Import Westminster general elections"].args
        assert "--refresh" in by_label["Import Holyrood elections"].args


class TestRunStep:
    """Covers run_step — exit codes, missing script, timeout, spawn failure."""

    def _script(self, tmp_path: Path, body: str) -> Path:
        f = tmp_path / "step.py"
        f.write_text(body, encoding="utf-8")
        return f

    def test_zero_exit_is_ok(self, tmp_path: Path) -> None:
        script = self._script(tmp_path, "print('done')")
        ok, output = run_step(Step("s", script, ()), timeout=30)
        assert ok is True
        assert "done" in output

    def test_nonzero_exit_is_failure(self, tmp_path: Path) -> None:
        script = self._script(tmp_path, "import sys; sys.stderr.write('boom'); sys.exit(2)")
        ok, output = run_step(Step("s", script, ()), timeout=30)
        assert ok is False
        assert "boom" in output

    def test_missing_script_is_failure(self, tmp_path: Path) -> None:
        ok, output = run_step(Step("s", tmp_path / "nope.py", ()), timeout=30)
        assert ok is False
        assert "not found" in output

    def test_timeout_is_failure(self, tmp_path: Path) -> None:
        script = self._script(tmp_path, "import time; time.sleep(10)")
        ok, output = run_step(Step("s", script, ()), timeout=1)
        assert ok is False
        assert "timed out" in output

    def test_spawn_oserror_is_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        script = self._script(tmp_path, "print('unused')")

        def boom(*_a: object, **_k: object) -> None:
            raise OSError("cannot spawn")

        monkeypatch.setattr("scripts.rebuild_database.subprocess.run", boom)
        ok, output = run_step(Step("s", script, ()), timeout=30)
        assert ok is False
        assert "failed to launch" in output


class TestMainAccounting:
    """Covers main — continue-on-failure, exit code, and dry-run."""

    def test_dry_run_runs_nothing_and_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls: list[str] = []

        def fake_run(step: Step, *, timeout: int) -> tuple[bool, str]:
            calls.append(step.label)
            return True, ""

        monkeypatch.setattr(rebuild, "run_step", fake_run)
        monkeypatch.setattr("sys.argv", ["rebuild_database.py", "--dry-run"])
        assert main() == 0
        assert calls == []
        assert "dry run" in capsys.readouterr().out

    def test_all_success_returns_zero_and_runs_every_step(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        expected = len(build_steps())
        calls: list[str] = []

        def fake_run(step: Step, *, timeout: int) -> tuple[bool, str]:
            calls.append(step.label)
            return True, "ok"

        monkeypatch.setattr(rebuild, "run_step", fake_run)
        monkeypatch.setattr("sys.argv", ["rebuild_database.py"])
        assert main() == 0
        assert len(calls) == expected

    def test_one_failure_still_runs_all_and_exits_nonzero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        expected = len(build_steps())
        calls: list[str] = []

        def fake_run(step: Step, *, timeout: int) -> tuple[bool, str]:
            calls.append(step.label)
            # Fail exactly one middle step; every other step must still run.
            return (step.label != "Import Holyrood elections", "out")

        monkeypatch.setattr(rebuild, "run_step", fake_run)
        monkeypatch.setattr("sys.argv", ["rebuild_database.py"])
        assert main() == 1
        assert len(calls) == expected  # continue-on-failure: all attempted
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "Import Holyrood elections" in out
