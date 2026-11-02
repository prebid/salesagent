"""Unit tests for Broadstreet Placement Manager.

A placement is what makes an advertisement serve in a zone, and it exists only once
Broadstreet has been called — the manager's dry-run branch, which counted placements it
never created, is gone. So the vendor is what these tests stand in for.
"""

import pytest

from src.adapters.broadstreet.managers.placements import (
    BroadstreetPlacementManager,
    PlacementInfo,
)
from tests.helpers.broadstreet_client import stub_broadstreet_client


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
    def client(self):
        """A stand-in vendor handing back a distinct id per placement."""
        return stub_broadstreet_client()

    @pytest.fixture
    def manager(self, client):
        """Create a placement manager over the stand-in vendor."""
        return BroadstreetPlacementManager(
            client=client,
            advertiser_id="adv_123",
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

    def test_create_placements(self, manager, client):
        """One placement per (zone, advertisement) pair — the cross product, in Broadstreet.

        Every ad must be placed in every zone the package targets, or the buy under-delivers
        silently: an ad with no placement in a zone simply never serves there. So the
        assertion is the set of calls the vendor received, not just how many came back.
        """
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

        assert {
            (call.kwargs["zone_id"], call.kwargs["advertisement_id"]) for call in client.create_placement.mock_calls
        } == {
            ("zone_1", "ad_1"),
            ("zone_1", "ad_2"),
            ("zone_2", "ad_1"),
            ("zone_2", "ad_2"),
        }
        assert all(call.kwargs["campaign_id"] == "camp_1" for call in client.create_placement.mock_calls)
        assert {(r["zone_id"], r["advertisement_id"]) for r in results} == {
            ("zone_1", "ad_1"),
            ("zone_1", "ad_2"),
            ("zone_2", "ad_1"),
            ("zone_2", "ad_2"),
        }

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
