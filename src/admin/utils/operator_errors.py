"""Operator-safe error answers for the admin Flask app.

:func:`register_error_handler` installs the app-wide answer for any exception a
route lets escape, so routes do not catch exceptions to build error responses.
An expected :class:`~src.core.operator_errors.OperatorRefusal` is answered with
its class's ``MESSAGE``; a :class:`~src.core.operator_errors.RecordedFailure`
with the text it already carries; anything else with the fixed text and log
reference of :func:`src.core.operator_errors.safe_error_message`.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from urllib.parse import urlsplit

from flask import Flask, flash, redirect, render_template, request
from flask.typing import ResponseReturnValue
from werkzeug.exceptions import HTTPException

from src.admin.utils.helpers import caller_reads_json, json_failure
from src.core.operator_errors import OperatorRefusal, RecordedFailure, safe_error_message

__all__ = ["flash_refusal", "register_error_handler"]

logger = logging.getLogger(__name__)


def _same_site_referrer(referrer: str | None, host: str, method: str, full_path: str) -> str | None:
    """The page *referrer* names, as a relative path, when it is on *host* and is not a failed GET of itself.

    *method* and *full_path* are the failed request's. Only the referrer's path and query are
    used, so no scheme or host from the ``Referer`` header reaches ``redirect()``. The path
    must start with exactly one ``/`` and hold no backslash: browsers read ``//host`` and
    ``/\\host`` as another site.
    """
    if not referrer:
        return None
    parsed = urlsplit(referrer)
    if parsed.scheme not in ("http", "https") or parsed.netloc != host:
        return None
    path = parsed.path
    if not path.startswith("/") or path.startswith("//") or "\\" in path or "\\" in parsed.query:
        return None
    # A constant "/" followed by the rest of the path: the result is always a path on
    # this host, whatever the referrer held.
    target = "/" + path[1:] + (f"?{parsed.query}" if parsed.query else "")
    # Going back to the URL that just failed would loop, unless the request was a form
    # post: a form that posts to its own page (create_tenant) is redisplayed by a GET.
    if method == "GET" and target == full_path.rstrip("?"):
        return None
    return target


def _flash_errors(lines: Iterable[str]) -> None:
    for line in lines:
        flash(line, "error")


def flash_refusal(refusal: OperatorRefusal) -> None:
    """Flash *refusal*'s ``MESSAGE`` and its details, for a route that redisplays its own form.

    A route that can keep the operator's input by rendering the form again catches the
    refusal for that alone; everything else is the error handler's answer.
    """
    _flash_errors((refusal.MESSAGE, *refusal.details()))


def _answer(
    message: str, status: int, fields: Mapping[str, object], details: Sequence[str] = ()
) -> ResponseReturnValue:
    """*message* as JSON for API and fetch callers; for a browser, a flash on the page it came from.

    With no same-site page to go back to, the browser gets the error page. *fields* are the
    JSON body's keys beside ``success`` and ``error``; *details* are the lines a browser is
    shown beside *message*.
    """
    if caller_reads_json():
        return json_failure(message, status, fields)
    back = _same_site_referrer(request.referrer, request.host, request.method, request.full_path)
    if back is None:
        _flash_errors(details)
        return render_template("error.html", error_message=message), status
    _flash_errors((message, *details))
    return redirect(back)


def handle_unexpected_error(e: Exception) -> ResponseReturnValue:
    """The admin app's answer to any exception a route lets escape.

    ``HTTPException`` (``abort(404)`` and friends) passes through unchanged, except that
    an error status with no response of its own, sent to a caller that reads JSON, is
    answered as JSON with Werkzeug's status name as its text. An :class:`OperatorRefusal`
    is expected, so it is answered with its class's ``STATUS`` and ``MESSAGE``, beside its
    details, and logged at info. A :class:`RecordedFailure` is already logged and worded, so it is
    answered 500 with its ``operator_message`` and not logged again. Anything else is
    logged with its traceback under a reference by :func:`safe_error_message`, and the
    operator gets fixed text plus that reference with 500.
    """
    if isinstance(e, HTTPException):
        if e.response is None and e.code is not None and e.code >= 400 and caller_reads_json():
            return json_failure(e.name, e.code)
        return e
    if isinstance(e, OperatorRefusal):
        logger.info("Operator refusal: %s", e)
        return _answer(e.MESSAGE, type(e).STATUS, e.response_fields(), e.details())
    if isinstance(e, RecordedFailure):
        return _answer(e.operator_message, 500, e.response_fields())
    return _answer(safe_error_message(e), 500, {})


def register_error_handler(app: Flask) -> None:
    app.register_error_handler(Exception, handle_unexpected_error)
