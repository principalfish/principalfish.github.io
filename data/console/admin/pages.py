"""The Admin page: database operations, kept off the section pages."""

from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("admin", __name__)


@bp.route("/admin")
def admin_page() -> str:
    """GET /admin — Rebuild the database, back it up or restore it."""
    return render_template("admin.html")
