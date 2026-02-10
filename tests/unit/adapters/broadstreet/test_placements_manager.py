"""Unit tests for Broadstreet Placement Manager."""

import pytest

from src.adapters.broadstreet.managers.placements import (
    BroadstreetPlacementManager,
    PlacementInfo,
)


class TestPlacementInfo:
    """Tests for PlacementInfo class."""

    def test_init_minimal(self):
        """Test PlacementInfo with minimal parameters."""
        info = PlacementInfo(
            package_id="pkg_1",
            product_id="prod_1",
            zone_ids=["zone_1", "zone_2"],
        )

        assert info.package_id == "pkg_1"
        assert info.product_id == "prod_1"
        assert info.zone_ids == ["zone_1", "zone_2"]
        assert info.advertisement_ids == []
        assert info.placement_ids == []

    def test_init_full(self):
        """Test PlacementInfo with all parameters."""
        info = PlacementInfo(
            package_id="pkg_1",
            product_id="prod_1",
            zone_ids=["zone_1"],
            advertisement_ids=["ad_1", "ad_2"],
        )

        assert info.advertisement_ids == ["ad_1", "ad_2"]

    def test_to_dict(self):
        """Test PlacementInfo serialization."""
        info = PlacementInfo(
            package_id="pkg_1",
            product_id="prod_1",
            zone_ids=["zone_1"],
        )
        info.placement_ids = ["placement_1"]

        result = info.to_dict()

        assert result["package_id"] == "pkg_1"
        assert result["product_id"] == "prod_1"
        assert result["zone_ids"] == ["zone_1"]
        assert result["placement_ids"] == ["placement_1"]


class TestBroadstreetPlacementManager:
    """Tests for BroadstreetPlacementManager."""

    @pytest.fixture
    def manager(self):
        """Create a placement manager in dry-run mode."""
        return BroadstreetPlacementManager(
            client=None,
            advertiser_id="adv_123",
            dry_run=True,
        )

    def test_register_package(self, manager):
        """Test registering a package."""
        impl_config = {
            "targeted_zone_ids": ["zone_1", "zone_2"],
        }

        info = manager.register_package(
            media_buy_id="mb_1",
            package_id="pkg_1",
            product_id="prod_1",
            impl_config=impl_config,
        )

        assert info.package_id == "pkg_1"
        assert info.product_id == "prod_1"
        assert set(info.zone_ids) == {"zone_1", "zone_2"}

    def test_register_multiple_packages(self, manager):
        """Test registering multiple packages."""
        manager.register_package(
            media_buy_id="mb_1",
            package_id="pkg_1",
            product_id="prod_1",
            impl_config={"targeted_zone_ids": ["zone_1"]},
        )
        manager.register_package(
            media_buy_id="mb_1",
            package_id="pkg_2",
            product_id="prod_2",
            impl_config={"targeted_zone_ids": ["zone_2"]},
        )

        packages = manager.get_all_packages("mb_1")
        assert len(packages) == 2

    def test_get_package_info(self, manager):
        """Test getting package info."""
        manager.register_package(
            media_buy_id="mb_1",
            package_id="pkg_1",
            product_id="prod_1",
            impl_config={"targeted_zone_ids": ["zone_1"]},
        )

        info = manager.get_package_info("mb_1", "pkg_1")
        assert info is not None
        assert info.package_id == "pkg_1"

        # Non-existent package
        info = manager.get_package_info("mb_1", "pkg_nonexistent")
        assert info is None

    def test_get_package_info_wrong_media_buy(self, manager):
        """Test getting package info from wrong media buy."""
        manager.register_package(
            media_buy_id="mb_1",
            package_id="pkg_1",
            product_id="prod_1",
            impl_config={"targeted_zone_ids": ["zone_1"]},
        )

        # Wrong media buy ID
        info = manager.get_package_info("mb_wrong", "pkg_1")
        assert info is None

    def test_create_placements_dry_run(self, manager):
        """Test creating placements in dry-run mode."""
        manager.register_package(
            media_buy_id="mb_1",
            package_id="pkg_1",
            product_id="prod_1",
            impl_config={"targeted_zone_ids": ["zone_1", "zone_2"]},
        )

        results = manager.create_placements(
            campaign_id="camp_1",
            media_buy_id="mb_1",
            package_id="pkg_1",
            advertisement_ids=["ad_1", "ad_2"],
        )

        # 2 zones x 2 ads = 4 placements
        assert len(results) == 4

        # Check placement info was updated
        info = manager.get_package_info("mb_1", "pkg_1")
        assert len(info.placement_ids) == 4
        assert set(info.advertisement_ids) == {"ad_1", "ad_2"}

    def test_create_placements_no_registration(self, manager):
        """Test creating placements without prior registration."""
        results = manager.create_placements(
            campaign_id="camp_1",
            media_buy_id="mb_1",
            package_id="pkg_unknown",
            advertisement_ids=["ad_1"],
        )

        assert results == []

    def test_isolation_between_media_buys(self, manager):
        """Test that packages are isolated between media buys."""
        manager.register_package(
            media_buy_id="mb_1",
            package_id="pkg_1",
            product_id="prod_1",
            impl_config={"targeted_zone_ids": ["zone_1"]},
        )
        manager.register_package(
            media_buy_id="mb_2",
            package_id="pkg_1",  # Same package ID, different media buy
            product_id="prod_2",
            impl_config={"targeted_zone_ids": ["zone_2"]},
        )

        # Packages should be independently tracked
        info1 = manager.get_package_info("mb_1", "pkg_1")
        info2 = manager.get_package_info("mb_2", "pkg_1")

        assert info1.product_id == "prod_1"
        assert info2.product_id == "prod_2"
        assert info1.zone_ids == ["zone_1"]
        assert info2.zone_ids == ["zone_2"]
