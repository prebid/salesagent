"""Operator-safe error answers for the admin Flask app.

The text comes from :func:`src.core.operator_errors.safe_error_message`, which
logs the exception under a reference and returns fixed text plus that reference.
:func:`register_error_handler` installs the app-wide answer for any exception a
route lets escape, so routes do not catch ``Exception`` to build error responses.
:func:`json_error` remains for the few places that answer a caught failure
themselves, and :func:`refusal_error` answers an expected
:class:`~src.core.operator_errors.OperatorRefusal` with its ``MESSAGE``.
"""

from urllib.parse import urlsplit

from flask import Flask, Response, flash, jsonify, redirect, render_template, request
from flask.typing import ResponseReturnValue
from werkzeug.exceptions import HTTPException

from src.core.operator_errors import OperatorRefusal, safe_error_message

__all__ = ["json_error", "refusal_error", "register_error_handler", "safe_error_message"]


def json_error(
    e: BaseException, context: str | None = None, status_code: int = 500, **fields: object
) -> tuple[Response, int]:
    """A ``(jsonify(...), status_code)`` answer whose ``error`` is :func:`safe_error_message`.

    ``fields`` carries the rest of the route's error shape (``success=False``,
    ``sync_id=...``) so the admin JavaScript reading it keeps working.
    """
    return jsonify({**fields, "error": safe_error_message(e, context)}), status_code


def refusal_error(refusal: OperatorRefusal, status_code: int = 400, **fields: object) -> tuple[Response, int]:
    """A ``(jsonify(...), status_code)`` answer whose ``error`` is the refusal's ``MESSAGE``."""
    return jsonify({**fields, "error": refusal.MESSAGE}), status_code


def _wants_html() -> bool:
    """A browser navigation or form post; fetch() calls send ``*/*`` and get JSON."""
    if request.is_json:
        return False
    return request.accept_mimetypes.best_match(["application/json", "text/html"]) == "text/html"


def _same_site_referrer() -> str | None:
    """The page the request came from, when it is on this host and is not this URL."""
    referrer = request.referrer
    if not referrer:
        return None
    parsed = urlsplit(referrer)
    if parsed.scheme not in ("http", "https") or parsed.netloc != request.host or referrer == request.url:
        return None
    return referrer


def handle_unexpected_error(e: Exception) -> ResponseReturnValue:
    """The admin app's answer to any exception a route lets escape.

    ``HTTPException`` (``abort(404)`` and friends) passes through unchanged. Anything
    else is logged with its traceback under a reference by :func:`safe_error_message`,
    and the operator gets fixed text plus that reference: as JSON for API and fetch
    callers, otherwise as a flash message on the page they came from (or the error
    page when there is no same-site page to go back to).
    """
    if isinstance(e, HTTPException):
        return e
    message = safe_error_message(e)
    if not _wants_html():
        return jsonify({"success": False, "error": message}), 500
    back = _same_site_referrer()
    if back is None:
        return render_template("error.html", error_message=message), 500
    flash(message, "error")
    return redirect(back)


def register_error_handler(app: Flask) -> None:
    app.register_error_handler(Exception, handle_unexpected_error)
