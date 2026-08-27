"""E2E-2 (narrowed to the refused leg): an unauthenticated, unsigned request to a
``required_for`` operation is refused, over real HTTP, on REST and MCP.

#1291 — the inbound verifier (B1/B2) had zero real-HTTP coverage; a later
finding made the gap concrete: the in-process a2a/mcp legs never traverse ASGI
middleware, so before this module the verifier's evidence was one in-process
leg (``test_request_signature_middleware.py::
TestCompositionWithFallbackAuthenticators::test_unsigned_and_unauthenticated_is_rejected``)
plus two simulations.

**PROVES.** ``RequestSignatureMiddleware``'s composition rule (security.mdx
:1268-1269) — reject an unauthenticated, unsigned request to a declared
``required_for`` operation with ``request_signature_required`` — is enforced by
the DEPLOYED ASGI middleware stack, driven through nginx/TLS over a real socket,
against a real ``tenants.capability_declarations`` row (written through
``provisioned_trust_root_tenant``'s ``declarations_from_tenant`` hook, the same
mechanism ``test_jwks_publication_e2e.py`` uses — a second, separate
``TenantConfigUoW`` write from inside the test body was tried first and found
NOT visible to the live server's read: it opens its own ``get_db_session()``
engine, a different SQLAlchemy engine object than ``live_db_env``'s
``create_engine(live_server["postgres"])``, see the local comment on
``signing_declarations``), on BOTH REST and MCP naming surfaces (the
ticket's own motivating gap). The declaration is also proven to be one
production would actually SERVE — not merely one the middleware happens to
read — by an anonymous ``fetch_capabilities()`` call that forces
``validate_signing_platform_backing`` to run (that validation does not run on
the middleware's read path, only on the capabilities read path) and asserts
the served block matches what was declared.

**DOES NOT PROVE.**
* The accepted-signed branch — a request signed with a counterparty key
  resolvable via the published trust root is accepted. That is a follow-on
  #1291 ticket, forked from this one and gated on B4's registered-test-counterparty
  mechanism: the "accepted" leg is structurally blocked here because every
  address that could stand up a reachable buyer counterparty from inside the
  live server's container resolves to a private/loopback/link-local address, which
  ``adcp.signing.jwks.resolve_and_validate_host`` unconditionally rejects
  under this deployment's non-negotiable ``allow_private_destinations=False``
  (``tests/unit/test_architecture_no_private_destinations.py``).
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

**Mutations verified by hand (both restored after verification).** Run this
module via ``saci run e2e`` (the WHOLE e2e suite via tox's ``[testenv:e2e]``,
against the live server's ``adcp`` database) — NOT ``saci run ci <file>``,
which bypasses tox and resolves the wrong database (``adcp_test``) for this
suite, AND NOT ``saci run e2e -- -k <test name>``, which the current
``run_all_tests.sh`` argument contract treats as an explicit suite list —
``e2e`` is captured, everything after it is silently discarded, so the
``-k`` filter never reaches pytest (confirmed against ``run_all_tests.sh``
and ``run_all_tests_host.sh`` source, #1291 mp53.6). There is currently no
supported way to target a single e2e file/test while keeping the correct
database; running the full suite is the only correct option today.

1. Disabled the ``bucket == "required" and not context.authenticated`` branch in
   ``RequestSignatureMiddleware._handle_unsigned``
   (``src/core/signing/request_verifier_middleware.py``) — the REST leg's
   assertion went RED (``got None`` instead of ``request_signature_required``:
   the request fell through to a plain auth-required 401 instead of the
   signing-specific rejection).
2. Passed ``declarations_from_tenant=None`` to ``provisioned_trust_root_tenant``
   (no declaration written) — the ``fetch_capabilities()`` assertion went RED
   (served ``required_for`` came back ``[]`` instead of
   ``['create_media_buy']``, since nothing promoted the operation into the
   required bucket).

Both mutations reverted after verification; ``git diff --stat`` showed no stray
changes.
"""

from __future__ import annotations

import httpx
import pytest

from tests.e2e._signing_e2e import (
    ca_verified_ssl_context,
    fetch_capabilities,
    netloc,
    provisioned_trust_root_tenant,
    signing_declarations,
    tls_base_url,
)
from tests.helpers.signing import (
    BODYLESS_ADCP_PATH,
    REWRITTEN_ADCP_PATH,
    rejection_code,
)

#: Distinct from every other signing e2e module's tenant/slug (see the
#: host-routing-collision note in the module docstring).
_SLUG = "reqsig_e2e"
_TENANT_ID = "reqsig_e2e"

#: The one operation this tenant declares ``required_for``. Naming it explicitly
#: (rather than leaving the bucket unnarrowed) is what puts
#: ``get_adcp_capabilities`` in the ``none`` bucket, which is what makes the
#: negative-adjacent control below meaningful.
_REQUIRED_OPERATION = "create_media_buy"

#: ``POST /api/v1/media-buys`` — the REST route ``RegistryOperationResolver``
#: names ``create_media_buy`` off the route table, regardless of body.
_MEDIA_BUYS_PATH = REWRITTEN_ADCP_PATH

