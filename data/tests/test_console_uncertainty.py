"""Tests for the console's uncertainty blueprint: the hub and the calibration view.

The settings and report paths are patched (autouse) to temporary files, so
the committed calibration files never decide what a test sees. Fixture
values are distinctive (no default or committed value), so a view that read
the wrong file would fail.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from console.blueprints.uncertainty import exclusion_pivot, fmt, party_lambda_pivot
from model_support.calibration import CALIBRATION_REPORT_SCHEMA, POLLBASE_URL

from tests.console_fixtures import app


@pytest.fixture(autouse=True)
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Point the settings and the report at temp files (absent until written)."""
    config = tmp_path / "uncertainty_config.json"
    report = tmp_path / "uncertainty_calibration_report.json"
    monkeypatch.setattr("model_support.uncertainty.UNCERTAINTY_CONFIG_PATH", config)
    monkeypatch.setattr("model_support.calibration.CALIBRATION_REPORT_PATH", report)
    return {"config": config, "report": report}


def fixture_report() -> dict[str, Any]:
    """A small report in the script's shape, with distinctive values."""
    return {
        "schema": CALIBRATION_REPORT_SCHEMA,
        "credit": {"url": POLLBASE_URL, "edition": "PollBase-test.xlsx"},
        "parameters": [
            {"name": "sigma_nat", "derivation": "2.72 pt: RMS of 2 test misses."},
            {"name": "sigma_seat", "derivation": 7},  # malformed: falls back
        ],
        "national": {
            "method": "RMS of z.",
            "sigma_nat_realised": 2.717,
            "mean_z2": 7.382,
            "sigma_nat_raw": 3.3074,
            "mean_z2_raw": 10.939,
            "ratios_contenders": {"count": 18, "p10": 0.795, "median": 0.8211,
                                  "p90": 0.843},
            "ratios_others": None,
            "centring_method": "Median contender ratio.",
            "sigma_nat_cells": 2.818,
            "sigma_nat_core": 2.111,
            "elections": [
                {
                    "year": 1992,
                    "polling_day": "1992-04-09",
                    "final_polls": 5,
                    "source_url": POLLBASE_URL,
                    "rms_z": 3.381,
                    "mean_z2": 11.4317,
                    "parties": [
                        {"key": "con", "label": "Conservative", "average": 38.3,
                         "result": 42.8, "scale": 1.08, "error": -4.5, "z": -4.1667,
                         "z2": 17.3614, "ratio": 0.7981, "z_raw": -5.2208},
                        {"key": "lab", "label": "Labour", "average": 39.4,
                         "result": 35.2, "scale": 1.042, "error": 4.2, "z": 4.0307,
                         "z2": 16.2465},
                    ],
                }
            ],
            "parties": [
                {"key": "con", "label": "Conservative", "cells": 9, "rms_z": 2.34}
            ],
            "cross_check": [
                {"year": 2019, "key": "con", "pollbase": 44.7, "database": 44.72,
                 "difference": -0.02}
            ],
        },
        "swing": {
            "method": "Least squares.",
            "centring_method": "Median over each seat's top two.",
            "speaker_seats": {"2024 General Election": "Chorley"},
            "exclusion_reasons": {"speaker_seat": "the Speaker's seat"},
            "pairs": [
                {"label": "2010→2015", "cells": 2810, "stood_down": 6, "lambda": 0.2381,
                 "cross": 14159.41, "lever_squares": 59374.52, "lambda2": 0.0567,
                 "residual_median": -0.267, "mad": 3.104,
                 "sigma_seat_realised": 4.6021, "residuals_used": 2719,
                 "predicted_zero": 91, "no_seat_noise": 2,
                 "raw_median": -0.45, "raw_mad": 3.3, "sigma_seat_raw": 4.8571,
                 "ratios_contenders": {"count": 1200, "p10": 0.77,
                                       "median": 0.8241, "p90": 0.87},
                 "ratios_others": {"count": 1519, "p10": 0.9, "median": 0.9381,
                                   "p90": 0.99},
                 "residuals": {"percentiles": {"5": -10.67, "50": -0.27},
                               "edges": [-1, 0, 1], "counts": [700, 650],
                               "below": 900, "above": 560},
                 "exclusions": {"speaker_seat": 3},
                 "parties": [{"party_id": 1, "name": "Labour", "cells": 631,
                              "lambda": 0.6731}]},
                {"label": "2019n→2024", "cells": 2626, "stood_down": 12,
                 "lambda": 0.5671,
                 "cross": 20659.2, "lever_squares": 36456.7, "lambda2": 0.3216,
                 "residual_median": -0.639, "mad": 4.185,
                 "sigma_seat_realised": 6.2051, "residuals_used": 2611,
                 "predicted_zero": 15, "no_seat_noise": 0,
                 "raw_median": -0.7, "raw_mad": 4.2, "sigma_seat_raw": 6.302,
                 "ratios_contenders": "garbled", "ratios_others": None,
                 "residuals": {"percentiles": {"5": -11.82, "50": -0.64},
                               "edges": [-1, 0, 1], "counts": "garbled",
                               "below": 1, "above": 2},
                 "exclusions": {"speaker_seat": 2},
                 "parties": [{"party_id": 4, "name": "Conservative", "cells": 631,
                              "lambda": 0.8241},
                             {"party_id": 1, "name": "Labour", "cells": 630,
                              "lambda": 0.1181}]},
            ],
            "pooled": {"pairs": 2, "mean_lambda2": 0.146, "sigma_lambda": 0.3821,
                       "mean_lambda": 0.3401, "cells": 11603,
                       "residual_median": -0.169, "mad": 2.878,
                       "sigma_seat_realised": 4.2671, "raw_median": -0.33,
                       "raw_mad": 3.31, "sigma_seat_raw": 4.9031,
                       "ratios_contenders": {"count": 5072, "p10": 0.774,
                                             "median": 0.822, "p90": 0.871},
                       "ratios_others": {"count": 6232, "p10": 0.904,
                                         "median": 0.962, "p90": 0.991},
                       "residuals": {"percentiles": {"5": -9.5, "50": -0.17},
                                     "edges": [-1, 0, 1], "counts": [1500, 1400],
                                     "below": 5000, "above": 3703}},
        },
        "decided_fraction": {
            "method": "Mean over polls.",
            "value": 0.7987,
            "ratio_sum": 27.1558,
            "rounded": 0.8,
            "polls": 34,
            "pollsters": [{"pollster": "more_in_common", "polls": 15,
                           "mean_ratio": 0.7529, "ratio_sum": 11.2941, "low": 0.695,
                           "high": 0.84}],
            "exclusions": {"weighted_only": 87},
            "exclusion_reasons": {"weighted_only": "only a weighted base"},
        },
        "config": {"sigma_nat": 2.72, "sigma_seat": 4.27},
    }  # fmt: skip


