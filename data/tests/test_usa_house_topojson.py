"""Tests for the US House district map builder, its source table and the redistricting check."""

from __future__ import annotations

import importlib
import io
import json
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

MAPS_DIR = Path(__file__).resolve().parents[2] / "uselectionmaps" / "data" / "maps"
GEOMETRY = "importers.results.us.geometry"

sources = importlib.import_module(f"{GEOMETRY}.house_2026_sources")
builder = importlib.import_module(f"{GEOMETRY}.build_house_topojson")
check = importlib.import_module(f"{GEOMETRY}.check_redistricting")

# Voting seats per state after the 2010 and 2020 censuses.
SEATS_2010 = {
    "AL": 7, "AK": 1, "AZ": 9, "AR": 4, "CA": 53, "CO": 7, "CT": 5, "DE": 1, "FL": 27,
    "GA": 14, "HI": 2, "ID": 2, "IL": 18, "IN": 9, "IA": 4, "KS": 4, "KY": 6, "LA": 6,
    "ME": 2, "MD": 8, "MA": 9, "MI": 14, "MN": 8, "MS": 4, "MO": 8, "MT": 1, "NE": 3,
    "NV": 4, "NH": 2, "NJ": 12, "NM": 3, "NY": 27, "NC": 13, "ND": 1, "OH": 16, "OK": 5,
    "OR": 5, "PA": 18, "RI": 2, "SC": 7, "SD": 1, "TN": 9, "TX": 36, "UT": 4, "VT": 1,
    "VA": 11, "WA": 10, "WV": 3, "WI": 8, "WY": 1,
}  # fmt: skip
SEATS_2020 = {
    **SEATS_2010, "CA": 52, "CO": 8, "FL": 28, "IL": 17, "MI": 13, "MT": 2, "NY": 26,
    "NC": 14, "OH": 15, "OR": 6, "PA": 17, "TX": 38, "WV": 2,
}  # fmt: skip


def square(x: float, y: float, w: float = 1.0, h: float = 1.0) -> dict[str, Any]:
    """A rectangular Polygon geometry with its lower-left corner at (x, y)."""
    return {
        "type": "Polygon",
        "coordinates": [[[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]]],
    }


def feature(name: str, geometry: dict[str, Any]) -> dict[str, Any]:
    """A cleaned district feature, as ``voting_districts`` emits."""
    region = builder.ST_NAME[name[:2]].lower()
    return {
        "type": "Feature",
        "properties": {"name": name, "region": region},
        "geometry": geometry,
    }


class TestDistrictNames:
    """Naming and filtering of the raw Census rows."""

    def test_district_code_pads_and_maps_at_large(self) -> None:
        assert builder.district_code("CA", "05") == "CA-05"
        assert builder.district_code("AK", "00") == "AK-01"

    def test_voting_districts_drops_delegates_and_water(self) -> None:
        raw = {
            "features": [
                {
                    "properties": {"STATEFP": "06", "CD120FP": "12"},
                    "geometry": square(0, 0),
                },
                {
                    "properties": {"STATEFP": "11", "CD120FP": "98"},
                    "geometry": square(1, 0),
                },
                {
                    "properties": {"STATEFP": "06", "CD120FP": "ZZ"},
                    "geometry": square(2, 0),
                },
                {
                    "properties": {"STATEFP": "72", "CD120FP": "98"},
                    "geometry": square(3, 0),
                },
            ]
        }
        got = builder.voting_districts(raw, "CD120FP")
        assert [f["properties"] for f in got] == [
            {"name": "CA-12", "region": "california"}
        ]


