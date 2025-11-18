"""Unit tests for GAM adapter update_media_buy method.

Tests that package budget updates are persisted to the database
and that unsupported actions return explicit errors (no silent failures).
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock, patch

from src.core.schemas import UpdateMediaBuyError, UpdateMediaBuySuccess


def test_update_package_budget_persists_to_database():
    """Test that update_package_budget action actually updates the database."""
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
        "pricing": {"model": "cpm", "currency": "USD"},  # Add pricing info
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

    with (
        patch("src.core.database.database_session.get_db_session") as mock_db,
        patch("sqlalchemy.orm.attributes.flag_modified") as mock_flag_modified,
    ):
        mock_session = MagicMock()
        mock_db.return_value.__enter__.return_value = mock_session

        # Mock the query to return our test package
        mock_scalars = Mock()
        mock_scalars.first.return_value = mock_package
        mock_session.scalars.return_value = mock_scalars

        # Call the actual method (bind to real class)
        result = GoogleAdManager.update_media_buy(
            mock_adapter,
            media_buy_id=media_buy_id,
            buyer_ref="buyer_test",
            action="update_package_budget",
            package_id=package_id,
            budget=new_budget,
            today=datetime.now(UTC),
        )

        # Verify GAM sync was called
        mock_adapter.orders_manager.update_line_item_budget.assert_called_once_with(
            line_item_id="123456",
            new_budget=float(new_budget),
            pricing_model="cpm",
            currency="USD",
        )

        # Verify flag_modified was called
        mock_flag_modified.assert_called_once_with(mock_package, "package_config")

        # Verify success response
        assert isinstance(result, UpdateMediaBuySuccess)
        assert result.media_buy_id == media_buy_id

        # Verify database was updated
        assert mock_package.package_config["budget"] == float(new_budget)

        # Verify session.commit() was called
        mock_session.commit.assert_called_once()


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

    with patch("src.core.database.database_session.get_db_session") as mock_db:
        mock_session = MagicMock()
        mock_db.return_value.__enter__.return_value = mock_session

        # Mock the query to return None (package not found)
        mock_scalars = Mock()
        mock_scalars.first.return_value = None
        mock_session.scalars.return_value = mock_scalars

        # Call the actual method
        result = GoogleAdManager.update_media_buy(
            mock_adapter,
            media_buy_id=media_buy_id,
            buyer_ref="buyer_test",
            action="update_package_budget",
            package_id=package_id,
            budget=new_budget,
            today=datetime.now(UTC),
        )

        # Verify error response
        assert isinstance(result, UpdateMediaBuyError)
        assert len(result.errors) == 1
        assert result.errors[0].code == "package_not_found"
        assert package_id in result.errors[0].message

        # Verify commit was NOT called (no changes to persist)
        mock_session.commit.assert_not_called()


def test_unsupported_action_returns_explicit_error():
    """Test that unsupported actions return explicit error (no silent success)."""
    from src.adapters.google_ad_manager import GoogleAdManager

    media_buy_id = "mb_test123"

    # Create a minimal mock adapter
    mock_adapter = Mock(spec=GoogleAdManager)
    mock_adapter.log = Mock()
    mock_adapter._is_admin_principal = Mock(return_value=False)
    mock_adapter._requires_manual_approval = Mock(return_value=False)

    # Test an action that doesn't exist
    result = GoogleAdManager.update_media_buy(
        mock_adapter,
        media_buy_id=media_buy_id,
        buyer_ref="buyer_test",
        action="delete_media_buy",  # Not supported
        package_id=None,
        budget=None,
        today=datetime.now(),
    )

    # Verify error response (not success!)
    assert isinstance(result, UpdateMediaBuyError)
    assert len(result.errors) == 1
    assert result.errors[0].code == "unsupported_action"
    assert "delete_media_buy" in result.errors[0].message


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

    with patch("src.core.database.database_session.get_db_session") as mock_db:
        mock_session = MagicMock()
        mock_db.return_value.__enter__.return_value = mock_session

        # Mock the query to return our test package
        mock_scalars = Mock()
        mock_scalars.first.return_value = mock_package
        mock_session.scalars.return_value = mock_scalars

        # Test pause_package
        result = GoogleAdManager.update_media_buy(
            mock_adapter,
            media_buy_id=media_buy_id,
            buyer_ref="buyer_test",
            action="pause_package",
            package_id=package_id,
            budget=None,
            today=datetime.now(UTC),
        )

        # Verify pause was called
        mock_adapter.orders_manager.pause_line_item.assert_called_once_with("123456")

        # Verify success response
        assert isinstance(result, UpdateMediaBuySuccess), "pause_package should return success"
        assert result.media_buy_id == media_buy_id

        # Reset mocks for next test
        mock_adapter.orders_manager.pause_line_item.reset_mock()
        mock_adapter.orders_manager.resume_line_item.reset_mock()
        mock_scalars.first.return_value = mock_package  # Reset package query

        # Test resume_package
        result = GoogleAdManager.update_media_buy(
            mock_adapter,
            media_buy_id=media_buy_id,
            buyer_ref="buyer_test",
            action="resume_package",
            package_id=package_id,
            budget=None,
            today=datetime.now(UTC),
        )

        # Verify resume was called
        mock_adapter.orders_manager.resume_line_item.assert_called_once_with("123456")

        # Verify success response
        assert isinstance(result, UpdateMediaBuySuccess), "resume_package should return success"
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

    with patch("src.core.database.database_session.get_db_session") as mock_db:
        mock_session = MagicMock()
        mock_db.return_value.__enter__.return_value = mock_session

        # Mock the query to return multiple packages
        mock_scalars = Mock()
        mock_scalars.all.return_value = [mock_package1, mock_package2]
        mock_session.scalars.return_value = mock_scalars

        # Test pause_media_buy
        result = GoogleAdManager.update_media_buy(
            mock_adapter,
            media_buy_id=media_buy_id,
            buyer_ref="buyer_test",
            action="pause_media_buy",
            package_id=None,
            budget=None,
            today=datetime.now(UTC),
        )

        # Verify pause was called for both packages
        assert mock_adapter.orders_manager.pause_line_item.call_count == 2
        mock_adapter.orders_manager.pause_line_item.assert_any_call("111")
        mock_adapter.orders_manager.pause_line_item.assert_any_call("222")

        # Verify success response
        assert isinstance(result, UpdateMediaBuySuccess), "pause_media_buy should return success"
        assert result.media_buy_id == media_buy_id

        # Reset mocks for next test
        mock_adapter.orders_manager.pause_line_item.reset_mock()
        mock_adapter.orders_manager.resume_line_item.reset_mock()
        mock_scalars.all.return_value = [mock_package1, mock_package2]  # Reset package query

        # Test resume_media_buy
        result = GoogleAdManager.update_media_buy(
            mock_adapter,
            media_buy_id=media_buy_id,
            buyer_ref="buyer_test",
            action="resume_media_buy",
            package_id=None,
            budget=None,
            today=datetime.now(UTC),
        )

        # Verify resume was called for both packages
        assert mock_adapter.orders_manager.resume_line_item.call_count == 2
        mock_adapter.orders_manager.resume_line_item.assert_any_call("111")
        mock_adapter.orders_manager.resume_line_item.assert_any_call("222")

        # Verify success response
        assert isinstance(result, UpdateMediaBuySuccess), "resume_media_buy should return success"
        assert result.media_buy_id == media_buy_id


def test_update_package_budget_rejects_budget_below_delivery():
    """Test that update_package_budget rejects budget less than current spend."""
    from src.adapters.google_ad_manager import GoogleAdManager

    media_buy_id = "mb_test123"
    package_id = "pkg_test456"
    current_spend = 15000.0
    new_budget = 10000  # Less than current spend

    # Mock database session and MediaPackage with delivery metrics
    mock_package = Mock()
    mock_package.package_id = package_id
    mock_package.package_config = {
        "budget": 19000,
        "product_id": "prod_1",
        "delivery_metrics": {"spend": current_spend, "impressions_delivered": 50000},
    }

    # Create a minimal mock adapter
    mock_adapter = Mock(spec=GoogleAdManager)
    mock_adapter.log = Mock()
    mock_adapter.tenant_id = "tenant_test123"
    mock_adapter._is_admin_principal = Mock(return_value=False)
    mock_adapter._requires_manual_approval = Mock(return_value=False)
    mock_adapter.workflow_manager = Mock()

    with patch("src.core.database.database_session.get_db_session") as mock_db:
        mock_session = MagicMock()
        mock_db.return_value.__enter__.return_value = mock_session

        # Mock the query to return our test package
        mock_scalars = Mock()
        mock_scalars.first.return_value = mock_package
        mock_session.scalars.return_value = mock_scalars

        # Call the actual method
        result = GoogleAdManager.update_media_buy(
            mock_adapter,
            media_buy_id=media_buy_id,
            buyer_ref="buyer_test",
            action="update_package_budget",
            package_id=package_id,
            budget=new_budget,
            today=datetime.now(UTC),
        )

        # Verify error response
        assert isinstance(result, UpdateMediaBuyError)
        assert len(result.errors) == 1
        assert result.errors[0].code == "budget_below_delivery"
        assert str(new_budget) in result.errors[0].message
        assert str(current_spend) in result.errors[0].message

        # Verify commit was NOT called (budget rejected)
        mock_session.commit.assert_not_called()
