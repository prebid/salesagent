"""Regression tests: principal_id NOT NULL on creative_assignments.

Three production code sites construct DBAssignment() and must include
principal_id to satisfy the NOT NULL constraint:

1. media_buy_create.py ~line 2252  (manual approval path)
2. media_buy_create.py ~line 3208  (auto-approve path)
3. media_buy_update.py ~line 769   (update with creative_ids)

Each test verifies that creative_assignment rows have principal_id populated
after the respective code path executes. Mutation verification: temporarily
removing principal_id= from the production site should cause IntegrityError.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import Creative as DBCreative
from src.core.database.models import CreativeAssignment as DBAssignment
from src.core.database.models import Tenant as TenantModel
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import (
    UpdateMediaBuyRequest,
)
from src.core.testing_hooks import AdCPTestContext
from tests.helpers.adcp_factories import create_test_format
from tests.integration.media_buy_helpers import _get_tenant_dict, _make_create_request

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future(days: int = 1) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def _make_identity(
    principal_id: str,
    tenant_id: str,
    tenant: dict[str, Any],
    dry_run: bool = False,
) -> ResolvedIdentity:
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_id,
        tenant=tenant,
        protocol="mcp",
        testing_context=AdCPTestContext(
            dry_run=dry_run,
            mock_time=None,
            jump_to_event=None,
            test_session_id=None,
        ),
    )


def _query_assignments(tenant_id: str, media_buy_id: str) -> list[DBAssignment]:
    """Query all creative_assignment rows for a given media buy."""
    with get_db_session() as session:
        stmt = select(DBAssignment).where(
            DBAssignment.tenant_id == tenant_id,
            DBAssignment.media_buy_id == media_buy_id,
        )
        return list(session.scalars(stmt).all())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DEFAULT_FORMAT_ID = "display_300x250"


@pytest.fixture(autouse=True)
def mock_format_spec():
    """Mock _get_format_spec_sync to avoid asyncio.run() inside running event loop.

    The auto-approve path calls _get_format_spec_sync which wraps
    CreativeAgentRegistry.get_format() in asyncio.run(). This fails inside
    pytest-asyncio. We mock the sync wrapper directly to return a valid format.
    """
    mock_formats = {
        DEFAULT_FORMAT_ID: create_test_format(
            format_id=DEFAULT_FORMAT_ID,
            name="Display 300x250",
            type="display",
        ),
    }

    def format_spec_side_effect(agent_url, format_id):
        return mock_formats.get(format_id)

    with patch(
        "src.core.tools.media_buy_create._get_format_spec_sync",
        side_effect=format_spec_side_effect,
    ):
        yield


@pytest.fixture
def ca_tenant(sample_tenant):
    """Tenant dict with human_review_required=False (auto-approve)."""
    return _get_tenant_dict(sample_tenant["tenant_id"])


@pytest.fixture
def ca_tenant_with_approval(integration_db, sample_tenant):
    """Tenant with human_review_required=True (manual approval path)."""
    with get_db_session() as session:
        stmt = select(TenantModel).where(TenantModel.tenant_id == sample_tenant["tenant_id"])
        tenant = session.scalars(stmt).first()
        assert tenant is not None
        tenant.human_review_required = True
        session.commit()
    return _get_tenant_dict(sample_tenant["tenant_id"])


@pytest.fixture
def ca_principal(sample_principal):
    return sample_principal


@pytest.fixture
def ca_products(sample_products):
    return sample_products


@pytest.fixture
def ca_creatives(integration_db, ca_tenant, ca_principal):
    """Create test creatives required for creative_assignments FK."""
    creative_ids = ["c_regress_1", "c_regress_2"]
    with get_db_session() as session:
        for cid in creative_ids:
            existing = session.scalars(
                select(DBCreative).where(
                    DBCreative.creative_id == cid,
                    DBCreative.tenant_id == ca_tenant["tenant_id"],
                    DBCreative.principal_id == ca_principal["principal_id"],
                )
            ).first()
            if not existing:
                session.add(
                    DBCreative(
                        creative_id=cid,
                        tenant_id=ca_tenant["tenant_id"],
                        principal_id=ca_principal["principal_id"],
                        name=f"Regression Creative {cid}",
                        agent_url="https://creative.adcontextprotocol.org",
                        format="display_300x250",
                        data={
                            "url": "https://example.com/creative.jpg",
                            "width": 300,
                            "height": 250,
                            "primary": {"url": "https://example.com/creative.jpg"},
                            "platform_creative_id": f"mock_creative_{cid}",
                        },
                    )
                )
        session.commit()
    return creative_ids


@pytest.fixture
def ca_identity(ca_tenant, ca_principal):
    return _make_identity(
        principal_id=ca_principal["principal_id"],
        tenant_id=ca_tenant["tenant_id"],
        tenant=ca_tenant,
    )


@pytest.fixture
def ca_identity_with_approval(ca_tenant_with_approval, ca_principal):
    return _make_identity(
        principal_id=ca_principal["principal_id"],
        tenant_id=ca_tenant_with_approval["tenant_id"],
        tenant=ca_tenant_with_approval,
    )


# ---------------------------------------------------------------------------
# Site 1: manual approval path (media_buy_create.py ~line 2252)
# ---------------------------------------------------------------------------


class TestCreativeAssignmentPrincipalIdManualApproval:
    """Regression: DBAssignment in manual approval create path must include principal_id."""

    @pytest.mark.asyncio
    async def test_assignment_has_principal_id_on_manual_approval_create(
        self,
        ca_tenant_with_approval,
        ca_principal,
        ca_products,
        ca_creatives,
        ca_identity_with_approval,
    ):
        """Site 1: creative_assignments created during manual-approval create_media_buy
        must have principal_id populated (NOT NULL constraint).
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = _make_create_request(
            packages=[
                {
                    "product_id": "guaranteed_display",
                    "buyer_ref": "approval-pkg-1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "creative_ids": ca_creatives,
                }
            ],
        )

        result = await _create_media_buy_impl(req=req, identity=ca_identity_with_approval)

        # The result should succeed (submitted for approval)
        assert result.status in ("submitted", "completed"), f"Unexpected status: {result.status}"
        assert result.response is not None

        # Extract media_buy_id from the response
        media_buy_id = getattr(result.response, "media_buy_id", None)
        assert media_buy_id is not None, "Response should contain media_buy_id"

        # Verify creative_assignment rows have principal_id populated
        assignments = _query_assignments(ca_tenant_with_approval["tenant_id"], media_buy_id)
        assert len(assignments) > 0, "Expected at least one creative assignment"

        for assignment in assignments:
            assert assignment.principal_id is not None, f"Assignment {assignment.assignment_id} has NULL principal_id"
            assert assignment.principal_id == ca_principal["principal_id"], (
                f"Assignment {assignment.assignment_id} has wrong principal_id: "
                f"{assignment.principal_id} != {ca_principal['principal_id']}"
            )


