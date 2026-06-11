"""Integration tests for GAM order lifecycle management (Issue #117).

Focused integration tests using real business logic with minimal mocking.
Tests the new lifecycle actions: activate_order, submit_for_approval,
approve_order, and archive_order with proper validation.
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from src.adapters.constants import UPDATE_ACTIONS
from src.adapters.google_ad_manager import GoogleAdManager
from src.core.exceptions import AdCPAuthorizationError, AdCPCapabilityNotSupportedError
from src.core.schemas import Principal, UpdateMediaBuySuccess


def _assert_unsupported_feature_for_action(response, action: str) -> None:
    """Pin the GAM adapter's current UNSUPPORTED_FEATURE rejection for ``action``.

    Three sites in this module exercise actions the GAM dry-run adapter
    currently rejects (``submit_for_approval``, ``archive_order``,
    ``approve_order``, ``activate_order`` non-guaranteed). When the
    adapter is fixed (separate ticket), removing or flipping a caller
    forces re-evaluation of that test's contract. Helper is private to
    this module — DRY per CLAUDE.md, intentionally not exported.
    """
    assert response.errors is not None and len(response.errors) > 0, (
        f"Expected Error response for action={action!r}; GAM currently rejects with UNSUPPORTED_FEATURE"
    )
    assert response.errors[0].code == "UNSUPPORTED_FEATURE", (
        f"Expected UNSUPPORTED_FEATURE for action={action!r}, got {response.errors[0].code}"
    )


class TestGAMOrderLifecycleIntegration:
    """Integration tests for GAM order lifecycle with real business logic."""

    @pytest.fixture
    def gam_config(self):
        """Standard GAM configuration for testing."""
        return {"network_code": "12345678", "refresh_token": "test_token", "trafficker_id": "987654"}

    @pytest.fixture
    def test_principals(self):
        """Create test principals with different admin configurations."""
        return {
            "regular": Principal(
                principal_id="advertiser",
                name="Regular Advertiser",
                platform_mappings={"google_ad_manager": {"advertiser_id": "123456"}},
            ),
            "gam_admin": Principal(
                principal_id="gam_admin",
                name="GAM Admin",
                platform_mappings={"google_ad_manager": {"advertiser_id": "123456", "gam_admin": True}},
            ),
            "is_admin": Principal(
                principal_id="is_admin",
                name="Is Admin",
                platform_mappings={"google_ad_manager": {"advertiser_id": "123456", "is_admin": True}},
            ),
        }

    def test_lifecycle_actions_exist_in_constants(self):
        """Verify all lifecycle actions are defined in UPDATE_ACTIONS."""
        required_actions = ["activate_order", "submit_for_approval", "approve_order", "archive_order"]
        for action in required_actions:
            assert action in UPDATE_ACTIONS
            assert isinstance(UPDATE_ACTIONS[action], str)

    def test_admin_detection_real_business_logic(self, test_principals, gam_config):
        """Test admin principal detection using real business logic."""
        with patch("src.adapters.google_ad_manager.GoogleAdManager._init_client"):
            # Test regular user - not admin
            regular_adapter = GoogleAdManager(
                config=gam_config,
                principal=test_principals["regular"],
                network_code=gam_config["network_code"],
                advertiser_id=test_principals["regular"].platform_mappings["google_ad_manager"]["advertiser_id"],
                trafficker_id=gam_config["trafficker_id"],
                dry_run=True,
                tenant_id="test",
            )
            assert regular_adapter._is_admin_principal() is False

            # Test gam_admin flag - should be admin
            gam_admin_adapter = GoogleAdManager(
                config=gam_config,
                principal=test_principals["gam_admin"],
                network_code=gam_config["network_code"],
                advertiser_id=test_principals["gam_admin"].platform_mappings["google_ad_manager"]["advertiser_id"],
                trafficker_id=gam_config["trafficker_id"],
                dry_run=True,
                tenant_id="test",
            )
            assert gam_admin_adapter._is_admin_principal() is True

            # Test is_admin flag - should be admin
            is_admin_adapter = GoogleAdManager(
                config=gam_config,
                principal=test_principals["is_admin"],
                network_code=gam_config["network_code"],
                advertiser_id=test_principals["is_admin"].platform_mappings["google_ad_manager"]["advertiser_id"],
                trafficker_id=gam_config["trafficker_id"],
                dry_run=True,
                tenant_id="test",
            )
            assert is_admin_adapter._is_admin_principal() is True

    @pytest.mark.requires_db
    def test_lifecycle_workflow_validation(self, test_principals, gam_config):
        """Test lifecycle action workflows with business validation.

        After the error-emission architecture migration, the GAM adapter raises typed AdCPError subclasses for
        unsupported actions and authorization failures. Only ``approve_order``,
        ``activate_order``, and ``update_package_budget`` are supported.
        """
        with patch("src.adapters.google_ad_manager.GoogleAdManager._init_client"):
            # Test regular user with different actions
            regular_adapter = GoogleAdManager(
                config=gam_config,
                principal=test_principals["regular"],
                network_code=gam_config["network_code"],
                advertiser_id=test_principals["regular"].platform_mappings["google_ad_manager"]["advertiser_id"],
                trafficker_id=gam_config["trafficker_id"],
                dry_run=True,
                tenant_id="test",
            )

            # submit_for_approval and archive_order are NOT supported by GAM.
            unsupported_actions = ["submit_for_approval", "archive_order"]
            for action in unsupported_actions:
                with pytest.raises(AdCPCapabilityNotSupportedError, match=f"{action}|not supported"):
                    regular_adapter.update_media_buy(
                        media_buy_id="12345",
                        action=action,
                        package_id=None,
                        budget=None,
                        today=datetime.now(UTC),
                    )

            # approve_order is admin-only — non-admin gets AdCPAuthorizationError.
            with pytest.raises(AdCPAuthorizationError, match="admin"):
                regular_adapter.update_media_buy(
                    media_buy_id="12345",
                    action="approve_order",
                    package_id=None,
                    budget=None,
                    today=datetime.now(UTC),
                )

            # Admin user passes the admin_only_actions gate, but approve_order has
            # no dedicated handler in the adapter so it falls through to the
            # AdCPCapabilityNotSupportedError path. Documenting current behavior:
            # the admin gate exists but no implementation backs it.
            admin_adapter = GoogleAdManager(
                config=gam_config,
                principal=test_principals["gam_admin"],
                network_code=gam_config["network_code"],
                advertiser_id=test_principals["gam_admin"].platform_mappings["google_ad_manager"]["advertiser_id"],
                trafficker_id=gam_config["trafficker_id"],
                dry_run=True,
                tenant_id="test",
            )
            with pytest.raises(AdCPCapabilityNotSupportedError, match="approve_order|not supported"):
                admin_adapter.update_media_buy(
                    media_buy_id="12345",
                    action="approve_order",
                    package_id=None,
                    budget=None,
                    today=datetime.now(UTC),
                )

    def test_guaranteed_line_item_classification(self):
        """Test line item type classification logic with real data structures."""
        # Test guaranteed line item types
        guaranteed_items = [
            {"id": "1", "lineItemType": "STANDARD", "name": "Standard Item"},
            {"id": "2", "lineItemType": "SPONSORSHIP", "name": "Sponsorship Item"},
            {"id": "3", "lineItemType": "HOUSE", "name": "House Item"},
        ]
        has_guaranteed, types = self._classify_line_items(guaranteed_items)
        assert has_guaranteed is True
        assert set(types) == {"STANDARD", "SPONSORSHIP", "HOUSE"}

        # Test non-guaranteed line item types
        non_guaranteed_items = [
            {"id": "4", "lineItemType": "NETWORK", "name": "Network Item"},
            {"id": "5", "lineItemType": "BULK", "name": "Bulk Item"},
            {"id": "6", "lineItemType": "PRICE_PRIORITY", "name": "Price Priority Item"},
        ]
        has_guaranteed, types = self._classify_line_items(non_guaranteed_items)
        assert has_guaranteed is False
        assert len(types) == 0

        # Test mixed types - should detect guaranteed
        mixed_items = guaranteed_items + non_guaranteed_items
        has_guaranteed, types = self._classify_line_items(mixed_items)
        assert has_guaranteed is True
        assert "STANDARD" in types and "SPONSORSHIP" in types

    @pytest.mark.requires_db
    def test_activation_validation_with_guaranteed_items(self, test_principals, gam_config):
        """Test activation validation blocking guaranteed line items.

        After the error-emission architecture migration, ``activate_order`` raises AdCPCapabilityNotSupportedError
        when there are no guaranteed items (the code path only handles the
        guaranteed-items case explicitly). With guaranteed items it returns
        a workflow step in the success response.
        """
        with patch("src.adapters.google_ad_manager.GoogleAdManager._init_client"):
            adapter = GoogleAdManager(
                config=gam_config,
                principal=test_principals["regular"],
                network_code=gam_config["network_code"],
                advertiser_id=test_principals["regular"].platform_mappings["google_ad_manager"]["advertiser_id"],
                trafficker_id=gam_config["trafficker_id"],
                dry_run=True,
                tenant_id="test",
            )

            # Test activation with non-guaranteed items — no special handling, raises.
            with patch.object(adapter, "_check_order_has_guaranteed_items", return_value=(False, [])):
                with pytest.raises(AdCPCapabilityNotSupportedError, match="activate_order|not supported"):
                    adapter.update_media_buy(
                        media_buy_id="12345",
                        action="activate_order",
                        package_id=None,
                        budget=None,
                        today=datetime.now(UTC),
                    )

            # Test activation with guaranteed items (should create workflow step)
            with patch.object(adapter, "_check_order_has_guaranteed_items", return_value=(True, ["STANDARD"])):
                # Mock workflow step creation to avoid database foreign key issues
                with patch.object(
                    adapter.workflow_manager, "create_activation_workflow_step", return_value="test_step_id"
                ):
                    response = adapter.update_media_buy(
                        media_buy_id="12345",
                        action="activate_order",
                        package_id=None,
                        budget=None,
                        today=datetime.now(UTC),
                    )
                    # Guaranteed activation actually creates the workflow
                    # step (the patched ``create_activation_workflow_step``
                    # runs even though the adapter logs "Unsupported action"
                    # for activate_order). The response is Success — a class
                    # with no errors field at all (the update-success schema
                    # forbids one) — and ``workflow_step_id`` is the anchor.
                    assert isinstance(response, UpdateMediaBuySuccess)
                    assert response.workflow_step_id == "test_step_id"

    # Helper method for line item classification (no external dependencies)
    def _classify_line_items(self, line_items):
        """Helper method to test line item classification logic."""
        guaranteed_types = {"STANDARD", "GUARANTEED", "SPONSORSHIP", "HOUSE"}
        guaranteed_found = []

        for item in line_items:
            item_type = item.get("lineItemType")
            if item_type in guaranteed_types:
                guaranteed_found.append(item_type)

        return len(guaranteed_found) > 0, guaranteed_found
