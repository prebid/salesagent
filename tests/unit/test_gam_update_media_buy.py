"""Unit tests for GAM adapter update_media_buy method.

Tests that package budget and pause updates reach the GAM line items, read from the
stored packages the update tool passes in, and that unsupported actions return
explicit errors (no silent failures).
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.adapters.base import AdapterUpdateResult
from src.core.database.repositories.media_buy import stored_package_of
from src.core.stored_package import StoredPackage


def _stored(package_id: str, package_config: dict) -> StoredPackage:
    """The stored package the update tool passes the adapter (``packages=``), read by the repository's projection."""
    return stored_package_of(SimpleNamespace(package_id=package_id, package_config=package_config, budget=None))


def test_update_package_budget_syncs_the_line_item():
    """update_package_budget changes the package's GAM line item and writes nothing itself.

    The seller's record of the budget is the package's budget column, which the update
    tool writes inside its unit of work; the adapter opens no session (#2647).
    """
    from src.adapters.google_ad_manager import GoogleAdManager

    media_buy_id = "mb_test123"
    package_id = "pkg_test456"
    new_budget = 30000

    # Mock database session and MediaPackage
    mock_package = Mock()
    mock_package.package_id = package_id
    mock_package.package_config = {
        "budget": 19000,
        "product_id": "prod_1",
        "platform_line_item_id": "123456",  # Add platform ID for GAM sync
        # The terms the create path stores (pricing_helpers): a CPC package in EUR, so a
        # read of any other key falls back to cpm/USD and the assertion below fails.
        "pricing_info": {"pricing_model": "cpc", "currency": "EUR"},
    }

    # Create a minimal mock adapter with orders_manager
    mock_adapter = Mock(spec=GoogleAdManager)
    mock_adapter.log = Mock()
    mock_adapter.tenant_id = "tenant_test123"  # Add tenant_id for tenant isolation
    mock_adapter._is_admin_principal = Mock(return_value=False)
    mock_adapter._requires_manual_approval = Mock(return_value=False)
    mock_adapter.workflow_manager = Mock()
    # Mock orders_manager for GAM API sync
    mock_adapter.orders_manager = Mock()
    mock_adapter.orders_manager.update_line_item_budget = Mock(return_value=True)

    # Call the actual method (bind to real class)
    result = GoogleAdManager.update_media_buy(
        mock_adapter,
        media_buy_id=media_buy_id,
        action="update_package_budget",
        package_id=package_id,
        budget=new_budget,
        today=datetime.now(UTC),
        packages=[_stored(package_id, mock_package.package_config)],
    )

    # Verify GAM sync was called
    mock_adapter.orders_manager.update_line_item_budget.assert_called_once_with(
        line_item_id="123456",
        new_budget=float(new_budget),
        pricing_model="cpc",
        currency="EUR",
    )

    # The adapter hands back its own carrier; the tool builds the buyer's
    # UpdateMediaBuySuccess from the re-read row (src/adapters/base.py).
    assert isinstance(result, AdapterUpdateResult)
    assert result.media_buy_id == media_buy_id


def test_update_package_budget_returns_error_when_package_not_found():
    """Test that update_package_budget returns error when package doesn't exist."""
    from src.adapters.google_ad_manager import GoogleAdManager

    media_buy_id = "mb_test123"
    package_id = "pkg_nonexistent"
    new_budget = 30000

    # Create a minimal mock adapter
    mock_adapter = Mock(spec=GoogleAdManager)
    mock_adapter.log = Mock()
    mock_adapter.tenant_id = "tenant_test123"  # Add tenant_id for tenant isolation
    mock_adapter._is_admin_principal = Mock(return_value=False)
    mock_adapter._requires_manual_approval = Mock(return_value=False)
    mock_adapter.workflow_manager = Mock()

    from src.core.exceptions import AdCPPackageNotFoundError

    # The buy holds no package with this id.
    with pytest.raises(AdCPPackageNotFoundError):
        GoogleAdManager.update_media_buy(
            mock_adapter,
            media_buy_id=media_buy_id,
            action="update_package_budget",
            package_id=package_id,
            budget=new_budget,
            today=datetime.now(UTC),
            packages=[],
        )


