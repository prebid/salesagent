"""Embedded-mode capability flags.

On embedded instances, the storefront (upstream host product) may absorb
workflows that the salesagent historically owned — creative approval,
Slack notifications, advertising policy, etc. ``EMBEDDED_CAPABILITIES``
is a JSON env var that names which workflows the storefront has taken
over for this salesagent instance. The salesagent hides UI and rejects
writes for any workflow owned by the storefront.

Why instance-level, not per-tenant: one embedded salesagent corresponds
to one storefront operator. The storefront decides once which workflows
it owns across all of its tenants.

Why env-var, not DB: ownership flips at the storefront's release pace,
not the salesagent's. An env-var bump is a deploy; a DB column would
need a migration plus a per-tenant rollout. Same reason ``MANAGED_INSTANCE``
is an env var.

Open instances (``MANAGED_INSTANCE`` unset or false): the env var is
ignored entirely. ``capability_owner()`` always returns ``"publisher"``,
``publisher_owns()`` always returns ``True``. There is no embedded
storefront to take ownership.

Format::

    EMBEDDED_CAPABILITIES='{"creative_approval": "storefront", "slack": "storefront"}'

Unknown keys → default ``"publisher"``. Invalid JSON or non-``str``
values → ``ValueError`` at first call (fail loud — misconfiguration
silently leaving every workflow on the publisher side would be the
worst failure mode).
"""

from __future__ import annotations

import json
import os
from typing import Literal

from src.admin.utils.embedded_mode_auth import is_managed_instance

CapabilityOwner = Literal["publisher", "storefront"]


def _parse_capabilities() -> dict[str, CapabilityOwner]:
    raw = os.environ.get("EMBEDDED_CAPABILITIES", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"EMBEDDED_CAPABILITIES is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"EMBEDDED_CAPABILITIES must be a JSON object, got {type(parsed).__name__}")
    result: dict[str, CapabilityOwner] = {}
    for key, value in parsed.items():
        if value not in ("publisher", "storefront"):
            raise ValueError(f"EMBEDDED_CAPABILITIES[{key!r}] must be 'publisher' or 'storefront', got {value!r}")
        result[key] = value
    return result


def capability_owner(name: str) -> CapabilityOwner:
    """Return ``"storefront"`` if the upstream host has taken over this
    workflow, ``"publisher"`` otherwise.

    Always returns ``"publisher"`` on open instances (no storefront to
    take ownership). Re-reads the env var on every call so a deploy
    flip takes effect without process restart.
    """
    if not is_managed_instance():
        return "publisher"
    return _parse_capabilities().get(name, "publisher")


def publisher_owns(name: str) -> bool:
    """Sugar for ``capability_owner(name) == "publisher"``. Used in
    Jinja gates: ``{% if publisher_owns('creative_approval') %}``."""
    return capability_owner(name) == "publisher"


def require_capability_blueprint(capability: str):
    """Build a Flask ``before_request`` handler that blocks every route
    on a blueprint when ``capability`` is storefront-owned.

    Use when an entire surface (e.g. the Creative Agents management
    pages) should disappear on embedded instances that have centralized
    that workflow upstream::

        my_bp.before_request(require_capability_blueprint("creative_agents"))

    Returns ``None`` when the publisher owns the capability so Flask
    proceeds to the route; otherwise returns a 403 response from
    :func:`capability_owned_response`.
    """

    def _hook():
        if not publisher_owns(capability):
            return capability_owned_response(capability)
        return None

    _hook.__name__ = f"_require_capability_{capability}"
    return _hook


def capability_owned_response(capability: str):
    """Build the 403 response for a write attempt on a storefront-owned
    workflow. JSON requests get a structured body; everything else gets a
    plain-text 403.

    The UI shouldn't be reaching these endpoints when the section is
    gated off — this is defense-in-depth against direct POSTs, stale
    forms cached in a browser, and template gate bugs.

    Lives next to ``publisher_owns()`` so blueprint code can stay terse::

        if not publisher_owns("slack"):
            return capability_owned_response("slack")
    """
    from flask import jsonify, request

    message = f"The '{capability}' workflow is managed by your platform."
    if request.is_json or request.headers.get("Accept", "").startswith("application/json"):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "capability_owned_by_storefront",
                    "capability": capability,
                    "message": message,
                }
            ),
            403,
        )
    return message, 403, {"Content-Type": "text/plain; charset=utf-8"}
