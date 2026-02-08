"""Unit tests for Broadstreet adapter."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.broadstreet import BroadstreetAdapter
from src.core.schemas import (
    CreateMediaBuySuccess,
    MediaPackage,
)


@pytest.fixture
def mock_principal():
    """Create a mock principal for testing."""
    principal = MagicMock()
    principal.name = "Test Advertiser"
    principal.principal_id = "principal_123"
    principal.platform_mappings = {"broadstreet": {"advertiser_id": "adv_456"}}
    principal.get_adapter_id = lambda adapter: "adv_456" if adapter == "broadstreet" else None
    return principal


@pytest.fixture
def mock_config():
    """Create mock adapter config."""
    return {
        "api_key": "test_api_key",
        "network_id": "net_123",
        "default_advertiser_id": "adv_default",
    }


class TestBroadstreetAdapterInit:
    """Tests for adapter initialization."""

    def test_init_dry_run_mode(self, mock_principal, mock_config):
        """Test adapter initializes in dry-run mode."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        assert adapter.adapter_name == "broadstreet"
        assert adapter.dry_run is True
        assert adapter.client is None
        assert adapter.advertiser_id == "adv_456"

    def test_init_uses_principal_advertiser_id(self, mock_principal, mock_config):
        """Test adapter uses advertiser ID from principal platform_mappings."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        assert adapter.advertiser_id == "adv_456"

    def test_init_falls_back_to_default_advertiser(self, mock_config):
        """Test adapter falls back to default advertiser when principal has none."""
        principal = MagicMock()
        principal.name = "Test Advertiser"
        principal.principal_id = "principal_123"
        principal.platform_mappings = {}
        principal.get_adapter_id = lambda adapter: None

        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=principal,
            dry_run=True,
        )

        assert adapter.advertiser_id == "adv_default"

    def test_init_raises_without_advertiser_id(self):
        """Test adapter raises error when no advertiser ID available and not dry run."""
        principal = MagicMock()
        principal.name = "Test Advertiser"
        principal.principal_id = "principal_123"
        principal.platform_mappings = {}
        principal.get_adapter_id = lambda adapter: None

        config = {"network_id": "net_123", "api_key": "test_key"}

        with pytest.raises(ValueError) as exc_info:
            BroadstreetAdapter(config=config, principal=principal, dry_run=False)

        assert "does not have a Broadstreet advertiser ID" in str(exc_info.value)


class TestBroadstreetAdapterCapabilities:
    """Tests for adapter capability methods."""

    def test_get_supported_pricing_models(self, mock_principal, mock_config):
        """Test adapter reports supported pricing models."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        models = adapter.get_supported_pricing_models()

        assert "cpm" in models
        assert "flat_rate" in models

    def test_get_targeting_capabilities(self, mock_principal, mock_config):
        """Test adapter reports targeting capabilities."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        caps = adapter.get_targeting_capabilities()

        # Broadstreet has limited geo targeting
        assert caps.geo_countries is True
        assert caps.geo_regions is False
        assert caps.nielsen_dma is False

    def test_default_channels(self, mock_principal, mock_config):
        """Test adapter default channels."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        assert "display" in adapter.default_channels


class TestBroadstreetAdapterCreateMediaBuy:
    """Tests for create_media_buy method."""

    def test_create_media_buy_dry_run(self, mock_principal, mock_config):
        """Test creating media buy in dry-run mode."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(days=30)

        # Create a minimal valid request using MagicMock to avoid schema complexity
        request = MagicMock()
        request.buyer_ref = "buyer_123"
        request.po_number = "PO-001"

        # Create package with implementation config
        package = MagicMock(spec=MediaPackage)
        package.package_id = "pkg_1"
        package.product_id = "prod_1"
        package.name = "Test Package"
        package.budget = 10000
        package.impressions = 100000
        package.buyer_ref = "buyer_pkg_1"
        package.implementation_config = {
            "targeted_zone_ids": ["zone_1", "zone_2"],
            "automation_mode": "automatic",  # Skip workflow for this test
        }

        result = adapter.create_media_buy(
            request=request,
            packages=[package],
            start_time=start_time,
            end_time=end_time,
        )

        assert isinstance(result, CreateMediaBuySuccess)
        assert result.buyer_ref == "buyer_123"
        assert result.media_buy_id.startswith("bs_")
        assert len(result.packages) == 1
        assert result.packages[0].package_id == "pkg_1"

    def test_create_media_buy_fails_without_zones(self, mock_principal, mock_config):
        """Test create_media_buy fails when no zones configured."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(days=30)

        # Create a minimal valid request using MagicMock
        request = MagicMock()
        request.buyer_ref = "buyer_123"
        request.po_number = "PO-001"

        # Package without zones
        package = MagicMock(spec=MediaPackage)
        package.package_id = "pkg_1"
        package.product_id = "prod_1"
        package.name = "Test Package"
        package.budget = 10000
        package.impressions = 100000
        package.buyer_ref = "buyer_pkg_1"
        package.implementation_config = {}  # No zones

        result = adapter.create_media_buy(
            request=request,
            packages=[package],
            start_time=start_time,
            end_time=end_time,
        )

        # Should fail with error
        from src.core.schemas import CreateMediaBuyError

        assert isinstance(result, CreateMediaBuyError)
        assert any("no_zones" in str(err.code).lower() for err in result.errors)


