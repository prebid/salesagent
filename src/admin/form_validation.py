"""Sanitization and presence checks for Admin UI form posts.

Lives in ``src/admin/`` because a Flask form is an admin-UI concern. It sat in
``src/core/validation.py``, which put a second, name-guessing validation layer
next to the boundary models — and one of the things it grew there was an
``agent_url`` normalizer that decided a protocol authorization outcome. Whatever
the protocol accepts is expressed in the boundary model, the business rules or
the persistence model; nothing here is reachable from a tool implementation.

Scope is deliberately narrow: a ``request.form.to_dict()`` is a flat dict of
strings typed by a human into a browser. Before one reaches a repository it is
trimmed, a blank required field is rejected, and a field that cannot be read is
refused with :class:`~src.core.operator_errors.InvalidFormInput`, which the admin
error handler answers; the refusals every admin form shares live here.
"""

import json
import logging
import math
from decimal import Decimal
from typing import Any

from flask import request

from src.core.helpers.publisher_property_helpers import STALE_SELECTION_MESSAGE
from src.core.operator_errors import FormIssue, InvalidFormInput

logger = logging.getLogger(__name__)

#: The form field that carries a product's or a profile's canonical format declarations.
FORMAT_OPTIONS = "format_options"


def parse_json_field(field: str, raw: str | None, *, blank: Any = None) -> Any:
    """*raw*, the value of the form field *field*, parsed as JSON; *blank* when it is empty.

    A value that is not JSON is refused for *field*, saying where the parser stopped
    (:meth:`FormIssue.invalid_json`), never with the parser's own text.
    """
    if raw is None or not raw.strip():
        return blank
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise InvalidFormInput(FormIssue.invalid_json(field, e)) from e


def json_object_body(field: str = "body") -> dict[str, Any]:
    """The request's body parsed as a JSON object, whatever its content type; ``{}`` when it is empty.

    A body that is not JSON is refused as :func:`parse_json_field` refuses a field, and JSON
    that is not an object (``[1]``, ``"x"``) is refused for *field*, rather than reaching a
    route's ``.get`` as an ``AttributeError``.
    """
    body = parse_json_field(field, request.get_data(as_text=True), blank={})
    if not isinstance(body, dict):
        raise InvalidFormInput(FormIssue(field, f"{field} must be a JSON object"))
    return body


def required_values(data: dict[str, Any], *fields: str) -> list[Any]:
    """The values of *fields* in *data*, a string trimmed, or one refusal naming every blank field.

    A field is blank when it is absent, ``None`` or a string of whitespace.
    """
    values = [value.strip() if isinstance(value, str) else value for value in map(data.get, fields)]
    blank = [field for field, value in zip(fields, values, strict=True) if value is None or value == ""]
    if blank:
        raise InvalidFormInput(*(FormIssue(field, f"{field} is required") for field in blank))
    return values


def form_number[N: (int, float, Decimal)](
    cast: type[N], field: str, raw: str | N, min_val: N | None = None, max_val: N | None = None
) -> N:
    """*raw*, the value of the form field *field*, as *cast* within the bounds, or refused for *field*.

    NaN and infinity are refused like text that is not a number: no operator means either as
    an amount, a rate or an id, and PostgreSQL refuses both in a JSON column.
    """
    try:
        value = cast(raw)
        finite = value.is_finite() if isinstance(value, Decimal) else isinstance(value, int) or math.isfinite(value)
    except (ValueError, TypeError, ArithmeticError):  # Decimal's InvalidOperation is an ArithmeticError
        finite = False
    if not finite:
        raise InvalidFormInput(FormIssue(field, f"{field} must be a number")) from None
    if min_val is not None and value < min_val:
        raise InvalidFormInput(FormIssue(field, f"{field} must be at least {min_val}"))
    if max_val is not None and value > max_val:
        raise InvalidFormInput(FormIssue(field, f"{field} must be at most {max_val}"))
    return value


def format_options_refusal(text: str) -> InvalidFormInput:
    """The refusal of the ``format_options`` field, for *text*."""
    return InvalidFormInput(FormIssue(FORMAT_OPTIONS, text))


def selectors_or_refusal(field: str, selection: tuple[list[dict], str | None]) -> list[dict]:
    """The selectors of a ``(selectors, refusal)`` validator answer, or :class:`InvalidFormInput` for *field*.

    The selection validators (``publisher_property_helpers``) answer with their own refusal
    text; the product and inventory-profile forms refuse with it here, in one place.
    """
    selectors, refusal = selection
    if refusal:
        raise InvalidFormInput(FormIssue(field, refusal))
    return selectors


def refuse_a_stale_selection(selection_save: str, subject: str) -> None:
    """:class:`InvalidFormInput` when the selection the page rendered is no longer the stored one."""
    if selection_save == "stale":
        raise InvalidFormInput(FormIssue("rendered_selection", STALE_SELECTION_MESSAGE.format(subject=subject)))


def validate_form_data(data: dict[str, Any], required_fields: list[str]) -> tuple[bool, list[str]]:
    """Check that every named field is present and not blank.

    Args:
        data: Form data dictionary.
        required_fields: Field names that must carry a non-blank value.

    Returns:
        ``(is_valid, error messages)``.
    """
    errors = [f"{field.title()} is required" for field in required_fields if not data.get(field, "").strip()]
    return (not errors, errors)


def sanitize_json(json_str: str) -> str:
    """Pretty-print a JSON string, or hand it back untouched if it will not parse.

    Returning the raw text on a parse failure is deliberate: the form redisplays
    what the operator typed so they can see and fix it, rather than losing it.
    """
    try:
        return json.dumps(json.loads(json_str), indent=2)
    except json.JSONDecodeError:
        return json_str


def sanitize_url(url: str) -> str:
    """Add the scheme an operator left off and drop a trailing slash.

    A CONVENIENCE for a hand-typed form field, not a normalizer anything compares
    on. Two URLs are the same agent per ``src.core.schemas.canonical_agent_url``
    and nothing else — do not reach for this to decide identity.
    """
    if not url:
        return url

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    return url.rstrip("/")


def sanitize_form_data(data: dict[str, Any]) -> dict[str, Any]:
    """Trim every string field, and tidy the ones a form names as URL or JSON."""
    sanitized: dict[str, Any] = {}

    for key, value in data.items():
        if isinstance(value, str):
            value = value.strip()

            if "url" in key.lower():
                value = sanitize_url(value)
            elif key == "config" or "json" in key.lower():
                value = sanitize_json(value)

        sanitized[key] = value

    return sanitized
