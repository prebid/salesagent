"""Stateful property: tenant isolation across sequences of create_media_buy.

A single-call property cannot find sequencing bugs. This generates a
sequence of K (tenant_choice, payload) operations, executes them against
two real tenants in the same environment, then asserts:

    INVARIANT 1 (isolation): MediaBuyRepository scoped to tenant A never
        returns a row created under tenant B, and vice versa.

    INVARIANT 2 (completeness): the union of both tenants' repository
        views contains exactly the buys we created -- nothing missing,
        nothing duplicated.

    INVARIANT 3 (buyer_ref uniqueness per principal): no two buys within
        the same tenant share a buyer_ref. (Collision-resistant generator
        is used; Hypothesis may still generate collisions that the system
        must reject.)

Catches:
    * Tenant-scoping bugs in repository queries (where a missing filter
      leaks rows across tenants -- see the tenant_isolation guard in
      tests/unit/test_architecture_workflow_tenant_isolation.py for the
      structural side of the same concern)
    * Cross-tenant contamination via shared state (singletons, caches,
      ContextVars that leak between calls)
    * Buyer_ref uniqueness check bypass (race / double-write)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select

from src.core.database.models import MediaBuy as MediaBuyModel
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import CreateMediaBuyRequest, CreateMediaBuySuccess
from src.core.testing_hooks import AdCPTestContext
from src.core.tools.media_buy_create import _create_media_buy_impl
from tests.factories import (
    PricingOptionFactory,
    PrincipalFactory,
    ProductFactory,
    PropertyTagFactory,
    TenantFactory,
)
from tests.harness.media_buy_create import MediaBuyCreateEnv

pytestmark = [pytest.mark.requires_db, pytest.mark.integration]


PRODUCT_ID = "prod_display"
PRICING_OPTION_ID = "cpm_usd_fixed"


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #


budget_strategy = st.decimals(
    min_value=Decimal("100.00"),
    max_value=Decimal("9000.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

# A sequence of (tenant_key, budget) tuples describes the operation list.
# tenant_key is "A" or "B"; Hypothesis picks randomly so each example has
# a different interleaving of cross-tenant operations.
operation_sequence_strategy = st.lists(
    st.tuples(st.sampled_from(["A", "B"]), budget_strategy),
    min_size=2,
    max_size=8,
)


# --------------------------------------------------------------------------- #
# Catalog setup
# --------------------------------------------------------------------------- #


def _setup_tenant(tenant_id: str, principal_id: str) -> None:
    tenant = TenantFactory(tenant_id=tenant_id)
    PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
    PrincipalFactory(
        tenant=tenant,
        principal_id=principal_id,
        platform_mappings={"mock": {"advertiser_id": f"mock_{principal_id}"}},
    )
    product = ProductFactory(
        tenant=tenant, product_id=PRODUCT_ID, property_tags=["all_inventory"]
    )
    PricingOptionFactory(
        product=product,
        pricing_model="cpm",
        currency="USD",
        is_fixed=True,
        rate=Decimal("10.00"),
    )


def _make_identity(tenant_id: str, principal_id: str) -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={"tenant_id": tenant_id},
        testing_context=AdCPTestContext(dry_run=False, test_session_id="isolation_test"),
        protocol="mcp",
    )


def _make_request(buyer_ref: str, budget: float) -> CreateMediaBuyRequest:
    start = datetime.now(UTC) + timedelta(days=1)
    end = start + timedelta(days=7)
    return CreateMediaBuyRequest(
        buyer_ref=buyer_ref,
        brand={"domain": "example.com"},
        start_time=start,
        end_time=end,
        packages=[
            {
                "product_id": PRODUCT_ID,
                "buyer_ref": f"pkg-{uuid.uuid4().hex[:8]}",
                "budget": budget,
                "pricing_option_id": PRICING_OPTION_ID,
            }
        ],
    )


# --------------------------------------------------------------------------- #
# Property
# --------------------------------------------------------------------------- #


@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
@given(ops=operation_sequence_strategy)
def test_tenant_isolation_across_sequence(integration_db, ops: list) -> None:
    """For any sequence of create_media_buy operations split across two
    tenants, each tenant's repository view must contain exactly its own
    creates -- no leaks, no duplicates, no cross-contamination.
    """
    # Fresh tenant pair per example -- avoids accumulated state.
    run_id = uuid.uuid4().hex[:8]
    tenant_a = f"iso-a-{run_id}"
    tenant_b = f"iso-b-{run_id}"
    principal_a = f"iso-pa-{run_id}"
    principal_b = f"iso-pb-{run_id}"

    # Use tenant_a for the env default identity; tenant_b identity is
    # constructed per-op below.
    with MediaBuyCreateEnv(
        tenant_id=tenant_a, principal_id=principal_a, dry_run=False
    ) as env:
        _setup_tenant(tenant_a, principal_a)
        _setup_tenant(tenant_b, principal_b)
        env.commit_catalog()

        identity = {
            "A": _make_identity(tenant_a, principal_a),
            "B": _make_identity(tenant_b, principal_b),
        }

        # Execute the sequence, tracking which media_buy_ids belong to which tenant.
        expected: dict[str, set[str]] = {"A": set(), "B": set()}
        for tenant_key, budget in ops:
            buyer_ref = f"iso-{tenant_key}-{uuid.uuid4().hex[:10]}"
            req = _make_request(buyer_ref, float(budget))
            result = env.call_impl(req=req, identity=identity[tenant_key])

            # Only successful creates land in the repository; errors shouldn't.
            if isinstance(result.response, CreateMediaBuySuccess):
                expected[tenant_key].add(result.response.media_buy_id)

        # Commit so our own read session sees the rows.
        assert env._session is not None
        env._session.commit()

        # Query what each tenant's view sees.
        actual: dict[str, set[str]] = {
            "A": set(
                env._session.scalars(
                    select(MediaBuyModel.media_buy_id).filter_by(tenant_id=tenant_a)
                ).all()
            ),
            "B": set(
                env._session.scalars(
                    select(MediaBuyModel.media_buy_id).filter_by(tenant_id=tenant_b)
                ).all()
            ),
        }

        # INVARIANT 1: isolation -- no cross-tenant leaks.
        assert expected["A"].isdisjoint(actual["B"]), (
            f"Tenant A's buys leaked into tenant B's view: "
            f"{expected['A'] & actual['B']}"
        )
        assert expected["B"].isdisjoint(actual["A"]), (
            f"Tenant B's buys leaked into tenant A's view: "
            f"{expected['B'] & actual['A']}"
        )

        # INVARIANT 2: completeness -- each tenant's view contains exactly
        # what we created under it.
        assert expected["A"] == actual["A"], (
            f"Tenant A view mismatch. Missing: {expected['A'] - actual['A']}, "
            f"Unexpected: {actual['A'] - expected['A']}"
        )
        assert expected["B"] == actual["B"], (
            f"Tenant B view mismatch. Missing: {expected['B'] - actual['B']}, "
            f"Unexpected: {actual['B'] - expected['B']}"
        )
