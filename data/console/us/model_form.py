"""The model run form of each US chamber, described as data for ``model_run.html``.

House, Senate and President share one form shape; only the chamber's poll
window default, heading and confirmation text differ. The route in
:mod:`console.us.views` posts it, validates it with
:class:`console.forms.UsModelRunForm` and runs the chamber's model.
"""

from __future__ import annotations

from collections.abc import Mapping

from console.shared.run_form import (
    EXPORT_CHECKBOX_NAME,
    RunForm,
    RunFormCheckbox,
    RunFormField,
    RunFormOption,
    day_options,
    decimal_options,
    export_checkbox,
    half_life_options,
    ticked,
)
from console.us.models import UsChamber

# The runners' own defaults for these two flags, offered pre-selected.
DEFAULT_HALF_LIFE_DAYS = "30.0"
DEFAULT_SEAT_PRIOR_WEIGHT = "1.0"

_POLL_WINDOW_DAYS = (14, 21, 30, 45, 60, 90, 120, 180, 365)
_HALF_LIFE_DAYS = (7.0, 14.0, 21.0, 30.0, 45.0, 60.0, 90.0)
_SEAT_PRIOR_WEIGHTS = (0.0, 0.5, 1.0, 2.0, 5.0, 10.0)

REBUILD_CHECKBOX_NAME = "rebuild_history"
IGNORE_SEAT_POLLS_CHECKBOX_NAME = "ignore_seat_polls"


def rebuild_confirm_message(chamber: UsChamber) -> str:
    """The prompt shown before a rebuild of one chamber's history is submitted."""
    short = chamber.slug.capitalize()
    return (
        f"Rebuild {chamber.label} history? The {short} model recomputes its whole "
        "trend history, then the export runs if Export when done is ticked. "
        "It runs in the background and can take hours; its progress shows on the "
        "run page."
    )


def us_run_form(chamber: UsChamber) -> RunForm:
    """Describe ``chamber``'s model run form.

    The poll window offers the usual choices plus the chamber's own default,
    which is read from :attr:`UsChamber.model_args` rather than repeated here.
    """
    windows = sorted({*_POLL_WINDOW_DAYS, chamber.default_since_days_back})
    dry_run_options = [
        RunFormOption("true", "Yes (preview only — writes nothing, never exports)"),
        RunFormOption("false", "No (write the forecast to the database)"),
    ]
    return RunForm(
        title=f"Run {chamber.label} Model",
        action_endpoint="us.model_run",
        action_values={"chamber": chamber.slug},
        export_heading="Static Data Export",
        fields=[
            RunFormField("since_days_back", "--since-days-back", day_options(windows)),
            RunFormField(
                "half_life_days", "--half-life-days", half_life_options(_HALF_LIFE_DAYS)
            ),
            RunFormField(
                "seat_prior_weight",
                "--seat-prior-weight",
                decimal_options(_SEAT_PRIOR_WEIGHTS),
            ),
            RunFormField("dry_run", "--dry-run", dry_run_options),
        ],
        checkboxes=[
            RunFormCheckbox(
                IGNORE_SEAT_POLLS_CHECKBOX_NAME,
                "Ignore seat polls",
            ),
            RunFormCheckbox(
                REBUILD_CHECKBOX_NAME,
                f"Rebuild {chamber.label} history",
                "Recomputes the whole trend history, not only today's point. "
                "It runs in the background and can take hours; its progress shows "
                "on the run page. "
                "Not available with a dry run.",
            ),
            export_checkbox(),
        ],
        confirm_checkbox=REBUILD_CHECKBOX_NAME,
        confirm_message=rebuild_confirm_message(chamber),
    )


def us_form_defaults(chamber: UsChamber) -> dict[str, str]:
    """The values a freshly opened form shows."""
    return {
        "since_days_back": str(chamber.default_since_days_back),
        "half_life_days": DEFAULT_HALF_LIFE_DAYS,
        "seat_prior_weight": DEFAULT_SEAT_PRIOR_WEIGHT,
        "dry_run": "true",
        IGNORE_SEAT_POLLS_CHECKBOX_NAME: "",
        REBUILD_CHECKBOX_NAME: "",
        EXPORT_CHECKBOX_NAME: "on",
    }


def us_form_values(raw_form: Mapping[str, str]) -> dict[str, str]:
    """The values of a posted form, to draw it again after a run or an error."""
    return {
        "since_days_back": raw_form.get("since_days_back", ""),
        "half_life_days": raw_form.get("half_life_days", ""),
        "seat_prior_weight": raw_form.get("seat_prior_weight", ""),
        "dry_run": raw_form.get("dry_run", "true"),
        **ticked(
            raw_form,
            IGNORE_SEAT_POLLS_CHECKBOX_NAME,
            REBUILD_CHECKBOX_NAME,
            EXPORT_CHECKBOX_NAME,
        ),
    }


def us_arg_explanations() -> list[dict[str, str]]:
    """Help text for each dropdown, keyed by the runner flag it sets."""
    return [
        {
            "flag": "--since-days-back",
            "description": (
                "How many days back from today to include polls from "
                "(the chamber's usual window is pre-selected)."
            ),
        },
        {
            "flag": "--half-life-days",
            "description": (
                "Time-decay half-life for poll weighting. "
                "Lower values favour more recent polls."
            ),
        },
        {
            "flag": "--seat-prior-weight",
            "description": (
                "How much the national swing counts against a seat's own polls. "
                "Higher trusts the national swing more; 0 follows a seat's polls "
                "outright."
            ),
        },
        {
            "flag": "--dry-run",
            "description": (
                "When Yes, computes the forecast without writing to the database "
                "or to disk, and never exports."
            ),
        },
    ]
