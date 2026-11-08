"""E2E: a counterparty resolves our signing keys from our published trust root.

TDD RED for #1291 A3 (salesagent-z6nr.9), step 9 as amended by R-H2.

This walks the SDK's own three-hop discovery (``adcp.signing.async_resolve_agent``)
against the live Docker stack, and is the only test in the A3 suite that grades
the documents as a real counterparty consumes them rather than as we build them.

Two deliberate seams, each with its reason:

1. **Hop 1 is seeded** (``_capabilities_client_factory``), because this module
   grades A3, whose scope stops at the published documents. Seeding hop 1 from A3's
   own helpers is what keeps a failure here attributable to brand.json or the JWKS
   rather than to the capabilities builder.

   What is NO LONGER a reason: SDK divergence #4. ``_fetch_capabilities`` used to
   do a raw ``GET <agent_url>`` and require a JSON object body, while
   security.mdx:1142 said the opposite verbatim — "invoke
   ``get_adcp_capabilities`` via the agent's declared transport (MCP
   ``tools/call`` or A2A skill invocation), **not** a raw HTTP ``GET``". The SDK
   now does what the spec says, so the seed is a SCOPE decision alone and the
   transport under it speaks MCP (``json_seeded_client_factory``). Un-seeding hop 1
   here would still need a second decision this module has no business making: the
   url walked is the card's A2A interface url (``/a2a``) while the resolver's
   default protocol is MCP. ``AGENT_ENDPOINT_PATHS`` publishes a ``/mcp`` entry
   too, so what a buyer resolves against is reachable; which of our two published
   entries a walk should pick is a question for whoever un-seeds this.

   ``identity`` is no longer gated: publishing the trust root is what took it out
   of ``_UNBACKED_BLOCKS`` (``src/core/schemas/capability_declarations.py``), and
   ``emitted_identity`` (``src/core/signing/posture.py``) puts it on the wire — so
   the served capabilities document really does carry it. Grading THAT document —
   fetched, not seeded — is ``test_jwks_publication_e2e`` (salesagent-mp53.7), not
   this module.

   So the seeded body's ``identity`` is built from **A3's own** helpers, and the
   test asserts the values the resolver ends up using are those helpers' output.

2. **Hops 2 and 3 are live.** No ``_brand_jwks_client_factory`` is passed —
   brand.json and the JWKS are fetched from the running stack over real TLS,
   because those two hops are the ones A3 owns.

The walk runs against ``seller.adcp-e2e.dev`` on the stack's one TLS front, and two
independent rules put it there. Neither is a stack limitation; both are the pin.

* **https.** ``identity.brand_json_url`` is pinned ``pattern: "^https://"``, so
  production refuses to publish one for a host that cannot serve https:
  ``origin_is_publishable`` (``src/core/signing/posture.py``) gates the emission and
  ``emitted_identity`` returns ``None``. A plaintext ``localhost:<port>`` tenant
  therefore publishes NO trust root, and a walk seeded with an ``http://`` pointer
  was grading a document this agent never serves — which is what this module did
  until ``adcp`` 9's ``_extract_brand_json_url`` refused the scheme and said so.
* **A registrable domain.** security.mdx step 3 (Origin binding) requires the agent
  url's host eTLD+1 to equal the ``brand_json_url``'s, computed from a pinned Public
  Suffix List snapshot. ``.test`` and ``localhost`` are in no PSL, so both sides
  compute ``None``, equality never holds, and the documented fallback wants an
  ``authorized_operators[]`` entry only a House Portfolio document carries. A
  conformant verifier MUST answer ``request_signature_brand_origin_mismatch`` there,
  and the SDK does, with no development-domain opt-in to pass. So the name a buyer
  resolves us by is ``*.adcp-e2e.dev`` — the same conclusion, in the same stack, that
  the counterparty origin reached for the inbound direction. See
  :data:`~tests.e2e._signing_e2e.SELLER_ETLD1_HOST`.

That second rule is also why this test is in-network only: the name is a compose
alias, and the host path has no PSL-resolvable name for a loopback listener.

Two test arguments make a private TLS front walkable, and neither relaxes a check:

* ``allow_private_destinations=True`` opens the SDK's SSRF pin so a loopback or
  compose-internal address is reachable. No production path reads it: the inbound
  verifier (``src/core/signing/verifier.py``) pins it ``False`` on every resolution
  walk, and ``src/core/config.py`` deliberately exposes no knob for it. This module
  can pass ``True`` precisely because it calls ``async_resolve_agent`` itself rather
  than going through that verifier.
* ``SSL_CERT_FILE`` points at ``.test-tls/combined-ca.pem`` — the public roots PLUS
  this stack's private CA — so full certificate validation stays on and simply
  trusts our leaf as well. The SDK's hops 2 and 3 build their own
  ``ssl.create_default_context()`` (``adcp.signing.ip_pinned_transport.
  _build_ssl_context``), which takes no cafile and honours that variable at the
  OpenSSL level; hop 3 (``async_default_jwks_fetcher``) has no client-factory seam
  at all, so this is the only way to hand it a trust anchor. The in-network runner
  already sets it (``docker-compose.e2e.yml``); ``tests/e2e/conftest.py`` sets the
  same default on the host path. ``verify=False`` is what this avoids, and it would
  have destroyed the handshake these two hops exist to perform.
"""

