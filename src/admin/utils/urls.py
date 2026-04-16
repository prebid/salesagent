"""Safe next-URL validation for post-login redirects.

Replaces session["login_next_url"] — URL-carried state survives the
D3 session cookie cutover (Flask `session` → Starlette `adcp_session`).

Validation follows OWASP ASVS V14.6 (Unvalidated Redirect): path-only,
prefix allowlist, decode-before-check, length-capped. Re-run on every
read, never trust that a prior validation is still safe.
"""

from __future__ import annotations

import logging
from urllib.parse import quote, unquote, urlsplit

logger = logging.getLogger(__name__)

_ALLOWED_PREFIXES: tuple[str, ...] = ("/admin/", "/tenant/")
_MAX_LEN = 2048


def safe_next_url(candidate: str | None) -> str | None:
    """Return *candidate* only if it is a safe path-only admin URL, else None.

    Rejects: absolute URLs, protocol-relative (`//evil.com`), backslash
    smuggling, non-admin prefixes (`/api/`, `/mcp/`, `/a2a/`, `/_internal/`),
    encoded path traversal, and URLs longer than 2048 chars.
    """
    if not candidate:
        return None
    if len(candidate) > _MAX_LEN:
        logger.warning("[SECURITY] next URL rejected: length %d > %d", len(candidate), _MAX_LEN)
        return None

    decoded = unquote(candidate).strip()
    if len(decoded) > _MAX_LEN:
        logger.warning("[SECURITY] next URL rejected: decoded length > %d", _MAX_LEN)
        return None

    parts = urlsplit(decoded)
    if parts.scheme or parts.netloc:
        logger.warning("[SECURITY] next URL rejected (scheme/netloc): %r", candidate)
        return None
    if decoded.startswith(("//", "\\\\", "\\")) or "\\" in decoded or not decoded.startswith("/"):
        logger.warning("[SECURITY] next URL rejected (malformed path): %r", candidate)
        return None
    if ".." in decoded.split("?", 1)[0].split("/"):
        logger.warning("[SECURITY] next URL rejected (path traversal): %r", candidate)
        return None
    if not any(decoded.startswith(p) for p in _ALLOWED_PREFIXES):
        logger.warning("[SECURITY] next URL rejected (prefix not allowlisted): %r", candidate)
        return None

    return decoded


def login_url_with_next(login_path: str, current_path: str | None) -> str:
    """Build `{login_path}?next=<urlencoded current_path>`.

    current_path must already be path-only (use request.url.path + query).
    safe_next_url() is re-run server-side at read time.
    Returns just `login_path` if current_path is invalid.
    """
    validated = safe_next_url(current_path)
    if not validated:
        return login_path
    return f"{login_path}?next={quote(validated, safe='')}"
