"""Pydantic form models for validating console POST request data."""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from console.sections import SECTIONS_BY_SLUG
from importers.polls.us.us_wikipedia_polls import US_CONTESTS, normalise_states
from models.support.cli import validate_half_life, validate_prior_weight

# The kinds of content the site export can be narrowed to, in the order the
# console passes them to the script.
EXPORT_CONTENT_KINDS: tuple[str, ...] = ("results", "forecast", "metadata")


class ModelRunForm(BaseModel):
    """Validated form data for POST /westminster/model."""

    map_name: str
    baseline_election_name: str
    as_of_days_back: int = Field(ge=0)
    since_days_back: int = Field(ge=0)
    half_life_days: float = Field(gt=0)
    output_csv: str = ""
    dry_run: bool = True
    export_when_done: bool = False

    @field_validator("half_life_days")
    @classmethod
    def check_half_life(cls, value: float) -> float:
        """Use the same finite, positive decay parameter as the CLI."""
        return validate_half_life(value)

    @model_validator(mode="after")
    def check_since_gte_as_of(self) -> "ModelRunForm":
        """Validate that since_days_back is not narrower than as_of_days_back.

        Returns:
            The validated ModelRunForm instance.

        Raises:
            ValueError: If since_days_back is less than as_of_days_back.
        """
        if self.since_days_back < self.as_of_days_back:
            raise ValueError("Since-days-back must be >= as-of-days-back")
        return self


class HolyroodModelRunForm(BaseModel):
    """Validated form data for POST /holyrood/model."""

    election_name: str
    as_of_days_back: int = Field(ge=0)
    since_days_back: int = Field(ge=0)
    half_life_days: float = Field(gt=0)
    dry_run: bool = True
    export_when_done: bool = False

    @field_validator("half_life_days")
    @classmethod
    def check_half_life(cls, value: float) -> float:
        """Use the same finite, positive decay parameter as the CLI."""
        return validate_half_life(value)

    @model_validator(mode="after")
    def check_since_gte_as_of(self) -> "HolyroodModelRunForm":
        """Validate that since_days_back is not narrower than as_of_days_back.

        Returns:
            The validated HolyroodModelRunForm instance.

        Raises:
            ValueError: If since_days_back is less than as_of_days_back.
        """
        if self.since_days_back < self.as_of_days_back:
            raise ValueError("Since-days-back must be >= as-of-days-back")
        return self


class UsModelRunForm(BaseModel):
    """Validated form data for POST /<chamber>/model (House, Senate, President).

    A checkbox the browser leaves out of the post is unticked, so the three
    checkbox fields default to False. ``dry_run`` comes from a yes/no dropdown;
    a post that omits it is a dry run, the form's safe default.

    Attributes:
        since_days_back: Poll window in days, at least 1.
        half_life_days: Finite, positive poll decay half-life.
        seat_prior_weight: Finite, non-negative weight of the uniform-swing
            prior against a seat's own polls.
        ignore_seat_polls: Project the pure uniform national swing.
        rebuild_history: Recompute the chamber's whole trend history.
        dry_run: Compute without writing anything.
        export_when_done: Run the static export after a real run. Ignored for a
            dry run, which never exports.
    """

    since_days_back: int = Field(gt=0)
    half_life_days: float = Field(gt=0)
    seat_prior_weight: float = Field(ge=0)
    ignore_seat_polls: bool = False
    rebuild_history: bool = False
    dry_run: bool = True
    export_when_done: bool = False

    @field_validator("half_life_days")
    @classmethod
    def check_half_life(cls, value: float) -> float:
        """Use the same finite, positive decay parameter as the CLI."""
        return validate_half_life(value)

    @field_validator("seat_prior_weight")
    @classmethod
    def check_seat_prior_weight(cls, value: float) -> float:
        """Use the same finite, non-negative prior weight as the CLI."""
        return validate_prior_weight(value)

    @model_validator(mode="after")
    def check_rebuild_is_a_real_run(self) -> "UsModelRunForm":
        """Refuse a rebuild that is also a dry run.

        The runner skips ``--rebuild-history`` in dry-run mode, so the pair
        would run, change nothing and report success.

        Returns:
            The validated UsModelRunForm instance.

        Raises:
            ValueError: If both rebuild_history and dry_run are set.
        """
        if self.rebuild_history and self.dry_run:
            raise ValueError(
                "A dry run writes nothing, so it cannot rebuild history: "
                "untick the rebuild box or set dry run to No"
            )
        return self


class PollImportForm(BaseModel):
    """Validated form data for POST /westminster/polls/import/preview."""

    pollster_identifier: str
    source_url: str


class WikipediaQueueStartForm(BaseModel):
    """Validated form data for POST /westminster/polls/import/wikipedia/start."""

    # ISO date; blank derives the cutoff from the latest poll already stored.
    cutoff_date: str = ""
    run_model_at_end: bool = True


