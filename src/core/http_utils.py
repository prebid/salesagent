from adcp.types import AuthenticationScheme

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
    gaps and is the single canonical implementation used by every
    ``Authorization: Bearer`` parser in the codebase
    (``auth.py``, ``auth_middleware.py``, ``resolved_identity.py``).

    Args:
        authorization_header: Raw ``Authorization`` header value (may be empty).

    Returns:
        The token string if the header has the form ``Bearer <token>``
        (case-insensitive), otherwise ``None``.
    """
    # Compared against the pinned enum member, never a string literal: a
    # mistyped member fails to resolve, while a mistyped literal silently takes
    # the wrong branch (the reason
    # ``test_architecture_enum_not_compared_to_string`` forbids the literal
    # form). ``.lower()`` on both sides keeps the RFC 7235 §2.1
    # case-insensitivity this helper exists to provide.
    parts = authorization_header.strip().split(None, 1)
    if len(parts) == 2 and parts[0].lower() == AuthenticationScheme.Bearer.value.lower():
        token = parts[1].strip()
        return token if token else None
    return None
