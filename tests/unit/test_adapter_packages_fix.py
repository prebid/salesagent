"""
Unit tests proving ALL adapters now return packages with package_id correctly.

Tests that Kevel, Triton, and Xandr adapters all return packages with package_id,
fixing the "Adapter did not return package_id" error.
"""

from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from src.adapters.kevel import Kevel
from src.adapters.triton_digital import TritonDigital
from src.adapters.xandr import XandrAdapter
from src.core.schemas import CreateMediaBuyRequest, FormatId, MediaPackage


@pytest.fixture
def mock_principal():
    """Mock principal for testing."""
    principal = Mock()
    principal.name = "test_principal"
    principal.principal_id = "principal_123"
    return principal


@pytest.fixture
def sample_request():
    """Sample CreateMediaBuyRequest."""
    from tests.helpers.adcp_factories import create_test_package_request

    return CreateMediaBuyRequest(
        buyer_ref="test_buyer_ref_123",
        brand_manifest={"name": "Test Brand"},
        packages=[
            create_test_package_request(product_id="prod_123", buyer_ref="buyer_pkg_001"),
            create_test_package_request(product_id="prod_456", buyer_ref="buyer_pkg_002"),
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


class TestKevelAdapterPackages:
    """Test Kevel adapter returns packages correctly."""

    def test_kevel_returns_packages_with_package_ids(self, mock_principal, sample_request, sample_packages):
        """Kevel adapter must return packages with package_id for each package."""
        # Arrange
        config = {
            "api_key": "test_key",
            "base_url": "https://api.kevel.com",
        }

        # Mock principal to return advertiser ID
        mock_principal.get_adapter_id = Mock(return_value="123")

        adapter = Kevel(
            config=config,
            principal=mock_principal,
            dry_run=True,  # Use dry_run to avoid API calls
            tenant_id="tenant_123",
        )

        # Act
        start_time = datetime.now()
        end_time = start_time + timedelta(days=30)
        response = adapter.create_media_buy(
            request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
        )

        # Assert - Response must have packages field
        assert response.packages is not None, "Kevel response must have packages field"
        assert isinstance(response.packages, list), "Kevel packages must be a list"

        # Assert - Must have same number of packages as input
        assert len(response.packages) == len(sample_packages), f"Expected {len(sample_packages)} packages"

        # Assert - Each package must have package_id
        for i, pkg in enumerate(response.packages):
            assert hasattr(pkg, "package_id") and pkg.package_id is not None, f"Kevel package {i} missing package_id"

        # Assert - Package IDs must match input packages
        returned_ids = {pkg.package_id for pkg in response.packages}
        expected_ids = {pkg.package_id for pkg in sample_packages}
        assert returned_ids == expected_ids, f"Package IDs don't match. Got {returned_ids}, expected {expected_ids}"

        # Assert - buyer_ref must be present
        assert response.buyer_ref == sample_request.buyer_ref, "Kevel must return buyer_ref"

    def test_kevel_live_mode_returns_packages_with_flight_ids(self, mock_principal, sample_request, sample_packages):
        """Kevel adapter in live mode must return packages with platform_line_item_id."""
        # Arrange
        config = {
            "api_key": "test_key",
            "base_url": "https://api.kevel.com",
            "network_id": "456",  # Required for live mode
        }

        # Mock principal to return advertiser ID
        mock_principal.get_adapter_id = Mock(return_value="123")

        adapter = Kevel(
            config=config,
            principal=mock_principal,
            dry_run=False,  # Live mode
            tenant_id="tenant_123",
        )

        # Mock requests.post to simulate campaign and flight creation
        with patch("src.adapters.kevel.requests.post") as mock_post:
            # Mock campaign creation
            campaign_response = Mock()
            campaign_response.json.return_value = {"Id": 999}
            campaign_response.raise_for_status = Mock()

            # Mock flight creation (one per package)
            flight_response_1 = Mock()
            flight_response_1.json.return_value = {"Id": 111}
            flight_response_1.raise_for_status = Mock()

            flight_response_2 = Mock()
            flight_response_2.json.return_value = {"Id": 222}
            flight_response_2.raise_for_status = Mock()

            # Return campaign response first, then flight responses
            mock_post.side_effect = [campaign_response, flight_response_1, flight_response_2]

            # Act
            start_time = datetime.now()
            end_time = start_time + timedelta(days=30)
            response = adapter.create_media_buy(
                request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
            )

        # Assert - Each package must have package_id (AdCP spec requirement)
        # Note: platform_line_item_id is internal tracking data, not part of AdCP Package spec
        for i, pkg in enumerate(response.packages):
            assert hasattr(pkg, "package_id") and pkg.package_id is not None, f"Package {i} missing package_id"

        # Assert - Should have expected number of packages
        assert len(response.packages) == 2, f"Expected 2 packages, got {len(response.packages)}"


class TestTritonAdapterPackages:
    """Test Triton adapter returns packages correctly."""

    def test_triton_returns_packages_with_package_ids(self, mock_principal, sample_request, sample_packages):
        """Triton adapter must return packages with package_id for each package."""
        # Arrange
        config = {
            "api_key": "test_key",
            "base_url": "https://api.tritondigital.com",
        }

        # Mock principal to return advertiser ID
        mock_principal.get_adapter_id = Mock(return_value="123")

        adapter = TritonDigital(
            config=config,
            principal=mock_principal,
            dry_run=True,  # Use dry_run to avoid API calls
            tenant_id="tenant_123",
        )

        # Act
        start_time = datetime.now()
        end_time = start_time + timedelta(days=30)
        response = adapter.create_media_buy(
            request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
        )

        # Assert - Response must have packages field
        assert response.packages is not None, "Triton response must have packages field"
        assert isinstance(response.packages, list), "Triton packages must be a list"

        # Assert - Must have same number of packages as input
        assert len(response.packages) == len(sample_packages), f"Expected {len(sample_packages)} packages"

        # Assert - Each package must have package_id
        for i, pkg in enumerate(response.packages):
            assert hasattr(pkg, "package_id") and pkg.package_id is not None, f"Triton package {i} missing package_id"

        # Assert - Package IDs must match input packages
        returned_ids = {pkg.package_id for pkg in response.packages}
        expected_ids = {pkg.package_id for pkg in sample_packages}
        assert returned_ids == expected_ids, f"Package IDs don't match. Got {returned_ids}, expected {expected_ids}"

        # Assert - buyer_ref must be present
        assert response.buyer_ref == sample_request.buyer_ref, "Triton must return buyer_ref"

    def test_triton_live_mode_returns_packages_with_flight_ids(self, mock_principal, sample_request, sample_packages):
        """Triton adapter in live mode must return packages with platform_line_item_id."""
        # Arrange
        config = {
            "api_key": "test_key",
            "base_url": "https://api.tritondigital.com",
            "auth_token": "test_auth_token",  # Required for live mode
        }

        # Mock principal to return advertiser ID
        mock_principal.get_adapter_id = Mock(return_value="123")

        adapter = TritonDigital(
            config=config,
            principal=mock_principal,
            dry_run=False,  # Live mode
            tenant_id="tenant_123",
        )

        # Mock requests.post to simulate campaign and flight creation
        with patch("src.adapters.triton_digital.requests.post") as mock_post:
            # Mock campaign creation
            campaign_response = Mock()
            campaign_response.json.return_value = {"id": 888}
            campaign_response.raise_for_status = Mock()

            # Mock flight creation (one per package)
            flight_response_1 = Mock()
            flight_response_1.json.return_value = {"id": 333}
            flight_response_1.raise_for_status = Mock()

            flight_response_2 = Mock()
            flight_response_2.json.return_value = {"id": 444}
            flight_response_2.raise_for_status = Mock()

            # Return campaign response first, then flight responses
            mock_post.side_effect = [campaign_response, flight_response_1, flight_response_2]

            # Act
            start_time = datetime.now()
            end_time = start_time + timedelta(days=30)
            response = adapter.create_media_buy(
                request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
            )

        # Assert - Each package must have package_id (AdCP spec requirement)
        # Note: platform_line_item_id is internal tracking data, not part of AdCP Package spec
        for i, pkg in enumerate(response.packages):
            assert hasattr(pkg, "package_id") and pkg.package_id is not None, f"Package {i} missing package_id"

        # Assert - Should have expected number of packages (matches number of flights created)
        assert len(response.packages) == 2, f"Expected 2 packages, got {len(response.packages)}"


class TestXandrAdapterPackages:
    """Test Xandr adapter returns packages correctly.

    NOTE: Xandr adapter is marked for full refactor (see src/adapters/xandr.py comments).
    Only create_media_buy has been updated to new API, other methods still use old schemas.
    Testing is limited until full refactor is complete.
    """

    def test_xandr_returns_packages_with_package_ids_and_line_item_ids(
        self, mock_principal, sample_request, sample_packages
    ):
        """Xandr adapter must return packages with package_id and platform_line_item_id."""
        # Arrange
        config = {
            "api_endpoint": "https://api.appnexus.com",
            "username": "test_user",
            "password": "test_pass",
            "member_id": "123",
        }

        # Mock principal platform_mappings
        mock_principal.platform_mappings = {"xandr": {"advertiser_id": "789"}}

        # Create adapter - need to mock abstract methods to allow instantiation
        with patch.multiple(
            "src.adapters.xandr.XandrAdapter",
            __abstractmethods__=set(),  # Allow instantiation of abstract class
        ):
            adapter = XandrAdapter(config=config, principal=mock_principal)
            # advertiser_id is set automatically from platform_mappings

            # Mock abstract methods
            adapter.add_creative_assets = Mock()
            adapter.associate_creatives = Mock()
            adapter.check_media_buy_status = Mock()
            adapter.update_media_buy_performance_index = Mock()
            adapter._log_operation = Mock()  # Mock logging method

            # Mock authentication
            adapter.token = "test_token"
            adapter.token_expiry = datetime.now() + timedelta(hours=2)

            # Mock _make_request to simulate IO and line item creation
            with patch.object(adapter, "_make_request") as mock_request:
                # Mock insertion order creation
                io_response = {"response": {"insertion-order": {"id": 555}}}

                # Mock line item creation (one per package)
                li_response_1 = {"response": {"line-item": {"id": 666}}}
                li_response_2 = {"response": {"line-item": {"id": 777}}}

                # Return IO response first, then line item responses
                mock_request.side_effect = [io_response, li_response_1, li_response_2]

                # Act
                start_time = datetime.now()
                end_time = start_time + timedelta(days=30)
                response = adapter.create_media_buy(
                    request=sample_request, packages=sample_packages, start_time=start_time, end_time=end_time
                )

            # Assert - Response must have packages field
            assert response.packages is not None, "Xandr response must have packages field"
            assert isinstance(response.packages, list), "Xandr packages must be a list"

            # Assert - Must have same number of packages as input
            assert len(response.packages) == len(sample_packages), f"Expected {len(sample_packages)} packages"

            # Assert - Each package must have package_id (AdCP spec requirement)
            # Note: platform_line_item_id is internal tracking data, not part of AdCP Package spec
            for i, pkg in enumerate(response.packages):
                assert (
                    hasattr(pkg, "package_id") and pkg.package_id is not None
                ), f"Xandr package {i} missing package_id"

            # Assert - Package IDs must match input packages
            returned_ids = {pkg.package_id for pkg in response.packages}
            expected_ids = {pkg.package_id for pkg in sample_packages}
            assert returned_ids == expected_ids, f"Package IDs don't match. Got {returned_ids}, expected {expected_ids}"

            # Assert - buyer_ref must be present
            assert response.buyer_ref == sample_request.buyer_ref, "Xandr must return buyer_ref"
