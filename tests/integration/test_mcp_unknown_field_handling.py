"""Integration tests for transport compat handling (MCP + REST).

Environment-aware: dev mode rejects unknown fields (fail loudly),
production mode strips them (forward compatible).

Deprecated field translation works in both modes on all transports.
"""

import os
from unittest.mock import patch

import pytest

from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.transport import Transport

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

TENANT_ID = "mcptest"


def _create_tenant_with_product():
    """Create minimal tenant with a product inside an active env session.

    Includes a Principal row matching ProductEnv's default principal_id
    ("test_principal") — the harness's credential() reads its token from that
    row and presents none when it is absent, so a real row is
    required for these calls to actually authenticate rather than fall
    through to the tenant's default require_auth brand_manifest_policy gate.
    """
    tenant = TenantFactory(tenant_id=TENANT_ID)
    PrincipalFactory(tenant=tenant, principal_id="test_principal")
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

    def test_unknown_field_rejected(self, integration_db):
        """Dev mode: unknown field fails loudly for schema drift detection.

        INVALID_REQUEST, not VALIDATION_ERROR. 3.1/enums/error-code.json splits the two by
        WHERE the rule lives: INVALID_REQUEST is "violates schema constraints",
        VALIDATION_ERROR is for business rules "beyond schema validation". A field the
        schema does not declare is rejected BY the schema (extra="forbid" in dev), so it is
        the former -- there is no business rule involved to be beyond.

        Both channels core/error.json defines for "which field", not ``field`` alone.
        ``issues[]`` is the primary one (RFC 6901 ``pointer`` plus the JSON Schema
        ``keyword`` that rejected the payload), and ``field`` is the JSONPath-lite
        dual-write the pin makes a MUST when ``issues`` is present. Grading ``field`` by
        itself would pass against a hand-set string with no issue behind it, which is the
        shape a buyer cannot machine-read.
        """
        from tests.harness.product import ProductEnv

        with ProductEnv(tenant_id=TENANT_ID) as env:
            _create_tenant_with_product()
            result = env.call_via(Transport.MCP, brief="test ads", nonsense_field="bar")

            assert result.is_error
            result.assert_wire_error(
                "INVALID_REQUEST",
                recovery="correctable",
                field="nonsense_field",
                issues=[{"pointer": "/nonsense_field", "keyword": "additionalProperties"}],
            )
            result.assert_wire_error_is_schema_conformant()

    @pytest.mark.xfail(
        reason="#2218: v2-compat request normalization was deleted with the per-tool wrappers, "
        "so a deprecated alias no longer reaches a DTO field. The redesign is filed with its 11 rules; "
        "this grades the mechanism that was removed, not one that is broken.",
        strict=True,
    )
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


class TestRestCompat:
    """REST transport: RestCompatMiddleware normalizes JSON body before Pydantic."""

    @pytest.mark.xfail(
        reason="#2218: v2-compat request normalization was deleted with the per-tool wrappers, "
        "so a deprecated alias no longer reaches a DTO field. The redesign is filed with its 11 rules; "
        "this grades the mechanism that was removed, not one that is broken.",
        strict=True,
    )
    def test_deprecated_field_translated_via_rest(self, integration_db):
        """brand_manifest in REST body is translated to brand before route handler."""
        from tests.harness.product import ProductEnv
        from tests.harness.transport import Transport

        with ProductEnv(tenant_id=TENANT_ID) as env:
            _create_tenant_with_product()
            result = env.call_via(
                Transport.REST,
                brand_manifest="https://acme.com/.well-known/brand.json",
                brief="test ads",
            )
            assert result.is_success

    def test_known_fields_work_via_rest(self, integration_db):
        """Standard REST call with known fields succeeds."""
        from tests.harness.product import ProductEnv
        from tests.harness.transport import Transport

        with ProductEnv(tenant_id=TENANT_ID) as env:
            _create_tenant_with_product()
            result = env.call_via(Transport.REST, brief="test ads")
            assert result.is_success
