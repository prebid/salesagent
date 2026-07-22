"""Shared seed helper for the idempotency verbatim success cache.

Tests seed the cache through the same repository production uses (a real
``MediaBuyUoW`` → ``IdempotencyAttemptRepository.record_success``) so the
probe's ``find_by_key`` serves exactly what production would have stored.
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
    """Write a verbatim-cache row for ``create_media_buy`` via the production repository.

    ``payload_hash`` must match the canonical hash of the request the test will
    retry for a replay; pass a non-matching hash to exercise the
    ``IDEMPOTENCY_CONFLICT`` path. Only successes are ever seeded — errors are
    never cached by production, and tests must mirror that. ``ttl``/``now``
    pass through to ``record_success`` so expiry tests can seed already-expired
    rows.
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
    """Commit a tenant + principal so the idempotency ``_impl`` auth/FK checks pass.

    One home for the ``BareIntegrationEnv`` + factory seed shared by the
    rate-limit and replay integration tests.
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
    """Commit a tenant + principal + MediaBuy (the dup-booking backstop) via factories.

    The committed MediaBuy carries the ``idempotency_key`` backstop without a
    verbatim cache row — the state the degraded post-race path and the
    account-scoped key lookup are tested against. One home so the seed block
    does not duplicate across the repository and race test modules.
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


def create_media_buy_kwargs(
    product,
    *,
    idempotency_key: str,
    brand_domain: str = "idempotency-test.example.com",
    po_number: str = "IDEM-1",
) -> dict:
    """One fixed create_media_buy payload for idempotency tests.

    Callers copy the result per call so the canonical hash stays stable across
    a retry. Four structurally identical builders had been copied across the
    wire-matrix, rate-limit, and race modules (one of them twice, once inline),
    varying only in ``brand_domain`` and ``po_number`` — sub-threshold for the
    clone detector, so nothing would have caught the day one copy drifted a
    field and quietly stopped exercising what its siblings did.
    """
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    return {
        "brand": {"domain": brand_domain},
        "packages": [{"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
        "start_time": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time": (now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "po_number": po_number,
        "idempotency_key": idempotency_key,
    }