from __future__ import annotations

import contextlib

import httpx
import pytest

from tests.e2e._signing_e2e import (
    SELLER_ETLD1_HOST,
    ca_verified_ssl_context,
    netloc,
    origin,
    provisioned_trust_root_tenant,
    seeded_capabilities_factory,
    seller_etld1_base_url,
    tls_base_url,
)
from tests.e2e.conftest import e2e_in_network
from tests.helpers.signing import published_kids

_SLUG = "trustroot_e2e"
_TENANT_ID = "tr_e2e"

# The TLS sibling gets its OWN tenant id and slug. Both fixtures delete by tenant
# id at setup AND teardown, and ``virtual_host`` is host-routing state shared by
# the whole e2e session — a shared id would have the two tests stomping each
# other's routing.
_TLS_SLUG = "trustroot_e2e_tls"
_TLS_TENANT_ID = "tr_e2e_tls"

#: The walk below needs a seller name with an eTLD+1 in the Public Suffix List
#: (see :data:`SELLER_ETLD1_HOST`), and that name is a compose network alias. It
#: resolves inside the stack's network and nowhere else, so on the host path there
#: is no address for this test to dial — not a weaker assertion, an absent origin.
#: ``./run_all_tests.sh`` runs every suite in-network, which is where this is
#: graded. Same device, same wording as
#: ``tests/e2e/test_webhook_capture_egress_gate_e2e.py``.
requires_in_network = pytest.mark.skipif(
    not e2e_in_network(),
    reason=f"in-network only: {SELLER_ETLD1_HOST} is a compose alias, not resolvable from the host path "
    "(set ADCP_TEST_HOST)",
)


@pytest.fixture
def trust_root_tenant(live_server):
    """A tenant answering at the walkable seller origin, plus one key.

    Two independent reasons it is not the plaintext stack, and either one alone
    would be enough:

    * a plaintext host publishes no trust root at all — ``origin_is_publishable``
      refuses an ``http://`` origin, so ``emitted_identity`` emits no
      ``identity.brand_json_url`` for it and there is nothing to walk;
    * the walk binds the agent url to that pointer by eTLD+1, which rules out
      ``.test`` and ``localhost`` alike (:data:`SELLER_ETLD1_HOST`).
    """
    with provisioned_trust_root_tenant(
        live_server,
        tenant_id=_TENANT_ID,
        slug=_SLUG,
        host=netloc(seller_etld1_base_url(live_server)),
    ) as provisioned:
        yield provisioned


@pytest.fixture
def tls_trust_root_tenant(live_server):
    """Provision the TLS sibling at a caller-supplied netloc; drop it afterwards.

    A factory rather than a plain fixture so the assertions about *where* the TLS
    listener is — which are the point of the test — happen in the test body and
    fail as failures, not as fixture-setup errors. The tenant seam itself is the
    same context manager the plaintext fixture uses; nothing is duplicated.
    """
    stack = contextlib.ExitStack()

    def provision(host: str):
        return stack.enter_context(
            provisioned_trust_root_tenant(
                live_server,
                tenant_id=_TLS_TENANT_ID,
                slug=_TLS_SLUG,
                host=host,
            )
        )

    with stack:
        yield provision


