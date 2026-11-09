"""Plain-data description of a model run form, rendered by ``model_run.html``.

The Westminster and Holyrood model pages share one template; each route builds
a :class:`RunForm` from its dropdown choices and the template loops over the
fields.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True)
class RunFormOption:
    """One ``<option>`` of a select field."""

    value: str
    label: str


@dataclass(frozen=True)
class RunFormField:
    """A labelled ``<select>`` mapped to one model CLI flag."""

    name: str
    flag: str
    options: list[RunFormOption]
    required: bool = True


@dataclass(frozen=True)
class RunForm:
    """Everything the shared template needs to draw a model run form."""

    title: str
    action_endpoint: str
    export_heading: str
    fields: list[RunFormField]


def name_options(names: Iterable[str]) -> list[RunFormOption]:
    """Options whose value and label are the same string."""
    return [RunFormOption(name, name) for name in names]


def election_options(choices: Mapping[str, object]) -> list[RunFormOption]:
    """Options for ``choices['election_options']`` (election name, label)."""
    elections = cast("list[dict[str, str]]", choices["election_options"])
    return [RunFormOption(e["name"], e["label"]) for e in elections]


def day_options(values: Iterable[int]) -> list[RunFormOption]:
    """Options for a day-count dropdown."""
    return [RunFormOption(str(v), str(v)) for v in values]


def half_life_options(values: Iterable[float]) -> list[RunFormOption]:
    """Options for the half-life dropdown, shown to one decimal place."""
    return [RunFormOption(f"{v:.1f}", f"{v:.1f}") for v in values]


def output_csv_options(values: Iterable[str]) -> list[RunFormOption]:
    """Options for the optional CSV path; the empty path is shown as "None"."""
    return [RunFormOption(v, v or "None") for v in values]


def dry_run_options(choices: Mapping[str, object]) -> list[RunFormOption]:
    """Options for ``choices['dry_run_options']`` (value, label)."""
    options = cast("list[dict[str, str]]", choices["dry_run_options"])
    return [RunFormOption(o["value"], o["label"]) for o in options]


def days_field(name: str, flag: str, choices: Mapping[str, object]) -> RunFormField:
    """A day-count field whose options come from ``choices[name]``."""
    return RunFormField(name, flag, day_options(cast("list[int]", choices[name])))


def half_life_field(choices: Mapping[str, object]) -> RunFormField:
    """The half-life field, fed by ``choices['half_life_days']``."""
    values = cast("list[float]", choices["half_life_days"])
    return RunFormField("half_life_days", "--half-life-days", half_life_options(values))


def dry_run_field(choices: Mapping[str, object]) -> RunFormField:
    """The dry-run field, fed by ``choices['dry_run_options']``."""
    return RunFormField("dry_run", "--dry-run", dry_run_options(choices))
