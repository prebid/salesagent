"""Full-stack bearer-token authentication across all three transports (#1546 review).

The Bearer scheme parse used to be hand-rolled at four boundary sites and had
diverged (UnifiedAuthMiddleware stripped the header before the prefix check;
auth.py / resolved_identity did not), so a padded ``Authorization`` value
authenticated on one transport and failed on another. All sites now route
through ``src.core.http_utils.extract_auth_token``; the per-path consistency
is pinned by tests/unit/test_token_extraction_consistency.py.

This module grades the three canonical/padded/lowercase-scheme forms on the
REAL wire seam of every transport, against a factory-created DB principal —
no ``_get_auth_token`` mock, so ``extract_auth_token`` runs for real:

- REST: TestClient POST → UnifiedAuthMiddleware → AuthContext →
  _require_auth_dep → resolve_identity → DB token lookup.
- A2A: TestClient POST /a2a (in-process ASGI) → the SAME UnifiedAuthMiddleware →
  AdCPCallContextBuilder → handler._get_auth_token → _resolve_a2a_identity →
  DB lookup. The A2A bearer seam IS UnifiedAuthMiddleware (shared with REST);
  the harness normally injects a pre-built AuthContext, so this is the first
  end-to-end grading of the real A2A parse.
- MCP: resolve_identity_from_context (the MCP wrapper's identity resolver)
  reads headers via get_http_headers and runs extract_auth_token → DB lookup.
  Patching get_http_headers with a raw ``Authorization: Bearer`` value (not a
  pre-extracted x-adcp-auth) exercises the bearer parse at the MCP boundary.
"""

from __future__ import annotations

import uuid

import pytest

from tests.harness.capabilities import CapabilitiesEnv

# The three header forms this PR's normalization must treat identically.
_BEARER_FORMS = ["Bearer {token}", "  Bearer {token}  ", "bearer {token}"]
_BEARER_IDS = ["canonical", "padded", "lowercase-scheme"]


def _build_jsonrpc_skill(skill: str, params: dict | None = None) -> dict:
    """JSON-RPC 2.0 SendMessage envelope for an explicit A2A skill invocation."""
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "ROLE_USER",
                "parts": [{"data": {"skill": skill, "parameters": params or {}}}],
            }
        },
    }


def _is_auth_rejection(body: dict) -> bool:
    """True when a JSON-RPC A2A response is an authentication rejection."""
    error = body.get("error")
    if not error:
        return False
    message = str(error.get("message", "")).lower()
    return "auth" in message or "token" in message


@pytest.mark.requires_db
class TestBearerAuthenticatesOnRestWire:
    """Authorization: Bearer authenticates end-to-end, padded or not."""

    @pytest.mark.parametrize(
        "authorization_template",
        ["Bearer {token}", "  Bearer {token}  ", "bearer {token}"],
        ids=["canonical", "padded", "lowercase-scheme"],
    )
    def test_bearer_header_authenticates(self, integration_db, authorization_template):
        from starlette.testclient import TestClient

        from src.app import app

        with CapabilitiesEnv() as env:
            tenant, principal = env.setup_default_data()

            client = TestClient(app)
            response = client.post(
                "/api/v1/creatives",
                json={},
                headers={
                    "Authorization": authorization_template.format(token=principal.access_token),
                    "x-adcp-tenant": tenant.tenant_id,
                },
            )

            assert response.status_code == 200, f"Bearer auth failed on REST wire: {response.text}"
            assert response.json()["creatives"] == []

    def test_missing_token_is_rejected(self, integration_db):
        """Control: the endpoint genuinely requires auth (no false green above)."""
        from starlette.testclient import TestClient

        from src.app import app

        with CapabilitiesEnv() as env:
            tenant, _principal = env.setup_default_data()

            client = TestClient(app)
            response = client.post(
                "/api/v1/creatives",
                json={},
                headers={"x-adcp-tenant": tenant.tenant_id},
            )

            assert response.status_code == 401
            # AdCPAuthenticationError carries the project's AUTH_TOKEN_INVALID wire code.
            assert response.json()["adcp_error"]["code"] == "AUTH_TOKEN_INVALID"


