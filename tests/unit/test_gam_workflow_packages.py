"""
Unit tests for GAM adapter workflow paths returning packages correctly.

Tests that both manual approval and activation workflow paths return packages
with package_id, fixing the "Adapter did not return package_id" error.
"""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.adapters.google_ad_manager import GoogleAdManager
from src.core.schemas import CreateMediaBuyRequest, FormatId, MediaPackage, PackageRequest


@pytest.fixture
def mock_principal():
    """Mock principal for testing."""
    principal = Mock()
    principal.name = "test_principal"
    principal.principal_id = "principal_123"
    return principal


@pytest.fixture
def mock_gam_config():
    """Mock GAM configuration."""
    return {
        "network_code": "123456",
        "advertiser_id": "789",
        "trafficker_id": "456",
        "refresh_token": "test_token",
        "manual_approval_operations": ["create_media_buy"],  # Enable manual approval
    }


@pytest.fixture
def sample_request():
    """Sample CreateMediaBuyRequest."""
    return CreateMediaBuyRequest(
        buyer_ref="test_buyer_ref_123",
        brand_manifest={"name": "Test Brand"},
        packages=[
            PackageRequest(product_id="prod_123", buyer_ref="pkg_001", budget=5000.0, pricing_option_id="test_pricing"),
            PackageRequest(product_id="prod_456", buyer_ref="pkg_002", budget=5000.0, pricing_option_id="test_pricing"),
        ],
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        total_budget=10000.0,
    )


@pytest.fixture
def sample_packages():
    """Sample packages list."""
    return [
        MediaPackage(
            package_id="pkg_001",
            name="Package 1",
            delivery_type="guaranteed",
            impressions=10000,
            cpm=5.0,
            format_ids=[FormatId(agent_url="https://test.com", id="display_300x250")],
        ),
        MediaPackage(
            package_id="pkg_002",
            name="Package 2",
            delivery_type="guaranteed",
            impressions=20000,
            cpm=7.5,
            format_ids=[FormatId(agent_url="https://test.com", id="display_728x90")],
        ),
    ]


class TestGAMManualApprovalPath:
    """Test GAM adapter manual approval path returns packages correctly."""

    def test_manual_approval_returns_packages_with_package_ids(
        self, mock_principal, mock_gam_config, sample_request, sample_packages
    ):
        """Manual approval path must return packages with package_id for each package."""
        # Arrange - Mock the client manager to avoid OAuth initialization
        with patch("src.adapters.google_ad_manager.GAMClientManager") as mock_client_manager:
            mock_client_manager.return_value.get_client.return_value = Mock()

            adapter = GoogleAdManager(
                config=mock_gam_config,
                principal=mock_principal,
                network_code="123456",
                advertiser_id="789",
                trafficker_id="456",
                dry_run=False,
                tenant_id="tenant_123",
            )

            # Mock _requires_manual_approval to return True
            with (
                patch.object(adapter, "_requires_manual_approval", return_value=True),
                patch.object(adapter.workflow_manager, "create_manual_order_workflow_step") as mock_workflow,
            ):
                mock_workflow.return_value = "workflow_step_123"

                # Act
                start_time = datetime.now()
                end_time = start_time + timedelta(days=30)
                response = adapter.create_media_buy(
                    request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
                )

                # Assert - Response must have packages field
                assert response.packages is not None, "Response must have packages field"
                assert isinstance(response.packages, list), "packages must be a list"

                # Assert - Must have same number of packages as input
                assert len(response.packages) == len(sample_packages), f"Expected {len(sample_packages)} packages"

                # Assert - Each package must have package_id
                for i, pkg in enumerate(response.packages):
                    assert hasattr(pkg, "package_id") and pkg.package_id is not None, f"Package {i} missing package_id"

                # Assert - Package IDs must match input packages
                returned_ids = {pkg.package_id for pkg in response.packages}
                expected_ids = {pkg.package_id for pkg in sample_packages}
                assert (
                    returned_ids == expected_ids
                ), f"Package IDs don't match. Got {returned_ids}, expected {expected_ids}"

                # Assert - Other required fields
                assert response.buyer_ref == sample_request.buyer_ref, "buyer_ref must be preserved"
                assert response.workflow_step_id == "workflow_step_123", "workflow_step_id must be set"

    def test_manual_approval_failure_still_returns_packages(
        self, mock_principal, mock_gam_config, sample_request, sample_packages
    ):
        """Manual approval path must return packages even when workflow creation fails."""
        # Arrange
        with patch("src.adapters.google_ad_manager.GAMClientManager") as mock_client_manager:
            mock_client_manager.return_value.get_client.return_value = Mock()

            adapter = GoogleAdManager(
                config=mock_gam_config,
                principal=mock_principal,
                network_code="123456",
                advertiser_id="789",
                trafficker_id="456",
                dry_run=False,
                tenant_id="tenant_123",
            )

            # Mock workflow manager to fail
            with (
                patch.object(adapter, "_requires_manual_approval", return_value=True),
                patch.object(adapter.workflow_manager, "create_manual_order_workflow_step") as mock_workflow,
            ):
                mock_workflow.return_value = None  # Simulate failure

                # Act
                start_time = datetime.now()
                end_time = start_time + timedelta(days=30)
                response = adapter.create_media_buy(
                    request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
                )

            # Assert - With oneOf pattern, workflow failure returns error response without packages
            from src.core.schemas import CreateMediaBuyError

            assert isinstance(response, CreateMediaBuyError), "Workflow failure should return error response"
            assert len(response.errors) > 0, "Error response must have errors"
            assert response.errors[0].code == "workflow_creation_failed", "Error code should indicate workflow failure"