class TestBroadstreetAdapterCreatives:
    """Tests for creative management methods."""

    def test_add_creative_assets_dry_run(self, mock_principal, mock_config):
        """Test adding creative assets in dry-run mode."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        assets = [
            {
                "creative_id": "creative_1",
                "name": "Test Banner",
                "format": "display",
                "media_url": "https://example.com/banner.jpg",
            },
            {
                "creative_id": "creative_2",
                "name": "Test HTML",
                "format": "html",
                "html": "<div>Test Ad</div>",
            },
        ]

        results = adapter.add_creative_assets(
            media_buy_id="bs_12345",
            assets=assets,
            today=datetime.now(UTC),
        )

        assert len(results) == 2
        assert all(r.status == "approved" for r in results)

    def test_associate_creatives_dry_run(self, mock_principal, mock_config):
        """Test associating creatives in dry-run mode."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        results = adapter.associate_creatives(
            line_item_ids=["zone_1", "zone_2"],
            platform_creative_ids=["ad_1", "ad_2"],
        )

        assert len(results) == 4  # 2 zones x 2 creatives
        assert all(r["status"] == "success" for r in results)


def _make_mock_db_package(package_id="pkg_1", media_buy_id="bs_12345", ad_ids=None):
    """Create a mock DB MediaPackage with package_config."""
    pkg = MagicMock()
    pkg.package_id = package_id
    pkg.media_buy_id = media_buy_id
    pkg.package_config = {
        "broadstreet_advertisement_ids": ad_ids or ["ad_100", "ad_200"],
    }
    return pkg


@contextmanager
def _mock_db_session(packages):
    """Context manager that mocks get_db_session returning given packages."""
    mock_session = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = packages
    mock_scalars.first.return_value = packages[0] if packages else None
    mock_session.scalars.return_value = mock_scalars

    @contextmanager
    def fake_get_db_session():
        yield mock_session

    with patch("src.core.database.database_session.get_db_session", fake_get_db_session):
        yield mock_session


class TestBroadstreetAdapterUpdates:
    """Tests for update methods."""

    def test_update_media_buy_pause_dry_run(self, mock_principal, mock_config):
        """Test pausing media buy in dry-run mode queries DB and returns success."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        db_pkgs = [_make_mock_db_package()]

        with _mock_db_session(db_pkgs):
            result = adapter.update_media_buy(
                media_buy_id="bs_12345",
                buyer_ref="buyer_123",
                action="pause_media_buy",
                package_id=None,
                budget=None,
                today=datetime.now(UTC),
            )

        from src.core.schemas import UpdateMediaBuySuccess

        assert isinstance(result, UpdateMediaBuySuccess)
        assert len(result.affected_packages) == 1
        assert result.affected_packages[0].paused is True

    def test_update_media_buy_resume_dry_run(self, mock_principal, mock_config):
        """Test resuming media buy in dry-run mode queries DB and returns success."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        db_pkgs = [_make_mock_db_package()]

        with _mock_db_session(db_pkgs):
            result = adapter.update_media_buy(
                media_buy_id="bs_12345",
                buyer_ref="buyer_123",
                action="resume_media_buy",
                package_id=None,
                budget=None,
                today=datetime.now(UTC),
            )

        from src.core.schemas import UpdateMediaBuySuccess

        assert isinstance(result, UpdateMediaBuySuccess)
        assert len(result.affected_packages) == 1
        assert result.affected_packages[0].paused is False

    def test_update_media_buy_pause_no_packages(self, mock_principal, mock_config):
        """Test pause returns error when no packages found in DB."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        with _mock_db_session([]):
            result = adapter.update_media_buy(
                media_buy_id="bs_12345",
                buyer_ref="buyer_123",
                action="pause_media_buy",
                package_id=None,
                budget=None,
                today=datetime.now(UTC),
            )

        from src.core.schemas import UpdateMediaBuyError

        assert isinstance(result, UpdateMediaBuyError)
        assert any("no_packages_found" in str(err.code) for err in result.errors)

    def test_update_media_buy_pause_package_dry_run(self, mock_principal, mock_config):
        """Test pausing a single package in dry-run mode."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        db_pkgs = [_make_mock_db_package(package_id="pkg_1")]

        with _mock_db_session(db_pkgs):
            result = adapter.update_media_buy(
                media_buy_id="bs_12345",
                buyer_ref="buyer_123",
                action="pause_package",
                package_id="pkg_1",
                budget=None,
                today=datetime.now(UTC),
            )

        from src.core.schemas import UpdateMediaBuySuccess

        assert isinstance(result, UpdateMediaBuySuccess)
        assert result.affected_packages[0].package_id == "pkg_1"
        assert result.affected_packages[0].paused is True

    def test_update_media_buy_unsupported_action(self, mock_principal, mock_config):
        """Test update with unsupported action returns error without DB call."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        result = adapter.update_media_buy(
            media_buy_id="bs_12345",
            buyer_ref="buyer_123",
            action="unsupported_action",
            package_id=None,
            budget=None,
            today=datetime.now(UTC),
        )

        from src.core.schemas import UpdateMediaBuyError

        assert isinstance(result, UpdateMediaBuyError)
        assert any("unsupported_action" in str(err.code) for err in result.errors)

    def test_check_media_buy_status_dry_run(self, mock_principal, mock_config):
        """Test checking media buy status in dry-run mode."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        result = adapter.check_media_buy_status(
            media_buy_id="bs_12345",
            today=datetime.now(UTC),
        )

        assert result.media_buy_id == "bs_12345"
        assert result.status == "active"


