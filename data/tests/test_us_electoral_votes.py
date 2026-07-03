"""Tests for the per-era US electoral-vote table (data/old_data/scripts/usa)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

USA_DIR = Path(__file__).resolve().parents[1] / "old_data" / "scripts" / "usa"


def _load(name: str) -> ModuleType:
    """Load a usa/ script module by file path (they are run as scripts, not a package)."""
    spec = importlib.util.spec_from_file_location(name, USA_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


us_ev = _load("us_electoral_votes")


class TestEraForYear:
    """_era_for_year: routing to the correct census apportionment."""

    def test_era_boundaries_1960(self) -> None:
        # Both 1964 and 1968 use the 1960 census.
        assert us_ev._era_for_year(1964) == 1960
        assert us_ev._era_for_year(1968) == 1960

    def test_era_boundaries_1970(self) -> None:
        # 1972, 1976, and 1980 all use the 1970 census.
        assert us_ev._era_for_year(1972) == 1970
        assert us_ev._era_for_year(1976) == 1970
        assert us_ev._era_for_year(1980) == 1970

    def test_era_boundaries_1980(self) -> None:
        # 1984 and 1988 use the 1980 census.
        assert us_ev._era_for_year(1984) == 1980
        assert us_ev._era_for_year(1988) == 1980

    def test_era_boundary_1992(self) -> None:
        # 1992 is the first election under the 1990 census.
        assert us_ev._era_for_year(1992) == 1990

    def test_era_boundary_crossings(self) -> None:
        # Spot-check that consecutive era elections flip correctly.
        assert us_ev._era_for_year(1968) == 1960
        assert us_ev._era_for_year(1972) == 1970
        assert us_ev._era_for_year(1980) == 1970
        assert us_ev._era_for_year(1984) == 1980
        assert us_ev._era_for_year(1988) == 1980
        assert us_ev._era_for_year(1992) == 1990
        assert us_ev._era_for_year(2000) == 1990
        assert us_ev._era_for_year(2004) == 2000


class TestEvFor:
    """ev_for: per-era lookups and ME/NE special cases."""

    def test_california_by_era(self) -> None:
        assert us_ev.ev_for("California", 1964) == 40   # 1960 census
        assert us_ev.ev_for("California", 1972) == 45   # 1970 census
        assert us_ev.ev_for("California", 1984) == 47   # 1980 census
        assert us_ev.ev_for("California", 2004) == 55   # 2000 census
        assert us_ev.ev_for("California", 2012) == 55
        assert us_ev.ev_for("California", 2024) == 54

    def test_texas_by_era(self) -> None:
        assert us_ev.ev_for("Texas", 1964) == 25  # 1960 census
        assert us_ev.ev_for("Texas", 1972) == 26  # 1970 census
        assert us_ev.ev_for("Texas", 1984) == 29  # 1980 census
        assert us_ev.ev_for("Texas", 2000) == 32  # 1990 census
        assert us_ev.ev_for("Texas", 2004) == 34  # 2000 census
        assert us_ev.ev_for("Texas", 2016) == 38
        assert us_ev.ev_for("Texas", 2024) == 40

    def test_florida_by_era(self) -> None:
        assert us_ev.ev_for("Florida", 1964) == 14  # 1960 census
        assert us_ev.ev_for("Florida", 1972) == 17  # 1970 census
        assert us_ev.ev_for("Florida", 1984) == 21  # 1980 census
        assert us_ev.ev_for("Florida", 2000) == 25  # 1990 census
        assert us_ev.ev_for("Florida", 2004) == 27  # 2000 census
        assert us_ev.ev_for("Florida", 2016) == 29
        assert us_ev.ev_for("Florida", 2024) == 30

    def test_2000_election_uses_1990_census(self) -> None:
        # The 2000 election ran on the 1990-census apportionment (one cycle behind the
        # count), not the 2000-census values used from 2004 on — this is what makes the
        # 2000 map tally to Bush 271 / Gore 267 rather than 278 / 260.
        assert us_ev.ev_for("New York", 2000) == 33   # 33 (1990) vs 31 (2000 census)
        assert us_ev.ev_for("California", 2000) == 54  # 54 (1990) vs 55 (2000 census)
        assert us_ev.ev_for("Pennsylvania", 2000) == 23

    def test_dc_constant(self) -> None:
        # DC gets 3 EVs in all supported years; 23rd Amendment first applied 1964.
        assert us_ev.ev_for("District of Columbia", 1964) == 3
        assert us_ev.ev_for("District of Columbia", 2000) == 3
        assert us_ev.ev_for("District of Columbia", 2012) == 3
        assert us_ev.ev_for("District of Columbia", 2024) == 3

    def test_maine_statewide(self) -> None:
        assert us_ev.ev_for("Maine", 2000) == 2
        assert us_ev.ev_for("Maine", 2024) == 2

    def test_nebraska_statewide(self) -> None:
        assert us_ev.ev_for("Nebraska", 2000) == 2
        assert us_ev.ev_for("Nebraska", 2024) == 2

    def test_maine_cd_units(self) -> None:
        assert us_ev.ev_for("Maine CD-1", 2024) == 1
        assert us_ev.ev_for("Maine CD-2", 2024) == 1

    def test_nebraska_cd_units(self) -> None:
        assert us_ev.ev_for("Nebraska CD-1", 2024) == 1
        assert us_ev.ev_for("Nebraska CD-2", 2024) == 1
        assert us_ev.ev_for("Nebraska CD-3", 2024) == 1

    def test_era_boundary_2008(self) -> None:
        # 2008 uses the 2000 era: California is 55 there.
        assert us_ev.ev_for("California", 2008) == 55

    def test_era_boundary_2012(self) -> None:
        # 2012 uses the 2010 era: Texas is 38 there.
        assert us_ev.ev_for("Texas", 2012) == 38

    def test_era_boundary_2020(self) -> None:
        # 2020 uses the 2010 era: California is 55, not 54.
        assert us_ev.ev_for("California", 2020) == 55

    def test_era_boundary_2024(self) -> None:
        # 2024 uses the 2020 era: California is 54.
        assert us_ev.ev_for("California", 2024) == 54

    def test_unknown_unit_raises(self) -> None:
        with pytest.raises(KeyError):
            us_ev.ev_for("Nonexistent State", 2024)


class TestEvMapForYear:
    """ev_map_for_year: full presidential maps sum to 538 and include ME/NE/DC."""

    def test_sum_1964(self) -> None:
        assert sum(us_ev.ev_map_for_year(1964).values()) == 538  # 1960-census era

    def test_sum_1968(self) -> None:
        assert sum(us_ev.ev_map_for_year(1968).values()) == 538  # 1960-census era

    def test_sum_1972(self) -> None:
        assert sum(us_ev.ev_map_for_year(1972).values()) == 538  # 1970-census era

    def test_sum_1980(self) -> None:
        assert sum(us_ev.ev_map_for_year(1980).values()) == 538  # 1970-census era

    def test_sum_1984(self) -> None:
        assert sum(us_ev.ev_map_for_year(1984).values()) == 538  # 1980-census era

    def test_sum_1988(self) -> None:
        assert sum(us_ev.ev_map_for_year(1988).values()) == 538  # 1980-census era

    def test_sum_2024(self) -> None:
        assert sum(us_ev.ev_map_for_year(2024).values()) == 538

    def test_sum_2012(self) -> None:
        assert sum(us_ev.ev_map_for_year(2012).values()) == 538

    def test_sum_2000(self) -> None:
        assert sum(us_ev.ev_map_for_year(2000).values()) == 538  # 1990-census era

    def test_sum_2004(self) -> None:
        assert sum(us_ev.ev_map_for_year(2004).values()) == 538  # 2000-census era

    def test_contains_me_ne_units(self) -> None:
        ev_map = us_ev.ev_map_for_year(2024)
        assert ev_map["Maine"] == 2
        assert ev_map["Maine CD-1"] == 1
        assert ev_map["Maine CD-2"] == 1
        assert ev_map["Nebraska"] == 2
        assert ev_map["Nebraska CD-1"] == 1
        assert ev_map["Nebraska CD-2"] == 1
        assert ev_map["Nebraska CD-3"] == 1
        assert ev_map["District of Columbia"] == 3


class TestEvByEraTables:
    """Structural invariants each era table must hold (49 whole units → 529)."""

    def test_every_era_has_49_units_summing_to_529(self) -> None:
        # 49 whole units (48 non-ME/NE states + DC) at 529 EVs, +ME(4)+NE(5) = 538.
        for era, table in us_ev.EV_BY_ERA.items():
            assert len(table) == 49, f"era {era} has {len(table)} units, expected 49"
            assert sum(table.values()) == 529, f"era {era} sums to {sum(table.values())}, expected 529"

    def test_every_era_excludes_me_ne(self) -> None:
        # Maine and Nebraska are never in the per-era table; they are special-cased.
        for era, table in us_ev.EV_BY_ERA.items():
            assert "Maine" not in table, f"era {era} should not list Maine"
            assert "Nebraska" not in table, f"era {era} should not list Nebraska"
