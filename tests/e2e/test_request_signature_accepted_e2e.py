"""E2E-2b (the accepted leg): a request signed by a counterparty the server resolved
by WALKING its published trust root is accepted, over real HTTP — and the acceptance
is attributed to the checklist-pass branch by a positive wire observable.

Sibling of ``test_request_signature_required_e2e.py``, which grades the refused leg
on the same tenant seam. Read them as one pair: that module proves an unsigned
request to a ``required_for`` operation is refused; this one proves a correctly
signed one is ACCEPTED, and — the part that makes it worth having — proves the
acceptance came from the verifier rather than from the verifier never running.

**Where the verifier lives now.** #1721 deleted the fourth ASGI middleware this
module was first written against (``src/core/signing/request_verifier_middleware.py``
— gone, along with its own tenant lookup and its own bodyless 401). Inbound
verification is a call the request boundary's ONE resolver makes:
``src.core.resolved_identity._resolve_identity`` step 4b ->
``src.core.signing.verifier.verify_inbound_signature``, with the tenant, the caller
and the operation already resolved. The refusal is a typed
``AdCPRequestSignatureError`` carrying the taxonomy code, and
``src.core.auth_middleware.AuthChallengeResponder`` is what turns it into 401 +
``WWW-Authenticate: Signature error="<code>"``. Every wire fact this module asserts
is unchanged by that move; only the citations below are.

**The whole point, stated first.** On the wire a checklist PASS is byte-identical to
a pass-through. Five different routes produce the same 2xx without the checklist
passing:

1. ``config.verifier_enabled == False`` — ``verify_inbound_signature`` returns at
   ``verifier.py`` :225 before anything is read;
2. ``bucket == "none"`` — a SIGNED request passes through without a CHECKLIST,
   recorded as ``record_request_unsigned(op, "ignored")``. Only the
   ``supported: false`` half is fully UNVERIFIED (``_verify_signed`` :333-335); the
   ``supported: true`` half runs ``_strict_header_precheck`` first and a malformed
   header REJECTS there (``_handle_rejection`` :395), so that route yields 2xx only
   for a well-formed signature;
3. ``bucket == "warn"`` — ``_handle_rejection`` logs and continues, so even an
   INVALID signature yields 2xx;
4. the composition rule (``_refuse_unsigned_if_required`` :277-311, security.mdx @
   v3.1.1 :1268-1271) ALLOWS an authenticated-but-unsigned request, so a bearer-authed
   2xx proves nothing on its own — and ``get_adcp_capabilities`` is a PUBLIC registry
   row (``ToolSpec.requires_credential()`` is False for it), so an unsigned request
   with no bearer at all is served 200 anonymously unless the verifier refuses it;
5. NEW on this architecture: ``signature_subject is None``. The REST handler passes
   ``captured_exchange(request.scope)`` into ``serve``, and a scope the
   ``SignedExchangeCapture`` middleware did not capture — a path
   ``src.core.signing.capture.is_adcp_surface`` does not claim, a middleware dropped
   from ``src/app.py``'s stack — makes ``_resolve_identity`` read the request as
   "presented no signature" and verify nothing.

So a bare ``assert response.status_code == 200`` here would be exactly the disease
this module exists to kill. Each route is closed by a control that differs from the
accepted request in ONE bit and is asserted on the wire, and the acceptance itself is
graded by a POSITIVE observable rather than by the absence of a rejection.

**PROVES.**

* ``adcp_request_signature_verified_total{operation,keyid}`` increments by EXACTLY 1
  across the accepted request. ``record_signature_verified`` has exactly one call site
  in ``src/`` (``src/core/signing/verifier.py`` :391), reached only after the SDK
  checklist — Tier 3 brand authorization included, since ``_run_verifier`` passes
  ``agent_url`` and the walked ``resolution`` into ``verify_request_signature`` —
  RETURNS. So the delta is a direct read of the one line that executes only on the
  checklist-pass branch. ``src/app.py`` mounts the Flask admin (whose ``/metrics``
  route carries no ``@require_auth``) in the SAME process as the resolver, and
  ``get_metrics_text()`` reads the process-global registry, so the scrape is
  same-process, not an inference. This is PRIMARY, not decoration: control (i) alone
  passes even when the request is passing through unverified, and only the delta
  catches that.
* The three controls, each one bit away from the accepted request, on the same
  tenant / route / declaration / counterparty:
  (i)   unsigned, no bearer      -> 401 ``request_signature_required``
        [closes ``verifier_enabled=False``, ``bucket == "none"``, and the
        uncaptured-exchange route: under any of the three this request is a public
        discovery call that is SERVED 200 anonymously, so ``rejection_code()``
        returns None]
  (ii)  signed but TAMPERED — one byte flipped INSIDE the ``Signature`` value, with
        ``Signature-Input`` left well-formed so it is not a step-1 header rejection
        -> 401 ``request_signature_invalid``
        [closes ``bucket == "warn"``, and proves the signature BYTES were graded
        rather than the mere presence of the headers]
  (iii) signed correctly         -> 2xx + the metric delta
  (iv)  signed correctly, by a counterparty whose capabilities point at a brand.json
        that does NOT list it -> 401 ``request_signature_agent_not_in_brand_json``
        [this is what makes "resolvable via the PUBLISHED TRUST ROOT" — the ticket's
        own title — a wire fact, and it is the ONLY control here that proves the
        brand.json hop RAN. Traced, not assumed: ``resolve_agent`` hop 2
        (``adcp/signing/agent_resolver.py``) reads the agent's ``jwks_uri`` out of
        the brand.json ``agents[]`` entry, so an unlisted agent fails there with
        ``BrandJsonResolverError("agent_not_found")``, which
        ``_map_brand_json_resolver_error`` (``verifier.py`` :601) maps to exactly this
        code and ``_FailedDiscoveryJwksResolver`` (:174) raises at checklist step 7.
        Tier 3 is the SECOND consumer of the same document and is simply not reached
        on this input — the walk fails first. The registry branch cannot produce this
        code at all: with no ``agent_url`` there is no walk, and an unmatched keyid
        yields the generic ``request_signature_key_unknown``. So a regression that
        resolved this key from config instead of from the published trust root —
        which would otherwise yield a byte-identical 2xx AND an identical metric
        increment — is visible here and nowhere else in this module.]
  Every 401 is read with ``rejection_code()`` (``tests/helpers/signing.py``), which
  reads ``WWW-Authenticate: Signature error="<code>"`` — the only wire signal that
  distinguishes a verifier rejection from a route-level 401. Never hand-rolled.

**DOES NOT PROVE.**

* The REGISTRY key-trust source (``settings.signing.counterparty_registry``). It is
  consulted only when ``agent_url`` is falsy (``_resolution_for`` :511-545), i.e.
  exactly when no trust root was walked, and that branch reaches no Tier 3 — so it
  cannot satisfy this module's title and is not graded here.
* The MCP and A2A naming surfaces for the accepted leg. The refused-leg sibling
  grades MCP; the accepted leg's novel evidence is the checklist-pass branch, which
  is transport-independent once the operation is named — all three transports reach
  the same ``_resolve_identity`` through ``src.core.tools._boundary.invoke_tool``.
* ``create_media_buy`` specifically. The headline request is deliberately
  ``POST /api/v1/capabilities``, which names ``get_adcp_capabilities`` — already in
  ``LADDER_OPERATIONS`` and therefore already in this tenant's declaration. Every
  field of ``GetAdcpCapabilitiesRequest`` is optional, so the ``{}`` body below
  cannot 4xx on schema validation and the leg turns on the signing outcome alone;
  a ``{}`` POST to ``/api/v1/media-buys`` would 4xx before the signing outcome is
  observable.

**Why POST, not GET.** On #1721 the REST surface is DERIVED from
``src.core.tools.registry``: one row, one verb, one path. ``get_adcp_capabilities``
declares ``RestBinding("POST", "/capabilities")`` and nothing registers a GET, so the
bodyless GET this module was first written against now 405s in FastAPI's router —
before ``serve``, before ``_resolve_identity``, before any verification. A 405 carries
no ``WWW-Authenticate``, so every control here would read ``None`` and fail for a
routing reason having nothing to do with signing. The four legs POST the same
:data:`_REQUEST_BODY` bytes, and each signature covers those bytes through
``content-digest`` exactly as ``signed_probe`` does in-process.

**Nothing in the live server is patched, relaxed or bypassed.** No
``allow_private_destinations``, no ``_capabilities_client_factory`` seam in
production, no monkeypatching across the container boundary (``verifier_spy`` and
``counterparty_key`` are TEST-PROCESS only and are unusable here by construction).
The counterparty is a real HTTPS origin on the stack's non-private per-stack subnet,
which the SDK's ``resolve_and_validate_host`` accepts on its own IP arithmetic with
its pinned ``allow_private_destinations=False`` untouched;
``tests/unit/test_architecture_no_private_destinations.py`` stays untouched. The one
piece of trust configuration added is ``SSL_CERT_FILE`` on ``adcp-server``, pointing
at a CONCATENATED bundle so the server can validate the stack CA's leaf — the
receiver-side twin of the runner's ``E2E_CA_BUNDLE``, with ``check_hostname`` and
verification fully ON. Its wiring is guarded in-process by
``tests/unit/test_architecture_e2e_counterparty_origin_wiring.py``.

**Per-leg key ids are a PRECONDITION, not a detail.** ``sign_wire_request`` /
``signed_headers`` default to the shared module constant ``COUNTERPARTY_KID``, and the
server's metric registry is cumulative for the whole e2e session. With a shared kid, a
before/after window spanning two legs reads 2 and a window around either can be
satisfied by the other. Each leg below passes an explicit distinct ``key_id=``, and the
label match filters on it — that uniqueness is what makes "exactly 1" a real claim.

**Two traps in ``signed_headers``, both closed below.**

1. It signs ``f"{origin}{path}"``. ``src.core.signing.capture._target_uri`` (:226-249)
   rebuilds the authority from the ``Host`` header nginx forwards verbatim and the
   scheme from ``X-Forwarded-Proto``, and ``_run_verifier`` passes that reconstructed
   ``exchange.url`` to the SDK rather than letting ``verify_starlette_request`` derive
   its own. So the signed origin must be the real TLS netloc INCLUDING the port. Hence
   the ``origin=`` argument.
2. ``request_headers`` unconditionally injects ``x-adcp-tenant: "sig_tenant"``, an
   in-process tenant id that does not exist in the e2e database. ``_detect_tenant``
   (``src/core/resolved_identity.py`` :230) resolves the ``Host`` header FIRST and only
   falls back to that hint, so this tenant does resolve either way here — but a header
   asserting a DIFFERENT tenant than the one being addressed is a lie inside the signed
   byte range, and one reordering of that ladder away from loading the wrong tenant,
   reading an empty posture, collapsing the bucket to ``none`` and taking the
   :333-335 UNVERIFIED pass-through: a green 2xx with zero verification, this module's
   own disease. Overridden with ``extra={"x-adcp-tenant": _TENANT_ID}``
   (``headers.update(extra)`` wins the merge).

**Host-routing collision.** ``ix_tenants_virtual_host`` is UNIQUE and every signing
e2e module provisions at the SAME TLS netloc. A distinct ``_TENANT_ID``/``_SLUG``
(colliding with none of ``tr_e2e``, ``tr_e2e_tls``, ``jwkspub_e2e``, ``dn4i_e2e``,
``whsig_e2e``, ``reqsig_e2e``) only makes the blame land here; what PREVENTS the clash
is the serial e2e run (``tox -e e2e``, no ``-n``) plus ``drop_tenant`` at both ends.

**Running it.** ``saci run e2e`` — the WHOLE e2e suite. NOT ``saci run ci <file>``
(bypasses tox and resolves the wrong database), and NOT ``saci run e2e -- -k <name>``
(the argument contract silently discards everything after the suite name). There is
no supported way to target a single e2e test while keeping the correct database.

**Mutation verification — OWED BY THE IMPLEMENTATION ATOM, not yet performed.**
This module was written as the TDD-red artifact before the counterparty origin, the
compose wiring and ``SSL_CERT_FILE`` existed, so no control here has yet been shown to
go red when the thing it grades is broken. Four mutations must be run by hand and their
observed results recorded HERE, replacing this paragraph, before the leg is called
green:

1. revert the ``x-adcp-tenant`` override on the accepted leg -> the metric delta must
   go to 0 WHILE the three controls still pass. That is the proof the metric is
   load-bearing rather than redundant with them;
2. point leg (iv) at ``brand.json`` instead of ``brand-unlisted.json`` -> it must stop
   401ing;
3. un-tamper leg (ii) -> it must stop 401ing;
4. drop ``SSL_CERT_FILE`` from ``adcp-server`` -> leg (iii) must fail as an
   unresolvable counterparty, never pass silently.
"""

