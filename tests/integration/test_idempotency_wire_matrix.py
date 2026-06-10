"""Wire matrix for create_media_buy idempotency: replay / conflict / missing-key per transport.

AdCP 3.0.1 graded steps pinned at the real wire (not reconstructed exceptions):

- ``create_media_buy_replay``: an IDENTICAL retry returns the original response
  with top-level ``replayed: true``; the adapter is NOT re-invoked and no second
  booking exists.
- ``key_reuse_conflict``: the same key with a different canonical payload rejects
  with ``IDEMPOTENCY_CONFLICT`` on every transport.
- ``missing_key``: a create without idempotency_key rejects as VALIDATION_ERROR
  (the REST pin lives in test_idempotency_replay; A2A/MCP are pinned here — the
  IMPL transport cannot express absence, the model requires the field).
- ``fresh_key_new_resource``: a different key with an identical payload creates a
  NEW media buy (no cross-key replay).

The request kwargs are built ONCE per test and copied per call: rebuilding them
would shift the start/end timestamps, changing the canonical payload hash and
turning an intended replay into a conflict.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.harness.media_buy_create import OMIT_IDEMPOTENCY_KEY, MediaBuyCreateEnv
from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

WIRE_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.MCP, Transport.REST]


def _create_kwargs(product, *, idempotency_key, po_number="WIRE-1"):
    """One fixed payload; callers copy it per call so the canonical hash is stable."""
    now = datetime.now(UTC)
    return {
        "brand": {"domain": "wire-matrix.example.com"},
        "packages": [{"product_id": product.product_id, "budget": 5000.0, "pricing_option_id": "cpm_usd_fixed"}],
        "start_time": (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time": (now + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "po_number": po_number,
        "idempotency_key": idempotency_key,
    }


@pytest.mark.parametrize("transport", WIRE_TRANSPORTS, ids=lambda t: t.value)
class TestIdempotencyWireMatrix:
    """Replay, conflict, and fresh-key behavior observed through each real transport."""

    def test_identical_retry_replays_verbatim(self, integration_db, transport):
        """An identical retry replays the original success with replayed=true,
        without re-invoking the adapter or creating a second booking."""
        key = f"wire-replay-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = _create_kwargs(product, idempotency_key=key)
            adapter_create = env.mock["adapter"].return_value.create_media_buy

            first = env.call_via(transport, **dict(kwargs))
            assert first.is_success, f"fresh create failed on {transport.value}: {first.error}"
            calls_after_first = adapter_create.call_count

            second = env.call_via(transport, **dict(kwargs))
            assert second.is_success, f"replay failed on {transport.value}: {second.error}"

            # The spec's top-level marker: present on the replay, absent on the fresh call.
            assert first.payload.replayed is False
            assert second.payload.replayed is True
            # Verbatim: same buy, same protocol status.
            assert second.payload.response.media_buy_id == first.payload.response.media_buy_id
            assert second.payload.status == first.payload.status
            # The handler was NOT re-invoked for the replay (storyboard's
            # no-duplicate-side-effects invariant).
            assert adapter_create.call_count == calls_after_first

            # Exactly one booking exists for this key (the unique index is the
            # backstop; the lookup returns the single winner).
            from src.core.database.repositories import MediaBuyUoW

            with MediaBuyUoW(env._tenant_id) as uow:
                assert uow.media_buys is not None
                existing = uow.media_buys.find_by_idempotency_key(key, env._principal_id)
                assert existing is not None
                assert existing.media_buy_id == first.payload.response.media_buy_id

            if transport is Transport.REST:
                # Byte-level wire check: the replay body is the original body
                # plus exactly the top-level replayed marker.
                first_body = first.raw_response.json()
                second_body = second.raw_response.json()
                assert second_body == {**first_body, "replayed": True}

    def test_same_key_different_payload_conflicts(self, integration_db, transport):
        """The same key with a different canonical payload is IDEMPOTENCY_CONFLICT."""
        key = f"wire-conflict-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = _create_kwargs(product, idempotency_key=key)

            first = env.call_via(transport, **dict(kwargs))
            assert first.is_success, f"fresh create failed on {transport.value}: {first.error}"

            mutated = dict(kwargs)
            mutated["po_number"] = "WIRE-2-DIFFERENT"
            second = env.call_via(transport, **mutated)

        assert second.is_error, f"conflicting payload must reject on {transport.value}"
        envelope = second.wire_error_envelope or second.synthesized_error_envelope
        assert envelope is not None, f"conflict must carry the two-layer envelope on {transport.value}"
        assert_envelope_shape(envelope, "IDEMPOTENCY_CONFLICT", recovery="terminal")

    def test_fresh_key_identical_payload_creates_new_buy(self, integration_db, transport):
        """A different key with an identical payload creates a NEW media buy."""
        key_one = f"wire-fresh-{uuid.uuid4().hex}"
        key_two = f"wire-fresh-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = _create_kwargs(product, idempotency_key=key_one)

            first = env.call_via(transport, **dict(kwargs))
            assert first.is_success, f"fresh create failed on {transport.value}: {first.error}"

            renewed = dict(kwargs)
            renewed["idempotency_key"] = key_two
            second = env.call_via(transport, **renewed)
            assert second.is_success, f"fresh-key create failed on {transport.value}: {second.error}"

        assert second.payload.replayed is False, "a fresh key must never replay"
        assert second.payload.response.media_buy_id != first.payload.response.media_buy_id


@pytest.mark.parametrize("transport", [Transport.A2A, Transport.MCP], ids=lambda t: t.value)
class TestMissingKeyWireMatrix:
    """Storyboard ``missing_key`` on the A2A and MCP wires (REST is pinned in
    test_idempotency_replay; IMPL cannot express absence — the model requires it)."""

    def test_missing_key_rejects_validation_error(self, integration_db, transport):
        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = _create_kwargs(product, idempotency_key=OMIT_IDEMPOTENCY_KEY)
            result = env.call_via(transport, **kwargs)

        assert result.is_error, f"missing idempotency_key must reject on {transport.value}"
        envelope = result.wire_error_envelope or result.synthesized_error_envelope
        assert envelope is not None
        assert_envelope_shape(
            envelope,
            "VALIDATION_ERROR",
            recovery="correctable",
            message_substr="idempotency_key",
        )