@requires_in_network
@pytest.mark.asyncio
async def test_counterparty_resolves_our_jwks_through_our_published_brand_json(
    docker_services_e2e, live_server, trust_root_tenant
):
    """The full chain: agent URL -> capabilities identity -> brand.json -> JWKS.

    Asserts, in the order a verifier would hit them:

    * the URL we PUBLISH is the URL we are REACHABLE at;
    * every hop the SDK walked succeeded (no ``trace`` entry with status "error");
    * the brand.json entry the resolver matched is the one whose ``url``
      byte-equals the agent card's A2A interface URL — security.mdx step 5, with
      no canonicalization;
    * the ``jwks_uri`` the LIVE document advertised equals ``jwks_uri(tenant)``,
      and its origin equals the ``key_origins`` value we seeded from
      ``jwks_origin(tenant)`` — the consistency check the inbound verifier
      performs, and the reason ``_resolve_signing_blocks`` imports this origin
      rather than re-literalling it;
    * the key that comes back is the key we stored.
    """
    from adcp.signing import async_resolve_agent

    from src.core.agent_identity import brand_json_url, canonical_agent_url, jwks_origin, jwks_uri

    # The identity canonicalizer, aliased because ``canonical_agent_url`` above is a
    # DIFFERENT function of the same name (tenant -> published origin). #1721 deleted
    # ``src.core.validation.normalize_agent_url``, which this assertion used to call:
    # its extra ``/mcp`` / ``/a2a`` suffix-stripping decided an AUTHORIZATION outcome
    # nothing asked for. Its one surviving job — canonicalize an agent_url for identity
    # comparison, through ``src.core.signing.canonical`` — is this function
    # (``tests/unit/test_guards_normalize_agent_url_delegates.py`` pins the delegation).
    from src.core.schemas._base import canonical_agent_url as canonical_identity_url

    tenant, key = trust_root_tenant
    base_url = seller_etld1_base_url(live_server)

    # 1. The URL we publish must be the URL we are reachable at. If this fails, the
    #    scheme derivation in domain_config does not handle the host the stack is
    #    actually served on (e.g. an in-network service name rather than localhost),
    #    and every document below would advertise an unreachable origin.
    assert canonical_agent_url(tenant) == base_url, (
        f"canonical_agent_url must equal the live stack base URL; published "
        f"{canonical_agent_url(tenant)!r}, reachable at {base_url!r}"
    )

    # The pointer the walk below starts from is the one the pin obliges, and on this
    # origin production really does emit it. Asserted here rather than left implicit
    # because the seed two blocks down is only faithful while this holds — a seed
    # carrying a scheme production suppresses is a document no counterparty can ever
    # receive, and that is the state this test was in before.
    assert brand_json_url(tenant).startswith("https://"), (
        f"identity.brand_json_url is pinned ^https:// and origin_is_publishable gates the "
        f"emission on it, so a walk can only start from an https pointer; derived "
        f"{brand_json_url(tenant)!r} for a tenant at {netloc(base_url)!r}"
    )

    async with httpx.AsyncClient(base_url=base_url, verify=ca_verified_ssl_context(), timeout=15.0) as client:
        card = (await client.get("/.well-known/agent-card.json")).json()
        brand = (await client.get("/.well-known/brand.json")).json()
        adagents = (await client.get("/.well-known/adagents.json")).json()

    agent_url = card["supportedInterfaces"][0]["url"]
    matching = [entry for entry in brand["agents"] if entry["url"] == agent_url]
    assert len(matching) == 1, (
        "brand.json must contain exactly one agents[] entry whose url BYTE-EQUALS the agent card's "
        f"A2A interface URL {agent_url!r} — that is the match security.mdx step 5 performs, with no "
        f"canonicalization; got {[entry['url'] for entry in brand['agents']]}"
    )
    entry_id = matching[0]["id"]

    # 2. Hop 1 seeded from A3's OWN helpers. The SERVED document carries identity for
    #    real now; grading that one is ``test_jwks_publication_e2e``, not this module.
    seeded_identity = {
        "brand_json_url": brand_json_url(tenant),
        "key_origins": {"request_signing": jwks_origin(tenant)},
    }
    capabilities_body = {"adcp_version": "3.1.1", "identity": seeded_identity}

    resolution = await async_resolve_agent(
        agent_url,
        agent_type="sales",
        agent_id=entry_id,
        allow_private_destinations=True,
        _capabilities_client_factory=seeded_capabilities_factory(capabilities_body),
    )

    failed = [hop for hop in resolution.trace if hop.status == "error"]
    assert failed == [], f"every discovery hop must succeed; failed hops: {failed}"

    assert resolution.brand_json_url == brand_json_url(tenant), (
        "the brand.json URL the resolver walked must be the one A3's helper emits — this is the "
        f"string ``_resolve_signing_blocks`` publishes as identity.brand_json_url; got "
        f"{resolution.brand_json_url!r}"
    )
    assert resolution.agent_entry["url"] == agent_url, (
        f"the matched entry's url must byte-equal the agent URL the call was made to; got "
        f"{resolution.agent_entry['url']!r} vs {agent_url!r}"
    )

    # 3. The jwks_uri comes from the SERVED document, not from the seed — so this is
    #    the assertion that proves the published document and the local helper agree.
    assert resolution.jwks_uri == jwks_uri(tenant), (
        f"the live brand.json advertised {resolution.jwks_uri!r}; A3's helper says "
        f"{jwks_uri(tenant)!r}. These are the same string or key discovery is a coin flip."
    )
    assert origin(resolution.jwks_uri) == seeded_identity["key_origins"]["request_signing"], (
        "the declared request_signing key origin must equal the origin the JWKS actually resolved "
        f"at — otherwise a verifier raises request_signature_key_origin_mismatch; declared "
        f"{seeded_identity['key_origins']['request_signing']!r}, resolved {origin(resolution.jwks_uri)!r}"
    )

    assert published_kids(resolution.jwks["keys"]) == {key.kid}, (
        f"the resolved JWKS must carry exactly this tenant's stored key; got "
        f"{sorted(jwk['kid'] for jwk in resolution.jwks['keys'])}, stored {key.kid!r}"
    )

    # 4. adagents' url obeys the OTHER comparison rule: canonicalize both sides
    #    (core/authorized-agent-base.json), never byte-equality. A canonical-by-
    #    construction producer satisfies both rules at once, which is the point.
    #
    #    The comparison is against the ORIGIN, not against ``agent_url``. ``build_adagents_json``
    #    emits ``canonical_agent_url(tenant)`` — the anchor — while the brand.json entry and the
    #    agent card carry the ENDPOINT (origin + ``/a2a``). Those two were only ever equal under
    #    the deleted normalizer's ``/a2a`` stripping; the canonicalizer that replaced it preserves
    #    the path, and comparing them now would be asserting that an endpoint IS its own origin.
    #    So both facts are graded separately: the adagents anchor canonicalizes to our published
    #    origin, and the brand.json endpoint sits on that same origin — which together are what
    #    "names the same agent" means once the path survives canonicalization.
    published_origin = canonical_agent_url(tenant)
    adagents_urls = {entry["url"] for entry in adagents["authorized_agents"]}
    assert adagents_urls, "the seeded authorized-property record must yield an authorized_agents entry"
    assert all(canonical_identity_url(url) == canonical_identity_url(published_origin) for url in adagents_urls), (
        "every adagents authorized_agents[].url must canonicalize to this tenant's published origin — "
        "authorized-agent-base mandates canonicalize-both-sides, never byte-equality; got "
        f"{sorted(adagents_urls)} vs {published_origin!r}"
    )
    assert origin(agent_url) == published_origin, (
        "the brand.json entry the resolver matched must live on the SAME origin the adagents anchor "
        f"names, or the two documents authorize different agents; entry {agent_url!r} sits on "
        f"{origin(agent_url)!r}, adagents anchors {published_origin!r}"
    )


