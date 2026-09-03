"""Whose URL was refused, as a TYPE — and what that type is allowed to put on the wire.

The egress seam reports a refusal the same way whatever it refused
(``src/core/security/outbound_http.py``, ``_BLOCKED_MESSAGE``): opaque, because
"a refusal must not disclose network topology" is a spec obligation, not a
courtesy (AdCP 3.1.1, ``docs/building/by-layer/L1/security.mdx``
point 6, read at tag ``v3.1.1``). What the refusal is ALLOWED to say beyond that
depends entirely on WHOSE address it was:

* a **counterparty** URL — one that arrived on the buyer's own request document —
  may name the request-document path it arrived on, so the buyer knows which of
  up to 100 creatives to fix;
* an **operator** endpoint — a registered agent, a vendor host — may name
  neither a field (the buyer's document contains no path to it) nor the endpoint
  itself (that is the topology disclosure the spec forbids).

Today that distinction is carried by ``field: str | None`` — provenance by
OMISSION. Two consequences are graded here, one on each side of the union:

1. ``media_buy_create.py`` has a counterparty URL (a stored creative's
   ``agent_url``, which the buyer supplied on a prior ``sync_creatives``) but no
   canonical request-document path to name it by — ``create-media-buy-request.json``
   @3.1.1 defines no ``creative:{id}`` path — so it FABRICATES one,
   ``f"creative:{creative.creative_id}.agent_url"``, purely to make the
   ``field is not None`` test come out "counterparty". A locator is a real path
   into the request document or it is absent; it is never invented.
2. ``creatives/_processing.py`` builds its operator label by string-formatting
   ``getattr(format_obj, "agent_url", None)`` into buyer-visible text — a free
   ``str`` label is allowed to hold an endpoint, so one does.

``CounterpartyUrl(field=...) | OperatorEndpoint(name=...)`` makes possession of
the variant the proof: there is no field to omit, and ``OperatorEndpoint``
refuses a name containing ``"://"`` at construction, so the label above becomes
unconstructible rather than hand-corrected.

Recovery hints below are read off the pinned enum, never off
``STANDARD_ERROR_CODES``: ``dist/schemas/3.1.1/enums/error-code.json``
``enumMetadata`` gives ``VALIDATION_ERROR`` = {recovery: correctable,
suggestion: "review error details and fix field values"} and
``CONFIGURATION_ERROR`` = {recovery: terminal, suggestion: "surface to a human
at the seller — the buyer cannot resolve a seller-side deployment
misconfiguration and MUST NOT auto-retry"}.

Conformance storyboard: **ungraded** — nothing in ``dist/compliance/3.1.1/``
exercises a creative-agent egress refusal on either tool (the same finding
``test_creative_agent_egress.py`` and ``test_creative_agent_dial_refusal_recovery.py``
already record).

beads: GH #1802
"""

from __future__ import annotations

import json
import uuid

import pytest

from src.core.security.outbound_http import OutboundRequestBlocked
from tests.factories import CreativeFactory
from tests.factories.creative_asset import CreativeAssetFactory
from tests.factories.format import AGENT_URL, FormatFactory
from tests.harness.creative_sync import CreativeSyncEnv
from tests.harness.media_buy_create import RealFormatResolverMediaBuyCreateEnv
from tests.harness.transport import Transport
from tests.helpers import assert_envelope_shape
from tests.integration.media_buy_helpers import _single_creative_request

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# Wire transports only — IMPL has no wire envelope by definition, and a
# ``field`` that only the synthesized envelope drops would prove nothing about
# what a buyer receives.
_WIRE_TRANSPORTS = [Transport.REST, Transport.MCP, Transport.A2A]

_ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.REST, Transport.MCP]

_FORMAT_ID = "display_300x250"

# What the operator arm of ``raise_mapped_outbound_error`` must say once its
# label is an ``OperatorEndpoint("the creative agent")`` rather than an
# interpolated attribute read: the mapper's own template, filled with a name
# that identifies a ROLE, not an address.
_OPERATOR_REFUSAL_MESSAGE = (
    "The configured endpoint for the creative agent is not reachable under this deployment's egress policy."
)


