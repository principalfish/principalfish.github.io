"""Westminster uncertainty diagnostics: a hub and the calibration report.

Each view is its own route and template, so one can be removed without
touching the others. The hub states what the UNS model has stored
(``model_support.persistence.uncertainty_status``), or that the table has not
been migrated yet. The calibration view reads the committed settings
(``model_support.uncertainty.load_uncertainty_config``) and the report
``scripts/calibrate_uncertainty.py`` writes
(``model_support.calibration.load_calibration_report``); either being absent
or malformed shows a message rather than failing the page.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import Blueprint, render_template
from sqlalchemy import select

from console.blueprints.poll_import import WESTMINSTER_MAP_NAME
from console.db import get_db
from console.services.uncertainty_export import ExportPreview, export_preview
from db import Database
from model_support import calibration as calibration_module
from model_support import uncertainty as uncertainty_module
from model_support.calibration import (
    PARAMETER_NOTES,
    POLLBASE_CREDIT,
    POLLBASE_URL,
    CalibrationError,
    load_calibration_report,
)
from model_support.persistence import (
    UncertaintyStatus,
    UncertaintyStoreError,
    uncertainty_status,
    westminster_uns_scope,
)
from model_support.public_uncertainty import PublicUncertaintyError
from model_support.uncertainty import (
    UncertaintyConfig,
    UncertaintyConfigError,
    load_uncertainty_config,
)
from model_support import westminster_validation
from model_support import westminster_conversion_report
from model_support.westminster_conversion_validation import (
    FOUR_TRANSITION,
    SIX_TRANSITION,
)
from model_support.monte_carlo import config_hash
from models import Map

bp = Blueprint("uncertainty", __name__)


def fmt(value: object, spec: str = ".2f") -> str:
    """Format a report number, or "-" for anything that is not one.

    The report is a file; a hand-edited or truncated value must not stop the
    page rendering.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "-"
    if not math.isfinite(value):
        return "-"
    result = format(value, spec)
    # A value that rounds to zero reads as "0.00", never "-0.00".
    if result.startswith("-") and float(result) == 0:
        return result.removeprefix("-")
    return result


