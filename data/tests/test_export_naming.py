"""Tests for scripts.export.naming — party-key resolution, slugs, and election naming.

Imports the helpers via ``export_elections`` (which re-exports the ``scripts.export``
package), mirroring ``test_export_elections.py``. Pure functions; no DB needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from models import ElectionType
from export_elections import (
    file_stem_for_election,
    legacy_party_key_for_vote,
    manifest_id_for_election,
    normalize_region_name,
    normalize_token,
    normalize_vote_total_value,
    party_key_for_party,
    slugify,
)


class _Party:
    """Minimal stand-in for a Party row (only the fields the helpers read)."""

    def __init__(self, name: str, short_name: str | None = None) -> None:
        self.name = name
        self.short_name = short_name


class _Vote:
    """Minimal stand-in for a Vote row (only ``.party``)."""

    def __init__(self, party: _Party | None) -> None:
        self.party = party


class _Election:
    """Minimal stand-in for an Election row (type / name / year)."""

    def __init__(self, name: str, type: ElectionType, year: int = 2024) -> None:
        self.name = name
        self.type = type
        self.year = year


class TestLegacyPartyKeyForVote:
    """Resolves the legacy party key, with the Reform-UK / UKIP year split."""

    def test_no_party_is_others(self) -> None:
        assert legacy_party_key_for_vote(_Vote(None)) == "others"

    def test_reform_short_name_2024_plus_is_reform(self) -> None:
        vote = _Vote(_Party("Reform UK", short_name="Reform UK"))
        assert legacy_party_key_for_vote(vote, election_year=2024) == "reform"

    def test_reform_short_name_pre_2024_is_ukip(self) -> None:
        vote = _Vote(_Party("Reform UK", short_name="Reform UK"))
        assert legacy_party_key_for_vote(vote, election_year=2019) == "ukip"

    def test_reform_unknown_year_is_ukip(self) -> None:
        vote = _Vote(_Party("Reform UK", short_name="Reform UK"))
        assert legacy_party_key_for_vote(vote, election_year=None) == "ukip"

    def test_reform_via_name_when_no_short_name(self) -> None:
        vote = _Vote(_Party("Reform UK"))
        assert legacy_party_key_for_vote(vote, election_year=2024) == "reform"

    def test_short_name_takes_precedence_and_is_mapped(self) -> None:
        # short_name normalises to a mapped key, so the name is never consulted.
        vote = _Vote(_Party("Scottish National Party", short_name="SNP"))
        assert legacy_party_key_for_vote(vote, election_year=2024) == "snp"

    def test_falls_back_to_name_mapping(self) -> None:
        vote = _Vote(_Party("Liberal Democrats"))
        assert legacy_party_key_for_vote(vote, election_year=2024) == "libdems"

    def test_unmapped_name_passes_through_normalised(self) -> None:
        vote = _Vote(_Party("Monster Raving Loony"))
        assert legacy_party_key_for_vote(vote, election_year=2024) == "monsterravingloony"


class TestPartyKeyForParty:
    """Canonical party key from a Party row (no year context)."""

    def test_reform_short_name(self) -> None:
        assert party_key_for_party(_Party("Reform UK", short_name="Reform UK")) == "reform"

    def test_ukip_short_name(self) -> None:
        assert party_key_for_party(_Party("UK Independence Party", short_name="UKIP")) == "ukip"

    def test_ukip_via_name(self) -> None:
        assert party_key_for_party(_Party("UK Independence Party")) == "ukip"

    def test_reform_via_name(self) -> None:
        assert party_key_for_party(_Party("Reform UK")) == "reform"

    def test_mapped_name(self) -> None:
        assert party_key_for_party(_Party("Scottish National Party")) == "snp"

    def test_us_democratic_maps_to_democrat(self) -> None:
        assert party_key_for_party(_Party("Democratic", short_name="democratic")) == "democrat"

    def test_us_democratic_via_name(self) -> None:
        assert party_key_for_party(_Party("Democratic")) == "democrat"

    def test_us_republican(self) -> None:
        assert party_key_for_party(_Party("Republican", short_name="republican")) == "republican"

    def test_us_green_maps_to_usgreen(self) -> None:
        assert party_key_for_party(_Party("US Green", short_name="usgreen")) == "usgreen"

    def test_unmapped_name_passes_through(self) -> None:
        assert party_key_for_party(_Party("Foo Bar")) == "foobar"


class TestSlugAndTokenHelpers:
    """slugify / normalize_token / normalize_region_name."""

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("2024 General Election", "2024-general-election"),
            ("  Hello  World  ", "hello-world"),
            ("!!!", "election"),
            ("", "election"),
        ],
    )
    def test_slugify(self, value: str, expected: str) -> None:
        assert slugify(value) == expected

    def test_normalize_token_strips_non_alnum(self) -> None:
        assert normalize_token("Reform UK!") == "reformuk"

    @pytest.mark.parametrize(
        "value, expected",
        [
            (None, "unknown"),
            ("", "unknown"),
            ("East Midlands", "eastmidlands"),
            ("South-West", "southwest"),
        ],
    )
    def test_normalize_region_name(self, value: str | None, expected: str) -> None:
        assert normalize_region_name(value) == expected


class TestNormalizeVoteTotalValue:
    """Whole numbers collapse to int; fractions round to two places."""

    def test_whole_number_returns_int(self) -> None:
        result = normalize_vote_total_value(100.0)
        assert result == 100
        assert isinstance(result, int)

    def test_rounds_to_whole_returns_int(self) -> None:
        result = normalize_vote_total_value(100.004)
        assert result == 100
        assert isinstance(result, int)

    def test_fractional_returns_rounded_float(self) -> None:
        result = normalize_vote_total_value(100.456)
        assert result == 100.46
        assert isinstance(result, float)


class TestFileStemForElection:
    """Output JSON filename stem per election type/name."""

    def test_model_uns_is_prediction_simulation(self) -> None:
        assert file_stem_for_election(_Election("anything", ElectionType.model_uns)) == "prediction-simulation"

    def test_uk_general(self) -> None:
        assert file_stem_for_election(_Election("2024 General Election", ElectionType.uk_general)) == "uk-general-2024"

    def test_holyrood_general(self) -> None:
        election = _Election("2021 Scottish Parliament Election", ElectionType.holyrood_general)
        assert file_stem_for_election(election) == "holyrood-general-2021"

    def test_holyrood_general_changed_boundaries(self) -> None:
        election = _Election(
            "2021 Scottish Parliament Election (2026 Boundaries)", ElectionType.holyrood_general
        )
        assert file_stem_for_election(election) == "holyrood-general-2021-changed-boundaries"

    def test_us_house(self) -> None:
        election = _Election("2024 US House Election", ElectionType.us_house, year=2024)
        assert file_stem_for_election(election) == "us-house-2024"

    def test_us_president(self) -> None:
        election = _Election("2024 US Presidential Election", ElectionType.us_presidential, year=2024)
        assert file_stem_for_election(election) == "us-president-2024"

    def test_fallback_slugifies_type_year_name(self) -> None:
        election = _Election("Some By-Election", ElectionType.by_election, year=2025)
        assert file_stem_for_election(election) == "by-election-2025-some-by-election"


class TestManifestIdForElection:
    """Stable manifest id per election type/name."""

    def test_model_uns_is_current_prediction(self) -> None:
        assert manifest_id_for_election(_Election("anything", ElectionType.model_uns)) == "current-prediction"

    def test_uk_general(self) -> None:
        assert manifest_id_for_election(_Election("2024 General Election", ElectionType.uk_general)) == "2024-general"

    def test_holyrood_general(self) -> None:
        election = _Election("2021 Scottish Parliament Election", ElectionType.holyrood_general)
        assert manifest_id_for_election(election) == "2021-holyrood"

    def test_us_house(self) -> None:
        election = _Election("2024 US House Election", ElectionType.us_house, year=2024)
        assert manifest_id_for_election(election) == "2024-us-house"

    def test_us_president(self) -> None:
        election = _Election("2024 US Presidential Election", ElectionType.us_presidential, year=2024)
        assert manifest_id_for_election(election) == "2024-us-president"

    def test_fallback_slugifies_year_name(self) -> None:
        election = _Election("Some By-Election", ElectionType.by_election, year=2025)
        assert manifest_id_for_election(election) == "2025-some-by-election"
