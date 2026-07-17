"""Smoke tests that the media-buy harness envs actually enter and seed.

The structural regression test (tests/unit/test_media_buy_harness.py) checks
import/subclass/method presence but never ENTERS the env — which let
MediaBuyCreateEnv ship referencing two undefined helpers (setup_product_chain,
_build_mock_context_manager) without any test failing. These tests close that
gap by entering the real (IntegrationEnv) envs against a real DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.schemas._base import CreateMediaBuySuccess
from tests.helpers.adcp_factories import create_test_package_request_dict

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


@pytest.mark.requires_db
class TestMediaBuyCreateEnvEntersAndSeeds:
    """MediaBuyCreateEnv enters cleanly, seeds the product chain, and drives a create."""

    def test_enter_seed_and_create(self, integration_db):
        from tests.harness.media_buy_create import MediaBuyCreateEnv

        # tenant_id kept hyphen-free: TenantFactory derives the subdomain via
        # tenant_subdomain() (pub-<tenant_id>, underscores normalized to hyphens) and the
        # product's publisher_domain from it, which keeps the derived name predictable here.
        with MediaBuyCreateEnv(tenant_id="smoketenant") as env:
            # Entering already exercised _build_mock_context_manager (built in _configure_mocks).
            tenant, principal, product, pricing_option = env.setup_media_buy_data()
            assert product is not None, "setup_product_chain must create a Product"
            assert pricing_option is not None, "setup_product_chain must create a PricingOption"

            start = datetime.now(UTC) + timedelta(days=1)
            end = start + timedelta(days=30)
            result = env.call_impl(
                brand={"domain": "harness-smoke.example"},
                packages=[
                    create_test_package_request_dict(
                        product_id=product.product_id,
                        pricing_option_id="cpm_usd_fixed",
                        budget=5000.0,
                    )
                ],
                start_time=start.isoformat(),
                end_time=end.isoformat(),
            )
            created = result.response
            assert isinstance(created, CreateMediaBuySuccess), f"create must succeed, got {type(created).__name__}"
            assert created.media_buy_id is not None
