"""Sprint 7 Phase 4c — unconditional hides on embedded.

Signing keys, OIDC, and Buyer Agents (already done in Phase 1a) never
make sense on embedded tenants regardless of which storefront is the
wrapper. There's no "publisher" answer, so these surfaces don't get
capability flags — they're hard-gated on ``not embedded_view``.

Coverage:
- Signing keys nav entry + section hidden on embedded tenants
- Signing keys POST routes return 403 on embedded tenants
- Open tenants still see + can use the signing keys surface
- OIDC blueprint not registered when ``MANAGED_INSTANCE=true``

See ``docs/design/embedded-mode-sprint-7-ia-cleanup.md`` Phase 4c.
"""

from __future__ import annotations

import pytest

from tests.integration._embedded_helpers import (
    cleanup_embedded_test_tenant,
    insert_embedded_test_tenant,
)

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


# ---------------------------------------------------------------------------
# Signing keys section visibility (uses embedded_client fixture from conftest)
# ---------------------------------------------------------------------------


@pytest.fixture
def open_tenant_id(integration_db):
    tid = insert_embedded_test_tenant(is_embedded=False, name_prefix="t_p4c")
    yield tid
    cleanup_embedded_test_tenant(tid)


@pytest.fixture
def embedded_tenant_id(integration_db):
    tid = insert_embedded_test_tenant(is_embedded=True, external_source="scope3", name_prefix="t_p4c")
    yield tid
    cleanup_embedded_test_tenant(tid)


class TestSigningKeysStandalonePage:
    """The signing-keys surface lives at a standalone Configure → Workspace
    peer page (Sprint 7 Phase 2), not in Tenant Settings. The hard-hide on
    embedded tenants (Sprint 7 Phase 4c) still applies — the standalone
    page returns 403 on embedded tenants because the salesagent doesn't
    issue webhooks under its own domain there."""

    def test_standalone_page_returns_403_on_embedded(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.get(f"/tenant/{embedded_tenant_id}/signing-keys/")
        assert resp.status_code == 403
        assert b"embedded" in resp.data.lower() or b"not available" in resp.data.lower()

    def test_standalone_page_renders_on_open(self, embedded_client, open_tenant_id):
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/signing-keys/")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "🔑 Signing keys" in body
        assert "Generate Ed25519 keypair" in body

    def test_settings_page_no_longer_renders_signing_keys_section(self, embedded_client, open_tenant_id):
        """The in-page Settings section is gone — the tab data-attribute
        and the section's H2 must NOT render in Tenant Settings on either
        open or embedded tenants."""
        resp = embedded_client.get(f"/tenant/{open_tenant_id}/settings")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'data-section="signing-keys"' not in body
        assert 'id="signing-keys"' not in body

    def test_old_settings_deep_link_redirects_to_standalone(self, embedded_client, open_tenant_id):
        """``/settings/signing-keys`` was the legacy deep-link before
        Phase 2 promoted Signing Keys out of Tenant Settings. Bookmarks
        and external references to that URL must redirect to the new
        standalone page, not silently render the default section."""
        resp = embedded_client.get(
            f"/tenant/{open_tenant_id}/settings/signing-keys",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert f"/tenant/{open_tenant_id}/signing-keys/" in resp.headers["Location"]

    def test_old_publishers_deep_link_redirects_to_standalone(self, embedded_client, open_tenant_id):
        """Same shape as signing-keys for the Publishers promotion (#431).
        Pinned here because the redirect map is the canonical place
        where each promoted section gets its forwarding rule."""
        resp = embedded_client.get(
            f"/tenant/{open_tenant_id}/settings/publishers",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert f"/tenant/{open_tenant_id}/publishers/" in resp.headers["Location"]


class TestSigningKeysPostRoutesRejectEmbedded:
    """POST routes return 403 when the tenant is embedded — defense-in-depth
    against direct submissions."""

    def test_generate_returns_403_on_embedded(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.post(
            f"/tenant/{embedded_tenant_id}/signing-keys/generate",
            headers={"Origin": "http://localhost"},
        )
        assert resp.status_code == 403
        assert b"embedded" in resp.data.lower() or b"not available" in resp.data.lower()

    def test_rotate_out_returns_403_on_embedded(self, embedded_client, embedded_tenant_id):
        resp = embedded_client.post(
            f"/tenant/{embedded_tenant_id}/signing-keys/some-kid/rotate-out",
            headers={"Origin": "http://localhost"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# OIDC blueprint registration
# ---------------------------------------------------------------------------


@pytest.fixture
def managed_instance_app(integration_db, monkeypatch):
    """Build the admin app with ``MANAGED_INSTANCE=true`` set *before*
    ``create_app()`` — this is when the blueprint registration check
    runs. The default ``embedded_app`` fixture from conftest delvars
    ``MANAGED_INSTANCE`` for the session-bypass to work; here we need
    the opposite signal at startup, so we build our own app."""
    monkeypatch.setenv("ADCP_AUTH_TEST_MODE", "true")
    monkeypatch.setenv("MANAGED_INSTANCE", "true")
    monkeypatch.delenv("EMBEDDED_CAPABILITIES", raising=False)

    from src.admin.app import create_app

    return create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})


@pytest.fixture
def managed_instance_client(managed_instance_app):
    c = managed_instance_app.test_client()
    with c.session_transaction() as sess:
        sess["test_user"] = {"email": "admin@example.com", "name": "Admin"}
        sess["test_user_role"] = "super_admin"
        sess["test_tenant_id"] = "*"
    return c


class TestOidcBlueprintGatedOnManagedInstance:
    """OIDC routes are not registered when ``MANAGED_INSTANCE=true``."""

    def test_oidc_route_404_on_managed_instance(self, managed_instance_client):
        """A known OIDC route returns 404 because the blueprint never
        registered — there's no ``oidc.*`` URL rule in the map."""
        # The oidc blueprint registers /auth/oidc/* routes. Probe one of
        # them — should be 404 (not 401/403) because the route doesn't
        # exist at all in this app.
        resp = managed_instance_client.get("/auth/oidc/callback")
        assert resp.status_code == 404

    def test_oidc_route_exists_on_open_instance(self, embedded_client):
        """Sanity: on an open-instance app (MANAGED_INSTANCE unset), the
        OIDC routes ARE registered. Status doesn't have to be 200; any
        non-404 is enough proof that the blueprint mounted."""
        resp = embedded_client.get("/auth/oidc/callback")
        # The route exists but may return any non-404 status (401, 400,
        # redirect, etc.) depending on what /initiate expects. We only
        # care that it's not "no such route."
        assert resp.status_code != 404, (
            f"OIDC blueprint should be registered on open instances but /auth/oidc/callback returned {resp.status_code}"
        )