@pytest.mark.asyncio
async def test_hop_one_of_discovery_resolves_over_real_tls(docker_services_e2e, live_server, tls_trust_root_tenant):
    """canonical_agent_url -> brand.json -> jwks_uri -> JWKS, all over a real handshake.

    The sibling above walks the same chain over plaintext and stays exactly as it
    is; this one is additive. What it adds is the ONE thing plaintext cannot
    grade: that a host for which ``_get_protocol_for_domain`` answers ``https``
    can actually complete a TLS handshake and serve its own JWKS at that exact
    ``host:port``. A test host earns https by serving it, never by being told to.

    Why the last leg is a real fetch and not a string check: a tenant whose
    ``virtual_host`` merely happens to contain a dot ALREADY derives an https URL
    today. Asserting ``startswith("https://")`` on such a tenant is satisfied by
    a host that serves nothing — the vacuity this whole ticket exists to remove.
    So the scheme assertion is only the first line; the JWKS coming back over the
    CA-verified connection is the assertion that has content.

    Scope: this grades hop 1 being SERVABLE, starting from a HARDCODED path built
    from a ``src`` helper, against a key the TEST PROCESS minted. Grading the same
    chain from the SERVED capabilities document, against a key a PRODUCTION path
    minted inside the container, is ``test_jwks_publication_e2e``
    (salesagent-mp53.7).
    """
    from src.core.agent_identity import canonical_agent_url, jwks_uri

    # The well-known paths are LITERALS here, deliberately, rather than the path
    # constants src.core.agent_identity exports. They are fixed by the pinned spec
    # — /.well-known/adagents.json, /.well-known/brand.json, and
    # /.well-known/jwks.json as the normative default for a jwks_uri
    # (AdCP 3.1.1 building/by-layer/L1/security.mdx:111, :630, :644) — so the path
    # IS the contract under test. Importing the production constant would make
    # these assertions move in lockstep with production: change the constant and
    # the test follows it off-spec silently, which is precisely the regression they
    # exist to catch. Constants are right for CONSTRUCTING our own URLs
    # (canonical_agent_url, jwks_uri, imported above); literals are right for
    # asserting the spec-mandated location (salesagent-z6nr.36).
    brand_json_path = "/.well-known/brand.json"
    jwks_path = "/.well-known/jwks.json"

    published_tls_base_url = tls_base_url(live_server)
    verify = ca_verified_ssl_context()

    # The netloc INCLUDES the port: get_tenant_by_virtual_host (config_loader.py:252)
    # matches the Host header as an exact string, and httpx only omits the port
    # when it is the scheme default.
    tenant, key = tls_trust_root_tenant(netloc(published_tls_base_url))

    # 1. The scheme we publish is derived from the stored host by the unchanged
    #    production predicate — nothing here forces it.
    published = canonical_agent_url(tenant)
    assert published.startswith("https://"), (
        f"a tenant whose virtual_host is the TLS netloc must publish an https origin; got {published!r}. "
        f"If this fails, _get_protocol_for_domain was weakened or the TLS host is not dotted"
    )
    assert published == published_tls_base_url, (
        f"the URL we publish must be the URL we are reachable at; published {published!r}, "
        f"TLS listener at {published_tls_base_url!r}"
    )

    # 2. Hop 1, for real: brand.json fetched over TLS, verified against the CA the
    #    stack's leaf was signed with. A plaintext listener at this address fails
    #    the handshake here — which is exactly what makes the scheme assertion
    #    above non-vacuous.
    async with httpx.AsyncClient(base_url=published, verify=verify, timeout=15.0) as client:
        brand_response = await client.get(brand_json_path)
        assert brand_response.status_code == 200, (
            f"brand.json must be served over TLS at {published + brand_json_path!r}; "
            f"got HTTP {brand_response.status_code}"
        )
        brand = brand_response.json()

        # 3. Follow the jwks_uri the SERVED document advertises — not one we built.
        advertised = {entry["jwks_uri"] for entry in brand["agents"]}
        assert advertised == {jwks_uri(tenant)}, (
            f"every agents[] entry must advertise this tenant's jwks_uri; served {sorted(advertised)}, "
            f"helper says {jwks_uri(tenant)!r}"
        )
        advertised_jwks_uri = advertised.pop()
        assert advertised_jwks_uri == published + jwks_path, (
            f"the advertised JWKS must live on the origin we published, or a verifier raises "
            f"request_signature_key_origin_mismatch; got {advertised_jwks_uri!r} vs origin {published!r}"
        )

        jwks_response = await client.get(advertised_jwks_uri)
        assert jwks_response.status_code == 200, (
            f"the advertised JWKS must resolve over TLS at {advertised_jwks_uri!r}; "
            f"got HTTP {jwks_response.status_code}"
        )
        jwks = jwks_response.json()

    # 4. The key that comes back over TLS is the key this tenant was provisioned
    #    with — so the chain resolved OUR trust root, not some other tenant's.
    assert published_kids(jwks["keys"]) == {key.kid}, (
        f"the JWKS served over TLS must carry exactly this tenant's stored key; got "
        f"{sorted(jwk['kid'] for jwk in jwks['keys'])}, provisioned {key.kid!r}"
    )
