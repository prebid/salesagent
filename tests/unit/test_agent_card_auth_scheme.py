"""Contract test: agent card advertises the right auth scheme per leg.

adcp 4.5.0 auto-publishes a ``security_schemes`` + ``security_requirements``
pair on the A2A agent card derived from the seller's
:class:`BearerTokenAuth` config. The mapping rule:

* A2A leg with bearer prefix required (RFC 6750
  ``Authorization: Bearer <token>``) → ``HTTPAuthSecurityScheme`` with
  ``scheme="bearer"`` and id ``"bearerAuth"``. This is what off-the-shelf
  a2a-sdk clients know how to attach credentials for.
* A2A leg with bearer prefix NOT required (raw-token custom header) →
  ``APIKeySecurityScheme`` with ``in: header`` and id ``"adcpAuth"``.

Salesagent's production wiring is::

    BearerTokenAuth(
        validate_token=...,
        mcp_header_name="x-adcp-auth",
        mcp_bearer_prefix_required=False,
    )

A2A defaults to ``Authorization`` + ``bearer_prefix_required=True``, so the
expected agent-card advertisement on A2A is ``bearerAuth``.

Why this test: a future config tweak that flips A2A defaults would silently
break a2a-sdk's interceptor, sending unauthenticated requests against an
auth-protected seller. This test fails loudly when the wire shape drifts.
"""

from __future__ import annotations

from adcp.server.a2a_server import _build_security_for_auth
from adcp.server.auth import BearerTokenAuth, Principal


def _production_auth() -> BearerTokenAuth:
    """The exact ``BearerTokenAuth`` config ``core.main._serve_kwargs`` passes."""
    return BearerTokenAuth(
        validate_token=lambda t: Principal(caller_identity="x") if t == "v" else None,
        mcp_header_name="x-adcp-auth",
        mcp_bearer_prefix_required=False,
    )


def test_a2a_advertises_bearer_auth_scheme():
    """A2A leg → ``bearerAuth`` (HTTP scheme=bearer) on the agent card."""
    schemes, requirements = _build_security_for_auth(_production_auth())

    assert "bearerAuth" in schemes, (
        f"Expected 'bearerAuth' scheme on A2A agent card; got {list(schemes)!r}. "
        "a2a-sdk clients won't attach credentials without it."
    )
    bearer_scheme = schemes["bearerAuth"]
    # SecurityScheme is a oneof — the bearer variant must be populated.
    assert bearer_scheme.HasField("http_auth_security_scheme"), (
        "Expected HTTPAuthSecurityScheme variant; A2A buyers reading the "
        "card need 'scheme=bearer' to drive RFC 6750 auth."
    )
    assert bearer_scheme.http_auth_security_scheme.scheme == "bearer"


def test_security_requirement_references_bearer_scheme():
    """The agent card's ``security_requirements`` must reference the
    advertised scheme by id — without the requirement, a2a-sdk's
    interceptor still skips credential attachment even if the scheme
    is published."""
    schemes, requirements = _build_security_for_auth(_production_auth())

    assert len(requirements) == 1, (
        f"Expected exactly one SecurityRequirement; got {len(requirements)}"
    )
    requirement = requirements[0]
    assert "bearerAuth" in requirement.schemes, (
        f"SecurityRequirement must reference 'bearerAuth' scheme; got {dict(requirement.schemes)!r}"
    )


def test_unauthenticated_handler_publishes_no_security():
    """When ``auth`` is ``None`` (e.g. public-discovery agents), the card
    publishes no security envelope so unauthenticated buyers can still
    discover."""
    schemes, requirements = _build_security_for_auth(None)
    assert schemes == {}, f"Expected empty schemes for None auth; got {schemes!r}"
    assert requirements == [], f"Expected empty requirements; got {requirements!r}"
