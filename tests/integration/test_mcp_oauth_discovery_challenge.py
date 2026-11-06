"""MCP's OAuth discovery answer (#2028), graded on the path that now produces it.

RFC 9728 §5.1 — and MCP's authorization text, which profiles it — requires a protected MCP
resource to answer a call it cannot authenticate with ``401`` plus a ``WWW-Authenticate``
challenge naming ``resource_metadata``, so a client holding no token can find this
deployment's authorization server without having been configured with it. That pointer is
the whole of what #2028 added.

WHY THIS FILE REPLACED A UNIT TEST OF A MIDDLEWARE
--------------------------------------------------
#2028 first produced the challenge from ``MCPOAuthChallengeMiddleware``, an ASGI wrapper
around the ``/mcp`` mount that sniffed the tool name out of the JSON-RPC body, asked
``ToolSpec.requires_credential`` for itself and answered before FastMCP dispatched. That is
the shape ``_resolve_identity`` records #1291's signature verifier being DELETED for -- a
middleware that reads the headers, decides auth and sends its own refusal -- so it was
deleted too, and nothing replaced it. The behaviour did not move: it is produced by the parts
that already existed, in three hops with one owner each.

    ``RegistryTool.run`` -> ``serve`` -> ``_resolve_identity`` raises AUTH_MISSING
      -> ``failure_response`` renders the AdCP envelope
        -> ``AuthChallengeResponder`` lifts it to 401 and derives the pointer

So the subject is no longer a middleware, and a test of one would have nothing left to drive.
It is the SELLER's answer on the MCP wire, which is why this file lives in the integration
suite: all three hops have to run for the challenge to exist, and the middle one is
``_resolve_identity``, which loads the tenant row from PostgreSQL before it refuses anything.
It cannot be a unit test (the subject is not a pure function and a mock of any hop would
grade the mock), and it cannot be a BDD scenario: the harness dispatches MCP over FastMCP's
in-memory transport, which has no ASGI and therefore no ``AuthChallengeResponder``, so an
HTTP status and a response header are unobservable from there. A real request through
``src.app.app`` is the smallest thing that can see them.

WHAT MAKES THESE ASSERTIONS NON-VACUOUS
---------------------------------------
Every one reads the ACTUAL status line and the ACTUAL ``WWW-Authenticate`` bytes off a real
response. Nothing in this file is mocked -- the tenant and the principal are stored rows, the
credential is the one a buyer presents, and the issuer is set as a real deployment input
(``MCP_OAUTH_ISSUER``, read by the settings loader and answered by
``src.core.oauth_service.get_mcp_oauth_issuer``).

The two challenged cases are held against controls, because either alone passes for the wrong
reason: ``tools/list``, a tool needing no credential, and a name this seller does not
implement all prove the challenge is not indiscriminate, and an ACCEPTED bearer on the very
tool that was challenged proves the 401 is about the credential rather than about that tool
always refusing.

Spec-Grounding: RFC 9728 §5.1 (``resource_metadata``); RFC 6750 §3
(``error="invalid_token"`` only when a credential WAS presented); AdCP 3.1.1
``enums/error-code.json`` (the AUTH_MISSING / AUTH_INVALID split keyed on presentation).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from starlette.testclient import TestClient

from src.core.config import load_settings
from tests.factories.principal import plaintext_token_for
from tests.helpers.app_state import preserved_global_app_state
from tests.helpers.credentials import credential_headers
from tests.helpers.envelope_assertions import assert_envelope_shape

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]

ISSUER = "https://agent.example.com"
#: The RFC 9728 protected-resource metadata document ``src/routes/oauth.py`` serves. The
#: challenge has to name THIS, because it is the only document that names the authorization
#: server, and a client that was never configured with an issuer has nowhere else to look.
METADATA_URL = "https://discovery.adcp.test/.well-known/oauth-protected-resource"

#: No credential presented: the scheme alone plus the pointer. RFC 6750 §3 reserves the
#: ``error`` parameter for a credential that WAS presented and rejected, so AUTH_MISSING
#: earns none.
CHALLENGE_NO_CREDENTIAL = f'Bearer resource_metadata="{METADATA_URL}"'
#: Presented and refused: the same pointer, plus the RFC 6750 §3 error code. A client whose
#: token expired needs the metadata URL exactly as much as one that never had a token.
CHALLENGE_REJECTED = f'Bearer error="invalid_token", resource_metadata="{METADATA_URL}"'

_TENANT_ID = "mcp_discovery_tenant"
_PRINCIPAL_ID = "mcp_discovery_principal"
#: The seller's host, which is how a request names its tenant (nginx derives the header from
#: it in production, so the Host is the deployment's own spelling).
_HOST = "discovery.adcp.test"

#: A tool whose registry row requires a caller and whose DTO requires NO field. Both halves
#: matter: the row is what makes the call protected, and the empty DTO is what keeps the
#: refusal under test on the credential. ``invoke_tool`` validates the payload BEFORE it
#: resolves an identity, so a protected tool with required fields answers an empty
#: ``arguments`` with INVALID_REQUEST and never reaches the resolver at all -- the discovery
#: challenge would simply not be the thing graded.
PROTECTED_TOOL = "list_creatives"
#: A tool that serves an anonymous buyer. ``list_creative_formats``, not ``get_products``:
#: the seller-policy half of ``ToolSpec.requires_credential`` makes a ``brand``-accepting
#: tool protected on a tenant whose ``brand_manifest_policy`` is ``require_auth`` (the
#: column's default), so ``get_products`` is correctly challenged here and is not a control.
PUBLIC_TOOL = "list_creative_formats"


@pytest.fixture(autouse=True)
def _oauth_issuer(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """This deployment's OAuth issuer, set the way a deployment sets it.

    BOTH halves of how a deployment sets it. ``MCP_OAUTH_ISSUER`` is read by the settings
    loader, which is the process's one reader of the environment and CACHES what it read
    (``src/core/config.py``), so a bare ``setenv`` after something has already asked for
    settings names a variable nothing will look at again. ``load_settings()`` is the other
    half, and it is exactly what each composition root calls at startup.

    Restored the same way: ``monkeypatch.undo()`` before the reload, so the settings handed
    back to the next file on the xdist worker are built from the ambient environment rather
    than from this file's — the same reason ``preserved_global_app_state`` exists below.
    """
    monkeypatch.setenv("MCP_OAUTH_ISSUER", ISSUER)
    load_settings()
    yield
    monkeypatch.undo()
    load_settings()


@contextmanager
def _seller() -> Iterator[TestClient]:
    """The stored seller, and a client that can reach its ``/mcp`` mount.

    The lifespan must be STARTED -- the mount is driven by a
    ``StreamableHTTPSessionManager`` that FastMCP starts there -- which is also why
    ``preserved_global_app_state`` is not optional: startup rewrites ``app.router.routes``
    and latches the session manager, and both have to be handed back unchanged to the next
    file on the xdist worker.
    """
    from src.app import app
    from tests.factories import PrincipalFactory, TenantFactory
    from tests.harness._base import BareIntegrationEnv

    with BareIntegrationEnv(tenant_id=_TENANT_ID, principal_id=_PRINCIPAL_ID) as env:
        tenant = TenantFactory(tenant_id=_TENANT_ID, virtual_host=_HOST)
        PrincipalFactory(tenant=tenant, principal_id=_PRINCIPAL_ID)
        env._commit_factory_data()

        with preserved_global_app_state(), TestClient(app) as client:
            yield client


def _post(client: TestClient, payload: dict[str, Any], *, token: str | None = None) -> Any:
    """One JSON-RPC message to ``/mcp``, with the credential a buyer would present.

    ``Accept`` carries both media types because MCP's streamable-HTTP transport requires a
    client to offer them, and this request is a conformant client's. ``credential_headers``
    is the one producer of what a test presents to this seller.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **credential_headers(token=token, host=_HOST),
    }
    return client.post("/mcp/", headers=headers, json=payload)


