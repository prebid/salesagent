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
        if transport is Transport.IMPL:
            # IMPL has no wire by definition — its leg grades the synthesized
            # envelope (what production WOULD emit at the boundary). The three
            # wire legs below assert REAL wire bytes strictly: an `or` fallback
            # here would let a dead wire path pass on the synthesized shape.
            envelope = second.synthesized_error_envelope
        else:
            envelope = second.wire_error_envelope
        assert envelope is not None, f"conflict must carry the two-layer envelope on {transport.value}"
        assert_envelope_shape(envelope, "IDEMPOTENCY_CONFLICT", recovery="correctable")

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

    def test_expired_replay_window_rejects(self, integration_db, transport):
        """A retry after the replay window has expired rejects with
        IDEMPOTENCY_EXPIRED (correctable) on the real wire.

        Drives the degraded fail-closed path: create for real (cache row +
        MediaBuy backstop), age the cached row past its TTL while the backstop
        stays fresh, then retry. The probe misses the expired row, the create
        hits the backstop, and the degraded path anchors expiry on the stored
        expires_at. Pins the EXPIRED wire shape — the only idempotency error code
        previously asserted solely through the reconstructed exception, and the
        recovery class corrected from terminal to correctable.
        """
        from src.core.database.repositories import MediaBuyUoW

        key = f"wire-expired-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = _create_kwargs(product, idempotency_key=key)

            first = env.call_via(transport, **dict(kwargs))
            assert first.is_success, f"fresh create failed on {transport.value}: {first.error}"

            # Age the cached row past its replay TTL; the backstop MediaBuy stays
            # fresh, so a correct degraded path must read the row's stored expires_at.
            with MediaBuyUoW(env._tenant_id) as uow:
                assert uow.idempotency_attempts is not None
                row = uow.idempotency_attempts.find_including_expired(
                    principal_id=env._principal_id, idempotency_key=key
                )
                assert row is not None, "the fresh create must have written a cache row"
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)

            second = env.call_via(transport, **dict(kwargs))

        assert second.is_error, f"an expired replay window must reject on {transport.value}"
        if transport is Transport.IMPL:
            # IMPL has no wire — grade the synthesized envelope (what production
            # WOULD emit), exactly as the conflict leg does.
            envelope = second.synthesized_error_envelope
        else:
            envelope = second.wire_error_envelope
        assert envelope is not None, f"EXPIRED must carry the two-layer envelope on {transport.value}"
        assert_envelope_shape(envelope, "IDEMPOTENCY_EXPIRED", recovery="correctable")
        # The spec's buyer-recovery guidance (the natural-key check that MAKES
        # EXPIRED correctable) must ride the WIRE on both envelope layers — not
        # just live at the raise site — on every transport.
        for layer_name, layer in (("adcp_error", envelope["adcp_error"]), ("errors[0]", envelope["errors"][0])):
            assert layer.get("suggestion"), (
                f"EXPIRED must carry a suggestion in {layer_name} on {transport.value}: {layer}"
            )
        assert "natural-key" in envelope["adcp_error"]["suggestion"], (
            f"EXPIRED suggestion must point the buyer at a natural-key check on {transport.value}: "
            f"{envelope['adcp_error']['suggestion']}"
        )

    def test_in_flight_when_cache_row_absent_after_race(self, integration_db, transport):
        """A retry whose winner committed the backstop but whose verbatim cache row is not
        yet present rejects with IDEMPOTENCY_IN_FLIGHT (transient) on the real wire.

        Drives the degraded fail-closed path for the in-flight window: create for real
        (cache row + MediaBuy backstop), DELETE the cache row while the backstop stays fresh
        (the race winner's cache write not-yet-committed), then retry. The probe misses the
        absent row, the create hits the backstop, and — with a FRESH anchor, no surviving
        cache row, and a matching hash — the degraded path fails closed to transient
        IN_FLIGHT (rule 9 reject-and-redirect), never a fabricated body. Complements
        test_expired_replay_window_rejects: an AGED row rejects EXPIRED, an ABSENT row
        rejects IN_FLIGHT — the two fail-closed branches share one raise site, and this is
        the only idempotency code otherwise asserted solely through the reconstructed
        exception, so it is pinned here on every transport's real envelope.
        """
        from src.core.database.repositories import MediaBuyUoW

        key = f"wire-inflight-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = _create_kwargs(product, idempotency_key=key)

            first = env.call_via(transport, **dict(kwargs))
            assert first.is_success, f"fresh create failed on {transport.value}: {first.error}"

            # Remove the verbatim cache row while the backstop MediaBuy stays fresh — the
            # in-flight window (winner committed the resource, its cache write not yet landed).
            # Reclaim it via the repo's own eviction (as if the replay window had closed),
            # leaving the row ABSENT — distinct from the EXPIRED leg, where the row survives.
            with MediaBuyUoW(env._tenant_id) as uow:
                assert uow.idempotency_attempts is not None
                row = uow.idempotency_attempts.find_including_expired(
                    principal_id=env._principal_id, idempotency_key=key
                )
                assert row is not None, "the fresh create must have written a cache row"
                evicted = uow.idempotency_attempts.expire_old(now=datetime.now(UTC) + timedelta(days=2))
                assert evicted >= 1, "the fresh create's cache row must be reclaimed, leaving it absent"

            second = env.call_via(transport, **dict(kwargs))

        assert second.is_error, f"an in-flight replay must reject on {transport.value}"
        if transport is Transport.IMPL:
            # IMPL has no wire — grade the synthesized envelope, exactly as the other legs do.
            envelope = second.synthesized_error_envelope
        else:
            envelope = second.wire_error_envelope
        assert envelope is not None, f"IN_FLIGHT must carry the two-layer envelope on {transport.value}"
        assert_envelope_shape(envelope, "IDEMPOTENCY_IN_FLIGHT", recovery="transient")
        # The transient-recovery guidance must ride the WIRE on both envelope layers, not just
        # live at the raise site — parity with the EXPIRED leg above.
        for layer_name, layer in (("adcp_error", envelope["adcp_error"]), ("errors[0]", envelope["errors"][0])):
            assert layer.get("suggestion"), (
                f"IN_FLIGHT must carry a suggestion in {layer_name} on {transport.value}: {layer}"
            )


