"""Drive the REAL property-list resolver against a REAL local HTTP origin.

``src/core/property_list_resolver.py`` fetches through the egress seam
(``src/core/security/outbound_http.py``), so a test that patches
``httpx.AsyncClient`` no longer exercises anything the resolver does: scheme
policy, address validation, IP pinning, redirect refusal and retry
classification all live behind the seam, and substituting the client deletes
all five at once. The seam grades those once, in
``tests/integration/test_outbound_http.py``.

What the seam does NOT own is the resolver's own cache, so the cases that grade
it point at ``local_origin`` instead — where "the second call did not re-fetch"
is ``origin.hits == 1`` on a server that actually ran, rather than a mock's
``call_count``.

These helpers are shared by every module that needs that setup so the
"which escape hatches are open" decision is made in exactly one place: a test of
a SUCCESSFUL fetch calls :func:`allow_local_origin`, a test of a REFUSAL calls
:func:`enforce_egress_policy`, and neither spells the variable names itself.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from adcp.types import PropertyListReference

from tests.helpers.local_http_origin import LocalOrigin

# Reuse the seam suite's flag helper rather than restating the variable names:
# a third spelling of "both hatches, explicitly" is a third thing to get wrong.
from tests.integration.test_outbound_http import set_flags

DEFAULT_LIST_ID = "test_list"


def allow_local_origin(monkeypatch) -> None:
    """Open the private-range egress escape hatch so a loopback origin is reachable.

    A local origin is ``127.0.0.1`` — a reserved address, which the seam refuses
    by default and rightly so. A test that wants a real fetch to succeed has to
    say that out loud; a test of a refusal must never call this, or it would
    grade nothing. Pair with the ``local_origin_tls`` fixture (not
    ``local_origin``) — the seam requires https unconditionally now
    (GH #1802), so a plain-http origin is refused regardless of this hatch.
    """
    set_flags(monkeypatch, private=True)


def enforce_egress_policy(monkeypatch) -> None:
    """Pin both escape hatches OFF — the production posture.

    Written explicitly rather than assumed unset, so a refusal test cannot be
    silently disarmed by an ambient ``ADCP_OUTBOUND_ALLOW_*`` in the shell that
    runs it.
    """
    set_flags(monkeypatch)


def domain_identifiers(*values: str) -> list[dict[str, str]]:
    """``PropertyIdentifier`` entries of type ``domain`` for *values*."""
    return [{"type": "domain", "value": value} for value in values]


def property_list_payload(
    identifiers: list[dict] | None = None,
    *,
    list_id: str = DEFAULT_LIST_ID,
    cache_valid_until: datetime | None = None,
    has_more: bool = False,
    cursor: str | None = None,
    total_count: int | None = None,
) -> dict[str, Any]:
    """Build a ``GetPropertyListResponse`` payload as the agent service would send it.

    ``identifiers=None`` omits the key entirely, which is a different wire shape
    from an empty list and is what the resolver's "no identifiers" branch reads.
    """
    payload: dict[str, Any] = {
        "list": {"list_id": list_id, "name": "Test Property List"},
        "resolved_at": datetime.now(UTC).isoformat(),
    }
    if identifiers is not None:
        payload["identifiers"] = identifiers
    if cache_valid_until is not None:
        payload["cache_valid_until"] = cache_valid_until.isoformat()
    if has_more or cursor is not None:
        pagination: dict[str, Any] = {"has_more": has_more}
        if cursor is not None:
            pagination["cursor"] = cursor
        if total_count is not None:
            pagination["total_count"] = total_count
        payload["pagination"] = pagination
    return payload


def property_list_body(identifiers: list[dict] | None = None, **kwargs: Any) -> bytes:
    """:func:`property_list_payload`, encoded for :meth:`LocalOrigin.respond_with`."""
    return json.dumps(property_list_payload(identifiers, **kwargs)).encode()


def origin_ref(
    origin: LocalOrigin,
    *,
    list_id: str = DEFAULT_LIST_ID,
    auth_token: str | None = None,
) -> PropertyListReference:
    """A ``PropertyListReference`` aimed at the running local origin."""
    return PropertyListReference(agent_url=origin.base_url, list_id=list_id, auth_token=auth_token)
