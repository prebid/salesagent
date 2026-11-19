"""Operator-safe JSON error answers for the admin Flask routes.

The text comes from :func:`src.core.operator_errors.safe_error_message`, which
logs the exception under a reference and returns fixed text plus that reference.
This module adds the Flask response shape around it.
"""

from flask import Response, jsonify

from src.core.operator_errors import OperatorRefusal, safe_error_message

__all__ = ["json_error", "refusal_error", "safe_error_message"]


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