from __future__ import annotations

from typing import Final

import httpx
import pytest

from tests.e2e._signing_e2e import (
    ca_verified_ssl_context,
    netloc,
    origin,
    provisioned_trust_root_tenant,
    signing_declarations,
    tls_base_url,
)
from tests.helpers.signing import (
    CAPABILITIES_ADCP_PATH,
    LADDER_OPERATIONS,
    VERIFIED_METRIC,
    keypair_for,
    rejection_code,
    scraped_counter_samples,
    scraped_metrics_text_async,
    signed_headers,
)

#: Distinct from every other signing e2e module's tenant/slug (see the
#: host-routing-collision note in the module docstring).
_SLUG = "acptsig_e2e"
_TENANT_ID = "acptsig_e2e"

#: ``/api/v1/capabilities``. The shared constant's name predates #1721's
#: registry-derived REST table, which registers ONE verb per row — POST for this one —
#: so the surface is no longer reachable without a body. The path is what this module
#: needs from it; the verb and the bytes are declared here (see "Why POST, not GET").
_ADCP_PATH: Final[str] = CAPABILITIES_ADCP_PATH

#: The wire bytes every leg sends and every signature covers through ``content-digest``.
#: Every ``GetAdcpCapabilitiesRequest`` field is optional, so this validates — which is
#: what keeps each leg's outcome attributable to the signing decision and not to schema
#: validation happening ahead of it.
_REQUEST_BODY: Final[bytes] = b"{}"

