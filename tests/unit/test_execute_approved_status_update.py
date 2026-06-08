"""Unit test: execute_approved_media_buy must update status to 'active' after adapter success.

Bug: salesagent-mckm
Root cause: execute_approved_media_buy returns (True, None) after successful adapter
execution but never sets media_buy.status = 'active' in the database.
"""

from unittest.mock import MagicMock, patch

from src.core.schemas import CreateMediaBuySuccess, Principal
from tests.helpers.execute_approved_mocks import (
    make_mock_media_buy as _make_mock_media_buy,
)
from tests.helpers.execute_approved_mocks import (
    make_mock_package as _make_mock_package,
)
from tests.helpers.execute_approved_mocks import (
    make_mock_product as _make_mock_product,
)
from tests.helpers.execute_approved_mocks import (
    make_mock_tenant as _make_mock_tenant,
)


class TestExecuteApprovedStatusUpdate:
    """execute_approved_media_buy must set status='active' after adapter success."""

    def test_status_updated_to_active_after_adapter_success(self):
        """After successful adapter execution, media_buy.status must be 'active'.

        This is the regression test for salesagent-mckm: the function returns
        (True, None) but never updates the status field.
        """
        # -- Arrange --
        tenant = _make_mock_tenant()
        media_buy = _make_mock_media_buy()
        db_package = _make_mock_package()
        product = _make_mock_product()

        principal = Principal(
            principal_id="principal_1",
            name="Test Principal",
            platform_mappings={},
        )

        adapter_response = CreateMediaBuySuccess(
            media_buy_id="mb_test_001",
            packages=[],
        )

        # Mock adapter with no orders_manager (skip order approval)
        mock_adapter = MagicMock()
        mock_adapter.orders_manager = None

        # Set up four UoW instances the function opens:
        # 1. Load tenant, media_buy, packages, products
        # 2. Persist platform_order_id after adapter success
        # 3. Handle creative uploads
        # 4. Update media buy status to 'active' (the fix)
        mock_session_1 = MagicMock()
        mock_session_2 = MagicMock()
        mock_session_3 = MagicMock()

        # Session 1 scalars: tenant, media_buy, packages, product
        session_1_scalars = [
            MagicMock(first=MagicMock(return_value=tenant)),
            MagicMock(first=MagicMock(return_value=media_buy)),
            MagicMock(all=MagicMock(return_value=[db_package])),
            MagicMock(first=MagicMock(return_value=product)),
        ]
        mock_session_1.scalars = MagicMock(side_effect=session_1_scalars)

        # Session 2: creative assignments returns empty
        mock_session_2.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))

        # Build mock UoWs — each call to MediaBuyUoW() returns the next one
        mock_uow_1 = MagicMock()
        mock_uow_1.__enter__ = MagicMock(return_value=mock_uow_1)
        mock_uow_1.__exit__ = MagicMock(return_value=None)
        mock_uow_1.session = mock_session_1
        mock_uow_1.media_buys = MagicMock()

        mock_repo_plids = MagicMock()
        mock_repo_plids.get_packages.return_value = [db_package]
        mock_uow_plids = MagicMock()
        mock_uow_plids.__enter__ = MagicMock(return_value=mock_uow_plids)
        mock_uow_plids.__exit__ = MagicMock(return_value=None)
        mock_uow_plids.media_buys = mock_repo_plids

        mock_uow_2 = MagicMock()
        mock_uow_2.__enter__ = MagicMock(return_value=mock_uow_2)
        mock_uow_2.__exit__ = MagicMock(return_value=None)
        mock_uow_2.session = mock_session_2
        mock_uow_2.media_buys = MagicMock()

        # UoW 4 uses update_status on the repository — track it was called
        mock_repo_3 = MagicMock()
        mock_uow_3 = MagicMock()
        mock_uow_3.__enter__ = MagicMock(return_value=mock_uow_3)
        mock_uow_3.__exit__ = MagicMock(return_value=None)
        mock_uow_3.session = mock_session_3
        mock_uow_3.media_buys = mock_repo_3

        uow_iter = iter([mock_uow_1, mock_uow_plids, mock_uow_2, mock_uow_3])

        with (
            patch("src.core.database.repositories.MediaBuyUoW", side_effect=lambda _: next(uow_iter)),
            patch("src.core.config_loader.set_current_tenant"),
            patch(
                "src.core.config_loader.get_tenant_by_id",
                return_value={"tenant_id": "tenant_1", "adapter_type": "mock"},
            ),
            patch("src.core.auth.get_principal_object", return_value=principal),
            patch(
                "src.core.tools.media_buy_create._execute_adapter_media_buy_creation",
                return_value=adapter_response,
            ),
            patch("src.core.tools.media_buy_create._validate_creatives_before_adapter_call"),
            patch("src.core.helpers.adapter_helpers.get_adapter", return_value=mock_adapter),
        ):
            from src.core.tools.media_buy_create import execute_approved_media_buy

            success, error = execute_approved_media_buy("mb_test_001", "tenant_1")

        # -- Assert --
        assert success is True, f"Expected success but got error: {error}"
        assert error is None

        # THE KEY ASSERTION: update_status must be called with 'active'
        mock_repo_3.update_status.assert_called_once_with("mb_test_001", "active")
