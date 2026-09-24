"""A same-origin check for the console's state-changing routes (CSRF).

The console is a single-user tool bound to ``127.0.0.1``, but a page on any
other site open in the same browser can still make it POST to
``http://127.0.0.1:5055/…``. Several routes do slow or destructive things on a
POST — restore the database, rebuild every chamber's trend history for hours —
and their only in-page guard is a ``confirm()`` that a forged form never shows.

Instead of per-form tokens (a new dependency, a token in every form, and an
exemption flag on every POST in the tests), :func:`~console.create_app`
registers :func:`same_origin_only` as an app-wide ``before_request`` hook. It
reads the headers a browser stamps on every request, which a hostile page
cannot set. They only mean what they say for the console's own host names, so
``create_app`` also pins ``TRUSTED_HOSTS``; otherwise a hostile domain
re-pointed at 127.0.0.1 (DNS rebinding) would be same-origin with itself.
"""

from __future__ import annotations

from flask import abort, request

# What a browser sends on a request the page's own form made; "none" is a
# request typed in or bookmarked, which a hostile page can't produce.
_SAME_SITE = ("same-origin", "none")


def same_origin_only() -> None:
    """Refuse POSTs another site's page made the browser send.

    Browsers mark every request with ``Sec-Fetch-Site`` (older ones with
    ``Origin`` on a POST); a request with neither isn't from a browser, so it
    can't be forged this way and is let through — curl, and the test client.

    Raises:
        werkzeug.exceptions.Forbidden: For a cross-site POST (a 403).
    """
    if request.method != "POST":
        return
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None:
        if site not in _SAME_SITE:
            abort(403)
        return
    origin = request.headers.get("Origin")
    if origin is not None and origin != request.host_url.rstrip("/"):
        abort(403)