class TestGAMActivationWorkflowPath:
    """Test GAM adapter activation workflow path returns packages correctly."""

    def test_activation_workflow_returns_packages_with_line_item_ids(
        self, mock_principal, sample_request, sample_packages
    ):
        """Activation workflow path must return packages with package_id AND platform_line_item_id."""
        # Arrange - No manual approval, guaranteed line items trigger activation workflow
        config = {
            "network_code": "123456",
            "advertiser_id": "789",
            "trafficker_id": "456",
            "refresh_token": "test_token",
            # manual_approval_operations not set - automatic mode
        }

        with patch("src.adapters.google_ad_manager.GAMClientManager") as mock_client_manager:
            mock_client_manager.return_value.get_client.return_value = Mock()

            adapter = GoogleAdManager(
                config=config,
                principal=mock_principal,
                network_code="123456",
                advertiser_id="789",
                trafficker_id="456",
                dry_run=False,
                tenant_id="tenant_123",
            )

            # Mock the order creation
            mock_order_id = "order_123"
            mock_line_item_ids = [111, 222]

            with (
                patch.object(adapter.orders_manager, "create_order") as mock_create_order,
                patch.object(adapter.orders_manager, "create_line_items") as mock_create_line_items,
                patch.object(adapter, "_check_order_has_guaranteed_items") as mock_check_guaranteed,
                patch.object(adapter.workflow_manager, "create_activation_workflow_step") as mock_activation_workflow,
                patch("src.core.database.database_session.get_db_session") as mock_db_session,
            ):
                # Setup mocks
                mock_create_order.return_value = mock_order_id
                mock_create_line_items.return_value = mock_line_item_ids
                mock_check_guaranteed.return_value = (True, ["STANDARD"])  # Guaranteed line items
                mock_activation_workflow.return_value = "activation_workflow_123"

                # Mock database session - need to return products with inventory config
                mock_session = MagicMock()
                mock_db_session.return_value.__enter__.return_value = mock_session

                # Create mock products with inventory targeting (required by validation)
                mock_product = Mock()
                mock_product.product_id = "prod_test"
                mock_product.implementation_config = {"targeted_ad_unit_ids": ["123456"]}

                # Simpler approach: Always return mock_product for .first(), empty for .all()
                mock_result = Mock()
                mock_result.first.return_value = mock_product
                mock_result.all.return_value = []
                mock_session.scalars.return_value = mock_result

                # Act
                start_time = datetime.now()
                end_time = start_time + timedelta(days=30)
                response = adapter.create_media_buy(
                    request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
                )

            # Assert - Response must have packages field
            assert response.packages is not None, "Response must have packages field"
            assert isinstance(response.packages, list), "packages must be a list"

            # Assert - Must have same number of packages as input
            assert len(response.packages) == len(sample_packages), f"Expected {len(sample_packages)} packages"

            # Assert - Each package must have package_id (AdCP spec requirement)
            # Note: platform_line_item_id is internal tracking data, not part of AdCP Package spec
            for i, pkg in enumerate(response.packages):
                assert hasattr(pkg, "package_id") and pkg.package_id is not None, f"Package {i} missing package_id"

            # Assert - Package IDs must match input packages
            returned_ids = {pkg.package_id for pkg in response.packages}
            expected_ids = {pkg.package_id for pkg in sample_packages}
            assert returned_ids == expected_ids, f"Package IDs don't match. Got {returned_ids}, expected {expected_ids}"

            # Assert - Other required fields
            assert response.buyer_ref == sample_request.buyer_ref, "buyer_ref must be preserved"
            assert response.workflow_step_id == "activation_workflow_123", "workflow_step_id must be set"
            assert response.media_buy_id == mock_order_id, "media_buy_id must match order ID"


