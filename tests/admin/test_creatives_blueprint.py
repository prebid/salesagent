"""Integration tests for the creatives admin blueprint.

Tests creative review, approval, and rejection via Flask test client.
Requires PostgreSQL (integration_db fixture).
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select

from src.admin.app import create_app
from src.core.database.database_session import get_db_session
from src.core.database.models import Creative, Principal, Tenant
from tests.utils.database_helpers import create_tenant_with_timestamps

app = create_app()

pytestmark = [pytest.mark.admin, pytest.mark.requires_db]

_TENANT_ID = "creative_test_tenant"
_PRINCIPAL_ID = "creative_test_principal"

# Patch target: post-commit side effects (webhooks, Slack) — prevent network calls
_SIDE_EFFECTS_PATCH = "src.admin.blueprints.creatives._send_post_commit_side_effects"


@pytest.fixture
def client():
    """Flask test client with test configuration."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SESSION_COOKIE_PATH"] = "/"
    with app.test_client() as client:
        yield client


@pytest.fixture
def test_tenant(integration_db):
    """Create a test tenant and principal for creative tests."""
    with get_db_session() as session:
        try:
            session.execute(delete(Creative).where(Creative.tenant_id == _TENANT_ID))
            session.execute(delete(Principal).where(Principal.tenant_id == _TENANT_ID))
            session.execute(delete(Tenant).where(Tenant.tenant_id == _TENANT_ID))
            session.commit()
        except Exception:
            session.rollback()

        tenant = create_tenant_with_timestamps(
            tenant_id=_TENANT_ID,
            name="Creative Test Tenant",
            subdomain="creative-test",
            ad_server="mock",
            is_active=True,
        )
        session.add(tenant)

        principal = Principal(
            tenant_id=_TENANT_ID,
            principal_id=_PRINCIPAL_ID,
            name="Creative Test Principal",
            platform_mappings={"mock": {"advertiser_id": "test_advertiser"}},
            access_token=f"creative-test-token-{uuid.uuid4().hex}",
        )
        session.add(principal)
        session.commit()

    return _TENANT_ID


def _auth_session(client, tenant_id):
    """Set up authenticated session for test client."""
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["user"] = {"email": "test@example.com", "is_super_admin": True}
        sess["email"] = "test@example.com"
        sess["tenant_id"] = tenant_id
        sess["test_user"] = "test@example.com"
        sess["test_user_role"] = "super_admin"
        sess["test_user_name"] = "Test User"
        sess["test_tenant_id"] = tenant_id


