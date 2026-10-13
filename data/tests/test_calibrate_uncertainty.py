"""Tests for ``scripts/calibrate_uncertainty.py`` and its committed outputs.

A synthetic database holds one same-boundary election pair whose seat
results were generated with a known swing-type factor λ and residual SD, so
the script must recover both through its real read path. Each test points
``DATABASE_PATH`` and every output at temporary files; the live database and
the committed files are never written.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import statistics
from contextlib import closing
from pathlib import Path

import pytest

from model_support.calibration import (
    CALIBRATION_REPORT_PATH,
    FINAL_POLL_ERRORS_PATH,
    FINAL_POLL_SCHEMA,
    POLLBASE_URL,
    load_calibration_report,
    load_final_poll_table,
)
from model_support.io import write_text_files
from model_support.uncertainty import (
    UNCERTAINTY_CONFIG_PATH,
    UncertaintyConfig,
    load_uncertainty_config,
    share_scale,
)
from scripts import calibrate_uncertainty as script

LAB, REFORM, CON, LD, GREEN, OTHER, OTHERS = 1, 2, 4, 5, 6, 7, 15
PARTIES = {
    LAB: "Labour",
    REFORM: "Reform UK",
    CON: "Conservative",
    LD: "Liberal Democrats",
    GREEN: "Green",
    OTHER: "Other",
    OTHERS: "Others",
}
GB_REGIONS = {1: "London", 2: "Wales", 3: "Scotland"}
NI_REGION = 4
# Regional change of each fitted party, per region.
REGION_SWING = {
    1: {CON: -5.0, LAB: 6.0, LD: 2.0},
    2: {CON: 3.0, LAB: -4.0, LD: -1.0},
    3: {CON: -2.0, LAB: -3.0, LD: 4.0},
}
SEATS_PER_REGION = 150
TURNOUT = 10_000.0
INJECTED_LAMBDA = 0.4
INJECTED_SIGMA = 2.5
SPEAKER = "Speaker Seat"
CONFIG = UncertaintyConfig()


SeatShares = dict[int, dict[int, float]]


def _seat_shares(seed: int) -> tuple[SeatShares, SeatShares]:
    """Earlier and later seat shares with λ and σ injected (seat id keys).

    Every seat polls the same total, so a region's share is the mean of its
    seats'. Noise is centred per region and party, so each region's change
    is exactly ``REGION_SWING``. The rest of each seat goes to "Other" in the
    earlier election and "Others" in the later one, both of which the model
    aliases to Others and the fit leaves out.
    """
    rng = random.Random(seed)
    earlier: dict[int, dict[int, float]] = {}
    later: dict[int, dict[int, float]] = {}
    seat_id = 0
    for region_id, swings in REGION_SWING.items():
        seats = list(range(seat_id + 1, seat_id + SEATS_PER_REGION + 1))
        seat_id += SEATS_PER_REGION
        for seat in seats:
            earlier[seat] = {
                CON: rng.uniform(15, 35),
                LAB: rng.uniform(15, 35),
                LD: rng.uniform(4, 16),
            }
        for party, delta in swings.items():
            region0 = statistics.fmean(earlier[seat][party] for seat in seats)
            # Each seat's predicted share before noise (the seat totals stay
            # near 100, so this is close to the engine-style p̂ the fit uses).
            predicted = {
                seat: earlier[seat][party]
                + delta
                + INJECTED_LAMBDA * (delta * earlier[seat][party] / region0 - delta)
                for seat in seats
            }
            noise = [
                rng.gauss(0.0, INJECTED_SIGMA) * share_scale(predicted[seat], CONFIG)
                for seat in seats
            ]
            centre = statistics.fmean(noise)
            for seat, error in zip(seats, noise, strict=True):
                later.setdefault(seat, {})[party] = predicted[seat] + error - centre
        for seat in seats:
            earlier[seat][OTHER] = 100 - sum(earlier[seat].values())
            later[seat][OTHERS] = 100 - sum(later[seat].values())
            assert min(later[seat].values()) > 0
    return earlier, later


def _seed_database(path: Path) -> tuple[SeatShares, SeatShares]:
    earlier, later = _seat_shares(seed=7)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE maps (id INTEGER PRIMARY KEY, name TEXT, parliament TEXT);
            CREATE TABLE regions (id INTEGER PRIMARY KEY, map_id INTEGER, name TEXT);
            CREATE TABLE parties (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE seats (
                id INTEGER PRIMARY KEY, map_id INTEGER, seat_name TEXT,
                region_id INTEGER
            );
            CREATE TABLE elections (
                id INTEGER PRIMARY KEY, map_id INTEGER, year INTEGER, name TEXT,
                type TEXT
            );
            CREATE TABLE votes (
                id INTEGER PRIMARY KEY, election_id INTEGER, seat_id INTEGER,
                party_id INTEGER, vote_total REAL
            );
            CREATE TABLE pollsters (id INTEGER PRIMARY KEY, name TEXT, identifier TEXT);
            CREATE TABLE polls (
                id INTEGER PRIMARY KEY, pollster_id INTEGER, map_id INTEGER,
                sample_size INTEGER, seat_id INTEGER
            );
            CREATE TABLE poll_bases (
                id INTEGER PRIMARY KEY, poll_id INTEGER, region_id INTEGER,
                unweighted_n INTEGER, weighted_n INTEGER, kind TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO maps VALUES (?, ?, ?)",
            [(1, "Test map", "westminster"), (2, "Holyrood map", "holyrood")],
        )
        regions = {**GB_REGIONS, NI_REGION: "Northern Ireland"}
        conn.executemany(
            "INSERT INTO regions VALUES (?, 1, ?)", list(regions.items())
        )
        conn.executemany("INSERT INTO parties VALUES (?, ?)", list(PARTIES.items()))
        conn.executemany(
            "INSERT INTO elections VALUES (?, 1, ?, ?, 'uk_general')",
            [(10, 2019, "E0"), (11, 2024, "E1")],
        )
        seat_rows = [
            (seat, 1, f"Seat {seat}", 1 + (seat - 1) // SEATS_PER_REGION)
            for seat in earlier
        ]
        speaker = len(seat_rows) + 1
        seat_rows.append((speaker, 1, SPEAKER, NI_REGION))
        conn.executemany("INSERT INTO seats VALUES (?, ?, ?, ?)", seat_rows)
        votes: list[tuple[int, int, int | None, float]] = [
            (election, seat, party, share * TURNOUT / 100)
            for election, shares in ((10, earlier), (11, later))
            for seat, parties in shares.items()
            for party, share in parties.items()
        ]
        votes += [
            (10, speaker, OTHER, 6000.0),
            (10, speaker, CON, 2000.0),
            (10, speaker, LAB, 2000.0),
            (11, speaker, OTHER, 5500.0),
            (11, speaker, LAB, 2500.0),
            (11, speaker, CON, 2000.0),
            # A null-party row is ignored.
            (11, speaker, None, 100.0),
        ]
        conn.executemany(
            "INSERT INTO votes (election_id, seat_id, party_id, vote_total) "
            "VALUES (?, ?, ?, ?)",
            votes,
        )
        conn.executemany(
            "INSERT INTO pollsters VALUES (?, ?, ?)",
            [
                (1, "More in Common", "more_in_common"),
                (2, "Opinium", "opinium"),
                (3, "Find Out Now", "find_out_now"),
            ],
        )
        conn.executemany(
            "INSERT INTO polls VALUES (?, ?, ?, ?, ?)",
            [
                (1, 1, 1, 2000, None),
                (2, 1, 1, 2000, None),
                (3, 2, 1, 1500, None),
                (4, 3, 1, 2000, None),
                (5, 1, 2, 2000, None),  # Holyrood map: ignored
                (6, 1, 1, 1000, 3),  # constituency poll: ignored
            ],
        )
        conn.executemany(
            "INSERT INTO poll_bases "
            "(poll_id, region_id, unweighted_n, weighted_n, kind) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (1, None, 1400, None, "decided"),
                (2, None, 1520, 1490, "decided"),
                (2, 1, 300, None, "decided"),  # regional: ignored
                (3, None, 1500, None, "decided"),
                (4, None, None, 1800, "decided"),
                (5, None, 500, None, "decided"),
                (6, None, 100, None, "decided"),
            ],
        )
    return earlier, later


def _final_poll_table(path: Path) -> None:
    table = {
        "schema": FINAL_POLL_SCHEMA,
        "credit": {
            "text": f"Final-poll data: Mark Pack, PollBase — {POLLBASE_URL}",
            "name": "Mark Pack, PollBase",
            "url": POLLBASE_URL,
            "edition": "PollBase-test.xlsx",
        },
        "elections": [
            {
                "year": 2024,
                "polling_day": "2024-07-04",
                "source": {"url": POLLBASE_URL},
                "parties": {
                    "con": {"label": "Conservative", "average": 22.0, "result": 25.0,
                            "pollsters": 3},
                    "lab": {"label": "Labour", "average": 32.0, "result": 30.0,
                            "pollsters": 3},
                },
                "final_polls": [{}, {}, {}],
            }
        ],
    }  # fmt: skip
    path.write_text(json.dumps(table), encoding="utf-8")


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A seeded database, a final-poll table, and output paths, all temporary."""
    database = tmp_path / "calibration.db"
    _seed_database(database)
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setattr(script, "ELECTION_PAIRS", (("E0→E1", "E0", "E1"),))
    monkeypatch.setattr(script, "SPEAKER_SEATS", {"E0": SPEAKER, "E1": SPEAKER})
    monkeypatch.setattr(script, "CROSS_CHECK_ELECTIONS", {2024: "E1"})
    table = tmp_path / "final_polls.json"
    _final_poll_table(table)
    config = tmp_path / "uncertainty_config.json"
    # A non-default, uncalibrated setting the run must keep.
    config.write_text(json.dumps({"sigma_reg": 1.7}), encoding="utf-8")
    return {
        "database": database,
        "table": table,
        "config": config,
        "report": tmp_path / "report.json",
    }


