"""Tests for scripts.export.seat_overrides — the Alaska 2026 candidate override."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from export_elections import SeatRow, build_result_payload
from db import SeatStatus
from scripts.export.seat_overrides import (
    OVERRIDES_PATH,
    SeatOverridesError,
    apply_seat_overrides,
    load_seat_overrides,
)

ELECTION = "2026 US Senate Election"
IDS = {"democrat": 20, "republican": 21, "others": 15}


class _Party:
    def __init__(self, id: int) -> None:
        self.id = id


class _Vote:
    def __init__(self, seat_id: int, total: float, party_id: int | None, name: str) -> None:
        self.seat_id = seat_id
        self.vote_total = total
        self.party = _Party(party_id) if party_id is not None else None
        self.elected = False
        self.candidate_name = name


def _alaska_payload(*, called: bool = False, sullivan: int = 102325) -> dict[str, Any]:
    """The importer's merged Alaska rows, built by the real payload builder."""
    seats = [
        SeatRow(1, "Alaska", 228, None, None),
        SeatRow(2, "Texas", 229, None, None),
    ]
    votes = [
        _Vote(1, sullivan, 21, "Dan Sullivan"),
        _Vote(1, 4846, 21, "Gerald Heikes"),
        _Vote(1, 102962, 20, "Mary Peltola"),
        _Vote(1, 3600, 15, "Daniel Sullivan"),
        _Vote(2, 600, 21, "Ted"),
        _Vote(2, 400, 20, "Beto"),
    ]
    statuses = {
        1: SeatStatus(seat_id=1, called=called, pct_counted=74.09),
        2: SeatStatus(seat_id=2, called=False, pct_counted=50.0),
    }
    payload: dict[str, Any] = build_result_payload(seats, votes, statuses=statuses)
    return payload


def _seat(payload: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in payload["seats"] if s["n"] == name)


def test_committed_file_loads_and_matches_the_snapshot_candidates() -> None:
    overrides = load_seat_overrides()
    rows = overrides[ELECTION]["Alaska"]["p"]
    assert [(r[0], r[1], r[2]) for r in rows] == [
        ("democrat", 102962, "Mary Peltola"),
        ("republican", 102325, "Dan Sullivan"),
        ("republican", 4846, "Gerald Heikes"),
        ("others", 3600, "Daniel Sullivan"),
    ]
    assert overrides[ELECTION]["Alaska"]["w"] == "democrat"
    assert OVERRIDES_PATH.name == "seat_overrides.json"


