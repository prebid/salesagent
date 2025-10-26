#!/usr/bin/env python3
"""
Unit test for duplicate product validation in media buy packages.

Tests the validation logic that rejects media buy requests where the same
product_id appears in multiple packages.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.core.schemas import Budget, Package


@pytest.mark.requires_db
class TestDuplicateProductValidation:
    """Test that duplicate products in packages are rejected."""

    @pytest.mark.asyncio
    async def test_duplicate_product_in_packages_rejected(self, integration_db):
        """Test that duplicate product_ids in packages are rejected."""
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Create a mock context with auth header
        mock_context = MagicMock()
        mock_context.headers = {"x-adcp-auth": "test_token"}

        # Mock testing context
        mock_testing_ctx = MagicMock()
        mock_testing_ctx.dry_run = False
        mock_testing_ctx.test_session_id = None

        # Mock context manager
        mock_ctx_manager = MagicMock()
        mock_persistent_ctx = MagicMock()
        mock_ctx_manager.get_context.return_value = mock_persistent_ctx

        # Mock tenant for get_principal_from_context return
        mock_tenant = {"tenant_id": "test_tenant", "subdomain": "test", "ad_server": "mock"}

        # Mock the dependencies
        with (
            patch(
                "src.core.tools.media_buy_create.get_principal_id_from_context",
                return_value="test_principal",
            ),
            patch(
                "src.core.tools.media_buy_create.get_current_tenant",
                return_value=mock_tenant,
            ),
            patch(
                "src.core.tools.media_buy_create.get_principal_object",
                return_value=MagicMock(principal_id="test_principal", name="Test Principal"),
            ),
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.tools.media_buy_create.get_testing_context", return_value=mock_testing_ctx),
            patch("src.core.tools.media_buy_create.get_context_manager", return_value=mock_ctx_manager),
        ):
            # Create packages with duplicate product_id
            packages = [
                Package(
                    buyer_ref="pkg_1",
                    product_id="prod_test_1",
                    budget=Budget(total=1000, currency="USD"),
                ),
                Package(
                    buyer_ref="pkg_2",
                    product_id="prod_test_1",  # Same product as pkg_1
                    budget=Budget(total=1500, currency="USD"),
                ),
            ]

            start_time = datetime.now(UTC) + timedelta(hours=1)  # 1 hour in the future
            end_time = start_time + timedelta(days=7)

            # Should return error response about duplicate products
            result = await _create_media_buy_impl(
                buyer_ref="test_media_buy_duplicate",
                brand_manifest={"name": "Test Brand"},
                packages=packages,
                start_time=start_time,
                end_time=end_time,
                budget=Budget(total=2500, currency="USD"),
                context=mock_context,
            )

            # Verify response contains error about duplicate products
            assert result.errors is not None and len(result.errors) > 0, f"Expected errors in response, got: {result}"
            error_msg = result.errors[0].message
            assert "duplicate" in error_msg.lower(), f"Error should mention 'duplicate': {error_msg}"
            assert "prod_test_1" in error_msg, f"Error should mention 'prod_test_1': {error_msg}"
            assert (
                "each product can only be used once" in error_msg.lower()
            ), f"Error should say 'each product can only be used once': {error_msg}"

    @pytest.mark.asyncio
    async def test_multiple_duplicate_products_all_listed(self, integration_db):
        """Test that all duplicate product_ids are listed in error message."""
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Create a mock context with auth header
        mock_context = MagicMock()
        mock_context.headers = {"x-adcp-auth": "test_token"}

        # Mock testing context
        mock_testing_ctx = MagicMock()
        mock_testing_ctx.dry_run = False
        mock_testing_ctx.test_session_id = None

        # Mock context manager
        mock_ctx_manager = MagicMock()
        mock_persistent_ctx = MagicMock()
        mock_ctx_manager.get_context.return_value = mock_persistent_ctx

        # Mock tenant for get_principal_from_context return
        mock_tenant = {"tenant_id": "test_tenant", "subdomain": "test", "ad_server": "mock"}

        # Mock the dependencies
        with (
            patch(
                "src.core.tools.media_buy_create.get_principal_id_from_context",
                return_value="test_principal",
            ),
            patch(
                "src.core.tools.media_buy_create.get_current_tenant",
                return_value=mock_tenant,
            ),
            patch(
                "src.core.tools.media_buy_create.get_principal_object",
                return_value=MagicMock(principal_id="test_principal", name="Test Principal"),
            ),
            patch("src.core.tools.media_buy_create.validate_setup_complete"),
            patch("src.core.tools.media_buy_create.get_testing_context", return_value=mock_testing_ctx),
            patch("src.core.tools.media_buy_create.get_context_manager", return_value=mock_ctx_manager),
        ):
            # Create packages with multiple duplicates
            packages = [
                Package(
                    buyer_ref="pkg_1",
                    product_id="prod_test_1",
                    budget=Budget(total=1000, currency="USD"),
                ),
                Package(
                    buyer_ref="pkg_2",
                    product_id="prod_test_1",  # Duplicate of pkg_1
                    budget=Budget(total=1500, currency="USD"),
                ),
                Package(
                    buyer_ref="pkg_3",
                    product_id="prod_test_2",
                    budget=Budget(total=2000, currency="USD"),
                ),
                Package(
                    buyer_ref="pkg_4",
                    product_id="prod_test_2",  # Duplicate of pkg_3
                    budget=Budget(total=1800, currency="USD"),
                ),
            ]

            start_time = datetime.now(UTC) + timedelta(hours=1)  # 1 hour in the future
            end_time = start_time + timedelta(days=7)

            # Should return error response listing both duplicate products
            result = await _create_media_buy_impl(
                buyer_ref="test_media_buy_multiple_duplicates",
                brand_manifest={"name": "Test Brand"},
                packages=packages,
                start_time=start_time,
                end_time=end_time,
                budget=Budget(total=6300, currency="USD"),
                context=mock_context,
            )

            # Verify both duplicate products are mentioned
            assert result.errors is not None and len(result.errors) > 0, f"Expected errors in response, got: {result}"
            error_msg = result.errors[0].message
            assert "prod_test_1" in error_msg
            assert "prod_test_2" in error_msg

    @pytest.mark.asyncio
    async def test_no_duplicates_validation_passes(self):
        """Test that packages with unique product_ids pass validation."""
        from src.core.tools.media_buy_create import _create_media_buy_impl

        # Mock ALL the dependencies to get past validation
        with (
            patch("src.core.database.database_session.get_db_session") as mock_session_context,
            patch(
                "src.core.tools.media_buy_create.get_current_tenant",
                return_value={"tenant_id": "test_tenant", "subdomain": "test", "ad_server": "mock"},
            ),
            patch("src.core.tools.media_buy_create.activity_feed") as mock_activity,
        ):
            # Create a mock session
            mock_session = MagicMock()
            mock_session_context.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_session_context.return_value.__exit__ = MagicMock(return_value=None)

            # Mock product query to return products
            mock_product1 = MagicMock()
            mock_product1.product_id = "prod_test_1"
            mock_product1.pricing_options = []

            mock_product2 = MagicMock()
            mock_product2.product_id = "prod_test_2"
            mock_product2.pricing_options = []

            # Mock scalars().all() to return products
            mock_scalars = MagicMock()
            mock_scalars.all.return_value = [mock_product1, mock_product2]
            mock_session.scalars.return_value = mock_scalars

            # Mock currency limit query
            mock_session.scalar.return_value = None  # No currency limit found

            # Create packages with different product_ids
            packages = [
                Package(
                    buyer_ref="pkg_1",
                    product_id="prod_test_1",
                    budget=Budget(total=1000, currency="USD"),
                ),
                Package(
                    buyer_ref="pkg_2",
                    product_id="prod_test_2",  # Different product
                    budget=Budget(total=1500, currency="USD"),
                ),
            ]

            start_time = datetime.now(UTC) + timedelta(hours=1)  # 1 hour in the future
            end_time = start_time + timedelta(days=7)

            # Should fail on currency validation (since we didn't set that up)
            # but NOT on duplicate product validation
            with pytest.raises((ValueError, Exception)) as exc_info:
                await _create_media_buy_impl(
                    buyer_ref="test_media_buy_different",
                    brand_manifest={"name": "Test Brand"},
                    packages=packages,
                    start_time=start_time,
                    end_time=end_time,
                    budget=Budget(total=2500, currency="USD"),
                )

            # Should NOT be about duplicate products
            error_msg = str(exc_info.value)
            # If it's about duplicates, the test failed
            if "duplicate" in error_msg.lower() and "product" in error_msg.lower():
                pytest.fail(f"Validation should not fail on duplicate products, but got: {error_msg}")
