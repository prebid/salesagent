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
        assert info.paused is False
        assert info.placement_ids == []

    def test_init_full(self):
        """Test PlacementInfo with all parameters."""
        info = PlacementInfo(
            package_id="pkg_1",
            product_id="prod_1",
            zone_ids=["zone_1"],
            advertisement_ids=["ad_1", "ad_2"],
            paused=True,
        )

        assert info.advertisement_ids == ["ad_1", "ad_2"]
        assert info.paused is True

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
        assert result["paused"] is False


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

    def test_pause_package(self, manager):
        """Test pausing a package."""
        manager.register_package(
            media_buy_id="mb_1",
            package_id="pkg_1",
            product_id="prod_1",
            impl_config={"targeted_zone_ids": ["zone_1"]},
        )

        result = manager.pause_package("mb_1", "pkg_1")
        assert result is True

        info = manager.get_package_info("mb_1", "pkg_1")
        assert info.paused is True

    def test_pause_package_not_found(self, manager):
        """Test pausing non-existent package."""
        result = manager.pause_package("mb_1", "pkg_unknown")
        assert result is False

    def test_resume_package(self, manager):
        """Test resuming a paused package."""
        manager.register_package(
            media_buy_id="mb_1",
            package_id="pkg_1",
            product_id="prod_1",
            impl_config={"targeted_zone_ids": ["zone_1"]},
        )

        # Pause then resume
        manager.pause_package("mb_1", "pkg_1")
        result = manager.resume_package("mb_1", "pkg_1")
        assert result is True

        info = manager.get_package_info("mb_1", "pkg_1")
        assert info.paused is False

    def test_update_package_budget(self, manager):
        """Test updating package budget."""
        manager.register_package(
            media_buy_id="mb_1",
            package_id="pkg_1",
            product_id="prod_1",
            impl_config={"targeted_zone_ids": ["zone_1"]},
        )

        result = manager.update_package_budget("mb_1", "pkg_1", 10000)
        assert result is True

    def test_update_package_budget_not_found(self, manager):
        """Test updating budget for non-existent package."""
        result = manager.update_package_budget("mb_1", "pkg_unknown", 10000)
        assert result is False

    def test_update_package_impressions(self, manager):
        """Test updating package impressions."""
        manager.register_package(
            media_buy_id="mb_1",
            package_id="pkg_1",
            product_id="prod_1",
            impl_config={"targeted_zone_ids": ["zone_1"]},
        )

        result = manager.update_package_impressions("mb_1", "pkg_1", 100000)
        assert result is True

    def test_get_package_status(self, manager):
        """Test getting package status."""
        manager.register_package(
            media_buy_id="mb_1",
            package_id="pkg_1",
            product_id="prod_1",
            impl_config={"targeted_zone_ids": ["zone_1", "zone_2"]},
        )

        status = manager.get_package_status("mb_1", "pkg_1")

        assert status["package_id"] == "pkg_1"
        assert status["product_id"] == "prod_1"
        assert status["status"] == "active"
        assert status["zone_count"] == 2
        assert status["placement_count"] == 0
        assert status["creative_count"] == 0

    def test_get_package_status_paused(self, manager):
        """Test getting status for paused package."""
        manager.register_package(
            media_buy_id="mb_1",
            package_id="pkg_1",
            product_id="prod_1",
            impl_config={"targeted_zone_ids": ["zone_1"]},
        )
        manager.pause_package("mb_1", "pkg_1")

        status = manager.get_package_status("mb_1", "pkg_1")
        assert status["status"] == "paused"

    def test_get_package_status_not_found(self, manager):
        """Test getting status for non-existent package."""
        status = manager.get_package_status("mb_1", "pkg_unknown")
        assert status["status"] == "not_found"

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

        # Pause only in mb_1
        manager.pause_package("mb_1", "pkg_1")

        # mb_1 should be paused
        info1 = manager.get_package_info("mb_1", "pkg_1")
        assert info1.paused is True

        # mb_2 should not be affected
        info2 = manager.get_package_info("mb_2", "pkg_1")
        assert info2.paused is False
