"""E2E-2 (narrowed to the refused leg): an unauthenticated, unsigned request to a
``required_for`` operation is refused, over real HTTP, on REST and MCP.

#1291 — the inbound verifier (B1/B2) had zero real-HTTP coverage; a later
finding made the gap concrete: the in-process a2a/mcp legs never traverse the
ASGI stack, so before this module the verifier's evidence was one in-process leg
plus two simulations.

**PROVES.** The composition rule (security.mdx @ v3.1.1 :1268-1271) — refuse an
unauthenticated, unsigned request to a declared ``required_for`` operation with
``request_signature_required`` — is enforced by the DEPLOYED stack, driven
through nginx/TLS over a real socket, against a real
``tenants.capability_declarations`` row (written through
``provisioned_trust_root_tenant``'s ``declarations_from_tenant`` hook, the same
mechanism ``test_jwks_publication_e2e.py`` uses — a second, separate
``TenantConfigUoW`` write from inside the test body was tried first and found
NOT visible to the live server's read: it opens its own ``get_db_session()``
engine, a different SQLAlchemy engine object than ``live_db_env``'s
``create_engine(live_server["postgres"])``, see the local comment on
``signing_declarations``), on BOTH REST and MCP naming surfaces (the ticket's own
motivating gap).

**WHERE THE RULE LIVES NOW — and why that changes what this module must send.**
The ASGI ``RequestSignatureMiddleware`` this module was first written against is
gone. Verification runs inside the ONE resolver,
``src.core.resolved_identity._resolve_identity`` (step 4b), reached only from
``src.core.tools._boundary.invoke_tool``; the typed
``AdCPRequestSignatureError`` it raises is lifted to ``401`` +
``WWW-Authenticate: Signature error="<code>"`` by ``AuthChallengeResponder``
(``src/core/auth_middleware.py``), app-wide middleware that reads the AdCP code
off a FINISHED JSON body and therefore answers REST, MCP, A2A and JSON-RPC from
one renderer. Three consequences this module is built around, each of which
silently produced a NON-401 under the old module's request shapes:

1. **The payload is validated BEFORE the caller is resolved.** ``serve()`` is
   ``invoke_tool(name, validated_request(name, raw, protocol), ...)``, and
   ``validated_request`` is a plain ``dto.model_validate(raw)``. An operation
   whose DTO has required fields never reaches the verifier on an empty body —
   ``create_media_buy`` (this module's original choice) requires five, so
   ``json={}`` was answered with a validation envelope, not a challenge.
2. **A credential-requiring operation is refused on its BEARER first.**
   ``_resolve_identity`` step 2 raises ``AUTH_MISSING`` for an anonymous caller
   on a row whose impl takes ``ResolvedIdentity``, well above step 4b. Only a
   PUBLIC row (``PublicIdentity``) lets an anonymous unsigned request travel far
   enough for the composition rule to be the thing that refuses it.
3. **MCP now answers inside FastMCP, not above it.** The mount is
   ``mcp.http_app(path="/", json_response=True, stateless_http=True)``
   (``src/app.py:128``). Stateless means no ``initialize`` handshake is needed
   (``mcp.server.session`` marks a stateless session Initialized on
   construction), but the SDK still rejects a POST whose ``Accept`` omits
   ``application/json`` with ``406`` before any dispatch
   (``mcp/server/streamable_http.py::_validate_accept_header``). httpx's default
   ``Accept: */*`` does not satisfy it, so the frame must name the media types.

So the graded operation here is ``list_creative_formats``: a public row
(``requires_credential()`` is unconditionally False — its DTO declares no
``brand``, so the tenant's ``brand_manifest_policy`` cannot escalate it the way
it can ``get_products``) whose DTO validates ``{}``. It is the ONLY shape that
reaches step 4b anonymously, which is what makes a ``request_signature_required``
challenge attributable to the composition rule and nothing else.

**DOES NOT PROVE.**
* The accepted-signed branch — that is the sibling module,
  ``test_request_signature_accepted_e2e.py``.
* The a2a ``message/send`` naming path, over real HTTP — out of scope for this
  ticket; if wanted, file as a follow-up.
* Bearer-authenticated-but-unsigned pass-through (security.mdx :1269's OTHER
  branch) — not asserted here.

**Host-routing collision — the real mechanism, not the tenant id.**
``ix_tenants_virtual_host`` is UNIQUE and every signing e2e module provisions at
the SAME TLS netloc, so a distinct ``_TENANT_ID``/``_SLUG`` (not colliding with
``tr_e2e``/``tr_e2e_tls``/``jwkspub_e2e``/``dn4i_e2e``/``whsig_e2e``) does NOT by
itself prevent a collision — it only makes the blame land in the right module
when one happens. What actually prevents it is the serial e2e run (``tox -e
e2e``, no ``-n``) plus ``drop_tenant`` at both ends of every module. A tenant
leaked by a crashed teardown surfaces here as an ``IntegrityError`` in THIS
module's fixture setup.

**How to run it.** Via ``saci run e2e`` (the WHOLE e2e suite via tox's
``[testenv:e2e]``, against the live server's ``adcp`` database) — NOT ``saci run
ci <file>``, which bypasses tox and resolves the wrong database (``adcp_test``)
for this suite, AND NOT ``saci run e2e -- -k <test name>``, which the current
``run_all_tests.sh`` argument contract treats as an explicit suite list — ``e2e``
is captured, everything after it is silently discarded, so the ``-k`` filter
never reaches pytest (confirmed against ``run_all_tests.sh`` and
``run_all_tests_host.sh`` source, #1291 mp53.6). There is currently no supported
way to target a single e2e file/test while keeping the correct database.

**Mutations that must turn it RED** (the three assertions are independent):
disabling the ``bucket != "required"`` early return in
``src.core.signing.verifier._refuse_unsigned_if_required`` takes the control to a
401; passing ``declarations_from_tenant=None`` to
``provisioned_trust_root_tenant`` takes the served ``required_for`` to ``[]`` and
both refusals to a 200; dropping the ``www-authenticate`` append in
``src.core.auth_middleware._flush`` takes both ``rejection_code()`` reads to
``None``.
"""

