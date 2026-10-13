"""Tests for poll bases: parsing helpers, apportioning, and the n resolver.

``polls.importers.bases`` turns printed column bases into planned rows;
``model_support.poll_bases`` resolves the n behind a poll's shares from them
(falling back to ``sample_size`` or a population estimate) and summarises
coverage. Importer-specific parsing is tested in each importer's test file and
commit persistence in ``test_westminster_importers_commit.py``.
"""

from __future__ import annotations

import math
from datetime import date

import pytest
from pydantic import ValidationError
from db import Database
from model_support.poll_bases import (
    UNRESOLVED,
    CapturedBase,
    NBasis,
    NOrigin,
    PollNIndex,
    PollNInputs,
    PollsterCoverage,
    ResolvedN,
    poll_base_coverage,
    resolve_poll_n,
)
from models import Poll, PollBase, PollRow, Region
from polls.importers.bases import (
    ParsedBase,
    PlannedPollBase,
    RegionRef,
    apportion,
    count_value,
    make_base,
    make_weighted_base,
    plan_poll_bases,
    region_refs,
    weighted_value,
)
from tests.uk_fixtures import WestminsterWorld, add_poll_with_rows

# ── Cell readers ──────────────────────────────────────────────────────────────


class TestCountValue:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1491, 1491),
            (1491.0, 1491),
            ("1,491", 1491),
            (" 2310 ", 2310),
            ("12,345,678", 12345678),
        ],
    )
    def test_counts(self, value: object, expected: int) -> None:
        result = count_value(value)
        assert result == expected
        assert type(result) is int

    @pytest.mark.parametrize(
        "value",
        [None, "", "-", 0, 0.0, -5, 1491.6, "1,49", "1491 adults", "1.491", True,
         math.nan, math.inf, [1491]],
    )
    def test_non_counts(self, value: object) -> None:
        assert count_value(value) is None


class TestWeightedValue:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (954.641, 955),
            (954.4, 954),
            ("1,029.181", 1029),
            (1508, 1508),
            ("1508", 1508),
        ],
    )
    def test_rounds_to_whole_number(self, value: object, expected: int) -> None:
        result = weighted_value(value)
        assert result == expected
        assert type(result) is int

    @pytest.mark.parametrize(
        "value",
        [None, "-", 0, 0.4, -3.0, math.nan, math.inf, False, "n/a", "1,0,2,9.181",
         "10,29.1", "1,029.", ".5"],
    )
    def test_non_bases(self, value: object) -> None:
        assert weighted_value(value) is None


class TestWeightedOnlyBase:
    def test_make_weighted_base_rounds_and_flags_weighted_only(self) -> None:
        base = make_weighted_base(("London",), 255.237, "decided")
        assert base == ParsedBase(regions=("London",), weighted_n=255, kind="decided")
        assert base.unweighted_n is None
        assert base.weighted_only is True

    @pytest.mark.parametrize("value", [None, "-", 0.3])
    def test_no_weighted_value_gives_none(self, value: object) -> None:
        assert make_weighted_base((), value, "decided") is None

    def test_a_base_needs_some_count(self) -> None:
        with pytest.raises(ValidationError, match="unweighted or a weighted count"):
            ParsedBase(kind="decided")
        with pytest.raises(ValidationError, match="unweighted or a weighted count"):
            PlannedPollBase(region_id=None, region_name="National", kind="decided")

    def test_unweighted_base_is_not_weighted_only(self) -> None:
        assert ParsedBase(unweighted_n=10, kind="decided").weighted_only is False

    def test_weighted_only_macro_base_splits_the_weighted_count(self) -> None:
        planned = plan_poll_bases(
            [ParsedBase(regions=_NORTH, weighted_n=502, kind="decided")], _REGIONS
        )
        assert [(p.region_id, p.unweighted_n, p.weighted_n) for p in planned] == [
            (21, None, 85),
            (15, None, 243),
            (18, None, 174),
        ]


class TestMakeBase:
    def test_builds_from_raw_cells(self) -> None:
        assert make_base(("Wales",), 54.0, 36.4, "decided") == ParsedBase(
            regions=("Wales",), unweighted_n=54, weighted_n=36, kind="decided"
        )

    def test_missing_unweighted_count_gives_none(self) -> None:
        assert make_base((), "-", 1500, "decided") is None

    def test_missing_weighted_base_is_kept_as_none(self) -> None:
        base = make_base((), 1500, None, "all_adults")
        assert base is not None
        assert base.weighted_n is None