#: The operation the accepted leg names. ``POST /api/v1/capabilities`` resolves to it
#: off the registry-derived REST route table, and it is already in
#: :data:`LADDER_OPERATIONS` and therefore already in the declaration below.
_OPERATION = "get_adcp_capabilities"

#: The counterparty's origin, behind the SHARED tls-proxy front by SNI. Port 8443 is
#: the front's COMPOSE-INTERNAL listener — the port the SERVER dials on its outbound
#: walk — and is unrelated to whatever host port the stack publishes. The hostname sits
#: under the existing ``*.adcp.test`` wildcard SAN, so no certificate changes; ``.test``
#: is safe on THIS path because the inbound verifier's walk is gated by the SDK's
#: ``resolve_and_validate_host`` (IP arithmetic only) and ``src/core/signing/`` never
#: calls ``is_reserved_tld_host``.
_COUNTERPARTY_ORIGIN = "https://counterparty.adcp-e2e.dev:8443"

#: Two agent URLs on that one origin, differing ONLY in which brand.json their
#: capabilities document points at. ``AGENT_RESOLUTION_CACHE`` is keyed on the agent
#: url, so the listed and unlisted counterparties cannot collide in the server's cache.
_LISTED_AGENT_URL = f"{_COUNTERPARTY_ORIGIN}/agent/listed"
_UNLISTED_AGENT_URL = f"{_COUNTERPARTY_ORIGIN}/agent/unlisted"

