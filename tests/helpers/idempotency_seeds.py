"""Shared seed helper for the idempotency verbatim success cache.

Tests seed the cache through the same repository production uses (a real
``MediaBuyUoW`` → ``IdempotencyAttemptRepository.record_success``) so the
probe's ``find_by_key`` serves exactly what production would have stored.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from src.core.schemas._base import CreateMediaBuySuccess

#: The instant the canonical seeded buy was confirmed. A literal, so every module
#: seeding this body stores the same one.
SEEDED_CONFIRMED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def make_active_cached_success(media_buy_id: str = "mb_seeded") -> CreateMediaBuySuccess:
    """Build the canonical ACTIVE-buy success model that cache-seeding tests store.

    One construction shared by the harness seeder and the integration tests so
    the seeded shape (active status + matching valid_actions, empty packages)
    cannot drift between files.

    It carries a non-fatal ``errors`` advisory because a successful buy MAY carry one --
    ``property_list_unsupported_advisories`` puts them there -- and that is the shape
    that separates a correct revive from a plausible one. ``create-media-buy-response``
    discriminates its branches by required field, and the success branch is the only one
    requiring ``media_buy_id`` while the error branch requires ``errors``; a revive that
    keys on ``errors`` first resolves this body to the error branch. Every seeded replay
    in the suite therefore grades the order, instead of passing on a body so plain that
    either order works.

    This is the BUYER'S envelope, not an adapter carrier, and it has to be:
    ``record_success`` stores ``response_model.model_dump(mode="json")`` as the
    ``response`` half of the cached row, and a replay serves that body verbatim. So
    ``sync_success`` is the right constructor and ``confirmed_at`` / ``revision`` are
    passed explicitly — they carry no field default, which is what stops a seed from
    fabricating them silently. The instant is a fixed literal rather than ``now()``
    because this is the ONE canonical seeded body the whole suite shares, and a
    deterministic one cannot make two modules disagree; a seeded row's expiry is
    ``record_success``'s own ``ttl``/``now``, not this field.
    """
    from adcp.server.helpers import valid_actions_for_status
    from adcp.types import MediaBuyStatus

    from src.core.errors.codes import ErrorCode
    from src.core.schemas._base import CreateMediaBuySuccess, Error

    return CreateMediaBuySuccess.sync_success(
        media_buy_id=media_buy_id,
        packages=[],
        status=MediaBuyStatus.active,
        valid_actions=valid_actions_for_status(MediaBuyStatus.active.value),
        errors=[Error.of(ErrorCode.UNSUPPORTED_FEATURE, field="packages[0].targeting_overlay.property_list")],
        confirmed_at=SEEDED_CONFIRMED_AT,
        revision=1,
    )


class LegacyCachedShape(BaseModel):
    """A stored envelope shape that ``CreateMediaBuySuccess`` no longer validates.

    The schema-drift stand-in: a row written by an older deploy, inside the replay TTL,
    whose body the current model refuses. Both the replay and the race module seed it, and
    they each carried a byte-identical private copy.
    """

    legacy_field: str = "older-deploy"


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
    from src.core.idempotency_policy import DEFAULT_REPLAY_TTL

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


def seed_principal(tenant_id: str, principal_id: str, *, account_id: str | None = None) -> None:
    """Commit a tenant + principal + account so the boundary's auth/FK checks pass.

    One home for the ``BareIntegrationEnv`` + factory seed shared by the rate-limit and
    replay integration tests.

    The ACCOUNT and the principal's GRANT on it are seeded too, because dispatch now goes
    through ``src.core.tools._boundary.invoke_tool``, which resolves the reference the request
    names before probing the cache. Resolution is real authorization: the account must exist
    (else ACCOUNT_NOT_FOUND) and this principal must be granted access to it (else
    PERMISSION_DENIED), and the cache scope is (agent, account, key), so without both the
    probe never looks in the scope the seeded row sits in.
    """
    from tests.factories import AccountFactory, PrincipalFactory, TenantFactory
    from tests.factories.account import AgentAccountAccessFactory
    from tests.harness._base import DEFAULT_TEST_ACCOUNT_ID, BareIntegrationEnv

    with BareIntegrationEnv() as env:
        tenant = TenantFactory(tenant_id=tenant_id)
        principal = PrincipalFactory(tenant=tenant, principal_id=principal_id)
        account = AccountFactory(tenant_id=tenant.tenant_id, account_id=account_id or DEFAULT_TEST_ACCOUNT_ID)
        AgentAccountAccessFactory(tenant_id=tenant.tenant_id, principal=principal, account=account)
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
    from tests.harness._base import DEFAULT_TEST_ACCOUNT_ID, BareIntegrationEnv

    # A buy created by a conformant request carries an account, and the backstop lookup is
    # scoped by (principal, account, key) -- a seeded buy with no account sits in a scope
    # nothing looks in. Callers naming their own account keep it.
    account_id = account_id or DEFAULT_TEST_ACCOUNT_ID

    with BareIntegrationEnv() as env:
        tenant = TenantFactory(tenant_id=tenant_id)
        principal = PrincipalFactory(tenant=tenant, principal_id=principal_id)
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