def _tools_call(tool: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": {}}}


def _mcp_result_envelope(response: Any) -> dict[str, Any]:
    """The AdCP error envelope a FastMCP tool failure carries, decoded from the wire bytes.

    MCP reports a tool failure as ``result.content[].text`` with ``isError`` set, and that
    text is the envelope re-encoded as a JSON string -- the shape
    ``AuthChallengeResponder._envelope_in_mcp_result`` reads the code out of. Decoded here
    from the SAME bytes, so the envelope asserted on is the one the challenge was derived
    from rather than a second rendering.
    """
    import json

    body = response.json()
    assert body["result"]["isError"] is True, f"expected an MCP tool failure, got {body!r}"
    return json.loads(body["result"]["content"][0]["text"])


def test_credential_less_protected_tool_call_is_answered_401_naming_resource_metadata(integration_db: Any) -> None:
    """The whole point of #2028, on the real path and asserted on the bytes a buyer receives.

    A ``tools/call`` for a protected tool carrying no credential at all comes back 401 with
    the discovery pointer on it. Produced with no MCP-specific middleware in the stack: the
    resolver raises AUTH_MISSING because the registry row requires a caller, and the one
    renderer derives the pointer from that code.
    """
    with _seller() as client:
        response = _post(client, _tools_call(PROTECTED_TOOL))

    assert response.status_code == 401, (
        f"a credential-less call to the protected tool {PROTECTED_TOOL!r} must be refused 401, got "
        f"{response.status_code} — a 200 here means the refusal never left MCP's JSON-RPC envelope"
    )
    assert response.headers.get("www-authenticate") == CHALLENGE_NO_CREDENTIAL, (
        "the challenge must name resource_metadata: it is how a client holding no token finds "
        f"this deployment's authorization server. Got {response.headers.get('www-authenticate')!r}"
    )
    assert_envelope_shape(_mcp_result_envelope(response), "AUTH_MISSING", recovery="correctable")


def test_a_rejected_bearer_is_answered_invalid_token_with_the_same_pointer(integration_db: Any) -> None:
    """A credential the resolver refused keeps the discovery pointer, and earns RFC 6750's error.

    Same tool, same wire, one difference: a bearer was PRESENTED. ``_resolve_identity``
    answers AUTH_INVALID rather than AUTH_MISSING, and the renderer adds
    ``error="invalid_token"`` beside the unchanged pointer — a client whose token this seller
    does not accept needs the metadata URL as much as one that never had a token.
    """
    with _seller() as client:
        response = _post(client, _tools_call(PROTECTED_TOOL), token="not-a-token-this-seller-issued")

    assert response.status_code == 401, (
        f"a presented-and-refused bearer must be answered 401, got {response.status_code}"
    )
    assert response.headers.get("www-authenticate") == CHALLENGE_REJECTED, (
        'a rejected credential earns error="invalid_token" AND the same resource_metadata '
        f"pointer. Got {response.headers.get('www-authenticate')!r}"
    )
    assert_envelope_shape(_mcp_result_envelope(response), "AUTH_INVALID", recovery="terminal")


def test_tools_list_discovery_is_never_challenged(integration_db: Any) -> None:
    """``tools/list`` is not a tool call, so no credential policy applies to it.

    The roster is asserted non-empty, not merely 200: a challenge-free 200 carrying nothing
    would be indistinguishable from an empty server, and what has to survive the deletion is
    that an unauthenticated client can still LEARN what this seller implements.
    """
    with _seller() as client:
        response = _post(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

    assert response.status_code == 200, f"unauthenticated discovery must not be refused: {response.text[:400]}"
    assert response.headers.get("www-authenticate") is None, (
        "tools/list names no tool, so there is no credential policy to refuse it under; a "
        f"challenge here means something is deciding auth before the tool is known: "
        f"{response.headers.get('www-authenticate')!r}"
    )
    advertised = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert PROTECTED_TOOL in advertised and PUBLIC_TOOL in advertised, (
        f"the unauthenticated roster is {sorted(advertised)}, which does not advertise this "
        "seller's tools — a 200 that lists nothing is not discovery"
    )


def test_a_tool_that_needs_no_credential_is_never_challenged(integration_db: Any) -> None:
    """A public row serves an anonymous buyer, and the registry row is what says so.

    Read through ``ToolSpec.requires_credential()`` by the resolver, from the row — the
    deleted middleware asked the same question a second time from inside the /mcp mount, and
    a second reader of a declared fact is how a tool becomes public on one surface only.
    """
    with _seller() as client:
        response = _post(client, _tools_call(PUBLIC_TOOL))

    assert response.status_code == 200, (
        f"{PUBLIC_TOOL!r} requires no credential, so an anonymous call must be served: "
        f"{response.status_code} {response.text[:400]}"
    )
    assert response.headers.get("www-authenticate") is None, (
        f"a public tool must not be challenged, got {response.headers.get('www-authenticate')!r}"
    )
    payload = response.json()["result"]["structuredContent"]
    assert payload["status"] == "completed", f"the anonymous call did not complete: {payload!r}"
    assert payload["formats"], "a challenge-free 200 carrying no formats is not proof the tool ran"


def test_a_name_this_seller_does_not_implement_is_not_challenged(integration_db: Any) -> None:
    """FastMCP owns "no such tool", and a discovery challenge would mislabel it as auth.

    A 401 here would tell a buyer to go and authenticate for a tool that will never exist,
    and would hide the real refusal behind the challenge. Nothing in the stack can produce
    one now -- the registry has no row to read a credential policy off, so the boundary is
    never reached -- and this is what pins that a future pre-dispatch gate cannot reintroduce
    it.
    """
    with _seller() as client:
        response = _post(client, _tools_call("teleport_media_buy"))

    assert response.headers.get("www-authenticate") is None, (
        f"an unimplemented tool name must not earn an auth challenge, got {response.headers.get('www-authenticate')!r}"
    )
    assert response.status_code == 200, (
        f"the unknown-tool refusal belongs to MCP's own error channel, not to HTTP: {response.status_code}"
    )
    body = response.json()["result"]
    assert body["isError"] is True and "teleport_media_buy" in body["content"][0]["text"], (
        f"expected MCP's own unknown-tool refusal naming the tool, got {body!r}"
    )


def test_an_accepted_bearer_on_the_same_tool_is_not_challenged(integration_db: Any) -> None:
    """The control: the 401s above are about the CREDENTIAL, not about this tool.

    Without this, every assertion in this file is satisfied by a seller that refuses
    ``list_creatives`` unconditionally. The token is the one the stored principal's hash was
    derived from, so the resolver resolves a real caller and the tool runs.
    """
    with _seller() as client:
        response = _post(client, _tools_call(PROTECTED_TOOL), token=plaintext_token_for(_PRINCIPAL_ID))

    assert response.status_code == 200, (
        f"an accepted bearer must be served on {PROTECTED_TOOL!r}, got {response.status_code} {response.text[:400]}"
    )
    assert response.headers.get("www-authenticate") is None, (
        f"an authenticated caller must not be challenged, got {response.headers.get('www-authenticate')!r}"
    )
    assert response.json()["result"]["structuredContent"]["status"] == "completed", (
        f"the authenticated call did not complete: {response.text[:400]}"
    )
