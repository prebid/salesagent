"""Operator-safe error answers for the admin Flask app.

:func:`register_error_handler` installs the app-wide answer for any exception a
route lets escape, so routes do not catch exceptions to build error responses.
An expected :class:`~src.core.operator_errors.OperatorRefusal` is answered with
its class's ``MESSAGE``; anything else with the fixed text and log reference of
:func:`src.core.operator_errors.safe_error_message`. :func:`json_error` remains
for the few places that answer a caught failure themselves.
"""

import logging
from collections.abc import Mapping
from urllib.parse import urlsplit

from flask import Flask, Response, flash, jsonify, redirect, render_template, request
from flask.typing import ResponseReturnValue
from werkzeug.exceptions import HTTPException

from src.admin.utils.helpers import caller_reads_json
from src.core.operator_errors import OperatorRefusal, safe_error_message

__all__ = ["json_error", "register_error_handler", "safe_error_message"]

logger = logging.getLogger(__name__)


def json_error(
    e: BaseException, context: str | None = None, status_code: int = 500, **fields: object
) -> tuple[Response, int]:
    """A ``(jsonify(...), status_code)`` answer whose ``error`` is :func:`safe_error_message`.

    ``fields`` carries the rest of the route's error shape (``success=False``,
    ``sync_id=...``) so the admin JavaScript reading it keeps working.
    """
    return jsonify({**fields, "error": safe_error_message(e, context)}), status_code


def _same_site_referrer() -> str | None:
    """The page the request came from, as a relative path, when it is on this host and is not a failed GET of itself.

    Only the referrer's path and query are used, so no scheme or host from the ``Referer``
    header reaches ``redirect()``. The path must start with exactly one ``/`` and hold no
    backslash: browsers read ``//host`` and ``/\\host`` as another site.
    """
    referrer = request.referrer
    if not referrer:
        return None
    parsed = urlsplit(referrer)
    if parsed.scheme not in ("http", "https") or parsed.netloc != request.host:
        return None
    path = parsed.path
    if not path.startswith("/") or path.startswith("//") or "\\" in path or "\\" in parsed.query:
        return None
    # A constant "/" followed by the rest of the path: the result is always a path on
    # this host, whatever the referrer held.
    target = "/" + path[1:] + (f"?{parsed.query}" if parsed.query else "")
    # Going back to the URL that just failed would loop, unless the request was a form
    # post: a form that posts to its own page (create_tenant) is redisplayed by a GET.
    if request.method == "GET" and target == request.full_path.rstrip("?"):
        return None
    return target


def _answer(message: str, status: int, fields: Mapping[str, object]) -> ResponseReturnValue:
    """*message* as JSON for API and fetch callers; for a browser, a flash on the page it came from.

    With no same-site page to go back to, the browser gets the error page. *fields* are the
    JSON body's keys beside ``success`` and ``error``.
    """
    if caller_reads_json():
        return jsonify({"success": False, "error": message, **fields}), status
    back = _same_site_referrer()
    if back is None:
        return render_template("error.html", error_message=message), status
    flash(message, "error")
    return redirect(back)


def handle_unexpected_error(e: Exception) -> ResponseReturnValue:
    """The admin app's answer to any exception a route lets escape.

    ``HTTPException`` (``abort(404)`` and friends) passes through unchanged. An
    :class:`OperatorRefusal` is expected, so it is answered 400 with its class's
    ``MESSAGE`` and logged at info. Anything else is logged with its traceback under a
    reference by :func:`safe_error_message`, and the operator gets fixed text plus that
    reference with 500.
    """
    if isinstance(e, HTTPException):
        return e
    if isinstance(e, OperatorRefusal):
        logger.info("Operator refusal: %s", e)
        return _answer(e.MESSAGE, 400, e.response_fields())
    return _answer(safe_error_message(e), 500, {})


def register_error_handler(app: Flask) -> None:
    app.register_error_handler(Exception, handle_unexpected_error)