class TestStitchStates:
    """Replacing a state's districts while keeping its outline."""

    def base(self) -> list[dict[str, Any]]:
        # Utah as a 2x1 block split into two districts; Ohio untouched to its right.
        return [
            feature("UT-01", square(0, 0)),
            feature("UT-02", square(1, 0)),
            feature("OH-01", square(2, 0)),
        ]

    def test_replacement_is_clipped_to_the_base_outline(self) -> None:
        # The new plan splits the block top/bottom and spills a unit past the state border.
        repl = [
            feature("UT-01", square(-0.5, 0, 3.5, 0.5)),
            feature("UT-02", square(-0.5, 0.5, 3.5, 0.5)),
        ]
        got = builder.stitch_states(self.base(), {"UT": repl})
        assert [f["properties"]["name"] for f in got] == ["OH-01", "UT-01", "UT-02"]
        by_name = {f["properties"]["name"]: f for f in got}
        assert by_name["OH-01"]["geometry"] == square(
            2, 0
        )  # untouched state kept as is
        ut1 = builder.shape(by_name["UT-01"]["geometry"])
        assert ut1.bounds == (0.0, 0.0, 2.0, 0.5)
        assert ut1.area == pytest.approx(1.0)

    def test_small_gap_goes_to_the_neighbouring_district(self) -> None:
        # The new plan leaves a thin strip at the top of the outline uncovered.
        repl = [
            feature("UT-01", square(0, 0, 2, 0.5)),
            feature("UT-02", square(0, 0.5, 2, 0.495)),
        ]
        got = builder.stitch_states(self.base(), {"UT": repl})
        areas = {
            f["properties"]["name"]: builder.shape(f["geometry"]).area
            for f in got
            if f["properties"]["name"][:2] == "UT"
        }
        # The strip runs along UT-02's whole top edge and UT-01's is shorter, so UT-02 gets it.
        assert sum(areas.values()) == pytest.approx(2.0)
        assert areas["UT-01"] == pytest.approx(1.0)
        assert areas["UT-02"] == pytest.approx(1.0)

    def test_large_gap_is_an_error(self) -> None:
        repl = [feature("UT-01", square(0, 0, 2, 0.5))]
        with pytest.raises(ValueError, match="uncovered"):
            builder.stitch_states(self.base(), {"UT": repl})

    def test_state_missing_from_base_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="not present"):
            builder.stitch_states(self.base(), {"TX": [feature("TX-01", square(5, 5))]})

    def test_districts_split_over_several_parts_are_merged(self) -> None:
        repl = [
            feature("UT-01", square(0, 0, 1, 1)),
            feature("UT-01", square(1, 0, 1, 1)),
        ]
        got = builder.stitch_states(self.base(), {"UT": repl})
        ut = [f for f in got if f["properties"]["name"].startswith("UT")]
        assert [f["properties"]["name"] for f in ut] == ["UT-01"]


class TestGapFallback:
    def test_piece_touching_no_candidate_goes_to_the_nearest_district(self) -> None:
        # An L-shaped district A whose bounding box covers an outline-only island; district C
        # lies outside that box. The island touches neither, so it must go to the nearer one.
        a = builder.shape(
            {
                "type": "Polygon",
                "coordinates": [
                    [[0, 0], [4, 0], [4, 1], [1, 1], [1, 4], [0, 4], [0, 0]]
                ],
            }
        )
        island = builder.shape(square(2, 2, 0.1, 0.1))
        c = builder.shape(square(5, 2, 1, 1))
        outline = builder.unary_union([a, island, c])
        got = builder._fill_gaps({"A": a, "C": c}, outline, "XX")
        assert got["A"].contains(island.representative_point())
        assert not got["C"].contains(island.representative_point())
        assert got["A"].area + got["C"].area == pytest.approx(outline.area)


class TestCheckDistricts:
    def test_duplicate_names_rejected(self) -> None:
        feats = [feature("CA-01", square(0, 0)), feature("CA-01", square(1, 0))]
        with pytest.raises(ValueError, match="duplicate"):
            builder.check_districts(feats, expected=2)

    def test_wrong_count_rejected(self) -> None:
        with pytest.raises(ValueError, match="expected 3"):
            builder.check_districts([feature("CA-01", square(0, 0))], expected=3)


