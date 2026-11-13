"""Holyrood model-output pages, wired through the shared outputs routes."""

from __future__ import annotations

from flask import Blueprint

from console.db import get_db
from console.paths import HOLYROOD_TREND_CACHE_JSON
from console.shared.outputs_routes import OutputsSpec, register_outputs_routes
from core.schema import ElectionType

HOLYROOD_BASELINE_TYPES = (ElectionType.holyrood_general,)

HOLYROOD_OUTPUTS = OutputsSpec(
    url_prefix="/holyrood",
    list_endpoint="holyrood_outputs",
    detail_endpoint="holyrood_output_detail",
    delete_endpoint="delete_holyrood_output",
    delete_selected_endpoint="delete_selected_holyrood_outputs",
    election_type=ElectionType.holyrood_uns,
    baseline_types=HOLYROOD_BASELINE_TYPES,
    heading="Holyrood Model Outputs",
    message_prefix="Holyrood ",
    section_slug="holyrood",
    # Looked up per request, so a patched module global is honoured.
    trend_cache_path=lambda: HOLYROOD_TREND_CACHE_JSON,
    get_db=lambda: get_db(),
)


def register_outputs(bp: Blueprint) -> None:
    """Register the Holyrood outputs routes on its blueprint."""
    register_outputs_routes(bp, HOLYROOD_OUTPUTS)
