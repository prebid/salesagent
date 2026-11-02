"""Unit test: execute_approved_media_buy must write the buy's status after adapter success.

Original defect: the function returned ``(True, None)`` after a successful adapter
execution but never wrote ``media_buy.status`` at all. That intent — a status write
MUST happen, and exactly once — is what this test still guards, and "no write happens"
remains a regression this code could plausibly reintroduce.

THE EXPECTED VALUE CHANGED, deliberately. This test used to assert
``update_status(media_buy_id, "active")``: an unconditional ``ACTIVE``, which was
the defect — a buy approved before its flight window opened was published as
serving. The status is now ``resolve_flight_window_status(...)`` on a re-fetched row,
written together with ``approved_at``/``approved_by`` in the SAME call so one approval
is one write and one revision bump. The fixture below therefore seeds a buy whose
window has not opened and expects ``SCHEDULED``: the point is that the write is the
shared rule's answer, not a constant.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from src.adapters.base import AdapterCreateResult
from src.core.database.models import PersistedMediaBuyStatus
from src.core.database.repositories.creative import CreativeAssignmentRepository
from src.core.tools.media_buy_create import ApprovalOutcome
from tests.factories.principal import PrincipalFactory

# Who approved, and when. Passed in by the caller and written by the same
# ``update_status`` call as the status, so the assertion can name all three.
_APPROVED_BY = "approver@example.com"
_APPROVED_AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _make_mock_media_buy():
    """Build a mock MediaBuy ORM object with minimal fields for execute_approved_media_buy."""
    mb = MagicMock()
    mb.media_buy_id = "mb_test_001"
    mb.tenant_id = "tenant_1"
    mb.principal_id = "principal_1"
    mb.status = "pending_approval"
    mb.order_name = "Test Order"
    mb.advertiser_name = "Test Advertiser"
    # A window that has NOT opened yet — the flight-window rule resolves this to
    # SCHEDULED, which is what distinguishes "the rule was consulted" from the
    # unconditional ACTIVE this test used to assert. Matches the raw_request below.
    mb.start_date = (datetime.now(UTC) + timedelta(days=1)).date()
    mb.end_date = (datetime.now(UTC) + timedelta(days=8)).date()
    mb.start_time = datetime.now(UTC) + timedelta(days=1)
    mb.end_time = datetime.now(UTC) + timedelta(days=8)
    mb.budget = Decimal("5000.00")
    mb.currency = "USD"
    # The PERSISTED request. execute_approved_media_buy no longer rebuilds a
    # CreateMediaBuyRequest out of it: it reads the brand and po_number it needs for the
    # adapter carrier, takes the total off ``budget`` above, and resolves the account
    # from the row's own account_id. A stored payload is a record, not a request, so the
    # keys a request must carry (account, idempotency_key) are no longer consulted here.
    mb.raw_request = {
        "brand": {"domain": "testbrand.com"},
        "start_time": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "end_time": (datetime.now(UTC) + timedelta(days=8)).isoformat(),
        "packages": [{"product_id": "prod_1", "pricing_option_id": "po_1", "budget": 5000.0}],
        "account": {"account_id": "acct_test"},
        "idempotency_key": "approved-exec-key-0001",
    }
    return mb


def _make_mock_tenant():
    """Build a mock Tenant ORM object."""
    tenant = MagicMock()
    tenant.tenant_id = "tenant_1"
    tenant.name = "Test Tenant"
    tenant.subdomain = "test"
    tenant.ad_server = "mock"
    tenant.virtual_host = None
    return tenant


def _make_mock_package():
    """Build a mock MediaPackage DB object."""
    pkg = MagicMock()
    pkg.package_id = "pkg_001"
    pkg.media_buy_id = "mb_test_001"
    pkg.package_config = {"product_id": "prod_1", "name": "Test Package", "budget": 5000.0, "pricing_model": "CPM"}
    return pkg


def _make_mock_product():
    """Build a mock Product ORM object."""
    product = MagicMock()
    product.product_id = "prod_1"
    product.name = "Test Product"
    product.delivery_type = "non_guaranteed"
    product.format_ids = [{"agent_url": "https://example.com/formats", "format_id": "fmt_1", "id": "fmt_1"}]

    # Set up pricing option
    pricing_option = MagicMock()
    pricing_option.pricing_model = "CPM"
    pricing_option.rate = Decimal("10.00")
    pricing_option.currency = "USD"
    pricing_option.is_fixed = True
    pricing_option.root = pricing_option  # Self-reference for getattr(po, "root", po)
    product.pricing_options = [pricing_option]

    return product


class TestExecuteApprovedStatusUpdate:
    """execute_approved_media_buy must write the resolved status after adapter success."""

    def test_status_write_after_adapter_success_is_the_resolved_status(self):
        """One ``update_status`` call, carrying the flight-window status and the stamps.

        See the module docstring for why the expected value is ``SCHEDULED`` and no
        longer ``"active"``.
        """
        # -- Arrange --
        tenant = _make_mock_tenant()
        media_buy = _make_mock_media_buy()
        db_package = _make_mock_package()
        product = _make_mock_product()

        adapter_response = AdapterCreateResult(
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
        # 4. Re-fetch the row and write the resolved status
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

        # UoW 4 re-fetches the row and writes the resolved status. get_by_id must
        # return the buy itself: the resolver reads its flight window off that row,
        # and a bare MagicMock has no orderable datetimes.
        mock_repo_3 = MagicMock()
        mock_repo_3.get_by_id.return_value = media_buy
        mock_uow_3 = MagicMock()
        mock_uow_3.__enter__ = MagicMock(return_value=mock_uow_3)
        mock_uow_3.__exit__ = MagicMock(return_value=None)
        mock_uow_3.session = mock_session_3
        mock_uow_3.media_buys = mock_repo_3

        uow_iter = iter([mock_uow_1, mock_uow_plids, mock_uow_2, mock_uow_3])

        with (
            patch("src.core.database.repositories.MediaBuyUoW", side_effect=lambda _: next(uow_iter)),
            patch(
                "src.core.config_loader.get_tenant_by_id",
                return_value={"tenant_id": "tenant_1", "adapter_type": "mock"},
            ),
            # Resolution from the stored ids is the resolver's (``identity_of``), and it
            # reads the tenant and principal rows this unit test has no database for.
            # The identity it would have built comes from the factory instead.
            patch(
                "src.core.tools.media_buy_create.identity_of",
                return_value=PrincipalFactory.make_identity(principal_id="principal_1", tenant_id="tenant_1"),
            ),
            patch(
                "src.core.tools.media_buy_create._execute_adapter_media_buy_creation",
                return_value=adapter_response,
            ),
            patch("src.core.tools.media_buy_create._validate_creatives_before_adapter_call"),
            patch("src.core.helpers.adapter_helpers.get_adapter", return_value=mock_adapter),
            # The creative gate is a separate concern with its own tests; this buy has
            # nothing outstanding, so the run reaches the adapter and the status write.
            patch.object(CreativeAssignmentRepository, "unapproved_creative_ids", return_value=[]),
        ):
            from src.core.tools.media_buy_create import execute_approved_media_buy

            result = execute_approved_media_buy(
                "mb_test_001",
                "tenant_1",
                approved_by=_APPROVED_BY,
                approved_at=_APPROVED_AT,
            )

        # -- Assert --
        assert result.outcome is ApprovalOutcome.EXECUTED, f"expected EXECUTED, got {result}"
        assert result.status is PersistedMediaBuyStatus.SCHEDULED

        # THE KEY ASSERTION: exactly one status write, carrying the resolved status
        # and both approval stamps. ``assert_called_once_with`` is what makes it a
        # single-writer assertion — a second write from anywhere fails it.
        mock_repo_3.update_status.assert_called_once_with(
            "mb_test_001",
            PersistedMediaBuyStatus.SCHEDULED,
            # Asserted, not tolerated. This call is reached only AFTER the adapter
            # created the order, which is the moment the seller commits — so it is
            # also the write that must claim the commitment. The creative-review hold
            # returns before the adapter and leaves the default, so pinning the flag
            # here is what keeps the two paths distinguishable at the single writer.
            seller_committed=True,
            approved_at=_APPROVED_AT,
            approved_by=_APPROVED_BY,
        )
