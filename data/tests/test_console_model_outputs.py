"""Tests for console.services.model_outputs against a real (temp) database.

Exercises the list/detail context builders and the delete helpers using the
shared ``db`` fixture (a fresh SQLite database per test).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import Database
from models import ElectionType

from console.services.model_outputs import (
    build_output_detail_context,
    build_outputs_context,
    delete_model_output,
    delete_selected_model_outputs,
)

WESTMINSTER_BASELINE_TYPES = [ElectionType.uk_general, ElectionType.by_election]


def _seed(db: Database) -> dict[str, int]:
    """Build a map, two parties, two seats, a baseline GE, and two model outputs.

    Baseline winners: both Labour. Latest model winners: Alpha Labour (no change),
    Bravo Conservative (changed).
    """
    m = db.add_map("UK")
    region = db.add_region(m.id, "East Midlands")
    lab = db.add_party("Labour", colour="#dc2626")
    con = db.add_party("Conservative", colour="#1d4ed8")
    alpha = db.add_seat(m.id, "Alpha", region_id=region.id, electorate=1000)
    bravo = db.add_seat(m.id, "Bravo", region_id=region.id, electorate=1000)

    baseline = db.add_election(m.id, 2024, "2024 General Election", ElectionType.uk_general)
    db.add_vote(baseline.id, alpha.id, party_id=lab.id, vote_total=500.0, elected=True)
    db.add_vote(baseline.id, alpha.id, party_id=con.id, vote_total=300.0)
    db.add_vote(baseline.id, bravo.id, party_id=lab.id, vote_total=450.0, elected=True)
    db.add_vote(baseline.id, bravo.id, party_id=con.id, vote_total=400.0)

    model_a = db.add_election(m.id, 2026, "UNS 2026-01-01", ElectionType.model_uns)
    db.add_vote(model_a.id, alpha.id, party_id=lab.id, vote_total=520.0, elected=True)
    db.add_vote(model_a.id, alpha.id, party_id=con.id, vote_total=300.0)
    db.add_vote(model_a.id, bravo.id, party_id=con.id, vote_total=480.0, elected=True)
    db.add_vote(model_a.id, bravo.id, party_id=lab.id, vote_total=400.0)

    model_b = db.add_election(m.id, 2026, "UNS 2026-02-01", ElectionType.model_uns)
    db.add_vote(model_b.id, alpha.id, party_id=lab.id, vote_total=530.0, elected=True)
    db.add_vote(model_b.id, bravo.id, party_id=con.id, vote_total=470.0, elected=True)

    return {
        "map_id": m.id,
        "baseline_id": baseline.id,
        "model_a_id": model_a.id,
        "model_b_id": model_b.id,
    }


HOLYROOD_BASELINE_TYPES = [ElectionType.holyrood_general]


def _seed_holyrood(db: Database) -> dict[str, int]:
    """Build a Holyrood baseline + one holyrood_uns model output (const + list seats).

    Float vote totals (including a large duplicated list total) exercise the
    float-tolerant reads the model_outputs service relies on.
    """
    m = db.add_map("Scotland")
    region = db.add_region(m.id, "Glasgow")
    snp = db.add_party("SNP", colour="#FDF38E")
    lab = db.add_party("Labour", colour="#dc2626")
    const_seat = db.add_seat(m.id, "Glasgow Const 1", region_id=region.id)
    list_seat = db.add_seat(m.id, "Glasgow List 1", region_id=region.id)

    baseline = db.add_election(
        m.id, 2021, "2021 Scottish Parliament Election", ElectionType.holyrood_general
    )
    db.add_vote(baseline.id, const_seat.id, party_id=snp.id, vote_total=500.0, elected=True)
    db.add_vote(baseline.id, const_seat.id, party_id=lab.id, vote_total=300.0)

    model = db.add_election(m.id, 2026, "Holyrood UNS 2026-07-05", ElectionType.holyrood_uns)
    db.add_vote(model.id, const_seat.id, party_id=snp.id, vote_total=520.0, elected=True)
    db.add_vote(model.id, const_seat.id, party_id=lab.id, vote_total=300.0)
    db.add_vote(model.id, list_seat.id, party_id=lab.id, vote_total=12000.0, elected=True)
    db.add_vote(model.id, list_seat.id, party_id=snp.id, vote_total=9000.0)

    return {"map_id": m.id, "baseline_id": baseline.id, "model_id": model.id}


class TestHolyroodOutputs:
    """The shared model-outputs service also serves holyrood_uns elections."""

    def test_lists_holyrood_output(self, db: Database) -> None:
        _seed_holyrood(db)
        ctx = build_outputs_context(
            db, election_type=ElectionType.holyrood_uns, trend_cache_path=None, show_all=True
        )
        assert ctx["total_output_count"] == 1
        assert ctx["outputs"][0]["name"] == "Holyrood UNS 2026-07-05"

    def test_detail_counts_constituency_and_list_seats(self, db: Database) -> None:
        ids = _seed_holyrood(db)
        ctx = build_output_detail_context(
            db,
            election_id=ids["model_id"],
            election_type=ElectionType.holyrood_uns,
            baseline_types=HOLYROOD_BASELINE_TYPES,
            page=1,
        )
        assert ctx is not None
        assert ctx["election"]["name"] == "Holyrood UNS 2026-07-05"
        # One constituency + one list seat.
        assert ctx["pagination"]["total_seats"] == 2


class TestBuildOutputsContext:
    """The list page context: counts, items, and derived trend datasets."""

    def test_lists_model_outputs_with_counts(self, db: Database) -> None:
        _seed(db)
        ctx = build_outputs_context(
            db, election_type=ElectionType.model_uns, trend_cache_path=None, show_all=True
        )
        assert ctx["total_output_count"] == 2
        assert len(ctx["outputs"]) == 2
        # Newest first (Election.id desc); model_b's "UNS 2026-02-01" leads.
        assert ctx["outputs"][0]["name"] == "UNS 2026-02-01"
        assert ctx["outputs"][1]["vote_rows"] == 4  # model_a has 4 vote rows

    def test_derives_trend_from_votes_without_cache(self, db: Database) -> None:
        _seed(db)
        ctx = build_outputs_context(
            db, election_type=ElectionType.model_uns, trend_cache_path=None, show_all=True
        )
        trend = ctx["trend_data"]
        assert trend["labels"] == ["UNS 2026-01-01", "UNS 2026-02-01"]
        assert len(trend["seats_datasets"]) == 2
        labels = {d["label"] for d in trend["seats_datasets"]}
        assert labels == {"Labour", "Conservative"}

    def test_default_limit_caps_results(self, db: Database) -> None:
        _seed(db)
        ctx = build_outputs_context(
            db,
            election_type=ElectionType.model_uns,
            trend_cache_path=None,
            show_all=False,
            default_limit=1,
        )
        assert ctx["total_output_count"] == 2  # full count regardless of limit
        assert len(ctx["outputs"]) == 1  # only the most recent shown


class TestBuildOutputDetailContext:
    """The detail page context: seats, pagination, and baseline change status."""

    def test_detail_reports_change_vs_baseline(self, db: Database) -> None:
        ids = _seed(db)
        ctx = build_output_detail_context(
            db,
            election_id=ids["model_a_id"],
            election_type=ElectionType.model_uns,
            baseline_types=WESTMINSTER_BASELINE_TYPES,
            page=1,
        )
        assert ctx is not None
        assert ctx["election"]["name"] == "UNS 2026-01-01"
        assert ctx["pagination"]["total_seats"] == 2
        assert ctx["baseline_election"] is not None
        statuses = {row["seat_name"]: row["change_status"] for row in ctx["seats"]}
        assert statuses["Alpha"] == "No change"
        assert statuses["Bravo"].startswith("Changed")

    def test_unknown_election_returns_none(self, db: Database) -> None:
        _seed(db)
        ctx = build_output_detail_context(
            db,
            election_id=999_999,
            election_type=ElectionType.model_uns,
            baseline_types=WESTMINSTER_BASELINE_TYPES,
            page=1,
        )
        assert ctx is None

    def test_wrong_type_returns_none(self, db: Database) -> None:
        ids = _seed(db)
        # The baseline is a uk_general, so asking for it as a model_uns finds nothing.
        ctx = build_output_detail_context(
            db,
            election_id=ids["baseline_id"],
            election_type=ElectionType.model_uns,
            baseline_types=WESTMINSTER_BASELINE_TYPES,
            page=1,
        )
        assert ctx is None


class TestDeleteModelOutputs:
    """Single and bulk delete helpers report counts and persist."""

    def test_delete_single_returns_vote_count(self, db: Database) -> None:
        ids = _seed(db)
        deleted = delete_model_output(
            db, election_id=ids["model_a_id"], election_type=ElectionType.model_uns
        )
        assert deleted == 4
        ctx = build_outputs_context(
            db, election_type=ElectionType.model_uns, trend_cache_path=None, show_all=True
        )
        assert ctx["total_output_count"] == 1

    def test_delete_single_unknown_returns_none(self, db: Database) -> None:
        _seed(db)
        assert (
            delete_model_output(db, election_id=999_999, election_type=ElectionType.model_uns)
            is None
        )

    def test_delete_selected_returns_counts(self, db: Database) -> None:
        ids = _seed(db)
        elections, votes = delete_selected_model_outputs(
            db,
            election_ids=[ids["model_a_id"], ids["model_b_id"]],
            election_type=ElectionType.model_uns,
        )
        assert elections == 2
        assert votes == 6  # 4 + 2 vote rows

    def test_delete_selected_ignores_wrong_type(self, db: Database) -> None:
        ids = _seed(db)
        # The baseline GE id is not a model_uns, so nothing is deleted.
        elections, votes = delete_selected_model_outputs(
            db, election_ids=[ids["baseline_id"]], election_type=ElectionType.model_uns
        )
        assert (elections, votes) == (0, 0)
