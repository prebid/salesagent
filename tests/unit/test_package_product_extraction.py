"""Test Package product extraction using product_id field per AdCP spec.

Per AdCP specification, packages use product_id (singular) field, not products (plural).
This test verifies that get_product_ids() correctly extracts product IDs from packages.
"""

from unittest.mock import Mock

from src.core.schemas import CreateMediaBuyRequest, PackageRequest


class TestPackageProductExtraction:
    """Test get_product_ids() extracts product_id per AdCP spec."""

    def test_get_product_ids_with_single_product_id(self):
        """Test extraction from product_id field (AdCP spec compliant)."""
        # Per AdCP v2.2.0: budget removed from top-level (now at package level)
        req = CreateMediaBuyRequest(
            brand_manifest={"name": "Test"},
            buyer_ref="test1",
            po_number="PO-001",
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            packages=[
                PackageRequest(buyer_ref="pkg1", product_id="prod1", budget=1000.0, pricing_option_id="test_pricing")
            ],
        )

        product_ids = req.get_product_ids()
        assert product_ids == ["prod1"]
        assert len(product_ids) == 1

    def test_get_product_ids_with_multiple_packages(self):
        """Test extraction from multiple packages."""
        req = CreateMediaBuyRequest(
            brand_manifest={"name": "Test"},
            buyer_ref="test2",
            po_number="PO-002",
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            packages=[
                PackageRequest(buyer_ref="pkg1", product_id="prod1", budget=1000.0, pricing_option_id="test_pricing"),
                PackageRequest(buyer_ref="pkg2", product_id="prod2", budget=1000.0, pricing_option_id="test_pricing"),
                PackageRequest(buyer_ref="pkg3", product_id="prod3", budget=1000.0, pricing_option_id="test_pricing"),
            ],
        )

        product_ids = req.get_product_ids()
        assert product_ids == ["prod1", "prod2", "prod3"]
        assert len(product_ids) == 3

    def test_get_product_ids_with_empty_package(self):
        """Test extraction from package with no product_id."""
        # Use Mock for edge case where package has no product_id
        mock_package = Mock(spec=PackageRequest)
        mock_package.product_id = None
        mock_package.products = None

        req = CreateMediaBuyRequest(
            brand_manifest={"name": "Test"},
            buyer_ref="test3",
            po_number="PO-003",
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            packages=[
                PackageRequest(buyer_ref="pkg1", product_id="dummy", budget=1000.0, pricing_option_id="test_pricing")
            ],
        )
        # Manually set packages to mock for edge case testing
        req.packages = [mock_package]

        product_ids = req.get_product_ids()
        assert product_ids == []

    def test_get_product_ids_fallback_to_legacy_product_ids(self):
        """Test fallback to legacy product_ids field when no packages."""
        req = CreateMediaBuyRequest(
            brand_manifest={"name": "Test"},
            buyer_ref="test4",
            po_number="PO-004",
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            product_ids=["legacy1", "legacy2"],
        )

        product_ids = req.get_product_ids()
        assert product_ids == ["legacy1", "legacy2"]

    def test_get_product_ids_packages_override_legacy(self):
        """Test that packages take precedence over legacy product_ids."""
        req = CreateMediaBuyRequest(
            brand_manifest={"name": "Test"},
            buyer_ref="test5",
            po_number="PO-005",
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            packages=[
                PackageRequest(buyer_ref="pkg1", product_id="prod1", budget=1000.0, pricing_option_id="test_pricing")
            ],
            product_ids=["legacy1", "legacy2"],  # Should be ignored
        )

        product_ids = req.get_product_ids()
        # Should get from packages, not legacy product_ids
        assert product_ids == ["prod1"]
        assert "legacy1" not in product_ids
        assert "legacy2" not in product_ids

    def test_get_product_ids_skips_packages_without_product_id(self):
        """Test that packages without product_id are skipped."""
        # Create valid packages
        pkg1 = PackageRequest(buyer_ref="pkg1", product_id="prod1", budget=1000.0, pricing_option_id="test_pricing")
        pkg3 = PackageRequest(buyer_ref="pkg3", product_id="prod3", budget=1000.0, pricing_option_id="test_pricing")

        # Mock package without product_id for edge case testing
        mock_pkg2 = Mock(spec=PackageRequest)
        mock_pkg2.product_id = None
        mock_pkg2.products = None

        req = CreateMediaBuyRequest(
            brand_manifest={"name": "Test"},
            buyer_ref="test6",
            po_number="PO-006",
            start_time="2025-02-15T00:00:00Z",
            end_time="2025-02-28T23:59:59Z",
            packages=[pkg1],
        )
        # Manually set packages to include mock for edge case testing
        req.packages = [pkg1, mock_pkg2, pkg3]

        product_ids = req.get_product_ids()
        assert product_ids == ["prod1", "prod3"]
        assert len(product_ids) == 2