class TestSourceTable:
    """The per-state source table behind map 24."""

    def test_nine_redrawn_states_with_census_urls(self) -> None:
        assert set(sources.REDRAWN_2026) == {
            "AL",
            "CA",
            "FL",
            "LA",
            "NC",
            "OH",
            "TN",
            "TX",
            "UT",
        }
        for state, src in sources.REDRAWN_2026.items():
            assert builder.FIPS_TO_ST[src.fips] == state
            assert src.name == builder.ST_NAME[state]
            assert src.url == (
                f"https://www2.census.gov/geo/tiger/TIGER2026/CD/tl_2026_{src.fips}_cd120.zip"
            )
            assert sources.check_url(src.url) == src.url
            assert src.filename == f"tl_2026_{src.fips}_cd120"

    def test_missouri_is_not_redrawn_for_2026(self) -> None:
        assert "MO" not in sources.REDRAWN_2026

    @pytest.mark.parametrize(
        "url",
        [
            "http://www2.census.gov/x.zip",
            "https://example.com/x.zip",
            "file:///etc/passwd",
        ],
    )
    def test_check_url_refuses_other_hosts_and_schemes(self, url: str) -> None:
        with pytest.raises(ValueError, match="refusing"):
            sources.check_url(url)

    def test_recipes_per_map(self) -> None:
        assert set(builder.RECIPES) == {21, 24, 25}
        assert not builder.RECIPES[21].replace
        assert builder.RECIPES[24].replace is sources.REDRAWN_2026
        assert builder.RECIPES[24].base_field == "CD119FP"
        assert builder.RECIPES[21].simplify == "12%"
        assert builder.RECIPES[24].simplify == "6%"
        assert builder.RECIPES[25].base_field == "CD116FP"
        assert "cd116" in builder.RECIPES[25].base_url


@pytest.mark.skipif(shutil.which("mapshaper") is None, reason="mapshaper not installed")
def test_build_topojson_writes_a_map_object(tmp_path: Path) -> None:
    out = tmp_path / "m.topo.json"
    builder.build_topojson(
        [feature("UT-01", square(0, 0)), feature("UT-02", square(1, 0))], out, "100%"
    )
    topo = json.loads(out.read_text(encoding="utf-8"))
    names = [g["properties"]["name"] for g in topo["objects"]["map"]["geometries"]]
    assert names == ["UT-01", "UT-02"]


def _topo_names(map_id: int) -> list[str]:
    topo = json.loads(
        (MAPS_DIR / f"map-{map_id}.topo.json").read_text(encoding="utf-8")
    )
    return [g["properties"]["name"] for g in topo["objects"]["map"]["geometries"]]


class TestCommittedMaps:
    """Invariants of the committed House maps."""

    @pytest.mark.parametrize(
        ("map_id", "seats"), [(21, SEATS_2020), (24, SEATS_2020), (25, SEATS_2010)]
    )
    def test_435_unique_districts_matching_the_apportionment(
        self, map_id: int, seats: dict[str, int]
    ) -> None:
        names = _topo_names(map_id)
        assert len(names) == 435
        assert len(set(names)) == 435
        counts = Counter(n[:2] for n in names)
        assert dict(counts) == seats
        for state, n in seats.items():
            assert sorted(x for x in names if x.startswith(state + "-")) == [
                f"{state}-{i:02d}" for i in range(1, n + 1)
            ]

    def test_regions_are_lowercase_state_names(self) -> None:
        for map_id in (21, 24, 25):
            topo = json.loads(
                (MAPS_DIR / f"map-{map_id}.topo.json").read_text(encoding="utf-8")
            )
            for g in topo["objects"]["map"]["geometries"]:
                props = g["properties"]
                assert props["region"] == builder.ST_NAME[props["name"][:2]].lower()


NYT_FIXTURE = {
    "races": [
        {
            "office_id": "H",
            "seat_number": "1",
            "reporting_units": [
                {"level": "state", "state_postal": "AA"},
                {
                    "level": "county",
                    "fips_state": "01",
                    "fips_county": "001",
                    "total_votes": 10,
                },
                {
                    "level": "county",
                    "fips_state": "01",
                    "fips_county": "002",
                    "total_votes": 10,
                },
                {
                    "level": "county",
                    "fips_state": "01",
                    "fips_county": "003",
                    "total_votes": 0,
                },
            ],
        },
        {
            "office_id": "H",
            "seat_number": "2",
            "reporting_units": [
                {"level": "state", "state_postal": "AA"},
                {
                    "level": "county",
                    "fips_state": "01",
                    "fips_county": "002",
                    "total_votes": 5,
                },
            ],
        },
        {
            "office_id": "H",
            "seat_number": None,  # at-large
            "reporting_units": [
                {"level": "state", "state_postal": "BB"},
                {
                    "level": "county",
                    "fips_state": "02",
                    "fips_county": "001",
                    "total_votes": 1,
                },
            ],
        },
        {"office_id": "S", "seat_number": "1", "reporting_units": []},
        {"office_id": "H", "seat_number": "3", "reporting_units": []},
    ]
}


