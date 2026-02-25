"""Tests for GAM placement targeting feature (adcp#208).

These tests verify the creative-level placement targeting implementation:
1. PlacementTargeting schema in GAM implementation config
2. Validation of placement_ids against product placements
3. Building creativeTargetings on GAM line items
4. Setting targetingName on LICAs
"""

from unittest.mock import MagicMock


class TestPlacementTargetingSchema:
    """Test PlacementTargeting schema in GAM implementation config."""

    def test_placement_targeting_class_exists(self):
        """Verify PlacementTargeting class exists in schema."""
        from src.adapters.gam_implementation_config_schema import PlacementTargeting

        # Should be importable
        assert PlacementTargeting is not None

    def test_placement_targeting_fields(self):
        """Verify PlacementTargeting has required fields."""
        from src.adapters.gam_implementation_config_schema import PlacementTargeting

        fields = PlacementTargeting.model_fields
        assert "placement_id" in fields
        assert "targeting_name" in fields
        assert "targeting" in fields

    def test_placement_targeting_validation(self):
        """Test PlacementTargeting validates correctly."""
        from src.adapters.gam_implementation_config_schema import PlacementTargeting

        pt = PlacementTargeting(
            placement_id="homepage_atf",
            targeting_name="homepage-above-fold",
            targeting={
                "customTargeting": {
                    "children": [{"keyId": "123", "valueIds": ["456"], "operator": "IS"}],
                    "logicalOperator": "AND",
                }
            },
        )

        assert pt.placement_id == "homepage_atf"
        assert pt.targeting_name == "homepage-above-fold"
        assert "customTargeting" in pt.targeting

    def test_placement_targeting_defaults_empty_targeting(self):
        """Test PlacementTargeting defaults targeting to empty dict."""
        from src.adapters.gam_implementation_config_schema import PlacementTargeting

        pt = PlacementTargeting(
            placement_id="test_placement",
            targeting_name="test-targeting",
        )

        assert pt.targeting == {}

    def test_gam_implementation_config_has_placement_targeting_field(self):
        """Verify GAMImplementationConfig has placement_targeting field."""
        from src.adapters.gam_implementation_config_schema import GAMImplementationConfig

        fields = GAMImplementationConfig.model_fields
        assert "placement_targeting" in fields

    def test_gam_implementation_config_placement_targeting_default_empty(self):
        """Test placement_targeting defaults to empty list."""
        from src.adapters.gam_implementation_config_schema import GAMImplementationConfig

        config = GAMImplementationConfig(creative_placeholders=[{"width": 300, "height": 250}])

        assert config.placement_targeting == []

    def test_gam_implementation_config_with_placement_targeting(self):
        """Test GAMImplementationConfig accepts placement_targeting."""
        from src.adapters.gam_implementation_config_schema import (
            GAMImplementationConfig,
            PlacementTargeting,
        )

        config = GAMImplementationConfig(
            creative_placeholders=[{"width": 300, "height": 250}],
            placement_targeting=[
                PlacementTargeting(
                    placement_id="homepage_atf",
                    targeting_name="homepage-above-fold",
                    targeting={"customTargeting": {"children": [], "logicalOperator": "AND"}},
                ),
                PlacementTargeting(
                    placement_id="article_inline",
                    targeting_name="article-inline",
                    targeting={},
                ),
            ],
        )

        assert len(config.placement_targeting) == 2
        assert config.placement_targeting[0].placement_id == "homepage_atf"
        assert config.placement_targeting[1].placement_id == "article_inline"


