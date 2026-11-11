"""Election Data Console — Flask application factory.

Assembles the console web app from per-area blueprints. Run it via the thin
``data/server.py`` entrypoint (``python server.py`` from the ``data/`` dir).
"""

from __future__ import annotations

import os

from flask import Flask, Response, request

from console.csrf import same_origin_only
from console.nav import nav_context
from console.shared import jobs
from core import backup


def create_app() -> Flask:
    """Build and configure the console Flask application."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    # The secret only signs the flash-message session; there are no CSRF tokens.
    # Every route instead refuses cross-site POSTs (``console.csrf``, which says
    # why), and only the console's own host names are answered, so a hostile
    # domain re-pointed at 127.0.0.1 (DNS rebinding) cannot pass as same-origin.
    # Revisit both the day the console is reachable beyond localhost.
    app.config["SECRET_KEY"] = os.environ.get("POLLS_SECRET_KEY", "local-polls-dev-key")
    app.config["TRUSTED_HOSTS"] = ["127.0.0.1", "localhost"]
    app.before_request(same_origin_only)
    app.context_processor(nav_context)
    app.jinja_env.globals["BUSY_CATEGORY_PREFIX"] = jobs.BUSY_CATEGORY_PREFIX

    from console.admin.db_admin import bp as db_admin_bp
    from console.admin.export import bp as export_bp
    from console.admin.pages import bp as admin_bp
    from console.admin.runs import bp as runs_bp
    from console.admin.site import bp as site_bp
    from console.holyrood.model import bp as holyrood_bp
    from console.home import bp as home_bp
    from console.polls.views import bp as polls_bp
    from console.section_pages import bp as sections_bp
    from console.us.poll_import import bp as us_poll_import_bp
    from console.us.results import bp as us_results_bp
    from console.us.views import bp as us_bp
    from console.westminster.by_elections import bp as by_elections_bp
    from console.westminster.model import bp as westminster_bp
    from console.westminster.poll_import import bp as poll_import_bp
    from console.westminster.uncertainty.explorer import bp as uncertainty_explorer_bp
    from console.westminster.uncertainty.hub import bp as uncertainty_bp
    from console.westminster.uncertainty.runs import bp as uncertainty_runs_bp

    for blueprint in (
        home_bp,
        sections_bp,
        admin_bp,
        export_bp,
        runs_bp,
        poll_import_bp,
        polls_bp,
        westminster_bp,
        uncertainty_bp,
        uncertainty_runs_bp,
        uncertainty_explorer_bp,
        holyrood_bp,
        us_bp,
        us_poll_import_bp,
        us_results_bp,
        by_elections_bp,
        site_bp,
        db_admin_bp,
    ):
        app.register_blueprint(blueprint)

    @app.after_request
    def backup_after_write(response: Response) -> Response:
        """Ask for a backup after any request that could have changed the DB.

        Hooked here rather than called from each route: most writes happen in
        scripts the routes launch as subprocesses, and a new route would
        eventually be added without the call. A write that changed nothing
        gzips to the same bytes as the last archive, so none gets written.
        The db_admin routes back up or restore themselves. A 4xx is a request
        refused before it did anything (the cross-site guard's 403, an
        untrusted host's 400, a bad form), so it asks for none; a 5xx may have
        written part of its change before failing, so it still does.
        """
        if app.config.get("TESTING") or 400 <= response.status_code < 500:
            return response
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and not (
            request.endpoint or ""
        ).startswith("db_admin."):
            backup.request_backup()
        return response

    return app