def test_unsupported_action_returns_explicit_error():
    """Test that unsupported actions return explicit error (no silent success)."""
    from src.adapters.google_ad_manager import GoogleAdManager

    media_buy_id = "mb_test123"

    # Create a minimal mock adapter
    mock_adapter = Mock(spec=GoogleAdManager)
    mock_adapter.log = Mock()
    mock_adapter._is_admin_principal = Mock(return_value=False)
    mock_adapter._requires_manual_approval = Mock(return_value=False)

    from src.core.exceptions import AdCPCapabilityNotSupportedError

    with pytest.raises(AdCPCapabilityNotSupportedError) as _ei:
        GoogleAdManager.update_media_buy(
            mock_adapter,
            media_buy_id=media_buy_id,
            action="delete_media_buy",  # Not supported
            package_id=None,
            budget=None,
            today=datetime.now(),
            packages=(),
        )
    # The identifier is STRUCTURED now: it lives in details/field, not in prose.


def test_pause_resume_package_actions_work():
    """Test that pause/resume package actions work via GAM API."""
    from src.adapters.google_ad_manager import GoogleAdManager

    media_buy_id = "mb_test123"
    package_id = "pkg_test456"

    # Mock database session and MediaPackage
    mock_package = Mock()
    mock_package.package_id = package_id
    mock_package.package_config = {
        "budget": 19000,
        "product_id": "prod_1",
        "platform_line_item_id": "123456",  # Add platform ID for GAM sync
    }

    # Create a minimal mock adapter with orders_manager
    mock_adapter = Mock(spec=GoogleAdManager)
    mock_adapter.log = Mock()
    mock_adapter.tenant_id = "tenant_test123"  # Add tenant_id for tenant isolation
    mock_adapter._is_admin_principal = Mock(return_value=False)
    mock_adapter._requires_manual_approval = Mock(return_value=False)
    mock_adapter.workflow_manager = Mock()
    # Mock orders_manager for GAM API sync
    mock_adapter.orders_manager = Mock()
    mock_adapter.orders_manager.pause_line_item = Mock(return_value=True)
    mock_adapter.orders_manager.resume_line_item = Mock(return_value=True)

    packages = [_stored(package_id, mock_package.package_config)]

    # Test pause_package
    result = GoogleAdManager.update_media_buy(
        mock_adapter,
        media_buy_id=media_buy_id,
        action="pause_package",
        package_id=package_id,
        budget=None,
        today=datetime.now(UTC),
        packages=packages,
    )

    # Verify pause was called
    mock_adapter.orders_manager.pause_line_item.assert_called_once_with("123456")

    # Verify success response
    assert isinstance(result, AdapterUpdateResult), "pause_package should return success"
    assert result.media_buy_id == media_buy_id

    # Reset mocks for next test
    mock_adapter.orders_manager.pause_line_item.reset_mock()
    mock_adapter.orders_manager.resume_line_item.reset_mock()

    # Test resume_package
    result = GoogleAdManager.update_media_buy(
        mock_adapter,
        media_buy_id=media_buy_id,
        action="resume_package",
        package_id=package_id,
        budget=None,
        today=datetime.now(UTC),
        packages=packages,
    )

    # Verify resume was called
    mock_adapter.orders_manager.resume_line_item.assert_called_once_with("123456")

    # Verify success response
    assert isinstance(result, AdapterUpdateResult), "resume_package should return success"
    assert result.media_buy_id == media_buy_id


