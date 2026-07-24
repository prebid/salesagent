"""Shared helpers for media-buy integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.schemas import CreateMediaBuyRequest
from tests.harness._idempotency import fresh_idempotency_key


def _future(days: int = 1) -> datetime:
    """Return a timezone-aware datetime N days in the future."""
    return datetime.now(UTC) + timedelta(days=days)


def _make_create_request(**overrides: Any) -> CreateMediaBuyRequest:
    """Build a minimal valid CreateMediaBuyRequest.

    idempotency_key is required by AdCP 3.1.1 but is operationally inert while
    the seller advertises support false. A unique default keeps test requests
    independently traceable; callers may override it deliberately.
    """
    defaults: dict[str, Any] = {
        "brand": {"domain": "testbrand.com"},
        "start_time": _future(1),
        "end_time": _future(8),
        "idempotency_key": fresh_idempotency_key("int-key"),
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


def resolve_media_buy_id_from_task(task_id: str) -> str:
    """Resolve the persisted media_buy_id from a submitted response's task_id.

    Spec 3.1.1: a pending-approval create returns the CreateMediaBuySubmitted
    envelope (task_id only, no media_buy_id) — the buy is located via the
    ObjectWorkflowMapping the create path links to the workflow step
    (PR #1567 round-2 item 2). Fails loud when no mapping exists.
    """
    from src.core.database.models import ObjectWorkflowMapping

    with get_db_session() as session:
        mapping = session.scalars(
            select(ObjectWorkflowMapping).where(
                ObjectWorkflowMapping.step_id == task_id,
                ObjectWorkflowMapping.object_type == "media_buy",
            )
        ).first()
    assert mapping is not None, f"submitted create must map workflow step {task_id!r} to a media buy"
    return mapping.object_id
