"""Smoke tests that the media-buy harness envs actually enter and seed.

The structural regression test (tests/unit/test_media_buy_harness.py) checks
import/subclass/method presence but never ENTERS the env — which let
MediaBuyCreateEnv ship referencing two undefined helpers (setup_product_chain,
_build_mock_context_manager) without any test failing. These tests close that
gap by entering the real (IntegrationEnv) envs against a real DB.
"""

from __future__ import annotations

import pytest

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

            created = env.create_default_buy(product, brand_domain="harness-smoke.example")
            assert created.media_buy_id is not None
