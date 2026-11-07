"""Tests for ``model_support.uncertainty``: settings and sampling-error inputs.

Expected values are worked by hand from the formulas (shares in percent,
errors in points), with real-ish poll bases: Opinium's London decided base of
204 and YouGov's London all-adult base of 218.
"""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

import pytest

from model_support.poll_bases import UNRESOLVED, NBasis, ResolvedN
from model_support.uncertainty import (
    AverageVariance,
    EffectiveN,
    UncertaintyConfig,
    UncertaintyConfigError,
    UncertaintyError,
    effective_n,
    kind_factor,
    kish_effective_count,
    load_uncertainty_config,
    poll_moe,
    share_scale,
    weighted_average_variance,
)

DEFAULTS = UncertaintyConfig()
# Every setting changed from its default, so a field the loader forgot to
# read would come back as the default and fail the round trip.
NON_DEFAULT_SETTINGS: dict[str, float | int] = {
    "design_effect": 1.8,
    "n_eff_cap": 2500.0,
    "interval": 0.8,
    "p_ref": 0.25,
    "decided_fraction": 0.7,
    "likely_voters_fraction": 0.85,
    "sigma_nat": 2.1,
    "sigma_reg": 1.2,
    "sigma_seat": 3.7,
    "sigma_lambda": 0.25,
    "lambda_min_share": 2.0,
    "min_polled_share": 0.25,
    "simulations": 5000,
    "chunk_size": 500,
    "majority_threshold": 301,
}


# ── UncertaintyConfig ─────────────────────────────────────────────────────────


class TestUncertaintyConfig:
    def test_defaults(self) -> None:
        assert dataclasses.asdict(DEFAULTS) == {
            "design_effect": 1.5,
            "n_eff_cap": 3000.0,
            "interval": 0.90,
            "p_ref": 0.30,
            "decided_fraction": 0.80,
            "likely_voters_fraction": 0.9,
            "sigma_nat": 2.5,
            "sigma_reg": 1.5,
            "sigma_seat": 3.0,
            "sigma_lambda": 0.3,
            "lambda_min_share": 1.0,
            "min_polled_share": 0.5,
            "simulations": 10_000,
            "chunk_size": 1_000,
            "majority_threshold": 326,
        }

    @pytest.mark.parametrize(
        ("interval", "z"), [(0.90, 1.6449), (0.95, 1.9600), (0.50, 0.6745)]
    )
    def test_z_follows_the_interval(self, interval: float, z: float) -> None:
        config = UncertaintyConfig(interval=interval)

        assert config.z == pytest.approx(z, abs=1e-4)

    def test_non_default_settings_cover_every_field(self) -> None:
        names = {field.name for field in dataclasses.fields(UncertaintyConfig)}
        assert set(NON_DEFAULT_SETTINGS) == names
        assert all(
            getattr(DEFAULTS, name) != value
            for name, value in NON_DEFAULT_SETTINGS.items()
        )

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("design_effect", 0.0),
            ("design_effect", -1.5),
            ("design_effect", math.inf),
            ("n_eff_cap", 0.0),
            ("interval", 0.0),
            ("interval", 1.0),
            ("p_ref", 0.0),
            ("p_ref", 1.0),
            ("decided_fraction", 0.0),
            ("decided_fraction", 1.01),
            ("likely_voters_fraction", 0.0),
            ("likely_voters_fraction", 1.2),
            ("sigma_nat", -0.1),
            ("sigma_reg", math.nan),
            ("sigma_seat", -3.0),
            ("sigma_lambda", -0.3),
            ("lambda_min_share", 0.0),
            ("lambda_min_share", 100.5),
            ("min_polled_share", -0.1),
            ("min_polled_share", 100.0),
            ("simulations", 0),
            ("chunk_size", -1),
            ("majority_threshold", 0),
        ],
    )
    def test_out_of_range_value_raises(self, name: str, value: float) -> None:
        with pytest.raises(UncertaintyConfigError, match=name):
            UncertaintyConfig(**{name: value})  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("decided_fraction", 1.0),
            ("likely_voters_fraction", 1.0),
            ("sigma_nat", 0.0),
            ("sigma_lambda", 0.0),
            ("lambda_min_share", 100.0),
            ("min_polled_share", 0.0),
        ],
    )
    def test_boundary_value_is_accepted(self, name: str, value: float) -> None:
        config = UncertaintyConfig(**{name: value})  # type: ignore[arg-type]

        assert getattr(config, name) == value