#: The counterparty's test control plane: the JWKS it serves is INSTALLED by this test
#: rather than baked in, so the keys are minted fresh per run by ``keypair_for()`` and
#: no private material is committed. Fixture setup, not a seam in anything under test —
#: the origin is scenery, exactly like a seeded tenant row.
_JWKS_CONTROL_PATH = "/_control/jwks"

#: What the counterparty publishes as its trust root, and the document leg (iv) turns on.
_BRAND_JSON_PATH = "/.well-known/brand.json"

#: The bearer tokens, and the ``agent_url`` each principal carries. ``Principal.agent_url``
#: is the ONLY legitimate source for a counterparty's identity (security.mdx forbids
#: taking it from a header or a body field) and is the sole input to the key-resolution
#: walk ``_resolve_identity`` -> ``verify_inbound_signature`` -> ``_verify_signed`` ->
#: ``_resolution_for`` makes, so the two counterparties are two principals.
_LISTED_TOKEN = "acptsig-e2e-listed-token"
_UNLISTED_TOKEN = "acptsig-e2e-unlisted-token"

#: One kid PER LEG. See the precondition paragraph in the module docstring: a shared kid
#: makes "incremented by exactly 1" satisfiable by a different leg's increment.
_ACCEPTED_KID = "acptsig-e2e-accepted-1"
_TAMPERED_KID = "acptsig-e2e-tampered-1"
_UNLISTED_KID = "acptsig-e2e-unlisted-1"


