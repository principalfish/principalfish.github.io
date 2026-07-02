"""Tests for scripts.export.legacy — supplemental-entry repositioning.

``reposition_supplemental_entries`` re-applies each supplemental's configured
``insertBeforeId`` / ``insertAfterId`` after ``reorder_manifest_entries`` (which sorts by the
previous manifest order) may have moved it. It also honours the per-page ``parliaments`` filter
so a page only repositions its own supplementals. Pure function operating on a list of dicts.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scripts.export.legacy import (
    SUPPLEMENTAL_LEGACY_ELECTIONS,
    reposition_supplemental_entries,
)


def _supplemental(sid: str) -> dict[str, Any]:
    """The configured supplemental with id ``sid`` (so anchors track the source of truth)."""
    return next(s for s in SUPPLEMENTAL_LEGACY_ELECTIONS if s["id"] == sid)


def _ids(entries: list[dict[str, Any]]) -> list[str]:
    return [e["id"] for e in entries]


class TestRepositionSupplementalEntries:
    def test_insert_before_anchor(self) -> None:
        # current-senate is configured to lead its class (insertBeforeId=2024-us-senate); a prior
        # reorder can push it to the end, so it must be pulled back in front of the anchor.
        anchor = _supplemental("current-senate")["insertBeforeId"]
        entries = [{"id": anchor}, {"id": "2022-us-senate"}, {"id": "current-senate"}]
        reposition_supplemental_entries(entries, parliaments={"us_senate"})
        assert _ids(entries) == ["current-senate", anchor, "2022-us-senate"]

    def test_insert_after_anchor(self) -> None:
        # 2019-general-changed-boundaries is configured to sit just after 2024-general.
        anchor = _supplemental("2019-general-changed-boundaries")["insertAfterId"]
        entries = [
            {"id": "2019-general-changed-boundaries"},
            {"id": anchor},
            {"id": "2019-general"},
        ]
        # parliaments=None processes every supplemental (the None default path).
        reposition_supplemental_entries(entries, None)
        assert _ids(entries) == [anchor, "2019-general-changed-boundaries", "2019-general"]

    def test_parliament_filter_skips_other_pages_supplementals(self) -> None:
        # current-senate belongs to us_senate; a westminster-only page must leave it untouched.
        entries = [{"id": "2024-us-senate"}, {"id": "current-senate"}]
        reposition_supplemental_entries(entries, parliaments={"westminster"})
        assert _ids(entries) == ["2024-us-senate", "current-senate"]

    def test_westminster_supplemental_skipped_on_us_page(self) -> None:
        # 2019-general-changed-boundaries defaults to the westminster parliament, so a US-only
        # page skips it (exercises the `get("parliament", "westminster")` default).
        entries = [
            {"id": "2019-general-changed-boundaries"},
            {"id": "2024-general"},
        ]
        reposition_supplemental_entries(entries, parliaments={"us_senate"})
        assert _ids(entries) == ["2019-general-changed-boundaries", "2024-general"]

    def test_missing_entry_is_a_noop(self) -> None:
        # No supplemental ids present → nothing to reposition, no error.
        entries = [{"id": "2024-general"}, {"id": "2019-general"}]
        reposition_supplemental_entries(entries, None)
        assert _ids(entries) == ["2024-general", "2019-general"]

    def test_is_idempotent(self) -> None:
        anchor = _supplemental("current-senate")["insertBeforeId"]
        entries = [{"id": anchor}, {"id": "current-senate"}]
        reposition_supplemental_entries(entries, parliaments={"us_senate"})
        once = _ids(entries)
        reposition_supplemental_entries(entries, parliaments={"us_senate"})
        assert _ids(entries) == once == ["current-senate", anchor]
