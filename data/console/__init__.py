"""Election Data Console — Flask application factory.

Assembles the console web app from per-area blueprints. Run it via the thin
``data/server.py`` entrypoint (``python server.py`` from the ``data/`` dir).
"""

from __future__ import annotations

import os

from flask import Flask


def create_app() -> Flask:
    """Build and configure the console Flask application."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = os.environ.get("POLLS_SECRET_KEY", "local-polls-dev-key")

    from console.blueprints.by_elections import bp as by_elections_bp
    from console.blueprints.db_admin import bp as db_admin_bp
    from console.blueprints.holyrood import bp as holyrood_bp
    from console.blueprints.home import bp as home_bp
    from console.blueprints.poll_import import bp as poll_import_bp
    from console.blueprints.polls import bp as polls_bp
    from console.blueprints.site import bp as site_bp
    from console.blueprints.westminster import bp as westminster_bp

    for blueprint in (
        home_bp,
        poll_import_bp,
        polls_bp,
        westminster_bp,
        holyrood_bp,
        by_elections_bp,
        site_bp,
        db_admin_bp,
    ):
        app.register_blueprint(blueprint)

    return app