def test_applies_to_alaska_2026_only(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _alaska_payload()
    texas_before = json.dumps(_seat(payload, "Texas"))
    merged = _seat(payload, "Alaska")
    assert merged["w"] == 21 and merged["p"][0] == [21, 107171, "Dan Sullivan"]

    applied = apply_seat_overrides(payload, ELECTION, IDS, load_seat_overrides())

    assert applied == ["Alaska"]
    alaska = _seat(payload, "Alaska")
    assert alaska == {
        "n": "Alaska", "r": 228, "w": 20,
        "p": [[20, 102962, "Mary Peltola"], [21, 102325, "Dan Sullivan"],
              [21, 4846, "Gerald Heikes"], [15, 3600, "Daniel Sullivan"]],
        "c": False, "pc": 74.1,
    }
    assert list(alaska) == ["n", "r", "w", "p", "c", "pc"]
    assert json.dumps(_seat(payload, "Texas")) == texas_before
    assert capsys.readouterr().out == ""


def test_party_totals_from_the_four_rows_equal_the_merged_totals() -> None:
    payload = _alaska_payload()
    merged: dict[int, int] = {}
    for pid, votes, *_ in _seat(payload, "Alaska")["p"]:
        merged[pid] = merged.get(pid, 0) + votes
    apply_seat_overrides(payload, ELECTION, IDS, load_seat_overrides())
    split: dict[int, int] = {}
    for pid, votes, *_ in _seat(payload, "Alaska")["p"]:
        split[pid] = split.get(pid, 0) + votes
    assert split == merged == {21: 107171, 20: 102962, 15: 3600}


def test_other_election_names_are_untouched() -> None:
    for name in ("2024 US Senate Election", "2026 US Senate Election ", "2026 US House Election"):
        payload = _alaska_payload()
        before = json.dumps(payload)
        assert apply_seat_overrides(payload, name, IDS, load_seat_overrides()) == []
        assert json.dumps(payload) == before


def test_stale_totals_skip_with_a_warning(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _alaska_payload(sullivan=102400)  # live count moved since the override was written
    before = json.dumps(payload)
    assert apply_seat_overrides(payload, ELECTION, IDS, load_seat_overrides()) == []
    assert json.dumps(payload) == before
    out = capsys.readouterr().out
    assert "WARNING" in out and "stale" in out and "Alaska" in out and "seat_overrides.json" in out


def test_called_seat_is_skipped_with_a_warning(capsys: pytest.CaptureFixture[str]) -> None:
    payload = _alaska_payload(called=True)
    before = json.dumps(payload)
    assert apply_seat_overrides(payload, ELECTION, IDS, load_seat_overrides()) == []
    assert json.dumps(payload) == before
    assert "no longer uncalled" in capsys.readouterr().out


def test_missing_seat_and_unknown_party_key_warn_and_never_raise(capsys: pytest.CaptureFixture[str]) -> None:
    overrides = load_seat_overrides()
    payload = {"schema": "pf-results-v4", "seats": []}
    assert apply_seat_overrides(payload, ELECTION, IDS, overrides) == []
    assert "not in the payload" in capsys.readouterr().out
    payload = _alaska_payload()
    assert apply_seat_overrides(payload, ELECTION, {"democrat": 20}, overrides) == []
    assert "unknown party key" in capsys.readouterr().out


def test_applying_twice_is_idempotent_for_the_totals() -> None:
    payload = _alaska_payload()
    overrides = load_seat_overrides()
    apply_seat_overrides(payload, ELECTION, IDS, overrides)
    first = json.dumps(payload)
    assert apply_seat_overrides(payload, ELECTION, IDS, overrides) == ["Alaska"]
    assert json.dumps(payload) == first


GOOD_ROWS = [["democrat", 10, "A"], ["republican", 5, "B"]]


@pytest.mark.parametrize(
    "content, message",
    [
        ("not json", "cannot read"),
        ("[]", "keyed by election name"),
        ('{"E": []}', "keyed by seat name"),
        ('{"E": {"S": {"p": []}}}', 'exactly the keys "w" and "p"'),
        (json.dumps({"E": {"S": {"w": "democrat", "p": [["democrat", 10, "A"]]}}}), "at least two"),
        (json.dumps({"E": {"S": {"w": "democrat", "p": [["democrat", 10], ["republican", 5, "B"]]}}}), "[partyKey, votes, name]"),
        (json.dumps({"E": {"S": {"w": "democrat", "p": [["democrat", -1, "A"], ["republican", 5, "B"]]}}}), "positive number"),
        (json.dumps({"E": {"S": {"w": "democrat", "p": [["democrat", 10, ""], ["republican", 5, "B"]]}}}), "candidate name"),
        (json.dumps({"E": {"S": {"w": "democrat", "p": [["republican", 5, "B"], ["democrat", 10, "A"]]}}}), "descending"),
        (json.dumps({"E": {"S": {"w": "green", "p": GOOD_ROWS}}}), '"w" must be'),
        (json.dumps({"E": {"S": {"w": "republican", "p": GOOD_ROWS}}}), "top \\(first\\) row"),
        (json.dumps({"E": {"S": {"w": "democrat", "p": [["democrat", 5, "A"], ["republican", 5, "B"]]}}}), "tie on votes"),
    ],
)
def test_bad_file_raises_a_clear_error(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "o.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(SeatOverridesError, match=message.replace("[", r"\[").replace("]", r"\]")):
        load_seat_overrides(path)


def test_missing_file_raises_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(SeatOverridesError, match="cannot read"):
        load_seat_overrides(tmp_path / "nope.json")