class TestLoadUncertaintyConfig:
    def test_missing_file_gives_the_defaults(self, tmp_path: Path) -> None:
        assert load_uncertainty_config(tmp_path / "absent.json") == DEFAULTS

    def test_round_trips_every_setting(self, tmp_path: Path) -> None:
        expected = UncertaintyConfig(**NON_DEFAULT_SETTINGS)  # type: ignore[arg-type]
        path = tmp_path / "uncertainty_config.json"
        path.write_text(json.dumps(dataclasses.asdict(expected)), encoding="utf-8")

        assert load_uncertainty_config(path) == expected

    def test_missing_keys_keep_their_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "uncertainty_config.json"
        path.write_text('{"sigma_nat": 2.2, "simulations": 2000}', encoding="utf-8")

        assert load_uncertainty_config(path) == dataclasses.replace(
            DEFAULTS, sigma_nat=2.2, simulations=2000
        )

    def test_integer_json_number_is_read_as_a_float(self, tmp_path: Path) -> None:
        path = tmp_path / "uncertainty_config.json"
        path.write_text('{"design_effect": 2}', encoding="utf-8")

        config = load_uncertainty_config(path)

        assert (type(config.design_effect), config.design_effect) == (float, 2.0)

    def test_default_path_is_read_at_call_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "uncertainty_config.json"
        path.write_text('{"sigma_seat": 4.4}', encoding="utf-8")
        monkeypatch.setattr("model_support.uncertainty.UNCERTAINTY_CONFIG_PATH", path)

        assert load_uncertainty_config().sigma_seat == 4.4

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            (
                '{"sigma_nat": 2.5, "sigma_national": 2.0}',
                "unknown settings: sigma_national$",
            ),
            ('{"design_effect": "1.5"}', "design_effect must be a number"),
            ('{"design_effect": true}', "design_effect must be a number"),
            ('{"simulations": 10000.0}', "simulations must be an integer"),
            ('{"simulations": false}', "simulations must be an integer"),
            ('{"interval": 1.5}', r"interval must be in \(0, 1\)"),
            ('{"design_effect": Infinity}', "design_effect must be positive"),
            ('{"n_eff_cap": 1' + "0" * 400 + "}", "n_eff_cap is out of range"),
            ("[1.5]", "must hold a JSON object"),
            ("{not json", "is not valid JSON"),
        ],
    )
    def test_malformed_file_raises(
        self, tmp_path: Path, text: str, message: str
    ) -> None:
        path = tmp_path / "uncertainty_config.json"
        path.write_text(text, encoding="utf-8")

        with pytest.raises(UncertaintyConfigError, match=message):
            load_uncertainty_config(path)

    def test_non_utf8_file_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "uncertainty_config.json"
        path.write_bytes(b'{"sigma_nat": 2.5, "note": "\xff"}')

        with pytest.raises(UncertaintyConfigError, match="cannot read"):
            load_uncertainty_config(path)

    def test_unreadable_path_raises(self, tmp_path: Path) -> None:
        # A directory exists but cannot be read as a file (IsADirectoryError).
        path = tmp_path / "uncertainty_config.json"
        path.mkdir()

        with pytest.raises(UncertaintyConfigError, match="cannot read"):
            load_uncertainty_config(path)


# ── Effective n ───────────────────────────────────────────────────────────────


class TestKindFactor:
    @pytest.mark.parametrize(
        ("basis", "factor"),
        [
            ("decided", 1.0),
            ("likely_voters", 0.85),
            ("all_adults", 0.7),
            ("sample_size", 0.7),
        ],
    )
    def test_factor_per_basis(self, basis: NBasis, factor: float) -> None:
        # Fractions differing from each other and from the defaults.
        config = UncertaintyConfig(decided_fraction=0.7, likely_voters_fraction=0.85)

        assert kind_factor(basis, config) == factor


