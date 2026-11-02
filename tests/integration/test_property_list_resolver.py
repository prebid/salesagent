"""The property-list resolver, against a real origin and the real egress seam.

Replaces ``tests/unit/test_property_list_resolver.py``. Every class in that file
patched either ``httpx.AsyncClient`` or ``src.core.security.url_validator``, and
the resolver uses neither any more: it fetches through
``src/core/security/outbound_http.py``, which owns scheme policy, address
validation, IP pinning, redirect refusal, the response-size cap and retry
classification, and grades all of them once in
``tests/integration/test_outbound_http.py``. Re-asserting them per call site was
the duplication the seam exists to delete — and mock-asserting them was worse,
because a substituted client can only restate the answer the test already chose.

Two things do NOT move to the seam, so they are graded here:

* The resolver's **cache** — its own state, invisible to the seam. Against a
  real origin "the second call did not re-fetch" is ``origin.hits == 1``,
  which is a stronger claim than a mock's ``call_count`` because a hit can only
  be recorded by a server that actually served a request.
* The **wire class of a refusal**. Migrating onto the seam moves a refused
  buyer-supplied ``agent_url`` from ``AdCPAdapterError``
  (SERVICE_UNAVAILABLE / transient — "try again later") to
  ``OutboundRequestBlocked`` (VALIDATION_ERROR / correctable — "fix the URL").
  That is a buyer-visible contract change, so it is asserted on the wire
  envelope through ``get_products``, not on a reconstructed exception.

Spec grounding — AdCP 3.1.1, the version this repo PINS (``adcp==6.6.0``, see
``docs/adcp-spec-version.md``). Read the pinned prose with
``git -C <adcp-checkout> show v3.1.1:<path>``; ``dist/docs/3.1.1/`` does not
exist at that tag, the prose lives at ``docs/…``.

**The pinned spec does not rule directly on ``property_list.agent_url``.**
``VALIDATION_ERROR`` / ``correctable`` is spec-CONSISTENT by analogy, not
spec-mandated, and is grounded on four passages:

1. ``docs/building/by-layer/L1/security.mdx`` § "Webhook URL validation
   (SSRF)": "Any URL that a buyer, seller, or governance agent provides for
   another party to fetch is an SSRF vector." Fetchers MUST reject non-HTTPS
   URLs (1) and reserved-range addresses (2), and MUST NOT "echo fetch errors
   to the agent that supplied the URL" (6). It prescribes the refusal and the
   silence — it names no wire code.
2. ``docs/building/by-layer/L3/error-handling.mdx`` § "Request Validation"
   is a five-row table and ``VALIDATION_ERROR`` is NOT one of its rows — the
   only request-validation row that could cover a refused URL is
   ``INVALID_REQUEST | correctable | Request is malformed or violates schema
   constraints``. The code this repo emits is settled one level down, in the
   pinned ``enums/error-code.json``, which defines both and separates them
   verbatim: INVALID_REQUEST = "Request is malformed, missing required fields,
   or violates schema constraints"; VALIDATION_ERROR = "Request contains
   invalid field values or violates business rules beyond schema validation".
   A ``format: "uri"``-valid URL that egress policy refuses is the latter — the
   value is well-formed and violates a rule beyond the schema — so this repo
   emits VALIDATION_ERROR (``AdCPBlockedUrlError``, the ONE refusal class both
   the seam and the DNS-free registration gate raise). Both codes carry
   ``recovery: "correctable"`` in the same enum, so the half the buyer acts on
   is identical either way; the prose table's silence on VALIDATION_ERROR is
   why this section says spec-CONSISTENT, not spec-mandated.
   § "Recovery Classification": ``correctable`` = "Request can be fixed and
   resent", ``transient`` = "Temporary failure … retry with exponential
   backoff"; the ``CONFIGURATION_ERROR`` row states the contrast outright —
   "``SERVICE_UNAVAILABLE`` (transient) and ``INVALID_REQUEST``
   (buyer-fixable)". A URL egress policy will refuse identically forever is not
   temporary, so ``transient`` would promise something false.
3. ``docs/accounts/tasks/sync_accounts.mdx``: a counterparty-supplied
   notification URL that is malformed is refused with ``INVALID_REQUEST`` (and
   the reserved-range/metadata case synchronously with ``VALIDATION_ERROR``,
   per ``docs/learning/specialist/security.mdx``) — both ``correctable`` in
   ``static/schemas/source/enums/error-code.json``. That split is the same one
   this repo now applies: malformed -> INVALID_REQUEST, policy-blocked -> the
   VALIDATION_ERROR this file grades.
4. ``CREATIVE_INACCESSIBLE`` in that same enum: a buyer-supplied asset URL the
   agent could not fetch is ``correctable``. Every time the pinned spec rules on
   an unfetchable counterparty URL, it grades it correctable.

Conformance storyboard: **ungraded**. No step in ``dist/compliance/3.1.1/``
grades an egress refusal of a buyer-supplied URL (``property_list_ref``: 0 hits;
the three ``ssrf`` hits are runner-side, grading the runner's own fetches). The
nearest analogue is storyboard ``notification_config_rejections``, step
``sync_accounts_rejects_duplicate_subscriber_id``, which grades a buyer-supplied
``notification_configs[]`` field rejection as
``allowed_values: ["INVALID_REQUEST", "VALIDATION_ERROR"]`` — same family,
different field, and it accepts either of the two codes this section weighs.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.core.property_list_resolver import clear_cache, resolve_property_list
from tests.factories import PricingOptionFactory, PrincipalFactory, ProductFactory, TenantFactory
from tests.harness.product import RealResolverProductEnv
from tests.harness.transport import Transport
from tests.integration.property_list_helpers import (
    allow_local_origin,
    domain_identifiers,
    enforce_egress_policy,
    origin_ref,
    property_list_body,
)

pytestmark = [pytest.mark.integration]

# Wire transports that can carry a ``property_list``. All three: ``property_list``
# is a top-level property of get-products-request.json at the pinned AdCP 3.1.1,
# so a REST buyer that cannot send one is a protocol gap, not a transport quirk
# (salesagent-sxl4).
_WIRE_TRANSPORTS = [Transport.MCP, Transport.A2A, Transport.REST]

# Buyer-supplied URLs the seam refuses before opening a connection: one for
# scheme policy, one for address policy. Neither resolves DNS or touches the
# network, so both are hermetic.
_REFUSED_AGENT_URLS = [
    pytest.param("http://property-agent.example.com", id="non-https-scheme"),
    pytest.param("https://169.254.169.254", id="cloud-metadata-address"),
]

# The JSONPath-lite path the buyer has to edit to fix the refusal. ``property_list``
# is a TOP-LEVEL property of ``dist/schemas/3.1.1/media-buy/get-products-request.json``
# ``$ref``-ing ``core/property-list-ref.json`` (required ``["agent_url","list_id"]``),
# so the path is ``property_list.agent_url`` — not a ``packages[i]`` path. Written as
# a literal, not derived: ``error.field`` is a fact about the buyer's document, and a
# path computed from the URL would be the same guess in test form.
_REFUSED_FIELD_PATH = "property_list.agent_url"


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    """Clear the module-level resolver cache around every test.

    The cache outlives a test function, so one test's successful fetch would
    otherwise satisfy the next test's first call and silently drop its hit
    count to zero.
    """
    clear_cache()
    yield
    clear_cache()


def _future(hours: int = 1) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


def _past(seconds: int = 1) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


class TestFetch:
    """What the resolver asks the agent service for, and what it does with the answer."""

    @pytest.mark.asyncio
    async def test_fetches_the_list_path_and_returns_identifier_values(self, local_origin_tls, monkeypatch):
        """The GET lands on ``{agent_url}/lists/{list_id}`` and the values come back."""
        allow_local_origin(monkeypatch)
        local_origin_tls.respond_with(200, body=property_list_body(domain_identifiers("example.com", "test.org")))

        result = await resolve_property_list(origin_ref(local_origin_tls, list_id="my-list-42"))

        assert result == ["example.com", "test.org"]
        assert local_origin_tls.paths == ["/lists/my-list-42"]

    @pytest.mark.asyncio
    async def test_omitted_identifiers_resolve_to_an_empty_list(self, local_origin_tls, monkeypatch):
        """A response with no ``identifiers`` key resolves to ``[]``, not an error."""
        allow_local_origin(monkeypatch)
        local_origin_tls.respond_with(200, body=property_list_body(None))

        assert await resolve_property_list(origin_ref(local_origin_tls)) == []


class TestCaching:
    """The resolver's own cache — the one behaviour the egress seam does not own.

    Every assertion here is a hit count on an origin that really served the
    requests, so "cached" means "the network was not touched a second time"
    rather than "a mock was not called again".
    """

    @pytest.mark.asyncio
    async def test_cached_result_avoids_a_second_fetch(self, local_origin_tls, monkeypatch):
        """A second call inside the TTL is served from cache — the origin sees one request."""
        allow_local_origin(monkeypatch)
        local_origin_tls.respond_with(
            200,
            body=property_list_body(domain_identifiers("cached.com"), cache_valid_until=_future()),
        )
        ref = origin_ref(local_origin_tls)

        first = await resolve_property_list(ref)
        second = await resolve_property_list(ref)

        assert first == ["cached.com"]
        assert second == ["cached.com"]
        assert local_origin_tls.hits == 1

    @pytest.mark.asyncio
    async def test_expired_cache_causes_a_refetch(self, local_origin_tls, monkeypatch):
        """``cache_valid_until`` already in the past means the next call re-fetches."""
        allow_local_origin(monkeypatch)
        local_origin_tls.respond_with(
            200,
            body=property_list_body(domain_identifiers("fresh.com"), cache_valid_until=_past()),
        )
        ref = origin_ref(local_origin_tls)

        await resolve_property_list(ref)
        await resolve_property_list(ref)

        assert local_origin_tls.hits == 2

    @pytest.mark.asyncio
    async def test_missing_cache_valid_until_still_caches_for_the_default_ttl(self, local_origin_tls, monkeypatch):
        """No ``cache_valid_until`` means the default TTL applies, not "do not cache"."""
        allow_local_origin(monkeypatch)
        local_origin_tls.respond_with(200, body=property_list_body(domain_identifiers("default-ttl.com")))
        ref = origin_ref(local_origin_tls)

        first = await resolve_property_list(ref)
        second = await resolve_property_list(ref)

        assert first == ["default-ttl.com"]
        assert second == ["default-ttl.com"]
        assert local_origin_tls.hits == 1

    @pytest.mark.asyncio
    async def test_different_list_ids_are_cached_separately(self, local_origin_tls, monkeypatch):
        """The cache is keyed per list_id — one list's entry never answers for another."""
        allow_local_origin(monkeypatch)
        local_origin_tls.respond_in_sequence(
            [
                (200, property_list_body(domain_identifiers("a.com"), cache_valid_until=_future())),
                (200, property_list_body(domain_identifiers("b.com"), cache_valid_until=_future())),
            ]
        )

        result_a = await resolve_property_list(origin_ref(local_origin_tls, list_id="list-a"))
        result_b = await resolve_property_list(origin_ref(local_origin_tls, list_id="list-b"))

        assert result_a == ["a.com"]
        assert result_b == ["b.com"]
        assert local_origin_tls.paths == ["/lists/list-a", "/lists/list-b"]

    @pytest.mark.asyncio
    async def test_a_failed_fetch_is_not_cached(self, local_origin_tls, monkeypatch):
        """A failure leaves no cache entry, so the next call really re-fetches.

        404 rather than 503: a 404 is terminal at the seam, so it costs exactly
        one hit and the second call's hit is unambiguously a fresh fetch rather
        than a retry of the first.
        """
        from src.core.security.outbound_http import OutboundDeliveryFailed

        allow_local_origin(monkeypatch)
        local_origin_tls.respond_with(404, body=b"nope")
        ref = origin_ref(local_origin_tls)

        with pytest.raises(OutboundDeliveryFailed):
            await resolve_property_list(ref)
        assert local_origin_tls.hits == 1

        local_origin_tls.respond_with(
            200,
            body=property_list_body(domain_identifiers("success.com"), cache_valid_until=_future()),
        )

        assert await resolve_property_list(ref) == ["success.com"]
        assert local_origin_tls.hits == 2