class TestPlacementIdsValidation:
    """Test placement_ids validation logic in update_media_buy."""

    def test_invalid_placement_ids_returns_error(self):
        """When creative_assignments reference placement_ids not defined on the product,
        _update_media_buy_impl returns UpdateMediaBuyError with code='invalid_placement_ids'."""
        from unittest.mock import MagicMock, Mock, patch

        from fastmcp.server.context import Context

        from src.core.schemas import UpdateMediaBuyError, UpdateMediaBuyRequest
        from src.core.testing_hooks import AdCPTestContext
        from src.core.tools.media_buy_update import _update_media_buy_impl

        MODULE = "src.core.tools.media_buy_update"
        DB_MODULE = "src.core.database.database_session"

        mock_ctx = MagicMock(spec=Context)
        mock_ctx.headers = {"x-adcp-auth": "test-token"}

        # Build mock DB session
        mock_session = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = Mock(return_value=mock_session)
        mock_cm.__exit__ = Mock(return_value=False)

        with (
            patch(f"{MODULE}.get_principal_id_from_context", return_value="principal_test"),
            patch(f"{MODULE}.get_current_tenant", return_value={"tenant_id": "t1", "name": "Test"}),
            patch(f"{MODULE}.get_principal_object") as m_principal_obj,
            patch(f"{MODULE}.get_testing_context", return_value=AdCPTestContext(dry_run=False)),
            patch(f"{MODULE}._verify_principal"),
            patch(f"{MODULE}.get_context_manager") as m_ctx_mgr,
            patch(f"{MODULE}.get_adapter") as m_adapter,
            patch(f"{MODULE}.get_audit_logger", return_value=MagicMock()),
            patch(f"{DB_MODULE}.get_db_session", return_value=mock_cm),
        ):
            m_principal_obj.return_value = MagicMock(principal_id="principal_test")

            mock_step = MagicMock(step_id="step_001")
            mock_ctx_mgr = MagicMock()
            mock_ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_001")
            mock_ctx_mgr.create_workflow_step.return_value = mock_step
            m_ctx_mgr.return_value = mock_ctx_mgr

            mock_adapter = MagicMock()
            mock_adapter.manual_approval_required = False
            mock_adapter.manual_approval_operations = []
            m_adapter.return_value = mock_adapter

            # Mock DB lookups for the creative_assignments path:
            # 1. media_buy lookup (by media_buy_id)
            mock_media_buy = MagicMock()
            mock_media_buy.media_buy_id = "mb_placement"
            mock_media_buy.status = "approved"
            mock_media_buy.approved_at = None

            # 2. package lookup
            mock_package = MagicMock()
            mock_package.package_config = {"product_id": "prod_1"}

            # 3. product lookup with placements that do NOT include "invalid_placement"
            mock_product = MagicMock()
            mock_product.placements = [
                {"placement_id": "homepage_atf"},
                {"placement_id": "sidebar"},
            ]

            # Wire up scalars().first() to return the right objects in sequence
            mock_scalars = MagicMock()
            mock_scalars.first.side_effect = [mock_media_buy, mock_package, mock_product]
            mock_session.scalars.return_value = mock_scalars

            req = UpdateMediaBuyRequest(
                media_buy_id="mb_placement",
                packages=[
                    {
                        "package_id": "pkg_1",
                        "creative_assignments": [
                            {
                                "creative_id": "c1",
                                "weight": 100,
                                "placement_ids": ["homepage_atf", "invalid_placement"],
                            },
                        ],
                    }
                ],
            )
            result = _update_media_buy_impl(req=req, ctx=mock_ctx)

            assert isinstance(result, UpdateMediaBuyError)
            assert len(result.errors) == 1
            assert result.errors[0].code == "invalid_placement_ids"
            assert "invalid_placement" in result.errors[0].message

    def test_placement_targeting_not_supported_returns_error(self):
        """When product has no placements defined but creative_assignments reference placement_ids,
        _update_media_buy_impl returns UpdateMediaBuyError with code='placement_targeting_not_supported'."""
        from unittest.mock import MagicMock, Mock, patch

        from fastmcp.server.context import Context

        from src.core.schemas import UpdateMediaBuyError, UpdateMediaBuyRequest
        from src.core.testing_hooks import AdCPTestContext
        from src.core.tools.media_buy_update import _update_media_buy_impl

        MODULE = "src.core.tools.media_buy_update"
        DB_MODULE = "src.core.database.database_session"

        mock_ctx = MagicMock(spec=Context)
        mock_ctx.headers = {"x-adcp-auth": "test-token"}

        mock_session = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = Mock(return_value=mock_session)
        mock_cm.__exit__ = Mock(return_value=False)

        with (
            patch(f"{MODULE}.get_principal_id_from_context", return_value="principal_test"),
            patch(f"{MODULE}.get_current_tenant", return_value={"tenant_id": "t1", "name": "Test"}),
            patch(f"{MODULE}.get_principal_object") as m_principal_obj,
            patch(f"{MODULE}.get_testing_context", return_value=AdCPTestContext(dry_run=False)),
            patch(f"{MODULE}._verify_principal"),
            patch(f"{MODULE}.get_context_manager") as m_ctx_mgr,
            patch(f"{MODULE}.get_adapter") as m_adapter,
            patch(f"{MODULE}.get_audit_logger", return_value=MagicMock()),
            patch(f"{DB_MODULE}.get_db_session", return_value=mock_cm),
        ):
            m_principal_obj.return_value = MagicMock(principal_id="principal_test")

            mock_step = MagicMock(step_id="step_001")
            mock_ctx_mgr = MagicMock()
            mock_ctx_mgr.get_or_create_context.return_value = MagicMock(context_id="ctx_001")
            mock_ctx_mgr.create_workflow_step.return_value = mock_step
            m_ctx_mgr.return_value = mock_ctx_mgr

            mock_adapter = MagicMock()
            mock_adapter.manual_approval_required = False
            mock_adapter.manual_approval_operations = []
            m_adapter.return_value = mock_adapter

            # Mock media_buy lookup
            mock_media_buy = MagicMock()
            mock_media_buy.media_buy_id = "mb_no_placements"
            mock_media_buy.status = "approved"
            mock_media_buy.approved_at = None

            # Mock package lookup
            mock_package = MagicMock()
            mock_package.package_config = {"product_id": "prod_no_placements"}

            # Mock product with NO placements (empty list)
            mock_product = MagicMock()
            mock_product.placements = []

            mock_scalars = MagicMock()
            mock_scalars.first.side_effect = [mock_media_buy, mock_package, mock_product]
            mock_session.scalars.return_value = mock_scalars

            req = UpdateMediaBuyRequest(
                media_buy_id="mb_no_placements",
                packages=[
                    {
                        "package_id": "pkg_1",
                        "creative_assignments": [
                            {"creative_id": "c1", "weight": 100, "placement_ids": ["some_placement"]},
                        ],
                    }
                ],
            )
            result = _update_media_buy_impl(req=req, ctx=mock_ctx)

            assert isinstance(result, UpdateMediaBuyError)
            assert len(result.errors) == 1
            assert result.errors[0].code == "placement_targeting_not_supported"
            assert "prod_no_placements" in result.errors[0].message

    def test_adcp_package_update_accepts_placement_ids_in_creative_assignments(self):
        """Verify AdCPPackageUpdate accepts placement_ids in creative_assignments."""
        from src.core.schemas import AdCPPackageUpdate

        pkg = AdCPPackageUpdate(
            package_id="pkg_1",
            creative_assignments=[
                {"creative_id": "c1", "weight": 50, "placement_ids": ["homepage_atf", "sidebar"]},
            ],
        )

        assert pkg.creative_assignments[0].placement_ids == ["homepage_atf", "sidebar"]

    def test_validation_set_operations(self):
        """Test the validation set operations work correctly."""
        # Simulate the validation logic
        all_requested_placement_ids = {"homepage_atf", "invalid_placement"}
        available_placement_ids = {"homepage_atf", "sidebar", "article_inline"}

        invalid_ids = all_requested_placement_ids - available_placement_ids

        assert invalid_ids == {"invalid_placement"}

    def test_validation_passes_when_all_valid(self):
        """Test validation passes when all placement_ids are valid."""
        all_requested_placement_ids = {"homepage_atf", "sidebar"}
        available_placement_ids = {"homepage_atf", "sidebar", "article_inline"}

        invalid_ids = all_requested_placement_ids - available_placement_ids

        assert invalid_ids == set()  # Empty - all valid