class TestEffectiveN:
    def test_decided_base_is_only_divided_by_the_design_effect(self) -> None:
        # Opinium London: 204 decided respondents.
        resolved = ResolvedN(204, "base", "decided")

        assert effective_n(resolved, DEFAULTS) == EffectiveN(
            n=204,
            source="base:decided",
            basis="decided",
            kind_factor=1.0,
            adjusted_n=204.0,
            design_effect=1.5,
            n_eff=136.0,
            capped=False,
        )

    def test_all_adult_base_is_adjusted_before_the_design_effect(self) -> None:
        # YouGov London: 218 adults x 0.80 -> 174.4 decided -> n_eff 116.3.
        resolved = ResolvedN(218, "base", "all_adults", weighted_n=241)

        result = effective_n(resolved, DEFAULTS)

        assert result is not None
        assert result.kind_factor == 0.80
        assert result.adjusted_n == pytest.approx(174.4)
        assert result.n_eff == pytest.approx(116.2667, abs=1e-4)
        assert result.source == "base:all_adults"

    @pytest.mark.parametrize(
        ("resolved", "factor"),
        [
            (ResolvedN(1000, "base", "decided"), 1.0),
            (ResolvedN(1000, "base", "decided", weighted_only=True), 1.0),
            (ResolvedN(1000, "estimate", "decided"), 1.0),
            (ResolvedN(1000, "estimate", "decided", weighted_only=True), 1.0),
            (ResolvedN(1000, "base", "likely_voters"), 0.85),
            (ResolvedN(1000, "estimate", "likely_voters"), 0.85),
            (ResolvedN(1000, "base", "all_adults"), 0.7),
            (ResolvedN(1000, "estimate", "all_adults"), 0.7),
            (ResolvedN(1000, "sample_size", "sample_size"), 0.7),
            (ResolvedN(1000, "estimate", "sample_size"), 0.7),
        ],
    )
    def test_factor_applies_only_to_its_kind(
        self, resolved: ResolvedN, factor: float
    ) -> None:
        # Fractions differing from each other and from the defaults, so the
        # test is about which kind gets which factor, not the default values.
        config = UncertaintyConfig(
            design_effect=2.0, decided_fraction=0.7, likely_voters_fraction=0.85
        )

        result = effective_n(resolved, config)

        assert result is not None
        assert result.kind_factor == factor
        assert result.n_eff == pytest.approx(1000 * factor / 2.0)

    def test_cap_applies_after_the_adjustment(self) -> None:
        # The cap compares the adjusted n_eff: 4200 likely voters x 0.9 / 1.5
        # = 2520 stays under 2700, while 4200 decided / 1.5 = 2800 is capped.
        config = UncertaintyConfig(n_eff_cap=2700.0)
        under = effective_n(ResolvedN(4200, "base", "likely_voters"), config)
        over = effective_n(ResolvedN(4200, "base", "decided"), config)

        assert under is not None and over is not None
        assert under.n_eff == pytest.approx(2520.0)
        assert under.capped is False
        assert (over.n_eff, over.capped) == (2700.0, True)
        assert over.adjusted_n == 4200.0

    def test_cap_holds_for_a_merged_mrp_sample(self) -> None:
        # YouGov MRP releases fall back to sample_size of about 17,230.
        result = effective_n(ResolvedN(17_230, "sample_size", "sample_size"), DEFAULTS)

        assert result is not None
        assert (result.n_eff, result.capped) == (3000.0, True)

    def test_value_exactly_at_the_cap_is_not_flagged(self) -> None:
        result = effective_n(ResolvedN(4500, "base", "decided"), DEFAULTS)

        assert result is not None
        assert (result.n_eff, result.capped) == (3000.0, False)

    def test_unresolved_n_gives_none(self) -> None:
        assert effective_n(UNRESOLVED, DEFAULTS) is None


# ── Sampling error ────────────────────────────────────────────────────────────


class TestPollMoe:
    def test_analytic_value(self) -> None:
        # 1.645 x sqrt(0.5 x 0.5 / 1000) = 0.02601 -> 2.6 points.
        assert poll_moe(50.0, 1000.0, DEFAULTS) == pytest.approx(2.6007, abs=1e-4)

    def test_through_effective_n_with_a_design_effect(self) -> None:
        # 1500 decided / 1.5 = 1000; 30% -> 1.645 x sqrt(0.21 / 1000) = 2.4.
        result = effective_n(ResolvedN(1500, "base", "decided"), DEFAULTS)

        assert result is not None
        assert poll_moe(30.0, result.n_eff, DEFAULTS) == pytest.approx(2.384, abs=1e-3)

    @pytest.mark.parametrize(
        ("resolved", "share", "moe"),
        [
            # YouGov London: n_eff 116.3, Reform 11% -> 4.8.
            (ResolvedN(218, "base", "all_adults"), 11.0, 4.77),
            # Opinium London: n_eff 136, Reform 18% -> 5.4.
            (ResolvedN(204, "base", "decided"), 18.0, 5.42),
        ],
    )
    def test_london_crossbreaks(
        self, resolved: ResolvedN, share: float, moe: float
    ) -> None:
        result = effective_n(resolved, DEFAULTS)

        assert result is not None
        assert poll_moe(share, result.n_eff, DEFAULTS) == pytest.approx(moe, abs=0.01)

    def test_interval_sets_z(self) -> None:
        config = UncertaintyConfig(interval=0.95)

        assert poll_moe(50.0, 1000.0, config) == pytest.approx(3.099, abs=1e-3)

    @pytest.mark.parametrize("share", [0.0, 100.0])
    def test_share_at_the_bounds_has_no_margin(self, share: float) -> None:
        assert poll_moe(share, 500.0, DEFAULTS) == 0.0

    @pytest.mark.parametrize("share", [-0.1, 100.1, math.nan])
    def test_share_out_of_range_raises(self, share: float) -> None:
        with pytest.raises(UncertaintyError, match="share must be in"):
            poll_moe(share, 500.0, DEFAULTS)

    @pytest.mark.parametrize("n_eff", [0.0, -10.0, math.nan, math.inf])
    def test_non_positive_n_eff_raises(self, n_eff: float) -> None:
        with pytest.raises(UncertaintyError, match="n_eff must be positive"):
            poll_moe(30.0, n_eff, DEFAULTS)