@contextmanager
def _products_env(tenant_id: str) -> Iterator[RealResolverProductEnv]:
    """A ``get_products`` env running the real resolver, with one sellable product seeded.

    The product exists so the request reaches the property-list branch on a
    realistic catalogue rather than an empty one — the refusal must not depend
    on there being nothing to filter.
    """
    principal_id = f"{tenant_id}-principal"
    with RealResolverProductEnv(tenant_id=tenant_id, principal_id=principal_id) as env:
        tenant = TenantFactory(tenant_id=tenant_id, subdomain=tenant_id)
        PrincipalFactory(tenant=tenant, principal_id=principal_id)
        product = ProductFactory(tenant=tenant, product_id=f"{tenant_id}-product")
        PricingOptionFactory(product=product, pricing_model="cpm", rate=Decimal("10.0"), is_fixed=True)
        yield env


class TestRefusedBuyerUrlOnTheWire:
    """A buyer-supplied ``agent_url`` the seam refuses is a CORRECTABLE buyer error.

    Before the seam migration this surfaced as ``AdCPAdapterError`` —
    SERVICE_UNAVAILABLE / transient — which tells the buyer to retry a request
    that will be refused identically every time. The refusal is a property of
    the URL the buyer sent, so VALIDATION_ERROR / correctable is the honest
    grading and is what this pins, on the wire, per transport — together with
    the ``error.field`` path that says WHICH URL, since the message itself may
    say nothing (L1 point 6).
    """

    @pytest.mark.requires_db
    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS, ids=lambda t: t.value)
    @pytest.mark.parametrize("agent_url", _REFUSED_AGENT_URLS)
    def test_refused_agent_url_is_a_correctable_buyer_error(self, integration_db, transport, agent_url, monkeypatch):
        """The refusal is VALIDATION_ERROR / correctable AND names the field to fix.

        ``correctable`` without ``error.field`` tells the buyer "your request is
        fixable" and not what to fix — and ``get_products`` carries several URLs
        and several other correctable failure modes, so "which one" is not
        recoverable from the code alone. The refusal message deliberately says
        nothing (L1 point 6, graded by the sibling test below), which leaves
        ``field`` as the only channel that can point at the offending input
        without disclosing anything about our network: it is a path into the
        document the buyer sent us, not a fact about the destination.

        Level 2 of ``error-handling.mdx:16`` is the grounding — ``recovery``,
        ``retry_after``, ``field`` and ``suggestion`` exist so an agent can
        self-correct correctable errors. Both refusal causes carry it (scheme
        and address are the same buyer input either way), on both envelope
        layers.
        """
        enforce_egress_policy(monkeypatch)

        with _products_env("proplist-egress") as env:
            result = env.call_via(
                transport,
                brief="property list egress test",
                property_list={"agent_url": agent_url, "list_id": "test_list"},
            )

        assert result.is_error, (
            "A refused buyer-supplied agent_url must fail get_products, not be silently "
            f"ignored. Got: {getattr(result, 'wire_response', None) or result.payload!r}"
        )
        result.assert_wire_error(
            "VALIDATION_ERROR",
            recovery="correctable",
            field=_REFUSED_FIELD_PATH,
        )

    @pytest.mark.requires_db
    @pytest.mark.parametrize("transport", _WIRE_TRANSPORTS, ids=lambda t: t.value)
    def test_refusal_message_does_not_echo_the_supplied_url(self, integration_db, transport, monkeypatch):
        """The envelope names neither the host nor its resolved address (L1 point 6).

        The seam's fixed refusal message is what reaches the buyer; leaking the
        host back would turn ``property_list.agent_url`` into an internal port
        scanner with a spec-compliant error envelope wrapped around it.
        """
        enforce_egress_policy(monkeypatch)

        with _products_env("proplist-leak") as env:
            result = env.call_via(
                transport,
                brief="property list leak test",
                property_list={"agent_url": "https://169.254.169.254", "list_id": "test_list"},
            )

        assert result.is_error
        envelope_text = str(result.wire_error_envelope)
        assert "169.254.169.254" not in envelope_text, (
            f"refusal echoed the supplied address back to the buyer: {envelope_text}"
        )