def test_pause_resume_media_buy_actions_work():
    """Test that pause/resume media buy actions work via GAM API (all packages)."""
    from src.adapters.google_ad_manager import GoogleAdManager

    media_buy_id = "mb_test123"

    # Mock database session and multiple MediaPackages
    mock_package1 = Mock()
    mock_package1.package_id = "pkg1"
    mock_package1.package_config = {"platform_line_item_id": "111"}

    mock_package2 = Mock()
    mock_package2.package_id = "pkg2"
    mock_package2.package_config = {"platform_line_item_id": "222"}

    # Create a minimal mock adapter with orders_manager
    mock_adapter = Mock(spec=GoogleAdManager)
    mock_adapter.log = Mock()
    mock_adapter.tenant_id = "tenant_test123"  # Add tenant_id for tenant isolation
    mock_adapter._is_admin_principal = Mock(return_value=False)
    mock_adapter._requires_manual_approval = Mock(return_value=False)
    mock_adapter.workflow_manager = Mock()
    # Mock orders_manager for GAM API sync
    mock_adapter.orders_manager = Mock()
    mock_adapter.orders_manager.pause_line_item = Mock(return_value=True)
    mock_adapter.orders_manager.resume_line_item = Mock(return_value=True)

    packages = [_stored(p.package_id, p.package_config) for p in (mock_package1, mock_package2)]

    # Test pause_media_buy
    result = GoogleAdManager.update_media_buy(
        mock_adapter,
        media_buy_id=media_buy_id,
        action="pause_media_buy",
        package_id=None,
        budget=None,
        today=datetime.now(UTC),
        packages=packages,
    )

    # Verify pause was called for both packages
    assert mock_adapter.orders_manager.pause_line_item.call_count == 2
    mock_adapter.orders_manager.pause_line_item.assert_any_call("111")
    mock_adapter.orders_manager.pause_line_item.assert_any_call("222")

    # Verify success response
    assert isinstance(result, AdapterUpdateResult), "pause_media_buy should return success"
    assert result.media_buy_id == media_buy_id

    # Reset mocks for next test
    mock_adapter.orders_manager.pause_line_item.reset_mock()
    mock_adapter.orders_manager.resume_line_item.reset_mock()

    # Test resume_media_buy
    result = GoogleAdManager.update_media_buy(
        mock_adapter,
        media_buy_id=media_buy_id,
        action="resume_media_buy",
        package_id=None,
        budget=None,
        today=datetime.now(UTC),
        packages=packages,
    )

    # Verify resume was called for both packages
    assert mock_adapter.orders_manager.resume_line_item.call_count == 2
    mock_adapter.orders_manager.resume_line_item.assert_any_call("111")
    mock_adapter.orders_manager.resume_line_item.assert_any_call("222")

    # Verify success response
    assert isinstance(result, AdapterUpdateResult), "resume_media_buy should return success"
    assert result.media_buy_id == media_buy_id


