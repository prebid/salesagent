"""Ingest-time egress refusal of the buyer-supplied ``creatives[].format_id.agent_url``.

``sync_creatives`` lets the buyer name the creative agent their creative's
format lives on: ``creatives[].format_id.agent_url`` is read straight off the
request (``src/core/tools/creatives/_validation.py``) and dialled — through
``fetch_format_spec`` → ``CreativeAgentRegistry`` → ``call_mcp_tool`` — to
check the format exists. That makes it a buyer-supplied URL WE fetch, i.e. an
SSRF vector in the same class as ``property_list.agent_url`` on
``get_products`` (GH #1802, exemplar
``tests/integration/test_property_list_resolver.py``
``TestRefusedBuyerUrlOnTheWire``) — and it is reachable by any authenticated
advertiser, the lowest privilege bar of any egress path in the tree
(gh-#1790).

The connection itself is already refused: ``call_mcp_tool`` runs
``validate_url`` before its candidate loop. What this file grades is the half
that is NOT closed — WHOSE error the refusal is reported as. The registry
translates a seam refusal through ``raise_mapped_outbound_error``, whose
documented arm is the OPERATOR one: an agent endpoint that is registered tenant
configuration becomes ``CONFIGURATION_ERROR`` / terminal, "surface to a human
at the seller — the buyer cannot resolve a seller-side deployment
misconfiguration". That classification is correct for the registry's normal
caller and wrong here, because on this path the URL came out of the buyer's own
request document: the buyer is told a seller is misconfigured, is told not to
retry, and is given no ``field`` naming the input that was actually refused.

Provenance, not module, decides the classification —
``src/core/helpers/outbound_error_mapping.py`` says so in its own docstring
("The opposite case — a buyer-supplied URL — keeps the seam's own
AdCPBlockedUrlError (VALIDATION_ERROR) and names the offending field instead")
— and this path is the buyer arm it names.

Spec grounding — AdCP 3.1.1, the version this repo PINS (``adcp==6.6.0``,
``docs/adcp-spec-version.md``; prose via ``git -C <adcp-checkout> show
v3.1.1:<path>``):

1. ``docs/building/by-layer/L1/security.mdx`` § "Webhook URL validation
   (SSRF)": "Any URL that a buyer, seller, or governance agent provides for
   another party to fetch is an SSRF vector." Point 1 (reject non-HTTPS),
   point 2 (reject reserved-range addresses, ``169.254.169.254`` named
   outright), point 6 (disclose nothing about our network — which leaves
   ``error.field`` as the only channel naming WHICH input to fix).
2. ``docs/building/by-layer/L3/error-handling.mdx`` § "Recovery
   Classification" (§ "Request Validation" is a five-row table that lists
   ``INVALID_REQUEST``, not ``VALIDATION_ERROR``): egress policy refuses this
   URL identically forever, so ``terminal`` addressed to the SELLER strands a
   buyer who could fix it by sending a different ``agent_url``.
   ``VALIDATION_ERROR`` and ``CONFIGURATION_ERROR`` are both in the pinned
   ``dist/schemas/3.1.1/enums/error-code.json``; the enumMetadata
   recovery/suggestion pair quoted above is what makes them non-interchangeable.
   VALIDATION_ERROR rather than the sibling INVALID_REQUEST because that enum
   defines the two apart, verbatim: INVALID_REQUEST = "Request is malformed,
   missing required fields, or violates schema constraints"; VALIDATION_ERROR =
   "Request contains invalid field values or violates business rules beyond
   schema validation" — a ``format: "uri"``-valid URL in a blocked range is the
   latter. Both are correctable, so the buyer's retry
   behaviour is identical either way.
3. ``format_id`` is a property of ``dist/schemas/3.1.1/core/creative-asset.json``
   (``$ref`` ``core/format-id.json``, "Always a structured object
   {agent_url, id}"), and ``creatives`` is a top-level array on
   ``dist/schemas/3.1.1/creative/sync-creatives-request.json`` — hence the
   JSONPath-lite path ``creatives[].format_id.agent_url``.

Conformance storyboard: UNGRADED — nothing in ``dist/compliance/3.1.1/``
grades a seller refusing a counterparty-supplied URL (same finding recorded for
GH #1802 and for the webhook ingest twin).
"""

from __future__ import annotations

import uuid

import pytest

from tests.harness.transport import Transport
from tests.integration.property_list_helpers import enforce_egress_policy

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

# IMPL alongside the wire transports: the refusal belongs at the ingest
# boundary inside ``_impl``, so every dispatch path must carry it. Same matrix
# as the sibling sync error suites (test_creative_sync_transport.py,
# test_webhook_url_ingest_refusal.py's sync leg).
_ALL_TRANSPORTS = [Transport.IMPL, Transport.A2A, Transport.REST, Transport.MCP]

