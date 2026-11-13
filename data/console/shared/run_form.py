"""Plain-data description of a model run form, rendered by ``model_run.html``.

The Westminster, Holyrood and three US model pages share one template; each
route builds a :class:`RunForm` from its dropdown choices and the template loops
over the fields, then the checkboxes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
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
class RunFormCheckbox:
    """A labelled checkbox; the browser sends ``"on"`` when it is ticked.

    Attributes:
        name: Form field name.
        label: Text beside the box.
        help: Smaller explanatory text under the label, if any.
    """

    name: str
    label: str
    help: str = ""


@dataclass(frozen=True)
class RunForm:
    """Everything the shared template needs to draw a model run form.

    Attributes:
        title: Heading of the form.
        action_endpoint: Endpoint the form posts to.
        export_heading: Heading of the export's result block.
        fields: The dropdown fields, in order.
        action_values: URL values for ``action_endpoint``.
        checkboxes: The checkboxes, drawn after the dropdowns.
        confirm_checkbox: Name of the checkbox that asks for confirmation on
            submit, or empty for none.
        confirm_message: The confirmation prompt shown when that checkbox is
            ticked.
    """

    title: str
    action_endpoint: str
    export_heading: str
    fields: list[RunFormField]
    action_values: Mapping[str, str] = field(default_factory=dict)
    checkboxes: list[RunFormCheckbox] = field(default_factory=list)
    confirm_checkbox: str = ""
    confirm_message: str = ""


EXPORT_CHECKBOX_NAME = "export_when_done"


def export_checkbox(extra_help: str = "") -> RunFormCheckbox:
    """The "export when done" checkbox every model run form carries.

    Args:
        extra_help: One more sentence for the help text, for a form whose run
            writes something of its own regardless of this box.
    """
    help_text = (
        "Refreshes the static data files after a successful run. "
        "A dry run never exports, whatever this says."
    )
    if extra_help:
        help_text = f"{help_text} {extra_help}"
    return RunFormCheckbox(EXPORT_CHECKBOX_NAME, "Export when done", help_text)


def ticked(raw_form: Mapping[str, str], *names: str) -> dict[str, str]:
    """Which of the named checkboxes a posted form ticked.

    Args:
        raw_form: The posted form fields.
        *names: Checkbox field names.

    Returns:
        ``"on"`` for each ticked box and ``""`` for each unticked one, the
        values the template reads back to redraw the boxes.
    """
    return {name: "on" if raw_form.get(name) == "on" else "" for name in names}


def run_summary(
    *,
    dry_run: bool,
    export: bool,
    return_code: int,
    export_return_code: int | None = None,
) -> str:
    """One sentence saying what a finished model run did.

    Args:
        dry_run: Whether the run was a dry run.
        export: Whether "export when done" was ticked.
        return_code: The model run's exit code.
        export_return_code: The export step's exit code, or None when no
            export ran.
    """
    if return_code != 0:
        return f"The run failed with exit code {return_code}; see the output below."
    if export_return_code:
        return (
            "The run finished but the export failed with exit code "
            f"{export_return_code}; see the output below."
        )
    if dry_run:
        return (
            "Dry run: the model computed without writing anything, "
            "and nothing was exported."
        )
    if export:
        return "The run finished and the export was requested; its output is below."
    return "The run finished. Export when done was off, so nothing was exported."


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


def decimal_options(values: Iterable[float]) -> list[RunFormOption]:
    """Options for a decimal dropdown, shown to one decimal place."""
    return [RunFormOption(f"{v:.1f}", f"{v:.1f}") for v in values]


def half_life_options(values: Iterable[float]) -> list[RunFormOption]:
    """Options for the half-life dropdown, shown to one decimal place."""
    return decimal_options(values)


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
