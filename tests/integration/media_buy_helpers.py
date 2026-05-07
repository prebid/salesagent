"""Shared helpers for media-buy integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.resolved_identity import ResolvedIdentity
from src.core.schemas import CreateMediaBuyRequest
from src.core.testing_hooks import AdCPTestContext


def set_tenant_approval_mode(tenant_id: str, mode: str) -> None:
    """Test helper — flip tenant.approval_mode through TenantConfigRepository.

    Replaces the inline ``with get_db_session(): t.approval_mode = ...`` pattern
    so test bodies don't manage sessions or mutate ORM state directly. See #42.
    """
    from src.core.database.repositories.uow import TenantConfigUoW

    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None
        uow.tenant_config.set_approval_mode(mode)


def set_tenant_human_review_required(tenant_id: str, required: bool) -> None:
    """Test helper — flip tenant.human_review_required through TenantConfigRepository."""
    from src.core.database.repositories.uow import TenantConfigUoW

    with TenantConfigUoW(tenant_id) as uow:
        assert uow.tenant_config is not None
        uow.tenant_config.set_human_review_required(required)


def admin_mark_creative_approved(tenant_id: str, creative_id: str, *, approved_by: str = "test_admin") -> None:
    """Test helper — mark a creative approved through CreativeRepository.

    Mirrors what the admin Flask route does without going through Flask. See #42.
    """
    from src.core.database.repositories.uow import CreativeUoW

    with CreativeUoW(tenant_id) as uow:
        assert uow.creatives is not None
        result = uow.creatives.admin_mark_approved(creative_id, approved_by=approved_by)
        assert result is not None, f"creative {creative_id} not found in tenant {tenant_id}"


def force_media_buy_status(tenant_id: str, media_buy_id: str, status: str) -> None:
    """Test helper — force a media buy's status through MediaBuyRepository.

    Used by tests that need to put a buy in a specific state without going
    through the approval/lifecycle code path under test (e.g., the webhook
    test wants an active buy without exercising the full create-and-approve
    flow). See #42.
    """
    from src.core.database.repositories.uow import MediaBuyUoW

    with MediaBuyUoW(tenant_id) as uow:
        assert uow.media_buys is not None
        result = uow.media_buys.update_status(media_buy_id, status)
        assert result is not None, f"media_buy {media_buy_id} not found in tenant {tenant_id}"


def make_lifecycle_identity(
    tenant_dict: dict[str, Any],
    principal_id: str,
    *,
    test_session_id: str | None = None,
) -> ResolvedIdentity:
    """Build a ResolvedIdentity matching what the transport boundary produces.

    By default ``test_session_id`` is ``None`` — ``_create_media_buy_impl``
    runs the production ``validate_setup_complete()`` path against the
    ``sample_tenant`` fixture's seeded house_domain + public_agent_url
    (closes #43). Tests that intentionally exercise unseeded tenants can
    pass an explicit ``test_session_id`` to short-circuit the validator,
    but doing so for routine lifecycle coverage is the test-integrity
    anti-pattern flagged in #43.
    """
    return ResolvedIdentity(
        principal_id=principal_id,
        tenant_id=tenant_dict["tenant_id"],
        tenant=tenant_dict,
        protocol="mcp",
        testing_context=AdCPTestContext(
            dry_run=False,
            mock_time=None,
            jump_to_event=None,
            test_session_id=test_session_id,
        ),
    )


def _future(days: int = 1) -> datetime:
    """Return a timezone-aware datetime N days in the future."""
    return datetime.now(UTC) + timedelta(days=days)


def _make_create_request(**overrides: Any) -> CreateMediaBuyRequest:
    """Build a minimal valid CreateMediaBuyRequest."""
    defaults: dict[str, Any] = {
        "brand": {"domain": "testbrand.com"},
        "start_time": _future(1),
        "end_time": _future(8),
        "packages": [
            {
                "product_id": "guaranteed_display",
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
            }
        ],
    }
    defaults.update(overrides)
    return CreateMediaBuyRequest(**defaults)


def _get_tenant_dict(tenant_id: str) -> dict[str, Any]:
    """Load full tenant dict from DB (matches resolve_identity output)."""
    from src.core.database.models import Tenant as TenantModel

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