# ── Apportioning ──────────────────────────────────────────────────────────────


class TestApportion:
    def test_largest_remainders_take_the_leftover(self) -> None:
        # 333 over 6.3/9.6/5.6: quotas 97.58, 148.69, 86.73.
        assert apportion(333, [63, 96, 56]) == [97, 149, 87]

    def test_parts_always_sum_to_the_total(self) -> None:
        for total in range(0, 60):
            assert sum(apportion(total, [27, 77, 55])) == total

    def test_equal_remainders_go_to_the_earlier_weight(self) -> None:
        assert apportion(1, [1, 1]) == [1, 0]
        assert apportion(2, [1, 1, 1]) == [1, 1, 0]

    def test_exact_ratios_are_not_disturbed_by_float_error(self) -> None:
        # 10 x 1/3 is exactly 3.333...; a float tie-break could misorder.
        assert apportion(10, [1, 1, 1]) == [4, 3, 3]

    @pytest.mark.parametrize(
        ("total", "weights"), [(5, []), (5, [1, 0]), (5, [2, -1]), (-1, [1])]
    )
    def test_invalid_input_raises(self, total: int, weights: list[int]) -> None:
        with pytest.raises(ValueError):
            apportion(total, weights)


# ── plan_poll_bases ───────────────────────────────────────────────────────────

_REGIONS = {
    "London": RegionRef(17, "London", 9_100_000),
    "North East England": RegionRef(21, "North East England", 2_700_000),
    "North West England": RegionRef(15, "North West England", 7_700_000),
    "Yorkshire and The Humber": RegionRef(18, "Yorkshire and The Humber", 5_500_000),
    "Wales": RegionRef(23, "Wales", None),
    "Scotland": RegionRef(22, "Scotland", None),
}
_NORTH = ("North East England", "North West England", "Yorkshire and The Humber")


def _base(regions: tuple[str, ...], n: int, weighted: int | None = None) -> ParsedBase:
    return ParsedBase(
        regions=regions, unweighted_n=n, weighted_n=weighted, kind="all_adults"
    )


