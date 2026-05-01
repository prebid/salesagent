"""Tests for model_dump removal from _impl functions.

Validates that serialization happens at repository/transport boundaries,
not inside business logic. These tests drive the fix for salesagent-lfto.

beads: salesagent-lfto
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from src.core.database.models import MediaBuy
from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.schemas import CreateMediaBuyRequest


def _make_minimal_request() -> CreateMediaBuyRequest:
    """Build a minimal CreateMediaBuyRequest for testing."""
    return CreateMediaBuyRequest(
        brand={"domain": "testbrand.com"},
        packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "po_1"}],
        start_time=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
        end_time=(datetime.now(UTC) + timedelta(days=8)).isoformat(),
    )


class TestMediaBuyRepositoryCreateFromRequest:
    """MediaBuyRepository.create_from_request() must serialize the request model internally."""

    def test_create_from_request_exists(self):
        """Repository must have a create_from_request method."""
        assert hasattr(MediaBuyRepository, "create_from_request"), (
            "MediaBuyRepository should have create_from_request() — "
            "serialization of raw_request belongs in the repository, not _impl"
        )

    def test_create_from_request_returns_media_buy(self):
        """create_from_request must return a MediaBuy ORM object."""
        session = MagicMock()
        repo = MediaBuyRepository(session=session, tenant_id="tenant_1")
        req = _make_minimal_request()

        result = repo.create_from_request(
            media_buy_id="mb_test_001",
            req=req,
            principal_id="principal_1",
            advertiser_name="Test Advertiser",
            budget=Decimal("5000.00"),
            currency="USD",
            start_time=datetime.now(UTC) + timedelta(days=1),
            end_time=datetime.now(UTC) + timedelta(days=8),
            status="active",
        )

        assert isinstance(result, MediaBuy)
        assert result.media_buy_id == "mb_test_001"
        assert result.tenant_id == "tenant_1"

    def test_create_from_request_serializes_raw_request(self):
        """raw_request must be a dict (serialized from the model), not a Pydantic object."""
        session = MagicMock()
        repo = MediaBuyRepository(session=session, tenant_id="tenant_1")
        req = _make_minimal_request()

        result = repo.create_from_request(
            media_buy_id="mb_test_002",
            req=req,
            principal_id="principal_1",
            advertiser_name="Test Advertiser",
            budget=Decimal("5000.00"),
            currency="USD",
            start_time=datetime.now(UTC) + timedelta(days=1),
            end_time=datetime.now(UTC) + timedelta(days=8),
            status="active",
        )

        # raw_request must be a dict, not a Pydantic model
        assert isinstance(result.raw_request, dict), (
            f"raw_request should be dict, got {type(result.raw_request).__name__}"
        )
        # buyer_ref removed from CreateMediaBuyRequest in adcp 3.12
        assert "brand" in result.raw_request

    def test_create_from_request_injects_package_ids(self):
        """When package_id_map is provided, package_ids must be injected into serialized packages."""
        session = MagicMock()
        repo = MediaBuyRepository(session=session, tenant_id="tenant_1")
        req = _make_minimal_request()

        result = repo.create_from_request(
            media_buy_id="mb_test_003",
            req=req,
            principal_id="principal_1",
            advertiser_name="Test Advertiser",
            budget=Decimal("5000.00"),
            currency="USD",
            start_time=datetime.now(UTC) + timedelta(days=1),
            end_time=datetime.now(UTC) + timedelta(days=8),
            status="pending_approval",
            package_id_map={0: "pkg_prod_1_abc123_1"},
        )

        # Package ID should be injected into the serialized packages
        packages = result.raw_request.get("packages", [])
        assert len(packages) == 1
        assert packages[0].get("package_id") == "pkg_prod_1_abc123_1"

    def test_create_from_request_adds_to_session(self):
        """Repository must add the MediaBuy to the session."""
        session = MagicMock()
        repo = MediaBuyRepository(session=session, tenant_id="tenant_1")
        req = _make_minimal_request()

        repo.create_from_request(
            media_buy_id="mb_test_004",
            req=req,
            principal_id="principal_1",
            advertiser_name="Test Advertiser",
            budget=Decimal("5000.00"),
            currency="USD",
            start_time=datetime.now(UTC) + timedelta(days=1),
            end_time=datetime.now(UTC) + timedelta(days=8),
            status="active",
        )

        session.add.assert_called_once()
        session.flush.assert_called_once()


class TestContextManagerAcceptsModel:
    """ContextManager.create_workflow_step must accept BaseModel for request_data."""

    def test_create_workflow_step_accepts_pydantic_model(self):
        """create_workflow_step should serialize BaseModel request_data to dict internally."""
        # ContextManager.create_workflow_step signature should accept BaseModel
        import inspect

        from src.core.context_manager import ContextManager

        sig = inspect.signature(ContextManager.create_workflow_step)
        # request_data parameter should exist
        assert "request_data" in sig.parameters


class TestImplNoModelDump:
    """_create_media_buy_impl must not accept BaseModel for push_notification_config."""

    def test_impl_push_notification_config_is_dict_only(self):
        """_impl should only accept dict|None for push_notification_config, not BaseModel.

        The transport wrapper is responsible for serializing the model before calling _impl.
        """
        import inspect

        from src.core.tools.media_buy_create import _create_media_buy_impl

        sig = inspect.signature(_create_media_buy_impl)
        param = sig.parameters["push_notification_config"]
        annotation = str(param.annotation)
        # Should NOT contain "BaseModel"
        assert "BaseModel" not in annotation, (
            f"push_notification_config should be dict|None, not {annotation}. "
            "Transport wrapper must serialize before calling _impl."
        )
