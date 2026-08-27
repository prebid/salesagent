"""HTTP utility functions shared across the codebase."""

from collections.abc import Mapping
from typing import Any


def headers_from_asgi_scope(scope: Mapping[str, Any]) -> dict[str, str]:
    """Decode an ASGI scope's raw header list into a lowercase-keyed dict.

    ASGI carries headers as ``list[tuple[bytes, bytes]]`` with latin-1 encoding and
    no case normalization. Every pure-ASGI middleware needs the same decode, so it
    lives here once rather than being re-derived per middleware — two copies is two
    chances to disagree on the encoding or the casing rule.
    """
    return {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in scope.get("headers", [])}


def path_from_asgi_scope(scope: Mapping[str, Any]) -> str:
    """The request path with any ASGI ``root_path`` mount prefix stripped.

    A sub-mounted app sees ``path`` still carrying the mount prefix, so anything
    matching a path against a route table or a surface allowlist has to strip it
    first. Two copies of that rule is two chances to disagree about the empty-path
    edge, which is why the signing middleware's surface allowlist and the operation
    resolver's route lookup both call this one.
    """
    path = str(scope.get("path", ""))
    root_path = str(scope.get("root_path") or "")
    if root_path and path.startswith(root_path):
        path = path[len(root_path) :] or "/"
    return path


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