class TestRedistrictingCheck:
    def test_nyt_county_districts(self) -> None:
        got = check.nyt_county_districts(NYT_FIXTURE)
        assert got == {
            "01001": {"AA-01"},
            "01002": {"AA-01", "AA-02"},
            "02001": {"BB-01"},
        }

    def test_specials_are_skipped(self) -> None:
        snap = {
            "races": [
                {
                    "office_id": "H",
                    "seat_number": "1",
                    "type": "Special",
                    "reporting_units": [
                        {"level": "state", "state_postal": "AA"},
                        {
                            "level": "county",
                            "fips_state": "01",
                            "fips_county": "001",
                            "total_votes": 5,
                        },
                    ],
                }
            ]
        }
        assert check.nyt_county_districts(snap) == {}

    def test_state_labels(self) -> None:
        got = check.state_labels(check.nyt_county_districts(NYT_FIXTURE))
        assert got == {"01": "AA", "02": "BB"}

    def test_geometry_county_districts_applies_the_threshold(self) -> None:
        counties = [
            {"properties": {"GEOID": "01001"}, "geometry": square(0, 0, 1, 1)},
            {
                "properties": {"STATEFP": "01", "COUNTYFP": "002"},
                "geometry": square(1, 0, 1, 1),
            },
        ]
        districts = [
            {"properties": {"name": "AA-01"}, "geometry": square(0, 0, 1.5, 1)},
            # AA-02 covers 1% of county 01002: below the 2% threshold.
            {"properties": {"name": "AA-02"}, "geometry": square(1.5, 0, 0.5, 0.02)},
            {"properties": {"name": "AA-03"}, "geometry": square(1.5, 0.02, 0.5, 0.98)},
        ]
        got = check.geometry_county_districts(counties, districts, 0.02)
        assert got == {"01001": {"AA-01"}, "01002": {"AA-01", "AA-03"}}

    def test_state_scores_share_of_mismatched_counties(self) -> None:
        nyt = {f"01{i:03d}": {"AA-01"} for i in range(4)} | {"02001": {"BB-01"}}
        geometry = {f"01{i:03d}": {"AA-01"} for i in range(3)} | {"01003": {"AA-02"}}
        geometry["02001"] = {"BB-01"}
        scores = check.state_scores(nyt, geometry)
        assert scores["01"] == (0.25, 4)
        assert scores["02"] == (None, 1)  # too few counties to say

    def test_counties_missing_from_the_county_file_are_skipped(self) -> None:
        nyt = {f"01{i:03d}": {"AA-01"} for i in range(4)}
        scores = check.state_scores(nyt, {"01000": {"AA-01"}, "01001": {"AA-01"}})
        assert scores == {"01": (None, 2)}

    def test_redrawn_means_the_score_falls_against_the_new_map(self) -> None:
        nyt = {
            f"{st}{i:03d}": {f"{st}-x"} for st in ("01", "02", "03") for i in range(10)
        }
        # State 01 moved (all counties wrong on the old map, right on the new one); state 02
        # is noisy on both maps; state 03 is right on both.
        old = {k: {"other"} if k[:2] == "01" else v for k, v in nyt.items()}
        noisy = {k: {"other"} for k in list(nyt) if k.startswith("02")}
        old.update(noisy)
        new = {k: v for k, v in nyt.items() if k[:2] != "02"} | noisy
        old_scores, new_scores = (
            check.state_scores(nyt, old),
            check.state_scores(nyt, new),
        )
        assert check.redrawn_states(old_scores, new_scores) == {"01"}

    def test_a_drop_of_exactly_the_threshold_counts(self) -> None:
        assert check.redrawn_states({"01": (0.15, 20)}, {"01": (0.10, 20)}) == {"01"}

    def test_redrawn_ignores_states_without_a_score(self) -> None:
        assert check.redrawn_states({"01": (None, 1)}, {"01": (0.0, 1)}) == set()
        assert check.redrawn_states({"01": (0.5, 5)}, {}) == set()