class TestACounterpartyRefusalNamesNoFabricatedLocator:
    """A refused creative ``agent_url`` on ``create_media_buy`` names no ``field``.

    ``_validate_creatives_before_adapter_call`` dials the stored creative's
    ``agent_url`` through the real resolver, so the refusal on the wire is
    produced by production code end to end: the seam refuses a plain-``http``
    loopback origin before a socket is opened (``hits == 0``), and the envelope
    that reaches the buyer is whatever the call site chose to attach to it.

    The code and recovery are unchanged by this lane and are pinned so a
    reclassification cannot ride along with the locator deletion:
    ``OutboundRequestBlocked`` is an ``AdCPBlockedUrlError``, i.e.
    VALIDATION_ERROR / correctable per the pinned ``enumMetadata``.
    """

    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS, ids=lambda t: t.value)
    def test_a_refused_creative_agent_url_carries_no_field(self, integration_db, transport, local_origin):
        creative_id = f"c_refused_{uuid.uuid4().hex[:8]}"

        with RealFormatResolverMediaBuyCreateEnv() as env:
            # The production posture, stated out loud: a refusal test that left
            # the hatch to ambient environment could be silently disarmed.
            env.set_egress_hatches(private=False)
            tenant, principal, _product, _pricing_option = env.setup_media_buy_data()
            CreativeFactory(
                tenant=tenant,
                principal=principal,
                creative_id=creative_id,
                format=_FORMAT_ID,
                agent_url=local_origin.base_url,
                data={"url": "https://example.com/ad.jpg", "width": 300, "height": 250},
            )

            result = env.call_via(transport, req=_single_creative_request(creative_id))

            assert result.is_error, (
                f"a creative whose agent_url egress policy refuses must fail create_media_buy: {result.payload!r}"
            )
            envelope = result.wire_error_envelope
            assert_envelope_shape(envelope, "VALIDATION_ERROR", recovery="correctable")

            assert local_origin.hits == 0, f"a refused agent_url was still dialled: {local_origin.requests}"

            assert "field" not in envelope["errors"][0], (
                f"errors[0].field={envelope['errors'][0].get('field')!r} — create-media-buy-request.json @3.1.1 "
                "defines no such path, so this locator is fabricated: a buyer cannot navigate to it, and "
                "CounterpartyUrl(field=None) is the honest statement that this URL has no request-document path"
            )
            assert "field" not in envelope["adcp_error"], (
                f"adcp_error.field={envelope['adcp_error'].get('field')!r} — both envelope layers must agree "
                "that there is no locator"
            )
            assert "creative:" not in json.dumps(envelope), (
                f"the fabricated creative:{{id}}.agent_url locator still rides somewhere on the envelope: {envelope}"
            )


class TestAnOperatorRefusalNamesNoEndpoint:
    """A refused OPERATOR dial names a role, never an address.

    ``sync_creatives`` dials the tenant's own registered creative agent to
    validate a creative (``_processing.py``). When the seam refuses that dial the
    per-item error the buyer reads is the operator arm of
    ``raise_mapped_outbound_error`` — CONFIGURATION_ERROR / terminal per the
    pinned ``enumMetadata`` — and its message is built from the label the call
    site passed. Today that label is
    ``f"creative agent {getattr(format_obj, 'agent_url', None)}"``: an attribute
    read interpolated into buyer-visible text, which is a topology disclosure
    whenever it resolves and a bare ``None`` when it does not. Neither is a name.
    """

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_a_refused_operator_dial_message_names_a_role_not_an_address(self, integration_db, transport):
        creative_id = f"c_operator_label_{uuid.uuid4().hex[:8]}"

        with CreativeSyncEnv() as env:
            env.setup_default_data()

            registry = env.mock["registry"].return_value
            env.set_run_async_result([FormatFactory(format_id={"id": _FORMAT_ID, "agent_url": AGENT_URL})])
            registry.preview_creative.side_effect = OutboundRequestBlocked()

            from adcp.types import FormatId

            result = env.call_via(
                transport,
                creatives=[
                    CreativeAssetFactory(
                        creative_id=creative_id,
                        format_id=FormatId(id=_FORMAT_ID, agent_url=AGENT_URL),
                    )
                ],
            )

            payload = result.payload
            assert payload is not None, f"expected a sync response, got {result!r}"
            entries = [c for c in payload.creatives if c.creative_id == creative_id]
            assert entries, f"no result for {creative_id!r} in {payload.creatives!r}"
            error = entries[0].errors[0]

            assert error.code == "CONFIGURATION_ERROR", f"errors[0].code={error.code!r}"
            assert error.recovery == "terminal", f"errors[0].recovery={error.recovery!r}"

            assert error.message == _OPERATOR_REFUSAL_MESSAGE, (
                f"errors[0].message={error.message!r} — an operator refusal names the ROLE whose endpoint was "
                f"refused, exactly {_OPERATOR_REFUSAL_MESSAGE!r}. Interpolating "
                "getattr(format_obj, 'agent_url', None) instead yields a topology disclosure when it resolves "
                "and the literal 'None' when it does not"
            )
            assert "://" not in error.message, (
                f"a scheme rode into a buyer-visible refusal message: {error.message!r} — security.mdx @3.1.1 "
                "point 6 forbids disclosing network topology on a refusal"
            )
            assert AGENT_URL not in error.message, f"the operator's registered endpoint leaked into {error.message!r}"


class TestOperatorEndpointRefusesAnAddressAsItsName:
    """The type, not a code review, is what stops a URL becoming a label.

    ``OperatorEndpoint`` carries the name of a ROLE this deployment stands
    behind. Accepting a URL there would re-open the disclosure
    ``TestAnOperatorRefusalNamesNoEndpoint`` closes, at every future call site,
    so the constructor refuses it: possession of an ``OperatorEndpoint`` is the
    proof that no address is being labelled.
    """

    def test_a_url_is_not_a_name(self):
        from src.core.security.outbound_http import OperatorEndpoint

        with pytest.raises(ValueError):
            OperatorEndpoint("https://x.example")

    def test_a_role_is_a_name(self):
        from src.core.security.outbound_http import OperatorEndpoint

        assert OperatorEndpoint("the creative agent").name == "the creative agent"
