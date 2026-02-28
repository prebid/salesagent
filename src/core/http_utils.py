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