# State FIPS prefix -> postal code for the main() tests below. AA's districts were redrawn,
# BB is noisy on both maps, CC matches on both, DD has too few counties to score.
_MAIN_STATES = {"01": "AA", "02": "BB", "03": "CC", "04": "DD"}


def _main_nyt() -> dict[str, Any]:
    """A NYT snapshot with 4 counties for AA, BB and CC, and 2 for DD; all report district 01."""
    races = []
    for fips, postal in _MAIN_STATES.items():
        units: list[dict[str, Any]] = [{"level": "state", "state_postal": postal}]
        for index in range(2 if postal == "DD" else 4):
            units.append({
                "level": "county", "fips_state": fips,
                "fips_county": f"{index + 1:03d}", "total_votes": 10,
            })  # fmt: skip
        races.append({"office_id": "H", "seat_number": "1", "reporting_units": units})
    return {"races": races}


def _main_counties() -> list[dict[str, Any]]:
    return [
        {
            "properties": {"GEOID": f"{fips}{index + 1:03d}"},
            "geometry": square(int(fips) * 10 + index, 0),
        }
        for fips in _MAIN_STATES
        for index in range(2 if fips == "04" else 4)
    ]


def _main_districts(*, redrawn: bool) -> list[dict[str, Any]]:
    """The old map (``redrawn=False``) puts AA in a district NYT never reported."""

    def district(name: str, state: str, first: int, width: int) -> dict[str, Any]:
        return {
            "properties": {"name": name},
            "geometry": square(int(state) * 10 + first, 0, width, 1),
        }

    return [
        district("AA-01" if redrawn else "AA-99", "01", 0, 4),
        # BB: counties 3 and 4 sit in a district NYT never reported, on both maps.
        district("BB-01", "02", 0, 2),
        district("BB-02", "02", 2, 2),
        district("CC-01", "03", 0, 4),
        district("DD-01", "04", 0, 2),
    ]


