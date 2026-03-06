"""Regression tests: principal_id NOT NULL on creative_assignments.

Three production code sites construct DBAssignment() and must include
principal_id to satisfy the NOT NULL constraint:

1. media_buy_create.py ~line 2252  (manual approval path)
2. media_buy_create.py ~line 3208  (auto-approve path)
3. media_buy_update.py ~line 769   (update with creative_ids)

Each test verifies that creative_assignment rows have principal_id populated
after the respective code path executes. Mutation verification: temporarily
removing principal_id= from the production site should cause IntegrityError.

Covers: salesagent-9t7f
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import CreativeAssignment as DBAssignment
from src.core.database.models import MediaPackage as DBMediaPackage
from src.core.database.models import Tenant as TenantModel
from src.core.schemas import (
    CreateMediaBuyRequest,
    UpdateMediaBuyRequest,
)
from tests.factories import CreativeFactory, PrincipalFactory
from tests.harness._base import IntegrationEnv
from tests.helpers.adcp_factories import create_test_format

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Custom test environment — patches _get_format_spec_sync only.
# ---------------------------------------------------------------------------

DEFAULT_FORMAT_ID = "display_300x250"


class _AssignmentTestEnv(IntegrationEnv):
    """Integration env for creative assignment principal_id tests.

    Patches _get_format_spec_sync to avoid asyncio.run() inside running event loop.
    The auto-approve path calls _get_format_spec_sync which wraps
    CreativeAgentRegistry.get_format() in asyncio.run(). This fails inside
    pytest-asyncio. We mock the sync wrapper directly to return a valid format.
    """

    EXTERNAL_PATCHES = {
        "format_spec": "src.core.tools.media_buy_create._get_format_spec_sync",
    }

    def _configure_mocks(self) -> None:
        mock_formats = {
            DEFAULT_FORMAT_ID: create_test_format(
                format_id=DEFAULT_FORMAT_ID,
                name="Display 300x250",
                type="display",
            ),
        }
        self.mock["format_spec"].side_effect = lambda agent_url, format_id: mock_formats.get(format_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future(days: int = 1) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def _get_tenant_dict(tenant_id: str) -> dict[str, Any]:
    """Load full tenant dict from DB (matches resolve_identity output)."""
    with get_db_session() as session:
        stmt = select(TenantModel).where(TenantModel.tenant_id == tenant_id)
        tenant = session.scalars(stmt).first()
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} not found")
        return {
            "tenant_id": tenant.tenant_id,
            "name": tenant.name,
            "subdomain": tenant.subdomain,
            "ad_server": tenant.ad_server,
            "human_review_required": tenant.human_review_required,
            "auto_create_media_buys": getattr(tenant, "auto_create_media_buys", True),
            "slack_webhook_url": getattr(tenant, "slack_webhook_url", None),
            "slack_audit_webhook_url": getattr(tenant, "slack_audit_webhook_url", None),
        }


def _make_create_request(**overrides: Any) -> CreateMediaBuyRequest:
    defaults: dict[str, Any] = {
        "buyer_ref": f"test-buyer-{uuid.uuid4().hex[:8]}",
        "brand": {"domain": "testbrand.com"},
        "start_time": _future(1),
        "end_time": _future(8),
        "packages": [
            {
                "product_id": "guaranteed_display",
                "buyer_ref": "pkg-1",
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
            }
        ],
    }
    defaults.update(overrides)
    return CreateMediaBuyRequest(**defaults)


def _query_assignments(tenant_id: str, media_buy_id: str) -> list[DBAssignment]:
    """Query all creative_assignment rows for a given media buy."""
    with get_db_session() as session:
        stmt = select(DBAssignment).where(
            DBAssignment.tenant_id == tenant_id,
            DBAssignment.media_buy_id == media_buy_id,
        )
        return list(session.scalars(stmt).all())


def _create_test_creatives(env: _AssignmentTestEnv, tenant_id: str, principal_id: str) -> list[str]:
    """Create test creatives using factory (inside env context for session binding).

    Loads the Tenant ORM object from the env session to satisfy SQLAlchemy's
    relationship sync. principal=None is safe because CreativeFactory excludes
    it from Meta (won't be passed to ORM constructor).
    """
    # Load tenant from env's factory session (conftest already committed it)
    tenant_orm = env._session.scalars(select(TenantModel).where(TenantModel.tenant_id == tenant_id)).first()
    assert tenant_orm is not None, f"Tenant {tenant_id} not found in env session"

    creative_ids = ["c_regress_1", "c_regress_2"]
    for cid in creative_ids:
        CreativeFactory(
            tenant=tenant_orm,  # Real ORM object — avoids relationship blank-out
            principal=None,  # Excluded from Meta — won't be passed to ORM
            creative_id=cid,
            tenant_id=tenant_id,
            principal_id=principal_id,
            name=f"Regression Creative {cid}",
            format="display_300x250",
            data={
                "url": "https://example.com/creative.jpg",
                "width": 300,
                "height": 250,
                "primary": {"url": "https://example.com/creative.jpg"},
                "platform_creative_id": f"mock_creative_{cid}",
            },
        )
    return creative_ids


# ---------------------------------------------------------------------------
# Site 1: manual approval path (media_buy_create.py ~line 2252)
# ---------------------------------------------------------------------------


class TestCreativeAssignmentPrincipalIdManualApproval:
    """Regression: DBAssignment in manual approval create path must include principal_id."""

    @pytest.mark.asyncio
    async def test_assignment_has_principal_id_on_manual_approval_create(
        self,
        integration_db,
        sample_tenant,
        sample_principal,
        sample_products,
    ):
        """Site 1: creative_assignments created during manual-approval create_media_buy
        must have principal_id populated (NOT NULL constraint).

        Covers: UC-006-ASSIGNMENT-PRINCIPAL-ID-01
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        with _AssignmentTestEnv() as env:
            # Set tenant to require human review
            with get_db_session() as session:
                stmt = select(TenantModel).where(TenantModel.tenant_id == sample_tenant["tenant_id"])
                tenant_obj = session.scalars(stmt).first()
                assert tenant_obj is not None
                tenant_obj.human_review_required = True
                session.commit()

            tenant_dict = _get_tenant_dict(sample_tenant["tenant_id"])
            creative_ids = _create_test_creatives(env, sample_tenant["tenant_id"], sample_principal["principal_id"])
            env._commit_factory_data()

            identity = PrincipalFactory.make_identity(
                principal_id=sample_principal["principal_id"],
                tenant_id=sample_tenant["tenant_id"],
                tenant=tenant_dict,
            )

            req = _make_create_request(
                packages=[
                    {
                        "product_id": "guaranteed_display",
                        "buyer_ref": "approval-pkg-1",
                        "budget": 5000.0,
                        "pricing_option_id": "cpm_usd_fixed",
                        "creative_ids": creative_ids,
                    }
                ],
            )

            result = await _create_media_buy_impl(req=req, identity=identity)

        assert result.status in ("submitted", "completed"), f"Unexpected status: {result.status}"
        assert result.response is not None

        media_buy_id = getattr(result.response, "media_buy_id", None)
        assert media_buy_id is not None, "Response should contain media_buy_id"

        assignments = _query_assignments(sample_tenant["tenant_id"], media_buy_id)
        assert len(assignments) > 0, "Expected at least one creative assignment"

        for assignment in assignments:
            assert assignment.principal_id is not None, f"Assignment {assignment.assignment_id} has NULL principal_id"
            assert assignment.principal_id == sample_principal["principal_id"], (
                f"Assignment {assignment.assignment_id} has wrong principal_id: "
                f"{assignment.principal_id} != {sample_principal['principal_id']}"
            )


# ---------------------------------------------------------------------------
# Site 2: auto-approve path (media_buy_create.py ~line 3208)
# ---------------------------------------------------------------------------


class TestCreativeAssignmentPrincipalIdAutoApprove:
    """Regression: DBAssignment in auto-approve create path must include principal_id."""

    @pytest.mark.asyncio
    async def test_assignment_has_principal_id_on_auto_approve_create(
        self,
        integration_db,
        sample_tenant,
        sample_principal,
        sample_products,
    ):
        """Site 2: creative_assignments created during auto-approve create_media_buy
        must have principal_id populated (NOT NULL constraint).

        Covers: UC-006-ASSIGNMENT-PRINCIPAL-ID-02
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        with _AssignmentTestEnv() as env:
            tenant_dict = _get_tenant_dict(sample_tenant["tenant_id"])
            creative_ids = _create_test_creatives(env, sample_tenant["tenant_id"], sample_principal["principal_id"])
            env._commit_factory_data()

            identity = PrincipalFactory.make_identity(
                principal_id=sample_principal["principal_id"],
                tenant_id=sample_tenant["tenant_id"],
                tenant=tenant_dict,
            )

            req = _make_create_request(
                packages=[
                    {
                        "product_id": "guaranteed_display",
                        "buyer_ref": "auto-pkg-1",
                        "budget": 5000.0,
                        "pricing_option_id": "cpm_usd_fixed",
                        "creative_ids": creative_ids,
                    }
                ],
            )

            result = await _create_media_buy_impl(req=req, identity=identity)

        assert result.status in ("completed", "submitted"), f"Unexpected status: {result.status}"
        assert result.response is not None

        media_buy_id = getattr(result.response, "media_buy_id", None)
        assert media_buy_id is not None, "Response should contain media_buy_id"

        assignments = _query_assignments(sample_tenant["tenant_id"], media_buy_id)
        assert len(assignments) > 0, "Expected at least one creative assignment"

        for assignment in assignments:
            assert assignment.principal_id is not None, f"Assignment {assignment.assignment_id} has NULL principal_id"
            assert assignment.principal_id == sample_principal["principal_id"], (
                f"Assignment {assignment.assignment_id} has wrong principal_id: "
                f"{assignment.principal_id} != {sample_principal['principal_id']}"
            )


# ---------------------------------------------------------------------------
# Site 3: update path (media_buy_update.py ~line 769)
# ---------------------------------------------------------------------------


class TestCreativeAssignmentPrincipalIdUpdate:
    """Regression: DBAssignment in update_media_buy creative_ids path must include principal_id."""

    @pytest.mark.asyncio
    async def test_assignment_has_principal_id_on_update_creative_ids(
        self,
        integration_db,
        sample_tenant,
        sample_principal,
        sample_products,
    ):
        """Site 3: creative_assignments created during update_media_buy with creative_ids
        must have principal_id populated (NOT NULL constraint).

        Covers: UC-006-ASSIGNMENT-PRINCIPAL-ID-03
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from src.core.tools.media_buy_update import _update_media_buy_impl

        with _AssignmentTestEnv() as env:
            tenant_dict = _get_tenant_dict(sample_tenant["tenant_id"])
            creative_ids = _create_test_creatives(env, sample_tenant["tenant_id"], sample_principal["principal_id"])
            env._commit_factory_data()

            identity = PrincipalFactory.make_identity(
                principal_id=sample_principal["principal_id"],
                tenant_id=sample_tenant["tenant_id"],
                tenant=tenant_dict,
            )

            # Step 1: Create a media buy WITHOUT creatives
            create_req = _make_create_request(
                packages=[
                    {
                        "product_id": "guaranteed_display",
                        "buyer_ref": "update-pkg-1",
                        "budget": 5000.0,
                        "pricing_option_id": "cpm_usd_fixed",
                    }
                ],
            )

            create_result = await _create_media_buy_impl(req=create_req, identity=identity)
            assert create_result.status in ("completed", "submitted"), (
                f"Create failed with status: {create_result.status}"
            )
            media_buy_id = getattr(create_result.response, "media_buy_id", None)
            assert media_buy_id is not None

            # Get the package_id that was created
            with get_db_session() as session:
                pkg_stmt = select(DBMediaPackage).where(
                    DBMediaPackage.media_buy_id == media_buy_id,
                )
                packages = session.scalars(pkg_stmt).all()
                assert len(packages) > 0, "Expected at least one package"
                package_id = packages[0].package_id

            # Step 2: Update the media buy to add creative_ids
            update_req = UpdateMediaBuyRequest(
                media_buy_id=media_buy_id,
                packages=[
                    {
                        "package_id": package_id,
                        "creative_ids": creative_ids,
                    }
                ],
            )

            update_result = _update_media_buy_impl(req=update_req, identity=identity)

        from src.core.schemas import UpdateMediaBuyError

        assert not isinstance(update_result, UpdateMediaBuyError), f"Update failed: {update_result}"

        # Verify creative_assignment rows have principal_id populated
        assignments = _query_assignments(sample_tenant["tenant_id"], media_buy_id)
        assert len(assignments) > 0, "Expected at least one creative assignment after update"

        for assignment in assignments:
            assert assignment.principal_id is not None, f"Assignment {assignment.assignment_id} has NULL principal_id"
            assert assignment.principal_id == sample_principal["principal_id"], (
                f"Assignment {assignment.assignment_id} has wrong principal_id: "
                f"{assignment.principal_id} != {sample_principal['principal_id']}"
            )
