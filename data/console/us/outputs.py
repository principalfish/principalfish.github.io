"""US model-output pages, one set of routes per chamber.

The shared templates build URLs from bare endpoint names, so each chamber in
:data:`console.us.models.US_CHAMBERS` gets its own endpoints
(``us.house_outputs``, ``us.delete_house_output``, ...).
"""

from __future__ import annotations

from flask import Blueprint

from console.db import get_db
from console.shared.outputs_routes import OutputsSpec, register_outputs_routes
from console.us.models import US_CHAMBERS, UsChamber


def outputs_spec(chamber: UsChamber) -> OutputsSpec:
    """Build the outputs wiring for one US chamber.

    Args:
        chamber: The chamber whose forecast outputs are listed.

    Returns:
        The spec, with endpoints named after the chamber's slug.
    """
    return OutputsSpec(
        url_prefix=f"/{chamber.slug}",
        list_endpoint=f"{chamber.slug}_outputs",
        detail_endpoint=f"{chamber.slug}_output_detail",
        delete_endpoint=f"delete_{chamber.slug}_output",
        delete_selected_endpoint=f"delete_selected_{chamber.slug}_outputs",
        election_type=chamber.model_type,
        baseline_types=(chamber.baseline_type,),
        heading=f"{chamber.label} Model Outputs",
        message_prefix=f"{chamber.label} ",
        section_slug=chamber.slug,
        trend_cache_path=lambda: chamber.trend_cache_path,
        get_db=lambda: get_db(),
    )


def register_outputs(bp: Blueprint) -> None:
    """Register the outputs routes of every US chamber on the US blueprint."""
    for chamber in US_CHAMBERS:
        register_outputs_routes(bp, outputs_spec(chamber))
