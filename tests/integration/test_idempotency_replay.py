"""Direct-entrypoint regressions for unsupported create idempotency.

The filename is retained for historical discoverability. These tests assert
the inverse of replay: while the request field remains required, this seller's
``idempotency.supported=false`` capability makes a valid key inert.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.harness.media_buy_create import MediaBuyCreateEnv

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


def _create_kwargs(product, idempotency_key):
    now = datetime.now(UTC)
    return {
        "brand": {"domain": "unsupported-idempotency.example.com"},
        "packages": [
            {
                "product_id": product.product_id,
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
            }
        ],
        "start_time": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time": (now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "po_number": "NO-REPLAY",
        "idempotency_key": idempotency_key,
    }


def test_identical_key_executes_twice_and_writes_no_cache(integration_db):
    from src.core.database.repositories import MediaBuyUoW

    key = f"no-replay-{uuid.uuid4().hex}"

    with MediaBuyCreateEnv() as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()
        kwargs = _create_kwargs(product, key)
        adapter_create = env.mock["adapter"].return_value.create_media_buy

        first = env.call_impl(**dict(kwargs))
        second = env.call_impl(**dict(kwargs))

        assert first.replayed is False
        assert second.replayed is False
        assert first.response.media_buy_id != second.response.media_buy_id
        assert adapter_create.call_count == 2

        with MediaBuyUoW(env._tenant_id) as uow:
            assert uow.media_buys is not None
            assert uow.idempotency_attempts is not None
            for media_buy_id in (first.response.media_buy_id, second.response.media_buy_id):
                created = uow.media_buys.get_by_id(media_buy_id)
                assert created is not None
                assert created.idempotency_key is None
                assert created.payload_hash is None
                assert created.raw_request["idempotency_key"] == key
            assert (
                uow.idempotency_attempts.find_including_expired(
                    principal_id=env._principal_id,
                    idempotency_key=key,
                )
                is None
            )


def test_failed_attempt_does_not_change_same_key_retry_semantics(integration_db):
    """A same-key retry after an adapter failure is just another execution."""
    from src.core.database.repositories import MediaBuyUoW
    from src.core.schemas import CreateMediaBuyError, Error

    key = f"no-replay-error-{uuid.uuid4().hex}"

    with MediaBuyCreateEnv() as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()
        kwargs = _create_kwargs(product, key)
        adapter_create = env.mock["adapter"].return_value.create_media_buy
        original_side_effect = adapter_create.side_effect
        adapter_create.side_effect = None
        adapter_create.return_value = CreateMediaBuyError(
            errors=[Error(code="ADAPTER_ERROR", message="adapter failure", recovery="terminal")]
        )

        first = env.call_impl(**dict(kwargs))
        adapter_create.side_effect = original_side_effect
        second = env.call_impl(**dict(kwargs))

        assert first.status == "failed"
        assert second.status == "completed"
        assert second.replayed is False
        assert adapter_create.call_count == 2

        with MediaBuyUoW(env._tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            assert (
                uow.idempotency_attempts.find_including_expired(
                    principal_id=env._principal_id,
                    idempotency_key=key,
                )
                is None
            )
