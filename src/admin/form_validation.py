"""Sanitization and presence checks for Admin UI form posts.

Lives in ``src/admin/`` because a Flask form is an admin-UI concern. It sat in
``src/core/validation.py``, which put a second, name-guessing validation layer
next to the boundary models — and one of the things it grew there was an
``agent_url`` normalizer that decided a protocol authorization outcome. Whatever
the protocol accepts is expressed in the boundary model, the business rules or
the persistence model; nothing here is reachable from a tool implementation.

Scope is deliberately narrow: a ``request.form.to_dict()`` is a flat dict of
strings typed by a human into a browser, and the two things worth doing to one
before it reaches a repository are trimming it and rejecting a blank required
field.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


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