def _tamper(headers: dict[str, str]) -> dict[str, str]:
    """Flip one byte INSIDE the ``Signature`` value, leaving every other header intact.

    Deliberately NOT a malformed header: ``Signature-Input`` stays well-formed and the
    mutated value stays valid base64 inside its ``:...:`` delimiters, so the request
    survives ``_strict_header_precheck`` (checklist step 1) and is refused by the SDK on
    the signature BYTES. A step-1 header rejection would be satisfied by a verifier that
    never ran the checklist, which is the opposite of what this control is for — hence
    the assertion on ``request_signature_invalid`` specifically, not on any 401.
    """
    value = headers["Signature"]
    start = value.index(":") + 1
    original = value[start]
    return {**headers, "Signature": value[:start] + ("B" if original == "A" else "A") + value[start + 1 :]}


def _verified_count(metrics_text: str) -> float:
    """The accepted leg's own ``{operation, keyid}`` series total in one scrape.

    Filtered on the per-leg kid, which is what makes the before/after difference a
    claim about THIS request rather than about the server's cumulative session.
    """
    samples = scraped_counter_samples(metrics_text, VERIFIED_METRIC, operation=_OPERATION, keyid=_ACCEPTED_KID)
    return sum(samples.values())


async def _scrape_metrics(client: httpx.AsyncClient, when: str) -> str:
    """GET ``/metrics`` through the TLS front, failing LOUDLY on anything but a scrape.

    The non-404 assertion is the point: ``/metrics`` was only ever measured under a
    Starlette ``TestClient`` — no nginx, no ``Host`` matching a tenant ``virtual_host``
    — so the route this test actually takes is not proven until it runs. A routing
    surprise must fail here and say so, rather than yielding two empty scrapes and a
    vacuous 0-vs-0 delta that reads as "the mechanism did not run".

    The guards themselves live in :func:`tests.helpers.signing.assert_metrics_scrape`,
    which this shares with the runner-side scrape. Only the FETCH differs, because only
    this caller needs the TLS front.
    """
    return await scraped_metrics_text_async(client, when=when)