#: ``GET /api/v1/capabilities`` — a route that IS in the REST route table
#: (resolves to ``get_adcp_capabilities``) and is NOT in this tenant's
#: declaration (only ``create_media_buy`` is named), so its bucket is ``none``.
#:
#: Deliberately NOT an unnamed/mistyped path: ``_fail_closed_bucket``
#: (``request_verifier_middleware.py``) promotes ANY request the resolver
#: cannot NAME to ``required`` whenever the tenant declares a required bucket
#: at all — so an unnamed-path control would 401 for a reason that has nothing
#: to do with the composition rule under test, and the control would pass
#: vacuously.
_CAPABILITIES_PATH = BODYLESS_ADCP_PATH

#: The MCP JSON-RPC envelope naming ``create_media_buy`` — the tool-call shape
#: ``_resolve_jsonrpc`` reads the OPERATION name off (``tools/call`` itself is
#: deliberately dropped from naming so it does not route into
#: ``protocol_methods_*`` and disable ``required_for`` on the whole MCP
#: surface).
_MCP_TOOLS_CALL_BODY = {
    "jsonrpc": "2.0",
    "id": "reqsig-e2e-1",
    "method": "tools/call",
    "params": {"name": _REQUIRED_OPERATION, "arguments": {}},
}


@pytest.mark.asyncio
@pytest.mark.requires_db
async def test_unsigned_unauthenticated_request_to_required_operation_is_refused_rest_and_mcp(
    docker_services_e2e, live_server
):
    """Same tenant, same declaration, two AdCP naming surfaces, one composition rule.

    REST alone would regrade a surface ``BareIntegrationEnv.get_rest_client()``
    already covers in-process (it builds ``src.app.app`` with the full
    middleware stack under a Starlette ``TestClient``); the MCP leg is the real
    novel evidence this ticket exists to produce.
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
            # ── Force validate_signing_platform_backing to actually run. ──────
            # It does NOT run on the middleware's read path, only on the
            # capabilities read path — so this ties "what the buyer was told"
            # to "what gets enforced" and proves the declaration is one
            # production would actually serve, not just one the middleware
            # happens to read.
            served = await fetch_capabilities(client)
            served_signing = served.get("request_signing") or {}
            assert served_signing.get("required_for") == [_REQUIRED_OPERATION], (
                f"the served capabilities document must echo the declared required_for bucket "
                f"verbatim — declared [{_REQUIRED_OPERATION!r}], served "
                f"{served_signing.get('required_for')!r}. A mismatch means the declaration written "
                "at tenant-creation time is not the one the platform-backing validation accepted."
            )

            # ── REST leg: unauthenticated, unsigned POST to the required op. ──
            rest_response = await client.post(_MEDIA_BUYS_PATH, json={})
            assert rest_response.status_code == 401, (
                f"an unauthenticated, unsigned POST {_MEDIA_BUYS_PATH!r} against a tenant that "
                f"declares {_REQUIRED_OPERATION!r} required_for must be refused with HTTP 401; got "
                f"{rest_response.status_code}. Body: {rest_response.text[:300]!r}"
            )
            assert rejection_code(rest_response) == "request_signature_required", (
                "a 401 alone is satisfied by several unrelated causes (auth middleware, a 404 "
                "wearing a 401, a malformed-header precheck) — the REST leg's WWW-Authenticate "
                f"challenge must carry request_signature_required specifically; got "
                f"{rest_response.headers.get('WWW-Authenticate')!r}"
            )

            # ── MCP leg: same tenant/declaration, JSON-RPC tools/call naming ──
            # create_media_buy. This is the surface the ticket's own motivating
            # gap names: the in-process a2a/mcp legs never traverse ASGI
            # middleware, so REST-only evidence would regrade an
            # already-covered surface.
            mcp_response = await client.post("/mcp/", json=_MCP_TOOLS_CALL_BODY)
            assert mcp_response.status_code == 401, (
                "an unauthenticated, unsigned MCP tools/call naming "
                f"{_REQUIRED_OPERATION!r} against a tenant that declares it required_for must be "
                f"refused with HTTP 401; got {mcp_response.status_code}. Body: "
                f"{mcp_response.text[:300]!r}"
            )
            assert rejection_code(mcp_response) == "request_signature_required", (
                "the MCP leg's WWW-Authenticate challenge must carry request_signature_required "
                f"specifically, the same composition rule the REST leg enforces; got "
                f"{mcp_response.headers.get('WWW-Authenticate')!r}"
            )

            # ── Negative-adjacent control: same tenant, a NAMED route that is ─
            # NOT in the declared bucket. Proves the 401s above are the
            # composition rule targeting create_media_buy specifically, not
            # this tenant's declaration blanket-rejecting every request.
            control_response = await client.get(_CAPABILITIES_PATH)
            assert control_response.status_code == 200, (
                f"GET {_CAPABILITIES_PATH!r} names get_adcp_capabilities, which this tenant's "
                f"declaration does NOT put in any bucket (only {_REQUIRED_OPERATION!r} is named) — "
                f"it must be served normally; got {control_response.status_code}. Body: "
                f"{control_response.text[:300]!r}"
            )
            assert rejection_code(control_response) is None, (
                "the control route must not be rejected by the verifier at all — a rejection here "
                "would mean either the declaration is being applied to every operation (defeating "
                "the point of a bucketed declaration) or the fail-closed unnameable-request rule is "
                f"firing for the wrong reason; got WWW-Authenticate="
                f"{control_response.headers.get('WWW-Authenticate')!r}"
            )
