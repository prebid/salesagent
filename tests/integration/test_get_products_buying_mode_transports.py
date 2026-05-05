"""Integration tests for buying_mode and refine across MCP, A2A, REST transports.

Exercises the full request/response pipeline at every transport for each of the three
modes (brief / wholesale / refine), verifying:
- Request reaches _get_products_impl with the right shape
- Mode-specific behavior (ranker called/skipped, brief_relevance/refinement_applied)
- Outbound 3.0.6 wire compat applies uniformly across transports
- Audit log fields populate

Covers: UC-001-MODE-BRIEF-01
Covers: UC-001-MODE-WHOLESALE-01
Covers: UC-001-MODE-REFINE-01
"""

from __future__ import annotations

import pytest

from tests.factories import (
    PrincipalFactory,
    TenantFactory,
    create_buying_mode_test_products,
)
from tests.harness.product import ProductEnv
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Shared fixture: a tenant with a small catalog
# ---------------------------------------------------------------------------


@pytest.fixture
def env(integration_db):
    """ProductEnv with the standard buying-mode test product set."""
    with ProductEnv(tenant_id="bm-test", principal_id="bm-principal") as e:
        tenant = TenantFactory(tenant_id="bm-test", subdomain="bm-test")
        PrincipalFactory(tenant=tenant, principal_id="bm-principal")
        create_buying_mode_test_products(tenant)

        yield e


# ---------------------------------------------------------------------------
# Brief mode across all four transports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("transport", [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST])
def test_brief_mode_returns_products(env, transport):
    """Brief mode returns products at every transport."""
    result = env.call_via(
        transport,
        buying_mode="brief",
        brief="display ads for tech audience",
        adcp_version="3.0.6",
    )

    assert result.is_success, f"transport={transport!r} failed: {result.error}"
    payload = result.payload
    assert len(payload.products) >= 2
    # Wholesale-only response fields are not present in brief mode
    assert payload.refinement_applied is None


# ---------------------------------------------------------------------------
# Wholesale mode across all four transports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("transport", [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST])
def test_wholesale_mode_returns_products_without_brief_relevance(env, transport):
    """Wholesale mode returns products at every transport; ranker is skipped."""
    result = env.call_via(
        transport,
        buying_mode="wholesale",
        adcp_version="3.0.6",
    )

    assert result.is_success, f"transport={transport!r} failed: {result.error}"
    payload = result.payload
    assert len(payload.products) >= 2
    # Wholesale never produces refinement_applied
    assert payload.refinement_applied is None
    # Wholesale mode does not run the ranker, so no brief_relevance is set
    assert all(p.brief_relevance is None for p in payload.products)


# ---------------------------------------------------------------------------
# Refine mode across all four transports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("transport", [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST])
def test_refine_mode_returns_refinement_applied(env, transport):
    """Refine mode returns refinement_applied with status='unable' at every transport.

    Until #1073 implements proposal-state persistence, every refine entry resolves to
    'unable'. Storyboard validation passes because the response_schema check requires
    refinement_applied to be a valid array (or absent), and products to be present.
    """
    result = env.call_via(
        transport,
        buying_mode="refine",
        refine=[
            {"scope": "request", "ask": "narrow to guaranteed only"},
            {"scope": "product", "product_id": "display_premium", "action": "include"},
        ],
        adcp_version="3.0.6",
    )

    assert result.is_success, f"transport={transport!r} failed: {result.error}"
    payload = result.payload
    assert len(payload.products) >= 2
    assert payload.refinement_applied is not None
    assert len(payload.refinement_applied) == 2
    # Both entries are unable until #1073 lands
    assert all(item.status.value == "unable" for item in payload.refinement_applied)
    # Echo of scope per spec
    assert payload.refinement_applied[0].scope.value == "request"
    assert payload.refinement_applied[1].scope.value == "product"


# ---------------------------------------------------------------------------
# Outbound wire compat: REST receives JSON with product_id / proposal_id
# ---------------------------------------------------------------------------


def test_rest_refine_response_uses_3_0_6_wire_field_names(env):
    """REST clients see product_id / proposal_id (spec 3.0.6) on refinement_applied items."""
    response = env._run_rest_request(
        "/api/v1/products",
        buying_mode="refine",
        refine=[
            {"scope": "product", "product_id": "display_premium", "action": "include"},
            {"scope": "proposal", "proposal_id": "prop_xyz", "action": "include"},
        ],
        adcp_version="3.0.6",
    )

    assert response.status_code == 200, response.text
    data = response.json()
    applied = data["refinement_applied"]
    assert len(applied) == 2
    # Storyboard 3.0.6 wire format
    assert applied[0]["product_id"] == "display_premium"
    assert "id" not in applied[0]
    assert "proposal_id" not in applied[0]
    assert applied[1]["proposal_id"] == "prop_xyz"
    assert "id" not in applied[1]
    assert "product_id" not in applied[1]


# ---------------------------------------------------------------------------
# Pre-v3 default-to-brief through wrapper layer
# ---------------------------------------------------------------------------


def test_pre_v3_client_without_buying_mode_defaults_to_brief_via_rest(env):
    """A REST client that omits buying_mode and declares pre-v3 version is treated as brief."""
    response = env._run_rest_request(
        "/api/v1/products",
        brief="display ads",
        adcp_version="2.5.0",  # pre-v3
    )

    # No 4xx — request is accepted with default-to-brief
    assert response.status_code == 200, response.text
    data = response.json()
    assert "products" in data
    # Brief mode response: no refinement_applied
    assert data.get("refinement_applied") is None


def test_v3_client_without_buying_mode_rejected_via_rest(env):
    """A v3 REST client must include buying_mode; the request is rejected."""
    response = env._run_rest_request(
        "/api/v1/products",
        brief="display ads",
        adcp_version="3.0.6",
    )

    # The wrapper does not default v3 clients; the schema validator rejects.
    assert response.status_code >= 400
