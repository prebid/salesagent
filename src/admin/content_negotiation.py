"""Accept-aware content-negotiation helper for admin exception handlers.

Implements Critical Invariant #3 — ``AdCPError`` + admin HTTPException handlers
render HTML for ``/admin/*`` + ``/tenant/*`` browser navigation and JSON
everywhere else. Keeps the AdCP wire format (MCP / A2A / REST) byte-stable:
AdCP surfaces NEVER receive HTML regardless of ``Accept``.

Canonical spec: ``flask-to-fastapi-foundation-modules.md §11.10`` + ``§11.11``;
``flask-to-fastapi-deep-audit.md §1.3`` (Blocker 3); ``frontend-deep-audit.md``
F6 / H7 (AJAX and browser-fetch wildcard-Accept edge cases).

Companion template: ``templates/error.html`` with pinned variable contract
``{error_code, message, status_code}`` — handlers that render the template
MUST pass exactly those three keys; no extras per L0-14 constraint.

Sync L0-L4 — no async primitives; header read is O(1).
"""

from __future__ import annotations

from typing import Literal

from starlette.requests import Request

ResponseMode = Literal["html", "json"]

_ADMIN_PATH_PREFIXES: tuple[str, ...] = ("/admin/", "/tenant/")


def _is_admin_path(path: str) -> bool:
    """Return True iff ``path`` belongs to the admin HTML surface.

    The two prefixes participate in HTML negotiation per D1 2026-04-16
    canonical URL routing — ``/tenant/{tenant_id}/...`` is the canonical
    admin mount; ``/admin/*`` is the legacy/operator-bookmark form that the
    ``LegacyAdminRedirectMiddleware`` (L1c) 308s forward. Both must be
    Accept-aware HTML surfaces.
    """
    return path.startswith(_ADMIN_PATH_PREFIXES)


def _response_mode(request: Request) -> ResponseMode:
    """Decide whether to render HTML or JSON for a given request.

    Decision order (first match wins):

    1. **XHR indicator wins JSON.** ``X-Requested-With: XMLHttpRequest`` forces
       JSON regardless of Accept — admin JS ``fetch()`` calls expect structured
       error payloads even when the browser auto-sends ``Accept: text/html``.
       Prevents the F6/H7 ``*/*`` false-positive entirely on XHR paths.

    2. **Path-scope to admin surface.** ``/admin/*`` or ``/tenant/*`` only —
       AdCP surfaces (``/mcp/*``, ``/a2a/*``, ``/api/*``) always receive JSON.
       The AdCP wire format is byte-stable per Invariant #3 + adcp-safety.

    3. **Accept header evaluation.** On an admin path:
         - ``text/html`` in Accept (and not ``application/json``) → HTML
         - ``*/*`` with no AJAX indicator (browser navigation fallback) → HTML
         - Otherwise (explicit JSON request, anything else) → JSON

    The ``*/*``-on-admin-path branch is the frontend-deep-audit F6 mitigation:
    a browser that happens to send only ``*/*`` on navigation should still see
    the templated error page, not raw JSON.
    """
    headers = request.headers
    if headers.get("x-requested-with", "").lower() == "xmlhttprequest":
        return "json"

    if not _is_admin_path(request.url.path):
        return "json"

    accept = headers.get("accept", "").lower()
    if "application/json" in accept and "text/html" not in accept:
        return "json"
    if "text/html" in accept:
        return "html"
    if "*/*" in accept or not accept:
        # Browser-fetch / navigation fallback — no AJAX indicator and no
        # explicit JSON request. F6/H7: render HTML so the user does not see
        # raw JSON in their browser.
        return "html"
    return "json"
