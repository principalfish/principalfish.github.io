"""Home page route."""

from __future__ import annotations

from flask import Blueprint, render_template

from console.nav import section_items
from console.sections import SECTIONS

bp = Blueprint("home", __name__)


@bp.route("/")
def home() -> str:
    """GET / — Render the home page: links to every section, All polls and Admin."""
    cards = [(section, section_items(section)) for section in SECTIONS]
    return render_template("home.html", cards=cards)
