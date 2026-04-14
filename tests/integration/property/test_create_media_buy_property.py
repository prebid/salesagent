"""Property-based integration tests for create_media_buy.

First Hypothesis test in the repo. Generates arbitrary valid
CreateMediaBuyRequest payloads and exercises the full chain:
Pydantic parse -> validators -> _create_media_buy_impl -> response model.

Properties asserted (per request):
    P1. Success path: response is CreateMediaBuySuccess (not Error)
    P2. Roundtrip: CreateMediaBuySuccess(**resp.model_dump()) == resp
        (catches the apply_testing_hooks bug class -- nested model_dump
         not propagating, extra fields breaking reconstruction)
    P3. JSON-mode roundtrip survives json.dumps -> json.loads -> validate
        (catches Decimal/datetime serialization drift)
    P4. buyer_ref preserved end-to-end

Run::

    eval $(.claude/skills/agent-db/agent-db.sh up)
    uv run pytest tests/integration/property/ -x -v
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.core.schemas import (
    CreateMediaBuyRequest,
    CreateMediaBuySuccess,
)
from tests.factories import (
    PricingOptionFactory,
    PrincipalFactory,
    ProductFactory,
    PropertyTagFactory,
    TenantFactory,
)
from tests.harness.media_buy_create import MediaBuyCreateEnv

pytestmark = [pytest.mark.requires_db, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #

# Use a fixed catalog -- product_id and pricing_option_id are constants the
# test catalog provides. The PricingOptionFactory defaults
# (pricing_model="cpm", currency="USD", is_fixed=True) produce the canonical
# pricing_option_id "cpm_usd_fixed" via the {model}_{currency}_{fixed} format.
PRODUCT_ID = "prod_display"
PRICING_OPTION_ID = "cpm_usd_fixed"


def _future_datetime_strategy() -> st.SearchStrategy[datetime]:
    """Tz-aware datetimes between 1 hour and 30 days in the future.

    Using a fresh ``datetime.now(UTC)`` per draw via st.builds keeps the
    relative offset stable even if test run time drifts.
    """
    return st.builds(
        lambda offset_seconds: datetime.now(UTC) + timedelta(seconds=offset_seconds),
        st.integers(min_value=3600, max_value=30 * 86400),
    )


_buyer_ref_alphabet = st.characters(whitelist_categories=("Ll", "Lu", "Nd"))

buyer_ref_strategy = st.text(
    alphabet=_buyer_ref_alphabet, min_size=4, max_size=20
).map(lambda s: f"buyer-{s}-{uuid.uuid4().hex[:8]}")

brand_domain_strategy = st.sampled_from(
    ["example.com", "testbrand.com", "publisher.io", "advertiser.org"]
)

# Mock adapter caps inventory at 1M impressions per line item. With CPM=$10
# (PricingOption defaults below), max budget = $10 * 1_000_000 / 1000 = $10,000.
# Hypothesis already shrunk to the boundary $10,000.01 -> stay safely under.
package_budget_strategy = st.decimals(
    min_value=Decimal("100.00"),
    max_value=Decimal("9000.00"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)


@st.composite
def create_media_buy_payload(draw: st.DrawFn) -> dict:
    """Generate kwargs for a valid CreateMediaBuyRequest."""
    start = draw(_future_datetime_strategy())
    duration_days = draw(st.integers(min_value=1, max_value=60))
    end = start + timedelta(days=duration_days)

    # Single package per buy. Production enforces "each product_id may appear
    # at most once per media buy", and our test catalog has one product. To
    # enable multi-package property tests, seed N products and let the strategy
    # pick distinct ones.
    packages = [
        {
            "product_id": PRODUCT_ID,
            "buyer_ref": f"pkg-{uuid.uuid4().hex[:8]}",
            "budget": float(draw(package_budget_strategy)),
            "pricing_option_id": PRICING_OPTION_ID,
        }
    ]

    return {
        "buyer_ref": draw(buyer_ref_strategy),
        "brand": {"domain": draw(brand_domain_strategy)},
        "start_time": start,
        "end_time": end,
        "packages": packages,
    }


# --------------------------------------------------------------------------- #
# Catalog setup
# --------------------------------------------------------------------------- #


def _setup_catalog(env: MediaBuyCreateEnv) -> None:
    """Create the minimum tenant/principal/product/pricing-option catalog.

    Done once per Hypothesis example. Cheap enough at this scale; if it
    becomes a hot path, hoist into a module-scoped fixture and reset
    media_buys table between examples instead.
    """
    tenant = TenantFactory(tenant_id=env._tenant_id)
    PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")
    PrincipalFactory(
        tenant=tenant,
        principal_id=env._principal_id,
        platform_mappings={"mock": {"advertiser_id": "mock_adv_1"}},
    )
    product = ProductFactory(
        tenant=tenant,
        product_id=PRODUCT_ID,
        property_tags=["all_inventory"],
    )
    PricingOptionFactory(
        product=product,
        pricing_model="cpm",
        currency="USD",
        is_fixed=True,
        rate=Decimal("10.00"),
    )
    env.commit_catalog()


# --------------------------------------------------------------------------- #
# Properties
# --------------------------------------------------------------------------- #


@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)
@given(payload=create_media_buy_payload())
def test_create_media_buy_response_roundtrips(integration_db, payload: dict) -> None:
    """For any valid request payload, the success response satisfies P1-P4."""
    # IDs use hyphens (DNS-safe). Underscores in tenant_id flow into
    # subdomain -> publisher_domain construction, which enforces a
    # ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]...)*$ regex.
    tenant_id = f"prop-t-{uuid.uuid4().hex[:8]}"
    principal_id = f"prop-p-{uuid.uuid4().hex[:6]}"

    with MediaBuyCreateEnv(
        tenant_id=tenant_id,
        principal_id=principal_id,
        dry_run=True,
    ) as env:
        _setup_catalog(env)

        request = CreateMediaBuyRequest(**payload)
        result = env.call_impl(req=request)
        response = result.response

        # P1 -- success path
        assert isinstance(response, CreateMediaBuySuccess), (
            f"Expected CreateMediaBuySuccess, got {type(response).__name__}: "
            f"{getattr(response, 'errors', response)}"
        )

        # P2 -- python-mode roundtrip
        dumped = response.model_dump()
        reconstructed = CreateMediaBuySuccess(**dumped)
        assert reconstructed.model_dump() == dumped, (
            "Python-mode model_dump roundtrip diverged"
        )

        # P3 -- JSON-mode roundtrip (Decimal/datetime serialization)
        json_dumped = response.model_dump(mode="json")
        json_blob = json.dumps(json_dumped)
        json_reloaded = CreateMediaBuySuccess(**json.loads(json_blob))
        assert json_reloaded.model_dump(mode="json") == json_dumped, (
            "JSON-mode roundtrip diverged"
        )

        # P4 -- buyer_ref preserved
        assert response.buyer_ref == payload["buyer_ref"], (
            f"buyer_ref drift: sent {payload['buyer_ref']!r}, "
            f"got {response.buyer_ref!r}"
        )