def round_display(value: object) -> object:
    """Round nested diagnostic values for display without changing stored data."""
    if isinstance(value, float):
        return (round(value, 2) or 0.0) if math.isfinite(value) else "-"
    if isinstance(value, dict):
        return {key: round_display(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [round_display(item) for item in value]
    return value


# Registered by call rather than decorator: Flask's decorator is untyped, which
# strict mypy rejects.
bp.add_app_template_filter(round_display, "round_display")


@dataclass(frozen=True, slots=True)
class ParameterRow:
    """One setting as the model uses it.

    Attributes:
        name: The ``UncertaintyConfig`` field.
        value: The value in use.
        unit: Its unit.
        source: calibrated / literature / guess / setting.
        meaning: What it does.
        reported: The value the last calibration wrote (None without a
            report).
        differs: True when the value in use is not the reported one.
        derivation: One line on what the value means and what it was
            computed from (the report's, else built from the notes).
    """

    name: str
    value: object
    unit: str
    source: str
    meaning: str
    reported: object
    differs: bool
    derivation: str


def parameter_rows(
    config: UncertaintyConfig, report: Mapping[str, Any] | None
) -> list[ParameterRow]:
    """Return every setting in use with its notes, reported value and derivation."""
    reported: Mapping[str, object] | None = None if report is None else report["config"]
    derived: dict[str, str] = {}
    if report is not None:
        for row in report["parameters"]:
            name, line = row.get("name"), row.get("derivation")
            if isinstance(name, str) and isinstance(line, str):
                derived[name] = line
    rows: list[ParameterRow] = []
    for name, value in dataclasses.asdict(config).items():
        note = PARAMETER_NOTES[name]
        written = None if reported is None else reported.get(name)
        fallback = f"{note.meaning} ({note.source})"
        if note.source == "calibrated" and report is None:
            fallback += "; no calibration report to show its derivation"
        rows.append(
            ParameterRow(
                name=name,
                value=value,
                unit=note.unit,
                source=note.source,
                meaning=note.meaning,
                reported=written,
                differs=reported is not None and written != value,
                derivation=derived.get(name, fallback),
            )
        )
    return rows


def _residuals(block: object) -> Mapping[str, Any]:
    if isinstance(block, Mapping):
        residuals = block.get("residuals")
        if isinstance(residuals, Mapping):
            return residuals
    return {}


def percentile_rows(
    pairs: Sequence[Mapping[str, Any]], pooled: Mapping[str, Any]
) -> list[PivotRow]:
    """Return each percentile of u across the pairs, then pooled (last cell)."""
    blocks = [*pairs, pooled]
    keys: list[str] = []
    for block in blocks:
        percentiles = _residuals(block).get("percentiles")
        if isinstance(percentiles, Mapping):
            keys.extend(str(key) for key in percentiles if str(key) not in keys)
    rows: list[PivotRow] = []
    for key in keys:
        cells = []
        for block in blocks:
            percentiles = _residuals(block).get("percentiles")
            if isinstance(percentiles, Mapping):
                cells.append(percentiles.get(key))
            else:
                cells.append(None)
        rows.append(PivotRow(label=f"p{key}", cells=tuple(cells)))
    return rows


@dataclass(frozen=True, slots=True)
class HistogramRow:
    """One bin of the u histogram.

    Attributes:
        label: The bin, e.g. "[-1, 0)" or "< -10".
        counts: Count per pair, then pooled.
        share: Pooled count as a percentage of the largest pooled bin (bar
            width).
    """

    label: str
    counts: tuple[int | None, ...]
    share: float


def _int_list(value: object) -> list[int] | None:
    if isinstance(value, list) and all(
        isinstance(item, int) and not isinstance(item, bool) for item in value
    ):
        return value
    return None


def histogram_rows(
    pairs: Sequence[Mapping[str, Any]], pooled: Mapping[str, Any]
) -> list[HistogramRow]:
    """Return the u histogram's bins with per-pair and pooled counts.

    The bins are the pooled block's; a block whose histogram is missing or
    malformed shows None counts. No pooled histogram, no rows.
    """
    pooled_residuals = _residuals(pooled)
    edges = _int_list(pooled_residuals.get("edges"))
    pooled_counts = _int_list(pooled_residuals.get("counts"))
    if not edges or pooled_counts is None or len(pooled_counts) != len(edges) - 1:
        return []
    labels = [f"< {edges[0]}"]
    labels += [f"[{low}, {high})" for low, high in zip(edges, edges[1:], strict=False)]
    labels.append(f"≥ {edges[-1]}")

    def bins(block: Mapping[str, Any]) -> list[int | None]:
        residuals = _residuals(block)
        counts = _int_list(residuals.get("counts"))
        below, above = residuals.get("below"), residuals.get("above")
        if (
            counts is None
            or len(counts) != len(edges) - 1
            or not isinstance(below, int)
            or not isinstance(above, int)
        ):
            return [None] * len(labels)
        return [below, *counts, above]

    columns = [bins(block) for block in [*pairs, pooled]]
    pooled_bins = [count or 0 for count in columns[-1]]
    peak = max(pooled_bins) or 1
    return [
        HistogramRow(
            label=label,
            counts=tuple(column[index] for column in columns),
            share=100 * pooled_bins[index] / peak,
        )
        for index, label in enumerate(labels)
    ]


@dataclass(frozen=True, slots=True)
class PivotRow:
    """One row of a party × pair (or reason × pair) table.

    Attributes:
        label: The party or reason.
        cells: One value per pair, aligned to the pair labels (None where
            the pair has none).
    """

    label: str
    cells: tuple[Any, ...]


def party_lambda_pivot(pairs: Sequence[Mapping[str, Any]]) -> list[PivotRow]:
    """Return each party's per-pair ``{"cells", "lambda"}``, parties by first sight."""
    order: list[str] = []
    by_pair: list[dict[str, Mapping[str, Any]]] = []
    for pair in pairs:
        parties: dict[str, Mapping[str, Any]] = {}
        for party in pair.get("parties", []):
            name = str(party.get("name", party.get("party_id", "?")))
            parties[name] = party
            if name not in order:
                order.append(name)
        by_pair.append(parties)
    return [
        PivotRow(label=name, cells=tuple(parties.get(name) for parties in by_pair))
        for name in order
    ]


def exclusion_pivot(
    pairs: Sequence[Mapping[str, Any]], reasons: Mapping[str, Any]
) -> list[PivotRow]:
    """Return each exclusion reason's per-pair cell count."""
    keys = list(reasons)
    for pair in pairs:
        keys.extend(key for key in pair.get("exclusions", {}) if key not in keys)
    return [
        PivotRow(
            label=str(reasons.get(key, key)),
            cells=tuple(pair.get("exclusions", {}).get(key) for pair in pairs),
        )
        for key in keys
    ]


def status_line(status: UncertaintyStatus | None) -> str:
    """Describe what the UNS model has stored, for the hub.

    Args:
        status: The scope's status, or None when the table is absent.

    Returns:
        One sentence, e.g. "3 runs have stored uncertainty; full detail on:
        UNS 2026-10-14", or "...; no run holds full detail" when none does
        (none stored yet, or the detailed run was deleted).
    """
    if status is None:
        return (
            "Stored uncertainty: table not migrated yet "
            "(run scripts/migrate_add_model_uncertainty.py)."
        )
    count = status.stored_runs
    runs = "1 run has" if count == 1 else f"{count} runs have"
    if status.detail_run is None:
        return f"{runs} stored uncertainty; no run holds full detail"
    return f"{runs} stored uncertainty; full detail on: {status.detail_run.name}"


def published_export(db: Database) -> tuple[ExportPreview | None, str | None]:
    """The hub's "Published export" panel: the preview, or why there is none.

    Returns:
        ``(preview, None)``, or ``(None, message)`` when there is no model run
        or its stored summary cannot be published.
    """
    try:
        preview = export_preview(db)
    except (UncertaintyStoreError, PublicUncertaintyError) as err:
        return None, f"The export would fail on this run's stored summary: {err}"
    if preview is None:
        return None, "No model run to export."
    return preview, None


@bp.route("/westminster/uncertainty")
def uncertainty_hub() -> str:
    """GET /westminster/uncertainty — Stored-run status and each view's link."""
    db = get_db()
    preview, preview_error = published_export(db)
    with db.session() as session:
        map_id = session.execute(
            select(Map.id).where(Map.name == WESTMINSTER_MAP_NAME)
        ).scalar_one_or_none()
    if map_id is None:
        stored = f"No {WESTMINSTER_MAP_NAME} map in the database."
    else:
        status = uncertainty_status(
            Path(db.config.database_path), westminster_uns_scope(map_id)
        )
        stored = status_line(status)
    return render_template(
        "uncertainty_hub.html",
        stored=stored,
        preview=preview,
        preview_error=preview_error,
    )


@bp.route("/westminster/uncertainty/calibration")
def uncertainty_calibration() -> str:
    """GET /westminster/uncertainty/calibration — The calibration report."""
    config_path = uncertainty_module.UNCERTAINTY_CONFIG_PATH
    report_path = calibration_module.CALIBRATION_REPORT_PATH
    config_error: str | None = None
    try:
        config = load_uncertainty_config()
    except UncertaintyConfigError as err:
        config_error = str(err)
        config = UncertaintyConfig()
    report: dict[str, Any] | None = None
    report_error: str | None = None
    if not report_path.exists():
        report_error = f"No calibration report at {report_path}."
    else:
        try:
            report = load_calibration_report()
        except CalibrationError as err:
            report_error = str(err)
    pairs: list[dict[str, Any]] = [] if report is None else report["swing"]["pairs"]
    reasons: Mapping[str, Any] = (
        {} if report is None else report["swing"]["exclusion_reasons"]
    )
    pooled: Mapping[str, Any] = {} if report is None else report["swing"]["pooled"]
    return render_template(
        "uncertainty_calibration.html",
        config_path=config_path,
        config_present=config_path.exists(),
        config_error=config_error,
        p_ref_pct=config.p_ref * 100,
        parameters=parameter_rows(config, report),
        report=report,
        report_error=report_error,
        report_path=report_path,
        pairs=pairs,
        party_lambdas=party_lambda_pivot(pairs),
        percentiles=percentile_rows(pairs, pooled),
        histogram=histogram_rows(pairs, pooled),
        exclusions=exclusion_pivot(pairs, reasons),
        credit_name=POLLBASE_CREDIT,
        credit_url=POLLBASE_URL,
        fmt=fmt,
    )


@bp.route("/westminster/uncertainty/validation")
def uncertainty_validation() -> str:
    """Show saved offline diagnostics without running simulations or reading DB."""
    report = None
    error = None
    stale = False
    try:
        report = westminster_validation.read_report()
        stale = report["identity"].get("config_hash") != config_hash(load_uncertainty_config())
    except (CalibrationError, UncertaintyConfigError) as exc:
        error = str(exc)
    return render_template(
        "uncertainty_validation.html", report=report, error=error,
        stale=stale, fmt=fmt,
    )


def _conversion_validation(contract: str) -> str:
    report = None
    error = None
    stale = False
    missing = False
    try:
        if (contract != FOUR_TRANSITION
                and not westminster_conversion_report.report_path(contract).exists()):
            missing = True
        else:
            report = westminster_conversion_report.read_report(contract=contract)
            stale = report["identity"]["config_hash"] != config_hash(
                load_uncertainty_config(),
            )
    except (CalibrationError, UncertaintyConfigError) as exc:
        error = str(exc)
    return render_template(
        "uncertainty_conversion_validation.html",
        report=report, error=error, stale=stale, fmt=fmt,
        contract=contract, missing=missing,
    )


@bp.route("/westminster/uncertainty/conversion-validation")
def uncertainty_conversion_validation() -> str:
    """Read saved joint-experiment evidence without running the experiment."""
    return _conversion_validation(FOUR_TRANSITION)


@bp.route("/westminster/uncertainty/conversion-validation/six-transition")
def uncertainty_conversion_validation_historical() -> str:
    """The six-transition experiment's saved evidence, if it has been run."""
    return _conversion_validation(SIX_TRANSITION)
