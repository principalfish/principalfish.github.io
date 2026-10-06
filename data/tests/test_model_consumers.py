"""Stored outputs survive trend repair and retain export/console semantics."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from console.services.model_outputs import build_outputs_context
from db import Database
from model_support.persistence import (
    OutputScope,
    OutputVote,
    output_dates,
    replace_output,
)
from model_support.trends import TREND_MODELS
from models import ElectionType, Vote
from scripts.export.payload import SeatRow, build_result_payload
from scripts.rebuild_model_trends import main
from sqlalchemy import select
from sqlalchemy.orm import joinedload


@pytest.mark.parametrize("model", TREND_MODELS)
@pytest.mark.parametrize("cache_state", ["missing", "corrupt"])
def test_repaired_history_feeds_export_and_console(
    db: Database,
    only_the_test_database: Path,
    tmp_path: Path,
    model: str,
    cache_state: str,
) -> None:
    definition = TREND_MODELS[model]
    election_map = db.add_map("Consumer map", parliament=definition.parliament)
    party_a = db.add_party("A")
    party_b = db.add_party("B")
    primary = db.add_seat(election_map.id, "Maine", electoral_votes=2)
    extra_name = {
        "holyrood": "Region List 1",
        "us-president": "Maine CD-1",
    }.get(model, "Second constituency")
    extra = db.add_seat(election_map.id, extra_name, electoral_votes=1)
    scope = OutputScope(
        definition.election_type, election_map.id, definition.name_prefix
    )
    election_ids: dict[int, int] = {}
    for day, share in [(4, 65), (2, 70), (3, 70), (1, 60)]:
        as_of = date(2026, 6, day)
        _, election_ids[day] = replace_output(
            only_the_test_database,
            scope,
            as_of,
            f"{scope.name_prefix} {as_of}",
            [
                OutputVote(primary.id, party_a.id, "", share, True),
                OutputVote(primary.id, party_b.id, "", 100 - share, False),
                OutputVote(extra.id, party_b.id, "", 300, True),
            ],
        )
    cache = tmp_path / "repaired.json"
    if cache_state == "corrupt":
        cache.write_text("{not valid JSON")
    before = only_the_test_database.read_bytes()
    assert (
        main(
            [
                "--model",
                model,
                "--map-id",
                str(election_map.id),
                "--database",
                str(only_the_test_database),
                "--output",
                str(cache),
            ]
        )
        == 0
    )
    assert only_the_test_database.read_bytes() == before
    assert len(output_dates(only_the_test_database, scope)) == 4
    entries = json.loads(cache.read_text())
    assert [entry["as_of_date"] for entry in entries] == [
        "2026-06-01",
        "2026-06-02",
        "2026-06-04",
    ]
    excluded_extra = model in {"holyrood", "us-president"}
    expected_a = [60.0, 70.0, 65.0] if excluded_extra else [15.0, 17.5, 16.2]
    assert [entry["parties"][str(party_a.id)]["v"] for entry in entries] == expected_a
    for entry in entries:
        assert entry["parties"][str(party_a.id)]["s"] == 1
        assert entry["parties"][str(party_b.id)]["s"] == 1
        if model == "us-president":
            assert entry["parties"][str(party_a.id)]["e"] == 2
            assert entry["parties"][str(party_b.id)]["e"] == 1

    context = build_outputs_context(
        db,
        election_type=ElectionType(definition.election_type),
        trend_cache_path=cache,
        show_all=True,
    )
    assert context["total_output_count"] == 4
    assert context["shows_electoral_votes"] == (model == "us-president")
    chart = context["trend_data"]
    assert chart["labels"] == [entry["election_name"] for entry in entries]
    datasets = {
        series["label"]: series["data"] for series in chart["vote_pct_datasets"]
    }
    assert datasets["A"] == expected_a
    tally = {series["label"]: series["data"] for series in chart["seats_datasets"]}
    assert tally["A"] == ([2, 2, 2] if model == "us-president" else [1, 1, 1])

    seats = [
        SeatRow(seat.id, seat.seat_name, None, None, None, seat.electoral_votes)
        for seat in (primary, extra)
    ]
    with db.session() as session:
        votes = session.scalars(
            select(Vote)
            .where(Vote.election_id == election_ids[4])
            .options(joinedload(Vote.party))
        ).all()
        payload = build_result_payload(seats, votes)
    assert payload["schema"] == "pf-results-v4"
    by_name = {seat["n"]: seat for seat in payload["seats"]}
    assert by_name["Maine"]["p"] == [[party_a.id, 65], [party_b.id, 35]]
    assert by_name["Maine"]["w"] == party_a.id
    assert by_name[extra_name]["p"] == [[party_b.id, 300]]
    assert by_name[extra_name]["w"] == party_b.id
