"""Shared helpers for media-buy integration tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.schemas import CreateMediaBuyRequest


def _future(days: int = 1) -> datetime:
    """Return a timezone-aware datetime N days in the future."""
    return datetime.now(UTC) + timedelta(days=days)


def _make_create_request(**overrides: Any) -> CreateMediaBuyRequest:
    """Build a minimal valid CreateMediaBuyRequest.

    idempotency_key is required by AdCP 3.1.1 (create-media-buy-request.json /required)
    and drives real replay/conflict
    behavior against the persistent integration DB, so a per-call-unique key is
    injected by default. Callers may override it (e.g. to deliberately reuse a
    key) via the ``idempotency_key`` kwarg.
    """
    defaults: dict[str, Any] = {
        "brand": {"domain": "testbrand.com"},
        "start_time": _future(1),
        "end_time": _future(8),
        "idempotency_key": f"int-key-{uuid.uuid4().hex}",
        "account": {"account_id": "acct_test"},
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


def _single_creative_request(creative_id: str, **overrides: Any) -> CreateMediaBuyRequest:
    """A ``_make_create_request`` for one product/pricing-option package carrying one creative.

    The one-package-one-creative shape a fetch-failure/refusal test needs to
    exercise a specific stored creative's format-fetch path, without each such
    test restating the package dict.
    """
    return _make_create_request(
        packages=[
            {
                "product_id": "prod_1",
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
                "creative_ids": [creative_id],
            }
        ],
        **overrides,
    )


def assert_created(response: Any) -> None:
    """Assert *response* is the SUCCESS branch of create-media-buy-response.json's oneOf.

    On fields that EXIST. ``not hasattr(response, "errors")`` stood at each of these call
    sites and could not tell the branches apart: ``CreateMediaBuyResult`` declares no
    ``errors`` field at all, so the check was True for every object it could be handed,
    including an error one.

    Skips rather than fails when the creative agent this environment reaches is down --
    external availability is not what a pricing test grades.
    """
    import pytest

    from tests.helpers.external_service import is_external_service_response_error

    if is_external_service_response_error(response):
        pytest.skip(f"External creative agent unavailable: {response.adcp_error}")

    assert response.adcp_error is None, f"create_media_buy failed: {response.adcp_error}"
    assert response.status == "completed", f"expected a completed create, got {response.status!r}"
    assert response.media_buy_id is not None


def make_media_buy_identity(principal_id: str, tenant_id: str, **tenant_overrides: Any) -> Any:
    """The caller ``_create_media_buy_impl`` / ``_update_media_buy_impl`` take.

    Principal, tenant and the ACCOUNT. ``create-media-buy-request.json`` and
    ``update-media-buy-request.json`` both list ``account`` in /required, so both
    implementations are annotated ``AccountIdentity`` and read
    ``identity.account.account_id`` directly. A plain ``ResolvedIdentity`` leaves that
    None, which surfaces as ``AttributeError: 'NoneType' object has no attribute
    'account_id'`` -- swallowed by the create path's catch-all and re-raised as
    ``AdCPAdapterError``, so the wrong identity TYPE reads as "the ad server is down".

    The account is ``DEFAULT_TEST_ACCOUNT_ID``, which is what
    ``create_test_media_buy_request`` and ``_make_create_request`` name in the payload;
    the fixture still has to seed the ROW and the access grant
    (``seed_default_account``), because the boundary resolves the reference for real.

    The tenant is LOADED FROM ITS ROW by default -- ``TenantContext.load``, which is what
    the resolver calls -- so the fixture's own columns govern, as they do in production. A
    hand-built ``tenant={"tenant_id": ...}`` instead takes every other field from
    TenantContext's defaults, and ``human_review_required`` defaults to True there: the
    create then answers ``submitted`` however the seeded row is configured. Pass
    ``tenant=`` explicitly only to state something the row does not.
    """
    from src.core.schemas.account import Account
    from src.core.tenant_context import TenantContext
    from tests.factories.account import DEFAULT_TEST_ACCOUNT_ID
    from tests.factories.principal import PrincipalFactory

    if "tenant" not in tenant_overrides:
        loaded = TenantContext.load(tenant_id)
        if loaded is None:
            raise ValueError(f"Tenant {tenant_id} not found: seed it before building an identity for it")
        tenant_overrides["tenant"] = loaded

    return PrincipalFactory.make_account_identity(
        PrincipalFactory.make_identity(
            principal_id=principal_id,
            tenant_id=tenant_id,
            **tenant_overrides,
        ),
        Account(account_id=DEFAULT_TEST_ACCOUNT_ID, name="Test Account", status="active"),
    )


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
