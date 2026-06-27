"""Pydantic form models for validating console POST request data."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


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


class PollImportForm(BaseModel):
    """Validated form data for POST /import/preview."""

    pollster_identifier: str
    source_url: str


class ByElectionPreviewForm(BaseModel):
    """Validated form data for POST /by-elections/preview."""

    source_url: str
    parent_election: str = ""
