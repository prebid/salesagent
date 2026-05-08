"""Integration tests for MCP transport compat handling.

Environment-aware: dev mode rejects unknown fields (fail loudly),
production mode strips them (forward compatible).

Deprecated field translation works in both modes.
"""

import os
from unittest.mock import patch

import pytest
from fastmcp.exceptions import ToolError

from tests.factories import PricingOptionFactory, ProductFactory, TenantFactory

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "mcptest"


def _create_tenant_with_product():
    """Create minimal tenant with a product inside an active env session."""
    tenant = TenantFactory(tenant_id=TENANT_ID)
    product = ProductFactory(tenant=tenant, product_id="mcp_prod_1")
    PricingOptionFactory(product=product)
    return tenant


class TestMcpDevMode:
    """Dev mode: unknown fields reach TypeAdapter and are rejected."""

    def test_known_fields_only(self, integration_db):
        """Standard call with only known fields works in dev mode."""
        from tests.harness.product import ProductEnv

        with ProductEnv(tenant_id=TENANT_ID) as env:
            _create_tenant_with_product()
            result = env.call_mcp(brief="test ads")
            assert result is not None

    @pytest.mark.xfail(
        reason=(
            "Unknown-field rejection (dev-mode strict-extra) isn't enforced at "
            "the wire boundary today — the SDK resolves params_model from the "
            "platform router's base advertisement, not from the delegate, so "
            "unknown kwargs reach the impl unchecked. Documented in "
            "core/platforms/_delegate.py:_delegate_get_products. Pending an "
            "upstream SDK change to honor strict-extra at the dispatcher."
        ),
        strict=True,
    )
    def test_unknown_field_rejected(self, integration_db):
        """Dev mode: unknown field causes ToolError — loud failure for schema drift detection."""
        from tests.harness.product import ProductEnv

        with ProductEnv(tenant_id=TENANT_ID) as env:
            _create_tenant_with_product()
            with pytest.raises(ToolError, match="nonsense_field"):
                env.call_mcp(brief="test ads", nonsense_field="bar")

    def test_deprecated_field_translated_even_in_dev(self, integration_db):
        """Deprecated field translation works in dev mode (always active)."""
        from tests.harness.product import ProductEnv

        with ProductEnv(tenant_id=TENANT_ID) as env:
            _create_tenant_with_product()
            # brand_manifest is translated to brand — this is a known field
            # after translation, so TypeAdapter accepts it
            result = env.call_mcp(
                brand_manifest="https://acme.com/.well-known/brand.json",
                brief="test ads",
            )
            assert result is not None


class TestMcpProductionMode:
    """Production mode: unknown fields stripped, type errors retried."""

    def test_unknown_field_stripped(self, integration_db):
        """Production mode: unknown field stripped, request succeeds."""
        from tests.harness.product import ProductEnv

        with ProductEnv(tenant_id=TENANT_ID) as env:
            _create_tenant_with_product()
            with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
                result = env.call_mcp(brief="test ads", nonsense_field="bar")
            assert result is not None

    def test_deprecated_translated_unknown_stripped(self, integration_db):
        """Production: deprecated field translated + unknown field stripped."""
        from tests.harness.product import ProductEnv

        with ProductEnv(tenant_id=TENANT_ID) as env:
            _create_tenant_with_product()
            with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
                result = env.call_mcp(
                    brand_manifest="https://acme.com/.well-known/brand.json",
                    brief="test ads",
                    bogus_param=123,
                )
            assert result is not None
