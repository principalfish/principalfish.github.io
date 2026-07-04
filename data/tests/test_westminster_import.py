"""Tests for the Westminster general-election importer's party-key mapping.

Guards the ``reform``/``ukip`` distinction: the 2024 source data keys Reform UK
candidates as ``reform`` (mapped to the existing "Reform UK" party), while the
older cycles key genuine UKIP candidates as ``ukip``. Losing the ``reform``
mapping would silently re-import 2024 Reform votes as UKIP.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "old_data" / "scripts" / "westminster"))

from import_general_elections import PARTY_KEY_TO_NAME, humanize_party_name


class TestPartyKeyToName:
    """The party-key map keeps Reform UK and UKIP as distinct parties."""

    def test_reform_maps_to_reform_uk(self) -> None:
        assert PARTY_KEY_TO_NAME["reform"] == "Reform UK"
        assert humanize_party_name("reform") == "Reform UK"

    def test_ukip_stays_ukip(self) -> None:
        assert PARTY_KEY_TO_NAME["ukip"] == "UK Independence Party"
        assert humanize_party_name("ukip") == "UK Independence Party"

    def test_reform_and_ukip_are_distinct(self) -> None:
        assert PARTY_KEY_TO_NAME["reform"] != PARTY_KEY_TO_NAME["ukip"]