class TestBroadstreetAdapterDelivery:
    """Tests for delivery reporting."""

    def test_get_media_buy_delivery_dry_run(self, mock_principal, mock_config):
        """Test getting delivery data in dry-run mode."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        from src.core.schemas import ReportingPeriod

        date_range = ReportingPeriod(
            start=(datetime.now(UTC) - timedelta(days=7)).isoformat(),
            end=datetime.now(UTC).isoformat(),
        )

        result = adapter.get_media_buy_delivery(
            media_buy_id="bs_12345",
            date_range=date_range,
            today=datetime.now(UTC),
        )

        assert result.media_buy_id == "bs_12345"
        assert result.totals is not None
        # Dry run should return simulated data
        assert result.totals.impressions >= 0


class TestBroadstreetAdapterInventory:
    """Tests for inventory operations."""

    @pytest.mark.asyncio
    async def test_get_available_inventory_dry_run(self, mock_principal, mock_config):
        """Test getting available inventory in dry-run mode."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        result = await adapter.get_available_inventory()

        assert "zones" in result
        assert len(result["zones"]) > 0
        assert "creative_specs" in result


class TestBroadstreetAdapterCreativeFormats:
    """Tests for creative format discovery (Broadstreet as creative agent)."""

    def test_get_creative_formats_returns_templates(self, mock_principal, mock_config):
        """Test that get_creative_formats returns Broadstreet templates as AdCP formats."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        formats = adapter.get_creative_formats()

        # Should return all template types
        assert len(formats) > 0

        # Check for cube_3d template
        cube_format = next((f for f in formats if "cube_3d" in f["format_id"]["id"]), None)
        assert cube_format is not None
        assert cube_format["name"] == "Amazing 3D Cube Gallery"
        assert cube_format["type"] == "display"

        # Cube should have 6 required face images
        required_assets = [a for a in cube_format["assets"] if a["required"]]
        assert len(required_assets) == 6
        assert all("image" in a["asset_id"] for a in required_assets)

    def test_get_creative_formats_includes_youtube(self, mock_principal, mock_config):
        """Test YouTube video format is included."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        formats = adapter.get_creative_formats()

        youtube_format = next((f for f in formats if "youtube" in f["format_id"]["id"]), None)
        assert youtube_format is not None
        assert youtube_format["name"] == "YouTube Video with Text"

        # YouTube requires youtube_url asset
        required = [a for a in youtube_format["assets"] if a["required"]]
        assert any(a["asset_id"] == "youtube_url" for a in required)

    def test_get_creative_formats_asset_types(self, mock_principal, mock_config):
        """Test that asset types are correctly inferred."""
        adapter = BroadstreetAdapter(
            config=mock_config,
            principal=mock_principal,
            dry_run=True,
        )

        formats = adapter.get_creative_formats()
        cube_format = next((f for f in formats if "cube_3d" in f["format_id"]["id"]), None)

        # Image assets should have type "image"
        front_image = next((a for a in cube_format["assets"] if a["asset_id"] == "front_image"), None)
        assert front_image["asset_type"] == "image"

        # Caption assets should have type "text"
        front_caption = next((a for a in cube_format["assets"] if a["asset_id"] == "front_caption"), None)
        assert front_caption["asset_type"] == "text"

        # Click URL should have type "url"
        click_url = next((a for a in cube_format["assets"] if a["asset_id"] == "click_url"), None)
        assert click_url["asset_type"] == "url"
