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

Covers: salesagent-9t7f
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from src.core.database.models import MediaBuy as DBMediaBuy
from src.core.schemas import CreativeStatusEnum
from tests.factories import (
    MediaBuyFactory,
    MediaPackageFactory,
    PrincipalFactory,
    ProductFactory,
    PropertyTagFactory,
    TenantFactory,
)
from tests.harness.creative_sync import CreativeSyncEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_AGENT_URL = "https://test-agent.example.com"
DEFAULT_FORMAT_ID = "display_300x250_image"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
# Test class: Cross-Principal Isolation (BR-RULE-034)
# ---------------------------------------------------------------------------


class TestCrossPrincipalIsolation:
    """BR-RULE-034: Cross-principal creative isolation with real DB.

    Unit stubs: TestCrossPrincipalIsolation in test_creative.py
    Spec: UNSPECIFIED (implementation-defined multi-tenant isolation).
    """

    TENANT_ID = "iso_tenant"

    def test_creative_lookup_filters_by_principal(self, integration_db):
        """Creative upsert lookup uses tenant_id + principal_id + creative_id triple.

        Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-01
        Spec: UNSPECIFIED (implementation-defined multi-tenant isolation).
        Unit stub: TestCrossPrincipalIsolation::test_creative_lookup_filters_by_principal
        """
        with CreativeSyncEnv(tenant_id=self.TENANT_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            PrincipalFactory(tenant=tenant, principal_id="principal_1")
            PrincipalFactory(tenant=tenant, principal_id="principal_2")

            identity = PrincipalFactory.make_identity(
                tenant_id=self.TENANT_ID, principal_id="principal_1", dry_run=True, approval_mode="auto-approve"
            )
            result = env.call_impl(
                creatives=[_make_creative_dict(creative_id="c_filter_test")],
                identity=identity,
            )

        assert result is not None
        assert len(result.creatives) == 1

        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(
                tenant_id=self.TENANT_ID,
                principal_id="principal_1",
                creative_id="c_filter_test",
            )
            db_row = session.scalars(stmt).first()
            assert db_row is not None
            assert db_row.principal_id == "principal_1"

    def test_same_creative_id_different_principal_creates_new(self, integration_db):
        """Same creative_id under different principals creates separate DB records.

        Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-02
        Unit stub: TestCrossPrincipalIsolation::test_same_creative_id_different_principal_creates_new
        """
        with CreativeSyncEnv(tenant_id=self.TENANT_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            PrincipalFactory(tenant=tenant, principal_id="principal_1")
            PrincipalFactory(tenant=tenant, principal_id="principal_2")

            env.call_impl(
                creatives=[_make_creative_dict(creative_id="c_shared")],
                identity=PrincipalFactory.make_identity(
                    tenant_id=self.TENANT_ID, principal_id="principal_1", dry_run=True, approval_mode="auto-approve"
                ),
            )
            env.call_impl(
                creatives=[_make_creative_dict(creative_id="c_shared")],
                identity=PrincipalFactory.make_identity(
                    tenant_id=self.TENANT_ID, principal_id="principal_2", dry_run=True, approval_mode="auto-approve"
                ),
            )

        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(
                tenant_id=self.TENANT_ID,
                creative_id="c_shared",
            )
            rows = session.scalars(stmt).all()
            assert len(rows) == 2
            principal_ids = {r.principal_id for r in rows}
            assert principal_ids == {"principal_1", "principal_2"}

    def test_new_creative_stamped_with_principal_id(self, integration_db):
        """New creative DB record has principal_id from identity.

        Covers: UC-006-CROSS-PRINCIPAL-CREATIVE-03
        Spec: UNSPECIFIED (implementation-defined multi-tenant isolation).
        Unit stub: TestCrossPrincipalIsolation::test_new_creative_stamped_with_principal_id
        """
        with CreativeSyncEnv(tenant_id=self.TENANT_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            PrincipalFactory(tenant=tenant, principal_id="principal_1")

            env.call_impl(
                creatives=[_make_creative_dict(creative_id="c_stamp_test")],
                identity=PrincipalFactory.make_identity(
                    tenant_id=self.TENANT_ID, principal_id="principal_1", dry_run=True, approval_mode="auto-approve"
                ),
            )

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

    def _get_db_status(self, creative_id: str) -> str | None:
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(
                tenant_id=self.TENANT_ID,
                creative_id=creative_id,
            )
            row = session.scalars(stmt).first()
            return row.status if row else None

    def test_auto_approve_sets_approved_status(self, integration_db):
        """Auto-approve mode sets creative status to approved in DB.

        Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-01
        Spec: UNSPECIFIED (implementation-defined approval workflow).
        Unit stub: TestApprovalWorkflow::test_auto_approve_sets_approved_status
        """
        with CreativeSyncEnv(tenant_id=self.TENANT_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            PrincipalFactory(tenant=tenant, principal_id=self.PRINCIPAL_ID)

            identity = PrincipalFactory.make_identity(
                tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID, dry_run=True, approval_mode="auto-approve"
            )
            env.call_impl(
                creatives=[_make_creative_dict(creative_id="c_auto")],
                identity=identity,
            )

        assert self._get_db_status("c_auto") == CreativeStatusEnum.approved.value

    def test_require_human_sets_pending_review(self, integration_db):
        """Require-human mode sets creative status to pending_review in DB.

        Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-02
        Spec: UNSPECIFIED (implementation-defined approval workflow).
        Unit stub: TestApprovalWorkflow::test_require_human_sets_pending_review
        """
        with CreativeSyncEnv(tenant_id=self.TENANT_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            PrincipalFactory(tenant=tenant, principal_id=self.PRINCIPAL_ID)

            identity = PrincipalFactory.make_identity(
                tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID, dry_run=True, approval_mode="require-human"
            )
            env.call_impl(
                creatives=[_make_creative_dict(creative_id="c_human")],
                identity=identity,
            )

        assert self._get_db_status("c_human") == CreativeStatusEnum.pending_review.value

    def test_default_approval_mode_is_require_human(self, integration_db):
        """Tenant with no approval_mode defaults to require-human.

        Covers: UC-006-CREATIVE-APPROVAL-WORKFLOW-04
        Spec: UNSPECIFIED (implementation-defined approval workflow).
        Unit stub: TestApprovalWorkflow::test_default_approval_mode_is_require_human
        """
        with CreativeSyncEnv(tenant_id=self.TENANT_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            PrincipalFactory(tenant=tenant, principal_id=self.PRINCIPAL_ID)

            # Identity with tenant dict that lacks approval_mode key
            identity = PrincipalFactory.make_identity(
                principal_id=self.PRINCIPAL_ID,
                tenant_id=self.TENANT_ID,
                dry_run=True,
            )
            env.call_impl(
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

    def test_batch_sync_multiple_creatives(self, integration_db):
        """Batch of N creatives produces N per-creative results and N DB rows.

        Covers: UC-006-MAIN-MCP-02
        Unit stub: TestSyncCreativesE2E::test_batch_sync_multiple_creatives
        """
        with CreativeSyncEnv(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            PrincipalFactory(tenant=tenant, principal_id=self.PRINCIPAL_ID)

            creatives = [_make_creative_dict(creative_id=f"c_{i}", name=f"Creative {i}") for i in range(5)]
            result = env.call_impl(creatives=creatives)

        assert len(result.creatives) == 5
        result_ids = {r.creative_id for r in result.creatives}
        expected_ids = {f"c_{i}" for i in range(5)}
        assert result_ids == expected_ids

        # Verify all 5 rows in DB
        with get_db_session() as session:
            stmt = select(DBCreative).filter_by(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID)
            rows = session.scalars(stmt).all()
            assert len(rows) == 5

    def test_upsert_by_triple_key(self, integration_db):
        """First sync creates, second sync updates (action=updated).

        Covers: UC-006-MAIN-MCP-03
        Unit stub: TestSyncCreativesE2E::test_upsert_by_triple_key
        """
        with CreativeSyncEnv(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            PrincipalFactory(tenant=tenant, principal_id=self.PRINCIPAL_ID)

            # First sync: create
            result1 = env.call_impl(
                creatives=[_make_creative_dict(creative_id="c_upsert", name="Original Name")],
            )
            action1 = result1.creatives[0].action
            if hasattr(action1, "value"):
                action1 = action1.value
            assert action1 == "created"

            # Second sync: update
            result2 = env.call_impl(
                creatives=[_make_creative_dict(creative_id="c_upsert", name="Updated Name")],
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

    def test_format_mismatch_strict_raises(self, integration_db):
        """Strict mode: display creative assigned to video-only package raises error.

        Covers: UC-006-ASSIGNMENT-FORMAT-COMPATIBILITY-02
        Spec: UNSPECIFIED (implementation-defined format compatibility logic).
        Unit stub: TestFormatCompatibility::test_format_mismatch_strict_raises
        """
        with CreativeSyncEnv(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            principal = PrincipalFactory(tenant=tenant, principal_id=self.PRINCIPAL_ID)
            PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")

            # Product that only supports video_instream_15s
            product = ProductFactory(
                tenant=tenant,
                product_id="prod_video_only",
                name="Video Only Product",
                format_ids=[{"agent_url": DEFAULT_AGENT_URL, "id": "video_instream_15s"}],
                delivery_type="non_guaranteed",
            )

            # Media buy with package pointing to the video-only product
            media_buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_fmt_test",
                status="active",
                buyer_ref="buyer_fmt",
            )
            MediaPackageFactory(
                media_buy=media_buy,
                package_id="pkg_video",
                package_config={"package_id": "pkg_video", "product_id": product.product_id},
            )

            identity = PrincipalFactory.make_identity(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID)

            # First sync the display creative (so it exists in DB)
            env.call_impl(
                creatives=[_make_creative_dict(creative_id="c_display")],
                identity=identity,
            )

            # Now try to assign it to the video-only package in strict mode
            from src.core.exceptions import AdCPValidationError

            with pytest.raises(AdCPValidationError, match="not supported by product"):
                env.call_impl(
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

    def test_draft_with_approved_at_transitions(self, integration_db):
        """Draft media buy with approved_at transitions to pending_creatives on assignment.

        Covers: UC-006-MEDIA-BUY-STATUS-01
        Spec: UNSPECIFIED (implementation-defined status machine).
        Unit stub: TestMediaBuyStatusTransition::test_draft_with_approved_at_transitions
        """
        with CreativeSyncEnv(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID) as env:
            tenant = TenantFactory(tenant_id=self.TENANT_ID)
            principal = PrincipalFactory(tenant=tenant, principal_id=self.PRINCIPAL_ID)
            PropertyTagFactory(tenant=tenant, tag_id="all_inventory", name="All Inventory")

            # Product matching the creative format
            ProductFactory(
                tenant=tenant,
                product_id="prod_display",
                name="Display Product",
                format_ids=[{"agent_url": DEFAULT_AGENT_URL, "id": DEFAULT_FORMAT_ID}],
                delivery_type="non_guaranteed",
            )

            # Draft media buy WITH approved_at set
            media_buy = MediaBuyFactory(
                tenant=tenant,
                principal=principal,
                media_buy_id="mb_draft_approved",
                status="draft",
                buyer_ref="buyer_mb_status",
                approved_at=datetime.now(UTC),
            )
            MediaPackageFactory(
                media_buy=media_buy,
                package_id="pkg_draft",
                package_config={"package_id": "pkg_draft", "product_id": "prod_display"},
            )

            identity = PrincipalFactory.make_identity(tenant_id=self.TENANT_ID, principal_id=self.PRINCIPAL_ID)

            # Sync creative and assign to draft media buy's package
            env.call_impl(
                creatives=[_make_creative_dict(creative_id="c_transition")],
                assignments={"c_transition": ["pkg_draft"]},
                identity=identity,
            )

        # Verify media buy status changed
        with get_db_session() as session:
            stmt = select(DBMediaBuy).filter_by(media_buy_id="mb_draft_approved")
            mb = session.scalars(stmt).first()
            assert mb is not None
            assert mb.status == "pending_creatives"
