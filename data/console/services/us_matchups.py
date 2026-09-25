"""Tracked-matchup choices for the US console's matchup pages.

Two pages decide which of a race's stored matchups the US models follow:

- **the national presidential matchup** — one head-to-head the President model
  and every statewide presidential poll follow (:func:`set_national_matchup`);
- **per-race overrides** for Senate and House races, whose tracked matchup the
  importer otherwise sets to each race's lead table
  (:func:`apply_race_matchup_action`).

Both only ever accept a label that some stored poll already carries, so a typo
or a stale form cannot point a race at a matchup with no polls. Refusals are
raised as :class:`MatchupChoiceError` for the routes to flash. Clearing or
resetting a choice that is not there is not a refusal: both pages say there
was nothing to do. The module is Flask-free, like the other console services.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeGuard, get_args

from db import Database, MatchupSummary
from models import TrackedMatchup, TrackedMatchupSource
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


def matchup_in_force(tracked: TrackedMatchup | None) -> TypeGuard[TrackedMatchup]:
    """Whether the models will follow this race's tracked matchup.

    A missing ``tracked_matchups`` row and a row whose ``matchup`` is NULL
    (the deliberate *ignore this race* marker) both mean no.
    """
    return tracked is not None and tracked.matchup is not None


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
        return "No national matchup was set, so there was nothing to clear."

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
            lead-table choice again (see :func:`_reset_to_automatic`).
        matchup: The label for ``"set"``; ignored by the other actions.

    Returns:
        A confirmation message, including for ``"auto"`` on a race with no
        tracked row, which has nothing to reset.

    Raises:
        MatchupChoiceError: On an unknown action, a label not stored for this
            seat on this map, or a seat that is not on the map.
    """
    if action not in RACE_MATCHUP_ACTIONS:
        raise MatchupChoiceError(f"Unknown matchup action {action!r}.")

    if action == "auto":
        return _reset_to_automatic(db, map_id, seat_id)

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


def _reset_to_automatic(db: Database, map_id: int, seat_id: int) -> str:
    """Drop a race's override so it follows the importer's lead table again.

    A race the importer has already judged goes back to its ``auto_matchup``.
    A race it never judged has no ``auto_matchup``, and copying that across
    would store ``(NULL, "auto")`` — a race silently ignored while it looks
    automatic. Its row is deleted instead: the race shows as not tracked,
    which the per-seat models treat the same way (its polls are unused), until
    an import finds its lead table with polls stored and tracks it.

    The read and the write are separate statements. An import storing the
    race's first ``auto_matchup`` in between would be deleted with the row,
    and restored by the next import; the console and an import are not run
    against each other in practice.

    Args:
        db: Active Database instance.
        map_id: Primary key of the race's map.
        seat_id: Primary key of the race's seat.

    Returns:
        A message saying what happened, or that there was nothing to reset.

    Raises:
        MatchupChoiceError: If the seat does not exist or is on another map,
            as for the other actions — "nothing to reset" would hide that.
    """
    seat = db.get_seat(seat_id)
    if seat is None:
        raise MatchupChoiceError(f"seat {seat_id} does not exist")
    if seat.map_id != map_id:
        raise MatchupChoiceError(
            f"seat {seat_id} belongs to map {seat.map_id}, not map {map_id}"
        )

    tracked = db.get_tracked_matchup(map_id, seat_id)
    if tracked is None:
        return "The race is not tracked, so there was nothing to reset."
    if tracked.auto_matchup is None:
        db.delete_tracked_matchup(map_id, seat_id)
        return (
            "The importer has not chosen a matchup for this race yet, so it is now"
            " not tracked and its polls are unused until an import tracks its lead"
            " matchup."
        )
    db.clear_tracked_matchup_override(map_id, seat_id)
    return "The race now follows its automatic matchup."
