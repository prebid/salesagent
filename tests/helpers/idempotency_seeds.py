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
