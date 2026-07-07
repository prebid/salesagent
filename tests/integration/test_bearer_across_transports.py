"""Full-stack bearer-token authentication on the REST wire (#1546 review).

The Bearer scheme parse used to be hand-rolled at four boundary sites and had
diverged (UnifiedAuthMiddleware stripped the header before the prefix check;
auth.py / resolved_identity did not), so a padded ``Authorization`` value
authenticated on one transport and failed on another. All sites now route
through ``src.core.http_utils.extract_auth_token``; the per-path consistency
is pinned by tests/unit/test_token_extraction_consistency.py.

This module grades the REAL REST ingress: TestClient with NO dependency
overrides, so the request flows UnifiedAuthMiddleware → AuthContext →
_require_auth_dep → resolve_identity → DB token lookup against a
factory-created principal.
"""

from __future__ import annotations

import pytest

from tests.harness.capabilities import CapabilitiesEnv


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
