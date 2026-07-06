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


def normalize_adcp_auth_token(raw: str) -> str:
    """Normalize a raw x-adcp-auth header value to the bare token.

    Tolerates clients that put "Bearer <token>" inside x-adcp-auth (some
    runners reuse one credential string for both header styles) and
    padded/newline-carrying values — verbatim use failed the DB lookup as
    "invalid for tenant 'any'".

    Semantics: trim, case-insensitive "bearer " prefix strip, re-trim.
    May return an empty string (e.g. for "Bearer " with no token) — callers
    treat that as no token.
    """
    token = raw.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token
