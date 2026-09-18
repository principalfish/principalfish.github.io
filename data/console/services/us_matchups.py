"""Tracked-matchup choices for the US console's matchup pages.

Two pages decide which of a race's stored matchups the US models follow:

- **the national presidential matchup** — one head-to-head the President model
  and every statewide presidential poll follow (:func:`set_national_matchup`);
- **per-race overrides** for Senate and House races, whose tracked matchup the
  importer otherwise sets to each race's lead table
  (:func:`apply_race_matchup_action`).

Both only ever accept a label that some stored poll already carries, so a typo
or a stale form cannot point a race at a matchup with no polls. Refusals are
raised as :class:`MatchupChoiceError` for the routes to flash. The module is
Flask-free, like the other console services.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, get_args

from db import Database, MatchupSummary
from models import TrackedMatchupSource
from polls.importers.us.us_wikipedia_polls import US_CONTESTS_BY_SLUG

# Chamber slug → the contest whose seat-level polls and automatic lead-table
# tracking the per-race page reviews. The President is absent: its statewide
# polls follow the national matchup, so it has no per-race choice to make.
RACE_CONTEST_BY_CHAMBER: Mapping[str, str] = {
    "senate": "senate_races",
    "house": "house_districts",
}

RaceMatchupAction = Literal["set", "ignore", "auto"]
RACE_MATCHUP_ACTIONS: frozenset[str] = frozenset(get_args(RaceMatchupAction))


class MatchupChoiceError(ValueError):
    """A matchup choice the console refuses; the message is shown to the user."""


@dataclass(frozen=True, slots=True)
class RaceMatchups:
    """One race's tracked matchup and the matchups stored for it.

    Attributes:
        seat_id: Primary key of the race's seat.
        seat_name: The seat's name (a state, or a district such as ``TX-07``).
        is_tracked: Whether the race has a ``tracked_matchups`` row. Without
            one the models ignore the race's polls.
        matchup: The matchup the models follow (the row's effective value);
            None when the race is untracked or deliberately ignored.
        source: ``"auto"`` or ``"manual"``; None when untracked.
        auto_matchup: The importer's latest lead-table choice, kept even while
            a manual override is in force; None if it has never set one.
        labels: The race's stored matchups, most polls first.
    """

    seat_id: int
    seat_name: str
    is_tracked: bool
    matchup: str | None
    source: TrackedMatchupSource | None
    auto_matchup: str | None
    labels: tuple[MatchupSummary, ...]


def race_map_name(chamber_slug: str) -> str | None:
    """Return the map whose races the per-race page lists for a chamber.

    Args:
        chamber_slug: A chamber slug such as ``"senate"``.

    Returns:
        The map name, or None when the chamber has no per-race matchups.
    """
    contest_slug = RACE_CONTEST_BY_CHAMBER.get(chamber_slug)
    if contest_slug is None:
        return None
    return US_CONTESTS_BY_SLUG[contest_slug].map_name


def race_chamber_for_map(db: Database, map_id: int) -> str | None:
    """Return the chamber whose per-race matchups live on ``map_id``.

    Args:
        db: Active Database instance.
        map_id: Primary key of a map.

    Returns:
        The chamber slug, or None when the map is not a per-race map.
    """
    for chamber_slug in RACE_CONTEST_BY_CHAMBER:
        map_name = race_map_name(chamber_slug)
        poll_map = db.get_map_by_name(map_name) if map_name else None
        if poll_map is not None and poll_map.id == map_id:
            return chamber_slug
    return None


def national_matchup_summaries(db: Database, map_id: int) -> list[MatchupSummary]:
    """Return the stored national matchups of a map, most polls first.

    Args:
        db: Active Database instance.
        map_id: Primary key of the map.

    Returns:
        One summary per national matchup label.
    """
    return [
        summary
        for summary in db.get_matchup_summaries(map_id)
        if summary.seat_id is None
    ]


def set_national_matchup(db: Database, map_id: int, matchup: str | None) -> str:
    """Choose the national matchup a map's model follows, as a manual choice.

    Args:
        db: Active Database instance.
        map_id: Primary key of the map.
        matchup: A stored national matchup label, or None to clear the choice
            by deleting the national row (the President model then refuses to
            run until one is chosen).

    Returns:
        A confirmation message.

    Raises:
        MatchupChoiceError: If ``matchup`` is not a stored national label.
    """
    if matchup is None:
        deleted = db.delete_tracked_matchup(map_id, None)
        if deleted:
            return (
                "Cleared the national matchup; the President model will not run"
                " until one is chosen."
            )
        return "No national matchup was set."

    labels = {summary.matchup for summary in national_matchup_summaries(db, map_id)}
    if matchup not in labels:
        raise MatchupChoiceError(f"{matchup!r} is not a stored national matchup.")

    outcome = db.set_tracked_matchup(map_id, None, matchup, source="manual")
    if outcome == "unchanged":
        return f"The national matchup is already {matchup}."
    return f"Now tracking {matchup} nationally."


def build_race_matchups(db: Database, map_id: int) -> list[RaceMatchups]:
    """List every race on a map that has stored matchup polls or a tracked row.

    Args:
        db: Active Database instance.
        map_id: Primary key of the map.

    Returns:
        One :class:`RaceMatchups` per race, ordered by seat name.
    """
    labels_by_seat: dict[int, list[MatchupSummary]] = {}
    for summary in db.get_matchup_summaries(map_id):
        if summary.seat_id is not None:
            labels_by_seat.setdefault(summary.seat_id, []).append(summary)
    tracked_by_seat = {
        tracked.seat_id: tracked
        for tracked in db.get_tracked_matchups_for_map(map_id)
        if tracked.seat_id is not None
    }
    seat_names = {seat.id: seat.seat_name for seat in db.get_seats_for_map(map_id)}

    races: list[RaceMatchups] = []
    for seat_id in labels_by_seat.keys() | tracked_by_seat.keys():
        tracked = tracked_by_seat.get(seat_id)
        races.append(
            RaceMatchups(
                seat_id=seat_id,
                seat_name=seat_names.get(seat_id, f"Seat #{seat_id}"),
                is_tracked=tracked is not None,
                matchup=tracked.matchup if tracked else None,
                source=tracked.source if tracked else None,
                auto_matchup=tracked.auto_matchup if tracked else None,
                labels=tuple(labels_by_seat.get(seat_id, ())),
            )
        )
    races.sort(key=lambda race: (race.seat_name, race.seat_id))
    return races


def apply_race_matchup_action(
    db: Database,
    map_id: int,
    seat_id: int,
    action: str,
    matchup: str | None,
) -> str:
    """Apply one per-race matchup choice.

    Args:
        db: Active Database instance.
        map_id: Primary key of the race's map.
        seat_id: Primary key of the race's seat.
        action: ``"set"`` follows ``matchup`` as a manual override;
            ``"ignore"`` stores a manual NULL so the models skip the race's
            polls; ``"auto"`` drops the override and follows the importer's
            lead-table choice again.
        matchup: The label for ``"set"``; ignored by the other actions.

    Returns:
        A confirmation message.

    Raises:
        MatchupChoiceError: On an unknown action, a label not stored for this
            seat on this map, a seat that is not on the map, or ``"auto"`` for
            a race with no tracked row.
    """
    if action not in RACE_MATCHUP_ACTIONS:
        raise MatchupChoiceError(f"Unknown matchup action {action!r}.")

    if action == "auto":
        if not db.clear_tracked_matchup_override(map_id, seat_id):
            raise MatchupChoiceError("This race has no tracked matchup to reset.")
        return "The race now follows its automatic matchup."

    chosen: str | None = None
    if action == "set":
        labels = {
            summary.matchup
            for summary in db.get_matchup_summaries(map_id)
            if summary.seat_id == seat_id
        }
        if not matchup or matchup not in labels:
            raise MatchupChoiceError(
                f"{matchup!r} is not a matchup stored for this race."
            )
        chosen = matchup

    try:
        db.set_tracked_matchup(map_id, seat_id, chosen, source="manual")
    except ValueError as err:
        raise MatchupChoiceError(str(err)) from err

    if chosen is None:
        return "The race's polls are now ignored."
    return f"The race now tracks {chosen}."
