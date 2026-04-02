"""Integration tests for MCP unknown field handling.

Verifies that the schema-aware RequestCompatMiddleware strips unknown
fields and translates deprecated fields through the real FastMCP pipeline.
"""

import logging

import pytest

from tests.factories import PricingOptionFactory, ProductFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "mcptest"


def _create_tenant_with_product():
    """Create minimal tenant with a product inside an active env session."""
    tenant = TenantFactory(tenant_id=TENANT_ID)
    product = ProductFactory(tenant=tenant, product_id="mcp_prod_1")
    PricingOptionFactory(product=product)
    return tenant


class TestMcpUnknownFieldStripping:
    """Unknown fields are stripped by middleware, request succeeds."""

    def test_known_fields_only(self, integration_db):
        """Standard call with only known fields works."""
        from tests.harness.product import ProductEnv

        with ProductEnv(tenant_id=TENANT_ID) as env:
            _create_tenant_with_product()
            result = env.call_mcp(brief="test ads")
            assert result is not None

    def test_unknown_field_stripped(self, integration_db, caplog):
        """Unknown field is stripped with WARNING, request succeeds."""
        from tests.harness.product import ProductEnv

        with ProductEnv(tenant_id=TENANT_ID) as env:
            _create_tenant_with_product()
            with caplog.at_level(logging.WARNING):
                result = env.call_mcp(brief="test ads", nonsense_field="bar")
            assert result is not None
            assert "nonsense_field" in caplog.text

    def test_deprecated_translated_unknown_stripped(self, integration_db, caplog):
        """Deprecated field translated + unknown field stripped in same call."""
        from tests.harness.product import ProductEnv

        with ProductEnv(tenant_id=TENANT_ID) as env:
            _create_tenant_with_product()
            with caplog.at_level(logging.INFO):
                result = env.call_mcp(
                    brand_manifest="https://acme.com/.well-known/brand.json",
                    brief="test ads",
                    bogus_param=123,
                )
            assert result is not None
            # brand_manifest translated (INFO from normalize_request_params)
            assert "brand_manifest" in caplog.text
            # bogus_param stripped (WARNING from middleware)
            assert "bogus_param" in caplog.text