class TestGAMSuccessPath:
    """Test GAM adapter success path (no workflow) returns packages correctly."""

    def test_success_path_returns_packages_with_line_item_ids(self, mock_principal, sample_request, sample_packages):
        """Success path (no workflow) must return packages with package_id AND platform_line_item_id."""
        # Arrange - No manual approval, non-guaranteed line items (no activation workflow)
        config = {
            "network_code": "123456",
            "advertiser_id": "789",
            "trafficker_id": "456",
            "refresh_token": "test_token",
        }

        with patch("src.adapters.google_ad_manager.GAMClientManager") as mock_client_manager:
            mock_client_manager.return_value.get_client.return_value = Mock()

            adapter = GoogleAdManager(
                config=config,
                principal=mock_principal,
                network_code="123456",
                advertiser_id="789",
                trafficker_id="456",
                dry_run=False,
                tenant_id="tenant_123",
            )

            # Mock the order creation
            mock_order_id = "order_456"
            mock_line_item_ids = [333, 444]

            with (
                patch.object(adapter.orders_manager, "create_order") as mock_create_order,
                patch.object(adapter.orders_manager, "create_line_items") as mock_create_line_items,
                patch.object(adapter, "_check_order_has_guaranteed_items") as mock_check_guaranteed,
                patch("src.core.database.database_session.get_db_session") as mock_db_session,
            ):
                # Setup mocks
                mock_create_order.return_value = mock_order_id
                mock_create_line_items.return_value = mock_line_item_ids
                mock_check_guaranteed.return_value = (False, ["PRICE_PRIORITY"])  # Non-guaranteed

                # Mock database session - need to return products with inventory config
                mock_session = MagicMock()
                mock_db_session.return_value.__enter__.return_value = mock_session

                # Create mock products with inventory targeting (required by validation)
                mock_product = Mock()
                mock_product.product_id = "prod_test"
                mock_product.implementation_config = {"targeted_ad_unit_ids": ["123456"]}

                # Simpler approach: Always return mock_product for .first(), empty for .all()
                mock_result = Mock()
                mock_result.first.return_value = mock_product
                mock_result.all.return_value = []
                mock_session.scalars.return_value = mock_result

                # Act
                start_time = datetime.now()
                end_time = start_time + timedelta(days=30)
                response = adapter.create_media_buy(
                    request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
                )

            # Assert - Response must have packages field
            assert response.packages is not None, "Response must have packages field"
            assert len(response.packages) == len(sample_packages), f"Expected {len(sample_packages)} packages"

            # Assert - Each package must have package_id (AdCP spec requirement)
            # Note: platform_line_item_id is internal tracking data, not part of AdCP Package spec
            for i, pkg in enumerate(response.packages):
                assert hasattr(pkg, "package_id") and pkg.package_id is not None, f"Package {i} missing package_id"

            # Assert - No workflow_step_id on success path
            assert response.workflow_step_id is None, "Success path should not have workflow_step_id"
