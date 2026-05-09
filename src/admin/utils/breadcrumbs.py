"""Embed-mode host-link root resolution.

Embedded tenants are rendered inside an upstream host's chrome (Scope3
Storefront, etc.). The leftmost item of the salesagent-rendered tenant
subnav should point back at the host's storefront homepage, not at the
salesagent's own dashboard, so navigation feels native.

The override comes from one of two sources, in precedence order:

1. ``X-Embed-Breadcrumb-Root`` header — per-request, set by the upstream
   proxy on each iframe load. Lets the host hot-swap the override without
   a tenant-management round-trip.
2. ``tenant.embed_breadcrumb_root`` column — persistent, set via the
   Tenant Management API. Acts as the default when the header is absent.

Both inputs are validated through the same :class:`EmbedBreadcrumbRoot`
Pydantic model, so a malformed header value falls through to the column
rather than 500-ing the page.

The header name and column name retain ``breadcrumb`` for backwards
compatibility with the upstream proxy contract — the API surface is
stable even though the salesagent's UI no longer renders breadcrumbs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from flask import request
from pydantic import ValidationError

from src.admin.api_schemas.tenant_management import EmbedBreadcrumbRoot

logger = logging.getLogger(__name__)

EMBED_BREADCRUMB_ROOT_HEADER = "X-Embed-Breadcrumb-Root"


def _validate(payload: Any) -> dict | None:
    """Return a serialized override dict, or None if the input is invalid."""
    if payload is None:
        return None
    try:
        return EmbedBreadcrumbRoot.model_validate(payload).model_dump()
    except ValidationError as exc:
        logger.warning("Rejecting invalid embed_breadcrumb_root payload: %s", exc)
        return None


def _read_header() -> dict | None:
    """Parse the request header, if present and valid. Header is JSON-encoded."""
    raw = request.headers.get(EMBED_BREADCRUMB_ROOT_HEADER) if request else None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Ignoring malformed %s header: %s", EMBED_BREADCRUMB_ROOT_HEADER, exc)
        return None
    return _validate(parsed)


def resolve_embed_breadcrumb_root(tenant: Any | None) -> dict | None:
    """Return the embed-mode first-crumb root: header > tenant column > None.

    The override is only meaningful when the current request is rendering
    in embedded chrome — either because the tenant is permanently
    ``is_embedded=True`` or because the caller authenticated via
    ``X-Identity-*`` headers (preview mode). Open-instance tenants viewed
    via OAuth ignore both sources since their crumbs already point at the
    salesagent's own dashboard.

    Args:
        tenant: The current ``Tenant`` ORM object (or ``None`` when no
            tenant context is bound to the request).

    Returns:
        A ``{"label": str, "url": str}`` dict, or ``None`` when no override
        is configured.
    """
    from src.admin.utils.embedded_mode_auth import is_embedded_view

    if tenant is None or not is_embedded_view(tenant):
        return None

    header_value = _read_header()
    if header_value is not None:
        return header_value

    column_value = getattr(tenant, "embed_breadcrumb_root", None)
    return _validate(column_value)
