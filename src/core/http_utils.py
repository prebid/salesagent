"""HTTP utility functions shared across the codebase."""

from collections.abc import Mapping
from typing import Any


def get_header_case_insensitive(headers: Mapping[str, Any], header_name: str) -> str | None:
    """Get a header value with case-insensitive lookup.

    HTTP headers are case-insensitive per RFC 7230, but Python dicts are
    case-sensitive. This helper performs case-insensitive header lookup.

    Args:
        headers: Dictionary of headers
        header_name: Header name to look up (compared case-insensitively)

    Returns:
        Header value if found, None otherwise
    """
    if not headers:
        return None

    header_name_lower = header_name.lower()
    for key, value in headers.items():
        if key.lower() == header_name_lower:
            return value
    return None


def parse_bearer_token(authorization_header: str) -> str | None:
    """Parse a Bearer token from an ``Authorization`` header value.

    Performs a case-insensitive scheme check (RFC 7235 §2.1) and rejects
    scheme-less values.  ``removeprefix("Bearer ")`` is a substring strip,
    not a scheme parse: it silently accepts a scheme-less value and rejects
    the RFC-legal lowercase ``bearer <key>`` form.  This helper closes both
    gaps and is the single canonical implementation used by all four
    ``Authorization: Bearer`` parsers in the codebase
    (``auth.py``, ``auth_middleware.py``, ``resolved_identity.py``,
    ``routes/tmp_providers.py``).

    Args:
        authorization_header: Raw ``Authorization`` header value (may be empty).

    Returns:
        The token string if the header has the form ``Bearer <token>``
        (case-insensitive), otherwise ``None``.
    """
    parts = authorization_header.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1].strip()
        return token if token else None
    return None