def _create_creative(tenant_id: str, status: str = "pending") -> str:
    """Create a test creative in the database. Returns creative_id."""
    creative_id = f"cre_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    with get_db_session() as session:
        session.add(
            Creative(
                creative_id=creative_id,
                tenant_id=tenant_id,
                principal_id=_PRINCIPAL_ID,
                name="Test Creative",
                agent_url="https://creatives.example.com",
                format="display_300x250_image",
                status=status,
                data={},
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    return creative_id


class TestCreativesReviewPage:
    """Test the unified creative review page."""

    def test_review_page_returns_200(self, client, test_tenant):
        """GET /tenant/<tid>/creatives/review returns 200."""
        _auth_session(client, test_tenant)
        response = client.get(f"/tenant/{test_tenant}/creatives/review")
        assert response.status_code == 200

    def test_review_page_shows_pending_creatives(self, client, test_tenant):
        """Review page includes names of pending creatives."""
        _auth_session(client, test_tenant)
        _create_creative(test_tenant, status="pending")

        response = client.get(f"/tenant/{test_tenant}/creatives/review")
        html = response.data.decode()
        assert "Test Creative" in html


class TestCreativeApproval:
    """Test creative approval endpoint."""

    def test_approve_creative_sets_status_approved(self, client, test_tenant):
        """POST approve sets the creative status to 'approved'."""
        _auth_session(client, test_tenant)
        creative_id = _create_creative(test_tenant, status="pending")

        with patch(_SIDE_EFFECTS_PATCH):
            response = client.post(
                f"/tenant/{test_tenant}/creatives/review/{creative_id}/approve",
                content_type="application/json",
                json={"approved_by": "test@example.com"},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert "error" not in data

        with get_db_session() as session:
            creative = session.scalars(
                select(Creative).where(
                    Creative.creative_id == creative_id,
                    Creative.tenant_id == test_tenant,
                )
            ).first()
        assert creative is not None
        assert creative.status == "approved"
        assert creative.approved_by == "test@example.com"

    def test_approve_creates_review_record(self, client, test_tenant):
        """POST approve creates a CreativeReview record."""
        from src.core.database.models import CreativeReview

        _auth_session(client, test_tenant)
        creative_id = _create_creative(test_tenant, status="pending")

        with patch(_SIDE_EFFECTS_PATCH):
            client.post(
                f"/tenant/{test_tenant}/creatives/review/{creative_id}/approve",
                content_type="application/json",
                json={},
            )

        with get_db_session() as session:
            review = session.scalars(
                select(CreativeReview).where(
                    CreativeReview.creative_id == creative_id,
                    CreativeReview.tenant_id == test_tenant,
                )
            ).first()
        assert review is not None
        assert review.final_decision == "approved"
        assert review.review_type == "human"

    def test_approve_nonexistent_creative_returns_404(self, client, test_tenant):
        """POST approve for a nonexistent creative returns 404."""
        _auth_session(client, test_tenant)
        with patch(_SIDE_EFFECTS_PATCH):
            response = client.post(
                f"/tenant/{test_tenant}/creatives/review/nonexistent_cre_id/approve",
                content_type="application/json",
                json={},
            )
        assert response.status_code == 404


def _lookup_tenant_principal(session, tenant_id: str):
    """Load tenant + principal created by the test_tenant fixture."""
    from sqlalchemy import select as sa_select

    from src.core.database.models import Principal as PrincipalModel
    from src.core.database.models import Tenant as TenantModel

    tenant = session.scalars(sa_select(TenantModel).filter_by(tenant_id=tenant_id)).first()
    principal = session.scalars(
        sa_select(PrincipalModel).filter_by(tenant_id=tenant_id, principal_id=_PRINCIPAL_ID)
    ).first()
    return tenant, principal


def _create_creative_for_retro_push(session, tenant_id: str, status: str = "pending_review") -> str:
    """Create a creative via factories (requires factory_session fixture)."""
    from tests.factories import CreativeFactory

    tenant, principal = _lookup_tenant_principal(session, tenant_id)
    creative = CreativeFactory(
        tenant=tenant,
        principal=principal,
        status=status,
        creative_id=f"cre_{uuid.uuid4().hex[:12]}",
        name="Test Creative",
        agent_url="https://creatives.example.com",
        format="display_300x250_image",
    )
    return creative.creative_id


def _create_active_media_buy(session, tenant_id: str, status: str = "active") -> tuple[str, str]:
    """Create a media buy + package with a platform_order_id (requires factory_session)."""
    from tests.factories import MediaBuyFactory, MediaPackageFactory

    tenant, principal = _lookup_tenant_principal(session, tenant_id)
    mb = MediaBuyFactory(tenant=tenant, principal=principal, status=status)
    pkg = MediaPackageFactory(
        media_buy=mb,
        package_config={"platform_order_id": "gam_order_test", "platform_line_item_id": "gam_li_test"},
    )
    return mb.media_buy_id, pkg.package_id


def _create_assignment(session, tenant_id: str, creative_id: str, media_buy_id: str, package_id: str) -> str:
    """Create a CreativeAssignment linking creative to media buy (requires factory_session)."""
    from sqlalchemy import select as sa_select

    from src.core.database.models import Creative as CreativeModel
    from src.core.database.models import MediaBuy as MediaBuyModel
    from tests.factories import CreativeAssignmentFactory

    creative = session.scalars(
        sa_select(CreativeModel).filter_by(tenant_id=tenant_id, creative_id=creative_id)
    ).first()
    media_buy = session.scalars(
        sa_select(MediaBuyModel).filter_by(tenant_id=tenant_id, media_buy_id=media_buy_id)
    ).first()
    asgn = CreativeAssignmentFactory(creative=creative, media_buy=media_buy, package_id=package_id)
    return asgn.assignment_id


_PUSH_PATCH = "src.core.tools.media_buy_create.push_creative_to_existing_buy"


class TestCreativeApprovalRetroactivePush:
    """approve_creative triggers retroactive push for already-active media buys (#1038)."""

    def test_active_buy_triggers_retroactive_push(self, client, test_tenant, factory_session):
        """Approving a creative assigned to an active buy calls push_creative_to_existing_buy."""
        _auth_session(client, test_tenant)
        creative_id = _create_creative_for_retro_push(factory_session, test_tenant, status="pending_review")
        media_buy_id, package_id = _create_active_media_buy(factory_session, test_tenant, status="active")
        _create_assignment(factory_session, test_tenant, creative_id, media_buy_id, package_id)

        with (
            patch(_SIDE_EFFECTS_PATCH),
            patch(_PUSH_PATCH, return_value=(True, None)) as mock_push,
        ):
            response = client.post(
                f"/tenant/{test_tenant}/creatives/review/{creative_id}/approve",
                content_type="application/json",
                json={"approved_by": "test@example.com"},
            )

        assert response.status_code == 200
        mock_push.assert_called_once_with(
            creative_id=creative_id,
            media_buy_id=media_buy_id,
            tenant_id=test_tenant,
        )

    def test_pending_creatives_buy_not_retroactively_pushed(self, client, test_tenant, factory_session):
        """Buys in pending_creatives status are handled by the existing loop, not the retroactive one."""
        _auth_session(client, test_tenant)
        creative_id = _create_creative_for_retro_push(factory_session, test_tenant, status="pending_review")
        media_buy_id, package_id = _create_active_media_buy(factory_session, test_tenant, status="pending_creatives")
        _create_assignment(factory_session, test_tenant, creative_id, media_buy_id, package_id)

        with (
            patch(_SIDE_EFFECTS_PATCH),
            patch(_PUSH_PATCH, return_value=(True, None)) as mock_push,
        ):
            response = client.post(
                f"/tenant/{test_tenant}/creatives/review/{creative_id}/approve",
                content_type="application/json",
                json={},
            )

        assert response.status_code == 200
        mock_push.assert_not_called()

    def test_push_failure_returns_200_with_warnings(self, client, test_tenant, factory_session):
        """Push failure is non-fatal: response is 200 with a warnings field."""
        _auth_session(client, test_tenant)
        creative_id = _create_creative_for_retro_push(factory_session, test_tenant, status="pending_review")
        media_buy_id, package_id = _create_active_media_buy(factory_session, test_tenant, status="active")
        _create_assignment(factory_session, test_tenant, creative_id, media_buy_id, package_id)

        with (
            patch(_SIDE_EFFECTS_PATCH),
            patch(_PUSH_PATCH, return_value=(False, "GAM rejected the creative")),
        ):
            response = client.post(
                f"/tenant/{test_tenant}/creatives/review/{creative_id}/approve",
                content_type="application/json",
                json={},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True
        assert any("see server logs" in w for w in data.get("warnings", []))
        assert not any("GAM rejected" in w for w in data.get("warnings", []))

    def test_no_active_buy_no_push_called(self, client, test_tenant, factory_session):
        """Approving a creative with no buy assignments does not call push."""
        _auth_session(client, test_tenant)
        creative_id = _create_creative_for_retro_push(factory_session, test_tenant, status="pending_review")

        with (
            patch(_SIDE_EFFECTS_PATCH),
            patch(_PUSH_PATCH, return_value=(True, None)) as mock_push,
        ):
            response = client.post(
                f"/tenant/{test_tenant}/creatives/review/{creative_id}/approve",
                content_type="application/json",
                json={},
            )

        assert response.status_code == 200
        mock_push.assert_not_called()

    def test_scheduled_buy_triggers_retroactive_push(self, client, test_tenant, factory_session):
        """Buys in 'scheduled' status (approved, not yet started) also get the push."""
        _auth_session(client, test_tenant)
        creative_id = _create_creative_for_retro_push(factory_session, test_tenant, status="pending_review")
        media_buy_id, package_id = _create_active_media_buy(factory_session, test_tenant, status="scheduled")
        _create_assignment(factory_session, test_tenant, creative_id, media_buy_id, package_id)

        with (
            patch(_SIDE_EFFECTS_PATCH),
            patch(_PUSH_PATCH, return_value=(True, None)) as mock_push,
        ):
            response = client.post(
                f"/tenant/{test_tenant}/creatives/review/{creative_id}/approve",
                content_type="application/json",
                json={},
            )

        assert response.status_code == 200
        mock_push.assert_called_once_with(
            creative_id=creative_id,
            media_buy_id=media_buy_id,
            tenant_id=test_tenant,
        )

    def test_paused_buy_triggers_retroactive_push(self, client, test_tenant, factory_session):
        """Buys in 'paused' status are live in the ad server and also get the push."""
        _auth_session(client, test_tenant)
        creative_id = _create_creative_for_retro_push(factory_session, test_tenant, status="pending_review")
        media_buy_id, package_id = _create_active_media_buy(factory_session, test_tenant, status="paused")
        _create_assignment(factory_session, test_tenant, creative_id, media_buy_id, package_id)

        with (
            patch(_SIDE_EFFECTS_PATCH),
            patch(_PUSH_PATCH, return_value=(True, None)) as mock_push,
        ):
            response = client.post(
                f"/tenant/{test_tenant}/creatives/review/{creative_id}/approve",
                content_type="application/json",
                json={},
            )

        assert response.status_code == 200
        mock_push.assert_called_once_with(
            creative_id=creative_id,
            media_buy_id=media_buy_id,
            tenant_id=test_tenant,
        )

    def test_pending_approval_buy_not_pushed(self, client, test_tenant, factory_session):
        """Buys in pending_approval (not yet sent to GAM) must not trigger push."""
        _auth_session(client, test_tenant)
        creative_id = _create_creative_for_retro_push(factory_session, test_tenant, status="pending_review")
        media_buy_id, package_id = _create_active_media_buy(factory_session, test_tenant, status="pending_approval")
        _create_assignment(factory_session, test_tenant, creative_id, media_buy_id, package_id)

        with (
            patch(_SIDE_EFFECTS_PATCH),
            patch(_PUSH_PATCH, return_value=(True, None)) as mock_push,
        ):
            response = client.post(
                f"/tenant/{test_tenant}/creatives/review/{creative_id}/approve",
                content_type="application/json",
                json={},
            )

        assert response.status_code == 200
        mock_push.assert_not_called()


class TestCreativeRejection:
    """Test creative rejection endpoint."""

    def test_reject_creative_sets_status_rejected(self, client, test_tenant):
        """POST reject with a reason sets the creative status to 'rejected'."""
        _auth_session(client, test_tenant)
        creative_id = _create_creative(test_tenant, status="pending")

        with patch(_SIDE_EFFECTS_PATCH):
            response = client.post(
                f"/tenant/{test_tenant}/creatives/review/{creative_id}/reject",
                content_type="application/json",
                json={"rejection_reason": "Does not comply with brand guidelines"},
            )

        assert response.status_code == 200
        data = response.get_json()
        assert data.get("success") is True

        with get_db_session() as session:
            creative = session.scalars(
                select(Creative).where(
                    Creative.creative_id == creative_id,
                    Creative.tenant_id == test_tenant,
                )
            ).first()
        assert creative is not None
        assert creative.status == "rejected"

    def test_reject_without_reason_returns_400(self, client, test_tenant):
        """POST reject without a rejection_reason returns 400."""
        _auth_session(client, test_tenant)
        creative_id = _create_creative(test_tenant, status="pending")

        with patch(_SIDE_EFFECTS_PATCH):
            response = client.post(
                f"/tenant/{test_tenant}/creatives/review/{creative_id}/reject",
                content_type="application/json",
                json={},
            )
        assert response.status_code == 400
        data = response.get_json()
        assert "rejection_reason" in data.get("error", "").lower() or "required" in data.get("error", "").lower()
