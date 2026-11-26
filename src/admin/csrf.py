"""Cross-site request forgery protection for the admin UI.

The admin UI authenticates browsers with a session cookie, so any request a browser
sends to it carries that cookie, including a form another site posts to it. A
state-changing request is therefore accepted only when it comes from the admin UI's
own origin, judged from the headers browsers attach and pages cannot set:

1. ``Sec-Fetch-Site``, sent by every current browser: ``same-origin``, or ``none``
   (the user typed the address or used a bookmark), passes; ``same-site`` and
   ``cross-site`` are refused. Sibling subdomains are refused too: the session
   cookie can be shared across subdomains, so a page on one of them is not trusted
   to act on this one.
2. Otherwise ``Origin``: it must equal the origin the request was addressed to.
3. A request with neither header is not from a browser (scripts, server-side
   callers, the test client) and is not a forgery vector, so it passes. Browsers
   old enough to send neither on a cross-site post also honour the session
   cookie's ``SameSite=Lax``, which keeps the cookie off such a post.

This is the scheme of Go's ``net/http.CrossOriginProtection``; it needs no token in
forms or ``fetch`` calls. Safe methods (``GET``, ``HEAD``, ``OPTIONS``) are not
checked: they must not change state.

Views authenticated by a header instead of the session cookie (the operator API
keys) are marked with :func:`csrf_exempt`; a forged request cannot set the header.

The ``CSRF_PROTECTION`` config key (default ``True``) turns the check off.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from flask import Flask, abort, current_app, request

logger = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
TRUSTED_FETCH_SITES = frozenset({"same-origin", "none"})
_EXEMPT_ATTRIBUTE = "csrf_exempt"


def csrf_exempt[F: Callable[..., Any]](view: F) -> F:
    """Mark *view* as not authenticated by the session cookie, so not checked.

    Only for views that authenticate every request from a header (an API key). The
    mark survives ``functools.wraps``, so outer decorators keep it.
    """
    setattr(view, _EXEMPT_ATTRIBUTE, True)
    return view


def request_origin() -> str:
    """The origin the request was addressed to, as ``scheme://host[:port]``.

    In production ``ProxyFix`` has already applied ``X-Forwarded-Proto`` and
    ``X-Forwarded-Host``, so this is the public origin the browser used.
    """
    return f"{request.scheme}://{request.host}".lower()


def is_same_origin_request() -> bool:
    """Whether the current request could have come only from the admin UI's own pages."""
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site is not None:
        return fetch_site.strip().lower() in TRUSTED_FETCH_SITES

    origin = request.headers.get("Origin")
    if origin is not None:
        # "null" (sandboxed frames, some redirects) is never this origin.
        return origin.strip().lower() == request_origin()

    return True


def _verify_request() -> None:
    if not current_app.config.get("CSRF_PROTECTION", True):
        return
    if request.method in SAFE_METHODS:
        return
    view = current_app.view_functions.get(request.endpoint) if request.endpoint else None
    if view is not None and getattr(view, _EXEMPT_ATTRIBUTE, False):
        return
    if is_same_origin_request():
        return
    logger.warning(
        "Refused a cross-origin %s %s (Sec-Fetch-Site=%r, Origin=%r)",
        request.method,
        request.path,
        request.headers.get("Sec-Fetch-Site"),
        request.headers.get("Origin"),
    )
    abort(403, description="Cross-origin request refused")


def init_csrf_protection(app: Flask) -> None:
    """Refuse cross-origin state-changing requests to *app*, before any view runs."""
    app.config.setdefault("CSRF_PROTECTION", True)
    app.before_request(_verify_request)