class TestGAMUpdateMediaBuyTaxonomyRaiseSites:
    """Drive the GAM update_media_buy raise sites for the adapter-taxonomy
    classes so a class-swap at the site is caught.

    test_typed_error_wire_codes.py pins each class -> wire-code mapping by
    constructing the exception directly; here the production update_media_buy
    branches are driven so removing the ``raise`` (or swapping it to a parent
    like AdCPAdapterError) fails the test. The internal error_code asserted on
    each class is the taxonomy code carried as class identity (logs/audit);
    the wire collapses it to SERVICE_UNAVAILABLE, pinned separately.
    """

    @staticmethod
    def _build_mock_adapter():
        """A Mock(spec=GoogleAdManager) wired for the update_media_buy entry checks."""
        from src.adapters.google_ad_manager import GoogleAdManager

        mock_adapter = Mock(spec=GoogleAdManager)
        mock_adapter.log = Mock()
        mock_adapter.tenant_id = "tenant_test123"
        mock_adapter._is_admin_principal = Mock(return_value=False)
        mock_adapter._requires_manual_approval = Mock(return_value=False)
        mock_adapter.workflow_manager = Mock()
        mock_adapter.orders_manager = Mock()
        return mock_adapter

    def test_activate_order_guaranteed_workflow_failure_raises_activation_error(self):
        """activate_order on a guaranteed order whose activation workflow step
        fails to create raises AdCPActivationWorkflowError (ACTIVATION_WORKFLOW_FAILED)."""
        from src.adapters.google_ad_manager import GoogleAdManager
        from src.core.exceptions import AdCPActivationWorkflowError

        mock_adapter = self._build_mock_adapter()
        # Order has guaranteed items -> activation workflow path is taken.
        mock_adapter._check_order_has_guaranteed_items = Mock(return_value=(True, ["STANDARD"]))
        # Workflow step creation fails (returns falsy) -> production raises.
        mock_adapter.workflow_manager.create_activation_workflow_step = Mock(return_value=None)

        with pytest.raises(AdCPActivationWorkflowError) as exc_info:
            GoogleAdManager.update_media_buy(
                mock_adapter,
                media_buy_id="mb_test123",
                action="activate_order",
                package_id=None,
                budget=None,
                today=datetime.now(UTC),
                packages=(),
            )

        assert exc_info.value.error_code == "ACTIVATION_WORKFLOW_FAILED"

    def test_update_package_budget_gam_sync_failure_raises_gam_update_error(self):
        """A failed GAM line-item budget sync raises AdCPGamUpdateError (GAM_UPDATE_FAILED)."""
        from src.adapters.google_ad_manager import GoogleAdManager
        from src.core.exceptions import AdCPGamUpdateError

        package_id = "pkg_test456"
        mock_package = Mock()
        mock_package.package_id = package_id
        mock_package.package_config = {
            "budget": 19000,
            "platform_line_item_id": "123456",
            "pricing_info": {"pricing_model": "cpm", "currency": "USD"},
        }

        mock_adapter = self._build_mock_adapter()
        # GAM sync reports failure (returns falsy) -> production raises.
        mock_adapter.orders_manager.update_line_item_budget = Mock(return_value=False)

        with pytest.raises(AdCPGamUpdateError) as exc_info:
            GoogleAdManager.update_media_buy(
                mock_adapter,
                media_buy_id="mb_test123",
                action="update_package_budget",
                package_id=package_id,
                budget=30000,
                today=datetime.now(UTC),
                packages=[_stored(package_id, mock_package.package_config)],
            )

        assert exc_info.value.error_code == "AD_SERVER_UPDATE_FAILED"

    def test_pause_media_buy_partial_gam_failure_raises_bulk_update_error(self):
        """When some line items fail to pause in GAM, the bulk operation raises
        AdCPBulkUpdateError (PARTIAL_FAILURE)."""
        from src.adapters.google_ad_manager import GoogleAdManager
        from src.core.exceptions import AdCPBulkUpdateError

        mock_pkg1 = Mock()
        mock_pkg1.package_id = "pkg1"
        mock_pkg1.package_config = {"platform_line_item_id": "111"}
        mock_pkg2 = Mock()
        mock_pkg2.package_id = "pkg2"
        mock_pkg2.package_config = {"platform_line_item_id": "222"}

        mock_adapter = self._build_mock_adapter()
        # First line item pauses, second fails -> at least one failed item -> production raises.
        mock_adapter.orders_manager.pause_line_item = Mock(side_effect=[True, False])

        with pytest.raises(AdCPBulkUpdateError) as exc_info:
            GoogleAdManager.update_media_buy(
                mock_adapter,
                media_buy_id="mb_test123",
                action="pause_media_buy",
                package_id=None,
                budget=None,
                today=datetime.now(UTC),
                packages=[_stored(p.package_id, p.package_config) for p in (mock_pkg1, mock_pkg2)],
            )

        assert exc_info.value.error_code == "PARTIAL_FAILURE"
