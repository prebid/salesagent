"""Regression tests for PR #1071 review findings.

Tests for bugs found during Chris's code review of the AdCP v3.6 upgrade PR.
Each test exercises the actual code path to verify correct behavior.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.core.schemas import GetProductsRequest


class TestDeliveryLoopErrorHandling:
    """salesagent-m06j: single media buy error must not kill entire response.

    The delivery loop should log errors for individual media buys and continue
    processing the rest, returning partial results.
    """

    @pytest.mark.asyncio
    async def test_single_media_buy_error_returns_partial_results(self):
        """When one media buy raises during processing, others still appear in response."""
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.schemas import GetMediaBuyDeliveryRequest
        from src.core.tools.media_buy_delivery import _get_media_buy_delivery_impl

        req = GetMediaBuyDeliveryRequest(
            media_buy_ids=["mb_good", "mb_bad"],
            start_date=(date.today() - timedelta(days=7)).isoformat(),
            end_date=date.today().isoformat(),
        )

        tenant = {
            "tenant_id": "test",
            "brand_manifest_policy": "public",
            "advertising_policy": {},
        }
        identity = ResolvedIdentity(
            principal_id="p1",
            tenant_id="test",
            tenant=tenant,
            protocol="mcp",
        )

        # Create two mock media buys: one good, one that will error
        good_buy = MagicMock()
        good_buy.start_date = date.today() - timedelta(days=5)
        good_buy.end_date = date.today() + timedelta(days=5)
        good_buy.budget = "1000.00"
        good_buy.raw_request = {
            "buyer_ref": "buyer1",
            "packages": [{"package_id": "pkg1", "product_id": "prod1"}],
        }
        good_buy.buyer_ref = "buyer1"

        bad_buy = MagicMock()
        # This will raise when accessed in the loop (e.g., start_date raises)
        type(bad_buy).start_date = property(lambda self: (_ for _ in ()).throw(ValueError("DB corruption")))
        bad_buy.raw_request = None

        target_buys = [("mb_good", good_buy), ("mb_bad", bad_buy)]

        mock_repo = MagicMock()
        mock_repo.get_packages.return_value = []

        mock_uow = MagicMock()
        mock_uow.__enter__ = MagicMock(return_value=mock_uow)
        mock_uow.__exit__ = MagicMock(return_value=False)
        mock_uow.media_buys = mock_repo

        with (
            patch("src.core.tools.media_buy_delivery.get_principal_object", return_value=MagicMock()),
            patch("src.core.tools.media_buy_delivery.get_adapter", return_value=MagicMock()),
            patch("src.core.tools.media_buy_delivery.MediaBuyUoW", return_value=mock_uow),
            patch("src.core.tools.media_buy_delivery._get_target_media_buys", return_value=target_buys),
            patch("src.core.tools.media_buy_delivery._get_pricing_options", return_value={}),
        ):
            response = _get_media_buy_delivery_impl(req, identity)

        # The good media buy should still be in the response
        assert len(response.media_buy_deliveries) >= 1, (
            "A single media buy error killed the entire response — the loop must catch exceptions and continue"
        )
        assert response.media_buy_deliveries[0].media_buy_id == "mb_good"


class TestBrandExtractionFromPydanticModel:
    """salesagent-7bzt: brand domain must be extracted after Pydantic coercion.

    When a buyer provides brand={"domain": "example.com"}, Pydantic coerces it
    to BrandReference. The code must extract domain from the model, not treat
    it as a dict.
    """

    def test_brand_reference_is_not_dict_after_pydantic(self):
        """After Pydantic parsing, req.brand is BrandReference, not dict."""
        req = GetProductsRequest(
            brand={"domain": "example.com"},
            brief="test products",
        )

        assert not isinstance(req.brand, dict), "Pydantic should coerce dict to BrandReference"
        assert hasattr(req.brand, "domain")
        assert req.brand.domain == "example.com"

    @pytest.mark.asyncio
    async def test_require_brand_policy_succeeds_with_brand_domain(self):
        """Tenant with require_brand policy must accept requests that provide brand domain.

        This was broken when isinstance(req.brand, dict) was used — Pydantic
        coerces dict to BrandReference, so the check always returned False,
        offering was always None, and require_brand rejected ALL requests.
        """
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.tools.products import _get_products_impl

        req = GetProductsRequest(
            brand={"domain": "nike.com"},
            brief="Athletic footwear",
        )

        tenant = {
            "tenant_id": "test",
            "brand_manifest_policy": "require_brand",
            "advertising_policy": {},
        }
        identity = ResolvedIdentity(
            principal_id="p1",
            tenant_id="test",
            tenant=tenant,
            protocol="mcp",
        )

        with (
            patch("src.core.tools.products.get_principal_object", return_value=None),
            patch("src.core.tools.products.get_db_session") as mock_db,
        ):
            mock_session = MagicMock()
            mock_result = MagicMock()
            mock_result.unique.return_value.scalars.return_value.all.return_value = []
            mock_session.execute.return_value = mock_result
            mock_db.return_value.__enter__.return_value = mock_session

            # This must NOT raise AdCPAuthorizationError — brand IS provided
            response = await _get_products_impl(req, identity)

        assert response is not None


class TestAuditLogBrandFieldName:
    """salesagent-bff0: audit log must use 'has_brand' key after 3.6 rename.

    In adcp 3.6.0, brand_manifest was renamed to brand. The audit log
    detail key must reflect this.
    """

    @pytest.mark.asyncio
    async def test_audit_log_records_has_brand_not_has_brand_manifest(self):
        """Audit log details dict must contain 'has_brand', not 'has_brand_manifest'."""
        from src.core.resolved_identity import ResolvedIdentity
        from src.core.tools.products import _get_products_impl

        req = GetProductsRequest(
            brand={"domain": "nike.com"},
            brief="Athletic footwear",
        )

        tenant = {
            "tenant_id": "test",
            "brand_manifest_policy": "public",
            "advertising_policy": {},
        }
        identity = ResolvedIdentity(
            principal_id="p1",
            tenant_id="test",
            tenant=tenant,
            protocol="mcp",
        )

        with (
            patch("src.core.tools.products.get_principal_object", return_value=None),
            patch("src.core.tools.products.get_db_session") as mock_db,
            patch("src.core.tools.products.get_audit_logger") as mock_audit_logger,
        ):
            mock_session = MagicMock()
            mock_result = MagicMock()
            mock_result.unique.return_value.scalars.return_value.all.return_value = []
            mock_session.execute.return_value = mock_result
            mock_db.return_value.__enter__.return_value = mock_session

            mock_logger = MagicMock()
            mock_audit_logger.return_value = mock_logger

            await _get_products_impl(req, identity)

        # Verify audit log was called with 'has_brand' key
        mock_logger.log_operation.assert_called_once()
        call_kwargs = mock_logger.log_operation.call_args
        details = call_kwargs.kwargs.get("details") or call_kwargs[1].get("details")

        assert "has_brand" in details, f"Audit log details missing 'has_brand' key: {details}"
        assert "has_brand_manifest" not in details, f"Audit log still uses stale 'has_brand_manifest' key: {details}"
        assert details["has_brand"] is True