@pytest.mark.requires_db
class TestBearerAuthenticatesOnA2AWire:
    """Authorization: Bearer authenticates end-to-end on the A2A wire, padded or not."""

    @pytest.mark.parametrize("authorization_template", _BEARER_FORMS, ids=_BEARER_IDS)
    def test_bearer_header_authenticates(self, integration_db, authorization_template):
        from starlette.testclient import TestClient

        from src.app import app

        with CapabilitiesEnv() as env:
            tenant, principal = env.setup_default_data()

            client = TestClient(app, raise_server_exceptions=False)
            # get_media_buys is auth-required, so getting PAST the auth gate proves
            # the bearer token was parsed and the principal resolved on the wire.
            response = client.post(
                "/a2a",
                json=_build_jsonrpc_skill("get_media_buys", {}),
                headers={
                    "Authorization": authorization_template.format(token=principal.access_token),
                    "x-adcp-tenant": tenant.tenant_id,
                    "Content-Type": "application/json",
                    "A2A-Version": "1.0",
                },
            )

            body = response.json()
            assert not _is_auth_rejection(body), f"Bearer auth failed on A2A wire: {body.get('error')}"
            assert "result" in body, f"Expected a JSON-RPC result past the auth gate: {body}"

    def test_missing_token_is_rejected(self, integration_db):
        """Control: the auth-required skill genuinely rejects an unauthenticated request."""
        from starlette.testclient import TestClient

        from src.app import app

        with CapabilitiesEnv() as env:
            tenant, _principal = env.setup_default_data()

            client = TestClient(app, raise_server_exceptions=False)
            response = client.post(
                "/a2a",
                json=_build_jsonrpc_skill("get_media_buys", {}),
                headers={
                    "x-adcp-tenant": tenant.tenant_id,
                    "Content-Type": "application/json",
                    "A2A-Version": "1.0",
                },
            )

            assert _is_auth_rejection(response.json()), "Auth-required A2A skill accepted a request with no token"


@pytest.mark.requires_db
class TestBearerResolvesIdentityOnMcpBoundary:
    """Authorization: Bearer resolves the MCP identity via the real extract_auth_token seam."""

    @pytest.mark.parametrize("authorization_template", _BEARER_FORMS, ids=_BEARER_IDS)
    def test_bearer_header_resolves_principal(self, integration_db, authorization_template):
        from unittest.mock import patch

        from src.core.transport_helpers import resolve_identity_from_context

        with CapabilitiesEnv() as env:
            tenant, principal = env.setup_default_data()

            headers = {
                "authorization": authorization_template.format(token=principal.access_token),
                "x-adcp-tenant": tenant.tenant_id,
            }
            # get_http_headers is the MCP boundary's header seam; feed it the raw
            # bearer value so resolve_identity_from_context runs the real parse.
            with patch("src.core.transport_helpers.get_http_headers", return_value=headers):
                identity = resolve_identity_from_context(None, require_valid_token=True, protocol="mcp")

            assert identity is not None, f"Bearer auth failed on MCP boundary for {authorization_template!r}"
            assert identity.principal_id == principal.principal_id

    def test_missing_token_resolves_no_principal(self, integration_db):
        """Control: a header with no token resolves to no principal (auth-optional MCP path)."""
        from unittest.mock import patch

        from src.core.transport_helpers import resolve_identity_from_context

        with CapabilitiesEnv() as env:
            tenant, _principal = env.setup_default_data()

            with patch(
                "src.core.transport_helpers.get_http_headers",
                return_value={"x-adcp-tenant": tenant.tenant_id},
            ):
                identity = resolve_identity_from_context(None, require_valid_token=False, protocol="mcp")

            assert identity is None or not identity.principal_id
