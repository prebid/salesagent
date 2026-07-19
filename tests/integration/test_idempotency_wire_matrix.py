"""Create-media-buy wire contract when idempotency is advertised unsupported.

AdCP 3.1.1 still requires a well-shaped ``idempotency_key`` on the create
request. Because this seller advertises ``idempotency.supported=false``, a
valid key is otherwise a no-op: it cannot cause lookup, hashing, admission,
cache writes, replay, conflicts, expiry errors, or duplicate suppression.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.harness.media_buy_create import OMIT_IDEMPOTENCY_KEY, MediaBuyCreateEnv
from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

WIRE_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]
VALIDATION_TRANSPORTS = [Transport.A2A, Transport.MCP, Transport.REST]


def _create_kwargs(product, *, idempotency_key, po_number="WIRE-1"):
    now = datetime.now(UTC)
    return {
        "brand": {"domain": "wire-matrix.example.com"},
        "packages": [
            {
                "product_id": product.product_id,
                "budget": 5000.0,
                "pricing_option_id": "cpm_usd_fixed",
            }
        ],
        "start_time": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time": (now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "po_number": po_number,
        "idempotency_key": idempotency_key,
    }


def _unexpected_idempotency_repository_call(*_args, **_kwargs):
    raise AssertionError("create_media_buy must not consult idempotency storage when supported=false")


@pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda value: value.value)
def test_supported_false_key_is_inert_on_every_transport(integration_db, monkeypatch, transport):
    """A historical matching cache row and repeated key cannot alter execution."""
    from src.core.database.repositories import MediaBuyUoW
    from src.core.database.repositories.idempotency_attempt import IdempotencyAttemptRepository

    key = f"wire-noop-{uuid.uuid4().hex}"

    with MediaBuyCreateEnv() as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()
        env.seed_success(key, payload_hash="historical-hash", media_buy_id="mb_historical_cache_only")

        with MediaBuyUoW(env._tenant_id) as uow:
            assert uow.idempotency_attempts is not None
            cached_before = uow.idempotency_attempts.find_including_expired(
                principal_id=env._principal_id,
                idempotency_key=key,
            )
            assert cached_before is not None
            cached_attempt_id = cached_before.attempt_id
            cached_envelope = dict(cached_before.response_envelope)

        kwargs = _create_kwargs(product, idempotency_key=key)
        adapter_create = env.mock["adapter"].return_value.create_media_buy
        calls_before = adapter_create.call_count

        # Any old replay/admission/cache hook is a hard failure. Construction of
        # the dormant repository is harmless; invoking any of its methods is not.
        with monkeypatch.context() as patcher:
            for method_name in (
                "find_by_key",
                "find_including_expired",
                "record_success",
                "count_inserts_since",
                "count_active",
                "expire_old",
            ):
                patcher.setattr(
                    IdempotencyAttemptRepository,
                    method_name,
                    _unexpected_idempotency_repository_call,
                )

            first = env.call_via(transport, **dict(kwargs))
            second = env.call_via(transport, **dict(kwargs))
            changed = env.call_via(transport, **{**kwargs, "po_number": "WIRE-CHANGED"})

        results = (first, second, changed)
        assert all(result.is_success for result in results), [result.error for result in results]
        assert adapter_create.call_count == calls_before + 3

        media_buy_ids = [result.payload.response.media_buy_id for result in results]
        assert None not in media_buy_ids
        assert len(set(media_buy_ids)) == 3
        assert all(result.payload.replayed is False for result in results)
        for result in results:
            if result.wire_response is not None:
                assert result.wire_response.get("replayed") in (None, False)

        with MediaBuyUoW(env._tenant_id) as uow:
            assert uow.media_buys is not None
            assert uow.idempotency_attempts is not None
            for media_buy_id in media_buy_ids:
                created = uow.media_buys.get_by_id(media_buy_id)
                assert created is not None
                assert created.idempotency_key is None
                assert created.payload_hash is None
                assert created.raw_request["idempotency_key"] == key

            cached_after = uow.idempotency_attempts.find_including_expired(
                principal_id=env._principal_id,
                idempotency_key=key,
            )
            assert cached_after is not None
            assert cached_after.attempt_id == cached_attempt_id
            assert cached_after.response_envelope == cached_envelope


@pytest.mark.parametrize("transport", VALIDATION_TRANSPORTS, ids=lambda value: value.value)
def test_missing_key_still_rejects_at_wire_boundary(integration_db, transport):
    with MediaBuyCreateEnv() as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()
        result = env.call_via(
            transport,
            **_create_kwargs(product, idempotency_key=OMIT_IDEMPOTENCY_KEY),
        )

    assert result.is_error
    assert_envelope_shape(
        result.wire_error_envelope,
        "VALIDATION_ERROR",
        recovery="correctable",
        message_substr="idempotency_key",
    )
    assert result.wire_error_envelope["errors"][0].get("field") == "idempotency_key"


@pytest.mark.parametrize("transport", VALIDATION_TRANSPORTS, ids=lambda value: value.value)
@pytest.mark.parametrize(
    "invalid_key",
    ["too-short", "spaces are invalid!", "a" * 256, 123],
    ids=["short", "pattern", "long", "wrong-type"],
)
def test_malformed_key_still_rejects_at_wire_boundary(integration_db, transport, invalid_key):
    with MediaBuyCreateEnv() as env:
        _tenant, _principal, product, _pricing = env.setup_media_buy_data()
        result = env.call_via(
            transport,
            **_create_kwargs(product, idempotency_key=invalid_key),
        )

    assert result.is_error
    assert_envelope_shape(
        result.wire_error_envelope,
        "VALIDATION_ERROR",
        recovery="correctable",
        message_substr="idempotency_key",
    )
