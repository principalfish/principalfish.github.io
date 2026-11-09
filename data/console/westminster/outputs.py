"""Westminster model-output pages, wired through the shared outputs routes."""

from __future__ import annotations

from flask import Blueprint

from console.db import get_db
from console.paths import UNS_TREND_CACHE_JSON
from console.shared.outputs_routes import OutputsSpec, register_outputs_routes
from core.schema import ElectionType

WESTMINSTER_BASELINE_TYPES = (ElectionType.uk_general, ElectionType.by_election)

WESTMINSTER_OUTPUTS = OutputsSpec(
    url_prefix="/models",
    list_endpoint="model_outputs",
    detail_endpoint="model_output_detail",
    delete_endpoint="delete_model_output",
    delete_selected_endpoint="delete_selected_model_outputs",
    election_type=ElectionType.model_uns,
    baseline_types=WESTMINSTER_BASELINE_TYPES,
    heading="Westminster Model Outputs",
    message_prefix="",
    section_slug="westminster",
    # Looked up per request, so a patched module global is honoured.
    trend_cache_path=lambda: UNS_TREND_CACHE_JSON,
    get_db=lambda: get_db(),
)


def register_outputs(bp: Blueprint) -> None:
    """Register the Westminster outputs routes on its blueprint."""
    register_outputs_routes(bp, WESTMINSTER_OUTPUTS)
