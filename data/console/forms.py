"""Pydantic form models for validating console POST request data."""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator

from polls.importers.us.us_wikipedia_polls import US_CONTESTS, normalise_states


class ModelRunForm(BaseModel):
    """Validated form data for POST /models/run."""

    map_name: str
    baseline_election_name: str
    as_of_days_back: int = Field(ge=0)
    since_days_back: int = Field(ge=0)
    half_life_days: float = Field(gt=0)
    output_csv: str = ""
    dry_run: bool = False

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
    """Validated form data for POST /holyrood/run-model."""

    election_name: str
    as_of_days_back: int = Field(ge=0)
    since_days_back: int = Field(ge=0)
    half_life_days: float = Field(gt=0)
    dry_run: bool = False

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


class PollImportForm(BaseModel):
    """Validated form data for POST /import/preview."""

    pollster_identifier: str
    source_url: str


class WikipediaQueueStartForm(BaseModel):
    """Validated form data for POST /import/wikipedia/start."""

    # ISO date; blank derives the cutoff from the latest poll already stored.
    cutoff_date: str = ""
    run_model_at_end: bool = True


# State names hold spaces ("New York"), so the filter is split on commas,
# semicolons and line breaks only.
_STATE_SEPARATOR_RE = re.compile(r"[,;\n]+")


class UsQueueStartForm(BaseModel):
    """Validated form data for POST /us/import/start.

    Attributes:
        contests: Contest slugs to scrape, at least one, in ``US_CONTESTS``
            order with repeats removed.
        states: Canonical state names limiting the per-state race pages. The
            form takes one comma-separated field of names or postal codes;
            empty means every state.
        cutoff_date: Earliest fieldwork end date to consider in every race.
            None windows each race on its own latest stored poll.
        run_model_at_end: Run the US models and the export when the queue is
            finished.
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
    def check_contests(cls, value: list[str]) -> list[str]:
        """Require at least one known contest and order them canonically.

        Raises:
            ValueError: If no contest is chosen or a slug is unknown.
        """
        known = [contest.slug for contest in US_CONTESTS]
        unknown = [slug for slug in value if slug not in known]
        if unknown:
            raise ValueError(f"unknown contest(s): {', '.join(unknown)}")
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
    """Validated form data for POST /by-elections/preview."""

    source_url: str
    parent_election: str = ""
