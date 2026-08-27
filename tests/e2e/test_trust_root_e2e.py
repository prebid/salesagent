"""E2E: a counterparty resolves our signing keys from our published trust root.

TDD RED for #1291 A3 (salesagent-z6nr.9), step 9 as amended by R-H2.

This walks the SDK's own three-hop discovery (``adcp.signing.async_resolve_agent``)
against the live Docker stack, and is the only test in the A3 suite that grades
the documents as a real counterparty consumes them rather than as we build them.

Two deliberate seams, each with its reason:

1. **Hop 1 is seeded** (``_capabilities_client_factory``). Two independent reasons,
   and either alone would be sufficient:

   * *SDK divergence #4.* ``_fetch_capabilities`` does a raw ``GET <agent_url>``
     and requires a JSON object body, while security.mdx:1142 says the opposite
     verbatim — "invoke ``get_adcp_capabilities`` via the agent's declared
     transport (MCP ``tools/call`` or A2A skill invocation), **not** a raw HTTP
     ``GET``". Our ``/mcp`` answers GET with a redirect to an SSE stream and
     ``/a2a`` is JSON-RPC POST, so an unseeded hop 1 fails no matter what A3
     publishes.
   * *This module grades A3, whose scope stops at the published documents.* Seeding
     hop 1 from A3's own helpers is what keeps a failure here attributable to
     brand.json or the JWKS rather than to the capabilities builder.

   ``identity`` is no longer gated: it is DERIVED platform state today
   (``_DERIVED_BLOCKS``, ``src/core/signing/posture.py``) and ``emitted_identity``
   puts it on the wire, so the served capabilities document really does carry it.
   Grading THAT document — fetched, not seeded — is
   ``test_jwks_publication_e2e`` (salesagent-mp53.7), not this module.

   So the seeded body's ``identity`` is built from **A3's own** helpers, and the
   test asserts the values the resolver ends up using are those helpers' output.

2. **Hops 2 and 3 are live.** No ``_brand_jwks_client_factory`` is passed —
   brand.json and the JWKS are fetched from the running stack over real HTTP,
   because those two hops are the ones A3 owns.

``allow_private_destinations=True`` is a TEST argument only: it relaxes the SDK's
SSRF pin so an ``http://localhost:<port>`` stack is reachable. Nothing in A3's
production path reads it (the real gate belongs to B1, salesagent-z6nr.12, where
the verifier actually runs).
"""

from __future__ import annotations

import contextlib
import ssl

import httpx
import pytest

from tests.e2e._signing_e2e import (
    ca_bundle,
    netloc,
    origin,
    provisioned_trust_root_tenant,
    seeded_capabilities_factory,
    tls_base_url,
)
from tests.helpers.signing import published_kids

_SLUG = "trustroot_e2e"
_TENANT_ID = "tr_e2e"

# The TLS sibling gets its OWN tenant id and slug. Both fixtures delete by tenant
# id at setup AND teardown, and ``virtual_host`` is host-routing state shared by
# the whole e2e session — a shared id would have the two tests stomping each
# other's routing.
_TLS_SLUG = "trustroot_e2e_tls"
_TLS_TENANT_ID = "tr_e2e_tls"


@pytest.fixture
def trust_root_tenant(live_server):
    """A tenant whose canonical agent URL IS the live PLAINTEXT stack, plus one key."""
    with provisioned_trust_root_tenant(
        live_server,
        tenant_id=_TENANT_ID,
        slug=_SLUG,
        host=netloc(live_server["mcp"]),
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
      ``jwks_origin(tenant)`` — the consistency check B1's verifier performs, and
      the reason D1 must import this origin rather than re-literal it;
    * the key that comes back is the key we stored.
    """
    from adcp.signing import async_resolve_agent

    from src.core.agent_identity import brand_json_url, canonical_agent_url, jwks_origin, jwks_uri
    from src.core.validation import normalize_agent_url

    tenant, key = trust_root_tenant
    base_url = live_server["mcp"]

    # 1. The URL we publish must be the URL we are reachable at. If this fails, the
    #    scheme derivation in domain_config does not handle the host the stack is
    #    actually served on (e.g. an in-network service name rather than localhost),
    #    and every document below would advertise an unreachable origin.
    assert canonical_agent_url(tenant) == base_url, (
        f"canonical_agent_url must equal the live stack base URL; published "
        f"{canonical_agent_url(tenant)!r}, reachable at {base_url!r}"
    )

    async with httpx.AsyncClient(base_url=base_url, timeout=15.0) as client:
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

    # 2. Hop 1 seeded from A3's OWN helpers (identity is D1's to emit for real).
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
        f"string D1 will publish as identity.brand_json_url; got {resolution.brand_json_url!r}"
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
    adagents_urls = {entry["url"] for entry in adagents["authorized_agents"]}
    assert adagents_urls, "the seeded authorized-property record must yield an authorized_agents entry"
    assert all(normalize_agent_url(url) == normalize_agent_url(agent_url) for url in adagents_urls), (
        "every adagents authorized_agents[].url must canonicalize to the same agent as the brand.json "
        f"entry; got {sorted(adagents_urls)} vs {agent_url!r}"
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
    verify = ssl.create_default_context(cafile=ca_bundle())

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
