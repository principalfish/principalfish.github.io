"""One route factory for every section's model-outputs pages.

Westminster, Holyrood and each US chamber list, inspect and delete their
model-output elections through the same four routes, differing only in URL
prefix, endpoint names, election type and wording. A section describes itself
with an :class:`OutputsSpec` and :func:`register_outputs_routes` adds the
routes to its blueprint. The shared ``model_outputs.html`` and
``model_output_detail.html`` templates build URLs from the endpoint names the
routes pass in, so each spec carries its own set.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from console.sections import SECTIONS_BY_SLUG, Capability
from console.shared.model_outputs import (
    build_output_detail_context,
    build_outputs_context,
    delete_model_output,
    delete_selected_model_outputs,
)
from core.db import Database
from core.schema import ElectionType


@dataclass(frozen=True)
class OutputsSpec:
    """How one section's model-outputs pages are wired.

    ``get_db`` and ``trend_cache_path`` are callables so the owning module can
    look its own globals up at request time rather than binding them at import.

    Attributes:
        url_prefix: URL stem of the section's outputs pages (``"/holyrood"``
            serves ``/holyrood/outputs``).
        list_endpoint: Bare endpoint name of the list page.
        detail_endpoint: Bare endpoint name of the detail page.
        delete_endpoint: Bare endpoint name of the single-delete route.
        delete_selected_endpoint: Bare endpoint name of the bulk-delete route.
        election_type: The model-output election type the section lists.
        baseline_types: Real-election types eligible as the seat-level
            baseline on the detail page.
        heading: Heading of the list page.
        message_prefix: Leading words of flash messages (``""``,
            ``"Holyrood "``, ``"US House "``), placed before "model output".
        section_slug: Slug in :data:`console.sections.SECTIONS_BY_SLUG` whose
            uncertainty capability controls the detail page's links.
        trend_cache_path: Returns the shipped trend JSON for the list charts.
        get_db: Returns the database handle.
    """

    url_prefix: str
    list_endpoint: str
    detail_endpoint: str
    delete_endpoint: str
    delete_selected_endpoint: str
    election_type: ElectionType
    baseline_types: tuple[ElectionType, ...]
    heading: str
    message_prefix: str
    section_slug: str
    trend_cache_path: Callable[[], Path]
    get_db: Callable[[], Database]


def register_outputs_routes(bp: Blueprint, spec: OutputsSpec) -> None:
    """Add the list, detail, delete and bulk-delete routes to a blueprint.

    Args:
        bp: Blueprint to register on; its name prefixes the endpoint strings handed to
            ``url_for`` and the templates.
        spec: The section's wiring.
    """
    outputs_endpoint = f"{bp.name}.{spec.list_endpoint}"
    detail_endpoint = f"{bp.name}.{spec.detail_endpoint}"
    delete_endpoint = f"{bp.name}.{spec.delete_endpoint}"
    delete_selected_endpoint = f"{bp.name}.{spec.delete_selected_endpoint}"
    noun = f"{spec.message_prefix}model output"

    def outputs() -> str:
        """GET list — the section's model outputs with trend chart data.

        Query parameters:
            show (str, optional): 'all' for every output; else the 30 most recent.
        """
        show_all = (request.args.get("show") or "").strip().lower() == "all"
        context = build_outputs_context(
            spec.get_db(),
            election_type=spec.election_type,
            trend_cache_path=spec.trend_cache_path(),
            show_all=show_all,
        )
        return render_template(
            "model_outputs.html",
            **context,
            heading=spec.heading,
            outputs_endpoint=outputs_endpoint,
            detail_endpoint=detail_endpoint,
            delete_endpoint=delete_endpoint,
            delete_selected_endpoint=delete_selected_endpoint,
        )

    def output_detail(election_id: int) -> str | Response:
        """GET detail — one output's seat and vote breakdown.

        Query parameters:
            page (int, optional): Page of the seat list (default 1, 50 per page).
        """
        page = request.args.get("page", default=1, type=int) or 1
        context = build_output_detail_context(
            spec.get_db(),
            election_id=election_id,
            election_type=spec.election_type,
            baseline_types=list(spec.baseline_types),
            page=page,
        )
        if context is None:
            flash(f"{noun[:1].upper()}{noun[1:]} #{election_id} not found.")
            return redirect(url_for(outputs_endpoint))
        return render_template(
            "model_output_detail.html",
            **context,
            show_uncertainty_links=SECTIONS_BY_SLUG[spec.section_slug].has(
                Capability.UNCERTAINTY
            ),
            outputs_endpoint=outputs_endpoint,
            detail_endpoint=detail_endpoint,
        )

    def delete_output(election_id: int) -> Response:
        """POST delete — remove one output and its votes."""
        deleted_votes = delete_model_output(
            spec.get_db(), election_id=election_id, election_type=spec.election_type
        )
        if deleted_votes is None:
            flash(f"{noun[:1].upper()}{noun[1:]} #{election_id} not found.")
        else:
            flash(f"Deleted {noun} #{election_id} and {deleted_votes} vote rows.")
        return redirect(url_for(outputs_endpoint))

    def delete_selected() -> Response:
        """POST bulk delete.

        Form parameters:
            election_ids (list[str]): Election IDs to delete; non-numeric
                values are ignored.
        """
        election_ids: list[int] = []
        for value in request.form.getlist("election_ids"):
            try:
                election_ids.append(int(value))
            except ValueError:
                continue

        if not election_ids:
            flash("No model outputs selected.")
            return redirect(url_for(outputs_endpoint))

        deleted_elections, deleted_votes = delete_selected_model_outputs(
            db=spec.get_db(),
            election_ids=election_ids,
            election_type=spec.election_type,
        )
        flash(
            f"Deleted {deleted_elections} model outputs and {deleted_votes} vote rows."
        )
        return redirect(url_for(outputs_endpoint))

    prefix = spec.url_prefix
    bp.add_url_rule(
        f"{prefix}/outputs",
        endpoint=spec.list_endpoint,
        view_func=outputs,
        methods=["GET"],
    )
    bp.add_url_rule(
        f"{prefix}/outputs/<int:election_id>",
        endpoint=spec.detail_endpoint,
        view_func=output_detail,
        methods=["GET"],
    )
    bp.add_url_rule(
        f"{prefix}/outputs/<int:election_id>/delete",
        endpoint=spec.delete_endpoint,
        view_func=delete_output,
        methods=["POST"],
    )
    bp.add_url_rule(
        f"{prefix}/outputs/delete-selected",
        endpoint=spec.delete_selected_endpoint,
        view_func=delete_selected,
        methods=["POST"],
    )