class TestKishEffectiveCount:
    def test_equal_weights_count_fully(self) -> None:
        assert kish_effective_count([0.4, 0.4, 0.4, 0.4]) == pytest.approx(4.0)

    def test_unequal_weights_count_less(self) -> None:
        # (3 + 1)^2 / (9 + 1) = 1.6
        assert kish_effective_count([3.0, 1.0]) == pytest.approx(1.6)

    def test_zero_weights_are_ignored(self) -> None:
        assert kish_effective_count([2.0, 0.0, 2.0]) == pytest.approx(2.0)

    @pytest.mark.parametrize("weights", [[], [0.0, 0.0]])
    def test_no_weight_gives_none(self, weights: list[float]) -> None:
        assert kish_effective_count(weights) is None

    @pytest.mark.parametrize("weight", [-1.0, math.inf, math.nan])
    def test_invalid_weight_raises(self, weight: float) -> None:
        with pytest.raises(UncertaintyError, match="weight must be non-negative"):
            kish_effective_count([1.0, weight])


class TestWeightedAverageVariance:
    def test_unequal_weights_match_a_hand_calculation(self) -> None:
        # p = 0.25 -> p(1-p) = 0.1875.
        # Σ w²/n_eff = 4/500 + 1/250 + 0.25/1000 = 0.01225; (Σw)² = 3.5² = 12.25
        # V = 0.1875 x 0.01225 / 12.25 = 1.875e-4 -> 1.875 points².
        entries = [(2.0, 500.0), (1.0, 250.0), (0.5, 1000.0)]

        result = weighted_average_variance(entries, 25.0)

        assert result is not None
        assert result.variance == pytest.approx(1.875)
        assert result.sd == pytest.approx(math.sqrt(1.875))
        # Kish: 12.25 / (4 + 1 + 0.25) = 2.333.
        assert result.kish_count == pytest.approx(12.25 / 5.25)
        assert result.total_n_eff == 1750.0
        assert result.moe(DEFAULTS) == pytest.approx(1.645 * math.sqrt(1.875), 1e-3)

    def test_equal_polls_shrink_like_one_bigger_poll(self) -> None:
        # Four equal polls of n_eff 1000 = one of 4000: V = 0.21 / 4000.
        result = weighted_average_variance([(1.0, 1000.0)] * 4, 30.0)

        assert result is not None
        assert result.variance == pytest.approx(0.21 / 4000 * 100**2)
        assert result.kish_count == pytest.approx(4.0)

    def test_zero_weight_entry_contributes_nothing(self) -> None:
        with_zero = weighted_average_variance(
            [(1.0, 400.0), (0.0, 50.0), (3.0, 900.0)], 40.0
        )
        without = weighted_average_variance([(1.0, 400.0), (3.0, 900.0)], 40.0)

        assert with_zero == without

    @pytest.mark.parametrize("entries", [[], [(0.0, 500.0), (0.0, 800.0)]])
    def test_no_weight_gives_none(self, entries: list[tuple[float, float]]) -> None:
        assert weighted_average_variance(entries, 30.0) is None

    def test_invalid_inputs_raise(self) -> None:
        with pytest.raises(UncertaintyError, match="share must be in"):
            weighted_average_variance([(1.0, 500.0)], 101.0)
        with pytest.raises(UncertaintyError, match="weight must be non-negative"):
            weighted_average_variance([(-1.0, 500.0)], 30.0)
        with pytest.raises(UncertaintyError, match="n_eff must be positive"):
            weighted_average_variance([(0.0, 0.0)], 30.0)

    def test_sd_and_moe_of_a_result(self) -> None:
        result = AverageVariance(variance=0.4624, kish_count=22.2, total_n_eff=2500.0)

        assert result.sd == pytest.approx(0.68)
        assert result.moe(DEFAULTS) == pytest.approx(1.1186, abs=1e-4)


class TestShareScale:
    @pytest.mark.parametrize(
        ("share", "scale"), [(30.0, 1.0), (10.0, 0.6547), (3.0, 0.3723), (70.0, 1.0)]
    )
    def test_values(self, share: float, scale: float) -> None:
        assert share_scale(share, DEFAULTS) == pytest.approx(scale, abs=1e-4)

    def test_reference_share_follows_the_config(self) -> None:
        config = UncertaintyConfig(p_ref=0.10)

        assert share_scale(10.0, config) == pytest.approx(1.0)

    @pytest.mark.parametrize("share", [-1.0, 100.5])
    def test_share_out_of_range_raises(self, share: float) -> None:
        with pytest.raises(UncertaintyError, match="share must be in"):
            share_scale(share, DEFAULTS)
