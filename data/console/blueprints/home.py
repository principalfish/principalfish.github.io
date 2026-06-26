"""Home dashboard route."""

from __future__ import annotations

from flask import Blueprint, render_template
from sqlalchemy import select

from models import Map, Poll, Pollster

from console.db import get_db

bp = Blueprint("home", __name__)


@bp.route("/")
def home() -> str:
    """GET / — Render the home dashboard."""
    db = get_db()
    with db.session() as s:
        holyrood_map_ids = s.execute(
            select(Map.id).where(Map.parliament == "holyrood")
        ).scalars().all()
        latest_constituency = s.execute(
            select(Poll.fieldwork_end)
            .join(Pollster, Poll.pollster_id == Pollster.id)
            .where(Poll.map_id.in_(holyrood_map_ids), ~Pollster.name.ilike("%list%"))
            .order_by(Poll.fieldwork_end.desc())
            .limit(1)
        ).scalar() if holyrood_map_ids else None
        latest_list = s.execute(
            select(Poll.fieldwork_end)
            .join(Pollster, Poll.pollster_id == Pollster.id)
            .where(Poll.map_id.in_(holyrood_map_ids), Pollster.name.ilike("%list%"))
            .order_by(Poll.fieldwork_end.desc())
            .limit(1)
        ).scalar() if holyrood_map_ids else None
    return render_template(
        "home.html",
        holyrood_latest_constituency=latest_constituency,
        holyrood_latest_list=latest_list,
    )