# Buyer-supplied URLs the seam refuses before opening a connection: one for
# scheme policy, one for address policy. Neither resolves DNS or touches the
# network, so both are hermetic.
_REFUSED_AGENT_URLS = [
    pytest.param("http://creative-agent.example.com", id="non-https-scheme"),
    pytest.param("https://169.254.169.254", id="cloud-metadata-address"),
]

_METADATA_URL = "https://169.254.169.254"

# Written as a literal, not derived from the request: ``error.field`` is a fact
# about the buyer's document (nbbe exemplar's ``_REFUSED_FIELD_PATH``
# rationale — a path computed from the URL would be the same guess in test
# form).
#
# INDEXED, because the pinned spec's ``field`` is JSONPath-lite and every
# example it gives is indexed (``docs/building/by-layer/L3/error-handling.mdx``
# :136 "e.g., packages[0].targeting", and :377/:385/:800). An empty-bracket
# ``creatives[]`` would also defeat this file's own reason for grading ``field``
# at all: a sync carries up to 100 creatives, so a path that cannot say WHICH
# one leaves the buyer exactly as stuck as no path. These tests send a
# single-creative payload, so the offending index is 0.
_REFUSED_FIELD_PATH = "creatives[0].format_id.agent_url"


def _creative_with_agent_url(agent_url: str, creative_id: str):
    """A schema-valid creative whose ONLY problem is the agent_url it names.

    Built through ``CreativeAssetFactory`` — overriding just ``format_id`` —
    rather than hand-rolled as a dict: a hand-rolled payload that misses a
    required field is rejected by the MCP wrapper's typed parameters BEFORE the
    request reaches any egress decision, and the resulting VALIDATION_ERROR
    looks enough like a refusal to pass a weak assertion for the wrong reason
    (observed: the first draft of this file failed MCP on three Pydantic
    'Field required' errors). Everything except the agent_url must be
    acceptable, so the refusal is the only error the request can produce.
    """
    from adcp.types import FormatId

    from tests.factories.creative_asset import CreativeAssetFactory

    return CreativeAssetFactory(
        creative_id=creative_id,
        name="Refused Agent URL Creative",
        format_id=FormatId(id="display_300x250_image", agent_url=agent_url),
    )


def _assert_refused_per_item(result, creative_id: str) -> None:
    """The refusal is a PER-ITEM failure carrying the seam's own classification.

    Per-item, not request-level, because ``format_id.agent_url`` is a PER-CREATIVE
    field: the pinned ``sync-creatives-response.json`` describes synchronous
    success as "best-effort processing with per-item status/failures" and says
    ``action="failed"`` items "indicate per-item validation/processing failures,
    not operation-level failures". (Its sibling
    ``push_notification_config.url`` fails the whole request because THAT field is
    request-level — the analogy does not carry.)

    What is graded is the classification, because that is what was wrong: the
    refusal used to arrive as ``CONFIGURATION_ERROR``/terminal from the registry's
    operator arm, and then as ``SERVICE_UNAVAILABLE`` with no recovery and no
    field from ``_failed_sync_result``'s defaults. Both told the buyer a SELLER
    was at fault for a URL the buyer chose.
    """
    payload = result.payload
    assert payload is not None, f"expected a sync response, got {result!r}"
    entries = [c for c in payload.creatives if c.creative_id == creative_id]
    assert entries, f"no result for {creative_id!r} in {payload.creatives!r}"
    entry = entries[0]

    assert str(getattr(entry.action, "value", entry.action)) == "failed", (
        f"a creative whose agent_url egress refused must not be synced; action={entry.action!r}"
    )
    assert entry.errors, f"a failed creative must carry an error; got {entry!r}"
    error = entry.errors[0]
    assert error.code == "VALIDATION_ERROR", (
        f"errors[0].code={error.code!r} — a URL the BUYER supplied is their correctable input, "
        "not a seller misconfiguration or a transient outage"
    )
    assert error.recovery == "correctable", (
        f"errors[0].recovery={error.recovery!r} — the buyer can fix this by sending a different URL"
    )
    assert error.field == _REFUSED_FIELD_PATH, (
        f"errors[0].field={error.field!r}, expected {_REFUSED_FIELD_PATH!r} — the message discloses "
        "nothing, so `field` is the only channel naming WHICH creative to fix"
    )


