"""Tests verifying dry_run mode skips database persistence.

Dry run should:
- Run all validation (to catch errors early)
- Return simulated response
- NOT write to database (no workflow steps, no media buys, no packages)
"""

import uuid
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.server.context import Context

from src.core.testing_hooks import AdCPTestContext


class TestCreateMediaBuyDryRunResponseStructure:
    """Verify create_media_buy dry_run response structure is valid.

    Tests that the dry_run response building code produces valid responses.
    Full integration test of _create_media_buy_impl requires database fixtures.
    """

    def test_dry_run_response_structure_is_valid(self):
        """Dry run response should be a valid CreateMediaBuySuccess."""
        from src.core.schemas import CreateMediaBuySuccess

        # Simulate the dry_run response building logic from _create_media_buy_impl
        simulated_media_buy_id = f"dry_run_mb_{uuid.uuid4().hex[:12]}"

        # Build simulated packages (matching the impl's structure)
        simulated_packages: list[dict[str, Any]] = []
        for idx, pkg_data in enumerate(
            [
                {"buyer_ref": "pkg-1", "product_id": "prod_123", "budget": 1000.0},
                {"buyer_ref": "pkg-2", "product_id": "prod_456", "bid_price": 5.0},
            ],
            1,
        ):
            simulated_package_id = f"dry_run_pkg_{uuid.uuid4().hex[:8]}_{idx}"
            simulated_pkg: dict[str, Any] = {
                "package_id": simulated_package_id,
                "paused": False,
                "buyer_ref": pkg_data["buyer_ref"],
                "product_id": pkg_data["product_id"],
            }
            if pkg_data.get("budget") is not None:
                simulated_pkg["budget"] = float(pkg_data["budget"])
            if pkg_data.get("bid_price") is not None:
                simulated_pkg["bid_price"] = float(pkg_data["bid_price"])
            simulated_packages.append(simulated_pkg)

        # Build response (matching impl's structure)
        response = CreateMediaBuySuccess(
            buyer_ref="test-buyer-ref",
            media_buy_id=simulated_media_buy_id,
            packages=cast(list[Any], simulated_packages),
            context=None,
        )

        # Verify response structure
        assert response.buyer_ref == "test-buyer-ref"
        assert response.media_buy_id.startswith("dry_run_mb_")
        assert len(response.packages) == 2
        # Access as Package objects (Pydantic validates/converts dict to Package)
        assert response.packages[0].package_id.startswith("dry_run_pkg_")
        assert response.packages[0].budget == 1000.0
        assert response.packages[1].bid_price == 5.0

    def test_dry_run_code_path_exists(self):
        """Verify the dry_run early return logic exists in create_media_buy.

        This is a structural test that verifies the implementation contains
        the expected dry_run handling. While not testing behavior directly,
        it catches accidental removal of the dry_run code path.
        """
        import inspect

        from src.core.tools.media_buy_create import _create_media_buy_impl

        source = inspect.getsource(_create_media_buy_impl)

        # Verify key structural elements exist
        assert "testing_ctx.dry_run" in source, "dry_run check should exist"
        # dry_run mode returns adapter response without database writes (adapter generates IDs)
        assert "DRY_RUN" in source, "dry_run logging should exist"
        assert "if not testing_ctx.dry_run:" in source, "workflow step should be guarded by dry_run check"


class TestUpdateMediaBuyDryRunNoPersistence:
    """Verify update_media_buy in dry_run mode doesn't write to database."""

    @pytest.fixture
    def mock_context(self):
        """Create a mock FastMCP context."""
        ctx = MagicMock(spec=Context)
        ctx.headers = {"x-adcp-auth": "test-token"}
        return ctx

    def test_dry_run_returns_simulated_response(self, mock_context):
        """Dry run should return a simulated response without database writes."""
        from src.core.schemas import UpdateMediaBuyRequest
        from src.core.tools.media_buy_update import _update_media_buy_impl

        with (
            patch("src.core.tools.media_buy_update.get_principal_id_from_context") as mock_principal_id,
            patch("src.core.tools.media_buy_update.get_current_tenant") as mock_tenant,
            patch("src.core.tools.media_buy_update.get_principal_object") as mock_principal,
            patch("src.core.tools.media_buy_update.get_testing_context") as mock_testing_ctx,
            patch("src.core.tools.media_buy_update._verify_principal"),
            patch("src.core.tools.media_buy_update.get_context_manager") as mock_ctx_manager,
            patch("src.core.tools.media_buy_update.get_adapter") as mock_adapter,
            patch("src.core.database.database_session.get_db_session") as mock_db,
        ):
            # Setup mocks
            mock_principal_id.return_value = "principal_123"
            mock_tenant.return_value = {
                "tenant_id": "test_tenant",
                "name": "Test Tenant",
            }
            mock_principal.return_value = MagicMock(
                principal_id="principal_123",
                name="Test Principal",
                platform_mappings={},
            )

            # Key: Return dry_run=True testing context
            mock_testing_ctx.return_value = AdCPTestContext(dry_run=True)

            # Mock adapter
            mock_adapter.return_value = MagicMock(
                manual_approval_required=False,
                manual_approval_operations=[],
            )

            # Mock database session for media buy lookup
            mock_session = MagicMock()
            mock_db.return_value.__enter__.return_value = mock_session
            mock_media_buy = MagicMock()
            mock_media_buy.media_buy_id = "mb_existing_123"
            mock_session.scalars.return_value.first.return_value = mock_media_buy

            # Execute — impl now accepts a typed request object
            req = UpdateMediaBuyRequest(
                media_buy_id="mb_existing_123",
                buyer_ref="test-buyer",
                paused=True,
                packages=[{"package_id": "pkg_1", "paused": True}],
            )
            response = _update_media_buy_impl(req=req, ctx=mock_context)

            # Verify response
            assert response.media_buy_id == "mb_existing_123"
            assert len(response.affected_packages) == 1
            assert response.affected_packages[0].changes_applied.get("dry_run") is True

            # Verify NO workflow step was created
            mock_ctx_manager.return_value.create_workflow_step.assert_not_called()
            mock_ctx_manager.return_value.get_or_create_context.assert_not_called()

            # Verify NO database writes occurred (no add/commit calls)
            mock_session.add.assert_not_called()
            mock_session.commit.assert_not_called()
