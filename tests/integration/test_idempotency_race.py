"""Legacy-index regression for unsupported create idempotency.

Two uncommitted transactions may persist requests carrying the same valid key.
The repository stores NULL in the legacy routing columns, so the historical
partial unique index cannot turn an inert protocol field into deduplication or
an IntegrityError race.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.core.schemas import CreateMediaBuyRequest
from tests.helpers import seed_principal

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def test_same_key_can_persist_in_overlapping_transactions(integration_db):
    from src.core.database.repositories import MediaBuyUoW

    tenant_id = f"noop_race_{uuid.uuid4().hex[:8]}"
    principal_id = f"principal_{uuid.uuid4().hex[:8]}"
    key = f"noop-race-{uuid.uuid4().hex}"
    seed_principal(tenant_id, principal_id)

    now = datetime.now(UTC)
    req = CreateMediaBuyRequest(
        brand={"domain": "noop-race.example.com"},
        packages=[],
        start_time=now + timedelta(days=30),
        end_time=now + timedelta(days=60),
        idempotency_key=key,
    )
    first_id = f"mb_first_{uuid.uuid4().hex[:8]}"
    second_id = f"mb_second_{uuid.uuid4().hex[:8]}"

    with MediaBuyUoW(tenant_id) as first_uow:
        assert first_uow.media_buys is not None
        first_uow.media_buys.create_from_request(
            media_buy_id=first_id,
            req=req,
            principal_id=principal_id,
            advertiser_name="No-op Buyer",
            budget=1000.0,
            currency="USD",
            start_time=now + timedelta(days=30),
            end_time=now + timedelta(days=60),
        )

        with MediaBuyUoW(tenant_id) as second_uow:
            assert second_uow.media_buys is not None
            second_uow.media_buys.create_from_request(
                media_buy_id=second_id,
                req=req,
                principal_id=principal_id,
                advertiser_name="No-op Buyer",
                budget=1000.0,
                currency="USD",
                start_time=now + timedelta(days=30),
                end_time=now + timedelta(days=60),
            )

    with MediaBuyUoW(tenant_id) as verify_uow:
        assert verify_uow.media_buys is not None
        for media_buy_id in (first_id, second_id):
            persisted = verify_uow.media_buys.get_by_id(media_buy_id)
            assert persisted is not None
            assert persisted.idempotency_key is None
            assert persisted.payload_hash is None
            assert persisted.raw_request["idempotency_key"] == key