class TestA2ADefaultsDoNotBreakReplay:
    """A2A must not fold server-minted defaults into the canonical payload.

    A buyer omitting po_number and retrying the same idempotency_key via A2A
    must get a replay. A randomized server-side po_number default would hash
    the two identical requests differently — rejecting the legitimate retry as
    IDEMPOTENCY_CONFLICT — and would diverge from the same payload sent via
    MCP/REST (cross-transport parity).
    """

    def test_a2a_retry_without_po_number_replays(self, integration_db):
        key = f"wire-a2a-nopo-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = _create_kwargs(product, idempotency_key=key)
            kwargs.pop("po_number")

            first = env.call_via(Transport.A2A, **dict(kwargs))
            assert first.is_success, f"fresh create failed: {first.error}"

            second = env.call_via(Transport.A2A, **dict(kwargs))
            assert second.is_success, f"identical A2A retry must replay, got: {second.error}"
            assert second.payload.replayed is True
            assert second.payload.response.media_buy_id == first.payload.response.media_buy_id


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
        # Both parametrized transports are real wires — assert actual wire bytes,
        # never the synthesized fallback (a dead wire path must fail here).
        envelope = result.wire_error_envelope
        assert envelope is not None, f"missing-key rejection must carry the wire envelope on {transport.value}"
        assert_envelope_shape(
            envelope,
            "VALIDATION_ERROR",
            recovery="correctable",
            message_substr="idempotency_key",
        )


class TestWireLevelHashInput:
    """The payload hash is computed over the WIRE payload, not the model dump.

    AdCP defines payload equivalence as RFC 8785 over the request AS SENT.
    Two encodings of the same instant ("...Z" vs "...+00:00") normalize to the
    same value inside the request model — a model-level hash would replay — but
    they are different wire payloads, so the retry must conflict. Pins that the
    transport wrappers thread the raw wire dict into the hash (a wrapper that
    silently dropped it would fall back to model hashing and replay here).
    """

    def test_equivalent_but_differently_encoded_retry_conflicts(self, integration_db):
        key = f"wire-enc-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = _create_kwargs(product, idempotency_key=key)

            first = env.call_via(Transport.REST, **dict(kwargs))
            assert first.is_success, f"fresh create failed: {first.error}"

            # Same instant, different wire encoding: +00:00 instead of Z.
            reencoded = dict(kwargs)
            reencoded["start_time"] = reencoded["start_time"].replace("Z", "+00:00")

            second = env.call_via(Transport.REST, **reencoded)

        assert second.is_error, "a differently-encoded wire payload must not replay"
        assert_envelope_shape(second.wire_error_envelope, "IDEMPOTENCY_CONFLICT", recovery="correctable")


class TestCaptureUniformity:
    """The hash input is the payload AS SENT — captured uniformly per transport.

    Seller-side machinery (compat-field translation, body rewriting) must never
    participate in the hash: a buyer retrying byte-identical content replays,
    on the same transport or across transports.
    """

    def test_cross_transport_identical_retry_replays(self, integration_db):
        """The same payload dict created via REST replays when retried via MCP."""
        key = f"wire-xport-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = _create_kwargs(product, idempotency_key=key)

            first = env.call_via(Transport.REST, **dict(kwargs))
            assert first.is_success, f"fresh REST create failed: {first.error}"

            second = env.call_via(Transport.MCP, **dict(kwargs))
            assert second.is_success, f"MCP retry failed: {second.error}"

        assert second.payload.replayed is True, (
            "identical payload dicts must hash equal across transports — "
            "a transport-specific capture point (normalized vs raw) breaks this"
        )
        assert second.payload.response.media_buy_id == first.payload.response.media_buy_id

    def test_rest_deprecated_field_identical_retry_replays(self, integration_db):
        """A body carrying a deprecated field spelling replays on identical retry.

        RestCompatMiddleware rewrites the body for model parsing; the hash must
        see the bytes AS SENT (the stashed pre-rewrite body) — otherwise a
        seller-side compat-table change inside the TTL window would flip an
        honest retry into IDEMPOTENCY_CONFLICT.
        """
        key = f"wire-depr-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = _create_kwargs(product, idempotency_key=key)
            # Deprecated v2.5 spelling: the compat layer translates
            # campaign_ref -> buyer_campaign_ref before Pydantic parses the
            # body, so the rewritten bytes differ from the bytes as sent. The
            # body model declares neither key, so the probe is routing-inert —
            # purely wire-bytes-vs-normalized-bytes.
            kwargs["campaign_ref"] = "ref-deprecated-spelling"

            first = env.call_via(Transport.REST, **dict(kwargs))
            assert first.is_success, f"fresh create failed: {first.error}"

            second = env.call_via(Transport.REST, **dict(kwargs))
            assert second.is_success, f"identical retry failed: {second.error}"

        assert second.payload.replayed is True, (
            "the hash must cover the wire bytes as sent, not the compat-normalized body"
        )