class TestRedistrictingMain:
    @pytest.fixture()
    def inputs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> dict[str, Path]:
        paths = {
            "nyt": tmp_path / "house.json",
            "new": tmp_path / "new.topo.json",
            "old": tmp_path / "old.topo.json",
            "counties": tmp_path / "counties.shp",
        }
        paths["nyt"].write_text(json.dumps(_main_nyt()), encoding="utf-8")
        features = {
            paths["counties"]: _main_counties(),
            paths["new"]: _main_districts(redrawn=True),
            paths["old"]: _main_districts(redrawn=False),
        }

        def read_geojson(path: Path, _tmp: Path, _name: str) -> list[dict[str, Any]]:
            return features[path]

        # The real reader shells out to ogr2ogr; the fixtures stand in for its output.
        monkeypatch.setattr(check, "_read_geojson", read_geojson)
        return paths

    def _run(
        self,
        paths: dict[str, Path],
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        *extra: str,
    ) -> list[list[str]]:
        argv = [
            "check_redistricting.py",
            "--nyt", str(paths["nyt"]),
            "--districts", str(paths["new"]),
            "--counties", str(paths["counties"]),
            *extra,
        ]  # fmt: skip
        monkeypatch.setattr(sys, "argv", argv)
        check.main()
        return [line.split() for line in capsys.readouterr().out.splitlines()]

    def test_old_and_new_map_rank_by_the_old_score_and_flag_redrawn_states(
        self,
        inputs: dict[str, Path],
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        lines = self._run(inputs, capsys, monkeypatch, "--old", str(inputs["old"]))

        assert lines == [
            ["state", "counties", "old", "new"],
            ["AA", "4", "1.00", "0.00", "<-", "redrawn"],  # worst on the old map
            ["BB", "4", "0.50", "0.50"],  # noisy on both: not redrawn
            ["CC", "4", "0.00", "0.00"],
            ["DD", "2", "n/a", "n/a"],  # unscored states come last
            ["redrawn:", "AA"],
        ]

    def test_no_state_redrawn_gives_an_empty_redrawn_line(
        self,
        inputs: dict[str, Path],
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The "old" map is the new one: nothing changed anywhere.
        lines = self._run(inputs, capsys, monkeypatch, "--old", str(inputs["new"]))

        assert all("<-" not in line for line in lines)
        assert lines[-1] == ["redrawn:"]

    def test_single_map_layout_ranks_by_its_own_score_without_flags(
        self,
        inputs: dict[str, Path],
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        lines = self._run(inputs, capsys, monkeypatch)

        assert lines == [
            ["state", "counties", "mismatch"],
            ["BB", "4", "0.50"],
            ["AA", "4", "0.00"],
            ["CC", "4", "0.00"],
            ["DD", "2", "n/a"],
        ]


class TestStateTables:
    """Spot-check the FIPS and name tables against known values."""

    @pytest.mark.parametrize(
        ("fips", "abbrev", "name"),
        [
            ("06", "CA", "California"),
            ("48", "TX", "Texas"),
            ("38", "ND", "North Dakota"),
        ],
    )
    def test_known_states(self, fips: str, abbrev: str, name: str) -> None:
        assert builder.FIPS_TO_ST[fips] == abbrev
        assert builder.ST_NAME[abbrev] == name

    def test_fifty_states(self) -> None:
        assert len(builder.FIPS_TO_ST) == len(builder.ST_NAME) == 50


class FakeResponse(io.BytesIO):
    """A urlopen() result: readable, a context manager, with a final URL."""

    def __init__(self, body: bytes, url: str) -> None:
        super().__init__(body)
        self._url = url

    def geturl(self) -> str:
        return self._url


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


class TestFetchZip:
    URL = "https://www2.census.gov/geo/tiger/TIGER2026/CD/tl_2026_49_cd120.zip"

    def test_downloads_once_and_extracts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
            calls.append(req.full_url)
            return FakeResponse(_zip_bytes({"a.shp": b"x"}), req.full_url)

        monkeypatch.setattr(builder.urllib.request, "urlopen", fake_urlopen)
        folder = builder.fetch_zip(self.URL, tmp_path)
        assert folder == tmp_path / "tl_2026_49_cd120"
        assert (folder / "a.shp").read_bytes() == b"x"
        assert builder.fetch_zip(self.URL, tmp_path) == folder
        assert calls == [self.URL]  # the second call used the extracted folder
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "tl_2026_49_cd120",
            "tl_2026_49_cd120.zip",
        ]

    def test_leftover_partial_extraction_is_not_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "tl_2026_49_cd120.zip").write_bytes(_zip_bytes({"a.shp": b"x"}))
        partial = tmp_path / "tl_2026_49_cd120.extracting"
        partial.mkdir()
        (partial / "junk").write_text("half")
        monkeypatch.setattr(builder.urllib.request, "urlopen", lambda *a, **k: 1 / 0)
        folder = builder.fetch_zip(self.URL, tmp_path)
        assert sorted(p.name for p in folder.iterdir()) == ["a.shp"]

    def test_redirect_off_census_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            builder.urllib.request,
            "urlopen",
            lambda req, timeout: FakeResponse(b"x", "http://evil.example/x.zip"),
        )
        with pytest.raises(ValueError, match="refusing"):
            builder.fetch_zip(self.URL, tmp_path)
        assert not list(tmp_path.glob("*.zip*"))  # nothing cached

    def test_other_hosts_are_refused_before_any_request(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="refusing"):
            builder.fetch_zip("https://example.com/a.zip", tmp_path)


needs_gdal = pytest.mark.skipif(
    shutil.which("ogr2ogr") is None or shutil.which("mapshaper") is None,
    reason="ogr2ogr and mapshaper are required",
)


def _write_shapefile(
    path: Path, rows: list[tuple[str, str, dict[str, Any]]], cd_field: str
) -> None:
    """Write a small shapefile of (STATEFP, district, geometry) rows with ogr2ogr."""
    geojson = path.with_suffix(".geojson")
    geojson.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"STATEFP": st, cd_field: cd, "EXTRA": "x"},
                        "geometry": geom,
                    }
                    for st, cd, geom in rows
                ],
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "ogr2ogr",
            "-f",
            "ESRI Shapefile",
            "-a_srs",
            "EPSG:4326",
            str(path),
            str(geojson),
        ],
        check=True,
        capture_output=True,
    )


