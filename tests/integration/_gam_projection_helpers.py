"""Shared helpers for GAM projection / materialization integration tests.

Identity construction and scenario setup are centralized here so the
same calls (with the same ``set_current_tenant`` side effect and the
same far-future flight dates) are used across every test that exercises
the projection/materialization paths.

The companion ``factory_session`` fixture lives in
``tests/integration/conftest.py`` so pytest discovers it automatically
without import-side redefinitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.core.resolved_identity import ResolvedIdentity


def make_identity(tenant_id: str, principal_id: str) -> ResolvedIdentity:
    """Build a ResolvedIdentity and seed the tenant contextvar."""
    from src.core.config_loader import set_current_tenant

    tenant_data = {"tenant_id": tenant_id, "adapter_type": "mock"}
    set_current_tenant(tenant_data)
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=tenant_data,
        protocol="mcp",
        testing_context=None,
    )


@dataclass
class GamProjectionScenario:
    """Tenant + principal + assigned advertiser + (optional) order/line items.

    Returned by ``build_assigned_order_scenario`` so tests can set up a
    common shape in a single line.
    """

    tenant: Any
    principal: Any
    advertiser: Any
    order: Any


def build_assigned_order_scenario(
    *,
    order_status: str = "APPROVED",
    line_item_count: int = 0,
    line_item_status: str = "DELIVERING",
    advertiser_principal_id: str | None | object = ...,
) -> GamProjectionScenario:
    """Create a tenant, a principal, a GamAdvertiser assigned to that
    principal, and a GAMOrder for that advertiser.

    Defaults: order is APPROVED with a far-future end_date so the date-
    derived projection status resolves to ``active``. Pass
    ``line_item_count`` to spawn that many GAMLineItem rows attached to
    the order. Pass ``advertiser_principal_id`` (e.g., None or a
    different principal_id) to test the unassigned path.
    """
    from tests.factories import (
        GamAdvertiserFactory,
        GAMLineItemFactory,
        GAMOrderFactory,
        PrincipalFactory,
        TenantFactory,
    )

    tenant = TenantFactory()
    principal = PrincipalFactory(tenant=tenant)
    assigned = principal.principal_id if advertiser_principal_id is ... else advertiser_principal_id
    advertiser = GamAdvertiserFactory(tenant=tenant, principal_id=assigned)
    order = GAMOrderFactory(
        tenant=tenant,
        advertiser_id=advertiser.advertiser_id,
        status=order_status,
        start_date=datetime(2024, 1, 1, tzinfo=UTC),
        end_date=datetime(2099, 12, 31, tzinfo=UTC),
    )
    for _ in range(line_item_count):
        GAMLineItemFactory(tenant=tenant, order_id=order.order_id, status=line_item_status)
    return GamProjectionScenario(tenant=tenant, principal=principal, advertiser=advertiser, order=order)