class TestPlanPollBases:
    def test_national_first_then_direct_and_split_regions(self) -> None:
        planned = plan_poll_bases(
            [
                _base(("London",), 241, 280),
                _base((), 2310, 2310),
                _base(_NORTH, 502, 547),
            ],
            _REGIONS,
        )

        rows = [
            (p.region_id, p.unweighted_n, p.weighted_n, p.split_from_macro)
            for p in planned
        ]
        assert rows == [
            (None, 2310, 2310, False),
            (17, 241, 280, False),
            # 502 over 2.7m/7.7m/5.5m: 85.25/243.12/173.62; 547: 92.89/264.9/189.21.
            (21, 85, 93, True),
            (15, 243, 265, True),
            (18, 174, 189, True),
        ]
        assert planned[0].region_name == "National"
        assert {p.kind for p in planned} == {"all_adults"}

    def test_split_without_weighted_base_keeps_none(self) -> None:
        planned = plan_poll_bases([_base(_NORTH, 502)], _REGIONS)
        assert [p.weighted_n for p in planned] == [None, None, None]

    def test_region_whose_share_rounds_to_zero_gets_no_row(self) -> None:
        planned = plan_poll_bases([_base(_NORTH, 1)], _REGIONS)
        assert [(p.region_id, p.unweighted_n) for p in planned] == [(15, 1)]

    def test_macro_with_an_unknown_population_is_skipped(self) -> None:
        planned = plan_poll_bases(
            [_base(("Wales", "Scotland"), 300), _base(("London",), 40)], _REGIONS
        )
        assert [(p.region_id, p.unweighted_n) for p in planned] == [(17, 40)]

    def test_direct_region_needs_no_population(self) -> None:
        planned = plan_poll_bases([_base(("Wales",), 40)], _REGIONS)
        assert [(p.region_id, p.unweighted_n) for p in planned] == [(23, 40)]

    def test_duplicate_columns_raise(self) -> None:
        with pytest.raises(ValueError, match="same column"):
            plan_poll_bases(
                [_base(_NORTH, 502), _base(("North West England",), 200)], _REGIONS
            )
        with pytest.raises(ValueError, match="same column"):
            plan_poll_bases([_base((), 2000), _base((), 1500)], _REGIONS)

    def test_region_missing_from_the_map_raises(self) -> None:
        with pytest.raises(ValueError, match="not on the map"):
            plan_poll_bases([_base(("Northern Ireland",), 13)], _REGIONS)

    def test_region_refs_accepts_orm_regions(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        refs = region_refs(db.get_regions_for_map(westminster_world.map_id))
        london_id = westminster_world.region_ids["London"]
        assert refs["London"] == RegionRef(london_id, "London", 9_100_000)


# ── resolve_poll_n ────────────────────────────────────────────────────────────

_POPULATIONS: dict[int, int | None] = {
    17: 9_100_000,
    22: 5_500_000,
    23: 3_100_000,
    24: None,
}


class TestResolvePollN:
    def test_captured_national_base_beats_sample_size(self) -> None:
        inputs = PollNInputs(2113, {None: CapturedBase(1491, 1402, "decided")})
        assert resolve_poll_n(inputs, None, _POPULATIONS) == ResolvedN(
            1491, "base", "decided", weighted_n=1402
        )

    def test_national_falls_back_to_sample_size(self) -> None:
        inputs = PollNInputs(2113, {17: CapturedBase(239, None, "decided")})
        resolved = resolve_poll_n(inputs, None, _POPULATIONS)
        assert resolved == ResolvedN(2113, "sample_size", "sample_size")

    @pytest.mark.parametrize("sample_size", [None, 0])
    def test_national_without_base_or_sample_is_unresolved(
        self, sample_size: int | None
    ) -> None:
        inputs = PollNInputs(sample_size)
        assert resolve_poll_n(inputs, None, _POPULATIONS) is UNRESOLVED

    def test_captured_regional_base_carries_its_kind_and_split_flag(self) -> None:
        inputs = PollNInputs(
            2310,
            {22: CapturedBase(187, 201, "all_adults", split_from_macro=True)},
            frozenset({22}),
        )
        assert resolve_poll_n(inputs, 22, _POPULATIONS) == ResolvedN(
            187, "base", "all_adults", weighted_n=201, split_from_macro=True
        )

    def test_regional_estimate_uses_sample_size_without_a_national_base(self) -> None:
        inputs = PollNInputs(2000, reported_region_ids=frozenset({17, 22, 23}))
        # 2000 x 9.1 / (9.1 + 5.5 + 3.1) = 1028.2
        london = resolve_poll_n(inputs, 17, _POPULATIONS)
        wales = resolve_poll_n(inputs, 23, _POPULATIONS)
        assert london == ResolvedN(1028, "estimate", "sample_size")
        assert wales == ResolvedN(350, "estimate", "sample_size")

    def test_regional_estimate_prefers_the_national_base(self) -> None:
        inputs = PollNInputs(
            2000, {None: CapturedBase(1500, None, "decided")}, frozenset({17, 22, 23})
        )
        # 1500 x 9.1 / 17.7 = 771.2
        resolved = resolve_poll_n(inputs, 17, _POPULATIONS)
        assert resolved == ResolvedN(771, "estimate", "decided")

    def test_estimate_denominator_is_only_the_reported_regions(self) -> None:
        inputs = PollNInputs(2000, reported_region_ids=frozenset({17, 22}))
        # 2000 x 9.1 / 14.6 = 1246.6
        resolved = resolve_poll_n(inputs, 17, _POPULATIONS)
        assert resolved == ResolvedN(1247, "estimate", "sample_size")

    def test_unreported_region_is_unresolved(self) -> None:
        inputs = PollNInputs(2000, reported_region_ids=frozenset({17, 22}))
        assert resolve_poll_n(inputs, 23, _POPULATIONS) is UNRESOLVED

    def test_unknown_population_leaves_the_estimate_unresolved(self) -> None:
        inputs = PollNInputs(2000, reported_region_ids=frozenset({17, 24}))
        assert resolve_poll_n(inputs, 24, _POPULATIONS) is UNRESOLVED
        assert resolve_poll_n(inputs, 17, _POPULATIONS) is UNRESOLVED

    def test_estimate_basis_names_the_national_base_kind(self) -> None:
        inputs = PollNInputs(
            2310,
            {None: CapturedBase(2310, 2310, "all_adults")},
            frozenset({17, 22, 23}),
        )
        assert resolve_poll_n(inputs, 23, _POPULATIONS) == ResolvedN(
            405, "estimate", "all_adults"
        )

    def test_weighted_only_base_uses_its_weighted_count(self) -> None:
        inputs = PollNInputs(
            2607,
            {None: CapturedBase(None, 1452, "decided")},
            frozenset({17, 22, 23}),
        )
        assert resolve_poll_n(inputs, None, _POPULATIONS) == ResolvedN(
            1452, "base", "decided", weighted_only=True, weighted_n=1452
        )
        # 1452 x 3.1m / 17.7m = 254.3
        assert resolve_poll_n(inputs, 23, _POPULATIONS) == ResolvedN(
            254, "estimate", "decided", weighted_only=True
        )

    def test_estimate_rounding_below_one_is_unresolved(self) -> None:
        populations: dict[int, int | None] = {17: 9_100_000, 30: 10_000}
        inputs = PollNInputs(40, reported_region_ids=frozenset({17, 30}))
        # 40 x 10k / 9.11m = 0.04, which must not become n=0.
        assert resolve_poll_n(inputs, 30, populations) is UNRESOLVED
        # 40 x 9.1m / 9.11m = 39.96 still resolves.
        assert resolve_poll_n(inputs, 17, populations) == ResolvedN(
            40, "estimate", "sample_size"
        )

    def test_estimate_without_any_poll_n_is_unresolved(self) -> None:
        inputs = PollNInputs(None, reported_region_ids=frozenset({17}))
        assert resolve_poll_n(inputs, 17, _POPULATIONS) is UNRESOLVED


class TestResolvedNSource:
    """The console label rendered from the structured origin and basis."""

    @pytest.mark.parametrize(
        ("resolved", "label"),
        [
            (ResolvedN(1491, "base", "decided"), "base:decided"),
            (ResolvedN(241, "base", "all_adults"), "base:all_adults"),
            (ResolvedN(300, "base", "likely_voters"), "base:likely_voters"),
            (
                ResolvedN(1452, "base", "decided", weighted_only=True),
                "base:decided(weighted)",
            ),
            (ResolvedN(2113, "sample_size", "sample_size"), "sample_size"),
            (ResolvedN(771, "estimate", "decided"), "estimate:decided"),
            (
                ResolvedN(254, "estimate", "decided", weighted_only=True),
                "estimate:decided(weighted)",
            ),
            (ResolvedN(1028, "estimate", "sample_size"), "estimate:sample_size"),
        ],
    )
    def test_label(self, resolved: ResolvedN, label: str) -> None:
        assert resolved.source == label

    def test_unresolved_has_no_label(self) -> None:
        assert UNRESOLVED.source is None


class TestResolvedNInvariants:
    @pytest.mark.parametrize(
        ("n", "origin", "basis"),
        [
            (None, "base", "decided"),
            (500, None, "decided"),
            (500, "base", None),
            (500, None, None),
            (None, None, "decided"),
        ],
    )
    def test_n_origin_and_basis_are_all_or_nothing(
        self, n: int | None, origin: NOrigin | None, basis: NBasis | None
    ) -> None:
        with pytest.raises(ValueError, match="all set or all None"):
            ResolvedN(n, origin, basis)

    @pytest.mark.parametrize(
        ("origin", "basis"),
        [
            ("sample_size", "decided"),
            ("sample_size", "all_adults"),
            ("base", "sample_size"),
        ],
    )
    def test_sample_size_basis_matches_its_origin(
        self, origin: NOrigin, basis: NBasis
    ) -> None:
        with pytest.raises(ValueError, match="cannot have basis"):
            ResolvedN(500, origin, basis)

    def test_estimate_from_sample_size_is_allowed(self) -> None:
        assert ResolvedN(500, "estimate", "sample_size").n == 500


# ── PollNIndex ────────────────────────────────────────────────────────────────


class TestPollNIndex:
    def test_from_records_ignores_zero_placeholder_regions(self) -> None:
        poll = Poll(id=5, sample_size=2000)
        rows = [
            PollRow(poll_id=5, region_id=17, party_id=1, percentage=30.0),
            PollRow(poll_id=5, region_id=17, party_id=2, percentage=0.0),
            PollRow(poll_id=5, region_id=22, party_id=1, percentage=20.0),
            PollRow(poll_id=5, region_id=24, party_id=1, percentage=0.0),
            PollRow(poll_id=5, region_id=None, party_id=1, percentage=29.0),
            PollRow(poll_id=6, region_id=23, party_id=1, percentage=25.0),
        ]
        regions = [Region(id=rid, population=pop) for rid, pop in _POPULATIONS.items()]

        index = PollNIndex.from_records([poll], rows, [], regions)

        assert index.inputs[5].reported_region_ids == frozenset({17, 22})
        assert 6 not in index.inputs
        # 2000 x 9.1 / (9.1 + 5.5); Northern Ireland's zero rows don't count.
        assert index.resolve(5, 17) == ResolvedN(1247, "estimate", "sample_size")
        assert index.resolve(5, 24) is UNRESOLVED
        assert index.resolve(6, 23) is UNRESOLVED

    def test_load_resolves_each_poll_from_its_own_bases(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        london, wales = world.region_ids["London"], world.region_ids["Wales"]
        first = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="more_in_common",
            fieldwork_start=date(2026, 9, 18), fieldwork_end=date(2026, 9, 21),
            national={labour: 26.0},
            regional={london: {labour: 35.0}, wales: {labour: 33.0}},
            sample_size=2113,
        )
        second = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="find_out_now",
            fieldwork_start=date(2026, 9, 29), fieldwork_end=date(2026, 9, 30),
            national={labour: 27.0},
            regional={london: {labour: 34.0}, wales: {labour: 24.0}},
            sample_size=2607,
        )
        assert first.id != second.id
        with db.session() as session:
            for region_id, n in ((None, 1491), (london, 239)):
                session.add(
                    PollBase(
                        poll_id=first.id, region_id=region_id, unweighted_n=n,
                        kind="decided",
                    )
                )

        with db.session() as session:
            index = PollNIndex.load(session, [first.id, second.id, 9999])

        assert index.resolve(first.id, None) == ResolvedN(1491, "base", "decided")
        assert index.resolve(first.id, london) == ResolvedN(239, "base", "decided")
        # 1491 x 3.1 / (9.1 + 3.1) = 378.9
        assert index.resolve(first.id, wales) == ResolvedN(379, "estimate", "decided")
        assert index.resolve(second.id, None) == ResolvedN(
            2607, "sample_size", "sample_size"
        )
        # 2607 x 9.1 / 12.2 = 1944.6
        resolved = index.resolve(second.id, london)
        assert resolved == ResolvedN(1945, "estimate", "sample_size")
        assert index.resolve(9999, None) is UNRESOLVED

    def test_load_with_no_polls_is_empty(self, db: Database) -> None:
        with db.session() as session:
            assert PollNIndex.load(session, []) == PollNIndex({}, {})