# ---------------------------------------------------------------------------
# Site 2: auto-approve path (media_buy_create.py ~line 3208)
# ---------------------------------------------------------------------------


class TestCreativeAssignmentPrincipalIdAutoApprove:
    """Regression: DBAssignment in auto-approve create path must include principal_id."""

    @pytest.mark.asyncio
    async def test_assignment_has_principal_id_on_auto_approve_create(
        self,
        ca_tenant,
        ca_principal,
        ca_products,
        ca_creatives,
        ca_identity,
    ):
        """Site 2: creative_assignments created during auto-approve create_media_buy
        must have principal_id populated (NOT NULL constraint).
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl

        req = _make_create_request(
            packages=[
                {
                    "product_id": "guaranteed_display",
                    "buyer_ref": "auto-pkg-1",
                    "budget": 5000.0,
                    "pricing_option_id": "cpm_usd_fixed",
                    "creative_ids": ca_creatives,
                }
            ],
        )

        result = await _create_media_buy_impl(req=req, identity=ca_identity)

        # Auto-approve should succeed
        assert result.status in ("completed", "submitted"), f"Unexpected status: {result.status}"
        assert result.response is not None

        media_buy_id = getattr(result.response, "media_buy_id", None)
        assert media_buy_id is not None, "Response should contain media_buy_id"

        # Verify creative_assignment rows have principal_id populated
        assignments = _query_assignments(ca_tenant["tenant_id"], media_buy_id)
        assert len(assignments) > 0, "Expected at least one creative assignment"

        for assignment in assignments:
            assert assignment.principal_id is not None, f"Assignment {assignment.assignment_id} has NULL principal_id"
            assert assignment.principal_id == ca_principal["principal_id"], (
                f"Assignment {assignment.assignment_id} has wrong principal_id: "
                f"{assignment.principal_id} != {ca_principal['principal_id']}"
            )


# ---------------------------------------------------------------------------
# Site 3: update path (media_buy_update.py ~line 769)
# ---------------------------------------------------------------------------


class TestCreativeAssignmentPrincipalIdUpdate:
    """Regression: DBAssignment in update_media_buy creative_ids path must include principal_id."""

    @pytest.mark.asyncio
    async def test_assignment_has_principal_id_on_update_creative_ids(
        self,
        ca_tenant,
        ca_principal,
        ca_products,
        ca_creatives,
        ca_identity,
    ):
        """Site 3: creative_assignments created during update_media_buy with creative_ids
        must have principal_id populated (NOT NULL constraint).
        """
        from src.core.tools.media_buy_create import _create_media_buy_impl
        from src.core.tools.media_buy_update import _update_media_buy_impl

        # Step 1: Create a media buy WITHOUT creatives (so we can add them via update)
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

        create_result = await _create_media_buy_impl(req=create_req, identity=ca_identity)
        assert create_result.status in ("completed", "submitted"), f"Create failed with status: {create_result.status}"
        media_buy_id = getattr(create_result.response, "media_buy_id", None)
        assert media_buy_id is not None

        # We need to get the package_id that was created
        with get_db_session() as session:
            from src.core.database.models import MediaPackage as DBMediaPackage

            pkg_stmt = select(DBMediaPackage).where(
                DBMediaPackage.media_buy_id == media_buy_id,
            )
            packages = session.scalars(pkg_stmt).all()
            assert len(packages) > 0, "Expected at least one package"
            package_id = packages[0].package_id

        # Step 2: Update the media buy to add creative_ids
        # Note: AdCP oneOf constraint — provide media_buy_id OR buyer_ref, not both
        update_req = UpdateMediaBuyRequest(
            media_buy_id=media_buy_id,
            packages=[
                {
                    "package_id": package_id,
                    "creative_ids": ca_creatives,
                }
            ],
        )

        update_result = _update_media_buy_impl(req=update_req, identity=ca_identity)

        # Update should succeed (not return error)
        from src.core.schemas import UpdateMediaBuyError

        assert not isinstance(update_result, UpdateMediaBuyError), f"Update failed: {update_result}"

        # Verify creative_assignment rows have principal_id populated
        assignments = _query_assignments(ca_tenant["tenant_id"], media_buy_id)
        assert len(assignments) > 0, "Expected at least one creative assignment after update"

        for assignment in assignments:
            assert assignment.principal_id is not None, f"Assignment {assignment.assignment_id} has NULL principal_id"
            assert assignment.principal_id == ca_principal["principal_id"], (
                f"Assignment {assignment.assignment_id} has wrong principal_id: "
                f"{assignment.principal_id} != {ca_principal['principal_id']}"
            )