@needs_gdal
class TestBuildMapEndToEnd:
    """build_map and the CLI on tiny shapefiles, with the downloads replaced."""

    def prepare(self, scratch: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        base = scratch / "base"
        base.mkdir()
        # Utah (49) as two blocks, Ohio (39) as one, plus a delegate that must be dropped.
        _write_shapefile(
            base / "base.shp",
            [
                ("49", "01", square(0, 0)),
                ("49", "02", square(1, 0)),
                ("39", "01", square(2, 0)),
                ("11", "98", square(3, 0)),
            ],
            "CD119FP",
        )
        redrawn = scratch / "ut"
        redrawn.mkdir()
        _write_shapefile(
            redrawn / "tl_2026_49_cd120.shp",
            [("49", "01", square(0, 0, 2, 0.5)), ("49", "02", square(0, 0.5, 2, 0.5))],
            "CD120FP",
        )
        folders = {"base": base, "ut": redrawn}

        def fake_fetch(url: str, dest: Path) -> Path:
            return folders["ut" if "tl_2026_49" in url else "base"]

        monkeypatch.setattr(builder, "fetch_zip", fake_fetch)
        monkeypatch.setattr(
            builder, "check_districts", lambda features, expected=435: None
        )
        return builder.MapRecipe(
            99,
            "https://www2.census.gov/base.zip",
            "CD119FP",
            {"UT": sources.StateSource("UT", "49", "Utah", "test")},
            "100%",
        )

    def test_build_map_replaces_the_redrawn_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recipe = self.prepare(tmp_path, monkeypatch)
        out = tmp_path / "out" / "m.topo.json"
        features = builder.build_map(recipe, tmp_path, out)
        assert [f["properties"]["name"] for f in features] == [
            "OH-01",
            "UT-01",
            "UT-02",
        ]
        topo = json.loads(out.read_text(encoding="utf-8"))
        geoms = topo["objects"]["map"]["geometries"]
        assert [g["properties"] for g in geoms] == [
            {"name": "OH-01", "region": "ohio"},
            {"name": "UT-01", "region": "utah"},
            {"name": "UT-02", "region": "utah"},
        ]
        ut1 = builder.shape(
            next(f for f in features if f["properties"]["name"] == "UT-01")["geometry"]
        )
        assert ut1.bounds == (0.0, 0.0, 2.0, 0.5)  # the new plan's top/bottom split

    def test_cli_with_map_and_fetch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        out = tmp_path / "cli.topo.json"
        recipe = self.prepare(tmp_path, monkeypatch)
        monkeypatch.setitem(builder.RECIPES, 24, recipe)
        monkeypatch.setattr(
            sys,
            "argv",
            ["b", "--map", "24", "--fetch", str(tmp_path), "--out", str(out)],
        )
        builder.main()
        assert "Wrote 3 districts" in capsys.readouterr().out
        assert out.exists()

    def test_cli_with_shp_builds_map_21_style(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        shp = tmp_path / "cd.shp"
        _write_shapefile(
            shp, [("49", "00", square(0, 0)), ("11", "98", square(1, 0))], "CD119FP"
        )
        monkeypatch.setattr(
            builder, "check_districts", lambda features, expected=435: None
        )
        out = tmp_path / "21.topo.json"
        monkeypatch.setattr(sys, "argv", ["b", "--shp", str(shp), "--out", str(out)])
        builder.main()
        assert "Wrote 1 districts" in capsys.readouterr().out
        topo = json.loads(out.read_text(encoding="utf-8"))
        assert topo["objects"]["map"]["geometries"][0]["properties"] == {
            "name": "UT-01",
            "region": "utah",
        }


class TestCliArguments:
    @pytest.mark.parametrize(
        "argv",
        [
            ["--out", "o"],
            ["--out", "o", "--map", "24"],
            ["--out", "o", "--map", "24", "--shp", "s", "--fetch", "f"],
            ["--out", "o", "--shp", "s", "--fetch", "f"],
        ],
    )
    def test_bad_combinations_are_usage_errors(
        self, argv: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["b", *argv])
        with pytest.raises(SystemExit) as exc:
            builder.main()
        assert exc.value.code == 2
