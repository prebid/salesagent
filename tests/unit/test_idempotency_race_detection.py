"""Unit guard for the retired create idempotency-race seam.

The historical index remains in the schema, but supported=false means new
create rows must never populate its key/hash columns. This production-method
test prevents a future refactor from accidentally reactivating duplicate
suppression without changing the advertised capability.
"""

import inspect
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from src.core.database.repositories.media_buy import MediaBuyRepository
from src.core.schemas import CreateMediaBuyRequest


def test_create_repository_keeps_legacy_race_columns_null():
    key = "unit-noop-race-key-0001"
    now = datetime.now(UTC)
    req = CreateMediaBuyRequest(
        brand={"domain": "unit-noop-race.example.com"},
        packages=[],
        start_time=now + timedelta(days=1),
        end_time=now + timedelta(days=2),
        idempotency_key=key,
    )
    session = MagicMock()
    repo = MediaBuyRepository(session, "tenant_noop")

    created = repo.create_from_request(
        media_buy_id="mb_noop_race",
        req=req,
        principal_id="principal_noop",
        advertiser_name="No-op Buyer",
        budget=1000.0,
        currency="USD",
        start_time=now + timedelta(days=1),
        end_time=now + timedelta(days=2),
    )

    assert created.idempotency_key is None
    assert created.payload_hash is None
    assert created.raw_request["idempotency_key"] == key
    assert "payload_hash" not in inspect.signature(repo.create_from_request).parameters
    session.add.assert_called_once_with(created)
    session.flush.assert_called_once_with()