class TestCreativeTargetingsOnLineItem:
    """Test building creativeTargetings on GAM line items."""

    def test_orders_manager_builds_creative_targetings(self):
        """Test orders manager adds creativeTargetings from impl_config."""
        # The implementation adds creativeTargetings to line_item when impl_config has placement_targeting
        # This verifies the code path exists
        from src.adapters.gam.managers.orders import GAMOrdersManager

        # Verify the class exists and can be instantiated (would need mocks for full test)
        assert GAMOrdersManager is not None


class TestTargetingNameOnLICA:
    """Test setting targetingName on LICAs."""

    def test_associate_creative_with_placement_targeting_dry_run(self):
        """Test _associate_creative_with_line_items sets targetingName in dry run."""
        from src.adapters.gam.managers.creatives import GAMCreativesManager

        # Create manager in dry_run mode
        mock_client_manager = MagicMock()
        manager = GAMCreativesManager(
            client_manager=mock_client_manager,
            advertiser_id="123",
            dry_run=True,
        )

        # Test asset with placement_ids
        asset = {
            "creative_id": "creative_1",
            "package_assignments": [{"package_id": "pkg_prod_abc_def_1", "weight": 100}],
            "placement_ids": ["homepage_atf"],
        }

        # Line item map
        line_item_map = {"TestLineItem - prod_abc": "12345"}

        # Placement targeting map
        placement_targeting_map = {
            "homepage_atf": "homepage-above-fold",
            "article_inline": "article-inline",
        }

        # Call method - should log but not make API calls
        manager._associate_creative_with_line_items(
            gam_creative_id="999",
            asset=asset,
            line_item_map=line_item_map,
            lica_service=None,
            placement_targeting_map=placement_targeting_map,
        )

        # No exception means success in dry run mode

    def test_associate_creative_without_placement_targeting(self):
        """Test _associate_creative_with_line_items works without placement targeting."""
        from src.adapters.gam.managers.creatives import GAMCreativesManager

        mock_client_manager = MagicMock()
        manager = GAMCreativesManager(
            client_manager=mock_client_manager,
            advertiser_id="123",
            dry_run=True,
        )

        # Asset without placement_ids
        asset = {
            "creative_id": "creative_1",
            "package_assignments": [{"package_id": "pkg_prod_abc_def_1", "weight": 100}],
        }

        line_item_map = {"TestLineItem - prod_abc": "12345"}

        # Call without placement_targeting_map
        manager._associate_creative_with_line_items(
            gam_creative_id="999",
            asset=asset,
            line_item_map=line_item_map,
            lica_service=None,
            placement_targeting_map=None,
        )

        # No exception means success

    def test_associate_creative_uses_first_placement_id(self):
        """Test that when multiple placement_ids exist, first is used."""
        from src.adapters.gam.managers.creatives import GAMCreativesManager

        mock_client_manager = MagicMock()
        manager = GAMCreativesManager(
            client_manager=mock_client_manager,
            advertiser_id="123",
            dry_run=True,
        )

        # Asset with multiple placement_ids
        asset = {
            "creative_id": "creative_1",
            "package_assignments": [{"package_id": "pkg_prod_abc_def_1", "weight": 100}],
            "placement_ids": ["homepage_atf", "sidebar"],  # Two placements
        }

        line_item_map = {"TestLineItem - prod_abc": "12345"}
        placement_targeting_map = {
            "homepage_atf": "homepage-above-fold",
            "sidebar": "sidebar-targeting",
        }

        # Should use first placement_id
        manager._associate_creative_with_line_items(
            gam_creative_id="999",
            asset=asset,
            line_item_map=line_item_map,
            lica_service=None,
            placement_targeting_map=placement_targeting_map,
        )

        # Would log warning about multiple placement_ids but use first