from __future__ import annotations

import httpx
import pytest

from tests.e2e._signing_e2e import (
    ca_verified_ssl_context,
    netloc,
    provisioned_trust_root_tenant,
    signing_declarations,
    tls_base_url,
)
from tests.helpers.signing import CAPABILITIES_ADCP_PATH, rejection_code

#: Distinct from every other signing e2e module's tenant/slug (see the
#: host-routing-collision note in the module docstring).
_SLUG = "reqsig_e2e"
_TENANT_ID = "reqsig_e2e"

#: The one operation this tenant declares ``required_for``. Naming it explicitly
#: (rather than leaving the bucket unnarrowed) is what puts
#: ``get_adcp_capabilities`` in the ``none`` bucket, which is what makes the
#: negative-adjacent control below meaningful.
#:
#: See the docstring's consequences (1) and (2) for why it is THIS operation and
#: not ``create_media_buy``: public row, and a DTO with no required fields.
_REQUIRED_OPERATION = "list_creative_formats"

#: ``POST /api/v1/creative-formats`` — the REST route the registry derives for
#: ``list_creative_formats`` (``ToolSpec.rest``, registered by
#: ``src/routes/api_v1.py``). The route table names the operation, so the
#: refusal is about the operation and not about the path text.
_CREATIVE_FORMATS_PATH = "/api/v1/creative-formats"

#: ``POST /api/v1/capabilities`` — a route that IS in the REST route table
#: (resolves to ``get_adcp_capabilities``) and is NOT in this tenant's
#: declaration (only ``list_creative_formats`` is named), so its bucket is
#: ``none``. POST, not GET: the registry derives exactly one verb per row and
#: this row's is POST.
#:
#: Deliberately NOT an unnamed/mistyped path: dispatch is by registry key, so an
#: unnamed path is a 404 from the router that never reaches the boundary at all
#: — a control that could not distinguish "the bucket is none" from "nothing ran".
_CAPABILITIES_PATH = CAPABILITIES_ADCP_PATH

#: The MCP JSON-RPC envelope naming the required operation. ``tools/call`` itself
#: is NOT the name the verifier grades: ``invoke_tool`` builds the
#: ``SignatureSubject`` from the REGISTRY KEY it dispatched on, so the AdCP
#: operation namespace is the only one consulted and the JSON-RPC method name can
#: never route this into ``protocol_methods_*``.
_MCP_TOOLS_CALL_BODY = {
    "jsonrpc": "2.0",
    "id": "reqsig-e2e-1",
    "method": "tools/call",
    "params": {"name": _REQUIRED_OPERATION, "arguments": {}},
}

