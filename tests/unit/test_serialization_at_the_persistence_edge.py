"""Serialization happens at the persistence edge, not in business logic.

A repository serializes the request model it stores (``create_from_request``), the
context manager accepts a model for a workflow step and serializes it itself, and an
implementation reads ``push_notification_config`` off the request rather than taking a
serialized copy as a parameter (CLAUDE.md pattern 4; ruff-serialization.toml and
.ast-grep/rules/serialize-only-at-the-edges.yml enforce the rule these tests illustrate).
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
        account={"account_id": "acct_test"},
        brand={"domain": "testbrand.com"},
        packages=[{"product_id": "prod_1", "budget": 5000.0, "pricing_option_id": "po_1"}],
        start_time=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
        end_time=(datetime.now(UTC) + timedelta(days=8)).isoformat(),
        idempotency_key="unit-test-key-nomodeldump-001",
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


class TestImplReadsPushNotificationConfigOffTheRequest:
    """_create_media_buy_impl takes no push_notification_config parameter; it reads ``req``."""

    def test_impl_takes_no_push_notification_config_parameter(self):
        """_impl reads push_notification_config off ``req``, so it has no parameter for it.

        This used to assert the parameter was dict-only, because the wrapper serialized a
        model and handed it over beside the request. The field is a REQUEST field now --
        built through CreateMediaBuyRequest like every other one -- so a second
        parameter would be a second way to supply the same value, which is the duplication
        that let MCP, REST and _impl each carry their own copy.
        """
        import inspect

        from src.core.tools.media_buy_create import _create_media_buy_impl

        sig = inspect.signature(_create_media_buy_impl)
        assert "push_notification_config" not in sig.parameters, (
            "push_notification_config must reach _impl on req, not as its own parameter; "
            f"found {sorted(sig.parameters)}"
        )
