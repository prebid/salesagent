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


def parse_bearer_authorization(value: str) -> str | None:
    """Extract the bare token from an RFC 6750 ``Authorization`` header value.

    The single primitive for the Bearer scheme parse — every transport routes
    through it so the semantics cannot diverge (whitespace-tolerant, scheme
    case-insensitive per RFC 7235 §2.1).

    Returns None when the value carries no Bearer credential: a different
    scheme, no scheme at all, or an empty token after the scheme.
    """
    value = value.strip()
    if not value.lower().startswith("bearer "):
        return None
    return value[len("bearer ") :].strip() or None


def normalize_adcp_auth_token(raw: str) -> str:
    """Normalize a raw x-adcp-auth header value to the bare token.

    Tolerates clients that put "Bearer <token>" inside x-adcp-auth (some
    runners reuse one credential string for both header styles) and
    padded/newline-carrying values — verbatim use failed the DB lookup as
    "invalid for tenant 'any'".

    Semantics: trim; if the value carries a Bearer credential, return that
    bare token; otherwise return the trimmed value verbatim — so a schemeless
    token passes through, and a credential-less ``"Bearer "`` comes back as
    the literal string ``"Bearer"`` (a token that fails auth loudly rather
    than reading as "no token sent"). May return an empty string for an
    all-whitespace value — callers treat that as no token.
    """
    token = raw.strip()
    return parse_bearer_authorization(token) or token


def extract_auth_token(headers: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Extract the AdCP auth token from request headers (all transports).

    ``x-adcp-auth`` takes priority (AdCP convention), then
    ``Authorization: Bearer`` (RFC 6750, standard HTTP/MCP clients). Header
    lookup is case-insensitive per RFC 7230.

    Returns:
        ``(token, source)`` — source is ``"x-adcp-auth"`` or
        ``"Authorization: Bearer"``; ``(None, None)`` when no usable token.
    """
    raw = get_header_case_insensitive(headers, "x-adcp-auth")
    if raw:
        adcp_token = normalize_adcp_auth_token(raw)
        if adcp_token:
            return adcp_token, "x-adcp-auth"

    authorization = get_header_case_insensitive(headers, "Authorization")
    if authorization:
        bearer_token = parse_bearer_authorization(authorization)
        if bearer_token:
            return bearer_token, "Authorization: Bearer"

    return None, None