#: Both media types, because ``_validate_accept_header`` runs ahead of dispatch
#: and httpx's default ``Accept: */*`` fails it with a 406 that carries no
#: challenge — a refusal this module would otherwise read as "the verifier did
#: not fire". Stated here rather than imported from ``tests.harness._base``'s
#: private ``_MCP_ACCEPT`` so this module does not depend on the harness package
#: importing cleanly.
_MCP_ACCEPT_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.mark.asyncio
@pytest.mark.requires_db
async def test_unsigned_unauthenticated_request_to_required_operation_is_refused_rest_and_mcp(
    docker_services_e2e, live_server
):
    """Same tenant, same declaration, two AdCP naming surfaces, one composition rule.

    REST alone would regrade a surface ``BareIntegrationEnv.get_rest_client()``
    already covers in-process (it builds ``src.app.app`` with the full middleware
    stack under a Starlette ``TestClient``); the MCP leg is the real novel
    evidence this ticket exists to produce, and it is novel twice over now that
    the refusal is minted inside FastMCP's own dispatch and has to travel back
    out through ``AuthChallengeResponder``'s MCP-result reader
    (``_envelope_in_mcp_result``) to become a 401 at all.
    """
    base_url = tls_base_url(live_server)
    verify = ca_verified_ssl_context()

    with provisioned_trust_root_tenant(
        live_server,
        tenant_id=_TENANT_ID,
        slug=_SLUG,
        host=netloc(base_url),
        mint_key=False,
        declarations_from_tenant=signing_declarations(_REQUIRED_OPERATION, bucket="required"),
    ) as (tenant, key):
        assert key is None, "this tenant must own no signing key — the refused leg needs no key at all"

        async with httpx.AsyncClient(base_url=base_url, verify=verify, timeout=15.0) as client:
            # ── REST leg: unauthenticated, unsigned POST to the required op. ──
            rest_response = await client.post(_CREATIVE_FORMATS_PATH, json={})
            assert rest_response.status_code == 401, (
                f"an unauthenticated, unsigned POST {_CREATIVE_FORMATS_PATH!r} against a tenant that "
                f"declares {_REQUIRED_OPERATION!r} required_for must be refused with HTTP 401; got "
                f"{rest_response.status_code}. Body: {rest_response.text[:300]!r}"
            )
            assert rejection_code(rest_response) == "request_signature_required", (
                "a 401 alone is satisfied by several unrelated causes (a bearer refusal from "
                "_resolve_identity step 2, a 404 wearing a 401, a malformed-header precheck) — the "
                "REST leg's WWW-Authenticate challenge must carry request_signature_required "
                f"specifically; got {rest_response.headers.get('WWW-Authenticate')!r}"
            )

            # ── MCP leg: same tenant/declaration, JSON-RPC tools/call naming ──
            # the required operation. This is the surface the ticket's own
            # motivating gap names: the in-process a2a/mcp legs never traverse
            # the ASGI stack, so REST-only evidence would regrade an
            # already-covered surface.
            mcp_response = await client.post("/mcp/", json=_MCP_TOOLS_CALL_BODY, headers=_MCP_ACCEPT_HEADERS)
            assert mcp_response.status_code == 401, (
                "an unauthenticated, unsigned MCP tools/call naming "
                f"{_REQUIRED_OPERATION!r} against a tenant that declares it required_for must be "
                f"refused with HTTP 401 — note that a 406 here means the Accept precheck refused the "
                f"frame before dispatch, and a 200 carrying isError means AuthChallengeResponder did "
                f"not read the code off the MCP result; got {mcp_response.status_code}. Body: "
                f"{mcp_response.text[:300]!r}"
            )
            assert rejection_code(mcp_response) == "request_signature_required", (
                "the MCP leg's WWW-Authenticate challenge must carry request_signature_required "
                f"specifically, the same composition rule the REST leg enforces; got "
                f"{mcp_response.headers.get('WWW-Authenticate')!r}"
            )

            # ── Negative-adjacent control: same tenant, a NAMED route that is ─
            # NOT in the declared bucket. Proves the 401s above are the
            # composition rule targeting the declared operation specifically,
            # not this tenant's declaration blanket-rejecting every request.
            #
            # It is ALSO the served-declaration tie-in: reading the capabilities
            # document forces validate_signing_platform_backing to run (it does
            # NOT run on the resolver's read path, only on the capabilities read
            # path), so one anonymous call proves both that the operation is
            # bucketed `none` and that the declaration being enforced is the one
            # production would actually SERVE.
            control_response = await client.post(_CAPABILITIES_PATH, json={})
            assert control_response.status_code == 200, (
                f"POST {_CAPABILITIES_PATH!r} names get_adcp_capabilities, which this tenant's "
                f"declaration does NOT put in any bucket (only {_REQUIRED_OPERATION!r} is named) — "
                f"it must be served normally; got {control_response.status_code}. Body: "
                f"{control_response.text[:300]!r}"
            )
            assert rejection_code(control_response) is None, (
                "the control route must not be rejected by the verifier at all — a rejection here "
                "would mean the declaration is being applied to every operation, defeating the point "
                f"of a bucketed declaration; got WWW-Authenticate="
                f"{control_response.headers.get('WWW-Authenticate')!r}"
            )

            served_signing = control_response.json().get("request_signing") or {}
            assert served_signing.get("required_for") == [_REQUIRED_OPERATION], (
                f"the served capabilities document must echo the declared required_for bucket "
                f"verbatim — declared [{_REQUIRED_OPERATION!r}], served "
                f"{served_signing.get('required_for')!r}. A mismatch means the declaration written "
                "at tenant-creation time is not the one the platform-backing validation accepted, "
                "and the two 401s above were produced by some other declaration."
            )