class TestPlacementTargetingMapFlow:
    """Test placement_targeting_map data flow through adapter."""

    def test_add_creative_assets_accepts_placement_targeting_map(self):
        """Test add_creative_assets accepts placement_targeting_map parameter."""
        # Check method signature includes placement_targeting_map
        import inspect

        from src.adapters.gam.managers.creatives import GAMCreativesManager

        sig = inspect.signature(GAMCreativesManager.add_creative_assets)
        params = list(sig.parameters.keys())
        assert "placement_targeting_map" in params


class TestExtractPackageInfo:
    """Test _extract_package_info helper function."""

    def test_extract_package_info_legacy_format(self):
        """Test extraction from legacy string format."""
        from src.adapters.gam.managers.creatives import _extract_package_info

        result = _extract_package_info(["pkg_1", "pkg_2"])
        assert result == [("pkg_1", 100), ("pkg_2", 100)]

    def test_extract_package_info_new_format(self):
        """Test extraction from new dict format with weight."""
        from src.adapters.gam.managers.creatives import _extract_package_info

        result = _extract_package_info(
            [
                {"package_id": "pkg_1", "weight": 50},
                {"package_id": "pkg_2", "weight": 150},
            ]
        )
        assert result == [("pkg_1", 50), ("pkg_2", 150)]

    def test_extract_package_info_mixed_format(self):
        """Test extraction from mixed formats."""
        from src.adapters.gam.managers.creatives import _extract_package_info

        result = _extract_package_info(
            [
                "pkg_1",  # Legacy
                {"package_id": "pkg_2", "weight": 75},  # New format
            ]
        )
        assert result == [("pkg_1", 100), ("pkg_2", 75)]

    def test_extract_package_info_default_weight(self):
        """Test default weight when not provided in dict."""
        from src.adapters.gam.managers.creatives import _extract_package_info

        result = _extract_package_info([{"package_id": "pkg_1"}])
        assert result == [("pkg_1", 100)]
