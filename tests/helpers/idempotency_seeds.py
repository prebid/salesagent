"""Shared seed helpers for the dormant idempotency storage substrate.

The current seller advertises ``idempotency.supported=false`` and production
tools do not call this substrate. Direct primitive tests keep it internally
sound; create no-op tests seed historical rows to prove they are ignored.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from src.core.schemas._base import CreateMediaBuySuccess


def make_active_cached_success(media_buy_id: str = "mb_seeded") -> CreateMediaBuySuccess:
    """Build the canonical ACTIVE-buy success model that cache-seeding tests store.

    One construction shared by the harness seeder and the integration tests so
    the seeded shape (active status + matching valid_actions, empty packages)
    cannot drift between files.
    """
    from adcp.server.helpers import valid_actions_for_status
    from adcp.types import MediaBuyStatus

    from src.core.schemas._base import CreateMediaBuySuccess

    return CreateMediaBuySuccess(
        media_buy_id=media_buy_id,
        packages=[],
        status=MediaBuyStatus.active,
        valid_actions=valid_actions_for_status(MediaBuyStatus.active.value),
    )


def seed_cached_success(
    tenant_id: str,
    principal_id: str,
    idempotency_key: str,
    *,
    response_model: BaseModel,
    payload_hash: str,
    protocol_status: str = "completed",
    account_id: str | None = None,
    ttl: timedelta | None = None,
    now: datetime | None = None,
) -> None:
    """Write a historical cache row via the dormant repository primitive.

    ``payload_hash`` is arbitrary for supported=false create tests because the
    production path must not read it. ``ttl``/``now`` pass through so direct
    repository tests can still exercise expiry mechanics.
    """
    from src.core.database.repositories import MediaBuyUoW
    from src.core.database.repositories.idempotency_attempt import DEFAULT_REPLAY_TTL

    with MediaBuyUoW(tenant_id) as uow:
        assert uow.idempotency_attempts is not None
        uow.idempotency_attempts.record_success(
            principal_id=principal_id,
            account_id=account_id,
            tool_name="create_media_buy",
            idempotency_key=idempotency_key,
            response_model=response_model,
            protocol_status=protocol_status,
            payload_hash=payload_hash,
            ttl=ttl if ttl is not None else DEFAULT_REPLAY_TTL,
            now=now,
        )


def seed_principal(tenant_id: str, principal_id: str) -> None:
    """Commit a tenant + principal for repository and create integration tests.

    One home for the ``BareIntegrationEnv`` + factory seed shared by the
    dormant-policy and supported=false create integration tests.
    """
    from tests.factories import PrincipalFactory, TenantFactory
    from tests.harness._base import BareIntegrationEnv

    with BareIntegrationEnv() as env:
        tenant = TenantFactory(tenant_id=tenant_id)
        PrincipalFactory(tenant=tenant, principal_id=principal_id)
        env._commit_factory_data()


def seed_media_buy(
    tenant_id: str,
    principal_id: str,
    media_buy_id: str,
    *,
    idempotency_key: str | None = None,
    account_id: str | None = None,
    status: str = "active",
) -> None:
    """Commit legacy media-buy key data via factories for repository tests.

    New production creates write NULL, but historical rows may still carry a
    key. This helper supports direct lookup/isolation coverage without implying
    that current create_media_buy uses that legacy column.
    """
    from tests.factories import AccountFactory, MediaBuyFactory, PrincipalFactory, TenantFactory
    from tests.harness._base import BareIntegrationEnv

    with BareIntegrationEnv() as env:
        tenant = TenantFactory(tenant_id=tenant_id)
        principal = PrincipalFactory(tenant=tenant, principal_id=principal_id)
        if account_id is not None:
            # media_buys.account_id is a FK into accounts — seed the account first.
            AccountFactory(tenant=tenant, account_id=account_id)
        MediaBuyFactory(
            tenant=tenant,
            principal=principal,
            media_buy_id=media_buy_id,
            idempotency_key=idempotency_key,
            account_id=account_id,
            status=status,
        )
        env.get_session()
