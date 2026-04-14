"""Deterministic regression test for salesagent-81u4.

_create_media_buy_impl must RETURN CreateMediaBuyError for known
validation rejections, never raise. Pinpointed by the Hypothesis property
test in test_create_media_buy_property.py at the exact mock-adapter
inventory boundary: budget=$10,000.01 with $10 CPM = 1,000,001 impressions
exceeding the 1M cap by one.

Before the fix at media_buy_create.py:2844, this raised AdCPValidationError
(contract violation -- the function is documented to return
CreateMediaBuyResult).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.core.schemas import (
    CreateMediaBuyError,
    CreateMediaBuyRequest,
    CreateMediaBuyResult,
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


def test_adapter_inventory_overflow_returns_error(integration_db) -> None:
    """Budget that overshoots mock adapter inventory must surface as a
    CreateMediaBuyError, not an unhandled AdCPValidationError."""
    with MediaBuyCreateEnv(
        tenant_id="reg-81u4-t",
        principal_id="reg-81u4-p",
        dry_run=True,
    ) as env:
        tenant = TenantFactory(tenant_id="reg-81u4-t")
        PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All")
        PrincipalFactory(
            tenant=tenant,
            principal_id="reg-81u4-p",
            platform_mappings={"mock": {"advertiser_id": "mock_adv_1"}},
        )
        product = ProductFactory(
            tenant=tenant, product_id="prod_display", property_tags=["all_inventory"]
        )
        PricingOptionFactory(
            product=product,
            pricing_model="cpm",
            currency="USD",
            is_fixed=True,
            rate=Decimal("10.00"),
        )
        env.commit_catalog()

        start = datetime.now(UTC) + timedelta(days=1)
        end = start + timedelta(days=7)
        request = CreateMediaBuyRequest(
            buyer_ref="reg-81u4-buyer",
            brand={"domain": "example.com"},
            start_time=start,
            end_time=end,
            packages=[
                {
                    "product_id": "prod_display",
                    "buyer_ref": "reg-81u4-pkg",
                    "budget": 10000.01,  # 1M+1 impressions at $10 CPM
                    "pricing_option_id": "cpm_usd_fixed",
                }
            ],
        )

        result = env.call_impl(req=request)

        assert isinstance(result, CreateMediaBuyResult)
        assert isinstance(result.response, CreateMediaBuyError), (
            f"Expected CreateMediaBuyError (returned), got "
            f"{type(result.response).__name__}: {result.response!r}"
        )
        assert result.response.errors, "errors[] must be non-empty"

        err = result.response.errors[0]
        assert err.code == "validation_error"
        assert "PERCENTAGE_UNITS_BOUGHT_TOO_HIGH" in err.message
        assert err.details and err.details.get("error_code") == "ADAPTER_VALIDATION_FAILED"
