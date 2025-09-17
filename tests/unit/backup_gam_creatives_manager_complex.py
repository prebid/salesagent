"""
Simplified unit tests for GAMCreativesManager class.

Focuses on core creative management functionality with minimal mocking
to comply with pre-commit limits.
"""

from unittest.mock import Mock

import pytest

from src.adapters.gam.managers.creatives import GAMCreativesManager
from tests.unit.helpers.gam_mock_factory import GAMClientMockFactory, GAMDataFactory, GAMServiceMockFactory


class TestGAMCreativesManagerCore:
    """Core functionality tests with minimal mocking."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client_manager = GAMClientMockFactory.create_client_manager()
        self.advertiser_id = "123456"

    def test_init_with_valid_parameters(self):
        """Test initialization with valid parameters."""
        creatives_manager = GAMCreativesManager(self.mock_client_manager, self.advertiser_id, dry_run=False)

        assert creatives_manager.client_manager == self.mock_client_manager
        assert creatives_manager.advertiser_id == self.advertiser_id
        assert creatives_manager.dry_run is False

    def test_init_with_dry_run_enabled(self):
        """Test initialization with dry_run enabled."""
        creatives_manager = GAMCreativesManager(self.mock_client_manager, self.advertiser_id, dry_run=True)

        assert creatives_manager.dry_run is True

    def test_create_display_creative_success(self):
        """Test successful display creative creation."""
        mock_creative_service = GAMServiceMockFactory.create_creative_service()
        self.mock_client_manager.get_service.return_value = mock_creative_service

        creatives_manager = GAMCreativesManager(self.mock_client_manager, self.advertiser_id)

        creative_id = creatives_manager.create_display_creative(
            name="Test Banner", snippet="<div>Test Ad</div>", width=300, height=250
        )

        assert creative_id == "13579"  # From mock factory
        self.mock_client_manager.get_service.assert_called_once_with("CreativeService")
        mock_creative_service.createCreatives.assert_called_once()

    def test_get_creative_delegates_to_service(self):
        """Test that get_creative properly delegates to CreativeService."""
        mock_creative_service = Mock()
        mock_creative_data = GAMDataFactory.create_creative_data("12345", "Retrieved Creative")
        mock_creative_service.getCreativesByStatement.return_value.results = [mock_creative_data]
        self.mock_client_manager.get_service.return_value = mock_creative_service

        creatives_manager = GAMCreativesManager(self.mock_client_manager, self.advertiser_id)

        result = creatives_manager.get_creative("12345")

        assert result == mock_creative_data
        mock_creative_service.getCreativesByStatement.assert_called_once()

    def test_get_creative_not_found_returns_none(self):
        """Test that get_creative returns None when creative is not found."""
        mock_creative_service = Mock()
        mock_creative_service.getCreativesByStatement.return_value.results = []
        self.mock_client_manager.get_service.return_value = mock_creative_service

        creatives_manager = GAMCreativesManager(self.mock_client_manager, self.advertiser_id)

        result = creatives_manager.get_creative("nonexistent")

        assert result is None

    def test_create_video_creative_success(self):
        """Test successful video creative creation."""
        mock_creative_service = GAMServiceMockFactory.create_creative_service()
        self.mock_client_manager.get_service.return_value = mock_creative_service

        creatives_manager = GAMCreativesManager(self.mock_client_manager, self.advertiser_id)

        creative_id = creatives_manager.create_video_creative(
            name="Test Video", video_url="https://example.com/video.mp4", duration_ms=30000
        )

        assert creative_id == "13579"  # From mock factory
        mock_creative_service.createCreatives.assert_called_once()

    def test_upload_creative_asset_success(self):
        """Test successful creative asset upload."""
        mock_creative_service = Mock()
        mock_asset_response = {"id": "asset_123", "assetUrl": "https://example.com/uploaded"}
        mock_creative_service.createCreativeAssets.return_value = [mock_asset_response]
        self.mock_client_manager.get_service.return_value = mock_creative_service

        creatives_manager = GAMCreativesManager(self.mock_client_manager, self.advertiser_id)

        asset_url = creatives_manager.upload_creative_asset(
            asset_data=b"fake_image_data", filename="banner.jpg", asset_type="IMAGE"
        )

        assert asset_url == "https://example.com/uploaded"
        mock_creative_service.createCreativeAssets.assert_called_once()

    def test_get_creatives_by_advertiser_success(self):
        """Test successful retrieval of creatives by advertiser."""
        mock_creative_service = Mock()
        mock_creatives = [
            GAMDataFactory.create_creative_data("1", "Creative 1"),
            GAMDataFactory.create_creative_data("2", "Creative 2"),
        ]
        mock_creative_service.getCreativesByStatement.return_value.results = mock_creatives
        self.mock_client_manager.get_service.return_value = mock_creative_service

        creatives_manager = GAMCreativesManager(self.mock_client_manager, self.advertiser_id)

        result = creatives_manager.get_creatives_by_advertiser()

        assert result == mock_creatives
        mock_creative_service.getCreativesByStatement.assert_called_once()

    def test_dry_run_mode_simulates_operations(self):
        """Test that dry-run mode simulates operations without making actual calls."""
        creatives_manager = GAMCreativesManager(self.mock_client_manager, self.advertiser_id, dry_run=True)

        # Dry-run operations should return success without calling services
        creative_id = creatives_manager.create_display_creative(
            name="Dry Run Creative", snippet="<div>Test</div>", width=300, height=250
        )

        # Should return a simulated ID
        assert creative_id is not None
        # Should not call actual service
        self.mock_client_manager.get_service.assert_not_called()


class TestGAMCreativesManagerErrorHandling:
    """Error handling and edge case tests."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client_manager = GAMClientMockFactory.create_client_manager()
        self.advertiser_id = "123456"

    def test_create_creative_service_error_propagates(self):
        """Test that service errors during creative creation are propagated."""
        mock_creative_service = Mock()
        mock_creative_service.createCreatives.side_effect = Exception("GAM API Error")
        self.mock_client_manager.get_service.return_value = mock_creative_service

        creatives_manager = GAMCreativesManager(self.mock_client_manager, self.advertiser_id)

        with pytest.raises(Exception, match="GAM API Error"):
            creatives_manager.create_display_creative(
                name="Failed Creative", snippet="<div>Test</div>", width=300, height=250
            )

    def test_invalid_creative_dimensions_raises_error(self):
        """Test that invalid creative dimensions raise appropriate errors."""
        creatives_manager = GAMCreativesManager(self.mock_client_manager, self.advertiser_id)

        with pytest.raises(ValueError, match="Width and height must be positive"):
            creatives_manager.create_display_creative(
                name="Invalid Dimensions", snippet="<div>Test</div>", width=0, height=250
            )