def _args(world: dict[str, Path], *extra: str) -> list[str]:
    return [
        "--final-polls", str(world["table"]),
        "--config", str(world["config"]),
        "--report", str(world["report"]),
        *extra,
    ]  # fmt: skip


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestScript:
    def test_dry_run_writes_nothing(
        self, world: dict[str, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        config_before = world["config"].read_text(encoding="utf-8")
        assert script.main(_args(world, "--dry-run")) == 0
        assert not world["report"].exists()
        assert world["config"].read_text(encoding="utf-8") == config_before
        out = capsys.readouterr().out
        assert "Dry-run complete. Nothing written." in out.splitlines()
        assert "λ̂ and σ_seat per election pair:" in out.splitlines()

    def test_recovers_injected_lambda_and_sigma_seat(
        self, world: dict[str, Path]
    ) -> None:
        assert script.main(_args(world)) == 0
        report = load_calibration_report(world["report"])
        (pair,) = report["swing"]["pairs"]
        assert pair["lambda"] == pytest.approx(INJECTED_LAMBDA, abs=0.05)
        assert pair["sigma_seat_realised"] == pytest.approx(INJECTED_SIGMA, rel=0.1)
        assert pair["predicted_zero"] == 0
        assert pair["no_seat_noise"] == 0
        # Raw: each u ÷ its own cell's centring ratio (all below 1).
        assert pair["sigma_seat_raw"] > pair["sigma_seat_realised"]
        contenders, others = pair["ratios_contenders"], pair["ratios_others"]
        assert contenders["count"] + others["count"] == pair["residuals_used"]
        assert 0.7 < contenders["median"] < others["median"] < 1
        assert pair["cells"] == 3 * SEATS_PER_REGION * 3
        # Speaker: Con, Lab and Other (aliased to Others) in one seat; Others
        # in every other seat.
        assert pair["exclusions"]["speaker_seat"] == 3
        assert pair["exclusions"]["others_aggregate"] == 3 * SEATS_PER_REGION
        # "Other" (earlier) and "Others" (later) are one party after aliasing,
        # so neither counts as absent from an election.
        assert pair["exclusions"]["party_absent"] == 0
        names = {party["name"] for party in pair["parties"]}
        assert names == {"Conservative", "Labour", "Liberal Democrats"}

    def test_report_shows_how_each_value_was_built(
        self, world: dict[str, Path]
    ) -> None:
        assert script.main(_args(world)) == 0
        report = load_calibration_report(world["report"])
        (pair,) = report["swing"]["pairs"]
        assert pair["lambda"] == pytest.approx(
            pair["cross"] / pair["lever_squares"], abs=1e-3
        )
        residuals = pair["residuals"]
        assert sum(residuals["counts"]) + residuals["below"] + residuals["above"] == (
            pair["cells"]
        )
        assert set(residuals["percentiles"]) == {
            "5", "10", "25", "50", "75", "90", "95",
        }  # fmt: skip
        assert report["swing"]["pooled"]["residuals"] == residuals
        national = report["national"]
        (election,) = national["elections"]
        z2 = [party["z2"] for party in election["parties"]]
        assert election["mean_z2"] == pytest.approx(sum(z2) / 2, abs=1e-3)
        root = national["mean_z2"] ** 0.5
        assert national["sigma_nat_realised"] == pytest.approx(root, abs=1e-3)
        z_raw = [party["z_raw"] for party in election["parties"]]
        assert national["mean_z2_raw"] == pytest.approx(
            sum(z * z for z in z_raw) / 2, abs=1e-3
        )
        decided = report["decided_fraction"]
        assert decided["ratio_sum"] == pytest.approx(0.70 + 0.76)
        lines = {row["name"]: row["derivation"] for row in report["parameters"]}
        raw = round(report["swing"]["pooled"]["sigma_seat_raw"], 2)
        assert lines["sigma_seat"].startswith(f"{raw} pt (raw, engine units): ")
        assert f"MAD of {pair['cells']} residuals u" in lines["sigma_seat"]
        assert "RMS of 2 party × election misses" in lines["sigma_nat"]
        assert "each z first divided by its own party's share" in lines["sigma_nat"]
        assert "over 2 polls (more_in_common 0.73 (2))" in lines["decided_fraction"]
        assert lines["sigma_reg"] == (
            "1.7 points at p_ref: SD of a region's own polling error beyond "
            "sampling. No historical regional poll data to calibrate it. (guess; "
            "not fitted here)."
        )

    def test_writes_a_config_the_loader_accepts(self, world: dict[str, Path]) -> None:
        assert script.main(_args(world)) == 0
        config = load_uncertainty_config(world["config"])
        report = load_calibration_report(world["report"])
        pooled = report["swing"]["pooled"]
        assert config.sigma_seat == round(pooled["sigma_seat_raw"], 2)
        assert pooled["sigma_seat_raw"] > pooled["sigma_seat_realised"]
        assert config.sigma_lambda == round(pooled["sigma_lambda"], 2)
        # One pair: σ_λ is |λ̂|.
        assert pooled["sigma_lambda"] == pytest.approx(abs(pooled["mean_lambda"]))
        assert config.sigma_reg == 1.7
        assert config.decided_fraction == 0.73
        assert report["config"] == json.loads(world["config"].read_text("utf-8"))

    def test_sigma_nat_from_the_table(self, world: dict[str, Path]) -> None:
        assert script.main(_args(world)) == 0
        report = load_calibration_report(world["report"])
        z_con = -3.0 / share_scale(25.0, CONFIG)
        z_lab = 2.0 / share_scale(30.0, CONFIG)
        expected = ((z_con**2 + z_lab**2) / 2) ** 0.5
        national = report["national"]
        assert national["sigma_nat_realised"] == pytest.approx(expected, abs=1e-4)
        # Con 25 and Lab 30 plus a 45-point remainder party; each z is
        # divided by its own party's ratio.
        s25, s30, s45 = (share_scale(v, CONFIG) for v in (25.0, 30.0, 45.0))
        total = s25**2 + s30**2 + s45**2
        r_con, r_lab = (
            ((s**2) * (1 - 2 * q) + q**2 * total) ** 0.5 / s
            for s, q in ((s25, 0.25), (s30, 0.30))
        )
        raw = (((z_con / r_con) ** 2 + (z_lab / r_lab) ** 2) / 2) ** 0.5
        assert national["sigma_nat_raw"] == pytest.approx(raw, abs=1e-4)
        (election,) = national["elections"]
        ratios = {party["key"]: party["ratio"] for party in election["parties"]}
        assert ratios == {
            "con": pytest.approx(r_con, abs=1e-4),
            "lab": pytest.approx(r_lab, abs=1e-4),
        }
        config = load_uncertainty_config(world["config"])
        assert config.sigma_nat == round(raw, 2)

    def test_decided_fraction_derivation(self, world: dict[str, Path]) -> None:
        assert script.main(_args(world)) == 0
        decided = load_calibration_report(world["report"])["decided_fraction"]
        assert decided["polls"] == 2
        assert decided["value"] == pytest.approx((0.70 + 0.76) / 2)
        assert decided["exclusions"] == {
            "weighted_only": 1,
            "equals_sample_size": 1,
            "exceeds_sample_size": 0,
        }
        assert [row["pollster"] for row in decided["pollsters"]] == ["more_in_common"]

    def test_cross_check_uses_gb_seats_only(self, world: dict[str, Path]) -> None:
        _earlier, later = _seat_shares(seed=7)
        assert script.main(_args(world)) == 0
        checks = load_calibration_report(world["report"])["national"]["cross_check"]
        con = next(check for check in checks if check["key"] == "con")
        expected = statistics.fmean(seat[CON] for seat in later.values())
        assert con["database"] == pytest.approx(expected, abs=0.01)
        assert con["pollbase"] == 25.0

    def test_report_carries_the_credit(self, world: dict[str, Path]) -> None:
        assert script.main(_args(world)) == 0
        report = load_calibration_report(world["report"])
        assert report["credit"]["url"] == POLLBASE_URL
        assert report["credit"]["edition"] == "PollBase-test.xlsx"
        assert {row["name"] for row in report["parameters"]} == set(
            UncertaintyConfig.__dataclass_fields__
        )

    def test_database_is_not_modified(self, world: dict[str, Path]) -> None:
        before = _digest(world["database"])
        assert script.main(_args(world)) == 0
        assert _digest(world["database"]) == before

    def test_missing_speaker_seat_fails_without_writing(
        self, world: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(script, "SPEAKER_SEATS", {"E0": "Nowhere", "E1": SPEAKER})
        config_before = world["config"].read_text(encoding="utf-8")
        assert script.main(_args(world)) == 1
        assert not world["report"].exists()
        assert world["config"].read_text(encoding="utf-8") == config_before

    def test_write_failure_is_a_clean_error(
        self,
        world: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def fail(outputs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(script, "write_text_files", fail)
        config_before = world["config"].read_text(encoding="utf-8")
        assert script.main(_args(world)) == 1
        assert "error: disk full" in capsys.readouterr().out.splitlines()
        assert world["config"].read_text(encoding="utf-8") == config_before
        assert not world["report"].exists()

    def test_written_files_are_world_readable(self, world: dict[str, Path]) -> None:
        assert script.main(_args(world)) == 0
        assert world["report"].stat().st_mode & 0o777 == 0o644
        assert world["config"].stat().st_mode & 0o777 == 0o644

    def test_missing_election_fails(
        self, world: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(script, "ELECTION_PAIRS", (("x", "E0", "E9"),))
        assert script.main(_args(world)) == 1
        assert not world["report"].exists()

    def test_write_text_files_leaves_no_temporaries(self, tmp_path: Path) -> None:
        first, second = tmp_path / "a.json", tmp_path / "b.json"
        write_text_files({first: "1\n", second: "2\n"})
        assert (first.read_text(), second.read_text()) == ("1\n", "2\n")
        assert sorted(p.name for p in tmp_path.iterdir()) == ["a.json", "b.json"]

    def test_a_failed_write_leaves_every_destination_alone(
        self, tmp_path: Path
    ) -> None:
        first = tmp_path / "a.json"
        first.write_text("old\n", encoding="utf-8")
        missing = tmp_path / "no-such-dir" / "b.json"
        with pytest.raises(OSError):
            write_text_files({first: "new\n", missing: "2\n"})
        assert first.read_text(encoding="utf-8") == "old\n"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["a.json"]


class TestCommittedFiles:
    """The committed calibration inputs and outputs stay loadable and sourced."""

    def test_every_election_has_a_source_and_the_credit(self) -> None:
        table = load_final_poll_table(FINAL_POLL_ERRORS_PATH)
        assert table.credit["url"] == POLLBASE_URL
        assert table.credit["text"].startswith("Final-poll data: Mark Pack, PollBase")
        assert [e.year for e in table.elections] == [
            1992, 1997, 2001, 2005, 2010, 2015, 2017, 2019, 2024,
        ]  # fmt: skip
        for election in table.elections:
            assert election.source_url == POLLBASE_URL
            assert election.final_polls > 0
            assert {"con", "lab", "ld"} <= {party.key for party in election.parties}

    def test_committed_config_loads(self) -> None:
        config = load_uncertainty_config(UNCERTAINTY_CONFIG_PATH)
        report = load_calibration_report(CALIBRATION_REPORT_PATH)
        assert report["config"] == json.loads(
            UNCERTAINTY_CONFIG_PATH.read_text(encoding="utf-8")
        )
        national = report["national"]
        pooled = report["swing"]["pooled"]
        assert config.sigma_nat == round(national["sigma_nat_raw"], 2)
        assert config.sigma_seat == round(pooled["sigma_seat_raw"], 2)
        assert national["sigma_nat_raw"] > national["sigma_nat_realised"]
        assert pooled["sigma_seat_raw"] > pooled["sigma_seat_realised"]
