"""Authentication functions for Prebid Sales Agent.

This module provides authentication and principal resolution functions used
by both MCP and A2A protocols.
"""

import logging
from typing import Any

from src.core.http_utils import get_header_case_insensitive as _get_header_case_insensitive

logger = logging.getLogger(__name__)


def get_push_notification_config_from_headers(headers: dict[str, str] | None) -> dict[str, Any] | None:
    """
    Extract protocol-level push notification config from MCP HTTP headers.

    MCP clients can provide push notification config via custom headers:
    - X-Push-Notification-Url: Webhook URL
    - X-Push-Notification-Auth-Scheme: Authentication scheme (HMAC-SHA256, Bearer, None)
    - X-Push-Notification-Credentials: Shared secret or Bearer token

    Returns:
        Push notification config dict matching A2A structure, or None if not provided
    """
    if not headers:
        return None

    url = _get_header_case_insensitive(headers, "x-push-notification-url")
    if not url:
        return None

    auth_scheme = _get_header_case_insensitive(headers, "x-push-notification-auth-scheme") or "None"
    credentials = _get_header_case_insensitive(headers, "x-push-notification-credentials")

    return {
        "url": url,
        "authentication": {"schemes": [auth_scheme], "credentials": credentials} if auth_scheme != "None" else None,
    }


# (Deleted) get_principal_from_context resolved a principal AND detected a tenant from a
# FastMCP Context -- its own x-adcp-tenant handling, parallel to _detect_tenant. It is the
# ancestor of src/core/resolved_identity._resolve_identity, which replaced it when identity
# resolution collapsed to one site. It had ZERO production callers and survived only
# because seven test modules called it directly; test_no_duplicate_auth_functions.py said
# so outright, keeping it "in auth.py for test compat". A second resolver kept alive by its
# own tests is still a second resolver.
#
# (Deleted) the two require-the-principal / require-the-tenant helpers re-checked, inside
# every protected tool, what the resolver had already decided: the boundary refuses an
# anonymous caller before a protected implementation runs, so ``identity.principal is None``
# there was unreachable, and a helper that raised AUTH_MISSING on it was a second minting
# site for the refusal. The type carries the decision now: a protected implementation takes
# ``ResolvedIdentity``, whose ``principal`` and ``tenant`` are not optional, and reads them
# directly; a public one takes ``PublicIdentity`` and branches on ``identity.principal``.
