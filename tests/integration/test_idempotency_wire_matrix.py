"""Wire matrix for create_media_buy idempotency: replay / conflict / missing-key per transport.

AdCP 3.0.1 graded steps pinned at the real wire (not reconstructed exceptions):

- ``create_media_buy_replay``: an IDENTICAL retry returns the original response
  with top-level ``replayed: true``; the adapter is NOT re-invoked and no second
  booking exists.
- ``key_reuse_conflict``: the same key with a different canonical payload rejects
  with ``IDEMPOTENCY_CONFLICT`` on every transport.
- ``missing_key``: a create without idempotency_key rejects as INVALID_REQUEST —
  idempotency_key is in create-media-buy-request.json /required, so its absence
  violates a SCHEMA constraint (the REST pin lives in test_idempotency_replay;
  A2A/MCP are pinned here — a direct _impl call cannot express absence at all,
  the model requires the field).
- ``fresh_key_new_resource``: a different key with an identical payload creates a
  NEW media buy (no cross-key replay).

The request kwargs are built ONCE per test and copied per call: rebuilding them
would shift the start/end timestamps, changing the canonical payload hash and
turning an intended replay into a conflict.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.harness._base import DEFAULT_TEST_ACCOUNT_ID
from tests.harness.media_buy_create import OMIT_IDEMPOTENCY_KEY, MediaBuyCreateEnv
from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

WIRE_TRANSPORTS = [Transport.A2A, Transport.MCP, Transport.REST]


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
            assert second.payload.media_buy_id == first.payload.media_buy_id
            assert second.payload.status == first.payload.status
            # The handler was NOT re-invoked for the replay (storyboard's
            # no-duplicate-side-effects invariant).
            assert adapter_create.call_count == calls_after_first

            # Exactly one booking exists for this key (the unique index is the
            # backstop; the lookup returns the single winner).
            from src.core.database.repositories import MediaBuyUoW

            with MediaBuyUoW(env._tenant_id) as uow:
                assert uow.media_buys is not None
                existing = uow.media_buys.find_by_idempotency_key(
                    key, env._principal_id, account_id=DEFAULT_TEST_ACCOUNT_ID
                )
                assert existing is not None
                assert existing.media_buy_id == first.payload.media_buy_id

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
        # One assertion surface for every leg, owned by the result rather than by
        # this test: assert_wire_error reads the REAL wire bytes this dispatch
        # captured (never a harness-side reconstruction), fails loudly when no
        # envelope was captured instead of comparing against None, and grades the
        # code against CODE_TABLE. There is no IMPL branch left to write — this
        # matrix dispatches only transports that HAVE a wire.
        second.assert_wire_error("IDEMPOTENCY_CONFLICT", recovery="correctable")

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
        assert second.payload.media_buy_id != first.payload.media_buy_id

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
                    principal_id=env._principal_id, idempotency_key=key, account_id=DEFAULT_TEST_ACCOUNT_ID
                )
                assert row is not None, "the fresh create must have written a cache row"
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)

            second = env.call_via(transport, **dict(kwargs))

        assert second.is_error, f"an expired replay window must reject on {transport.value}"
        # Same single surface as the conflict leg above. The suggestion checks
        # below need the envelope itself, so it is read back AFTER the shape
        # assertion has already proved a wire envelope was captured.
        second.assert_wire_error("IDEMPOTENCY_EXPIRED", recovery="correctable")
        envelope = second.wire_error_envelope
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
            assert second.payload.media_buy_id == first.payload.media_buy_id


@pytest.mark.parametrize("transport", [Transport.A2A, Transport.MCP], ids=lambda t: t.value)
class TestMissingKeyWireMatrix:
    """Storyboard ``missing_key`` on the A2A and MCP wires (REST is pinned in
    test_idempotency_replay; IMPL cannot express absence — the model requires it)."""

    def test_missing_key_rejects_invalid_request(self, integration_db, transport):
        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = _create_kwargs(product, idempotency_key=OMIT_IDEMPOTENCY_KEY)
            result = env.call_via(transport, **kwargs)

        assert result.is_error, f"missing idempotency_key must reject on {transport.value}"
        # Both parametrized transports are real wires — assert actual wire bytes,
        # never the synthesized fallback (a dead wire path must fail here).
        envelope = result.wire_error_envelope
        assert envelope is not None, f"missing-key rejection must carry the wire envelope on {transport.value}"
        # INVALID_REQUEST, not VALIDATION_ERROR: idempotency_key is in
        # create-media-buy-request.json /required, so its absence violates a SCHEMA
        # CONSTRAINT, which 3.1/enums/error-code.json assigns to INVALID_REQUEST.
        # VALIDATION_ERROR is reserved for business rules "beyond schema validation",
        # and a required-field check is not beyond it.
        assert_envelope_shape(
            envelope,
            "INVALID_REQUEST",
            recovery="correctable",
        )
        assert envelope["errors"][0].get("field") == "idempotency_key"


class TestCaptureUniformity:
    """The hash input is the payload AS SENT, captured the same way on every transport.

    Seller-side machinery (compat-field translation, body rewriting) must never participate
    in the hash: a buyer retrying byte-identical content replays, on the same transport or
    across transports.
    """

    # Graduated: this carried a strict xfail citing #2214 -- "the expectation is right and
    # unmet", because the implementation hashed ``raw_wire_payload`` when a transport threaded
    # it and the model dump when none did, so REST and MCP disagreed about what "the same
    # request" is. The capture point is uniform now: ``src/core/tools/_boundary.py`` takes
    # ``canonical_request_hash(req)`` for every transport, and no transport threads bytes into
    # business logic at all. Verified by this test passing on the run that removed the marker.
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
            "identical payload dicts must hash equal across transports -- "
            "a transport-specific capture point (normalized vs raw) breaks this"
        )
        assert second.payload.media_buy_id == first.payload.media_buy_id


class TestHashInputIsTheValidatedRequest:
    """The digest is taken over the validated REQUEST MODEL, uniformly on every transport.

    This is a KNOWN DIVERGENCE from the pinned prose, recorded here rather than hidden.
    L1/security.mdx ("Payload equivalence") says: "'Equivalent' means identical canonical
    JSON form, not field-by-field semantic comparison. Sellers MUST determine equivalence by
    hashing the canonical form and comparing hashes." Read strictly, the canonical form is
    taken over the request body AS SENT, so two spellings of one instant ("...Z" versus
    "...+00:00") are different JSON strings, different hashes, and a retry that changes only
    the spelling is IDEMPOTENCY_CONFLICT.

    This seller hashes ``req.model_dump(mode="json")`` instead, so pydantic has already
    normalised the datetime and the two spellings replay. The reason is that the strict
    reading requires wire bytes inside business logic, and threading them there is what
    produced the defect this test module was written around: each transport captured its own
    bytes, MCP threaded them and REST did not, so "the same request" had a per-transport
    answer and replay was silently dead on the transport that forgot (#2214). One digest
    input, taken after validation, is what makes the four transports agree at all.

    The divergence is WIDER than the re-encoding this test exercises, and the wider half is
    not obviously safe. Production runs ``extra="ignore"`` (critical pattern #7) and every
    pinned AdCP request schema sets ``additionalProperties: true``, so a field the DTO does
    not declare is dropped by validation BEFORE the hash is taken. Two payloads differing
    only in such a field hash equal, and the second replays the first:

        ENVIRONMENT=production, same key, ``some_future_field`` ALPHA vs BETA
        -> canonical_request_hash equal -> the second request replays

    For a re-encoded timestamp the two requests are semantically identical and replaying is
    harmless. For an undeclared field they are identical only to THIS seller, today: the
    buyer meant two different things, and a later version that implements the field would
    read the same two payloads as genuinely different -- with cache rows written under the
    old reading still in the window.

    Closing either half properly means canonicalising the received body BEFORE validation, at
    a seam that does not exist. Recorded rather than narrowed, because a test that claims a
    smaller divergence than production has is worse than one that names the whole of it.
    """

    def test_a_re_encoded_but_equivalent_retry_replays(self, integration_db):
        """Same instant, different spelling, same key: the retry replays rather than conflicting."""
        key = f"wire-enc-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = _create_kwargs(product, idempotency_key=key)

            first = env.call_via(Transport.MCP, **dict(kwargs))
            assert first.is_success, f"fresh create failed: {first.error}"

            # Same instant, different wire encoding: +00:00 instead of Z.
            reencoded = dict(kwargs)
            reencoded["start_time"] = reencoded["start_time"].replace("Z", "+00:00")

            second = env.call_via(Transport.MCP, **reencoded)

        assert second.is_success, f"a re-encoded but equivalent retry must replay: {second.error}"
        assert second.payload.replayed is True, (
            "the digest is taken after validation, so the two spellings are one payload -- "
            "see this class's docstring for the divergence from the strict wire reading"
        )
        assert second.payload.media_buy_id == first.payload.media_buy_id

    def test_a_genuinely_different_payload_still_conflicts(self, integration_db):
        """The permissive reading is about ENCODING only -- a real field change still conflicts.

        Without this, the test above would be satisfied by a seller that had stopped
        comparing payloads at all.
        """
        key = f"wire-diff-{uuid.uuid4().hex}"

        with MediaBuyCreateEnv() as env:
            _tenant, _principal, product, _pricing = env.setup_media_buy_data()
            kwargs = _create_kwargs(product, idempotency_key=key)

            first = env.call_via(Transport.MCP, **dict(kwargs))
            assert first.is_success, f"fresh create failed: {first.error}"

            mutated = dict(kwargs)
            mutated["po_number"] = "A-DIFFERENT-PO"

            second = env.call_via(Transport.MCP, **mutated)

        assert second.is_error, "a changed field must not replay"
        second.assert_wire_error("IDEMPOTENCY_CONFLICT", recovery="correctable")
