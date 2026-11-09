"""Section landing pages: one per section, listing what the section offers."""

from __future__ import annotations

from flask import Blueprint, render_template

from console.sections import SECTIONS_BY_SLUG

bp = Blueprint("sections", __name__)

# Actions that are not plain links and so are not in the navigation: Holyrood's
# one-click import.
_EXTRAS = {"holyrood": "_holyrood_import_form.html"}


def _landing(slug: str) -> str:
    return render_template(
        "section.html", section=SECTIONS_BY_SLUG[slug], extra=_EXTRAS.get(slug)
    )


@bp.route("/westminster")
def westminster() -> str:
    """GET /westminster — Westminster landing page."""
    return _landing("westminster")


@bp.route("/holyrood")
def holyrood() -> str:
    """GET /holyrood — Holyrood landing page."""
    return _landing("holyrood")


@bp.route("/house")
def house() -> str:
    """GET /house — House landing page."""
    return _landing("house")


@bp.route("/senate")
def senate() -> str:
    """GET /senate — Senate landing page."""
    return _landing("senate")


@bp.route("/president")
def president() -> str:
    """GET /president — President landing page."""
    return _landing("president")
