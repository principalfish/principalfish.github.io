"""Home dashboard route."""

from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("home", __name__)


@bp.route("/")
def home() -> str:
    """GET / — Render the home dashboard."""
    return render_template("home.html")
