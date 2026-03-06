"""Integration tests for creative entity (v3.6 migration batch).

Tests derived from unit test stubs in tests/unit/test_creative.py.
These verify the SAME behaviors with real PostgreSQL instead of mocks.

Iron Rule: If an integration test fails, the production code is wrong --
never adjust the expected behavior from the unit test stub.

Covers:
- Cross-principal isolation (BR-RULE-034)
- Approval workflow modes (BR-RULE-037)
- Batch sync with real DB
- Upsert by triple key
- Format compatibility during assignment (BR-RULE-039)
- Media buy status transition on creative assignment (BR-RULE-040)
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from src.core.database.models import (
    CurrencyLimit,
    MediaBuy,
    MediaPackage,
    Principal,
)
from src.core.database.models import Product as DBProduct
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import CreativeStatusEnum, SyncCreativesResponse
from src.core.testing_hooks import AdCPTestContext
from tests.utils.database_helpers import create_tenant_with_timestamps

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_AGENT_URL = "https://test-agent.example.com"
DEFAULT_FORMAT_ID = "display_300x250_image"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_identity(
    tenant_id: str,
    principal_id: str,
    approval_mode: str = "auto-approve",
) -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant={"tenant_id": tenant_id, "approval_mode": approval_mode},
        testing_context=AdCPTestContext(dry_run=True, test_session_id="test_session"),
        protocol="mcp",
    )


def _make_creative_dict(
    creative_id: str = "c_test_1",
    name: str = "Test Creative",
    format_id: str = DEFAULT_FORMAT_ID,
    agent_url: str = DEFAULT_AGENT_URL,
) -> dict:
    return {
        "creative_id": creative_id,
        "name": name,
        "format_id": {"agent_url": agent_url, "id": format_id},
        "assets": {},
        "url": "https://example.com/banner.png",
        "width": 300,
        "height": 250,
    }


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_format_registry():
    """Mock creative agent format registry to avoid real HTTP calls."""
    from tests.helpers.adcp_factories import create_test_format

    mock_formats = {
        DEFAULT_FORMAT_ID: create_test_format(
            format_id=DEFAULT_FORMAT_ID,
            name="Display 300x250 Image",
            type="display",
        ),
        "video_instream_15s": create_test_format(
            format_id="video_instream_15s",
            name="Video Instream 15s",
            type="video",
        ),
    }

    with patch("src.core.creative_agent_registry.CreativeAgentRegistry.get_format") as mock_get:

        def get_format_side_effect(agent_url, format_id):
            return mock_formats.get(format_id)

        mock_get.side_effect = get_format_side_effect
        yield mock_get


# ---------------------------------------------------------------------------
# Test class: Cross-Principal Isolation (BR-RULE-034)
# ---------------------------------------------------------------------------


class TestCrossPrincipalIsolation:
    """BR-RULE-034: Cross-principal creative isolation with real DB.

    Unit stubs: TestCrossPrincipalIsolation in test_creative.py
    Spec: UNSPECIFIED (implementation-defined multi-tenant isolation).
    """

    TENANT_ID = "iso_tenant"

    @pytest.fixture(autouse=True)
    def setup_tenant(self, integration_db):
        """Create tenant with two principals."""
        with get_db_session() as session:
            tenant = create_tenant_with_timestamps(
                tenant_id=self.TENANT_ID,
                name="Isolation Test Tenant",
                subdomain="iso-test",
                is_active=True,
                ad_server="mock",
                approval_mode="auto-approve",
            )
            session.add(tenant)
            session.add(
                CurrencyLimit(
                    tenant_id=self.TENANT_ID,
                    currency_code="USD",
                    min_package_budget=100.0,
                    max_daily_package_spend=10000.0,
                )
            )
            session.add(
                Principal(
                    tenant_id=self.TENANT_ID,
                    principal_id="principal_1",
                    name="Principal 1",
                    access_token="token_p1",
                    platform_mappings={"mock": {"id": "p1"}},
                )
            )
            session.add(
                Principal(
                    tenant_id=self.TENANT_ID,
                    principal_id="principal_2",
                    name="Principal 2",
                    access_token="token_p2",
                    platform_mappings={"mock": {"id": "p2"}},
                )
            )
            session.commit()

    def _sync_one(self, principal_id: str, creative_id: str = "c_shared") -> SyncCreativesResponse:
        from src.core.tools.creatives import sync_creatives_raw

        identity = _make_identity(self.TENANT_ID, principal_id)
        return sync_creatives_raw(
            creatives=[_make_creative_dict(creative_id=creative_id)],
            identity=identity,
        )

    def test_creative_lookup_filters_by_principal(self):
        """Creative upsert lookup uses tenant_id + principal_id + creative_id triple.

        Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-01
        Spec: UNSPECIFIED (implementation-defined multi-tenant isolation).
        Unit stub: TestCrossPrincipalIsolation::test_creative_lookup_filters_by_principal
        """
        result = self._sync_one("principal_1", "c_filter_test")
        assert result is not None
        assert len(result.creatives) == 1

        # Verify DB row has correct principal
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(
                tenant_id=self.TENANT_ID,
                principal_id="principal_1",
                creative_id="c_filter_test",
            )
            db_row = session.scalars(stmt).first()
            assert db_row is not None
            assert db_row.principal_id == "principal_1"

    def test_same_creative_id_different_principal_creates_new(self):
        """Same creative_id under different principals creates separate DB records.

        Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-02
        Unit stub: TestCrossPrincipalIsolation::test_same_creative_id_different_principal_creates_new
        """
        self._sync_one("principal_1", "c_shared")
        self._sync_one("principal_2", "c_shared")

        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(
                tenant_id=self.TENANT_ID,
                creative_id="c_shared",
            )
            rows = session.scalars(stmt).all()
            assert len(rows) == 2
            principal_ids = {r.principal_id for r in rows}
            assert principal_ids == {"principal_1", "principal_2"}

    def test_new_creative_stamped_with_principal_id(self):
        """New creative DB record has principal_id from identity.

        Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-03
        Spec: UNSPECIFIED (implementation-defined multi-tenant isolation).
        Unit stub: TestCrossPrincipalIsolation::test_new_creative_stamped_with_principal_id
        """
        self._sync_one("principal_1", "c_stamp_test")

        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(
                tenant_id=self.TENANT_ID,
                creative_id="c_stamp_test",
            )
            db_row = session.scalars(stmt).first()
            assert db_row is not None
            assert db_row.principal_id == "principal_1"


# ---------------------------------------------------------------------------
# Test class: Approval Workflow (BR-RULE-037)
# ---------------------------------------------------------------------------


class TestApprovalWorkflow:
    """BR-RULE-037: Creative approval modes with real DB.

    Unit stubs: TestApprovalWorkflow in test_creative.py
    Spec: UNSPECIFIED (implementation-defined approval workflow).
    """

    TENANT_ID = "approval_tenant"
    PRINCIPAL_ID = "approval_principal"

    @pytest.fixture(autouse=True)
    def setup_tenant(self, integration_db):
        """Create tenant and principal for approval tests."""
        with get_db_session() as session:
            tenant = create_tenant_with_timestamps(
                tenant_id=self.TENANT_ID,
                name="Approval Test Tenant",
                subdomain="approval-test",
                is_active=True,
                ad_server="mock",
                approval_mode="auto-approve",
            )
            session.add(tenant)
            session.add(
                CurrencyLimit(
                    tenant_id=self.TENANT_ID,
                    currency_code="USD",
                    min_package_budget=100.0,
                    max_daily_package_spend=10000.0,
                )
            )
            session.add(
                Principal(
                    tenant_id=self.TENANT_ID,
                    principal_id=self.PRINCIPAL_ID,
                    name="Test Advertiser",
                    access_token="token_approval",
                    platform_mappings={"mock": {"id": "adv1"}},
                )
            )
            session.commit()

    def _sync(self, approval_mode: str, creative_id: str) -> SyncCreativesResponse:
        from src.core.tools.creatives import sync_creatives_raw

        identity = _make_identity(self.TENANT_ID, self.PRINCIPAL_ID, approval_mode=approval_mode)
        return sync_creatives_raw(
            creatives=[_make_creative_dict(creative_id=creative_id)],
            identity=identity,
        )

    def _get_db_status(self, creative_id: str) -> str | None:
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(
                tenant_id=self.TENANT_ID,
                creative_id=creative_id,
            )
            row = session.scalars(stmt).first()
            return row.status if row else None

    def test_auto_approve_sets_approved_status(self):
        """Auto-approve mode sets creative status to approved in DB.

        Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-01
        Spec: UNSPECIFIED (implementation-defined approval workflow).
        Unit stub: TestApprovalWorkflow::test_auto_approve_sets_approved_status
        """
        self._sync("auto-approve", "c_auto")
        assert self._get_db_status("c_auto") == CreativeStatusEnum.approved.value

    def test_require_human_sets_pending_review(self):
        """Require-human mode sets creative status to pending_review in DB.

        Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-02
        Spec: UNSPECIFIED (implementation-defined approval workflow).
        Unit stub: TestApprovalWorkflow::test_require_human_sets_pending_review
        """
        self._sync("require-human", "c_human")
        assert self._get_db_status("c_human") == CreativeStatusEnum.pending_review.value

    def test_default_approval_mode_is_require_human(self):
        """Tenant with no approval_mode defaults to require-human.

        Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-04
        Spec: UNSPECIFIED (implementation-defined approval workflow).
        Unit stub: TestApprovalWorkflow::test_default_approval_mode_is_require_human
        """
        from src.core.tools.creatives import sync_creatives_raw

        # Identity with tenant dict that lacks approval_mode key
        identity = ResolvedIdentity(
            principal_id=self.PRINCIPAL_ID,
            tenant_id=self.TENANT_ID,
            tenant={"tenant_id": self.TENANT_ID},  # No approval_mode key
            testing_context=AdCPTestContext(dry_run=True, test_session_id="test_session"),
            protocol="mcp",
        )
        sync_creatives_raw(
            creatives=[_make_creative_dict(creative_id="c_default")],
            identity=identity,
        )
        assert self._get_db_status("c_default") == CreativeStatusEnum.pending_review.value


# ---------------------------------------------------------------------------
# Test class: Batch Sync (real DB)
# ---------------------------------------------------------------------------


class TestBatchSync:
    """Batch sync and upsert with real DB.

    Unit stubs: TestSyncCreativesE2E in test_creative.py
    """

    TENANT_ID = "batch_tenant"
    PRINCIPAL_ID = "batch_principal"

    @pytest.fixture(autouse=True)
    def setup_tenant(self, integration_db):
        with get_db_session() as session:
            tenant = create_tenant_with_timestamps(
                tenant_id=self.TENANT_ID,
                name="Batch Test Tenant",
                subdomain="batch-test",
                is_active=True,
                ad_server="mock",
                approval_mode="auto-approve",
            )
            session.add(tenant)
            session.add(
                CurrencyLimit(
                    tenant_id=self.TENANT_ID,
                    currency_code="USD",
                    min_package_budget=100.0,
                    max_daily_package_spend=10000.0,
                )
            )
            session.add(
                Principal(
                    tenant_id=self.TENANT_ID,
                    principal_id=self.PRINCIPAL_ID,
                    name="Batch Advertiser",
                    access_token="token_batch",
                    platform_mappings={"mock": {"id": "batch_adv"}},
                )
            )
            session.commit()

    def _identity(self) -> ResolvedIdentity:
        return _make_identity(self.TENANT_ID, self.PRINCIPAL_ID)

    def test_batch_sync_multiple_creatives(self):
        """Batch of N creatives produces N per-creative results and N DB rows.

        Covers: UC-006-MAIN-MCP-02
        Unit stub: TestSyncCreativesE2E::test_batch_sync_multiple_creatives
        """
        from src.core.tools.creatives import sync_creatives_raw

        creatives = [_make_creative_dict(creative_id=f"c_{i}", name=f"Creative {i}") for i in range(5)]
        result = sync_creatives_raw(creatives=creatives, identity=self._identity())

        assert len(result.creatives) == 5
        result_ids = {r.creative_id for r in result.creatives}
        expected_ids = {f"c_{i}" for i in range(5)}
        assert result_ids == expected_ids

        # Verify all 5 rows in DB
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID)
            rows = session.scalars(stmt).all()
            assert len(rows) == 5

    def test_upsert_by_triple_key(self):
        """First sync creates, second sync updates (action=updated).

        Covers: UC-006-MAIN-MCP-03
        Unit stub: TestSyncCreativesE2E::test_upsert_by_triple_key
        """
        from src.core.tools.creatives import sync_creatives_raw

        identity = self._identity()

        # First sync: create
        result1 = sync_creatives_raw(
            creatives=[_make_creative_dict(creative_id="c_upsert", name="Original Name")],
            identity=identity,
        )
        action1 = result1.creatives[0].action
        if hasattr(action1, "value"):
            action1 = action1.value
        assert action1 == "created"

        # Second sync: update
        result2 = sync_creatives_raw(
            creatives=[_make_creative_dict(creative_id="c_upsert", name="Updated Name")],
            identity=identity,
        )
        action2 = result2.creatives[0].action
        if hasattr(action2, "value"):
            action2 = action2.value
        assert action2 == "updated"

        # Still just one row in DB (upsert, not duplicate)
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(
                tenant_id=self.TENANT_ID,
                creative_id="c_upsert",
            )
            rows = session.scalars(stmt).all()
            assert len(rows) == 1


# ---------------------------------------------------------------------------
# Test class: Format Compatibility (BR-RULE-039)
# ---------------------------------------------------------------------------


class TestFormatCompatibility:
    """BR-RULE-039: Assignment format compatibility with real DB.

    Unit stub: TestFormatCompatibility::test_format_mismatch_strict_raises
    Spec: UNSPECIFIED (implementation-defined format compatibility logic).
    """

    TENANT_ID = "fmt_compat_tenant"
    PRINCIPAL_ID = "fmt_compat_principal"

    @pytest.fixture(autouse=True)
    def setup_tenant(self, integration_db):
        with get_db_session() as session:
            tenant = create_tenant_with_timestamps(
                tenant_id=self.TENANT_ID,
                name="Format Compat Tenant",
                subdomain="fmt-compat-test",
                is_active=True,
                ad_server="mock",
                approval_mode="auto-approve",
            )
            session.add(tenant)
            session.add(
                CurrencyLimit(
                    tenant_id=self.TENANT_ID,
                    currency_code="USD",
                    min_package_budget=100.0,
                    max_daily_package_spend=10000.0,
                )
            )
            session.add(
                Principal(
                    tenant_id=self.TENANT_ID,
                    principal_id=self.PRINCIPAL_ID,
                    name="Format Compat Advertiser",
                    access_token="token_fmt",
                    platform_mappings={"mock": {"id": "fmt_adv"}},
                )
            )
            # Product that only supports video_instream_15s
            product = DBProduct(
                tenant_id=self.TENANT_ID,
                product_id="prod_video_only",
                name="Video Only Product",
                description="Only supports video",
                format_ids=[
                    {"agent_url": DEFAULT_AGENT_URL, "id": "video_instream_15s"},
                ],
                targeting_template={},
                delivery_type="non_guaranteed",
                properties=[{"publisher_domain": "test.com", "selection_type": "all"}],
            )
            session.add(product)

            # Media buy with package pointing to the video-only product
            mb = MediaBuy(
                tenant_id=self.TENANT_ID,
                media_buy_id="mb_fmt_test",
                principal_id=self.PRINCIPAL_ID,
                order_name="Format Test Order",
                advertiser_name="Format Compat Advertiser",
                status="active",
                budget=5000.0,
                start_date=datetime.now(UTC).date(),
                end_date=(datetime.now(UTC) + timedelta(days=30)).date(),
                buyer_ref="buyer_fmt",
                raw_request={"packages": [{"package_id": "pkg_video", "paused": False}]},
            )
            session.add(mb)
            session.commit()

            pkg = MediaPackage(
                media_buy_id="mb_fmt_test",
                package_id="pkg_video",
                package_config={"package_id": "pkg_video", "product_id": "prod_video_only"},
            )
            session.add(pkg)
            session.commit()

    def test_format_mismatch_strict_raises(self):
        """Strict mode: display creative assigned to video-only package raises error.

        Covers: UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-02
        Spec: UNSPECIFIED (implementation-defined format compatibility logic).
        Unit stub: TestFormatCompatibility::test_format_mismatch_strict_raises
        """
        from src.core.exceptions import AdCPValidationError
        from src.core.tools.creatives import sync_creatives_raw

        identity = _make_identity(self.TENANT_ID, self.PRINCIPAL_ID)

        # First sync the display creative (so it exists in DB)
        sync_creatives_raw(
            creatives=[_make_creative_dict(creative_id="c_display")],
            identity=identity,
        )

        # Now try to assign it to the video-only package in strict mode
        with pytest.raises(AdCPValidationError, match="not supported by product"):
            sync_creatives_raw(
                creatives=[_make_creative_dict(creative_id="c_display")],
                assignments={"c_display": ["pkg_video"]},
                validation_mode="strict",
                identity=identity,
            )


# ---------------------------------------------------------------------------
# Test class: Media Buy Status Transition (BR-RULE-040)
# ---------------------------------------------------------------------------


class TestMediaBuyStatusTransition:
    """BR-RULE-040: Media buy status transitions on creative assignment.

    Unit stub: TestMediaBuyStatusTransition::test_draft_with_approved_at_transitions
    Spec: UNSPECIFIED (implementation-defined status machine).
    """

    TENANT_ID = "mb_status_tenant"
    PRINCIPAL_ID = "mb_status_principal"

    @pytest.fixture(autouse=True)
    def setup_tenant(self, integration_db):
        with get_db_session() as session:
            tenant = create_tenant_with_timestamps(
                tenant_id=self.TENANT_ID,
                name="MB Status Tenant",
                subdomain="mb-status-test",
                is_active=True,
                ad_server="mock",
                approval_mode="auto-approve",
            )
            session.add(tenant)
            session.add(
                CurrencyLimit(
                    tenant_id=self.TENANT_ID,
                    currency_code="USD",
                    min_package_budget=100.0,
                    max_daily_package_spend=10000.0,
                )
            )
            session.add(
                Principal(
                    tenant_id=self.TENANT_ID,
                    principal_id=self.PRINCIPAL_ID,
                    name="MB Status Advertiser",
                    access_token="token_mb_status",
                    platform_mappings={"mock": {"id": "mb_status_adv"}},
                )
            )
            # Product matching the creative format
            product = DBProduct(
                tenant_id=self.TENANT_ID,
                product_id="prod_display",
                name="Display Product",
                description="Supports display creatives",
                format_ids=[
                    {"agent_url": DEFAULT_AGENT_URL, "id": DEFAULT_FORMAT_ID},
                ],
                targeting_template={},
                delivery_type="non_guaranteed",
                properties=[{"publisher_domain": "test.com", "selection_type": "all"}],
            )
            session.add(product)

            # Draft media buy WITH approved_at set
            mb = MediaBuy(
                tenant_id=self.TENANT_ID,
                media_buy_id="mb_draft_approved",
                principal_id=self.PRINCIPAL_ID,
                order_name="Draft Approved Order",
                advertiser_name="MB Status Advertiser",
                status="draft",
                budget=5000.0,
                start_date=datetime.now(UTC).date(),
                end_date=(datetime.now(UTC) + timedelta(days=30)).date(),
                buyer_ref="buyer_mb_status",
                approved_at=datetime.now(UTC),
                raw_request={"packages": [{"package_id": "pkg_draft", "paused": False}]},
            )
            session.add(mb)
            session.commit()

            pkg = MediaPackage(
                media_buy_id="mb_draft_approved",
                package_id="pkg_draft",
                package_config={"package_id": "pkg_draft", "product_id": "prod_display"},
            )
            session.add(pkg)
            session.commit()

    def test_draft_with_approved_at_transitions(self):
        """Draft media buy with approved_at transitions to pending_creatives on assignment.

        Covers: UC-006-MEDIA-BUY-STATUS-01
        Spec: UNSPECIFIED (implementation-defined status machine).
        Unit stub: TestMediaBuyStatusTransition::test_draft_with_approved_at_transitions
        """
        from src.core.tools.creatives import sync_creatives_raw

        identity = _make_identity(self.TENANT_ID, self.PRINCIPAL_ID)

        # Sync creative and assign to draft media buy's package
        sync_creatives_raw(
            creatives=[_make_creative_dict(creative_id="c_transition")],
            assignments={"c_transition": ["pkg_draft"]},
            identity=identity,
        )

        # Verify media buy status changed
        with get_db_session() as session:
            stmt = select(MediaBuy).filter_by(media_buy_id="mb_draft_approved")
            mb = session.scalars(stmt).first()
            assert mb is not None
            assert mb.status == "pending_creatives"