# State names hold spaces ("New York"), so the filter is split on commas,
# semicolons and line breaks only.
_STATE_SEPARATOR_RE = re.compile(r"[,;\n]+")


class UsQueueStartForm(BaseModel):
    """Validated form data for POST /<chamber>/polls/import/start.

    Validate with ``context={"allowed_contests": (...)}`` to restrict the
    contests to those one chamber imports; without a context every known
    contest is accepted.

    Attributes:
        contests: Contest slugs to scrape, at least one, in ``US_CONTESTS``
            order with repeats removed.
        states: Canonical state names limiting the per-state race pages. The
            form takes one comma-separated field of names or postal codes;
            empty means every state.
        cutoff_date: Earliest fieldwork end date to consider in every race.
            None windows each race on its own latest stored poll.
        run_model_at_end: Run the chamber's model and the export when the queue
            is finished.
        include_collapsed_for_uncovered: Import hidden hypothetical tables for
            races that have no visible table.
    """

    contests: list[str]
    states: list[str] = Field(default_factory=list)
    cutoff_date: date | None = None
    run_model_at_end: bool = True
    include_collapsed_for_uncovered: bool = False

    @field_validator("contests")
    @classmethod
    def check_contests(cls, value: list[str], info: ValidationInfo) -> list[str]:
        """Require at least one allowed contest and order them canonically.

        Args:
            value: The posted contest slugs.
            info: Validation info; its context may carry ``allowed_contests``,
                the slugs the chamber being imported offers.

        Raises:
            ValueError: If no contest is chosen, a slug is unknown, or a known
                contest is not one the chamber imports.
        """
        known = [contest.slug for contest in US_CONTESTS]
        unknown = [slug for slug in value if slug not in known]
        if unknown:
            raise ValueError(f"unknown contest(s): {', '.join(unknown)}")
        context = info.context or {}
        allowed = list(context.get("allowed_contests", known))
        elsewhere = [slug for slug in value if slug not in allowed]
        if elsewhere:
            raise ValueError(
                f"contest(s) not part of this section: {', '.join(elsewhere)}"
            )
        if not value:
            raise ValueError("choose at least one contest")
        return [slug for slug in known if slug in value]

    @field_validator("states", mode="before")
    @classmethod
    def split_states(cls, value: object) -> object:
        """Split the free-text state field into its entries."""
        if isinstance(value, str):
            parts = _STATE_SEPARATOR_RE.split(value)
            return [part.strip() for part in parts if part.strip()]
        return value

    @field_validator("states")
    @classmethod
    def check_states(cls, value: list[str]) -> list[str]:
        """Resolve names and postal codes to canonical state names.

        Raises:
            ValueError: If an entry is not a US state or DC.
        """
        names, unknown = normalise_states(value)
        if unknown:
            raise ValueError(f"not a US state: {', '.join(unknown)}")
        return names

    @field_validator("cutoff_date", mode="before")
    @classmethod
    def blank_cutoff_is_none(cls, value: object) -> object:
        """Treat a blank date input as no cutoff."""
        if isinstance(value, str) and not value.strip():
            return None
        return value


class ByElectionPreviewForm(BaseModel):
    """Validated form data for POST /westminster/by-elections/preview."""

    source_url: str
    parent_election: str = ""


class ExportForm(BaseModel):
    """Validated form data for POST /export.

    ``dry_run`` is a real export only when it is exactly ``"false"``; a post
    that omits it is a dry run, the form's safe default, and any value other
    than ``"true"`` or ``"false"`` is refused.

    Attributes:
        sections: Section slugs to export, at least one, each in the registry.
        content: Content kinds to export, at least one, each in
            :data:`EXPORT_CONTENT_KINDS`.
        dry_run: Report what would be written without writing anything.
    """

    sections: list[str]
    content: list[str]
    dry_run: bool = True

    @field_validator("sections")
    @classmethod
    def check_sections(cls, value: list[str]) -> list[str]:
        """Require at least one section, each a known slug."""
        if not value:
            raise ValueError("Choose at least one section")
        unknown = [slug for slug in value if slug not in SECTIONS_BY_SLUG]
        if unknown:
            raise ValueError(f"Unknown section: {', '.join(unknown)}")
        return value

    @field_validator("content")
    @classmethod
    def check_content(cls, value: list[str]) -> list[str]:
        """Require at least one content kind, each a known one."""
        if not value:
            raise ValueError("Choose at least one kind of content")
        unknown = [kind for kind in value if kind not in EXPORT_CONTENT_KINDS]
        if unknown:
            raise ValueError(f"Unknown content: {', '.join(unknown)}")
        return value

    @field_validator("dry_run", mode="before")
    @classmethod
    def check_dry_run(cls, value: object) -> object:
        """Accept only the strings ``true`` and ``false``."""
        if isinstance(value, str):
            if value not in ("true", "false"):
                raise ValueError("Dry run must be true or false")
            return value == "true"
        return value
