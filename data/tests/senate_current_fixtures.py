"""Hand-made Senate fixtures shared by the Current Senate tests.

The election files use the shape ``importers/results/us/nyt/convert.py`` writes.
"""

from __future__ import annotations

from typing import Any

# Standalone 2026 specials: state -> class of the seat filled.
SPECIALS = {"Florida": 3, "Ohio": 3}


def senate_race(
    party: str | None,
    name: str | None,
    *,
    seat_class: int,
    special: bool = False,
    called: bool = True,
    pct: float | None = 99.5,
) -> dict[str, Any]:
    """One Senate race as a converted election file holds it.

    Args:
        party: The winning party's key (the row that carries ``name``), or
            ``None`` for a race with no winner row.
        name: The winner's name; ``None`` leaves the row unnamed.
        seat_class: Senate class of the seat filled.
        special: A special election.
        called: Whether a winner is called; if not, ``current`` is null.
        pct: Percent of the vote counted, or ``None`` when unknown.
    """
    party_info: dict[str, Any] = {
        "democrat": {"total": 100, "name": "Dana Dem"},
        "republican": {"total": 90, "name": "Rex Rep"},
    }
    if party is not None:
        row: dict[str, Any] = {"total": 120}
        if name is not None:
            row["name"] = name
        party_info[party] = row
    return {
        "seatInfo": {
            "current": party if called else None,
            "called": called,
            "pct": pct,
            "special": special,
            "senateClass": seat_class,
        },
        "partyInfo": party_info,
    }


def senate_election(
    snapshot: dict[str, Any], **overrides: dict[str, Any]
) -> dict[str, Any]:
    """A 2026 Senate file: every state with a class 2 senator, plus the FL/OH specials.

    Winners are Democrats named "Winner <state>" (the specials, Republicans named
    "Special <state>"); ``overrides`` replaces a race by state.
    """
    election: dict[str, Any] = {
        "meta": {"chamber": "senate", "year": 2026, "special_file": None},
    }
    for seat in snapshot["seats"]:
        state = seat["n"]
        if any(member["class"] == 2 for member in seat["members"]):
            election[state] = senate_race(
                "democrat", f"Winner {state}", seat_class=2
            )
        if state in SPECIALS:
            election[state] = senate_race(
                "republican", f"Special {state}", seat_class=3, special=True
            )
    election.update(overrides)
    return election