async def _counterparty_get(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> httpx.Response:
    """Reach the counterparty origin, turning a transport failure into a WIRING failure.

    Left bare, an unwired stack surfaces as ``httpx.ConnectError: Name or service not
    known`` from inside the client — a message that names neither the compose alias nor
    the SNI map nor the service, and reads like flakiness. The counterparty is scenery,
    not the thing under test, so a stack that cannot serve it must say exactly which of
    the four wiring sites is missing. Raised, never skipped: degrading this to a skip
    would report an unbuilt accepted leg as success, which is the whole failure mode
    this module exists to close.
    """
    try:
        return await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise AssertionError(
            f"{method} {_COUNTERPARTY_ORIGIN}{path} did not reach a counterparty origin ({exc!r}). "
            "The accepted leg needs FOUR wiring sites, all guarded in-process by "
            "tests/unit/test_architecture_e2e_counterparty_origin_wiring.py: a counterparty-origin "
            "compose service running tests.e2e.counterparty_origin_service, the "
            f"{_COUNTERPARTY_ORIGIN.split('//')[1].split(':')[0]} alias on the shared tls-proxy, "
            "the nginx $ssl_server_name map row routing it there, and SAN coverage for the name."
        ) from exc


@pytest.mark.asyncio
@pytest.mark.requires_db
async def test_a_signed_request_from_a_walked_counterparty_is_accepted_and_counted(docker_services_e2e, live_server):
    """One tenant, one route, one counterparty; four requests differing in one bit each.

    Sequenced deliberately: the two rejection controls run BEFORE the accepted leg so
    that a stack which cannot reach the counterparty at all fails on a named rejection
    code first, and the metric window is drawn as tightly as possible around the single
    accepted request.
    """
    base_url = tls_base_url(live_server)
    verify = ca_verified_ssl_context()
    signed_origin = origin(base_url)

    accepted_key, accepted_jwks = keypair_for(_ACCEPTED_KID)
    tampered_key, tampered_jwks = keypair_for(_TAMPERED_KID)
    unlisted_key, unlisted_jwks = keypair_for(_UNLISTED_KID)
    published_jwks = {"keys": accepted_jwks["keys"] + tampered_jwks["keys"] + unlisted_jwks["keys"]}

    def _sign(private_key, token: str, kid: str) -> dict[str, str]:
        return signed_headers(
            private_key,
            token,
            method="POST",
            path=_ADCP_PATH,
            body=_REQUEST_BODY,
            # Content-Type is signed as well as sent: signed_headers hands the SAME
            # header mapping to the signer and to the caller, so the signature covers
            # the bytes the server reads AND the header that tells it how to read them.
            #
            # Trap 2 (module docstring): request_headers injects the in-process
            # "sig_tenant" hint, which names a tenant that does not exist in this
            # database. Override it with the tenant actually being addressed.
            extra={"x-adcp-tenant": _TENANT_ID, "Content-Type": "application/json"},
            key_id=kid,
            # Trap 1: the signature covers @target-uri, and capture._target_uri rebuilds
            # the authority from the Host header nginx forwards verbatim — port included.
            origin=signed_origin,
        )

    with provisioned_trust_root_tenant(
        live_server,
        tenant_id=_TENANT_ID,
        slug=_SLUG,
        host=netloc(base_url),
        mint_key=False,
        declarations_from_tenant=signing_declarations(*LADDER_OPERATIONS, bucket="required"),
        counterparty_principals={
            _LISTED_TOKEN: _LISTED_AGENT_URL,
            _UNLISTED_TOKEN: _UNLISTED_AGENT_URL,
        },
    ):
        async with httpx.AsyncClient(base_url=base_url, verify=verify, timeout=30.0) as client:
            # ── Fixture setup: publish the freshly minted keys on the counterparty. ──
            # Through the same CA-verified TLS front the SERVER will walk, so a
            # counterparty that is unreachable, mis-routed by SNI or serving the wrong
            # certificate fails HERE, naming the wiring, instead of surfacing three
            # assertions later as an unresolvable key.
            async with httpx.AsyncClient(base_url=_COUNTERPARTY_ORIGIN, verify=verify, timeout=30.0) as counterparty:
                install = await _counterparty_get(counterparty, "PUT", _JWKS_CONTROL_PATH, json=published_jwks)
                assert install.status_code == 200, (
                    f"the counterparty origin must accept the test's JWKS at {_JWKS_CONTROL_PATH!r} over "
                    f"the shared TLS front; got HTTP {install.status_code}. This is the compose wiring "
                    f"({_COUNTERPARTY_ORIGIN}: tls-proxy alias + nginx SNI map + the counterparty-origin "
                    f"service), not the verifier. Body: {install.text[:300]!r}"
                )

                # The published trust root, as the SERVER will read it. Asserted here so
                # leg (iv)'s 401 is a statement about a document that demonstrably lists
                # one agent and not the other, rather than about an origin that happened
                # to serve something unexpected.
                brand = await _counterparty_get(counterparty, "GET", _BRAND_JSON_PATH)
                assert brand.status_code == 200, (
                    f"the counterparty must publish {_BRAND_JSON_PATH!r} as a plain 200 with a JSON "
                    f"object — agent_resolver._fetch_capabilities demands exactly that and follows no "
                    f"redirects; got HTTP {brand.status_code}. Body: {brand.text[:300]!r}"
                )
                listed_urls = [agent.get("url") for agent in brand.json().get("agents", [])]
                assert listed_urls == [_LISTED_AGENT_URL], (
                    f"the counterparty's published brand.json must list EXACTLY the listed agent url so "
                    f"that Tier 3 passes for it and refuses the unlisted sibling; it lists {listed_urls!r}"
                )

            # ── Control (i): unsigned, no bearer. ────────────────────────────
            # Closes verifier_enabled=False, bucket == "none", and the uncaptured-exchange
            # route: under any of them get_adcp_capabilities is a PUBLIC registry row and
            # this request is served 200 anonymously, so rejection_code() returns None and
            # this assertion fails.
            unsigned = await client.post(
                _ADCP_PATH, content=_REQUEST_BODY, headers={"Content-Type": "application/json"}
            )
            assert rejection_code(unsigned) == "request_signature_required", (
                f"an unsigned, unauthenticated POST {_ADCP_PATH!r} against a tenant declaring "
                f"{_OPERATION!r} required_for must be refused by the VERIFIER — a 401 alone is "
                f"satisfied by the resolver's own AUTH_MISSING, and on this PUBLIC row the "
                f"unverified answer is a 200. Got HTTP {unsigned.status_code} with "
                f"WWW-Authenticate={unsigned.headers.get('WWW-Authenticate')!r}"
            )

            # ── Control (ii): correctly signed, then one byte flipped. ───────
            # Closes bucket == "warn" (which swallows an invalid signature and
            # continues to a 2xx) and proves the BYTES were graded, not the presence
            # of the headers.
            tampered = await client.post(
                _ADCP_PATH,
                content=_REQUEST_BODY,
                headers=_tamper(_sign(tampered_key, _LISTED_TOKEN, _TAMPERED_KID)),
            )
            assert rejection_code(tampered) == "request_signature_invalid", (
                "a signature whose bytes were altered — with Signature-Input left well-formed, so it "
                "is not a step-1 header rejection — must be refused as request_signature_invalid. Got "
                f"HTTP {tampered.status_code} with WWW-Authenticate="
                f"{tampered.headers.get('WWW-Authenticate')!r}. A 2xx here means the operation is in "
                "the warn bucket, or the signature was never verified at all."
            )

            # ── Leg (iii): the accepted request, inside the metric window. ───
            before = await _scrape_metrics(client, "before")

            accepted = await client.post(
                _ADCP_PATH, content=_REQUEST_BODY, headers=_sign(accepted_key, _LISTED_TOKEN, _ACCEPTED_KID)
            )
            assert accepted.status_code == 200, (
                f"a correctly signed POST {_ADCP_PATH!r} from a counterparty resolvable through "
                f"its published trust root must be ACCEPTED; got HTTP {accepted.status_code} with "
                f"WWW-Authenticate={accepted.headers.get('WWW-Authenticate')!r}. Body: "
                f"{accepted.text[:300]!r}"
            )
            assert rejection_code(accepted) is None, (
                "the accepted leg must carry no verifier challenge at all; got "
                f"{accepted.headers.get('WWW-Authenticate')!r}"
            )

            after = await _scrape_metrics(client, "after")
            delta = _verified_count(after) - _verified_count(before)
            assert delta == 1.0, (
                f"{VERIFIED_METRIC}{{operation={_OPERATION!r}, keyid={_ACCEPTED_KID!r}}} must increment "
                f"by EXACTLY 1 across the accepted request; it moved by {delta}. That counter has one "
                "call site in src/ (src/core/signing/verifier.py:391), reached only after the SDK "
                "checklist returns with Tier 3 included — so 0 means the 2xx above came from somewhere "
                "else (the verifier disabled, the bucket collapsed to 'none' and the signed request "
                "passed through unverified, no exchange captured, or plain bearer authentication), and "
                ">1 means the kid is not unique to this leg and the delta is measuring another request."
            )

            # ── Leg (iv): same key material, a counterparty its brand does not list. ──
            # The ONLY control that proves the brand.json hop RAN. resolve_agent's hop 2
            # reads jwks_uri out of the agents[] entry, so an unlisted agent dies there as
            # BrandJsonResolverError("agent_not_found"), which _map_brand_json_resolver_error
            # maps to the code below and _FailedDiscoveryJwksResolver raises at checklist
            # step 7. Every other assertion in this module stays green if the key were
            # resolved from the registry instead — including the metric delta, which would
            # be byte-identical.
            unlisted = await client.post(
                _ADCP_PATH, content=_REQUEST_BODY, headers=_sign(unlisted_key, _UNLISTED_TOKEN, _UNLISTED_KID)
            )
            assert rejection_code(unlisted) == "request_signature_agent_not_in_brand_json", (
                "a validly signed request from an agent that its own published brand.json does NOT "
                "list must be refused with the code the brand.json walk assigns — a valid signature "
                f"proves WHO signed, never that the signer may act for the brand. Got HTTP "
                f"{unlisted.status_code} with WWW-Authenticate="
                f"{unlisted.headers.get('WWW-Authenticate')!r}. request_signature_key_unknown here "
                "means the key came from the registry fallback, i.e. no trust root was walked at "
                "all; a 2xx means the document was never consulted."
            )