class TestRefusedCreativeAgentUrlOnTheWire:
    """A buyer-supplied ``format_id.agent_url`` the seam refuses is a CORRECTABLE buyer error.

    Today the refusal surfaces as ``CONFIGURATION_ERROR`` / terminal with no
    ``field`` (``raise_mapped_outbound_error``'s operator arm, reached via
    ``CreativeAgentRegistry``), which tells the buyer a SELLER is misconfigured
    and that nothing they send can fix it — about a URL they chose. The honest
    grading is the seam's own ``VALIDATION_ERROR`` / correctable naming the
    field, which is what these pin, on the wire, per transport.
    """

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    @pytest.mark.parametrize("agent_url", _REFUSED_AGENT_URLS)
    def test_refused_agent_url_is_a_correctable_buyer_error(self, integration_db, transport, agent_url, monkeypatch):
        """VALIDATION_ERROR / correctable / field=creatives[].format_id.agent_url, and nothing synced.

        ``correctable`` without ``error.field`` would tell the buyer "your
        request is fixable" and not what to fix — a sync request carries up to
        100 creatives, so "which one's agent_url" is not recoverable from the
        code alone. The message deliberately says nothing (L1 point 6, graded
        below), which leaves ``field`` as the only channel that can point at the
        offending input without disclosing anything about our network.

        The refusal must also be REQUEST-level, not laundered into a per-
        creative failure: inside the per-creative try it would surface as a
        partial success with a transient per-item error, i.e. "retry
        recommended" about a URL that is refused identically forever. The
        no-creative-persisted assert is what pins that.
        """
        from tests.harness.creative_sync import RealRegistryCreativeSyncEnv

        enforce_egress_policy(monkeypatch)
        creative_id = f"c_refused_agent_{uuid.uuid4().hex[:8]}"

        with RealRegistryCreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                transport,
                creatives=[_creative_with_agent_url(agent_url, creative_id)],
            )

            _assert_refused_per_item(result, creative_id)

            from src.core.database.repositories.uow import CreativeUoW

            with CreativeUoW(env._tenant_id) as uow:
                assert uow.creatives is not None
                persisted = uow.creatives.get_by_ids([creative_id], env._principal_id)
            assert persisted == [], (
                "a sync refused for its format_id.agent_url must apply no creative writes, "
                f"found {[c.creative_id for c in persisted]}"
            )

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_refusal_does_not_echo_the_supplied_url(self, integration_db, transport, monkeypatch):
        """The envelope names neither the supplied host nor its address (L1 point 6).

        Leaking the host back would turn ``creatives[].format_id.agent_url``
        into an internal port scanner with a spec-compliant error envelope
        wrapped around it — reachable, on this path, by any authenticated
        advertiser.
        """
        from tests.harness.creative_sync import RealRegistryCreativeSyncEnv

        enforce_egress_policy(monkeypatch)

        with RealRegistryCreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                transport,
                creatives=[_creative_with_agent_url(_METADATA_URL, f"c_leak_{uuid.uuid4().hex[:8]}")],
            )

            # The whole serialized response, not just the message: `field`,
            # `details` and `suggestion` are equally buyer-visible, and a leak in
            # any of them turns this into an internal port scanner with a
            # spec-compliant response wrapped around it (L1 point 6).
            serialized = str(result.wire_response or result.payload.model_dump(mode="json"))
            assert "169.254.169.254" not in serialized, (
                f"refusal echoed the supplied address back to the buyer: {serialized}"
            )


class TestAdapterProvidedFormatIsNotRefused:
    """An adapter-provided ``agent_url`` is NOT a destination, and must survive the gate.

    ``broadstreet://<tenant_id>`` is a real ``agent_url`` the seller ADVERTISES
    (``src/core/tools/creative_formats.py``,
    ``src/adapters/broadstreet/adapter.py``): the format is served by the adapter
    in-process, so there is no request to make and no address to judge. An egress
    gate that judged the string anyway would refuse it on scheme — rejecting a
    format the seller itself published, for a buyer who did exactly as told.

    This is the acceptance twin of the refusal cases above, and it is what makes
    ``is_dialled_agent_url`` load-bearing rather than decorative: delete that
    predicate's use in the pre-pass and this test goes red while every refusal
    test stays green.
    """

    @pytest.mark.parametrize("transport", _ALL_TRANSPORTS, ids=lambda t: t.value)
    def test_adapter_pseudo_url_still_syncs(self, integration_db, transport, monkeypatch):
        """A broadstreet:// format syncs normally with egress policy fully enforced."""
        from tests.harness.creative_sync import RealRegistryCreativeSyncEnv

        enforce_egress_policy(monkeypatch)
        creative_id = f"c_adapter_{uuid.uuid4().hex[:8]}"

        with RealRegistryCreativeSyncEnv() as env:
            env.setup_default_data()

            result = env.call_via(
                transport,
                creatives=[_creative_with_agent_url("broadstreet://default", creative_id)],
            )

            assert not result.is_error, (
                "an adapter-provided agent_url is served in-process and must not be judged as an "
                f"egress destination. Got: {result.error_envelope_or_none()!r}"
            )