def _get(app: Flask, url: str = "/westminster/uncertainty/calibration") -> str:
    response = app.test_client().get(url)
    assert response.status_code == 200
    return html.unescape(response.get_data(as_text=True))


def _rows(body: str, header: str) -> list[list[str]]:
    """Return the cell texts of every table row whose row header is *header*."""
    matches = re.findall(
        rf"<tr>\s*<th scope=\"row\"[^>]*>\s*(?:<a [^>]*>)?{re.escape(header)}"
        rf"(?:</a>)?\s*</th>(.*?)</tr>",
        body,
        re.DOTALL,
    )
    return [
        [
            re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell)).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        ]
        for row in matches
    ]


def _row(body: str, header: str) -> list[str]:
    """Return the cells of the only row whose row header is *header*."""
    rows = _rows(body, header)
    assert len(rows) == 1, f"{len(rows)} rows headed {header!r}"
    return rows[0]


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_hub_links_to_the_calibration_view(app: Flask) -> None:
    body = _get(app, "/westminster/uncertainty")
    assert 'href="/westminster/uncertainty/calibration"' in body


def test_home_links_to_the_hub(app: Flask) -> None:
    body = _get(app, "/")
    assert 'href="/westminster/uncertainty"' in body


class TestCalibrationView:
    def test_settings_in_use_come_from_the_config_file(
        self, app: Flask, paths: dict[str, Path]
    ) -> None:
        settings = {"sigma_nat": 2.72, "sigma_seat": 4.27, "sigma_reg": 1.85}
        _write(paths["config"], settings)
        _write(paths["report"], fixture_report())
        body = _get(app)
        assert _row(body, "<code>sigma_nat</code>") == [
            "2.72", "points at p_ref", "calibrated", "2.72",
            "2.72 pt: RMS of 2 test misses.",
        ]  # fmt: skip
        # A malformed derivation falls back to the meaning and source.
        assert _row(body, "<code>sigma_seat</code>")[4] == (
            "SD of a seat's departure from its region's swing. (calibrated)"
        )
        sigma_reg = _row(body, "<code>sigma_reg</code>")
        assert sigma_reg[:3] == ["1.85", "points at p_ref", "guess"]
        # The report wrote no sigma_reg, so the value in use is flagged.
        assert sigma_reg[3] == "- (differs)"

    def test_every_setting_is_listed_with_a_meaning(self, app: Flask) -> None:
        body = _get(app)
        assert _row(body, "<code>sigma_nat</code>")[4] == (
            "SD of the industry-wide polling error, shared by every region. "
            "(calibrated); no calibration report to show its derivation"
        )
        design_effect = _row(body, "<code>design_effect</code>")
        assert design_effect[:3] == ["1.5", "×", "literature"]
        assert _row(body, "<code>decided_fraction</code>")[2] == "calibrated"
        assert _row(body, "<code>simulations</code>")[2] == "setting"

    def test_report_tables(self, app: Flask, paths: dict[str, Path]) -> None:
        _write(paths["report"], fixture_report())
        body = _get(app)
        assert "Realised σ_nat = <strong>2.717</strong>" in body
        flat = re.sub(r"\s+", " ", body)
        assert "Raw (engine) σ_nat = <strong>3.307</strong>:" in flat
        assert "Raw σ_nat = the same with z ÷ ratio = √10.939" in flat
        assert "2.111 over" in body
        assert _row(body, "1992") == [
            "5", "3.38", "Conservative", "38.30", "42.80", "-4.50", "1.080", "-4.17",
            "17.36", "11.43", "0.798", "-5.22",
        ]  # fmt: skip
        assert _row(body, "2019") == ["con", "44.70", "44.72", "-0.02"]
        # The λ̂ fit row, then the σ_seat row.
        assert _rows(body, "2010→2015") == [
            ["2810", "6", "14159.4", "59374.5", "+0.238", "0.057"],
            [
                "2719", "91", "2", "-0.267", "3.104", "4.602", "-0.450", "3.300",
                "4.857", "0.824 / 0.938",
            ],
        ]
        assert "√(mean over 2 pairs of λ̂²) = √0.146" in re.sub(r"\s+", " ", body)
        assert _row(body, "Pooled") == [
            "11603", "", "", "-0.169", "2.878", "4.267", "-0.330", "3.310", "4.903",
            "0.822 / 0.962",
        ]  # fmt: skip
        # A garbled or missing ratio summary shows dashes.
        assert _rows(body, "2019n→2024")[1][-1] == "- / -"
        assert _row(body, "σ_nat: each election's two largest parties") == [
            "18", "0.795", "0.821", "0.843",
        ]  # fmt: skip
        assert _row(body, "σ_nat: other parties") == ["-", "-", "-", "-"]
        assert _row(body, "σ_seat: other parties (pooled)") == [
            "6232", "0.904", "0.962", "0.991",
        ]  # fmt: skip
        assert "the election mean z²) = √7.382" in re.sub(r"\s+", " ", body)
        # Per-party λ̂ pivot: Labour in both pairs, Conservative only in the second.
        assert _row(body, "Labour") == ["+0.673 (631)", "+0.118 (630)"]
        # Conservative heads a by-party σ_nat row and a λ̂ pivot row.
        assert _rows(body, "Conservative") == [["9", "2.34"], ["-", "+0.824 (631)"]]
        assert _row(body, "the Speaker's seat") == ["3", "2"]
        assert _row(body, "more_in_common") == [
            "15", "0.7529", "11.294", "0.695", "0.840",
        ]  # fmt: skip
        assert _row(body, "All") == ["34", "27.156 ÷ 34 polls = 0.7987 → 0.8"]
        # Percentiles: each pair, then pooled.
        assert _row(body, "p5") == ["-10.67", "-11.82", "-9.50"]
        # Histogram: the garbled pair shows dashes; the bar scales to the
        # largest pooled bin (5000).
        assert _row(body, "[-1, 0)") == ["700", "-", "1500", ""]
        assert 'class="hist-bar" style="width: 30.0%"' in body
        assert _row(body, "< -1") == ["900", "-", "5000", ""]
        assert 'class="hist-bar" style="width: 100.0%"' in body
        assert "only a weighted base: 87" in body
        assert "written as <strong>0.8</strong>" in body

    def test_credit_links_to_pollbase(self, app: Flask) -> None:
        body = _get(app)
        assert f'<a href="{POLLBASE_URL}">{POLLBASE_URL}</a>' in body
        assert "Final-poll data: Mark Pack, PollBase" in body

    def test_missing_files_show_messages(
        self, app: Flask, paths: dict[str, Path]
    ) -> None:
        body = _get(app)
        assert f"No settings file at {paths['config']}" in body
        assert f"No calibration report at {paths['report']}." in body
        assert "σ_nat = " not in body

    def test_malformed_report_shows_a_message(
        self, app: Flask, paths: dict[str, Path]
    ) -> None:
        report = fixture_report()
        report["swing"]["pairs"] = {"not": "a list"}
        _write(paths["report"], report)
        body = _get(app)
        assert "report table 'pairs' must be a list" in body

    def test_non_https_source_is_refused_not_linked(
        self, app: Flask, paths: dict[str, Path]
    ) -> None:
        report = fixture_report()
        report["national"]["elections"][0]["source_url"] = "javascript:alert(1)"
        _write(paths["report"], report)
        body = _get(app)
        assert "election source_url must be https" in body
        assert 'href="javascript' not in body

    def test_malformed_config_shows_a_message(
        self, app: Flask, paths: dict[str, Path]
    ) -> None:
        _write(paths["config"], {"sigma_nat": "big"})
        body = _get(app)
        assert "Settings file unreadable (sigma_nat must be a number" in body
        assert _row(body, "<code>sigma_nat</code>")[0] == "2.5"

    def test_garbled_numbers_render_as_dashes(
        self, app: Flask, paths: dict[str, Path]
    ) -> None:
        report = fixture_report()
        report["swing"]["pairs"][0]["lambda"] = "big"
        report["national"]["elections"][0]["parties"][0]["z"] = None
        _write(paths["report"], report)
        body = _get(app)
        assert _rows(body, "2010→2015")[0][4] == "-"
        assert _row(body, "1992")[7] == "-"


def test_fmt() -> None:
    assert fmt(1.23456) == "1.23"
    assert fmt(2, "+.1f") == "+2.0"
    assert fmt(None) == "-"
    assert fmt(True) == "-"
    assert fmt(float("nan")) == "-"
    assert fmt("1.0") == "-"


def test_pivots_align_to_pairs() -> None:
    pairs = fixture_report()["swing"]["pairs"]
    rows = party_lambda_pivot(pairs)
    assert [row.label for row in rows] == ["Labour", "Conservative"]
    assert rows[1].cells[0] is None
    reasons = exclusion_pivot(pairs, {"speaker_seat": "Speaker"})
    assert [(row.label, row.cells) for row in reasons] == [("Speaker", (3, 2))]