# ── Coverage ──────────────────────────────────────────────────────────────────


class TestPollBaseCoverage:
    def test_counts_polls_and_reported_crossbreaks_per_pollster(
        self, db: Database, westminster_world: WestminsterWorld
    ) -> None:
        world = westminster_world
        labour = world.party_ids["Labour"]
        london, wales, ni = (
            world.region_ids[name] for name in ("London", "Wales", "Northern Ireland")
        )
        based = add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="yougov",
            fieldwork_end=date(2026, 9, 2), national={labour: 30.0},
            regional={london: {labour: 35.0}, wales: {labour: 30.0}, ni: {labour: 0.0}},
        )
        add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="yougov",
            fieldwork_end=date(2026, 9, 9), national={labour: 30.0},
            regional={london: {labour: 36.0}},
        )
        add_poll_with_rows(
            db, map_id=world.map_id, pollster_identifier="techne",
            fieldwork_end=date(2026, 9, 4), national={labour: 30.0},
        )
        other_map = db.add_map("Other Map", parliament="westminster")
        add_poll_with_rows(
            db, map_id=other_map.id, pollster_identifier="yougov",
            fieldwork_start=date(2026, 9, 5), fieldwork_end=date(2026, 9, 6),
            national={labour: 1.0},
        )
        with db.session() as session:
            # A base for a placeholder region (ni) is not a reported crossbreak.
            for region_id, n in ((None, 2310), (london, 241), (ni, 13)):
                session.add(
                    PollBase(
                        poll_id=based.id, region_id=region_id, unweighted_n=n,
                        kind="all_adults",
                    )
                )

        with db.session() as session:
            coverage = poll_base_coverage(session, world.map_id)

        assert coverage == [
            PollsterCoverage("techne", "techne", 1, 0, 0, 0),
            PollsterCoverage("yougov", "yougov", 2, 1, 3, 1),
        ]
        assert coverage[1].national_pct == 50.0
        assert coverage[1].crossbreak_pct == pytest.approx(100 / 3)
        assert coverage[0].crossbreak_pct is None
